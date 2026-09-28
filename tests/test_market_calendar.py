import asyncio
from datetime import date

import pytest

import market_calendar
from market_calendar import (
    us_close_time, previous_weekday,
    run_on_us_trading_days, run_if_us_closes_at, run_if_last_us_session_traded,
)

THANKSGIVING = date(2026, 11, 26)
BLACK_FRIDAY = date(2026, 11, 27)  # NYSE half-day, 1pm ET close
NORMAL_DAY = date(2026, 11, 25)
MONDAY_AFTER = date(2026, 11, 30)


def _run(wrapper_factory, today, *args):
    calls = []

    async def job():
        calls.append(today)

    original = market_calendar._today_us
    market_calendar._today_us = lambda: today
    try:
        asyncio.run(wrapper_factory(job, *args)())
    finally:
        market_calendar._today_us = original
    return bool(calls)


def test_close_times_from_real_calendar():
    assert us_close_time(NORMAL_DAY) == (16, 0)
    assert us_close_time(BLACK_FRIDAY) == (13, 0)
    assert us_close_time(THANKSGIVING) is None
    assert us_close_time(date(2026, 11, 28)) is None  # Saturday


def test_ae1_holiday_skips_open_close_and_early_reconcile():
    assert not _run(run_on_us_trading_days, THANKSGIVING)
    assert not _run(run_if_us_closes_at, THANKSGIVING, (16, 0))
    assert not _run(run_if_us_closes_at, THANKSGIVING, (13, 0))


def test_ae2_catchup_after_holiday_is_skipped():
    # The catch-up on the SGT/ET morning of Black Friday covers Thanksgiving's session.
    assert not _run(run_if_last_us_session_traded, BLACK_FRIDAY)
    assert _run(run_if_last_us_session_traded, THANKSGIVING)  # covers Wed, a normal day


def test_catchup_monday_covers_friday():
    assert _run(run_if_last_us_session_traded, MONDAY_AFTER)  # Black Friday traded (half-day)


def test_ae3_half_day_open_normal_close_early():
    assert _run(run_on_us_trading_days, BLACK_FRIDAY)
    assert _run(run_if_us_closes_at, BLACK_FRIDAY, (13, 0))
    assert not _run(run_if_us_closes_at, BLACK_FRIDAY, (16, 0))


def test_normal_day_runs_only_the_normal_close():
    assert _run(run_if_us_closes_at, NORMAL_DAY, (16, 0))
    assert not _run(run_if_us_closes_at, NORMAL_DAY, (13, 0))


def test_ae5_lookup_failure_fails_open(monkeypatch):
    def _broken():
        raise RuntimeError("calendar unavailable")
    monkeypatch.setattr(market_calendar, "_calendar", _broken)
    assert us_close_time(THANKSGIVING) == (16, 0)
    assert _run(run_on_us_trading_days, THANKSGIVING)
    assert _run(run_if_us_closes_at, THANKSGIVING, (16, 0))
    assert not _run(run_if_us_closes_at, THANKSGIVING, (13, 0))


def test_out_of_range_date_fails_open():
    assert us_close_time(date(2099, 3, 4)) == (16, 0)  # a Wednesday beyond the calendar


@pytest.mark.parametrize("day,expected", [
    (date(2026, 11, 30), date(2026, 11, 27)),  # Mon -> Fri
    (date(2026, 11, 27), date(2026, 11, 26)),  # Fri -> Thu
])
def test_previous_weekday(day, expected):
    assert previous_weekday(day) == expected
