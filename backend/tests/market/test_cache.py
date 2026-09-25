"""Tests for PriceCache."""

import threading

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        """Test updating and getting a price."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat(self):
        """Test that the first update has flat direction."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        """Test price update with upward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        """Test price update with downward direction."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        """Test removing a ticker from cache."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent(self):
        """Test removing a ticker that doesn't exist."""
        cache = PriceCache()
        cache.remove("AAPL")  # Should not raise

    def test_get_all(self):
        """Test getting all prices."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        all_prices = cache.get_all()
        assert set(all_prices.keys()) == {"AAPL", "GOOGL"}

    def test_version_increments(self):
        """Test that version counter increments."""
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_get_price_convenience(self):
        """Test the convenience get_price method."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        """Test __len__ method."""
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        """Test __contains__ method."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        """Test updating with a custom timestamp."""
        cache = PriceCache()
        custom_ts = 1234567890.0
        update = cache.update("AAPL", 190.50, timestamp=custom_ts)
        assert update.timestamp == custom_ts

    def test_price_rounding(self):
        """Test that prices are rounded to 2 decimal places."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.12345)
        assert update.price == 190.12

    def test_remove_bumps_version(self):
        """Test that removing a present ticker bumps the version (so SSE sends it)."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        v = cache.version
        cache.remove("AAPL")
        assert cache.version == v + 1

    def test_remove_nonexistent_keeps_version(self):
        """Test that removing an unknown ticker does not bump the version."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        v = cache.version
        cache.remove("GOOGL")
        assert cache.version == v

    def test_zero_timestamp_is_kept(self):
        """Test that timestamp=0.0 is used as given, not replaced by now."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.00, timestamp=0.0)
        assert update.timestamp == 0.0

    def test_previous_close_defaults_to_first_price(self):
        """Test that the daily-change reference defaults to the first price seen."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 195.00)
        assert update.previous_close == 190.00
        assert update.day_change == 5.00

    def test_previous_close_is_kept_across_updates(self):
        """Test that an explicit previous_close persists when later updates omit it."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, previous_close=180.00)
        update = cache.update("AAPL", 191.00)
        assert update.previous_close == 180.00

    def test_previous_close_can_be_replaced(self):
        """Test that a new previous_close (e.g. a new trading day) replaces the old one."""
        cache = PriceCache()
        cache.update("AAPL", 190.00, previous_close=180.00)
        update = cache.update("AAPL", 191.00, previous_close=189.00)
        assert update.previous_close == 189.00

    def test_concurrent_writers(self):
        """Test that concurrent writers from many threads lose no updates."""
        cache = PriceCache()
        tickers = [f"T{i}" for i in range(8)]
        updates_per_thread = 500

        def writer(ticker: str) -> None:
            for n in range(updates_per_thread):
                cache.update(ticker, 100.0 + n)
                cache.get_all()

        threads = [threading.Thread(target=writer, args=(t,)) for t in tickers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert cache.version == len(tickers) * updates_per_thread
        assert len(cache) == len(tickers)
        assert all(cache.get_price(t) == 100.0 + updates_per_thread - 1 for t in tickers)
