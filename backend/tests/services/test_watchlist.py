import pytest

from app import db
from app.services.portfolio import execute_trade
from app.services.watchlist import (
    WatchlistError,
    add_to_watchlist,
    get_watchlist,
    remove_from_watchlist,
)

ENTRY_KEYS = {
    "ticker",
    "price",
    "previous_price",
    "change",
    "change_percent",
    "day_change_percent",
    "direction",
    "timestamp",
}


async def test_get_watchlist_has_defaults_with_prices(ctx):
    entries = get_watchlist(ctx)
    assert [e["ticker"] for e in entries] == db.DEFAULT_TICKERS
    aapl = entries[0]
    assert aapl.keys() == ENTRY_KEYS
    assert aapl["price"] == 190.0
    assert aapl["direction"] == "flat"
    assert aapl["day_change_percent"] == 0.0


async def test_entry_reflects_price_moves(ctx):
    ctx.market_source.set_price("AAPL", 209.0)
    aapl = get_watchlist(ctx)[0]
    assert aapl["price"] == 209.0
    assert aapl["previous_price"] == 190.0
    assert aapl["change"] == 19.0
    assert aapl["change_percent"] == 10.0
    assert aapl["day_change_percent"] == 10.0
    assert aapl["direction"] == "up"


async def test_add_ticker(ctx):
    entry = await add_to_watchlist(ctx, " pypl ")
    assert entry["ticker"] == "PYPL"
    assert entry["price"] == 60.0
    assert "PYPL" in ctx.market_source.get_tickers()
    assert db.get_watchlist()[-1] == "PYPL"


async def test_add_ticker_without_price_yet(ctx):
    entry = await add_to_watchlist(ctx, "XYZ")
    assert entry == {"ticker": "XYZ", **{k: None for k in ENTRY_KEYS - {"ticker"}}}
    assert "XYZ" in ctx.market_source.get_tickers()


async def test_add_duplicate_is_409(ctx):
    with pytest.raises(WatchlistError, match="AAPL is already in your watchlist") as exc:
        await add_to_watchlist(ctx, "aapl")
    assert exc.value.status_code == 409


@pytest.mark.parametrize("ticker", ["", "   ", "1ABC", "A B", "TOOLONGTICKER", "AA$"])
async def test_add_invalid_is_400(ctx, ticker):
    with pytest.raises(WatchlistError, match="Invalid ticker") as exc:
        await add_to_watchlist(ctx, ticker)
    assert exc.value.status_code == 400
    assert len(db.get_watchlist()) == 10


async def test_add_accepts_dots_and_dashes(ctx):
    assert (await add_to_watchlist(ctx, "brk.b"))["ticker"] == "BRK.B"
    assert (await add_to_watchlist(ctx, "RDS-A"))["ticker"] == "RDS-A"


async def test_remove_ticker_stops_tracking(ctx):
    await remove_from_watchlist(ctx, "aapl")
    assert "AAPL" not in db.get_watchlist()
    assert "AAPL" not in ctx.market_source.get_tickers()
    assert ctx.price_cache.get("AAPL") is None


async def test_remove_missing_is_404(ctx):
    with pytest.raises(WatchlistError, match="PYPL is not in your watchlist") as exc:
        await remove_from_watchlist(ctx, "PYPL")
    assert exc.value.status_code == 404


async def test_remove_keeps_tracking_held_ticker(ctx):
    execute_trade(ctx, "AAPL", "buy", 1)
    await remove_from_watchlist(ctx, "AAPL")
    assert "AAPL" not in db.get_watchlist()
    assert "AAPL" in ctx.market_source.get_tickers()
    assert ctx.price_cache.get_price("AAPL") == 190.0


async def test_readd_after_remove(ctx):
    await remove_from_watchlist(ctx, "AAPL")
    entry = await add_to_watchlist(ctx, "AAPL")
    assert entry["price"] == 190.0
    assert db.get_watchlist()[-1] == "AAPL"
