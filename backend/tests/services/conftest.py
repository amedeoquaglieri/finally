import pytest

from app import db
from app.context import AppContext
from app.market import PriceCache

from .fakes import FakeSource


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.init_db(tmp_path / "finally.db")


@pytest.fixture
async def ctx() -> AppContext:
    """Context with a started fake source tracking the seeded watchlist."""
    cache = PriceCache()
    source = FakeSource(cache)
    await source.start(db.get_watchlist())
    return AppContext(price_cache=cache, market_source=source)
