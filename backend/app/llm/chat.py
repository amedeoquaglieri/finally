"""Chat flow: store the user message, ask the LLM (or mock), execute its actions, store the reply."""

from __future__ import annotations

import asyncio
import logging
import os

from app import db
from app.context import AppContext
from app.services import portfolio as portfolio_service
from app.services import watchlist as watchlist_service
from app.services.portfolio import TradeError
from app.services.watchlist import WatchlistError

from . import client
from .mock import mock_chat_response
from .prompt import build_messages
from .schema import ChatResponse, ResponseParseError

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20

UNAVAILABLE_MESSAGE = (
    "Sorry, I couldn't reach the AI service right now. Please try again in a moment. "
    "You can still trade and manage your watchlist manually."
)
NOT_CONFIGURED_MESSAGE = (
    "The AI assistant isn't configured: OPENROUTER_API_KEY is missing. "
    "You can still trade and manage your watchlist manually."
)
UNPARSEABLE_MESSAGE = (
    "Sorry, I got a response from the AI service that I couldn't understand. "
    "Please try rephrasing your request."
)


def is_mock_mode() -> bool:
    return os.environ.get("LLM_MOCK", "").strip().lower() == "true"


async def _get_response(ctx: AppContext, user_message: str) -> ChatResponse:
    """Mock or real structured response; failures become a friendly message with no actions."""
    if is_mock_mode():
        return mock_chat_response(user_message)

    messages = build_messages(
        portfolio_service.get_portfolio(ctx),
        watchlist_service.get_watchlist(ctx),
        db.get_chat_messages(limit=HISTORY_LIMIT),
    )
    try:
        return await asyncio.to_thread(client.call_llm, messages)
    except client.LLMUnavailableError as exc:
        logger.warning("LLM call failed: %s", exc)
        if not os.environ.get("OPENROUTER_API_KEY", "").strip():
            return ChatResponse(message=NOT_CONFIGURED_MESSAGE)
        return ChatResponse(message=UNAVAILABLE_MESSAGE)
    except ResponseParseError as exc:
        logger.warning("Unparseable LLM response: %s", exc)
        return ChatResponse(message=UNPARSEABLE_MESSAGE)


def _execute_trades(ctx: AppContext, response: ChatResponse) -> list[dict]:
    results = []
    for trade in response.trades:
        result = {
            "ticker": trade.ticker.strip().upper(),
            "side": trade.side,
            "quantity": trade.quantity,
            "status": "failed",
            "price": None,
            "error": None,
        }
        try:
            executed = portfolio_service.execute_trade(
                ctx, trade.ticker, trade.side, trade.quantity
            )
        except TradeError as exc:
            result["error"] = str(exc)
        except Exception:
            logger.exception("Unexpected error executing %s", trade)
            result["error"] = "Unexpected error executing trade"
        else:
            result.update(
                ticker=executed["ticker"],
                quantity=executed["quantity"],
                price=executed["price"],
                status="executed",
            )
        results.append(result)
    return results


async def _apply_watchlist_changes(ctx: AppContext, response: ChatResponse) -> list[dict]:
    results = []
    for change in response.watchlist_changes:
        result = {
            "ticker": change.ticker.strip().upper(),
            "action": change.action,
            "status": "failed",
            "error": None,
        }
        try:
            if change.action == "add":
                await watchlist_service.add_to_watchlist(ctx, change.ticker)
            else:
                await watchlist_service.remove_from_watchlist(ctx, change.ticker)
        except WatchlistError as exc:
            result["error"] = str(exc)
        except Exception:
            logger.exception("Unexpected error applying %s", change)
            result["error"] = "Unexpected error updating watchlist"
        else:
            result["status"] = "executed"
        results.append(result)
    return results


async def handle_chat(ctx: AppContext, user_message: str) -> dict:
    """Handle one user chat message and return the stored assistant ChatMessage.

    Trades and watchlist changes from the response are executed through the same
    services as the REST routes; each one's outcome (status "executed" or "failed"
    with a user-facing error) is recorded in the message's `actions`. LLM failures
    produce a friendly assistant message rather than an exception.
    """
    db.add_chat_message("user", user_message)

    response = await _get_response(ctx, user_message)
    actions = {
        "trades": _execute_trades(ctx, response),
        "watchlist_changes": await _apply_watchlist_changes(ctx, response),
    }
    return db.add_chat_message("assistant", response.message, actions)
