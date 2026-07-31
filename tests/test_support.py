import math
import pandas as pd
import pytest
import support
from support import (
    _sma, _pivots, _horizon_level, _fetch_history,
    compute_support_levels, compute_resistance_levels, compute_support_levels_bulk,
    resolve_support_levels, resolve_support_levels_bulk,
    format_support_compact, format_resistance_compact, format_support_table,
    near_support_flags, format_near_support_line,
)
from portfolio import fmt_money


@pytest.fixture(autouse=True)
def clear_history_cache():
    """The history cache is module-level state — reset it around every test
    so one test's fake data/clock can't leak into the next."""
    support._history_cache.clear()
    yield
    support._history_cache.clear()


def _synthetic_df(n=250):
    """Downtrend to a floor (~90), recovery, then oscillation around ~110 —
    gives both horizons real swing points and MA crossings to find."""
    closes = []
    for i in range(n):
        if i < 100:
            closes.append(120 - i * 0.3)          # slide 120 -> ~90
        elif i < 180:
            closes.append(90 + (i - 100) * 0.25)  # recover 90 -> ~110
        else:
            closes.append(110 - 5 * math.sin((i - 180) / 6.0))  # oscillate ~105-115
    lows = [c - 1.5 for c in closes]
    highs = [c + 1.5 for c in closes]
    return pd.DataFrame({"Close": closes, "Low": lows, "High": highs})


# ---------- pure helpers ----------

def test_sma():
    assert _sma([1, 2, 3, 4], 2) == 3.5
    assert _sma([1, 2], 5) is None


def test_pivots_finds_local_minima():
    lows = [10, 9, 8, 7, 8, 9, 10]
    assert 7 in _pivots(lows, 2, find_min=True)


def test_pivots_finds_local_maxima():
    highs = [10, 11, 12, 13, 12, 11, 10]
    assert 13 in _pivots(highs, 2, find_min=False)


def test_horizon_level_support_returns_none_when_nothing_below_bound():
    result = _horizon_level(
        current_price=1.0, bound=1.0, extremes=[5, 6, 7], closes=[5, 6, 7],
        window=10, pivot_span=1, ma_periods=[], mode="support",
    )
    assert result is None


def test_horizon_level_resistance_returns_none_when_nothing_above_bound():
    result = _horizon_level(
        current_price=100.0, bound=100.0, extremes=[5, 6, 7], closes=[5, 6, 7],
        window=10, pivot_span=1, ma_periods=[], mode="resistance",
    )
    assert result is None


# ---------- compute_support_levels / compute_resistance_levels ----------

def test_compute_support_levels_finds_floor_below_price(monkeypatch):
    df = _synthetic_df()
    monkeypatch.setattr(support, "_fetch_history", lambda symbol, period="1y": df)

    result = compute_support_levels("TEST", current_price=112.0)

    assert result["symbol"] == "TEST"
    st, mt = result["short_term"], result["mid_term"]
    assert st is not None and st["level"] < 112.0
    assert mt is not None and mt["level"] <= st["level"]  # mid-term is the next floor down (or equal)
    assert st["distance_pct"] > 0
    assert mt["distance_pct"] >= st["distance_pct"]


def test_compute_resistance_levels_finds_ceiling_above_price(monkeypatch):
    df = _synthetic_df()
    monkeypatch.setattr(support, "_fetch_history", lambda symbol, period="1y": df)

    result = compute_resistance_levels("TEST", current_price=95.0)

    st, mt = result["short_term"], result["mid_term"]
    assert st is not None and st["level"] > 95.0
    assert mt is not None and mt["level"] >= st["level"]
    assert st["distance_pct"] > 0
    assert mt["distance_pct"] >= st["distance_pct"]


def test_compute_support_levels_at_own_low_has_no_floor(monkeypatch):
    df = _synthetic_df()
    monkeypatch.setattr(support, "_fetch_history", lambda symbol, period="1y": df)

    result = compute_support_levels("TEST", current_price=1.0)

    assert result["short_term"] is None
    assert result["mid_term"] is None


def test_compute_support_levels_no_history_returns_none(monkeypatch):
    monkeypatch.setattr(support, "_fetch_history", lambda symbol, period="1y": None)
    assert compute_support_levels("BAD") is None


def test_compute_support_levels_uses_last_close_when_no_price_given(monkeypatch):
    df = _synthetic_df()
    monkeypatch.setattr(support, "_fetch_history", lambda symbol, period="1y": df)
    result = compute_support_levels("TEST")
    assert result["current_price"] == round(df["Close"].tolist()[-1], 2)


# ---------- history cache ----------

def test_fetch_history_caches_within_ttl(monkeypatch):
    df = _synthetic_df(n=30)
    calls = {"n": 0}

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period="1y", auto_adjust=True):
            calls["n"] += 1
            return df

    monkeypatch.setattr(support.yfinance, "Ticker", FakeTicker)

    first = _fetch_history("AAPL")
    second = _fetch_history("AAPL")

    assert calls["n"] == 1  # second call served from cache
    assert first is second


