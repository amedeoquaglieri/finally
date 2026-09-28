import importlib
import sys
import threading
import types

import pytest

import app as app_package
from app import db


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace app.llm with a stub whose handle_chat stores and echoes the message."""
    calls = []

    async def handle_chat(ctx, message):
        calls.append((ctx, message))
        db.add_chat_message("user", message)
        return db.add_chat_message(
            "assistant", f"echo: {message}", {"trades": [], "watchlist_changes": []}
        )

    module = types.ModuleType("app.llm")
    module.handle_chat = handle_chat
    monkeypatch.setitem(sys.modules, "app.llm", module)
    monkeypatch.setattr(app_package, "llm", module, raising=False)
    return calls


def test_chat_returns_assistant_message(harness, fake_llm):
    response = harness.client.post("/api/chat", json={"message": "  hello  "})

    assert response.status_code == 200
    body = response.json()
    assert body.keys() == {"id", "role", "content", "actions", "created_at"}
    assert body["role"] == "assistant"
    assert body["content"] == "echo: hello"
    assert body["actions"] == {"trades": [], "watchlist_changes": []}
    [(ctx, message)] = fake_llm
    assert message == "hello"
    assert ctx is harness.app.state.ctx


def test_chat_imports_llm_off_the_event_loop(client, monkeypatch, fake_llm):
    import_threads = []

    def recording_import(name):
        import_threads.append(threading.get_ident())
        return importlib.import_module(name)

    loop_threads = []

    async def handle_chat(ctx, message):
        loop_threads.append(threading.get_ident())
        return db.add_chat_message("assistant", "ok")

    monkeypatch.setattr(sys.modules["app.llm"], "handle_chat", handle_chat)
    monkeypatch.setattr(
        "app.api.chat.importlib", types.SimpleNamespace(import_module=recording_import)
    )

    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert len(import_threads) == 1
    assert import_threads != loop_threads  # the (slow) import never runs on the loop


@pytest.mark.parametrize("payload", [{"message": "   "}, {}, {"message": "x" * 4001}])
def test_chat_rejects_bad_message(client, fake_llm, payload):
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)
    assert fake_llm == []


def test_chat_unexpected_failure_is_500(client, monkeypatch, fake_llm):
    async def boom(ctx, message):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sys.modules["app.llm"], "handle_chat", boom)
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong handling your message"}


def test_chat_history(client, fake_llm):
    assert client.get("/api/chat/history").json() == {"messages": []}
    client.post("/api/chat", json={"message": "first"})
    client.post("/api/chat", json={"message": "second"})

    messages = client.get("/api/chat/history").json()["messages"]

    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "first"),
        ("assistant", "echo: first"),
        ("user", "second"),
        ("assistant", "echo: second"),
    ]
    assert messages[0]["actions"] is None
    assert len(client.get("/api/chat/history?limit=2").json()["messages"]) == 2
