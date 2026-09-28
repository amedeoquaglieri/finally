import pytest

POSITION_KEYS = {
    "ticker",
    "quantity",
    "avg_cost",
    "current_price",
    "market_value",
    "unrealized_pnl",
    "unrealized_pnl_percent",
    "weight",
}


def trade(client, ticker="AAPL", quantity=10, side="buy"):
    return client.post(
        "/api/portfolio/trade", json={"ticker": ticker, "quantity": quantity, "side": side}
    )


def test_get_portfolio_fresh(client):
    response = client.get("/api/portfolio")
    assert response.status_code == 200
    assert response.json() == {
        "cash_balance": 10000.0,
        "positions_value": 0.0,
        "total_value": 10000.0,
        "unrealized_pnl": 0.0,
        "positions": [],
    }
    # Floats even when empty, so clients never see a bare 0
    assert all(isinstance(v, float) for k, v in response.json().items() if k != "positions")


def test_buy(harness):
    client = harness.client
    response = trade(client, "aapl", 10, "buy")

    assert response.status_code == 200
    body = response.json()
    assert body.keys() == {"trade", "portfolio"}
    assert body["trade"].keys() == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert body["trade"]["ticker"] == "AAPL"
    assert body["trade"]["price"] == 190.0
    assert body["portfolio"]["cash_balance"] == 8100.0
    [position] = body["portfolio"]["positions"]
    assert position.keys() == POSITION_KEYS
    assert position["quantity"] == 10

    harness.source.set_price("AAPL", 195.0)
    portfolio = client.get("/api/portfolio").json()
    assert portfolio["total_value"] == 10050.0
    assert portfolio["unrealized_pnl"] == 50.0
    assert portfolio["positions"][0]["unrealized_pnl_percent"] == 2.63


def test_sell_entire_position(client):
    trade(client, "AAPL", 10, "buy")
    response = trade(client, "AAPL", 10, "sell")
    assert response.status_code == 200
    portfolio = response.json()["portfolio"]
    assert portfolio["positions"] == []
    assert portfolio["cash_balance"] == 10000.0


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"ticker": "NVDA", "quantity": 100, "side": "buy"}, "Insufficient cash"),
        ({"ticker": "AAPL", "quantity": 1, "side": "sell"}, "Insufficient shares"),
        ({"ticker": "XYZ", "quantity": 1, "side": "buy"}, "No price available for XYZ yet"),
        ({"ticker": "12!", "quantity": 1, "side": "buy"}, "Invalid ticker"),
        ({"ticker": "AAPL", "quantity": 1, "side": "hold"}, "Side must be"),
        ({"ticker": "AAPL", "quantity": 0, "side": "buy"}, "Quantity must be a positive number"),
        ({"ticker": "AAPL", "quantity": -5, "side": "buy"}, "Quantity must be a positive number"),
        ({"ticker": "AAPL", "quantity": "lots", "side": "buy"}, "Quantity must be"),
    ],
)
def test_trade_rejected(client, payload, detail):
    response = client.post("/api/portfolio/trade", json=payload)
    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert client.get("/api/portfolio").json()["cash_balance"] == 10000.0


def test_trade_missing_field_is_400_with_string_detail(client):
    response = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "side": "buy"})
    assert response.status_code == 400
    assert response.json() == {"detail": "quantity: Field required"}


def test_trade_malformed_json_is_400(client):
    response = client.post(
        "/api/portfolio/trade", content="{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


def test_history_records_snapshot_after_trade(client):
    initial = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(initial) == 1
    assert initial[0].keys() == {"total_value", "recorded_at"}
    assert initial[0]["total_value"] == 10000.0

    trade(client, "AAPL", 10, "buy")

    snapshots = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(snapshots) == 2
    assert snapshots[-1]["total_value"] == 10000.0
    assert snapshots[0]["recorded_at"] <= snapshots[1]["recorded_at"]


def test_failed_trade_records_no_snapshot(client):
    trade(client, "AAPL", 1, "sell")
    assert len(client.get("/api/portfolio/history").json()["snapshots"]) == 1
