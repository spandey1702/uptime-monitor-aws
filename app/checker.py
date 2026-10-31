import asyncio
import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

import asyncpg
import httpx

import db

log = logging.getLogger("uptime.checker")

CHECK_INTERVAL = 60          # seconds between rounds
TIMEOUT = 5                  # seconds before a site counts as down
USER_AGENT = "UptimeMonitor/1.0"

# For /health: when the checker started, and when its last round finished
# (time.monotonic() values, so changes to the system clock don't matter)
_started_at: float | None = None
_last_round_at: float | None = None


def seconds_since_last_round() -> float | None:
    """How long ago the last round finished (or the checker started, if no round
    has finished yet). None if the checker was never started."""
    reference = _last_round_at or _started_at
    return None if reference is None else time.monotonic() - reference


async def is_blocked(host: str) -> bool:
    """SSRF guard: True if the host resolves to any private or internal address
    (localhost, 10.x, 192.168.x, the EC2 metadata service 169.254.169.254, ...)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return any(not ipaddress.ip_address(info[4][0]).is_global for info in infos)


async def check_one(client: httpx.AsyncClient, monitor: asyncpg.Record) -> tuple:
    """Visit one site. Returns (monitor_id, is_up, status_code, latency_ms, error)."""
    monitor_id, url = monitor["id"], monitor["url"]
    try:
        host = urlparse(url).hostname
        if not host or await is_blocked(host):
            return (monitor_id, False, None, None, "Blocked: private or internal address")

        started = time.perf_counter()
        # stream=True: read the status line and headers only, never download the body
        async with client.stream("GET", url) as response:
            latency_ms = round((time.perf_counter() - started) * 1000)
            return (monitor_id, response.status_code < 400, response.status_code, latency_ms, None)

    except socket.gaierror:
        return (monitor_id, False, None, None, "DNS lookup failed")
    except httpx.TimeoutException:
        return (monitor_id, False, None, None, f"Timeout after {TIMEOUT}s")
    except httpx.HTTPError as exc:
        return (monitor_id, False, None, None, f"Connection failed: {type(exc).__name__}")
    except Exception:
        # One broken site must never take down the whole round
        log.exception("Unexpected error checking %r", url)
        return (monitor_id, False, None, None, "Check failed")


async def run_round(pool: asyncpg.Pool, client: httpx.AsyncClient) -> None:
    """Check every site once, save the results, prune old checks."""
    monitors = await db.list_monitors(pool)
    if not monitors:
        return

    started = time.perf_counter()
    results = await asyncio.gather(*(check_one(client, m) for m in monitors))
    await db.insert_checks(pool, results)
    await db.delete_old_checks(pool)

    for monitor, (_, is_up, status_code, _, error) in zip(monitors, results):
        if not is_up:
            log.warning("DOWN %r: %s", monitor["url"], error or f"HTTP {status_code}")
    up = sum(1 for r in results if r[1])
    log.info(
        "Checked %d sites: %d up, %d down in %.2fs",
        len(results), up, len(results) - up, time.perf_counter() - started,
    )


async def run(pool: asyncpg.Pool) -> None:
    """Background loop: one round now, then every CHECK_INTERVAL seconds, forever."""
    global _started_at, _last_round_at
    _started_at = time.monotonic()
    log.info("Checker started: every %ss, timeout %ss", CHECK_INTERVAL, TIMEOUT)
    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=False,      # a redirect could point at an internal address
        headers={"User-Agent": USER_AGENT},
    ) as client:
        while True:
            try:
                await run_round(pool, client)
                _last_round_at = time.monotonic()    # only successful rounds count
            except Exception:
                log.exception("Check round failed; trying again next round")
            await asyncio.sleep(CHECK_INTERVAL)
