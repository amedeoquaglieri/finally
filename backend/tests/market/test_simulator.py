"""Tests for GBMSimulator."""

import math

import numpy as np
import pytest

from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS
from app.market.simulator import GBMSimulator


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        """Test that unknown tickers get random seed prices."""
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"])
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL", "TSLA"], seed=1)
        for _ in range(100):
            for price in sim.step().values():
                assert price == round(price, 2)

    def test_seed_makes_paths_reproducible(self):
        """Test that the same seed gives the same prices, including random seed prices."""
        tickers = ["AAPL", "GOOGL", "ZZZZ"]
        sim1 = GBMSimulator(tickers=tickers, seed=123, event_probability=0.5)
        sim2 = GBMSimulator(tickers=tickers, seed=123, event_probability=0.5)
        assert sim1.get_price("ZZZZ") == sim2.get_price("ZZZZ")
        for _ in range(50):
            assert sim1.step() == sim2.step()

    def test_all_default_tickers(self):
        """Test that the full default watchlist builds a valid correlation matrix."""
        sim = GBMSimulator(tickers=list(SEED_PRICES), seed=0)
        assert sim._cholesky is not None
        assert sim._cholesky.shape == (10, 10)
        assert set(sim.step()) == set(SEED_PRICES)

    def test_log_returns_match_gbm_parameters(self):
        """Test that per-step log-returns have the configured volatility and correlations."""
        n_steps = 20_000
        tickers = ["AAPL", "GOOGL", "TSLA"]
        sim = GBMSimulator(tickers=tickers, seed=42, event_probability=0.0)

        log_returns = np.empty((n_steps, len(tickers)))
        prev = np.array([sim.get_price(t) for t in tickers])
        for i in range(n_steps):
            sim.step()
            cur = np.array([sim.get_price(t) for t in tickers])
            log_returns[i] = np.log(cur / prev)
            prev = cur

        dt = GBMSimulator.DEFAULT_DT
        for j, ticker in enumerate(tickers):
            sigma = TICKER_PARAMS[ticker]["sigma"]
            expected_std = sigma * math.sqrt(dt)
            assert log_returns[:, j].std() == pytest.approx(expected_std, rel=0.05)
            # Drift is negligible per tick; the mean is ~0 within sampling error
            assert abs(log_returns[:, j].mean()) < 5 * expected_std / math.sqrt(n_steps)

        corr = np.corrcoef(log_returns.T)
        assert corr[0, 1] == pytest.approx(0.6, abs=0.05)  # AAPL-GOOGL: tech
        assert corr[0, 2] == pytest.approx(0.3, abs=0.05)  # AAPL-TSLA

    def test_events_move_price_by_2_to_5_percent(self):
        """Test that a random event moves the price by 2-5% (always firing here)."""
        sim = GBMSimulator(tickers=["AAPL"], seed=7, event_probability=1.0)
        for _ in range(200):
            before = sim.get_price("AAPL")
            sim.step()
            move = abs(sim.get_price("AAPL") / before - 1)
            assert 0.019 < move < 0.051  # 2-5% shock plus a tiny GBM move

    def test_readding_ticker_resumes_last_price(self):
        """Test that remove + re-add resumes the last price instead of resetting to seed."""
        sim = GBMSimulator(tickers=["AAPL", "ZZZZ"], seed=3)
        sim._prices["AAPL"] = 250.0
        zzzz = sim.get_price("ZZZZ")

        sim.remove_ticker("AAPL")
        sim.remove_ticker("ZZZZ")
        sim.add_ticker("AAPL")
        sim.add_ticker("ZZZZ")

        assert sim.get_price("AAPL") == 250.0
        assert sim.get_price("ZZZZ") == zzzz

    def test_open_price_is_first_price_of_session(self):
        """Test that the daily-change reference is the first price, and survives re-adding."""
        sim = GBMSimulator(tickers=["AAPL"], seed=5)
        assert sim.get_open_price("AAPL") == SEED_PRICES["AAPL"]
        for _ in range(100):
            sim.step()
        sim.remove_ticker("AAPL")
        sim.add_ticker("AAPL")
        assert sim.get_open_price("AAPL") == SEED_PRICES["AAPL"]
        assert sim.get_open_price("NOPE") is None
