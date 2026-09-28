"""US market (NYSE) holiday and half-day awareness for scheduled jobs.

Every lookup fails open: if the calendar can't answer (library error, date
outside its range), the day is treated as a normal full trading day, so a
calendar problem never silently suppresses a ping or reconciliation."""
import logging
from datetime import datetime, timedelta
from functools import lru_cache, wraps

import pytz

from config import MARKETS

logger = logging.getLogger(__name__)

US_TZ = pytz.timezone(MARKETS["US"]["timezone"])
US_NORMAL_CLOSE = MARKETS["US"]["close"]
US_EARLY_CLOSE = MARKETS["US"]["early_close"]


@lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS")


def us_close_time(day):
    """(hour, minute) ET that the US market closes on `day`, or None on a full
    holiday/weekend. Normal close on any lookup failure."""
    try:
        cal = _calendar()
        stamp = day.isoformat()
        if not cal.is_session(stamp):
            return None
        close = cal.session_close(stamp).tz_convert(US_TZ)
        return (close.hour, close.minute)
    except Exception as e:
        logger.warning(f"⚠️ US market calendar lookup failed for {day}: {e} — assuming a normal trading day")
        return US_NORMAL_CLOSE if day.weekday() < 5 else None


def is_us_trading_day(day):
    return us_close_time(day) is not None


def previous_weekday(day):
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _today_us():
    return datetime.now(US_TZ).date()


def run_on_us_trading_days(func):
    """Skip `func` on a full US holiday (for jobs tied to the US session, e.g. the open ping)."""
    @wraps(func)
    async def wrapper():
        today = _today_us()
        if not is_us_trading_day(today):
            logger.info(f"ℹ️ Skipping {func.__name__}: {today} is a US market holiday")
            return
        await func()
    return wrapper


def run_if_us_closes_at(func, close_hm):
    """Run `func` only if today's US close is `close_hm`. Close-time jobs are
    scheduled at both the normal and the early close; this picks the one that
    matches the day — and skips both on a full holiday."""
    @wraps(func)
    async def wrapper():
        today = _today_us()
        if us_close_time(today) != tuple(close_hm):
            return
        await func()
    return wrapper


def run_if_last_us_session_traded(func):
    """For the evening catch-up reconciliation: it picks up IBKR's EOD data for
    the most recent US weekday, so skip it when that weekday was a holiday."""
    @wraps(func)
    async def wrapper():
        last = previous_weekday(_today_us())
        if not is_us_trading_day(last):
            logger.info(f"ℹ️ Skipping {func.__name__}: {last} was a US market holiday")
            return
        await func()
    return wrapper
