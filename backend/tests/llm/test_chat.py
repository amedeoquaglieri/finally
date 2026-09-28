import json
import threading

import pytest

from app import db
from app.llm import chat, handle_chat
from app.llm.prompt import SYSTEM_PROMPT


class FakeLiteLLMResponse:
    def __init__(self, content):
        message = type("Message", (), {"content": content})()
        self.choices = [type("Choice", (), {"message": message})()]


@pytest.fixture
def llm_reply(monkeypatch):
    """Patch litellm.completion to return `reply["content"]`; records every call's kwargs."""
    state = {"content": json.dumps({"message": "ok"}), "calls": [], "threads": []}

    def fake_completion(**kwargs):
        state["calls"].append(kwargs)
        state["threads"].append(threading.get_ident())
        if isinstance(state["content"], Exception):
            raise state["content"]
        return FakeLiteLLMResponse(state["content"])

    monkeypatch.setattr("litellm.completion", fake_completion)
    return state


def reply(message, trades=(), watchlist_changes=()):
    return json.dumps(
        {"message": message, "trades": list(trades), "watchlist_changes": list(watchlist_changes)}
    )


class TestMockMode:
    @pytest.fixture(autouse=True)
    def _mock(self, mock_mode, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("LLM must not be called in mock mode")

        monkeypatch.setattr("litellm.completion", fail)

    async def test_buy_executes_trade(self, ctx):
        result = await handle_chat(ctx, "buy 5 AAPL")

        assert result["role"] == "assistant"
        assert result["content"] == "Buying 5 AAPL for you."
        assert result["actions"] == {
            "trades": [
                {
                    "ticker": "AAPL",
                    "side": "buy",
                    "quantity": 5.0,
                    "status": "executed",
                    "price": 190.0,
                    "error": None,
                }
            ],
            "watchlist_changes": [],
        }
        assert db.get_cash_balance() == pytest.approx(10000.0 - 5 * 190.0)
        assert db.get_position("AAPL")["quantity"] == 5.0

    async def test_sell_executes_trade(self, ctx):
        await handle_chat(ctx, "buy 5 AAPL")
        result = await handle_chat(ctx, "sell 2 AAPL")

        (trade,) = result["actions"]["trades"]
        assert trade["status"] == "executed" and trade["side"] == "sell"
        assert db.get_position("AAPL")["quantity"] == 3.0

    async def test_failed_buy_reports_error(self, ctx):
        result = await handle_chat(ctx, "buy 1000 AAPL")

        (trade,) = result["actions"]["trades"]
        assert trade["status"] == "failed"
        assert trade["price"] is None
        assert "Insufficient cash" in trade["error"]
        assert db.get_cash_balance() == 10000.0

    async def test_failed_sell_reports_error(self, ctx):
        result = await handle_chat(ctx, "sell 1 MSFT")

        (trade,) = result["actions"]["trades"]
        assert trade["status"] == "failed"
        assert "Insufficient shares" in trade["error"]

    async def test_trade_without_price_fails(self, ctx):
        result = await handle_chat(ctx, "buy 1 ZZZZ")

        (trade,) = result["actions"]["trades"]
        assert trade["status"] == "failed"
        assert "No price available for ZZZZ" in trade["error"]

    async def test_add_to_watchlist(self, ctx):
        result = await handle_chat(ctx, "add PYPL")

        assert result["content"] == "Adding PYPL to your watchlist."
        assert result["actions"]["watchlist_changes"] == [
            {"ticker": "PYPL", "action": "add", "status": "executed", "error": None}
        ]
        assert "PYPL" in db.get_watchlist()
        assert "PYPL" in ctx.market_source.get_tickers()

    async def test_add_existing_ticker_fails(self, ctx):
        result = await handle_chat(ctx, "watch AAPL")

        (change,) = result["actions"]["watchlist_changes"]
        assert change["status"] == "failed"
        assert change["error"] == "AAPL is already in your watchlist"

    async def test_remove_from_watchlist(self, ctx):
        result = await handle_chat(ctx, "remove NFLX")

        assert result["content"] == "Removing NFLX from your watchlist."
        (change,) = result["actions"]["watchlist_changes"]
        assert change["status"] == "executed"
        assert "NFLX" not in db.get_watchlist()

    async def test_remove_missing_ticker_fails(self, ctx):
        result = await handle_chat(ctx, "unwatch PYPL")

        (change,) = result["actions"]["watchlist_changes"]
        assert change["status"] == "failed"
        assert change["error"]

    async def test_default_response_has_no_actions(self, ctx):
        result = await handle_chat(ctx, "hello there")

        assert result["content"] == "This is a mock response from FinAlly. Your portfolio is ready."
        assert result["actions"] == {"trades": [], "watchlist_changes": []}

    async def test_messages_persisted(self, ctx):
        result = await handle_chat(ctx, "buy 1 AAPL")

        user, assistant = db.get_chat_messages()
        assert (user["role"], user["content"], user["actions"]) == ("user", "buy 1 AAPL", None)
        assert assistant == result


class TestRealMode:
    async def test_prompt_contains_system_context_and_history(self, ctx, llm_reply):
        db.add_chat_message("user", "earlier question")
        db.add_chat_message(
            "assistant",
            "earlier answer",
            {
                "trades": [
                    {
                        "ticker": "AAPL",
                        "side": "buy",
                        "quantity": 1000,
                        "status": "failed",
                        "price": None,
                        "error": "Insufficient cash",
                    }
                ],
                "watchlist_changes": [],
            },
        )

        await handle_chat(ctx, "what should I do?")

        (call,) = llm_reply["calls"]
        messages = call["messages"]
        assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
        assert "FinAlly, an AI trading assistant" in SYSTEM_PROMPT
        context = messages[1]["content"]
        assert messages[1]["role"] == "system"
        assert "Cash: $10,000.00" in context
        assert "AAPL: price $190.00" in context
        assert [m["role"] for m in messages[2:]] == ["user", "assistant", "user"]
        assert messages[2]["content"] == "earlier question"
        assert messages[3]["content"].startswith("earlier answer")
        assert "FAILED: Insufficient cash" in messages[3]["content"]
        assert messages[4]["content"] == "what should I do?"

    async def test_context_includes_positions(self, ctx, llm_reply):
        db.record_trade("AAPL", "buy", 10, 180.0)

        await handle_chat(ctx, "how am I doing?")

        context = llm_reply["calls"][0]["messages"][1]["content"]
        assert "AAPL: 10 shares, avg cost $180.00, price $190.00" in context
        assert "P&L $100.00" in context

    async def test_history_limited(self, ctx, llm_reply):
        for i in range(30):
            db.add_chat_message("user", f"old {i}")

        await handle_chat(ctx, "latest")

        history = llm_reply["calls"][0]["messages"][2:]
        assert len(history) == chat.HISTORY_LIMIT
        assert history[-1]["content"] == "latest"

    async def test_llm_called_off_event_loop_thread(self, ctx, llm_reply):
        await handle_chat(ctx, "hi")
        assert llm_reply["threads"] == [llm_reply["threads"][0]]
        assert llm_reply["threads"][0] != threading.get_ident()

    async def test_executes_llm_actions(self, ctx, llm_reply):
        llm_reply["content"] = reply(
            "Bought and watching.",
            trades=[
                {"ticker": "aapl", "side": "buy", "quantity": 2},
                {"ticker": "MSFT", "side": "sell", "quantity": 1},
            ],
            watchlist_changes=[{"ticker": "PYPL", "action": "add"}],
        )

        result = await handle_chat(ctx, "buy 2 apple, sell a microsoft, watch paypal")

        assert result["content"] == "Bought and watching."
        buy, sell = result["actions"]["trades"]
        assert (buy["ticker"], buy["status"], buy["price"]) == ("AAPL", "executed", 190.0)
        assert sell["status"] == "failed" and "Insufficient shares" in sell["error"]
        assert result["actions"]["watchlist_changes"][0]["status"] == "executed"
        assert db.get_position("AAPL")["quantity"] == 2.0
        assert "PYPL" in db.get_watchlist()

    async def test_invalid_trade_from_llm_fails_cleanly(self, ctx, llm_reply):
        llm_reply["content"] = reply(
            "Trying.", trades=[{"ticker": "AAPL", "side": "buy", "quantity": -3}]
        )

        result = await handle_chat(ctx, "buy")

        (trade,) = result["actions"]["trades"]
        assert trade["status"] == "failed"
        assert trade["error"] == "Quantity must be a positive number"

    async def test_unexpected_service_error_is_contained(self, ctx, llm_reply, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr("app.services.portfolio.execute_trade", boom)
        llm_reply["content"] = reply(
            "Buying.", trades=[{"ticker": "AAPL", "side": "buy", "quantity": 1}]
        )

        result = await handle_chat(ctx, "buy 1 AAPL")

        (trade,) = result["actions"]["trades"]
        assert trade["status"] == "failed"
        assert trade["error"] == "Unexpected error executing trade"

    async def test_network_error_returns_friendly_message(self, ctx, llm_reply):
        llm_reply["content"] = ConnectionError("connection refused")

        result = await handle_chat(ctx, "hi")

        assert result["content"] == chat.UNAVAILABLE_MESSAGE
        assert result["actions"] == {"trades": [], "watchlist_changes": []}
        assert [m["role"] for m in db.get_chat_messages()] == ["user", "assistant"]

    async def test_missing_api_key_returns_friendly_message(self, ctx, llm_reply, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY")

        result = await handle_chat(ctx, "hi")

        assert result["content"] == chat.NOT_CONFIGURED_MESSAGE
        assert llm_reply["calls"] == []

    @pytest.mark.parametrize("content", [None, "", "I am not JSON", '{"trades": []}'])
    async def test_malformed_response_returns_friendly_message(self, ctx, llm_reply, content):
        llm_reply["content"] = content

        result = await handle_chat(ctx, "hi")

        assert result["content"] == chat.UNPARSEABLE_MESSAGE
        assert result["actions"] == {"trades": [], "watchlist_changes": []}
        assert db.get_cash_balance() == 10000.0

    async def test_mock_flag_is_case_insensitive(self, ctx, llm_reply, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "TRUE")

        result = await handle_chat(ctx, "hi")

        assert result["content"].startswith("This is a mock response")
        assert llm_reply["calls"] == []
