import pytest

from app import db
from app.context import AppContext
from app.market import MarketDataSource, PriceCache, normalize_ticker

PRICES = {"AAPL": 190.0, "MSFT": 400.0, "PYPL": 60.0}


class FakeSource(MarketDataSource):
    """In-memory market source that just records which tickers are tracked."""

    def __init__(self) -> None:
        self.tickers: set[str] = set()

    async def start(self, tickers: list[str]) -> None:
        self.tickers.update(normalize_ticker(t) for t in tickers)

    async def stop(self) -> None:
        pass

    async def add_ticker(self, ticker: str) -> None:
        self.tickers.add(normalize_ticker(ticker))

    async def remove_ticker(self, ticker: str) -> None:
        self.tickers.discard(normalize_ticker(ticker))

    def get_tickers(self) -> list[str]:
        return sorted(self.tickers)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.init_db(tmp_path / "finally.db")


@pytest.fixture(autouse=True)
def llm_env(monkeypatch):
    """Real-LLM mode by default with a dummy key; tests opt into mock mode."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


@pytest.fixture
def mock_mode(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")


@pytest.fixture
def ctx():
    cache = PriceCache()
    for ticker, price in PRICES.items():
        cache.update(ticker, price)
    source = FakeSource()
    source.tickers.update(db.get_watchlist())
    return AppContext(price_cache=cache, market_source=source)
