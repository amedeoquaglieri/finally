"""Tests for MassiveDataSource (mocked)."""

import json
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from massive import RESTClient
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import CLIENT_TIMEOUT, MassiveDataSource
from tests.helpers import wait_until

# Unix nanoseconds, as returned in the snapshot API's lastTrade.t / updated fields
TS_NS = 1_707_580_800_123_456_789


def _make_snapshot(ticker: str, price: float, timestamp_ns: int = TS_NS) -> TickerSnapshot:
    """Build a real SDK snapshot from an API-shaped payload.

    Real model objects (not MagicMock) make attribute-name mistakes fail the tests.
    """
    return TickerSnapshot.from_dict(
        {
            "ticker": ticker,
            "day": {"o": price, "h": price, "l": price, "c": price, "v": 1000},
            "lastTrade": {"p": price, "s": 100, "x": 4, "t": timestamp_ns},
            "updated": timestamp_ns,
        }
    )


@pytest.mark.asyncio
class TestMassiveDataSource:
    """Unit tests for MassiveDataSource with mocked API."""

    async def test_poll_updates_cache(self):
        """Test that polling updates the cache."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,  # Long interval so the loop doesn't auto-poll
        )
        source._tickers = ["AAPL", "GOOGL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [
            _make_snapshot("AAPL", 190.50),
            _make_snapshot("GOOGL", 175.25),
        ]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_malformed_snapshot_skipped(self):
        """Test that malformed snapshots are skipped gracefully."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL", "BAD"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        good_snap = _make_snapshot("AAPL", 190.50)
        bad_snap = TickerSnapshot.from_dict({"ticker": "BAD"})  # No lastTrade, no day

        with patch.object(source, "_fetch_snapshots", return_value=[good_snap, bad_snap]):
            await source._poll_once()

        # Good ticker processed, bad one skipped
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("BAD") is None

    async def test_api_error_does_not_crash(self):
        """Test that API errors don't crash the poller."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") is None  # No update happened

    async def test_timestamp_conversion(self):
        """Test that SIP timestamps are converted from nanoseconds to seconds."""
        cache = PriceCache()
        source = MassiveDataSource(
            api_key="test-key",
            price_cache=cache,
            poll_interval=60.0,
        )
        source._tickers = ["AAPL"]
        source._client = MagicMock()  # Satisfy the _poll_once guard

        mock_snapshots = [_make_snapshot("AAPL", 190.50)]

        with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.timestamp == pytest.approx(1707580800.123456789)

    async def test_real_api_payload_is_parsed(self):
        """Test a full API-shaped payload (as returned by the snapshot endpoint)."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        snap = TickerSnapshot.from_dict(
            {
                "ticker": "AAPL",
                "todaysChange": 1.2,
                "todaysChangePerc": 0.63,
                "updated": 1727200000123456789,
                "day": {"o": 190, "h": 192, "l": 189, "c": 191.2, "v": 1000, "vw": 190.5},
                "lastTrade": {"p": 191.23, "s": 100, "x": 4, "t": 1727200000123456789, "i": "a"},
                "prevDay": {"c": 190.0},
            }
        )
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.price == 191.23
        assert update.timestamp == pytest.approx(1727200000.123456789)

    async def test_falls_back_to_day_close_without_last_trade(self):
        """Test that a snapshot without a last trade uses today's close."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        snap = TickerSnapshot.from_dict({"ticker": "AAPL", "day": {"c": 189.75}, "updated": TS_NS})
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.price == 189.75
        assert update.timestamp == pytest.approx(1707580800.123456789)

    async def test_zeroed_day_bar_without_trade_is_skipped(self):
        """Test that a zero price (day bar just after the midnight reset) is not cached."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        snap = TickerSnapshot.from_dict({"ticker": "AAPL", "day": {"c": 0}})
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        assert cache.get("AAPL") is None

    async def test_missing_timestamp_uses_current_time(self):
        """Test that a snapshot without any timestamp gets the current time."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        snap = TickerSnapshot.from_dict({"ticker": "AAPL", "lastTrade": {"p": 190.0}})
        before = time.time()
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update is not None
        assert update.timestamp >= before

    async def test_add_ticker(self):
        """Test adding a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("AAPL")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_uppercase_normalization(self):
        """Test that tickers are normalized to uppercase."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("aapl")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_strips_whitespace(self):
        """Test that ticker whitespace is stripped."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.add_ticker("  AAPL  ")
        assert "AAPL" in source.get_tickers()

    async def test_remove_ticker_normalizes(self):
        """Test that remove_ticker normalizes case and whitespace."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker(" aapl ")
        assert source.get_tickers() == ["GOOGL"]
        assert cache.get("AAPL") is None

    async def test_start_normalizes_and_dedupes_tickers(self):
        """Test that start() normalizes tickers like add_ticker does."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["aapl", " GOOGL ", "AAPL"])

        assert source.get_tickers() == ["AAPL", "GOOGL"]
        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker("AAPL")
        assert "AAPL" not in source.get_tickers()
        assert cache.get("AAPL") is None

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "GOOGL"]

        tickers = source.get_tickers()
        assert tickers == ["AAPL", "GOOGL"]

    async def test_empty_tickers_skips_poll(self):
        """Test that polling is skipped when there are no tickers."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = []

        # Should not call _fetch_snapshots
        with patch.object(source, "_fetch_snapshots") as mock_fetch:
            await source._poll_once()
            mock_fetch.assert_not_called()

    async def test_stop_is_idempotent(self):
        """Test that stop() can be called multiple times."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)

        await source.stop()
        await source.stop()  # Should not raise

    async def test_stop_cancels_task(self):
        """Test that stop() cancels the polling task."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=10.0)

        # Mock the client and start
        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        # Verify task is running
        assert source._task is not None
        assert not source._task.done()

        # Stop and verify task is cancelled
        await source.stop()
        assert source._task is None

    async def test_start_polls_immediately_in_background(self):
        """Test that the first poll runs right after start(), without waiting an interval."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        mock_snapshots = [_make_snapshot("AAPL", 190.50)]

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=mock_snapshots):
                await source.start(["AAPL"])
                await wait_until(lambda: cache.get_price("AAPL") == 190.50)

        await source.stop()

    async def test_start_does_not_block_on_slow_api(self):
        """Test that a slow API never delays start() (and so app startup)."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=60.0)

        def slow_fetch(tickers: list[str]) -> list[TickerSnapshot]:
            time.sleep(0.5)
            return [_make_snapshot("AAPL", 190.50)]

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", side_effect=slow_fetch):
                started = time.monotonic()
                await source.start(["AAPL"])
                assert time.monotonic() - started < 0.25
                assert cache.get("AAPL") is None  # First poll still in flight

                await wait_until(lambda: cache.get("AAPL") is not None)

        await source.stop()

    async def test_client_uses_short_timeouts_and_no_retries(self):
        """Test that SDK-level retries are off (the poll loop already retries)."""
        source = MassiveDataSource(api_key="test-key", price_cache=PriceCache())

        with patch("app.market.massive_client.RESTClient") as client_cls:
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        client_cls.assert_called_once_with(
            api_key="test-key",
            connect_timeout=CLIENT_TIMEOUT,
            read_timeout=CLIENT_TIMEOUT,
            retries=0,
        )
        await source.stop()

    async def test_add_ticker_before_start_is_kept(self):
        """Test that tickers added before start() are merged into the start list."""
        source = MassiveDataSource(api_key="test-key", price_cache=PriceCache())
        await source.add_ticker("tsla")

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        assert source.get_tickers() == ["AAPL", "TSLA"]
        await source.stop()

    async def test_fetch_gets_a_copy_of_the_ticker_list(self):
        """Test that the worker thread never sees the list add_ticker() mutates."""
        source = MassiveDataSource(api_key="test-key", price_cache=PriceCache())
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", return_value=[]) as fetch:
            await source._poll_once()

        [passed] = fetch.call_args.args
        assert passed == ["AAPL"]
        assert passed is not source._tickers

    async def test_previous_close_from_prev_day(self):
        """Test that the previous session's close is the daily-change reference."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        snap = TickerSnapshot.from_dict(
            {"ticker": "AAPL", "lastTrade": {"p": 198.0, "t": TS_NS}, "prevDay": {"c": 180.0}}
        )
        with patch.object(source, "_fetch_snapshots", return_value=[snap]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update.previous_close == 180.0
        assert update.day_change_percent == 10.0

    async def test_missing_prev_day_keeps_first_price_reference(self):
        """Test that without prevDay the reference falls back to the first price seen."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", return_value=[_make_snapshot("AAPL", 190.0)]):
            await source._poll_once()
        with patch.object(source, "_fetch_snapshots", return_value=[_make_snapshot("AAPL", 191.0)]):
            await source._poll_once()

        assert cache.get("AAPL").previous_close == 190.0

    async def test_fetch_calls_sdk_snapshot_endpoint(self):
        """Test the actual SDK call: stock snapshots for exactly the given tickers."""
        source = MassiveDataSource(api_key="test-key", price_cache=PriceCache())
        source._client = MagicMock()
        source._client.get_snapshot_all.return_value = []

        assert source._fetch_snapshots(["AAPL", "MSFT"]) == []
        source._client.get_snapshot_all.assert_called_once_with(
            market_type="stocks",
            tickers=["AAPL", "MSFT"],
        )

    async def test_bad_price_type_skipped(self):
        """Test that a snapshot with a non-numeric price is skipped, not fatal."""
        cache = PriceCache()
        source = MassiveDataSource(api_key="test-key", price_cache=cache)
        source._tickers = ["AAPL", "BAD"]
        source._client = MagicMock()

        bad = TickerSnapshot.from_dict({"ticker": "BAD", "lastTrade": {"p": "n/a", "t": TS_NS}})
        with patch.object(
            source, "_fetch_snapshots", return_value=[bad, _make_snapshot("AAPL", 190.5)]
        ):
            await source._poll_once()

        assert cache.get("BAD") is None
        assert cache.get_price("AAPL") == 190.5


class _FakeSnapshotAPI(BaseHTTPRequestHandler):
    """Serves the snapshot endpoint the way the real API does; records requests."""

    requests: list[tuple[str, dict, str | None]] = []

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        type(self).requests.append((url.path, query, self.headers.get("Authorization")))
        if url.path != "/v2/snapshot/locale/us/markets/stocks/tickers":
            self.send_response(404)
            self.end_headers()
            return
        tickers = query["tickers"][0].split(",")
        body = {
            "status": "OK",
            "tickers": [
                {
                    "ticker": t,
                    "updated": TS_NS,
                    "prevDay": {"c": 100.0},
                    "lastTrade": {"p": 101.0 + i, "t": TS_NS},
                }
                for i, t in enumerate(tickers)
            ],
        }
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def fake_api():
    """A local HTTP server standing in for api.massive.com."""
    _FakeSnapshotAPI.requests = []
    server = HTTPServer(("127.0.0.1", 0), _FakeSnapshotAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


class TestMassiveOverHTTP:
    """Drive the real massive SDK over HTTP, so URL and parsing bugs can't hide behind mocks."""

    async def test_poll_through_real_sdk(self, fake_api):
        cache = PriceCache()
        source = MassiveDataSource(api_key="secret-key", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient", partial(RESTClient, base=fake_api)):
            await source.start(["aapl", "MSFT"])
            await wait_until(lambda: len(cache) == 2)
        await source.stop()

        [(path, query, auth)] = _FakeSnapshotAPI.requests
        assert path == "/v2/snapshot/locale/us/markets/stocks/tickers"
        assert query["tickers"] == ["AAPL,MSFT"]
        assert auth == "Bearer secret-key"

        aapl = cache.get("AAPL")
        assert aapl.price == 101.0
        assert aapl.previous_close == 100.0
        assert aapl.day_change_percent == 1.0
        assert aapl.timestamp == pytest.approx(TS_NS / 1e9)
        assert cache.get_price("MSFT") == 102.0
