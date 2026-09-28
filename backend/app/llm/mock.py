"""Deterministic mock responses for LLM_MOCK=true (E2E tests, development without a key).

Rules are case-insensitive and the first match wins (see planning/TEAM_CONTRACTS.md §6).
"""

from __future__ import annotations

import re

from .schema import ChatResponse, TradeInstruction, WatchlistChange

_TICKER = r"([A-Za-z][A-Za-z0-9.\-]*)"
_QTY = r"(\d+(?:\.\d+)?|\.\d+)"

_TRADE_RE = {
    side: re.compile(rf"\b{side}\s+{_QTY}\s+{_TICKER}", re.IGNORECASE) for side in ("buy", "sell")
}
_ADD_RE = re.compile(rf"\b(?:add|watch)\s+{_TICKER}", re.IGNORECASE)
_REMOVE_RE = re.compile(rf"\b(?:remove|unwatch)\s+{_TICKER}", re.IGNORECASE)

DEFAULT_MOCK_MESSAGE = "This is a mock response from FinAlly. Your portfolio is ready."


def _ticker(raw: str) -> str:
    # Drop sentence punctuation ("buy 5 AAPL.") while keeping inner dots (BRK.B)
    return raw.rstrip(".-").upper()


def _format_qty(quantity: float) -> str:
    return f"{quantity:g}"


def mock_chat_response(user_message: str) -> ChatResponse:
    """The canned structured output for `user_message`."""
    for side, pattern in _TRADE_RE.items():
        match = pattern.search(user_message)
        if match:
            quantity = float(match.group(1))
            ticker = _ticker(match.group(2))
            verb = "Buying" if side == "buy" else "Selling"
            return ChatResponse(
                message=f"{verb} {_format_qty(quantity)} {ticker} for you.",
                trades=[TradeInstruction(ticker=ticker, side=side, quantity=quantity)],
            )

    match = _ADD_RE.search(user_message)
    if match:
        ticker = _ticker(match.group(1))
        return ChatResponse(
            message=f"Adding {ticker} to your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="add")],
        )

    match = _REMOVE_RE.search(user_message)
    if match:
        ticker = _ticker(match.group(1))
        return ChatResponse(
            message=f"Removing {ticker} from your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="remove")],
        )

    return ChatResponse(message=DEFAULT_MOCK_MESSAGE)
