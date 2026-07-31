"""Earnings-release tracking: the next scheduled earnings date, and how the
last one landed, for any holding or watchlist symbol.

US tickers use Finnhub's earnings calendar (free tier; already used for
quotes). Finnhub's international coverage requires a paid plan, so SGX (.SI)
tickers fall back to yfinance's earnings-dates lookup — the same per-market
split used for prices/history elsewhere in this bot. Results are cached per
symbol with a short flat TTL: reschedules can be announced at any point
before a report, not just once it's imminent, so freshness can't be gated on
how close the currently-cached date happens to be — a tiered "long normally,
short once imminent" TTL tried that and still went stale on reschedules
announced while the old date was still several days out. Call volume here is
low (a handful of symbols, checked at most a few times a day), so there's no
real cost to just refetching often.
"""
import time
import logging
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor
import requests
import yfinance
from config import FINNHUB_API_KEY
from fetcher import _retry, is_sg_ticker

logger = logging.getLogger(__name__)

_earnings_cache = {}  # symbol -> (fetched_at, events)
EARNINGS_CACHE_TTL_SECONDS = 3 * 60 * 60  # flat TTL — reschedules can land any time, not just near the date

UPCOMING_WINDOW_DAYS = 14  # "in the next 2 weeks"
RECENT_WINDOW_DAYS = 3     # "just released"

_HOUR_LABELS = {
    "bmo": "before market open",
    "amc": "after market close",
    "dmh": "during market hours",
}


def _fetch_finnhub_calendar(symbol):
    """Raw earnings events for a US ticker from Finnhub, spanning ~100 days
    either side of today (enough to catch the last reported quarter and the
    next one at a typical ~90-day cadence)."""
    def _f():
        today = date.today()
        params = {
            "symbol": symbol.upper(),
            "from": (today - timedelta(days=100)).isoformat(),
            "to": (today + timedelta(days=100)).isoformat(),
            "token": FINNHUB_API_KEY,
        }
        response = requests.get("https://finnhub.io/api/v1/calendar/earnings", params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        events = []
        for e in data.get("earningsCalendar", []):
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError, TypeError):
                continue
            events.append({
                "date": d,
                "timing": _HOUR_LABELS.get(e.get("hour")),
                "eps_estimate": e.get("epsEstimate"),
                "eps_actual": e.get("epsActual"),
            })
        return events
    return _retry(_f, f"earnings calendar fetch for {symbol}")


def _fetch_yfinance_earnings(symbol):
    """Raw earnings events for an SGX ticker via yfinance (Finnhub has no
    international coverage on the free tier). Coverage for non-US tickers can
    be thin on Yahoo's side, so an empty/missing result is expected, not an
    error."""
    def _f():
        df = yfinance.Ticker(symbol.upper()).get_earnings_dates(limit=8)
        if df is None or df.empty:
            return []
        events = []
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            events.append({
                "date": d,
                "timing": None,  # yfinance doesn't expose before/after-market timing
                "eps_estimate": row.get("EPS Estimate"),
                "eps_actual": row.get("Reported EPS"),
            })
        return events
    return _retry(_f, f"yfinance earnings fetch for {symbol}")


def _fetch_events(symbol):
    symbol = symbol.upper()
    cached = _earnings_cache.get(symbol)
    if cached:
        fetched_at, cached_events = cached
        if (time.time() - fetched_at) < EARNINGS_CACHE_TTL_SECONDS:
            return cached_events

    events = _fetch_yfinance_earnings(symbol) if is_sg_ticker(symbol) else _fetch_finnhub_calendar(symbol)
    if events is not None:
        _earnings_cache[symbol] = (time.time(), events)
        return events
    return cached[1] if cached else None


def fetch_earnings(symbol):
    """Next scheduled earnings date and the last reported one for `symbol`.

    Returns None if no data could be fetched at all, else:
    {
        "symbol": "AAPL",
        "next": {"date": date(2026,1,30), "days_until": 5,
                  "timing": "after market close", "eps_estimate": 2.10} or None,
        "last": {"date": date(2025,10,30), "days_since": 87,
                  "eps_actual": 2.18, "eps_estimate": 2.10, "surprise_pct": 3.8} or None,
    }
    `next`/`last` are individually None when there's nothing upcoming, or
    nothing reported yet, in the fetched window.
    """
    events = _fetch_events(symbol)
    if events is None:
        return None

    today = date.today()

    def _happened(e):
        """Whether this event can be confidently treated as having already
        occurred. A date before today always has — but a date of *today*
        only counts once actuals have posted, since a same-day event could
        be scheduled before market open (already done) or after market
        close (still hours away) and a bare date comparison can't tell
        those apart. Actuals being populated is the one signal that proves
        it already happened, regardless of timing."""
        return e["date"] < today or (e["date"] == today and e["eps_actual"] is not None)

    reported = sorted((e for e in events if _happened(e)), key=lambda e: e["date"], reverse=True)
    not_yet_confirmed = sorted((e for e in events if not _happened(e)), key=lambda e: e["date"])

    next_event = None
    if not_yet_confirmed:
        e = not_yet_confirmed[0]
        next_event = {
            "date": e["date"],
            "days_until": (e["date"] - today).days,
            "timing": e["timing"],
            "eps_estimate": e["eps_estimate"],
        }

    # The most recently *confirmed-happened* event — regardless of whether
    # its actual EPS has posted yet, since Finnhub often takes hours-to-a-day
    # to backfill epsActual after the report. Treating a pending report as
    # "nothing to show" (the old behavior) meant a stock could vanish from
    # earnings watch entirely right when it's most relevant. Surface it as
    # "reported, results pending" instead — but only once we're sure it
    # actually happened (see _happened() above), not merely because the
    # calendar date arrived.
    last_event = None
    if reported:
        e = reported[0]
        days_since = (today - e["date"]).days
        if e["eps_actual"] is not None:
            surprise_pct = None
            if e["eps_estimate"] not in (None, 0):
                surprise_pct = round((e["eps_actual"] - e["eps_estimate"]) / abs(e["eps_estimate"]) * 100, 1)
            last_event = {
                "date": e["date"],
                "days_since": days_since,
                "eps_actual": e["eps_actual"],
                "eps_estimate": e["eps_estimate"],
                "surprise_pct": surprise_pct,
            }
        elif days_since <= RECENT_WINDOW_DAYS:
            last_event = {
                "date": e["date"],
                "days_since": days_since,
                "eps_actual": None,
                "eps_estimate": e["eps_estimate"],
                "surprise_pct": None,
            }

    return {"symbol": symbol.upper(), "next": next_event, "last": last_event}


