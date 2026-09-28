"""Trades, positions, cash and snapshots."""

import sqlite3
import threading

import pytest

from app import db


def _state():
    return db.get_cash_balance(), db.get_positions(), db.get_trades()


class TestBuy:
    def test_buy_opens_position_and_debits_cash(self):
        trade = db.record_trade("AAPL", "buy", 10, 190.0)

        assert db.get_cash_balance() == pytest.approx(8100.0)
        pos = db.get_position("AAPL")
        assert pos["ticker"] == "AAPL"
        assert pos["quantity"] == 10
        assert pos["avg_cost"] == 190.0
        assert pos["updated_at"] == trade["executed_at"]

    def test_returns_trade_row(self):
        trade = db.record_trade("aapl", "buy", 2, 100.0)
        assert set(trade) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
        assert trade["ticker"] == "AAPL"
        assert trade["side"] == "buy"
        assert trade["quantity"] == 2
        assert trade["price"] == 100.0
        assert db.get_trades() == [trade]

    def test_second_buy_uses_weighted_average_cost(self):
        db.record_trade("AAPL", "buy", 10, 100.0)
        db.record_trade("AAPL", "buy", 30, 200.0)
        pos = db.get_position("AAPL")
        assert pos["quantity"] == 40
        assert pos["avg_cost"] == pytest.approx(175.0)
        assert db.get_cash_balance() == pytest.approx(10000 - 1000 - 6000)

    def test_insufficient_cash_leaves_state_unchanged(self):
        db.record_trade("AAPL", "buy", 1, 100.0)
        before = _state()
        with pytest.raises(db.InsufficientCashError):
            db.record_trade("AAPL", "buy", 100, 100.0)
        with pytest.raises(db.InsufficientCashError):
            db.record_trade("MSFT", "buy", 1, 1_000_000.0)
        assert _state() == before

    def test_can_spend_exactly_all_cash(self):
        db.record_trade("AAPL", "buy", 50, 200.0)
        assert db.get_cash_balance() == 0.0

    def test_spending_all_cash_with_float_rounding(self):
        price = 10000.0 / 3
        db.record_trade("AAPL", "buy", 3, price)
        assert db.get_cash_balance() == pytest.approx(0.0, abs=1e-6)

    def test_fractional_shares(self):
        db.record_trade("NVDA", "buy", 0.5, 800.0)
        db.record_trade("NVDA", "buy", 0.25, 1000.0)
        pos = db.get_position("NVDA")
        assert pos["quantity"] == pytest.approx(0.75)
        assert pos["avg_cost"] == pytest.approx((400 + 250) / 0.75)
        assert db.get_cash_balance() == pytest.approx(9350.0)


class TestSell:
    def test_partial_sell_keeps_avg_cost_and_credits_cash(self):
        db.record_trade("AAPL", "buy", 10, 100.0)
        db.record_trade("AAPL", "sell", 4, 150.0)
        pos = db.get_position("AAPL")
        assert pos["quantity"] == 6
        assert pos["avg_cost"] == 100.0
        assert db.get_cash_balance() == pytest.approx(10000 - 1000 + 600)

    def test_sell_at_a_loss(self):
        db.record_trade("TSLA", "buy", 10, 250.0)
        db.record_trade("TSLA", "sell", 10, 200.0)
        assert db.get_cash_balance() == pytest.approx(9500.0)

    def test_selling_everything_deletes_position(self):
        db.record_trade("AAPL", "buy", 10, 100.0)
        db.record_trade("AAPL", "sell", 10, 110.0)
        assert db.get_position("AAPL") is None
        assert db.get_positions() == []
        assert db.get_cash_balance() == pytest.approx(10100.0)

    def test_selling_fractional_remainder_within_epsilon_deletes_position(self):
        db.record_trade("AAPL", "buy", 0.1, 100.0)
        db.record_trade("AAPL", "buy", 0.2, 100.0)  # 0.30000000000000004 held
        db.record_trade("AAPL", "sell", 0.3, 100.0)
        assert db.get_position("AAPL") is None

    def test_rebuy_after_closing_starts_fresh_avg_cost(self):
        db.record_trade("AAPL", "buy", 10, 100.0)
        db.record_trade("AAPL", "sell", 10, 100.0)
        db.record_trade("AAPL", "buy", 5, 300.0)
        assert db.get_position("AAPL")["avg_cost"] == 300.0

    def test_selling_more_than_held_leaves_state_unchanged(self):
        db.record_trade("AAPL", "buy", 5, 100.0)
        before = _state()
        with pytest.raises(db.InsufficientSharesError):
            db.record_trade("AAPL", "sell", 6, 100.0)
        assert _state() == before

    def test_selling_unheld_ticker_raises(self):
        before = _state()
        with pytest.raises(db.InsufficientSharesError):
            db.record_trade("MSFT", "sell", 1, 100.0)
        assert _state() == before