def test_fetch_history_refetches_after_ttl_expires(monkeypatch):
    df = _synthetic_df(n=30)
    calls = {"n": 0}

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period="1y", auto_adjust=True):
            calls["n"] += 1
            return df

    monkeypatch.setattr(support.yfinance, "Ticker", FakeTicker)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(support.time, "time", lambda: fake_now[0])

    _fetch_history("AAPL")
    fake_now[0] += support.HISTORY_CACHE_TTL_SECONDS + 1
    _fetch_history("AAPL")

    assert calls["n"] == 2


# ---------- bulk helper ----------

def test_compute_support_levels_bulk_returns_per_symbol_results(monkeypatch):
    def fake_compute(symbol, current_price=None):
        return {"symbol": symbol, "current_price": current_price or 0, "short_term": None, "mid_term": None}

    monkeypatch.setattr(support, "compute_support_levels", fake_compute)

    results = compute_support_levels_bulk([("AAPL", 100.0), ("MSFT", 200.0)])

    assert results["AAPL"]["current_price"] == 100.0
    assert results["MSFT"]["current_price"] == 200.0


def test_compute_support_levels_bulk_isolates_failures(monkeypatch):
    def flaky_compute(symbol, current_price=None):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"symbol": symbol, "current_price": 1, "short_term": None, "mid_term": None}

    monkeypatch.setattr(support, "compute_support_levels", flaky_compute)

    results = compute_support_levels_bulk([("AAPL", None), ("BAD", None)])

    assert results["AAPL"] is not None
    assert results["BAD"] is None


def test_compute_support_levels_bulk_empty():
    assert compute_support_levels_bulk([]) == {}


# ---------- resolve_support_levels (manual override) ----------

def test_resolve_support_levels_uses_manual_when_both_given(monkeypatch):
    def fail_if_called(symbol, current_price=None):
        raise AssertionError("should not fetch/compute when both horizons are manual")
    monkeypatch.setattr(support, "compute_support_levels", fail_if_called)

    result = resolve_support_levels("AAPL", current_price=100.0, manual_st=90.0, manual_mt=80.0)

    assert result["symbol"] == "AAPL"
    assert result["short_term"] == {"level": 90.0, "basis": "manual", "distance_pct": 10.0}
    assert result["mid_term"] == {"level": 80.0, "basis": "manual", "distance_pct": 20.0}


def test_resolve_support_levels_both_manual_needs_a_price():
    assert resolve_support_levels("AAPL", current_price=None, manual_st=90.0, manual_mt=80.0) is None


def test_resolve_support_levels_falls_back_to_auto_for_unset_horizon(monkeypatch):
    def fake_compute(symbol, current_price=None):
        return {
            "symbol": symbol,
            "current_price": current_price,
            "short_term": {"level": 95.0, "basis": "swing low", "distance_pct": 5.0},
            "mid_term": {"level": 85.0, "basis": "50d MA", "distance_pct": 15.0},
        }
    monkeypatch.setattr(support, "compute_support_levels", fake_compute)

    result = resolve_support_levels("AAPL", current_price=100.0, manual_st=90.0, manual_mt=None)

    assert result["short_term"] == {"level": 90.0, "basis": "manual", "distance_pct": 10.0}
    assert result["mid_term"] == {"level": 85.0, "basis": "50d MA", "distance_pct": 15.0}  # untouched


def test_resolve_support_levels_no_manual_is_pure_auto(monkeypatch):
    def fake_compute(symbol, current_price=None):
        return {"symbol": symbol, "current_price": current_price, "short_term": None, "mid_term": None}
    monkeypatch.setattr(support, "compute_support_levels", fake_compute)

    result = resolve_support_levels("AAPL", current_price=100.0)

    assert result == {"symbol": "AAPL", "current_price": 100.0, "short_term": None, "mid_term": None}


def test_resolve_support_levels_manual_survives_no_history(monkeypatch):
    """A manually-pinned symbol should still show its manual level even if
    there's not enough price history for the auto-computed horizon."""
    monkeypatch.setattr(support, "compute_support_levels", lambda symbol, current_price=None: None)

    result = resolve_support_levels("NEWCO", current_price=100.0, manual_st=90.0, manual_mt=None)

    assert result["short_term"] == {"level": 90.0, "basis": "manual", "distance_pct": 10.0}
    assert result["mid_term"] is None


def test_resolve_support_levels_no_data_and_no_manual_returns_none(monkeypatch):
    monkeypatch.setattr(support, "compute_support_levels", lambda symbol, current_price=None: None)
    assert resolve_support_levels("BAD", current_price=100.0) is None


