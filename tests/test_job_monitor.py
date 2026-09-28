import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED

import job_monitor
from job_monitor import handle_event


@pytest.fixture(autouse=True)
def _reset_state():
    job_monitor._failing_jobs.clear()
    yield
    job_monitor._failing_jobs.clear()


def test_success_without_prior_failure_is_silent():
    assert handle_event(EVENT_JOB_EXECUTED, "daily_report", "Daily Portfolio Report") is None


def test_first_error_notifies_with_exception_detail():
    msg = handle_event(EVENT_JOB_ERROR, "price_alerts", "Price Alert Check", ValueError("db down"))
    assert "Price Alert Check" in msg
    assert "ValueError: db down" in msg


def test_repeated_errors_stay_quiet_until_recovery():
    assert handle_event(EVENT_JOB_ERROR, "price_alerts", "Price Alert Check", ValueError("x"))
    assert handle_event(EVENT_JOB_ERROR, "price_alerts", "Price Alert Check", ValueError("x")) is None
    assert handle_event(EVENT_JOB_MISSED, "price_alerts", "Price Alert Check") is None
    recovered = handle_event(EVENT_JOB_EXECUTED, "price_alerts", "Price Alert Check")
    assert "recovered" in recovered
    # a fresh failure after recovery notifies again
    assert handle_event(EVENT_JOB_ERROR, "price_alerts", "Price Alert Check", ValueError("x"))


def test_missed_run_notifies():
    msg = handle_event(EVENT_JOB_MISSED, "daily_report", "Daily Portfolio Report")
    assert "skipped" in msg and "Daily Portfolio Report" in msg


def test_streaks_are_tracked_per_job():
    assert handle_event(EVENT_JOB_ERROR, "a", "A", ValueError("x"))
    assert handle_event(EVENT_JOB_ERROR, "b", "B", ValueError("x"))
