import logging
from pathlib import Path

import asyncpg

log = logging.getLogger("uptime.db")

# Table definitions, applied at startup
SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

# Every site + its latest check + its 24h uptime, in one round trip
STATUS_QUERY = """
SELECT m.id, m.name, m.url,
       last.is_up, last.status_code, last.latency_ms, last.error, last.checked_at,
       agg.uptime
FROM monitors m
LEFT JOIN LATERAL (
    SELECT is_up, status_code, latency_ms, error, checked_at
    FROM checks c
    WHERE c.monitor_id = m.id
    ORDER BY checked_at DESC, id DESC
    LIMIT 1
) last ON true
LEFT JOIN LATERAL (
    SELECT round(100.0 * avg(is_up::int), 1) AS uptime
    FROM checks c
    WHERE c.monitor_id = m.id
) agg ON true
ORDER BY m.id
"""


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Open the connection pool and create tables if missing."""
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    return pool


async def fetch_status(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """One row per monitored site, for the status page."""
    return await pool.fetch(STATUS_QUERY)


async def count_monitors(pool: asyncpg.Pool) -> int:
    """How many sites are being monitored (for the cap)."""
    return await pool.fetchval("SELECT count(*) FROM monitors")


async def add_monitor(pool: asyncpg.Pool, name: str, url: str) -> int:
    """Save a new site and return its id.
    Raises asyncpg.UniqueViolationError if the URL already exists."""
    return await pool.fetchval(
        "INSERT INTO monitors (name, url) VALUES ($1, $2) RETURNING id",
        name, url,
    )


async def list_monitors(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Every site to check (for the background checker)."""
    return await pool.fetch("SELECT id, url FROM monitors ORDER BY id")


async def insert_checks(pool: asyncpg.Pool, results: list[tuple]) -> None:
    """Save one round of results in a single round trip.
    Each result is (monitor_id, is_up, status_code, latency_ms, error)."""
    await pool.executemany(
        "INSERT INTO checks (monitor_id, is_up, status_code, latency_ms, error) "
        "VALUES ($1, $2, $3, $4, $5)",
        results,
    )


async def delete_old_checks(pool: asyncpg.Pool) -> None:
    """Keep only the last 24 hours of checks, so the table stays small."""
    await pool.execute("DELETE FROM checks WHERE checked_at < now() - interval '24 hours'")


async def delete_monitor(pool: asyncpg.Pool, monitor_id: int) -> bool:
    """Remove a site (its checks go with it, via ON DELETE CASCADE).
    Returns False if no site had that id."""
    result = await pool.execute("DELETE FROM monitors WHERE id = $1", monitor_id)
    return result == "DELETE 1"
