"""Database location, connections, and lazy schema initialization + seeding."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_USER = "default"
DEFAULT_CASH = 10000.0
DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_DB_PATH = _BACKEND_DIR.parent / "db" / "finally.db"
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Seconds a connection waits on a locked database before raising.
_BUSY_TIMEOUT = 10.0

_lock = threading.Lock()
_db_path: Path | None = None  # set by init_db(); None until the database is initialized


def now_iso() -> str:
    """Current time as an ISO-8601 UTC string."""
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def normalize(ticker: str) -> str:
    return ticker.strip().upper()


def _resolve_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return Path(os.environ.get("DB_PATH") or _DEFAULT_DB_PATH)


def _open(path: Path) -> sqlite3.Connection:
    # isolation_level=None: autocommit; multi-statement writes use explicit BEGIN IMMEDIATE.
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    """Create tables if missing and seed default data on first run. Idempotent.

    db_path=None uses the DB_PATH env var, falling back to <project root>/db/finally.db.
    The resolved path becomes the database used by every other function in this package.
    """
    global _db_path
    path = _resolve_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        conn = _open(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA_PATH.read_text())
            conn.execute("BEGIN IMMEDIATE")
            try:
                exists = conn.execute(
                    "SELECT 1 FROM users_profile WHERE id = ?", (DEFAULT_USER,)
                ).fetchone()
                if not exists:
                    _seed(conn)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        _db_path = path
    logger.info("Database ready at %s", path)


def _seed(conn: sqlite3.Connection) -> None:
    now = now_iso()
    conn.execute(
        "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
        (DEFAULT_USER, DEFAULT_CASH, now),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
        [(new_id(), DEFAULT_USER, ticker, now) for ticker in DEFAULT_TICKERS],
    )
    logger.info("Seeded default user with $%.2f and %d tickers", DEFAULT_CASH, len(DEFAULT_TICKERS))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Short-lived connection to the initialized database (lazily initializing it if needed)."""
    if _db_path is None:
        init_db()
    conn = _open(_db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Connection inside a write transaction (BEGIN IMMEDIATE); commits on success, rolls back on error."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
