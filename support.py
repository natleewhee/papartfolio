"""Short- and mid-term support & resistance level detection.

Support levels are price floors *below* the current price where a stock has
historically found buying interest; resistance levels are the mirror image —
price ceilings *above* it. Both are derived from ~1yr of daily history
(yfinance — the only source that gives us candles for both US and SGX tickers,
since Finnhub's free tier is quote-only) using two classic ingredients:

  • swing points — a bar whose low/high is the most extreme in a small window
    either side (a floor/ceiling the market has actually respected before),
    and
  • moving averages — which frequently act as dynamic support/resistance.

Two horizons are reported:
  • short-term — recent structure (~2 months of swing points + 20-day MA)
  • mid-term   — the next meaningful level *beyond* the short-term one, drawn
                 from a longer window (~6 months of swing points + 50/200-day
                 MA)

For each we return the level, what it's based on, and how far today's price
sits from it (distance_pct — the move it would take to reach that level).
History is cached per symbol (see HISTORY_CACHE_TTL_SECONDS) since it barely
changes intraday and is otherwise refetched on every report/command.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
import yfinance
from fetcher import _retry

logger = logging.getLogger(__name__)

# Trading-day windows (markets run ~21 trading days/month)
SHORT_WINDOW = 42     # ~2 months
MID_WINDOW = 130      # ~6 months
SHORT_PIVOT_SPAN = 3  # bars either side for a short-term swing point
MID_PIVOT_SPAN = 5    # wider span -> only more significant mid-term points qualify

_history_cache = {}  # symbol -> (fetched_at, df)
HISTORY_CACHE_TTL_SECONDS = 6 * 60 * 60  # daily bars barely move intraday


def _fetch_history(symbol, period="1y"):
    """Fetch daily OHLC history via yfinance (retried, cached), or None if unavailable."""
    symbol = symbol.upper()
    cached = _history_cache.get(symbol)
    if cached and (time.time() - cached[0]) < HISTORY_CACHE_TTL_SECONDS:
        return cached[1]

    def _f():
        df = yfinance.Ticker(symbol).history(period=period, auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    df = _retry(_f, f"history fetch for {symbol}")
    if df is not None:
        _history_cache[symbol] = (time.time(), df)
        return df
    return cached[1] if cached else None


def _pivots(values, span, find_min=True):
    """Local minima (find_min=True) or maxima (find_min=False) — bars whose
    value is the most extreme within `span` bars on each side. These are the
    floors/ceilings the market has actually bounced from."""
    pivots = []
    n = len(values)
    for i in range(span, n - span):
        window = values[i - span:i + span + 1]
        is_pivot = values[i] <= min(window) if find_min else values[i] >= max(window)
        if is_pivot:
            pivots.append(values[i])
    return pivots


def _sma(values, period):
    """Simple moving average of the last `period` values, or None if too few."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _horizon_level(current_price, bound, extremes, closes, window, pivot_span, ma_periods, mode):
    """Nearest support (mode='support', a floor below `bound`) or resistance
    (mode='resistance', a ceiling above `bound`) for this horizon, drawn from
    this horizon's swing points, its period extreme, and the given moving
    averages.

    `bound` lets callers stack horizons — the mid-term search is bounded by
    the short-term level so it returns the *next* level rather than the same
    one. `distance_pct` is always measured against the real current price.
    """
    find_min = (mode == "support")
    window_vals = extremes[-window:] if len(extremes) > window else list(extremes)

    candidates = []  # (price, basis label)
    point_label = "swing low" if find_min else "swing high"
    for p in _pivots(window_vals, pivot_span, find_min=find_min):
        candidates.append((p, point_label))
    if window_vals:
        extreme = min(window_vals) if find_min else max(window_vals)
        candidates.append((extreme, f"{len(window_vals)}d {'low' if find_min else 'high'}"))
    for mp in ma_periods:
        v = _sma(closes, mp)
        if v is not None:
            candidates.append((v, f"{mp}d MA"))

    if find_min:
        beyond = [(pr, lbl) for pr, lbl in candidates if 0 < pr < bound]
        if not beyond:
            return None
        level, basis = max(beyond, key=lambda x: x[0])  # nearest floor = highest below bound
        distance_pct = (current_price - level) / current_price * 100
    else:
        beyond = [(pr, lbl) for pr, lbl in candidates if pr > bound]
        if not beyond:
            return None
        level, basis = min(beyond, key=lambda x: x[0])  # nearest ceiling = lowest above bound
        distance_pct = (level - current_price) / current_price * 100

    return {"level": round(level, 2), "basis": basis, "distance_pct": round(distance_pct, 2)}


