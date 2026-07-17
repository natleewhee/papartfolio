from portfolio_db import get_active_alerts, deactivate_alert
from fetcher import get_price, get_currency_for_symbol
from portfolio import fmt_money
from telegram_handler import send_telegram_message
import logging

logger = logging.getLogger(__name__)

async def check_price_alerts():
    """Check all active alerts against current prices; notify and deactivate any that trigger.

    Runs every 15 min, so failures are logged server-side rather than pushed to
    Telegram (which would spam on a persistent fault). Each alert is guarded
    independently so one bad alert doesn't stop the rest of the batch. The
    underlying DB/price/send calls each already degrade gracefully on their own.
    """
    alerts = get_active_alerts()

    for alert in alerts:
        try:
            price_data = get_price(alert["symbol"])
            if not price_data or not price_data["price"]:
                continue

            price = price_data["price"]
            triggered = (
                (alert["direction"] == "above" and price >= alert["threshold"]) or
                (alert["direction"] == "below" and price <= alert["threshold"])
            )

            if triggered:
                deactivate_alert(alert["id"])
                currency = get_currency_for_symbol(alert["symbol"])
                arrow = "📈" if alert["direction"] == "above" else "📉"
                await send_telegram_message(
                    f"{arrow} *Alert triggered*: {alert['symbol']} is {alert['direction']} "
                    f"{fmt_money(alert['threshold'], currency)} (now {fmt_money(price, currency)})"
                )
                logger.info(f"✅ Alert #{alert['id']} triggered: {alert['symbol']} {alert['direction']} {alert['threshold']}")
        except Exception as e:
            logger.error(f"❌ Error checking alert #{alert.get('id')} ({alert.get('symbol')}): {e}")
