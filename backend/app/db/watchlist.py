"""Watchlist tickers."""

from __future__ import annotations

import sqlite3

from .connection import DEFAULT_USER, connect, new_id, normalize, now_iso


def get_watchlist(user_id: str = DEFAULT_USER) -> list[str]:
    """Watched tickers in the order they were added."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
            (user_id,),
        ).fetchall()
    return [r["ticker"] for r in rows]


def add_watchlist_ticker(ticker: str, user_id: str = DEFAULT_USER) -> bool:
    """Add a ticker. Returns False if it is already on the watchlist."""
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                (new_id(), user_id, normalize(ticker), now_iso()),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def remove_watchlist_ticker(ticker: str, user_id: str = DEFAULT_USER) -> bool:
    """Remove a ticker. Returns False if it was not on the watchlist."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, normalize(ticker))
        )
    return cur.rowcount > 0
