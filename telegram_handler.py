import requests
from telegram.ext import ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, TIMEZONE
from portfolio import calculate_portfolio_metrics, fmt_money, format_holdings_table, get_currency_breakdown
from portfolio_db import get_setting, get_watchlist
from fetcher import get_currency_for_symbol, get_prices_bulk
from support import compute_support_levels_bulk, format_support_compact
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
    """Compact per-stock support block for the daily report, covering every held
    stock plus everything on the watchlist. Support levels are public market
    prices (not portfolio values), so they're shown even in privacy mode.
    Returns "" if there's nothing to report."""
    held_price = {h["symbol"]: h["current_price"] for h in metrics["holdings"]}
    watched = [w["symbol"] for w in get_watchlist()]
    symbols = sorted(set(held_price) | set(watched))
    if not symbols:
        return ""

    # Watchlist-only symbols have no held price yet — fetch a live quote for
    # those (concurrently) before computing support, so the report reflects
    # today's actual price rather than yesterday's close.
    missing = [s for s in symbols if held_price.get(s) is None]
    resolved_price = dict(held_price)
    if missing:
        quotes = get_prices_bulk(missing)
        for s, pd in quotes.items():
            resolved_price[s] = pd["price"] if pd and pd.get("price") else None

    results = compute_support_levels_bulk((s, resolved_price.get(s)) for s in symbols)

    lines = ["", "📉 *Support Levels*"]
    for symbol in symbols:
        data = results.get(symbol)
        if not data:
            lines.append(f"*{symbol}*  — support data unavailable")
        else:
            currency = get_currency_for_symbol(symbol)
            lines.append(format_support_compact(data, currency, fmt_money))
    lines.append("_ST=short · MT=mid · (%) = drop from price to that support_")
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

        support_section = _build_support_section(metrics)
        if support_section:
            report += "\n" + support_section

        await send_telegram_message(report)
        logger.info("✅ Daily report sent")

    except Exception as e:
        logger.error(f"❌ Error generating report: {e}")
        await send_telegram_message(f"❌ Error generating report: {e}")