def test_resolve_support_levels_bulk_returns_per_symbol_results(monkeypatch):
    def fake_resolve(symbol, current_price=None, manual_st=None, manual_mt=None):
        return {"symbol": symbol, "current_price": current_price, "short_term": None, "mid_term": None}
    monkeypatch.setattr(support, "resolve_support_levels", fake_resolve)

    results = resolve_support_levels_bulk([("AAPL", 100.0, None, None), ("MSFT", 200.0, 190.0, 180.0)])

    assert results["AAPL"]["current_price"] == 100.0
    assert results["MSFT"]["current_price"] == 200.0


def test_resolve_support_levels_bulk_isolates_failures(monkeypatch):
    def flaky_resolve(symbol, current_price=None, manual_st=None, manual_mt=None):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"symbol": symbol, "current_price": 1, "short_term": None, "mid_term": None}
    monkeypatch.setattr(support, "resolve_support_levels", flaky_resolve)

    results = resolve_support_levels_bulk([("AAPL", None, None, None), ("BAD", None, None, None)])

    assert results["AAPL"] is not None
    assert results["BAD"] is None


def test_resolve_support_levels_bulk_empty():
    assert resolve_support_levels_bulk([]) == {}


# ---------- formatting ----------

def test_format_support_compact():
    data = {
        "symbol": "AAPL",
        "current_price": 195.0,
        "short_term": {"level": 182.0, "basis": "swing low", "distance_pct": 6.7},
        "mid_term": None,
    }
    line = format_support_compact(data, "USD", fmt_money)
    assert "AAPL" in line
    assert "$195.00" in line
    assert "$182.00" in line
    assert "-6.7%" in line
    assert "MT n/a" in line


def test_format_resistance_compact():
    data = {
        "symbol": "AAPL",
        "current_price": 195.0,
        "short_term": {"level": 205.0, "basis": "swing high", "distance_pct": 5.1},
        "mid_term": None,
    }
    line = format_resistance_compact(data, "USD", fmt_money)
    assert "$205.00" in line
    assert "+5.1%" in line
    assert "MT n/a" in line


def test_format_support_table_renders_header_and_rows():
    rows = [
        {
            "symbol": "AAPL", "currency": "USD", "current_price": 195.0, "daily_change_%": 1.3,
            "short_term": {"level": 182.0, "basis": "swing low", "distance_pct": 6.7},
            "mid_term": {"level": 168.0, "basis": "50d MA", "distance_pct": 13.8},
        },
        {
            "symbol": "MSFT", "currency": "USD", "current_price": 400.0, "daily_change_%": None,
            "short_term": None, "mid_term": None,
        },
    ]
    table = format_support_table(rows, fmt_money)
    lines = table.split("\n")
    assert lines[0].startswith("SYMBOL")
    assert "%CHG" in lines[0]
    assert "ST%" not in lines[0] and "MT%" not in lines[0]  # dropped: redundant with the near-support flag line
    assert "AAPL" in table and "MSFT" in table
    assert "$195.00" in table
    assert "$182.00" in table
    assert "+1.3%" in table
    assert "n/a" in table  # MSFT has no ST/MT/daily_change_%


def test_format_support_table_handles_missing_price_as_row():
    rows = [{"symbol": "BAD", "currency": "USD", "current_price": None, "daily_change_%": None,
             "short_term": None, "mid_term": None}]
    table = format_support_table(rows, fmt_money)
    assert "BAD" in table
    assert "n/a" in table


# ---------- near_support_flags / format_near_support_line ----------

def test_near_support_flags_picks_closer_horizon_and_sorts_by_distance():
    rows = [
        {
            "symbol": "FAR", "short_term": {"level": 1, "distance_pct": 20.0},
            "mid_term": {"level": 1, "distance_pct": 30.0},
        },
        {
            "symbol": "NEAR", "short_term": {"level": 1, "distance_pct": 4.0},
            "mid_term": {"level": 1, "distance_pct": 1.5},
        },
        {
            "symbol": "MID", "short_term": {"level": 1, "distance_pct": 3.0},
            "mid_term": None,
        },
    ]
    flags = near_support_flags(rows, threshold=5.0)

    assert [f[0] for f in flags] == ["NEAR", "MID"]  # FAR excluded, closest first
    assert flags[0] == ("NEAR", "MT", 1.5)  # MT is the closer of NEAR's two horizons
    assert flags[1] == ("MID", "ST", 3.0)


def test_near_support_flags_empty_when_nothing_qualifies():
    rows = [{"symbol": "FAR", "short_term": {"level": 1, "distance_pct": 20.0}, "mid_term": None}]
    assert near_support_flags(rows, threshold=5.0) == []


def test_format_near_support_line():
    assert format_near_support_line([]) == ""
    line = format_near_support_line([("AAPL", "ST", 4.5), ("MSFT", "MT", 1.2)])
    assert line == "⚠️ Near support: AAPL (ST -4.5%), MSFT (MT -1.2%)"
