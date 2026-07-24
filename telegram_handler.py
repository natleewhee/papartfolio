import requests
from telegram.ext import ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, TIMEZONE
from portfolio import calculate_portfolio_metrics, fmt_money, format_holdings_table, get_currency_breakdown
from portfolio_db import get_setting, get_watchlist
from fetcher import get_currency_for_symbol, get_prices_bulk, fetch_extended_hours_bulk
from support import compute_support_levels_bulk, format_support_table, near_support_flags, format_near_support_line
from earnings import fetch_earnings_bulk, earnings_flags, format_earnings_line
from datetime import datetime
import pytz
import logging

logger = logging.getLogger(__name__)

async def send_telegram_message(text: str, parse_mode: str = "Markdown"):
    """Send message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_USER_ID,
        "text": text,
        "parse_mode": parse_mode,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("✅ Message sent to Telegram")
            return True
        else:
            logger.error(f"❌ Telegram API error: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Error sending message: {e}")
        return False

def _build_support_section(metrics):
    """Compact support table for the daily report, covering only stocks
    explicitly on the watchlist — holding a stock no longer implies tracking
    its support levels; that's now a deliberate /watch action. Support levels
    are public market prices (not portfolio values), so they're shown even in
    privacy mode. Returns "" if the watchlist is empty."""
    watched = [w["symbol"] for w in get_watchlist()]
    if not watched:
        return ""

    # A watchlist symbol you also happen to hold already has a price from
    # metrics — only fetch a live quote (concurrently) for the rest, so the
    # report reflects today's actual price rather than yesterday's close.
    held_price = {h["symbol"]: h["current_price"] for h in metrics["holdings"]}
    resolved_price = {s: held_price[s] for s in watched if s in held_price}
    missing = [s for s in watched if s not in resolved_price]
    if missing:
        quotes = get_prices_bulk(missing)
        for s, pd in quotes.items():
            resolved_price[s] = pd["price"] if pd and pd.get("price") else None

    results = compute_support_levels_bulk((s, resolved_price.get(s)) for s in watched)

    rows = []
    for symbol in watched:
        data = results.get(symbol)
        rows.append({
            "symbol": symbol,
            "currency": get_currency_for_symbol(symbol),
            "current_price": data["current_price"] if data else None,
            "short_term": data["short_term"] if data else None,
            "mid_term": data["mid_term"] if data else None,
        })

    table = format_support_table(rows, fmt_money)
    section = (
        "\n📉 *Watchlist — Support Levels*\n"
        f"```\n{table}\n```\n"
        "_ST=short · MT=mid support (below price) · % = drop to reach it_"
    )
    flag_line = format_near_support_line(near_support_flags(rows))
    if flag_line:
        section += "\n" + flag_line
    return section

def _build_extended_hours_section(metrics, privacy=False):
    """Pre/post-market movement for held US tickers (SGX has no extended-hours
    sessions). Most relevant right at the report's 20:30 SGT send time, which
    sits inside the US pre-market window. Silently omits a holding when
    extended-hours data isn't available (outside those windows, or the
    slower yfinance lookup failed/timed out). Returns "" if nothing to show."""
    us_symbols = [h["symbol"] for h in metrics["holdings"] if h["currency"] == "USD"]
    if not us_symbols:
        return ""

    results = fetch_extended_hours_bulk(us_symbols)
    rows = [(sym, r) for sym, r in sorted(results.items()) if r]
    if not rows:
        return ""

    lines = ["", "🌅 *Pre/Post-Market (USD)*"]
    for sym, r in rows:
        label = "Pre" if r["market_state"] == "PRE" else "Post"
        price_str = fmt_money(r["price"], "USD", privacy)
        pct = r["change_pct"]
        if not privacy and pct is not None:
            emoji = "🟢" if pct >= 0 else "🔴"
            lines.append(f"{sym}: {label} {price_str} {emoji} {pct:+.2f}%")
        else:
            lines.append(f"{sym}: {label} {price_str}")
    return "\n".join(lines)

def _build_earnings_section(metrics):
    """Earnings-release radar for held + watchlist stocks: flags anything
    reporting within the next 2 weeks, or that reported within the last few
    days. Most stocks have nothing to say on a given day, so — unlike the
    support table — this is a flagged list, silent for everything else.
    Returns "" if nothing qualifies."""
    held = [h["symbol"] for h in metrics["holdings"]]
    watched = [w["symbol"] for w in get_watchlist()]
    symbols = sorted(set(held) | set(watched))
    if not symbols:
        return ""

    results = fetch_earnings_bulk(symbols)
    upcoming, recent = earnings_flags(results)
    if not upcoming and not recent:
        return ""

    lines = ["", "📅 *Earnings Watch*"]
    for symbol, next_event in upcoming:
        currency = get_currency_for_symbol(symbol)
        lines.append(format_earnings_line(symbol, currency, fmt_money, next_event=next_event))
    for symbol, last_event in recent:
        currency = get_currency_for_symbol(symbol)
        lines.append(format_earnings_line(symbol, currency, fmt_money, last_event=last_event))
    return "\n".join(lines)

async def send_daily_report(context: ContextTypes.DEFAULT_TYPE = None):
    """Generate and send daily portfolio report."""
    try:
        privacy = get_setting("privacy_mode", "0") == "1"
        compact = get_setting("report_style", "full") == "compact"

        # Calculate metrics
        metrics = calculate_portfolio_metrics()

        if not metrics["holdings"]:
            msg = "📭 Portfolio is empty. Use /add to add holdings."
            await send_telegram_message(msg)
            return

        home_currency = metrics["home_currency"]
        emoji = "🟢" if metrics["daily_change_%"] >= 0 else "🔴"

        # Summary
        today = datetime.now(pytz.timezone(TIMEZONE))
        report = f"📊 *{today.strftime('%d %b %Y')}* ({home_currency})\n\n"
        report += (
            f"Total: {fmt_money(metrics['total_value'], home_currency, privacy)}\n"
            f"Today: {emoji} {fmt_money(metrics['daily_change_$'], home_currency, privacy, show_sign=True)} "
            f"({metrics['daily_change_%']:+.2f}%)\n"
            f"Gain: {fmt_money(metrics['unrealized_gain'], home_currency, privacy, show_sign=True)} "
            f"({metrics['unrealized_gain_pct']:+.2f}%)\n"
        )

        best = metrics["best_performer"]
        worst = metrics["worst_performer"]
        if best and worst:
            report += f"🏆 {best['symbol']} {best['daily_change_%']:+.1f}% · 📉 {worst['symbol']} {worst['daily_change_%']:+.1f}%\n"

        for currency, rate in metrics["fx_rates"].items():
            report += f"FX: 1 {currency} = {fmt_money(rate, home_currency, decimals=4)}\n"

        breakdown = get_currency_breakdown(metrics)
        if len(breakdown) > 1:
            mix = " / ".join(f"{ccy} {pct:.0f}%" for ccy, pct in sorted(breakdown.items(), key=lambda x: -x[1]))
            report += f"Currency Mix: {mix}\n"

        if metrics["fx_warnings"]:
            warned = ", ".join(metrics["fx_warnings"])
            report += f"⚠️ No live FX rate for {warned} — assumed 1:1, total may be off\n"

        if metrics["failed_symbols"]:
            report += f"⚠️ Price unavailable: {', '.join(metrics['failed_symbols'])}\n"

        if not compact:
            report += "\n" + f"```\n{format_holdings_table(metrics['holdings'], privacy)}\n```"

        extended_hours_section = _build_extended_hours_section(metrics, privacy)
        if extended_hours_section:
            report += "\n" + extended_hours_section

        earnings_section = _build_earnings_section(metrics)
        if earnings_section:
            report += "\n" + earnings_section

        support_section = _build_support_section(metrics)
        if support_section:
            report += "\n" + support_section

        await send_telegram_message(report)
        logger.info("✅ Daily report sent")

    except Exception as e:
        logger.error(f"❌ Error generating report: {e}")
        await send_telegram_message(f"❌ Error generating report: {e}")
