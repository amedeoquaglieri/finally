"""Schema creation, seeding and database location."""

import sqlite3

from app import db
from app.db import connection

TABLES = {
    "users_profile",
    "watchlist",
    "positions",
    "trades",
    "portfolio_snapshots",
    "chat_messages",
}


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


class TestInitDb:
    def test_creates_all_tables(self, db_path):
        assert TABLES <= _tables(db_path)

    def test_every_data_table_has_user_id_defaulting_to_default(self, db_path):
        conn = sqlite3.connect(db_path)
        try:
            for table in TABLES - {"users_profile"}:
                cols = {r[1]: r[4] for r in conn.execute(f"PRAGMA table_info({table})")}
                assert cols["user_id"] == "'default'", table
        finally:
            conn.close()

    def test_wal_mode_enabled(self, db_path):
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            conn.close()

    def test_seeds_default_user_and_watchlist(self):
        assert db.get_cash_balance() == 10000.0
        assert db.get_watchlist() == [
            "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX",
        ]
        assert db.get_positions() == []
        assert db.get_trades() == []
        assert db.get_snapshots() == []
        assert db.get_chat_messages() == []

    def test_idempotent_does_not_reseed_or_reset(self, db_path):
        db.record_trade("AAPL", "buy", 1, 100.0)
        db.remove_watchlist_ticker("NFLX")
        db.add_watchlist_ticker("PYPL")

        db.init_db(db_path)
        db.init_db(db_path)

        assert db.get_cash_balance() == 9900.0
        assert "NFLX" not in db.get_watchlist()
        assert db.get_watchlist().count("PYPL") == 1
        assert len(db.get_watchlist()) == 10
        assert db.get_position("AAPL")["quantity"] == 1

    def test_empty_watchlist_is_not_reseeded(self, db_path):
        for ticker in db.get_watchlist():
            db.remove_watchlist_ticker(ticker)
        db.init_db(db_path)
        assert db.get_watchlist() == []

    def test_existing_empty_file_gets_initialized(self, tmp_path):
        path = tmp_path / "empty.db"
        path.touch()
        db.init_db(path)
        assert TABLES <= _tables(path)
        assert db.get_cash_balance() == 10000.0

    def test_creates_missing_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "finally.db"
        db.init_db(path)
        assert path.exists()

    def test_db_path_env_var_used_when_no_path_given(self, tmp_path, monkeypatch):
        path = tmp_path / "from_env.db"
        monkeypatch.setenv("DB_PATH", str(path))
        db.init_db()
        assert path.exists()
        db.add_watchlist_ticker("PYPL")
        assert "PYPL" in db.get_watchlist()

    def test_default_path_is_project_root_db_dir(self, monkeypatch):
        monkeypatch.delenv("DB_PATH", raising=False)
        path = connection._resolve_path(None)
        assert path.name == "finally.db"
        assert path.parent.name == "db"
        assert (path.parent.parent / "backend").is_dir()

    def test_lazy_init_on_first_use(self, tmp_path, monkeypatch):
        path = tmp_path / "lazy.db"
        monkeypatch.setenv("DB_PATH", str(path))
        monkeypatch.setattr(connection, "_db_path", None)
        assert db.get_cash_balance() == 10000.0
        assert path.exists()


class TestIsolation:
    def test_databases_are_independent(self, tmp_path):
        db.init_db(tmp_path / "a.db")
        db.add_watchlist_ticker("PYPL")
        db.init_db(tmp_path / "b.db")
        assert "PYPL" not in db.get_watchlist()
