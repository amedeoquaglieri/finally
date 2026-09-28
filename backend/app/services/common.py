"""Helpers shared by the portfolio and watchlist services."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from app import db

if TYPE_CHECKING:
    from app.context import AppContext

logger = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Event loop that owns the market data source. Set by the app lifespan so sync
# service code running in a worker thread can still schedule source updates.
_loop: asyncio.AbstractEventLoop | None = None
# Strong references so scheduled tasks aren't garbage collected mid-flight
_background_tasks: set[asyncio.Task] = set()


def is_valid_ticker(ticker: str) -> bool:
    """True if `ticker` (already normalized) looks like a ticker symbol."""
    return bool(TICKER_RE.fullmatch(ticker))


async def sync_ticker_tracking(ctx: AppContext, ticker: str) -> None:
    """Track `ticker` in the market source iff it is on the watchlist or held.

    Held positions stay tracked after leaving the watchlist so they keep a price
    for valuation and sells. The check and the source update run without an
    intervening await, so concurrent callers can't act on a stale decision.
    """
    wanted = ticker in db.get_watchlist() or db.get_position(ticker) is not None
    tracked = ticker in ctx.market_source.get_tickers()
    if wanted and not tracked:
        await ctx.market_source.add_ticker(ticker)
    elif not wanted and tracked:
        await ctx.market_source.remove_ticker(ticker)


def bind_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Register (or clear, with None) the loop used by run_in_background from other threads."""
    global _loop
    _loop = loop


def run_in_background(coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Schedule `coro_fn()` on the app's event loop without waiting for it.

    Works both from the event loop thread (e.g. an async route or the chat flow)
    and from a worker thread (a sync route or asyncio.to_thread). Silently skipped
    when no loop is available, e.g. in unit tests that call services directly.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(_log_errors(coro_fn))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    elif _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_log_errors(coro_fn), _loop)


async def _log_errors(coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
    try:
        await coro_fn()
    except Exception:
        logger.exception("Background task failed")
