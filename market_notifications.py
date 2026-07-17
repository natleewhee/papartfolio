from portfolio import calculate_portfolio_metrics, format_holdings_table, fmt_money
from portfolio_db import get_setting
from telegram_handler import send_telegram_message
from config import MARKETS
import logging

logger = logging.getLogger(__name__)

async def send_market_notification(market_key, event):
    """
    Ping when a market opens/closes, scoped to whatever holdings you actually
    have in that market's currency. Silently does nothing if you hold nothing
    there — this is what lets the same 4 scheduled jobs "scale" automatically
    as you add/remove holdings, with no rescheduling needed.
    """
    try:
        market = MARKETS[market_key]
        currency = market["currency"]

        metrics = calculate_portfolio_metrics(save_snapshot=False)
        holdings = [h for h in metrics["holdings"] if h["currency"] == currency]
        if not holdings:
            return

        privacy = get_setting("privacy_mode", "0") == "1"
        compact = get_setting("report_style", "full") == "compact"
        verb = "opened" if event == "open" else "closed"
        emoji = "🔔" if event == "open" else "🔕"
        # At open this is really the gap vs. the prior close (today's session has
        # barely started); at close it's the actual day's move. Label accordingly
        # so it doesn't read as "today's move so far" at open.
        change_label = "Gap from prior close" if event == "open" else "Change"

        subtotal_value = sum(h["current_value"] for h in holdings)
        subtotal_change = sum(h["daily_change_$"] for h in holdings)
        prior_value = subtotal_value - subtotal_change
        subtotal_change_pct = (subtotal_change / prior_value * 100) if prior_value > 0 else 0
        change_emoji = "🟢" if subtotal_change >= 0 else "🔴"

        msg = (
            f"{emoji} *{market['label']} has {verb}*\n\n"
            f"Value: {fmt_money(subtotal_value, currency, privacy)}\n"
            f"{change_label}: {change_emoji} {fmt_money(subtotal_change, currency, privacy, show_sign=True)} "
            f"({subtotal_change_pct:+.2f}%)\n"
        )
        if not compact:
            msg += "\n" + f"```\n{format_holdings_table(holdings, privacy)}\n```"

        await send_telegram_message(msg)
        logger.info(f"✅ {market_key} market {event} notification sent ({len(holdings)} holdings)")

    except Exception as e:
        logger.error(f"❌ Error in {market_key} market {event} notification: {e}")
        await send_telegram_message(f"❌ Error sending {market_key} market {event} notification: {e}")

async def notify_us_open():
    await send_market_notification("US", "open")

async def notify_us_close():
    await send_market_notification("US", "close")

async def notify_sg_open():
    await send_market_notification("SG", "open")

async def notify_sg_close():
    await send_market_notification("SG", "close")
