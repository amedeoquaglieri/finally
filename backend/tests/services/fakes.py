"""Deterministic market data source for service and API tests."""

from __future__ import annotations

from app.market import MarketDataSource, PriceCache, normalize_ticker

# Fixed prices; tickers not listed here never get a price (like a Massive cache miss)
DEFAULT_PRICES = {
    "AAPL": 190.0,
    "GOOGL": 175.0,
    "MSFT": 420.0,
    "AMZN": 185.0,
    "TSLA": 250.0,
    "NVDA": 800.0,
    "META": 500.0,
    "JPM": 200.0,
    "V": 280.0,
    "NFLX": 600.0,
    "PYPL": 60.0,
}


class FakeSource(MarketDataSource):
    """Tracks tickers and seeds the cache with fixed prices, with no background task."""

    def __init__(self, cache: PriceCache, prices: dict[str, float] | None = None) -> None:
        self.cache = cache
        self.prices = dict(DEFAULT_PRICES if prices is None else prices)
        self.tickers: list[str] = []
        self.started = False
        self.stopped = False

    async def start(self, tickers: list[str]) -> None:
        self.started = True
        for ticker in tickers:
            await self.add_ticker(ticker)

    async def stop(self) -> None:
        self.stopped = True

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self.tickers:
            return
        self.tickers.append(ticker)
        if ticker in self.prices:
            self.cache.update(ticker, self.prices[ticker])

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self.tickers = [t for t in self.tickers if t != ticker]
        self.cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self.tickers)

    def set_price(self, ticker: str, price: float) -> None:
        """Move the market: update the cached price of a tracked ticker."""
        self.prices[ticker] = price
        self.cache.update(ticker, price)
