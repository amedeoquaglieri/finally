import json
import subprocess
import sys

import pytest

from app.llm.client import call_llm
from app.llm.schema import ChatResponse, ResponseParseError, parse_chat_response


class TestValidShapes:
    def test_message_only(self):
        r = parse_chat_response('{"message": "Hi"}')
        assert r.message == "Hi"
        assert r.trades == [] and r.watchlist_changes == []

    def test_empty_action_lists(self):
        r = parse_chat_response('{"message": "Hi", "trades": [], "watchlist_changes": []}')
        assert r.trades == [] and r.watchlist_changes == []

    def test_full_response(self):
        content = json.dumps(
            {
                "message": "Done",
                "trades": [
                    {"ticker": "AAPL", "side": "buy", "quantity": 10},
                    {"ticker": "MSFT", "side": "sell", "quantity": 0.5},
                ],
                "watchlist_changes": [
                    {"ticker": "PYPL", "action": "add"},
                    {"ticker": "NFLX", "action": "remove"},
                ],
            }
        )
        r = parse_chat_response(content)
        assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [
            ("AAPL", "buy", 10.0),
            ("MSFT", "sell", 0.5),
        ]
        assert [(w.ticker, w.action) for w in r.watchlist_changes] == [
            ("PYPL", "add"),
            ("NFLX", "remove"),
        ]

    def test_null_lists_treated_as_empty(self):
        r = parse_chat_response('{"message": "Hi", "trades": null, "watchlist_changes": null}')
        assert r.trades == [] and r.watchlist_changes == []

    def test_markdown_fenced_json(self):
        r = parse_chat_response('```json\n{"message": "Hi", "trades": []}\n```')
        assert r.message == "Hi"

    def test_json_surrounded_by_prose(self):
        r = parse_chat_response('Here you go: {"message": "Hi"} Thanks!')
        assert r.message == "Hi"


class TestMalformed:
    @pytest.mark.parametrize("content", [None, "", "   "])
    def test_empty(self, content):
        with pytest.raises(ResponseParseError):
            parse_chat_response(content)

    @pytest.mark.parametrize(
        "content",
        [
            "not json at all",
            '{"message": "unterminated',
            "[1, 2, 3]",
            '{"trades": []}',
            '{"message": ""}',
            '{"message": 42}',
        ],
    )
    def test_unusable(self, content):
        with pytest.raises(ResponseParseError):
            parse_chat_response(content)

    def test_invalid_items_dropped_valid_kept(self):
        content = json.dumps(
            {
                "message": "Partly valid",
                "trades": [
                    {"ticker": "AAPL", "side": "hold", "quantity": 1},
                    {"ticker": "MSFT", "side": "buy"},
                    {"ticker": "AAPL", "side": "buy", "quantity": 2},
                ],
                "watchlist_changes": [{"ticker": "PYPL", "action": "follow"}, "junk"],
            }
        )
        r = parse_chat_response(content)
        assert r.message == "Partly valid"
        assert [(t.ticker, t.quantity) for t in r.trades] == [("AAPL", 2.0)]
        assert r.watchlist_changes == []

    def test_non_list_actions_ignored(self):
        r = parse_chat_response('{"message": "Hi", "trades": {"ticker": "AAPL"}}')
        assert r.trades == []


class FakeLiteLLMResponse:
    def __init__(self, content):
        message = type("Message", (), {"content": content})()
        self.choices = [type("Choice", (), {"message": message})()]


class TestCallLLM:
    def test_passes_cerebras_structured_output_params(self, monkeypatch):
        calls = []

        def fake_completion(**kwargs):
            calls.append(kwargs)
            return FakeLiteLLMResponse('{"message": "ok", "trades": [], "watchlist_changes": []}')

        monkeypatch.setattr("litellm.completion", fake_completion)
        messages = [{"role": "user", "content": "hi"}]
        result = call_llm(messages)

        assert result == ChatResponse(message="ok")
        (kwargs,) = calls
        assert kwargs["model"] == "openrouter/openai/gpt-oss-120b"
        assert kwargs["messages"] == messages
        assert kwargs["response_format"] is ChatResponse
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["extra_body"] == {"provider": {"order": ["cerebras"]}}


def test_importing_llm_package_does_not_load_litellm():
    # litellm takes seconds to import; mock mode and app startup must not pay for it
    code = "import sys, app.llm; assert 'litellm' not in sys.modules, 'litellm was imported'"
    subprocess.run([sys.executable, "-c", code], check=True)
