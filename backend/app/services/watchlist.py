"""Watchlist service: the single code path for watchlist changes.

Used by the REST routes and the LLM chat flow.
"""

from __future__ import annotations

from app import db
from app.context import AppContext
from app.market import normalize_ticker

from .common import is_valid_ticker, sync_ticker_tracking


class WatchlistError(Exception):
    """A watchlist change was rejected. The message is user-facing."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def get_watchlist(ctx: AppContext) -> list[dict]:
    """Watched tickers (in the order added) with their latest prices."""
    return [_entry(ctx, ticker) for ticker in db.get_watchlist()]


async def add_to_watchlist(ctx: AppContext, ticker: str) -> dict:
    """Add a ticker and start streaming its price. Returns its watchlist entry.

    Raises WatchlistError: 400 for an invalid ticker, 409 if already watched.
    """
    ticker = normalize_ticker(str(ticker))
    if not is_valid_ticker(ticker):
        raise WatchlistError(f"Invalid ticker: {ticker!r}", 400)
    if not db.add_watchlist_ticker(ticker):
        raise WatchlistError(f"{ticker} is already in your watchlist", 409)
    await sync_ticker_tracking(ctx, ticker)
    return _entry(ctx, ticker)


async def remove_from_watchlist(ctx: AppContext, ticker: str) -> None:
    """Remove a ticker. Its price keeps streaming while a position is open.

    Raises WatchlistError 404 if the ticker isn't watched.
    """
    ticker = normalize_ticker(str(ticker))
    if not db.remove_watchlist_ticker(ticker):
        raise WatchlistError(f"{ticker} is not in your watchlist", 404)
    await sync_ticker_tracking(ctx, ticker)


def _entry(ctx: AppContext, ticker: str) -> dict:
    """Watchlist entry for `ticker`; price fields are None until its first price."""
    update = ctx.price_cache.get(ticker)
    if update is None:
        return {
            "ticker": ticker,
            "price": None,
            "previous_price": None,
            "change": None,
            "change_percent": None,
            "day_change_percent": None,
            "direction": None,
            "timestamp": None,
        }
    return {
        "ticker": ticker,
        "price": update.price,
        "previous_price": update.previous_price,
        "change": update.change,
        "change_percent": update.change_percent,
        "day_change_percent": update.day_change_percent,
        "direction": update.direction,
        "timestamp": update.timestamp,
    }
