"""Portfolio service: trade execution, valuation and snapshots.

The single code path for trades — used by the REST routes and the LLM chat flow.
"""

from __future__ import annotations

import logging
import math

from app import db
from app.context import AppContext
from app.market import normalize_ticker

from .common import is_valid_ticker, run_in_background, sync_ticker_tracking

logger = logging.getLogger(__name__)

VALID_SIDES = {"buy", "sell"}


class TradeError(Exception):
    """A trade was rejected. The message is user-facing."""


def format_quantity(quantity: float) -> str:
    """Readable share count: 1000000 -> "1,000,000", 0.5 -> "0.5" (never exponent notation)."""
    if float(quantity).is_integer():
        return f"{int(quantity):,}"
    return f"{quantity:,.6f}".rstrip("0").rstrip(".")


def execute_trade(ctx: AppContext, ticker: str, side: str, quantity: float) -> dict:
    """Execute a market order at the current cached price and return the trade row.

    Raises TradeError for invalid input, a missing price, or insufficient cash/shares.
    On success, records a portfolio snapshot and makes sure the market source tracks
    exactly the tickers that are watched or held.
    """
    ticker = normalize_ticker(str(ticker))
    if not is_valid_ticker(ticker):
        raise TradeError(f"Invalid ticker: {ticker!r}")

    side = str(side).strip().lower()
    if side not in VALID_SIDES:
        raise TradeError("Side must be 'buy' or 'sell'")

    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        raise TradeError("Quantity must be a positive number") from None
    if not math.isfinite(quantity) or quantity <= 0:
        raise TradeError("Quantity must be a positive number")

    price = ctx.price_cache.get_price(ticker)
    if price is None:
        raise TradeError(f"No price available for {ticker} yet")

    try:
        trade = db.record_trade(ticker, side, quantity, price)
    except db.InsufficientCashError:
        cash = db.get_cash_balance()
        raise TradeError(
            f"Insufficient cash: buying {format_quantity(quantity)} {ticker} at ${price:,.2f} costs "
            f"${quantity * price:,.2f}, but you have ${cash:,.2f}"
        ) from None
    except db.InsufficientSharesError:
        position = db.get_position(ticker)
        held = position["quantity"] if position else 0
        raise TradeError(
            f"Insufficient shares: you hold {format_quantity(held)} {ticker} but tried to sell "
            f"{format_quantity(quantity)}"
        ) from None

    logger.info("Executed %s %g %s @ %.2f", side, quantity, ticker, price)
    # A buy of an unwatched ticker must stay priced; closing a position in one
    # that isn't watched should stop tracking it.
    run_in_background(lambda: sync_ticker_tracking(ctx, ticker))
    try:
        record_portfolio_snapshot(ctx)
    except Exception:
        # The trade is committed; a missed snapshot must not report it as failed
        logger.exception("Failed to record portfolio snapshot after trade")
    return trade


def get_portfolio(ctx: AppContext) -> dict:
    """Current cash, positions valued at live prices, totals and P&L.

    Positions without a price yet are valued at their average cost
    (current_price is None, P&L is 0).
    """
    cash = db.get_cash_balance()
    rows = []
    for position in db.get_positions():
        quantity = position["quantity"]
        avg_cost = position["avg_cost"]
        current_price = ctx.price_cache.get_price(position["ticker"])
        valuation_price = current_price if current_price is not None else avg_cost
        market_value = quantity * valuation_price
        cost_basis = quantity * avg_cost
        pnl = market_value - cost_basis
        rows.append(
            {
                "ticker": position["ticker"],
                "quantity": quantity,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "market_value": market_value,
                "unrealized_pnl": pnl,
                "unrealized_pnl_percent": pnl / cost_basis * 100 if cost_basis else 0.0,
            }
        )

    positions_value = sum((row["market_value"] for row in rows), 0.0)
    total_value = cash + positions_value
    for row in rows:
        row["weight"] = row["market_value"] / total_value * 100 if total_value else 0.0
        row["avg_cost"] = round(row["avg_cost"], 4)
        for key in ("market_value", "unrealized_pnl", "unrealized_pnl_percent", "weight"):
            row[key] = round(row[key], 2)

    return {
        "cash_balance": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_value": round(total_value, 2),
        "unrealized_pnl": round(sum((row["unrealized_pnl"] for row in rows), 0.0), 2),
        "positions": rows,
    }


def record_portfolio_snapshot(ctx: AppContext) -> dict:
    """Persist the current total portfolio value (for the P&L chart)."""
    return db.record_snapshot(get_portfolio(ctx)["total_value"])
