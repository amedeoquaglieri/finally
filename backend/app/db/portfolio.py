"""Cash, positions, trades and portfolio snapshots."""

from __future__ import annotations

import sqlite3

from .connection import DEFAULT_USER, connect, new_id, normalize, now_iso, transaction

# Quantities/cash within this of zero are treated as zero (float rounding after fractional trades).
EPSILON = 1e-9


class InsufficientCashError(Exception):
    """A buy costs more than the available cash."""


class InsufficientSharesError(Exception):
    """A sell asks for more shares than are held."""


def _cash(conn: sqlite3.Connection, user_id: str) -> float:
    row = conn.execute("SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"Unknown user: {user_id}")
    return row["cash_balance"]


def get_cash_balance(user_id: str = DEFAULT_USER) -> float:
    with connect() as conn:
        return _cash(conn, user_id)


def _position_dict(row: sqlite3.Row) -> dict:
    return {
        "ticker": row["ticker"],
        "quantity": row["quantity"],
        "avg_cost": row["avg_cost"],
        "updated_at": row["updated_at"],
    }


def get_positions(user_id: str = DEFAULT_USER) -> list[dict]:
    """Open positions (quantity > 0), ordered by ticker."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? AND quantity > ? ORDER BY ticker",
            (user_id, EPSILON),
        ).fetchall()
    return [_position_dict(r) for r in rows]


def get_position(ticker: str, user_id: str = DEFAULT_USER) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? AND ticker = ? AND quantity > ?",
            (user_id, normalize(ticker), EPSILON),
        ).fetchone()
    return _position_dict(row) if row else None


def record_trade(
    ticker: str, side: str, quantity: float, price: float, user_id: str = DEFAULT_USER
) -> dict:
    """Validate and apply a market order atomically: cash, position and trade log.

    Raises InsufficientCashError / InsufficientSharesError (state unchanged), or ValueError for a
    bad side, quantity or price.
    """
    ticker = normalize(ticker)
    side = side.strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"Invalid side: {side!r}")
    if not quantity > 0:
        raise ValueError(f"Quantity must be positive, got {quantity}")
    if not price > 0:
        raise ValueError(f"Price must be positive, got {price}")

    now = now_iso()
    with transaction() as conn:
        cash = _cash(conn, user_id)
        pos = conn.execute(
            "SELECT quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        held = pos["quantity"] if pos else 0.0
        cost = quantity * price

        if side == "buy":
            if cost > cash + EPSILON:
                raise InsufficientCashError(
                    f"Insufficient cash: buying {quantity:g} {ticker} costs ${cost:,.2f}, "
                    f"available ${cash:,.2f}"
                )
            new_cash = cash - cost
            new_qty = held + quantity
            new_avg = (held * pos["avg_cost"] + cost) / new_qty if pos else price
        else:
            if quantity > held + EPSILON:
                raise InsufficientSharesError(
                    f"Insufficient shares: selling {quantity:g} {ticker}, holding {held:g}"
                )
            new_cash = cash + cost
            new_qty = held - quantity
            new_avg = pos["avg_cost"]

        conn.execute(
            "UPDATE users_profile SET cash_balance = ? WHERE id = ?",
            (0.0 if abs(new_cash) < EPSILON else new_cash, user_id),
        )
        if new_qty <= EPSILON:
            conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )
        elif pos:
            conn.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? "
                "WHERE user_id = ? AND ticker = ?",
                (new_qty, new_avg, now, user_id, ticker),
            )
        else:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), user_id, ticker, new_qty, new_avg, now),
            )

        trade = {
            "id": new_id(),
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "executed_at": now,
        }
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
            "VALUES (:id, :user_id, :ticker, :side, :quantity, :price, :executed_at)",
            {**trade, "user_id": user_id},
        )
    return trade


def get_trades(limit: int = 100, user_id: str = DEFAULT_USER) -> list[dict]:
    """Most recent trades, newest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, quantity, price, executed_at FROM trades "
            "WHERE user_id = ? ORDER BY executed_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def record_snapshot(total_value: float, user_id: str = DEFAULT_USER) -> dict:
    snapshot = {"total_value": total_value, "recorded_at": now_iso()}
    with connect() as conn:
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (new_id(), user_id, total_value, snapshot["recorded_at"]),
        )
    return snapshot


def get_snapshots(limit: int = 1000, user_id: str = DEFAULT_USER) -> list[dict]:
    """The most recent `limit` snapshots, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots "
            "WHERE user_id = ? ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
