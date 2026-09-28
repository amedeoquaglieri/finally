"""SQLite persistence layer for FinAlly.

Call init_db() once at startup (or with a tmp path in tests); every other function opens its own
short-lived connection to that database, so all are safe to call from FastAPI's threadpool.
"""

from .chat import add_chat_message, get_chat_messages
from .connection import DEFAULT_CASH, DEFAULT_TICKERS, DEFAULT_USER, init_db
from .portfolio import (
    InsufficientCashError,
    InsufficientSharesError,
    get_cash_balance,
    get_position,
    get_positions,
    get_snapshots,
    get_trades,
    record_snapshot,
    record_trade,
)
from .watchlist import add_watchlist_ticker, get_watchlist, remove_watchlist_ticker

__all__ = [
    "DEFAULT_CASH",
    "DEFAULT_TICKERS",
    "DEFAULT_USER",
    "InsufficientCashError",
    "InsufficientSharesError",
    "add_chat_message",
    "add_watchlist_ticker",
    "get_cash_balance",
    "get_chat_messages",
    "get_position",
    "get_positions",
    "get_snapshots",
    "get_trades",
    "get_watchlist",
    "init_db",
    "record_snapshot",
    "record_trade",
    "remove_watchlist_ticker",
]
