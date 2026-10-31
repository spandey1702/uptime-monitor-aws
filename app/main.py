import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode, urlparse

import asyncpg
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import checker
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

# The live link is public, so cap how many sites anyone can add
MAX_MONITORS = 20


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
    # Start the background checker; create_task returns immediately
    checker_task = asyncio.create_task(checker.run(app.state.pool))
    yield                                    # the app runs while we're paused here
    # Runs once when the app stops
    log.info("Shutting down: stopping checker, closing database pool")
    checker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await checker_task                   # wait until it has actually stopped
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


def validation_error(name: str, url: str) -> str | None:
    """Return a message if the form input is invalid, else None."""
    if not 1 <= len(name) <= 100:
        return "Name must be 1 to 100 characters."
    if not url.startswith(("http://", "https://")):
        return "URL must start with http:// or https://."
    if len(url) > 2048:
        return "URL is too long."
    return None


def back_to_home(error: str | None = None) -> RedirectResponse:
    """Post/Redirect/Get: send the browser back to the page with a GET."""
    target = f"/?{urlencode({'error': error})}" if error else "/"
    return RedirectResponse(target, status_code=303)


@app.get("/")
async def index(request: Request, error: str | None = None):
    sites = await db.fetch_status(request.app.state.pool)
    return templates.TemplateResponse(
        request, "index.html", {"sites": sites, "error": error}
    )


@app.post("/monitors")
async def create_monitor(
    request: Request,
    name: Annotated[str, Form()] = "",
    url: Annotated[str, Form()] = "",
):
    pool = request.app.state.pool
    name, url = name.strip(), url.strip()

    error = validation_error(name, url)
    if error is None and await db.count_monitors(pool) >= MAX_MONITORS:
        error = f"Limit reached: at most {MAX_MONITORS} sites."
    if error is None:
        try:
            monitor_id = await db.add_monitor(pool, name, url)
        except asyncpg.UniqueViolationError:
            error = "That URL is already being monitored."

    if error:
        log.info("Rejected monitor %r: %s", url, error)
        return back_to_home(error)
    log.info("Added monitor %s %r", monitor_id, url)
    return back_to_home()


@app.post("/monitors/{monitor_id}/delete")
async def remove_monitor(request: Request, monitor_id: int):
    if await db.delete_monitor(request.app.state.pool, monitor_id):
        log.info("Removed monitor %s", monitor_id)
    return back_to_home()