class TestValidation:
    @pytest.mark.parametrize(
        "side, quantity, price",
        [("hold", 1, 100.0), ("buy", 0, 100.0), ("buy", -1, 100.0), ("sell", 1, 0.0),
         ("buy", float("nan"), 100.0)],
    )
    def test_invalid_arguments_raise_value_error(self, side, quantity, price):
        before = _state()
        with pytest.raises(ValueError):
            db.record_trade("AAPL", side, quantity, price)
        assert _state() == before

    def test_side_is_case_insensitive(self):
        assert db.record_trade("AAPL", "BUY", 1, 100.0)["side"] == "buy"

    def test_unknown_user_raises(self):
        with pytest.raises(LookupError):
            db.get_cash_balance(user_id="nobody")
        with pytest.raises(LookupError):
            db.record_trade("AAPL", "buy", 1, 100.0, user_id="nobody")


class TestAtomicity:
    def test_failure_mid_transaction_rolls_back(self, db_path):
        # A trigger makes the final trades INSERT fail after cash and position were updated.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TRIGGER fail_trade BEFORE INSERT ON trades "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        conn.commit()
        conn.close()

        with pytest.raises(sqlite3.IntegrityError):
            db.record_trade("AAPL", "buy", 1, 100.0)
        assert db.get_cash_balance() == 10000.0
        assert db.get_position("AAPL") is None

    def test_concurrent_buys_never_overspend(self):
        # 20 threads each try to spend $1000 of $10000: exactly 10 must succeed.
        results = []

        def buy():
            try:
                db.record_trade("AAPL", "buy", 10, 100.0)
                results.append("ok")
            except db.InsufficientCashError:
                results.append("rejected")

        threads = [threading.Thread(target=buy) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count("ok") == 10
        assert db.get_cash_balance() == pytest.approx(0.0)
        assert db.get_position("AAPL")["quantity"] == 100
        assert len(db.get_trades()) == 10


class TestQueries:
    def test_positions_ordered_by_ticker(self):
        for ticker in ("MSFT", "AAPL", "NVDA"):
            db.record_trade(ticker, "buy", 1, 10.0)
        assert [p["ticker"] for p in db.get_positions()] == ["AAPL", "MSFT", "NVDA"]
        assert set(db.get_positions()[0]) == {"ticker", "quantity", "avg_cost", "updated_at"}

    def test_get_position_normalizes_and_handles_missing(self):
        db.record_trade("AAPL", "buy", 1, 10.0)
        assert db.get_position(" aapl ")["ticker"] == "AAPL"
        assert db.get_position("MSFT") is None

    def test_trades_newest_first_with_limit(self):
        ids = [db.record_trade("AAPL", "buy", 1, 10.0)["id"] for _ in range(5)]
        assert [t["id"] for t in db.get_trades()] == ids[::-1]
        assert [t["id"] for t in db.get_trades(limit=2)] == ids[:-3:-1]

    def test_positions_and_trades_are_per_user(self):
        db.record_trade("AAPL", "buy", 1, 10.0)
        assert db.get_positions(user_id="other") == []
        assert db.get_trades(user_id="other") == []


class TestSnapshots:
    def test_record_snapshot_returns_row(self):
        snap = db.record_snapshot(10050.5)
        assert set(snap) == {"total_value", "recorded_at"}
        assert snap["total_value"] == 10050.5
        assert db.get_snapshots() == [snap]

    def test_snapshots_oldest_first(self):
        values = [10000.0, 10010.0, 9990.0]
        for v in values:
            db.record_snapshot(v)
        assert [s["total_value"] for s in db.get_snapshots()] == values

    def test_limit_keeps_most_recent(self):
        for v in range(10):
            db.record_snapshot(float(v))
        assert [s["total_value"] for s in db.get_snapshots(limit=3)] == [7.0, 8.0, 9.0]

    def test_timestamps_are_iso_utc(self):
        snap = db.record_snapshot(1.0)
        assert snap["recorded_at"].endswith("+00:00")
