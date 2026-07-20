"""Short- and mid-term support-level detection.

Support levels are price floors *below* the current price where a stock has
historically found buying interest. We derive them from ~1yr of daily history
(yfinance — the only source that gives us candles for both US and SGX tickers,
since Finnhub's free tier is quote-only) using two classic ingredients:

  • swing lows — a bar whose low is the lowest in a small window either side
    (a local price floor the market has actually respected before), and
  • moving averages — which frequently act as dynamic support.

Two horizons are reported:
  • short-term — recent structure (~2 months of swing lows + 20-day MA)
  • mid-term   — the next meaningful floor *beneath* the short-term one, drawn
                 from a longer window (~6 months of swing lows + 50/200-day MA)

For each we return the level, what it's based on, and how far today's price sits
above it (distance_pct — the drop it would take to reach that support).
"""
import logging
import yfinance
from fetcher import _retry

logger = logging.getLogger(__name__)

# Trading-day windows (markets run ~21 trading days/month)
SHORT_WINDOW = 42    # ~2 months
MID_WINDOW = 130     # ~6 months
SHORT_PIVOT_SPAN = 3  # bars either side for a short-term swing low
MID_PIVOT_SPAN = 5    # wider span → only more significant mid-term lows qualify


def _fetch_history(symbol, period="1y"):
    """Fetch daily OHLC history via yfinance (retried), or None if unavailable."""
    def _f():
        df = yfinance.Ticker(symbol.upper()).history(period=period, auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    return _retry(_f, f"history fetch for {symbol}")


def _pivot_lows(lows, span):
    """Return the values of local minima — bars whose low is the lowest within
    `span` bars on each side. These are price floors the market has bounced from."""
    pivots = []
    n = len(lows)
    for i in range(span, n - span):
        window = lows[i - span:i + span + 1]
        if lows[i] <= min(window):
            pivots.append(lows[i])
    return pivots


def _sma(values, period):
    """Simple moving average of the last `period` values, or None if too few."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _horizon_support(current_price, ceiling, lows, closes, window, pivot_span, ma_periods):
    """Nearest support floor strictly below `ceiling`, drawn from this horizon's
    swing lows, its period low, and the given moving averages.

    `ceiling` lets the caller stack horizons — the mid-term search is capped at
    the short-term level so it returns the *next* floor down rather than the same
    one. `distance_pct` is always measured against the real current price.
    """
    window_lows = lows[-window:] if len(lows) > window else list(lows)

    candidates = []  # (price, basis label)
    for p in _pivot_lows(window_lows, pivot_span):
        candidates.append((p, "swing low"))
    if window_lows:
        candidates.append((min(window_lows), f"{len(window_lows)}d low"))
    for mp in ma_periods:
        v = _sma(closes, mp)
        if v is not None:
            candidates.append((v, f"{mp}d MA"))

    below = [(pr, lbl) for pr, lbl in candidates if 0 < pr < ceiling]
    if not below:
        return None

    level, basis = max(below, key=lambda x: x[0])  # highest floor below ceiling = nearest one
    distance_pct = (current_price - level) / current_price * 100
    return {"level": round(level, 2), "basis": basis, "distance_pct": round(distance_pct, 2)}


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
    df = _fetch_history(symbol)
    if df is None or len(df) < 20:
        logger.warning(f"⚠️ Not enough history to compute support for {symbol}")
        return None

    closes = df["Close"].tolist()
    lows = df["Low"].tolist()

    if current_price is None:
        current_price = closes[-1]
    if not current_price or current_price <= 0:
        return None

    short = _horizon_support(
        current_price, current_price, lows, closes,
        SHORT_WINDOW, SHORT_PIVOT_SPAN, [20],
    )

    # Mid-term = the next floor *below* the short-term level. Fall back to the
    # full range below current price if nothing deeper is found.
    mid_ceiling = short["level"] if short else current_price
    mid = _horizon_support(
        current_price, mid_ceiling, lows, closes,
        MID_WINDOW, MID_PIVOT_SPAN, [50, 200],
    )
    if mid is None and short is not None:
        mid = _horizon_support(
            current_price, current_price, lows, closes,
            MID_WINDOW, MID_PIVOT_SPAN, [50, 200],
        )

    return {
        "symbol": symbol.upper(),
        "current_price": round(current_price, 2),
        "short_term": short,
        "mid_term": mid,
    }


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
