import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI

# Read the database address from the environment (never hard-code secrets)
DATABASE_URL = os.environ["DATABASE_URL"]

# Load our table definitions from schema.sql
SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the app starts
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)       # create tables if missing
    app.state.pool = pool               # share the pool with every request
    yield                               # the app runs while we're paused here
    # Runs once when the app stops
    await pool.close()


app = FastAPI(lifespan=lifespan)
