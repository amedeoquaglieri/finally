"""FastAPI application: REST API, SSE price stream and the static frontend.

Run from the backend directory:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import chat, health, portfolio, watchlist
from app.context import AppContext
from app.market import (
    MarketDataSource,
    PriceCache,
    create_market_data_source,
    create_stream_router,
)
from app.services.common import bind_event_loop
from app.services.portfolio import record_portfolio_snapshot

BACKEND_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_INTERVAL = 30.0

# Project-root .env; never overrides variables already set (e.g. Docker --env-file)
load_dotenv(BACKEND_DIR.parent / ".env", override=False)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")
logger = logging.getLogger(__name__)


def create_app(
    source_factory: Callable[[PriceCache], MarketDataSource] = create_market_data_source,
    snapshot_interval: float = SNAPSHOT_INTERVAL,
    static_dir: str | Path | None = None,
    preload_llm: bool = True,
) -> FastAPI:
    """Build the app. Tests inject a deterministic source factory and snapshot interval,
    and can skip preloading the LLM module."""
    price_cache = PriceCache()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.init_db()
        source = source_factory(price_cache)
        # Held positions stay priced even when they're no longer watched
        held = [p["ticker"] for p in db.get_positions()]
        await source.start(list(dict.fromkeys([*db.get_watchlist(), *held])))

        ctx = AppContext(price_cache=price_cache, market_source=source)
        app.state.ctx = ctx
        bind_event_loop(asyncio.get_running_loop())

        _safe_snapshot(ctx)
        # Warm app.llm and, outside mock mode, litellm (several seconds) in a background
        # thread; not awaited, so startup and the event loop aren't held up
        app.state.llm_preload = (
            asyncio.create_task(_preload_llm(), name="llm-preload") if preload_llm else None
        )
        snapshot_task = asyncio.create_task(
            _snapshot_loop(ctx, snapshot_interval), name="portfolio-snapshots"
        )
        try:
            yield
        finally:
            snapshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await snapshot_task
            if app.state.llm_preload is not None:
                app.state.llm_preload.cancel()
            await source.stop()
            bind_event_loop(None)

    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)

    app.include_router(health.router)
    app.include_router(portfolio.router)
    app.include_router(watchlist.router)
    app.include_router(chat.router)
    app.include_router(create_stream_router(price_cache))

    # Mounted last so /api/* routes always win
    static_path = Path(static_dir or os.environ.get("STATIC_DIR") or BACKEND_DIR / "static")
    if static_path.is_dir():
        app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
        logger.info("Serving frontend from %s", static_path)
    else:
        logger.info("No frontend build at %s; serving the API only", static_path)

    return app


async def _preload_llm() -> None:
    """Import the chat stack in the background so the first chat doesn't pay for it.

    app.llm itself is light; litellm (seconds to import, far more on a cold start) is
    only loaded when real LLM calls will be made, i.e. unless LLM_MOCK=true. Runs in a
    daemon thread rather than the default executor, whose threads are joined at exit,
    so shutting down mid-import never waits for it.
    """
    modules = [chat.LLM_MODULE]
    if os.environ.get("LLM_MOCK", "").strip().lower() != "true":
        modules.append("litellm")

    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def finish() -> None:
        if not done.done():  # cancelled if the app shut down first
            done.set_result(None)

    def run() -> None:
        for name in modules:
            try:
                importlib.import_module(name)
                logger.info("Preloaded %s", name)
            except Exception:
                # The chat flow imports these again on use and reports failures per request
                logger.exception("Failed to preload %s", name)
        with contextlib.suppress(RuntimeError):  # loop already closed
            loop.call_soon_threadsafe(finish)

    threading.Thread(target=run, name="llm-preload", daemon=True).start()
    await done


async def _snapshot_loop(ctx: AppContext, interval: float) -> None:
    """Record the total portfolio value every `interval` seconds."""
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(_safe_snapshot, ctx)


def _safe_snapshot(ctx: AppContext) -> None:
    try:
        record_portfolio_snapshot(ctx)
    except Exception:
        logger.exception("Failed to record portfolio snapshot")


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Report malformed request bodies as a 400 with a readable string detail."""
    messages = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        messages.append(f"{field}: {error.get('msg')}" if field else str(error.get("msg")))
    return JSONResponse(
        status_code=400, content={"detail": "; ".join(messages) or "Invalid request"}
    )


app = create_app()
