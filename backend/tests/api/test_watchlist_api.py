import time

from app import db


def test_get_watchlist(client):
    response = client.get("/api/watchlist")
    assert response.status_code == 200
    tickers = response.json()["tickers"]
    assert [t["ticker"] for t in tickers] == db.DEFAULT_TICKERS
    assert tickers[0] == {
        "ticker": "AAPL",
        "price": 190.0,
        "previous_price": 190.0,
        "change": 0.0,
        "change_percent": 0.0,
        "day_change_percent": 0.0,
        "direction": "flat",
        "timestamp": tickers[0]["timestamp"],
    }
    assert isinstance(tickers[0]["timestamp"], float)


def test_add_ticker(harness):
    response = harness.client.post("/api/watchlist", json={"ticker": "pypl"})
    assert response.status_code == 201
    assert response.json()["ticker"] == "PYPL"
    assert response.json()["price"] == 60.0
    assert "PYPL" in harness.source.get_tickers()
    tickers = harness.client.get("/api/watchlist").json()["tickers"]
    assert tickers[-1]["ticker"] == "PYPL"


def test_add_duplicate_is_409(client):
    response = client.post("/api/watchlist", json={"ticker": "AAPL"})
    assert response.status_code == 409
    assert response.json() == {"detail": "AAPL is already in your watchlist"}


def test_add_invalid_is_400(client):
    response = client.post("/api/watchlist", json={"ticker": "not a ticker"})
    assert response.status_code == 400
    assert "Invalid ticker" in response.json()["detail"]
    assert client.post("/api/watchlist", json={}).status_code == 400


def test_remove_ticker(harness):
    response = harness.client.delete("/api/watchlist/aapl")
    assert response.status_code == 204
    assert response.content == b""
    assert "AAPL" not in harness.source.get_tickers()
    tickers = harness.client.get("/api/watchlist").json()["tickers"]
    assert "AAPL" not in [t["ticker"] for t in tickers]


def test_remove_missing_is_404(client):
    response = client.delete("/api/watchlist/PYPL")
    assert response.status_code == 404
    assert response.json() == {"detail": "PYPL is not in your watchlist"}


def test_removing_held_ticker_keeps_it_priced(harness):
    client = harness.client
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 2, "side": "buy"})

    assert client.delete("/api/watchlist/AAPL").status_code == 204

    assert "AAPL" in harness.source.get_tickers()
    harness.source.set_price("AAPL", 200.0)
    position = client.get("/api/portfolio").json()["positions"][0]
    assert position["current_price"] == 200.0

    # Selling out afterwards stops tracking it
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 2, "side": "sell"})
    deadline = time.monotonic() + 2
    while "AAPL" in harness.source.get_tickers() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "AAPL" not in harness.source.get_tickers()