def _compute_levels(symbol, current_price, mode):
    """Shared engine behind compute_support_levels / compute_resistance_levels."""
    df = _fetch_history(symbol)
    if df is None or len(df) < 20:
        logger.warning(f"⚠️ Not enough history to compute {mode} for {symbol}")
        return None

    closes = df["Close"].tolist()
    extremes = df["Low"].tolist() if mode == "support" else df["High"].tolist()

    if current_price is None:
        current_price = closes[-1]
    if not current_price or current_price <= 0:
        return None

    short = _horizon_level(
        current_price, current_price, extremes, closes,
        SHORT_WINDOW, SHORT_PIVOT_SPAN, [20], mode,
    )

    # Mid-term = the next level *beyond* the short-term one. Fall back to the
    # full range beyond current price if nothing further out is found.
    mid_bound = short["level"] if short else current_price
    mid = _horizon_level(
        current_price, mid_bound, extremes, closes,
        MID_WINDOW, MID_PIVOT_SPAN, [50, 200], mode,
    )
    if mid is None and short is not None:
        mid = _horizon_level(
            current_price, current_price, extremes, closes,
            MID_WINDOW, MID_PIVOT_SPAN, [50, 200], mode,
        )

    return {
        "symbol": symbol.upper(),
        "current_price": round(current_price, 2),
        "short_term": short,
        "mid_term": mid,
    }


def compute_support_levels(symbol, current_price=None):
    """Compute short- and mid-term support levels for `symbol`.

    Pass `current_price` to avoid a redundant quote fetch (e.g. the daily report
    already has it); otherwise the latest close is used.

    Returns None if history is unavailable, else:
    {
        "symbol": "AAPL",
        "current_price": 195.0,
        "short_term": {"level": 182.0, "basis": "swing low", "distance_pct": 6.7} | None,
        "mid_term":   {"level": 168.0, "basis": "50d MA",    "distance_pct": 13.8} | None,
    }
    A horizon is None when no historical floor sits below the price (e.g. the
    stock is at/near its own low, so there's nothing beneath it to lean on).
    """
    return _compute_levels(symbol, current_price, "support")


def compute_resistance_levels(symbol, current_price=None):
    """Mirror of compute_support_levels(): nearest short- and mid-term
    resistance *above* the current price, from swing highs + moving averages.
    Same return shape, except distance_pct is the rise needed to reach it.
    A horizon is None when the stock is at/near its own high."""
    return _compute_levels(symbol, current_price, "resistance")


def compute_support_levels_bulk(items):
    """Compute support levels for many symbols concurrently — each call is a
    blocking yfinance fetch (cached, but still a network round-trip on a
    miss), so doing this in threads instead of a loop matters once you're
    tracking more than a couple of symbols.

    items: iterable of (symbol, current_price_or_None) pairs, matching
    compute_support_levels()'s own signature.
    Returns {symbol: result_or_None} — a per-symbol failure logs and resolves
    to None rather than failing the whole batch.
    """
    items = list(items)
    if not items:
        return {}

    def _one(item):
        symbol, price = item
        try:
            return symbol, compute_support_levels(symbol, price)
        except Exception as e:
            logger.error(f"❌ Error computing support for {symbol}: {e}")
            return symbol, None

    with ThreadPoolExecutor(max_workers=min(len(items), 8)) as pool:
        return dict(pool.map(_one, items))


def _manual_level(current_price, manual_price):
    """A user-supplied support price shaped like an auto-computed one, so
    downstream formatting/alerts don't need to know the difference."""
    distance_pct = (current_price - manual_price) / current_price * 100
    return {"level": round(manual_price, 2), "basis": "manual", "distance_pct": round(distance_pct, 2)}


def resolve_support_levels(symbol, current_price=None, manual_st=None, manual_mt=None):
    """Like compute_support_levels(), but a manual ST/MT price (set via
    /watch SYMBOL ST MT) always wins over the auto-computed swing/MA level
    for that horizon — the whole point of setting one manually is that it
    shouldn't get silently recalculated out from under you. A horizon left
    unset (None) still falls back to the auto-computed value. Skips the
    history fetch entirely when both horizons are manual.

    Returns None if there's nothing to show at all: no live price to measure
    distance from, and (for the auto-fallback path) no usable history either.
    """
    if manual_st is not None and manual_mt is not None:
        if not current_price or current_price <= 0:
            return None
        return {
            "symbol": symbol.upper(),
            "current_price": round(current_price, 2),
            "short_term": _manual_level(current_price, manual_st),
            "mid_term": _manual_level(current_price, manual_mt),
        }

    data = compute_support_levels(symbol, current_price)
    if data is None:
        if (manual_st is None and manual_mt is None) or not current_price or current_price <= 0:
            return None
        data = {
            "symbol": symbol.upper(),
            "current_price": round(current_price, 2),
            "short_term": None,
            "mid_term": None,
        }

    if manual_st is not None:
        data["short_term"] = _manual_level(data["current_price"], manual_st)
    if manual_mt is not None:
        data["mid_term"] = _manual_level(data["current_price"], manual_mt)
    return data


