"""Integration tests for SimulatorDataSource."""

import asyncio
from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import SimulatorDataSource
from tests.helpers import wait_until


@pytest.mark.asyncio
class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_start_populates_cache(self):
        """Test that start() immediately populates the cache."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        # Cache should have seed prices immediately (before first loop tick)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        """Test that prices are updated periodically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.3)  # Several update cycles

        # Version should have incremented (prices updated)
        assert cache.version > initial_version

        await source.stop()

    async def test_stop_is_clean(self):
        """Test that stop() is clean and idempotent."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        # Double stop should not raise
        await source.stop()

    async def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        tickers = source.get_tickers()
        assert set(tickers) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        """Test starting with no tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_exception_resilience(self):
        """Test that a failing step is logged and the loop keeps producing prices."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        real_step = source._sim.step
        calls = {"n": 0}

        def flaky_step() -> dict[str, float]:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("boom")
            return real_step()

        version = cache.version
        with patch.object(source._sim, "step", side_effect=flaky_step):
            await wait_until(lambda: cache.version > version)

        assert calls["n"] >= 3  # two failures, then successful steps
        assert not source._task.done()

        await source.stop()

    async def test_custom_update_interval(self):
        """Test using a custom update interval."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        # Polls instead of a fixed sleep, so coarse timers can't make this flaky
        await wait_until(lambda: cache.version >= initial_version + 5, timeout=1.0)

        await source.stop()

    async def test_custom_event_probability(self):
        """Test creating source with custom event probability."""
        cache = PriceCache()
        # An event fires on every tick, so each tick moves the price 2-5%
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.01, event_probability=1.0, seed=11
        )
        await source.start(["AAPL"])
        await wait_until(lambda: cache.version >= 2)
        await source.stop()

        update = cache.get("AAPL")
        assert 0.019 < abs(update.price / update.previous_price - 1) < 0.051

    async def test_ticker_normalization(self):
        """Test that tickers are normalized, matching MassiveDataSource."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10.0)
        await source.start(["aapl", " GOOGL ", "AAPL"])
        assert source.get_tickers() == ["AAPL", "GOOGL"]
        assert set(cache.get_all()) == {"AAPL", "GOOGL"}

        await source.add_ticker(" tsla")
        await source.add_ticker("aapl")  # Already tracked as AAPL
        assert source.get_tickers() == ["AAPL", "GOOGL", "TSLA"]

        await source.remove_ticker("googl ")
        assert source.get_tickers() == ["AAPL", "TSLA"]
        assert "GOOGL" not in cache

        await source.stop()

    async def test_add_ticker_before_start_is_kept(self):
        """Test that tickers added before start() are tracked once started."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10.0)
        await source.add_ticker("tsla")
        await source.add_ticker("NFLX")
        await source.remove_ticker("nflx")
        assert source.get_tickers() == ["TSLA"]
        assert len(cache) == 0  # Nothing priced until start()

        await source.start(["AAPL"])
        assert source.get_tickers() == ["AAPL", "TSLA"]
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_duplicate_add_does_not_touch_cache(self):
        """Test that re-adding a tracked ticker writes nothing (no spurious flat event)."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10.0)
        await source.start(["AAPL"])

        version = cache.version
        await source.add_ticker("AAPL")
        assert cache.version == version

        await source.stop()

    async def test_day_change_reference_is_session_open(self):
        """Test that previous_close is the seed price, including after remove + re-add."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=10.0, seed=2)
        await source.start(["AAPL"])
        assert cache.get("AAPL").previous_close == SEED_PRICES["AAPL"]

        source._sim._prices["AAPL"] = 200.0
        await source.remove_ticker("AAPL")
        await source.add_ticker("AAPL")

        update = cache.get("AAPL")
        assert update.price == 200.0  # Resumes, no jump back to the seed price
        assert update.previous_close == SEED_PRICES["AAPL"]
        assert update.day_change == 200.0 - SEED_PRICES["AAPL"]

        await source.stop()
