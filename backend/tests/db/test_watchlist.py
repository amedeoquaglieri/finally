"""Watchlist add/remove/list."""

from app import db


class TestWatchlist:
    def test_add_appends_in_insertion_order(self):
        assert db.add_watchlist_ticker("PYPL") is True
        assert db.add_watchlist_ticker("AMD") is True
        assert db.get_watchlist()[-2:] == ["PYPL", "AMD"]

    def test_add_duplicate_returns_false(self):
        assert db.add_watchlist_ticker("AAPL") is False
        assert db.get_watchlist().count("AAPL") == 1

    def test_add_normalizes_ticker(self):
        assert db.add_watchlist_ticker("  pypl ") is True
        assert "PYPL" in db.get_watchlist()
        assert db.add_watchlist_ticker("PYPL") is False

    def test_remove(self):
        assert db.remove_watchlist_ticker("TSLA") is True
        assert "TSLA" not in db.get_watchlist()
        assert len(db.get_watchlist()) == 9

    def test_remove_missing_returns_false(self):
        assert db.remove_watchlist_ticker("PYPL") is False
        assert len(db.get_watchlist()) == 10

    def test_remove_normalizes_ticker(self):
        assert db.remove_watchlist_ticker("aapl") is True
        assert "AAPL" not in db.get_watchlist()

    def test_remove_then_re_add_goes_to_end(self):
        db.remove_watchlist_ticker("AAPL")
        assert db.add_watchlist_ticker("AAPL") is True
        assert db.get_watchlist()[-1] == "AAPL"

    def test_watchlists_are_per_user(self):
        assert db.get_watchlist(user_id="other") == []
        assert db.add_watchlist_ticker("AAPL", user_id="other") is True
        assert db.get_watchlist(user_id="other") == ["AAPL"]
        assert db.remove_watchlist_ticker("AAPL", user_id="other") is True
        assert "AAPL" in db.get_watchlist()
