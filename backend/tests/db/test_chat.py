"""Chat message storage."""

import pytest

from app import db

ACTIONS = {
    "trades": [
        {"ticker": "AAPL", "side": "buy", "quantity": 5, "status": "executed",
         "price": 190.0, "error": None},
    ],
    "watchlist_changes": [
        {"ticker": "PYPL", "action": "add", "status": "failed",
         "error": "PYPL is already in your watchlist"},
    ],
}


class TestChatMessages:
    def test_add_user_message(self):
        msg = db.add_chat_message("user", "hello")
        assert set(msg) == {"id", "role", "content", "actions", "created_at"}
        assert msg["role"] == "user"
        assert msg["content"] == "hello"
        assert msg["actions"] is None
        assert db.get_chat_messages() == [msg]

    def test_actions_round_trip_as_dict(self):
        msg = db.add_chat_message("assistant", "Bought 5 AAPL.", ACTIONS)
        assert msg["actions"] == ACTIONS
        stored = db.get_chat_messages()[0]
        assert stored["actions"] == ACTIONS
        assert stored["actions"]["trades"][0]["error"] is None

    def test_empty_actions_preserved(self):
        empty = {"trades": [], "watchlist_changes": []}
        db.add_chat_message("assistant", "Nothing to do.", empty)
        assert db.get_chat_messages()[0]["actions"] == empty

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError):
            db.add_chat_message("system", "nope")
        assert db.get_chat_messages() == []

    def test_messages_oldest_first(self):
        contents = [f"m{i}" for i in range(5)]
        for i, c in enumerate(contents):
            db.add_chat_message("user" if i % 2 == 0 else "assistant", c)
        assert [m["content"] for m in db.get_chat_messages()] == contents

    def test_limit_returns_most_recent_oldest_first(self):
        for i in range(30):
            db.add_chat_message("user", f"m{i}")
        assert [m["content"] for m in db.get_chat_messages(limit=3)] == ["m27", "m28", "m29"]
        assert len(db.get_chat_messages()) == 20

    def test_messages_are_per_user(self):
        db.add_chat_message("user", "hi")
        assert db.get_chat_messages(user_id="other") == []
