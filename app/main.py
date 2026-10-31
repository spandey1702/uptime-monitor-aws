import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

import db

# Configure logging once; everything goes to the terminal (stdout),
# which Docker captures and CloudWatch can collect
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("uptime")

# Read the database address from the environment (never hard-code secrets)
DATABASE_URL = os.environ["DATABASE_URL"]
DB_HOST = urlparse(DATABASE_URL).hostname   # safe to log, no password

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the app starts
    log.info("Connecting to database at %s", DB_HOST)
    try:
        app.state.pool = await db.create_pool(DATABASE_URL)
    except Exception:
        log.exception("Startup failed: could not set up the database")
        raise                                # still stop the app
    log.info("Database ready, schema applied")
    yield                                    # the app runs while we're paused here
    # Runs once when the app stops
    log.info("Shutting down, closing database pool")
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index(request: Request):
    sites = await db.fetch_status(request.app.state.pool)
    return templates.TemplateResponse(request, "index.html", {"sites": sites})
