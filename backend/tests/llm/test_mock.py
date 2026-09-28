import pytest

from app.llm.mock import DEFAULT_MOCK_MESSAGE, mock_chat_response


@pytest.mark.parametrize(
    ("text", "message", "ticker", "quantity"),
    [
        ("buy 5 AAPL", "Buying 5 AAPL for you.", "AAPL", 5.0),
        ("BUY 5 aapl", "Buying 5 AAPL for you.", "AAPL", 5.0),
        ("please buy 2.5 msft.", "Buying 2.5 MSFT for you.", "MSFT", 2.5),
        ("Buy 1 BRK.B", "Buying 1 BRK.B for you.", "BRK.B", 1.0),
    ],
)
def test_buy(text, message, ticker, quantity):
    r = mock_chat_response(text)
    assert r.message == message
    assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [(ticker, "buy", quantity)]
    assert r.watchlist_changes == []


def test_sell():
    r = mock_chat_response("Sell 3 tsla")
    assert r.message == "Selling 3 TSLA for you."
    assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [("TSLA", "sell", 3.0)]
    assert r.watchlist_changes == []


@pytest.mark.parametrize("text", ["add PYPL", "Watch pypl", "please ADD pypl to my list"])
def test_add(text):
    r = mock_chat_response(text)
    assert r.message == "Adding PYPL to your watchlist."
    assert [(w.ticker, w.action) for w in r.watchlist_changes] == [("PYPL", "add")]
    assert r.trades == []


@pytest.mark.parametrize("text", ["remove NFLX", "Unwatch nflx"])
def test_remove(text):
    r = mock_chat_response(text)
    assert r.message == "Removing NFLX from your watchlist."
    assert [(w.ticker, w.action) for w in r.watchlist_changes] == [("NFLX", "remove")]
    assert r.trades == []


@pytest.mark.parametrize(
    "text", ["hello", "how is my portfolio?", "show my watchlist", "buy some apple", ""]
)
def test_default(text):
    r = mock_chat_response(text)
    assert r.message == DEFAULT_MOCK_MESSAGE
    assert r.trades == [] and r.watchlist_changes == []


def test_first_match_wins():
    r = mock_chat_response("sell 1 MSFT then buy 2 AAPL and add PYPL")
    assert [(t.ticker, t.side) for t in r.trades] == [("AAPL", "buy")]
    assert r.watchlist_changes == []
