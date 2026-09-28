"""Benchmark (S&P 500 via SPY) returns to set portfolio performance against.
Every function returns None when data is unavailable, so callers just omit
the comparison line instead of failing."""
import logging
from datetime import date

from config import BENCHMARK_SYMBOL, BENCHMARK_LABEL
from fetcher import get_price
from support import _fetch_history

logger = logging.getLogger(__name__)


def daily_change_pct():
    try:
        quote = get_price(BENCHMARK_SYMBOL)
        return quote["change_pct"] if quote and quote.get("change_pct") is not None else None
    except Exception as e:
        logger.warning(f"⚠️ Benchmark quote failed: {e}")
        return None


def change_pct_since(start_date):
    """Benchmark % change from the last close *before* `start_date` (YYYY-MM-DD)
    to its live price. A portfolio snapshot dated D is taken before that day's
    US session closes, so it reflects the prior US close — this matches it."""
    try:
        history = _fetch_history(BENCHMARK_SYMBOL)
        quote = get_price(BENCHMARK_SYMBOL)
        if history is None or not quote or not quote.get("price"):
            return None
        start = date.fromisoformat(start_date)
        before = history[[d.date() < start for d in history.index]]
        if before.empty:
            return None
        base = float(before["Close"].iloc[-1])
        return (quote["price"] - base) / base * 100 if base > 0 else None
    except Exception as e:
        logger.warning(f"⚠️ Benchmark history failed: {e}")
        return None


def comparison_line(portfolio_pct, benchmark_pct):
    """e.g. 'vs S&P 500: +0.80% (you +0.40 pts)'; "" when the benchmark is unavailable."""
    if benchmark_pct is None:
        return ""
    diff = portfolio_pct - benchmark_pct
    return f"vs {BENCHMARK_LABEL}: {benchmark_pct:+.2f}% (you {diff:+.2f} pts)"
