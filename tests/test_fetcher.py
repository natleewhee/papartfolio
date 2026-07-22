import fetcher
from fetcher import is_sg_ticker, get_currency_for_symbol, get_prices_bulk, fetch_extended_hours, fetch_extended_hours_bulk


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


# ---------- fetch_extended_hours ----------

class _FakeTicker:
    def __init__(self, info):
        self._info = info

    @property
    def info(self):
        return self._info


def test_fetch_extended_hours_skips_sg_tickers():
    # No yfinance.Ticker monkeypatch needed — it must short-circuit before that
    assert fetch_extended_hours("D05.SI") is None


def test_fetch_extended_hours_pre_market(monkeypatch):
    info = {"marketState": "PRE", "preMarketPrice": 196.10, "preMarketChangePercent": 0.35}
    monkeypatch.setattr(fetcher.yfinance, "Ticker", lambda symbol: _FakeTicker(info))

    result = fetch_extended_hours("AAPL")

    assert result == {"market_state": "PRE", "price": 196.10, "change_pct": 0.35}


def test_fetch_extended_hours_post_market(monkeypatch):
    info = {"marketState": "POST", "postMarketPrice": 194.0, "postMarketChangePercent": -0.7}
    monkeypatch.setattr(fetcher.yfinance, "Ticker", lambda symbol: _FakeTicker(info))

    result = fetch_extended_hours("AAPL")

    assert result == {"market_state": "POST", "price": 194.0, "change_pct": -0.7}


def test_fetch_extended_hours_none_during_regular_session(monkeypatch):
    info = {"marketState": "REGULAR"}
    monkeypatch.setattr(fetcher.yfinance, "Ticker", lambda symbol: _FakeTicker(info))
    assert fetch_extended_hours("AAPL") is None


def test_fetch_extended_hours_none_when_price_missing(monkeypatch):
    info = {"marketState": "PRE", "preMarketPrice": None}
    monkeypatch.setattr(fetcher.yfinance, "Ticker", lambda symbol: _FakeTicker(info))
    assert fetch_extended_hours("AAPL") is None


def test_fetch_extended_hours_bulk_empty():
    assert fetch_extended_hours_bulk([]) == {}


def test_fetch_extended_hours_bulk_isolates_failure(monkeypatch):
    def flaky(symbol):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"market_state": "PRE", "price": 100.0, "change_pct": 1.0}

    monkeypatch.setattr("fetcher.fetch_extended_hours", flaky)

    result = fetch_extended_hours_bulk(["AAPL", "BAD"])

    assert result["AAPL"]["price"] == 100.0
    assert result["BAD"] is None