def resolve_support_levels_bulk(items):
    """Concurrent resolve_support_levels() for many symbols.

    items: iterable of (symbol, current_price_or_None, manual_st_or_None,
    manual_mt_or_None) tuples.
    Returns {symbol: result_or_None} — a per-symbol failure logs and resolves
    to None rather than failing the whole batch.
    """
    items = list(items)
    if not items:
        return {}

    def _one(item):
        symbol, price, manual_st, manual_mt = item
        try:
            return symbol, resolve_support_levels(symbol, price, manual_st, manual_mt)
        except Exception as e:
            logger.error(f"❌ Error resolving support for {symbol}: {e}")
            return symbol, None

    with ThreadPoolExecutor(max_workers=min(len(items), 8)) as pool:
        return dict(pool.map(_one, items))


def format_support_compact(data, currency, fmt_money):
    """One-line summary for the daily report / lists, e.g.
    `AAPL  $195 · ST $182 (-6.7%) · MT $168 (-13.8%)`
    (the % is the drop from today's price down to that support).

    `fmt_money` is injected to avoid a circular import with portfolio.py.
    """
    def leg(tag, sl):
        if not sl:
            return f"{tag} n/a"
        return f"{tag} {fmt_money(sl['level'], currency)} (-{sl['distance_pct']:.1f}%)"

    price = fmt_money(data["current_price"], currency)
    return f"*{data['symbol']}*  {price} · {leg('ST', data['short_term'])} · {leg('MT', data['mid_term'])}"


def format_support_table(rows, fmt_money):
    """Render a compact watchlist table — same style as format_holdings_table()
    in portfolio.py — meant to be wrapped by the caller in a ``` code block so
    Telegram renders it with a fixed-width font.

    Shows today's move rather than ST%/MT% distance-to-support: the near-
    support flag line already surfaces proximity when it's actually close
    (see near_support_flags()), so repeating that distance in every row was
    redundant with it — the raw ST/MT levels are still shown as reference.

    rows: list of {"symbol", "currency", "current_price", "daily_change_%",
    "short_term", "mid_term"} (current_price/daily_change_%/short_term/
    mid_term are None when unavailable for that symbol; short_term/mid_term
    are each None or a level dict with "level").
    `fmt_money` is injected to avoid coupling this module to portfolio.py.
    """
    header = f"{'SYMBOL':<8}{'PRICE':>10}{'%CHG':>8}{'ST':>10}{'MT':>10}"
    lines = [header, "-" * len(header)]
    for r in rows:
        if r.get("current_price") is None:
            lines.append(f"{r['symbol']:<8}{'n/a':>10}{'n/a':>8}{'n/a':>10}{'n/a':>10}")
            continue
        currency = r["currency"]
        st, mt = r.get("short_term"), r.get("mid_term")
        price_str = fmt_money(r["current_price"], currency)
        chg = r.get("daily_change_%")
        chg_str = f"{chg:+.1f}%" if chg is not None else "n/a"
        st_str = fmt_money(st["level"], currency) if st else "n/a"
        mt_str = fmt_money(mt["level"], currency) if mt else "n/a"
        lines.append(f"{r['symbol']:<8}{price_str:>10}{chg_str:>8}{st_str:>10}{mt_str:>10}")
    return "\n".join(lines)


NEAR_SUPPORT_THRESHOLD_PCT = 5.0  # matches /alertsupport's own default


def near_support_flags(rows, threshold=NEAR_SUPPORT_THRESHOLD_PCT):
    """Which rows have a support horizon within `threshold`% of price right
    now — a passive, no-setup-required version of what /alertsupport
    otherwise needs a manual per-symbol alert for. Returns
    [(symbol, "ST"|"MT", distance_pct), ...] for the closer qualifying
    horizon per symbol, sorted closest-first."""
    flags = []
    for r in rows:
        candidates = [
            (label, sl["distance_pct"])
            for label, sl in (("ST", r.get("short_term")), ("MT", r.get("mid_term")))
            if sl and sl["distance_pct"] <= threshold
        ]
        if candidates:
            label, dist = min(candidates, key=lambda x: x[1])
            flags.append((r["symbol"], label, dist))
    return sorted(flags, key=lambda x: x[2])


def format_near_support_line(flags):
    """One-line summary of near_support_flags()'s output, or "" if none."""
    if not flags:
        return ""
    parts = [f"{symbol} ({label} -{dist:.1f}%)" for symbol, label, dist in flags]
    return "⚠️ Near support: " + ", ".join(parts)


def format_resistance_compact(data, currency, fmt_money):
    """One-line summary, e.g. `AAPL  $195 · ST $205 (+5.1%) · MT $220 (+12.8%)`
    (the % is the rise needed from today's price up to that resistance)."""
    def leg(tag, sl):
        if not sl:
            return f"{tag} n/a"
        return f"{tag} {fmt_money(sl['level'], currency)} (+{sl['distance_pct']:.1f}%)"

    price = fmt_money(data["current_price"], currency)
    return f"*{data['symbol']}*  {price} · {leg('ST', data['short_term'])} · {leg('MT', data['mid_term'])}"
