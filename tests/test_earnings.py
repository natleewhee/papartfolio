import pandas as pd
import pytest
import earnings
from earnings import (
    fetch_earnings, fetch_earnings_bulk, earnings_flags, format_earnings_line,
    _fetch_finnhub_calendar,
)
from portfolio import fmt_money


@pytest.fixture(autouse=True)
def clear_earnings_cache():
    earnings._earnings_cache.clear()
    yield
    earnings._earnings_cache.clear()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


# ---------- fetch_earnings (US / Finnhub path) ----------

def test_fetch_earnings_finds_next_and_last(monkeypatch):
    from datetime import date, timedelta
    today = date.today()
    payload = {
        "earningsCalendar": [
            {
                "date": (today - timedelta(days=87)).isoformat(),
                "hour": "amc", "epsEstimate": 2.10, "epsActual": 2.18,
            },
            {
                "date": (today + timedelta(days=5)).isoformat(),
                "hour": "amc", "epsEstimate": 2.30, "epsActual": None,
            },
        ]
    }
    monkeypatch.setattr("earnings.requests.get", lambda url, params, timeout: _FakeResponse(payload))

    result = fetch_earnings("AAPL")

    assert result["symbol"] == "AAPL"
    assert result["next"]["days_until"] == 5
    assert result["next"]["timing"] == "after market close"
    assert result["next"]["eps_estimate"] == 2.30
    assert result["last"]["days_since"] == 87
    assert result["last"]["eps_actual"] == 2.18
    assert result["last"]["surprise_pct"] == pytest.approx(3.8, abs=0.1)


def test_fetch_earnings_skips_unreported_past_events(monkeypatch):
    """A past date with epsActual still null (data lag) shouldn't be treated
    as the 'last reported' quarter."""
    from datetime import date, timedelta
    today = date.today()
    payload = {
        "earningsCalendar": [
            {"date": (today - timedelta(days=2)).isoformat(), "hour": "amc",
             "epsEstimate": 1.0, "epsActual": None},
        ]
    }
    monkeypatch.setattr("earnings.requests.get", lambda url, params, timeout: _FakeResponse(payload))

    result = fetch_earnings("AAPL")
    assert result["last"] is None


def test_fetch_earnings_no_data_returns_none(monkeypatch):
    monkeypatch.setattr("earnings.requests.get", lambda url, params, timeout: (_ for _ in ()).throw(RuntimeError("boom")))
    assert fetch_earnings("AAPL") is None


# ---------- fetch_earnings (SG / yfinance path) ----------

class _FakeTicker:
    def __init__(self, df):
        self._df = df

    def get_earnings_dates(self, limit=8):
        return self._df


def test_fetch_earnings_sg_ticker_uses_yfinance(monkeypatch):
    from datetime import date, timedelta
    today = date.today()
    idx = pd.to_datetime([today - timedelta(days=90), today + timedelta(days=10)])
    df = pd.DataFrame(
        {"EPS Estimate": [0.45, 0.50], "Reported EPS": [0.48, None]},
        index=idx,
    )
    monkeypatch.setattr(earnings.yfinance, "Ticker", lambda symbol: _FakeTicker(df))

    result = fetch_earnings("D05.SI")

    assert result["next"]["days_until"] == 10
    assert result["last"]["eps_actual"] == 0.48
    assert result["last"]["surprise_pct"] == pytest.approx(6.7, abs=0.1)


# ---------- caching ----------

def test_earnings_cache_avoids_refetch(monkeypatch):
    from datetime import date, timedelta
    today = date.today()
    payload = {"earningsCalendar": [
        {"date": (today + timedelta(days=5)).isoformat(), "hour": "bmo", "epsEstimate": 1.0, "epsActual": None},
    ]}
    calls = {"n": 0}

    def fake_get(url, params, timeout):
        calls["n"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr("earnings.requests.get", fake_get)

    fetch_earnings("AAPL")
    fetch_earnings("AAPL")

    assert calls["n"] == 1


# ---------- earnings_flags ----------

def test_earnings_flags_filters_and_sorts_by_proximity():
    results = {
        "FAR": {"symbol": "FAR", "next": {"date": None, "days_until": 30, "timing": None, "eps_estimate": None}, "last": None},
        "SOON": {"symbol": "SOON", "next": {"date": None, "days_until": 2, "timing": None, "eps_estimate": None}, "last": None},
        "MID": {"symbol": "MID", "next": {"date": None, "days_until": 10, "timing": None, "eps_estimate": None}, "last": None},
        "OLD": {"symbol": "OLD", "next": None, "last": {"date": None, "days_since": 10, "eps_actual": 1, "eps_estimate": 1, "surprise_pct": 0}},
        "RECENT": {"symbol": "RECENT", "next": None, "last": {"date": None, "days_since": 1, "eps_actual": 1, "eps_estimate": 1, "surprise_pct": 0}},
    }
    upcoming, recent = earnings_flags(results, upcoming_days=14, recent_days=3)

    assert [s for s, _ in upcoming] == ["SOON", "MID"]  # FAR excluded (30d > 14d), closest first
    assert [s for s, _ in recent] == ["RECENT"]  # OLD excluded (10d > 3d)


def test_earnings_flags_skips_none_results():
    assert earnings_flags({"AAPL": None}) == ([], [])


# ---------- fetch_earnings_bulk ----------

def test_fetch_earnings_bulk_empty():
    assert fetch_earnings_bulk([]) == {}


def test_fetch_earnings_bulk_isolates_failure(monkeypatch):
    def flaky(symbol):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"symbol": symbol, "next": None, "last": None}

    monkeypatch.setattr("earnings.fetch_earnings", flaky)

    result = fetch_earnings_bulk(["AAPL", "BAD"])

    assert result["AAPL"] is not None
    assert result["BAD"] is None


# ---------- format_earnings_line ----------

def test_format_earnings_line_upcoming():
    from datetime import date
    next_event = {"date": date(2026, 1, 30), "days_until": 5, "timing": "after market close", "eps_estimate": 2.1}
    line = format_earnings_line("AAPL", "USD", fmt_money, next_event=next_event)
    assert line == "AAPL — in 5d (Fri 30 Jan, after market close)"


def test_format_earnings_line_recent_with_surprise():
    last_event = {"date": None, "days_since": 1, "eps_actual": 1.42, "eps_estimate": 1.35, "surprise_pct": 5.2}
    line = format_earnings_line("DRAM", "USD", fmt_money, last_event=last_event)
    assert line == "DRAM — reported 1d ago: EPS $1.42 vs $1.35 est (+5.2%) 🟢"


def test_format_earnings_line_recent_negative_surprise():
    last_event = {"date": None, "days_since": 3, "eps_actual": 0.88, "eps_estimate": 0.95, "surprise_pct": -7.4}
    line = format_earnings_line("ECHO", "USD", fmt_money, last_event=last_event)
    assert "🔴" in line
    assert "-7.4%" in line


def test_format_earnings_line_neither():
    assert format_earnings_line("AAPL", "USD", fmt_money) == "AAPL — no earnings data"
