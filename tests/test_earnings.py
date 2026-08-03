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


# ---------- _today (SGT-aware) ----------

def test_today_is_sgt_aware_not_server_utc(monkeypatch):
    """The bot runs on Render (UTC), but a same-day BMO/AMC check needs
    'today' to follow the user's own SGT clock — plain date.today() is wrong
    for roughly a third of the SGT calendar day (00:00-08:00 SGT is still
    'yesterday' in UTC)."""
    from datetime import datetime as real_datetime
    import pytz as real_pytz

    # 2026-01-15 23:00 UTC == 2026-01-16 07:00 SGT (SGT = UTC+8)
    fixed_utc = real_pytz.utc.localize(real_datetime(2026, 1, 15, 23, 0))

    class _FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    monkeypatch.setattr(earnings, "datetime", _FixedDatetime)

    assert earnings._today() == real_datetime(2026, 1, 16).date()


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


def test_fetch_earnings_recent_unreported_shows_as_pending(monkeypatch):
    """A recent past date with epsActual still null (Finnhub backfill lag)
    should surface as 'reported, results pending' rather than vanishing
    entirely — that's exactly when it's most relevant to see, and a real
    reported bug: it used to disappear from earnings watch in this window
    (no longer 'upcoming', not yet 'recent' since there were no figures)."""
    from datetime import date, timedelta
    today = date.today()
    payload = {
        "earningsCalendar": [
            {"date": (today - timedelta(days=1)).isoformat(), "hour": "amc",
             "epsEstimate": 1.0, "epsActual": None},
        ]
    }
    monkeypatch.setattr("earnings.requests.get", lambda url, params, timeout: _FakeResponse(payload))

    result = fetch_earnings("AAPL")
    assert result["last"] is not None
    assert result["last"]["days_since"] == 1
    assert result["last"]["eps_actual"] is None


def test_fetch_earnings_old_unreported_past_event_shows_nothing(monkeypatch):
    """An old past date beyond the recent window with epsActual still null
    isn't meaningfully 'recent' anymore — genuinely nothing to show."""
    from datetime import date, timedelta
    today = date.today()
    payload = {
        "earningsCalendar": [
            {"date": (today - timedelta(days=10)).isoformat(), "hour": "amc",
             "epsEstimate": 1.0, "epsActual": None},
        ]
    }
    monkeypatch.setattr("earnings.requests.get", lambda url, params, timeout: _FakeResponse(payload))

    result = fetch_earnings("AAPL")
    assert result["last"] is None


def test_fetch_earnings_today_unconfirmed_stays_upcoming(monkeypatch):
    """Today's report date with no actuals yet (e.g. an AMC release still
    hours away) shouldn't be claimed as 'reported' — a bare date comparison
    can't tell a not-yet-happened AMC release from an already-done BMO one.
    It should surface as still-upcoming (days_until=0), not as a past/
    pending report."""
    from datetime import date
    today = date.today()
    payload = {"earningsCalendar": [
        {"date": today.isoformat(), "hour": "amc", "epsEstimate": 0.42, "epsActual": None},
    ]}
    monkeypatch.setattr("earnings.requests.get", lambda url, params, timeout: _FakeResponse(payload))

    result = fetch_earnings("NBIS")

    assert result["next"] is not None
    assert result["next"]["days_until"] == 0
    assert result["last"] is None


def test_fetch_earnings_today_confirmed_by_actuals_shows_as_reported(monkeypatch):
    """Today's date but actuals are ALREADY populated (e.g. a fast BMO
    turnaround) proves the report already happened — show it as reported
    with real numbers, not as still-upcoming."""
    from datetime import date
    today = date.today()
    payload = {"earningsCalendar": [
        {"date": today.isoformat(), "hour": "bmo", "epsEstimate": 0.42, "epsActual": 0.50},
    ]}
    monkeypatch.setattr("earnings.requests.get", lambda url, params, timeout: _FakeResponse(payload))

    result = fetch_earnings("NBIS")

    assert result["next"] is None
    assert result["last"] is not None
    assert result["last"]["days_since"] == 0
    assert result["last"]["eps_actual"] == 0.50


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


def test_fetch_events_refetches_after_ttl_expires_regardless_of_event_distance(monkeypatch):
    """A reschedule should show up once the flat TTL elapses, whether the
    cached event was close or far out — this is the reported bug: NBIS
    rescheduling wasn't reflected because an earlier tiered TTL only
    refreshed sooner for events that were *already* near-term, so a
    reschedule announced while the old date was still several days away
    kept trusting a stale snapshot for up to a day."""
    from datetime import date, timedelta
    today = date.today()
    payload = {"earningsCalendar": [
        {"date": (today + timedelta(days=10)).isoformat(), "hour": "amc", "epsEstimate": 1.0, "epsActual": None},
    ]}
    calls = {"n": 0}

    def fake_get(url, params, timeout):
        calls["n"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr("earnings.requests.get", fake_get)
    # Seeded from the real wall clock, not an arbitrary epoch: date.today() in
    # this environment derives from time.time(), so faking it to e.g. 1970
    # would silently corrupt "today" too.
    fake_now = [earnings.time.time()]
    monkeypatch.setattr(earnings.time, "time", lambda: fake_now[0])

    fetch_earnings("AAPL")
    fake_now[0] += earnings.EARNINGS_CACHE_TTL_SECONDS + 1
    fetch_earnings("AAPL")

    assert calls["n"] == 2


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


def test_format_earnings_line_upcoming_unconfirmed_timing():
    """Finnhub often leaves before/after-market timing blank until closer to
    the date (especially for smaller tickers) — say so explicitly rather
    than silently dropping it."""
    from datetime import date
    next_event = {"date": date(2026, 1, 30), "days_until": 5, "timing": None, "eps_estimate": None}
    line = format_earnings_line("NBIS", "USD", fmt_money, next_event=next_event)
    assert line == "NBIS — in 5d (Fri 30 Jan, timing TBD)"


def test_format_earnings_line_reports_today():
    next_event = {"date": None, "days_until": 0, "timing": "after market close", "eps_estimate": 0.42}
    line = format_earnings_line("NBIS", "USD", fmt_money, next_event=next_event)
    assert line == "NBIS — reports today (after market close)"


def test_format_earnings_line_recent_with_surprise():
    last_event = {"date": None, "days_since": 1, "eps_actual": 1.42, "eps_estimate": 1.35, "surprise_pct": 5.2}
    line = format_earnings_line("DRAM", "USD", fmt_money, last_event=last_event)
    assert line == "DRAM — reported 1d ago: EPS $1.42 vs $1.35 est (+5.2%) 🟢"


def test_format_earnings_line_recent_negative_surprise():
    last_event = {"date": None, "days_since": 3, "eps_actual": 0.88, "eps_estimate": 0.95, "surprise_pct": -7.4}
    line = format_earnings_line("ECHO", "USD", fmt_money, last_event=last_event)
    assert "🔴" in line
    assert "-7.4%" in line


def test_format_earnings_line_pending_results():
    last_event = {"date": None, "days_since": 0, "eps_actual": None, "eps_estimate": 2.3, "surprise_pct": None}
    line = format_earnings_line("NBIS", "USD", fmt_money, last_event=last_event)
    assert line == "NBIS — reported 0d ago: results pending"


def test_format_earnings_line_neither():
    assert format_earnings_line("AAPL", "USD", fmt_money) == "AAPL — no earnings data"
