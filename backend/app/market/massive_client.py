"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging

from massive import RESTClient
from massive.rest.models import SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource, normalize_ticker

logger = logging.getLogger(__name__)

# The poll loop already retries every interval, so SDK-level retries only delay
# a failing poll and, on 429s, spend more of the free tier's 5 requests/minute.
CLIENT_RETRIES = 0
CLIENT_TIMEOUT = 5.0  # seconds, for both connect and read


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all watched
    tickers in a single API call, then writes results to the PriceCache.

    Rate limits:
      - Free tier: 5 req/min → poll every 15s (default)
      - Paid tiers: higher limits → poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(
            api_key=self._api_key,
            connect_timeout=CLIENT_TIMEOUT,
            read_timeout=CLIENT_TIMEOUT,
            retries=CLIENT_RETRIES,
        )
        # Keep any tickers added before start()
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in [*tickers, *self._tickers]))

        # The loop polls immediately, in the background, so a slow or failing
        # API never delays application startup
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll immediately, then every `poll_interval` seconds."""
        while True:
            await self._poll_once()
            await asyncio.sleep(self._interval)

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch snapshots, update cache."""
        if not self._tickers or not self._client:
            return

        try:
            # The Massive RESTClient is synchronous — run in a thread to
            # avoid blocking the event loop. Pass a copy: add_ticker() may
            # append to the list while the thread builds the request.
            snapshots = await asyncio.to_thread(self._fetch_snapshots, list(self._tickers))
            processed = 0
            for snap in snapshots:
                try:
                    price, timestamp = self._extract_price(snap)
                    if price is None:
                        logger.warning("Skipping snapshot for %s: no price", snap.ticker)
                        continue
                    self._cache.update(
                        ticker=snap.ticker,
                        price=price,
                        timestamp=timestamp,
                        previous_close=self._extract_previous_close(snap),
                    )
                    processed += 1
                except (AttributeError, TypeError) as e:
                    logger.warning(
                        "Skipping snapshot for %s: %s",
                        getattr(snap, "ticker", "???"),
                        e,
                    )
            logger.debug("Massive poll: updated %d/%d tickers", processed, len(self._tickers))

        except Exception as e:
            logger.error("Massive poll failed: %s", e)
            # Don't re-raise — the loop will retry on the next interval.
            # Common failures: 401 (bad key), 429 (rate limit), network errors.

    @staticmethod
    def _extract_price(snap: TickerSnapshot) -> tuple[float | None, float | None]:
        """Return (price, unix_seconds) from a snapshot.

        Price comes from the last trade, falling back to today's close when there
        is no trade yet (snapshots are cleared at midnight ET). The timestamp is
        the trade's SIP timestamp, falling back to the snapshot's `updated` field;
        both are Unix *nanoseconds*. A None timestamp lets the cache use now.
        """
        trade = snap.last_trade
        price = trade.price if trade else None
        if not price and snap.day:
            price = snap.day.close
        if not price:  # None or 0 (the day bar is zeroed right after the reset)
            price = None

        ts_ns = (trade.sip_timestamp if trade else None) or snap.updated
        timestamp = ts_ns / 1e9 if ts_ns else None
        return price, timestamp

    @staticmethod
    def _extract_previous_close(snap: TickerSnapshot) -> float | None:
        """Previous session's close (daily-change reference), or None if missing."""
        close = snap.prev_day.close if snap.prev_day else None
        return close or None  # 0 means missing

    def _fetch_snapshots(self, tickers: list[str]) -> list[TickerSnapshot]:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            # Must be the string value: SnapshotMarketType is a plain Enum and the
            # SDK builds the URL from it, so the member itself would request
            # /locale/global/markets/SnapshotMarketType.STOCKS/... instead of
            # /locale/us/markets/stocks/...
            market_type=SnapshotMarketType.STOCKS.value,
            tickers=tickers,
        )
