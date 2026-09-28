import asyncio
import math

import pytest

from app import db
from app.services import watchlist as watchlist_service
from app.services.portfolio import TradeError, execute_trade, format_quantity, get_portfolio
from tests.helpers import wait_until


class TestExecuteTrade:
    async def test_buy_debits_cash_and_opens_position(self, ctx):
        trade = execute_trade(ctx, "AAPL", "buy", 10)

        assert trade["ticker"] == "AAPL"
        assert trade["side"] == "buy"
        assert trade["quantity"] == 10
        assert trade["price"] == 190.0
        assert {"id", "executed_at"} <= trade.keys()
        assert db.get_cash_balance() == pytest.approx(10000 - 1900)
        position = db.get_position("AAPL")
        assert position["quantity"] == 10
        assert position["avg_cost"] == 190.0

    async def test_normalizes_ticker_and_side(self, ctx):
        trade = execute_trade(ctx, "  aapl ", " BUY ", "2")
        assert trade["ticker"] == "AAPL"
        assert trade["side"] == "buy"
        assert trade["quantity"] == 2

    async def test_fractional_quantity(self, ctx):
        execute_trade(ctx, "NVDA", "buy", 0.5)
        assert db.get_position("NVDA")["quantity"] == 0.5
        assert db.get_cash_balance() == pytest.approx(9600)

    async def test_sell_credits_cash_and_reduces_position(self, ctx):
        execute_trade(ctx, "AAPL", "buy", 10)
        ctx.market_source.set_price("AAPL", 200.0)

        trade = execute_trade(ctx, "AAPL", "sell", 4)

        assert trade["price"] == 200.0
        assert db.get_cash_balance() == pytest.approx(10000 - 1900 + 800)
        position = db.get_position("AAPL")
        assert position["quantity"] == 6
        assert position["avg_cost"] == 190.0

    async def test_sell_at_a_loss(self, ctx):
        execute_trade(ctx, "TSLA", "buy", 4)
        ctx.market_source.set_price("TSLA", 200.0)
        execute_trade(ctx, "TSLA", "sell", 4)
        assert db.get_cash_balance() == pytest.approx(10000 - 200)
        assert db.get_position("TSLA") is None

    async def test_insufficient_cash(self, ctx):
        with pytest.raises(TradeError, match="Insufficient cash"):
            execute_trade(ctx, "NVDA", "buy", 13)  # $10,400 > $10,000
        assert db.get_cash_balance() == 10000
        assert db.get_position("NVDA") is None
        assert db.get_trades() == []

    async def test_insufficient_cash_message_is_readable(self, ctx):
        with pytest.raises(TradeError) as exc:
            execute_trade(ctx, "AAPL", "buy", 1_000_000)
        assert str(exc.value) == (
            "Insufficient cash: buying 1,000,000 AAPL at $190.00 costs $190,000,000.00, "
            "but you have $10,000.00"
        )

    async def test_insufficient_shares_message_is_readable(self, ctx):
        execute_trade(ctx, "AAPL", "buy", 2.5)
        with pytest.raises(TradeError) as exc:
            execute_trade(ctx, "AAPL", "sell", 1_000_000)
        assert str(exc.value) == (
            "Insufficient shares: you hold 2.5 AAPL but tried to sell 1,000,000"
        )

    async def test_insufficient_shares(self, ctx):
        execute_trade(ctx, "AAPL", "buy", 3)
        with pytest.raises(TradeError, match="you hold 3 AAPL but tried to sell 5"):
            execute_trade(ctx, "AAPL", "sell", 5)
        assert db.get_position("AAPL")["quantity"] == 3

    async def test_sell_without_position(self, ctx):
        with pytest.raises(TradeError, match="Insufficient shares"):
            execute_trade(ctx, "AAPL", "sell", 1)

    async def test_no_price_yet(self, ctx):
        with pytest.raises(TradeError, match="No price available for XYZ yet"):
            execute_trade(ctx, "xyz", "buy", 1)

    @pytest.mark.parametrize("ticker", ["", "123", "A B", "TOOLONGTICKER", "$AAPL"])
    async def test_invalid_ticker(self, ctx, ticker):
        with pytest.raises(TradeError, match="Invalid ticker"):
            execute_trade(ctx, ticker, "buy", 1)

    async def test_invalid_side(self, ctx):
        with pytest.raises(TradeError, match="Side must be"):
            execute_trade(ctx, "AAPL", "short", 1)

    @pytest.mark.parametrize("quantity", [0, -1, math.nan, math.inf, "abc", None])
    async def test_invalid_quantity(self, ctx, quantity):
        with pytest.raises(TradeError, match="Quantity must be a positive number"):
            execute_trade(ctx, "AAPL", "buy", quantity)

    async def test_records_snapshot_after_trade(self, ctx):
        execute_trade(ctx, "AAPL", "buy", 10)
        ctx.market_source.set_price("AAPL", 200.0)
        execute_trade(ctx, "AAPL", "buy", 1)

        snapshots = db.get_snapshots()
        assert [s["total_value"] for s in snapshots] == [10000.0, 10100.0]

    async def test_failed_trade_records_no_snapshot(self, ctx):
        with pytest.raises(TradeError):
            execute_trade(ctx, "AAPL", "sell", 1)
        assert db.get_snapshots() == []


