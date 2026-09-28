"""Structured output schema for the chat assistant, plus a lenient parser for it."""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class TradeInstruction(BaseModel):
    """A market order the assistant wants executed."""

    ticker: str = Field(description="Ticker symbol, e.g. AAPL")
    side: Literal["buy", "sell"]
    quantity: float = Field(description="Number of shares (fractional allowed), greater than 0")


class WatchlistChange(BaseModel):
    """A watchlist modification the assistant wants applied."""

    ticker: str = Field(description="Ticker symbol, e.g. PYPL")
    action: Literal["add", "remove"]


class ChatResponse(BaseModel):
    """What the LLM must return. Also the shape the mock produces."""

    message: str = Field(description="Conversational response shown to the user")
    trades: list[TradeInstruction] = Field(
        default_factory=list, description="Trades to execute now; empty if none"
    )
    watchlist_changes: list[WatchlistChange] = Field(
        default_factory=list, description="Watchlist changes to apply now; empty if none"
    )


class ResponseParseError(ValueError):
    """The LLM output couldn't be interpreted as a ChatResponse."""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _load_json_object(content: str) -> dict:
    """Decode a JSON object, tolerating markdown fences and surrounding prose."""
    text = content.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ResponseParseError("No JSON object in response") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ResponseParseError(f"Invalid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ResponseParseError("Response is not a JSON object")
    return data


def _valid_items(raw: object, model: type[BaseModel], field: str) -> list:
    """Validate each list item on its own so one bad entry doesn't discard the rest."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("LLM response %r is not a list; ignoring it", field)
        return []
    items = []
    for item in raw:
        try:
            items.append(model.model_validate(item))
        except ValidationError as exc:
            logger.warning("Dropping invalid %s entry %r: %s", field, item, exc)
    return items


def parse_chat_response(content: str | None) -> ChatResponse:
    """Parse raw LLM output into a ChatResponse.

    Strict validation first; on failure, falls back to keeping the message and
    every individually valid trade / watchlist change. Raises ResponseParseError
    only when there's no usable message at all.
    """
    if not content or not content.strip():
        raise ResponseParseError("Empty response")
    try:
        response = ChatResponse.model_validate_json(content)
    except ValidationError:
        response = None
    if response is not None:
        if not response.message.strip():
            raise ResponseParseError("Response has no message")
        return response

    data = _load_json_object(content)
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ResponseParseError("Response has no message")
    return ChatResponse(
        message=message,
        trades=_valid_items(data.get("trades"), TradeInstruction, "trades"),
        watchlist_changes=_valid_items(
            data.get("watchlist_changes"), WatchlistChange, "watchlist_changes"
        ),
    )
