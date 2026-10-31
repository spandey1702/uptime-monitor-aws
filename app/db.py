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
