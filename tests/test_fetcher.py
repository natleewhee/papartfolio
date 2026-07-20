from fetcher import is_sg_ticker, get_currency_for_symbol, get_prices_bulk


def test_is_sg_ticker():
    assert is_sg_ticker("D05.SI")
    assert is_sg_ticker("d05.si")
    assert not is_sg_ticker("AAPL")


def test_get_currency_for_symbol():
    assert get_currency_for_symbol("D05.SI") == "SGD"
    assert get_currency_for_symbol("AAPL") == "USD"


def test_get_prices_bulk_empty():
    assert get_prices_bulk([]) == {}


def test_get_prices_bulk_fetches_each_symbol_concurrently(monkeypatch):
    calls = []

    def fake_get_price(symbol):
        calls.append(symbol)
        return {"symbol": symbol, "price": 100.0, "change": 1.0, "change_pct": 1.0}

    monkeypatch.setattr("fetcher.get_price", fake_get_price)

    result = get_prices_bulk(["AAPL", "MSFT", "D05.SI"])

    assert sorted(calls) == ["AAPL", "D05.SI", "MSFT"]
    assert result["AAPL"]["price"] == 100.0
    assert result["MSFT"]["price"] == 100.0
    assert result["D05.SI"]["price"] == 100.0


def test_get_prices_bulk_isolates_per_symbol_failure(monkeypatch):
    def flaky_get_price(symbol):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"symbol": symbol, "price": 50.0, "change": 0, "change_pct": 0}

    monkeypatch.setattr("fetcher.get_price", flaky_get_price)

    result = get_prices_bulk(["AAPL", "BAD"])

    assert result["AAPL"]["price"] == 50.0
    assert result["BAD"] is None
