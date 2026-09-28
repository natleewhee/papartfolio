import pandas as pd
import pytest

import benchmark


def _history(closes_by_date):
    index = pd.to_datetime(list(closes_by_date)).tz_localize("America/New_York")
    return pd.DataFrame({"Close": list(closes_by_date.values())}, index=index)


def test_change_since_uses_last_close_before_start_date(monkeypatch):
    history = _history({"2026-09-17": 90.0, "2026-09-18": 100.0, "2026-09-21": 999.0})
    monkeypatch.setattr(benchmark, "_fetch_history", lambda symbol: history)
    monkeypatch.setattr(benchmark, "get_price", lambda symbol: {"price": 110.0, "change_pct": 1.0})
    # Snapshot dated Mon 21st reflects Fri 18th's close.
    assert benchmark.change_pct_since("2026-09-21") == pytest.approx(10.0)


def test_change_since_none_when_history_too_short(monkeypatch):
    monkeypatch.setattr(benchmark, "_fetch_history", lambda symbol: _history({"2026-09-21": 100.0}))
    monkeypatch.setattr(benchmark, "get_price", lambda symbol: {"price": 110.0})
    assert benchmark.change_pct_since("2026-09-21") is None


def test_change_since_none_on_fetch_failure(monkeypatch):
    def _boom(symbol):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr(benchmark, "_fetch_history", _boom)
    assert benchmark.change_pct_since("2026-09-21") is None


def test_daily_change(monkeypatch):
    monkeypatch.setattr(benchmark, "get_price", lambda symbol: {"price": 500, "change_pct": 0.8})
    assert benchmark.daily_change_pct() == 0.8
    monkeypatch.setattr(benchmark, "get_price", lambda symbol: None)
    assert benchmark.daily_change_pct() is None


def test_comparison_line():
    assert benchmark.comparison_line(1.2, 0.8) == "vs S&P 500: +0.80% (you +0.40 pts)"
    assert benchmark.comparison_line(-1.0, 0.5) == "vs S&P 500: +0.50% (you -1.50 pts)"
    assert benchmark.comparison_line(1.2, None) == ""
