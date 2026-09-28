"""LLM chat assistant for FinAlly.

Public API:
    handle_chat     - Process a user message and return the assistant ChatMessage
    ChatResponse    - Structured output schema (message, trades, watchlist_changes)
"""

from .chat import handle_chat
from .schema import ChatResponse, TradeInstruction, WatchlistChange

__all__ = ["ChatResponse", "TradeInstruction", "WatchlistChange", "handle_chat"]