@pytest.mark.parametrize(
    ("quantity", "text"),
    [
        (1_000_000, "1,000,000"),
        (5.0, "5"),
        (0.5, "0.5"),
        (1234.25, "1,234.25"),
        (1e7, "10,000,000"),
    ],
)
def test_format_quantity(quantity, text):
    assert format_quantity(quantity) == text


class TestTickerTracking:
    async def test_closing_unwatched_position_stops_tracking(self, ctx):
        await watchlist_service.add_to_watchlist(ctx, "PYPL")
        execute_trade(ctx, "PYPL", "buy", 5)
        await watchlist_service.remove_from_watchlist(ctx, "PYPL")
        assert "PYPL" in ctx.market_source.get_tickers()  # still held

        execute_trade(ctx, "PYPL", "sell", 5)

        await wait_until(lambda: "PYPL" not in ctx.market_source.get_tickers())
        assert ctx.price_cache.get("PYPL") is None

    async def test_closing_watched_position_keeps_tracking(self, ctx):
        execute_trade(ctx, "AAPL", "buy", 5)
        execute_trade(ctx, "AAPL", "sell", 5)
        await asyncio.sleep(0.05)  # let the scheduled tracking task run
        assert "AAPL" in ctx.market_source.get_tickers()

    async def test_bought_ticker_missing_from_source_gets_tracked(self, ctx):
        # A price without tracking can't normally happen; the service repairs it anyway
        ctx.price_cache.update("PYPL", 60.0)
        execute_trade(ctx, "PYPL", "buy", 1)
        await wait_until(lambda: "PYPL" in ctx.market_source.get_tickers())

    def test_trade_without_event_loop(self, ctx):
        # Called from a plain thread with no loop bound: trade still succeeds
        execute_trade(ctx, "AAPL", "buy", 1)
        assert db.get_position("AAPL")["quantity"] == 1


class TestGetPortfolio:
    async def test_fresh_portfolio(self, ctx):
        assert get_portfolio(ctx) == {
            "cash_balance": 10000.0,
            "positions_value": 0.0,
            "total_value": 10000.0,
            "unrealized_pnl": 0.0,
            "positions": [],
        }

    async def test_valuation_and_pnl(self, ctx):
        execute_trade(ctx, "AAPL", "buy", 10)  # 1900
        execute_trade(ctx, "TSLA", "buy", 4)  # 1000
        ctx.market_source.set_price("AAPL", 195.0)
        ctx.market_source.set_price("TSLA", 225.0)

        portfolio = get_portfolio(ctx)

        assert portfolio["cash_balance"] == 7100.0
        assert portfolio["positions_value"] == 1950.0 + 900.0
        assert portfolio["total_value"] == 7100.0 + 2850.0
        assert portfolio["unrealized_pnl"] == 50.0 - 100.0
        aapl, tsla = portfolio["positions"]
        assert aapl == {
            "ticker": "AAPL",
            "quantity": 10,
            "avg_cost": 190.0,
            "current_price": 195.0,
            "market_value": 1950.0,
            "unrealized_pnl": 50.0,
            "unrealized_pnl_percent": 2.63,
            "weight": round(1950 / 9950 * 100, 2),
        }
        assert tsla["unrealized_pnl"] == -100.0
        assert tsla["unrealized_pnl_percent"] == -10.0
        assert tsla["weight"] == round(900 / 9950 * 100, 2)

    async def test_position_without_price_is_valued_at_cost(self, ctx):
        execute_trade(ctx, "AAPL", "buy", 10)
        ctx.price_cache.remove("AAPL")

        position = get_portfolio(ctx)["positions"][0]

        assert position["current_price"] is None
        assert position["market_value"] == 1900.0
        assert position["unrealized_pnl"] == 0.0
        assert get_portfolio(ctx)["total_value"] == 10000.0
