"""Chat message history."""

from __future__ import annotations

import json
import sqlite3

from .connection import DEFAULT_USER, connect, new_id, now_iso

ROLES = ("user", "assistant")


def _message_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "actions": json.loads(row["actions"]) if row["actions"] is not None else None,
        "created_at": row["created_at"],
    }


def add_chat_message(
    role: str, content: str, actions: dict | None = None, user_id: str = DEFAULT_USER
) -> dict:
    """Store a message; `actions` is JSON-encoded (None stays NULL)."""
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role!r}")
    message = {
        "id": new_id(),
        "role": role,
        "content": content,
        "actions": actions,
        "created_at": now_iso(),
    }
    with connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                message["id"],
                user_id,
                role,
                content,
                json.dumps(actions) if actions is not None else None,
                message["created_at"],
            ),
        )
    return message


def get_chat_messages(limit: int = 20, user_id: str = DEFAULT_USER) -> list[dict]:
    """The most recent `limit` messages, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content, actions, created_at FROM chat_messages "
            "WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_message_dict(r) for r in reversed(rows)]