def fetch_earnings_bulk(symbols):
    """Concurrent fetch_earnings() for many symbols. Returns
    {symbol: result_or_None}; a per-symbol failure logs and resolves to None
    rather than failing the whole batch."""
    symbols = list(symbols)
    if not symbols:
        return {}

    def _one(symbol):
        try:
            return symbol, fetch_earnings(symbol)
        except Exception as e:
            logger.error(f"❌ Error fetching earnings for {symbol}: {e}")
            return symbol, None

    with ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as pool:
        return dict(pool.map(_one, symbols))


def earnings_flags(results, upcoming_days=UPCOMING_WINDOW_DAYS, recent_days=RECENT_WINDOW_DAYS):
    """Split fetch_earnings_bulk()'s results into what's actually worth
    surfacing: reports due within `upcoming_days`, and reports released
    within the last `recent_days`. Most symbols on a given day have neither,
    so this is a flagged list, not a per-symbol table.

    Returns (upcoming, recent):
      upcoming: [(symbol, next_event), ...] sorted soonest-first
      recent:   [(symbol, last_event), ...] sorted most-recent-first
    """
    upcoming = []
    recent = []
    for symbol, r in results.items():
        if not r:
            continue
        if r["next"] and r["next"]["days_until"] <= upcoming_days:
            upcoming.append((symbol, r["next"]))
        if r["last"] and r["last"]["days_since"] <= recent_days:
            recent.append((symbol, r["last"]))
    upcoming.sort(key=lambda x: x[1]["days_until"])
    recent.sort(key=lambda x: x[1]["days_since"])
    return upcoming, recent


def format_earnings_line(symbol, currency, fmt_money, next_event=None, last_event=None):
    """One line for either an upcoming or a just-released report, e.g.
    'AAPL — in 5d (Thu 30 Jan, after market close)' or
    'DRAM — reported 1d ago: EPS $1.42 vs $1.35 est (+5.2%) 🟢'.
    `fmt_money` is injected to avoid coupling this module to portfolio.py.
    """
    if next_event:
        # Finnhub frequently leaves before/after-market timing unconfirmed
        # until closer to the date, especially for smaller/less-followed
        # tickers — say so explicitly rather than silently dropping it,
        # which reads as broken rather than "not yet known".
        timing = next_event["timing"] or "timing TBD"
        if next_event["days_until"] == 0:
            # Today's date, but not yet confirmed as having happened (see
            # fetch_earnings()'s _happened()) — could be a BMO release
            # already done, or AMC still hours away. Say "reports today"
            # rather than "in 0d", and never "reported" until we're sure.
            return f"{symbol} — reports today ({timing})"
        when = next_event["date"].strftime("%a %d %b")
        return f"{symbol} — in {next_event['days_until']}d ({when}, {timing})"

    if last_event:
        eps_actual = last_event["eps_actual"]
        eps_estimate = last_event["eps_estimate"]
        surprise = last_event["surprise_pct"]
        if eps_actual is None:
            # Date has passed but the data provider hasn't backfilled actual
            # figures yet — say so plainly rather than a bare "EPS n/a".
            return f"{symbol} — reported {last_event['days_since']}d ago: results pending"
        actual_str = fmt_money(eps_actual, currency)
        if surprise is not None and eps_estimate is not None:
            est_str = fmt_money(eps_estimate, currency)
            emoji = "🟢" if surprise >= 0 else "🔴"
            return (
                f"{symbol} — reported {last_event['days_since']}d ago: "
                f"EPS {actual_str} vs {est_str} est ({surprise:+.1f}%) {emoji}"
            )
        return f"{symbol} — reported {last_event['days_since']}d ago: EPS {actual_str}"

    return f"{symbol} — no earnings data"
