"""LiteLLM → OpenRouter → Cerebras call with structured output."""

from __future__ import annotations

import logging
import os

from .schema import ChatResponse, parse_chat_response

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
TIMEOUT = 60.0  # seconds


class LLMUnavailableError(Exception):
    """The LLM couldn't be called (no API key, network or provider error)."""


def call_llm(messages: list[dict]) -> ChatResponse:
    """Blocking call to the model; returns the parsed structured response.

    Raises LLMUnavailableError if the request fails and ResponseParseError if the
    reply can't be interpreted.
    """
    if not os.environ.get("OPENROUTER_API_KEY", "").strip():
        raise LLMUnavailableError("OPENROUTER_API_KEY is not set")
    # Imported here, not at module level: loading litellm takes seconds, and the first
    # call already runs in a worker thread, so it never blocks the event loop (and mock
    # mode never loads it at all).
    from litellm import completion

    try:
        response = completion(
            model=MODEL,
            messages=messages,
            response_format=ChatResponse,
            reasoning_effort="low",
            extra_body=EXTRA_BODY,
            timeout=TIMEOUT,
        )
        content = response.choices[0].message.content
    except Exception as exc:
        raise LLMUnavailableError(str(exc)) from exc
    return parse_chat_response(content)
