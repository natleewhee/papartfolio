from portfolio_db import get_active_alerts, deactivate_alert, get_watchlist_entry
from fetcher import get_price, get_currency_for_symbol
from portfolio import fmt_money
from support import resolve_support_levels
from telegram_handler import send_telegram_message
import asyncio
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
            if alert["direction"] == "near_support":
                await _check_support_alert(alert)
            else:
                await _check_threshold_alert(alert)
        except Exception as e:
            logger.error(f"❌ Error checking alert #{alert.get('id')} ({alert.get('symbol')}): {e}")

async def _check_threshold_alert(alert):
    """direction is 'above' or 'below': threshold is a fixed price."""
    price_data = await asyncio.to_thread(get_price, alert["symbol"])
    if not price_data or not price_data["price"]:
        return

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

async def _check_support_alert(alert):
    """direction is 'near_support': threshold is a percent proximity — trigger
    when price comes within that percent of either the short- or mid-term
    support level (whichever is closer). A watchlist symbol with manual
    ST/MT levels (set via /watch SYMBOL ST MT) checks against those instead
    of the auto-computed ones."""
    symbol = alert["symbol"]
    price_data = await asyncio.to_thread(get_price, symbol)
    current_price = price_data["price"] if price_data and price_data.get("price") else None

    entry = get_watchlist_entry(symbol)
    manual_st = entry.get("manual_st_support") if entry else None
    manual_mt = entry.get("manual_mt_support") if entry else None

    data = await asyncio.to_thread(resolve_support_levels, symbol, current_price, manual_st, manual_mt)
    if not data:
        return

    hits = [
        (label, sl) for label, sl in (("short-term", data["short_term"]), ("mid-term", data["mid_term"]))
        if sl and sl["distance_pct"] <= alert["threshold"]
    ]
    if not hits:
        return

    label, sl = min(hits, key=lambda x: x[1]["distance_pct"])
    deactivate_alert(alert["id"])
    currency = get_currency_for_symbol(alert["symbol"])
    await send_telegram_message(
        f"📉 *Alert triggered*: {alert['symbol']} is {sl['distance_pct']:.1f}% from its {label} support "
        f"{fmt_money(sl['level'], currency)} (now {fmt_money(data['current_price'], currency)})"
    )
    logger.info(
        f"✅ Alert #{alert['id']} triggered: {alert['symbol']} near {label} support "
        f"({sl['distance_pct']:.1f}% <= {alert['threshold']}%)"
    )
