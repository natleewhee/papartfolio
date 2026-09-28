"""Saturday-morning week in review: the week's performance vs the benchmark,
best/worst holding, and earnings coming up in the next 7 days."""
import asyncio
import logging

import benchmark
from earnings import fetch_earnings_bulk, earnings_flags, format_earnings_line
from fetcher import get_currency_for_symbol
from portfolio import calculate_portfolio_metrics, get_period_performance, fmt_money
from portfolio_db import get_setting, get_snapshot_prices_on, get_watchlist
from telegram_handler import send_telegram_message

logger = logging.getLogger(__name__)

NEXT_WEEK_DAYS = 7


def holding_week_moves(holdings, start_prices):
    """[(symbol, pct)] for holdings present in the start snapshot, best first."""
    moves = [
        (h["symbol"], (h["current_price"] - start_prices[h["symbol"]]) / start_prices[h["symbol"]] * 100)
        for h in holdings
        if start_prices.get(h["symbol"])
    ]
    return sorted(moves, key=lambda m: -m[1])


def build_digest(perf, benchmark_pct, moves, upcoming, privacy):
    currency = perf["currency"]
    emoji = "🟢" if perf["change_pct"] >= 0 else "🔴"
    lines = [
        f"🗓️ *Week in review* (since {perf['start_date']})",
        "",
        f"Week: {emoji} {fmt_money(perf['change'], currency, privacy, show_sign=True)} ({perf['change_pct']:+.2f}%)",
    ]
    bench = benchmark.comparison_line(perf["change_pct"], benchmark_pct)
    if bench:
        lines.append(bench)
    if len(moves) >= 2:
        (best, best_pct), (worst, worst_pct) = moves[0], moves[-1]
        lines.append(f"Best: {best} {best_pct:+.1f}% · Worst: {worst} {worst_pct:+.1f}%")
    elif moves:
        lines.append(f"{moves[0][0]}: {moves[0][1]:+.1f}%")
    if upcoming:
        lines += ["", "*Earnings next week*"]
        lines += [
            "📅 " + format_earnings_line(symbol, get_currency_for_symbol(symbol), fmt_money, next_event=event)
            for symbol, event in upcoming
        ]
    return "\n".join(lines)


def _gather():
    perf = get_period_performance(7)
    if not perf:
        return None
    metrics = calculate_portfolio_metrics(save_snapshot=False)
    moves = holding_week_moves(metrics["holdings"], get_snapshot_prices_on(perf["start_date"]))
    symbols = list(dict.fromkeys([h["symbol"] for h in metrics["holdings"]] + [w["symbol"] for w in get_watchlist()]))
    upcoming, _ = earnings_flags(fetch_earnings_bulk(symbols), upcoming_days=NEXT_WEEK_DAYS, recent_days=-1)
    return perf, benchmark.change_pct_since(perf["start_date"]), moves, upcoming


async def send_weekly_digest():
    try:
        gathered = await asyncio.to_thread(_gather)
        if not gathered:
            logger.info("ℹ️ Weekly digest skipped: not enough snapshot history yet")
            return
        privacy = get_setting("privacy_mode", "0") == "1"
        await send_telegram_message(build_digest(*gathered, privacy))
        logger.info("✅ Weekly digest sent")
    except Exception as e:
        logger.error(f"❌ Error generating weekly digest: {e}")
        await send_telegram_message(f"❌ Error generating weekly digest: {e}")
