from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from apscheduler.triggers.cron import CronTrigger
import pytz
from fetcher import validate_symbol, get_price, get_currency_for_symbol
from portfolio_db import (
    add_holding, remove_holding, update_holding,
    get_all_holdings, get_holding, clear_all_holdings,
    get_setting, set_setting,
    create_alert, get_active_alerts, deactivate_alert, find_active_alert, update_alert_threshold,
)
from portfolio import (
    calculate_portfolio_metrics, get_period_performance, get_currency_breakdown,
    fmt_money,
)
from telegram_handler import send_daily_report
from config import TELEGRAM_USER_ID, TIMEZONE, DAILY_REPORT_TIME
import logging

logger = logging.getLogger(__name__)

# Conversation states
CONFIRM_CLEAR = 1
CONFIRM_REMOVE = 2
CONFIRM_UPDATE = 3

async def check_user(update: Update) -> bool:
    """Verify message is from authorized user."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return False
    return True

# ==================== COMMAND HANDLERS ====================

def _parse_add_args(line: str):
    """Parse one /add line into (symbol, shares, avg_cost), tolerating a leading /add token."""
    parts = line.split()
    if parts and parts[0].lower().lstrip("/") == "add":
        parts = parts[1:]
    if len(parts) != 3:
        return None
    symbol = parts[0].upper()
    shares = int(parts[1])
    avg_cost = float(parts[2])
    return symbol, shares, avg_cost

def _apply_add(symbol, shares, avg_cost):
    """Add or merge one holding. Returns a one-line result string."""
    if shares <= 0:
        return f"❌ {symbol}: shares must be > 0"
    if avg_cost <= 0:
        return f"❌ {symbol}: cost must be > 0"
    if not validate_symbol(symbol):
        return f"❌ {symbol}: invalid ticker"

    currency = get_currency_for_symbol(symbol)
    existing = get_holding(symbol)
    if existing:
        new_shares = existing["shares"] + shares
        new_avg_cost = (
            (existing["shares"] * existing["avg_cost"]) + (shares * avg_cost)
        ) / new_shares
        if update_holding(symbol, new_shares, round(new_avg_cost, 4)):
            return f"✅ {symbol}: merged → {new_shares} shares @ {fmt_money(new_avg_cost, currency)}"
        return f"❌ {symbol}: error merging"

    if add_holding(symbol, shares, avg_cost, currency=currency):
        return f"✅ {symbol}: added {shares} shares @ {fmt_money(avg_cost, currency)}"
    return f"❌ {symbol}: error adding"

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /add SYMBOL SHARES AVG_COST (merges into an existing position with a
    weighted-average cost). Also accepts multiple lines pasted in one message
    (e.g. from /export) and processes each as its own add/merge.
    """
    if not await check_user(update):
        return

    lines = [ln.strip() for ln in update.message.text.split("\n") if ln.strip()]

    # Single line: keep the original detailed usage message on a bad format
    if len(lines) == 1:
        try:
            parsed = _parse_add_args(lines[0])
        except ValueError:
            await update.message.reply_text("❌ Shares and cost must be numbers")
            return
        if not parsed:
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /add SYMBOL SHARES AVG_COST\n"
                "Example: /add ECHO 50 12.50"
            )
            return
        symbol, shares, avg_cost = parsed
        await update.message.reply_text(f"🔍 Validating {symbol}...")
        try:
            await update.message.reply_text(_apply_add(symbol, shares, avg_cost))
        except Exception as e:
            logger.error(f"Error in /add: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
        return

    # Multiple lines: bulk add/merge, one result per line
    await update.message.reply_text(f"🔄 Processing {len(lines)} holdings...")
    results = []
    for line in lines:
        try:
            parsed = _parse_add_args(line)
            if not parsed:
                results.append(f"❌ Invalid line: {line}")
                continue
            symbol, shares, avg_cost = parsed
            results.append(_apply_add(symbol, shares, avg_cost))
        except ValueError:
            results.append(f"❌ Invalid line (shares/cost must be numbers): {line}")
        except Exception as e:
            logger.error(f"Error in bulk /add line '{line}': {e}")
            results.append(f"❌ Error on line: {line}")

    await update.message.reply_text("\n".join(results))

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove SYMBOL (asks for confirmation before deleting)"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /remove SYMBOL\n"
            "Example: /remove ECHO"
        )
        return

    symbol = parts[1].upper()
    if not get_holding(symbol):
        await update.message.reply_text(f"❌ {symbol} not found in portfolio")
        return

    context.user_data["pending_remove"] = symbol
    await update.message.reply_text(
        f"⚠️ Remove {symbol} from your portfolio?\n\nReply with 'YES' to confirm or anything else to cancel"
    )
    return CONFIRM_REMOVE

async def remove_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove confirmation"""
    if not await check_user(update):
        return ConversationHandler.END

    symbol = context.user_data.pop("pending_remove", None)
    if symbol and update.message.text.upper() == "YES":
        if remove_holding(symbol):
            await update.message.reply_text(f"✅ Removed {symbol}")
        else:
            await update.message.reply_text(f"❌ {symbol} not found in portfolio")
    else:
        await update.message.reply_text("❌ Cancelled")

    return ConversationHandler.END

async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /update SYMBOL SHARES AVG_COST (asks for confirmation before overwriting)"""
    if not await check_user(update):
        return

    try:
        parts = update.message.text.split()
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /update SYMBOL SHARES AVG_COST\n"
                "Example: /update ECHO 60 13.00"
            )
            return

        symbol = parts[1].upper()
        shares = int(parts[2])
        avg_cost = float(parts[3])

        if shares <= 0:
            await update.message.reply_text("❌ Shares must be > 0")
            return
        if avg_cost <= 0:
            await update.message.reply_text("❌ Cost must be > 0")
            return

        existing = get_holding(symbol)
        if not existing:
            await update.message.reply_text(f"❌ {symbol} not found in portfolio")
            return

        currency = existing.get("currency") or "USD"
        context.user_data["pending_update"] = (symbol, shares, avg_cost)
        await update.message.reply_text(
            f"⚠️ Overwrite {symbol}?\n"
            f"  {existing['shares']} @ {fmt_money(existing['avg_cost'], currency)} → "
            f"{shares} @ {fmt_money(avg_cost, currency)}\n\n"
            f"Reply with 'YES' to confirm or anything else to cancel"
        )
        return CONFIRM_UPDATE

    except ValueError:
        await update.message.reply_text("❌ Shares and cost must be numbers")
    except Exception as e:
        logger.error(f"Error in /update: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def update_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /update confirmation"""
    if not await check_user(update):
        return ConversationHandler.END

    pending = context.user_data.pop("pending_update", None)
    if pending and update.message.text.upper() == "YES":
        symbol, shares, avg_cost = pending
        if update_holding(symbol, shares, avg_cost):
            await update.message.reply_text(
                f"✅ Updated {symbol}\nShares: {shares}\nAvg Cost: {avg_cost}"
            )
        else:
            await update.message.reply_text(f"❌ Error updating {symbol}")
    else:
        await update.message.reply_text("❌ Cancelled")

    return ConversationHandler.END

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list and /portfolio"""
    if not await check_user(update):
        return

    try:
        holdings = get_all_holdings()

        if not holdings:
            await update.message.reply_text("📭 Portfolio is empty")
            return

        privacy = get_setting("privacy_mode", "0") == "1"

        # Calculate metrics (read-only check — don't overwrite today's official snapshot)
        metrics = calculate_portfolio_metrics(save_snapshot=False)

        if not metrics["holdings"]:
            await update.message.reply_text("⚠️ Could not fetch prices for any holdings right now. Try again shortly.")
            return

        # Format message
        msg = "📊 *Current Portfolio*\n\n"

        for holding in metrics["holdings"]:
            currency = holding["currency"]
            emoji = "🟢" if holding["daily_change_%"] >= 0 else "🔴"
            msg += (
                f"*{holding['symbol']}* ({holding['pct_of_portfolio']:.1f}% of portfolio)\n"
                f"  Shares: {holding['shares']} @ {fmt_money(holding['avg_cost'], currency, privacy)}\n"
                f"  Current: {fmt_money(holding['current_price'], currency, privacy)} {emoji} {holding['daily_change_%']:+.2f}%\n"
                f"  Value: {fmt_money(holding['current_value'], currency, privacy)}\n"
                f"  Gain: {fmt_money(holding['unrealized_gain'], currency, privacy, show_sign=True)} ({holding['unrealized_gain_pct']:+.2f}%)\n\n"
            )

        if metrics["failed_symbols"]:
            msg += f"⚠️ Price unavailable: {', '.join(metrics['failed_symbols'])}\n\n"

        if metrics["fx_warnings"]:
            warned = ", ".join(metrics["fx_warnings"])
            msg += f"⚠️ No live FX rate for {warned} — assumed 1:1, total may be off\n\n"

        home_currency = metrics["home_currency"]
        emoji = "🟢" if metrics["daily_change_%"] >= 0 else "🔴"
        msg += (
            f"*Portfolio Total ({home_currency})*\n"
            f"Value: {fmt_money(metrics['total_value'], home_currency, privacy)}\n"
            f"Cost Basis: {fmt_money(metrics['total_cost_basis'], home_currency, privacy)}\n"
            f"Gain: {fmt_money(metrics['unrealized_gain'], home_currency, privacy, show_sign=True)} ({metrics['unrealized_gain_pct']:+.2f}%)\n"
            f"Today: {emoji} {fmt_money(metrics['daily_change_$'], home_currency, privacy, show_sign=True)} ({metrics['daily_change_%']:+.2f}%)"
        )

        breakdown = get_currency_breakdown(metrics)
        if len(breakdown) > 1:
            mix = " / ".join(f"{ccy} {pct:.0f}%" for ccy, pct in sorted(breakdown.items(), key=lambda x: -x[1]))
            msg += f"\nCurrency Mix: {mix}"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error in /list: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear (with confirmation)"""
    if not await check_user(update):
        return

    msg = "⚠️ This will delete ALL holdings!\n\nReply with 'YES' to confirm or anything else to cancel"
    await update.message.reply_text(msg)
    return CONFIRM_CLEAR

async def clear_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle clear confirmation"""
    if not await check_user(update):
        return ConversationHandler.END

    if update.message.text.upper() == "YES":
        clear_all_holdings()
        await update.message.reply_text("✅ Portfolio cleared")
    else:
        await update.message.reply_text("❌ Cancelled")

    return ConversationHandler.END

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sync (manually trigger daily report)"""
    if not await check_user(update):
        return

    try:
        await update.message.reply_text("🔄 Generating daily report...")
        await send_daily_report(context)
        await update.message.reply_text("✅ Report sent")
    except Exception as e:
        logger.error(f"Error in /sync: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /price SYMBOL — quick lookup without adding to the portfolio"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /price SYMBOL\n"
            "Example: /price AAPL"
        )
        return

    symbol = parts[1].upper()
    price_data = get_price(symbol)
    if not price_data or not price_data["price"]:
        await update.message.reply_text(f"❌ Could not fetch price for {symbol}")
        return

    currency = get_currency_for_symbol(symbol)
    change_pct = price_data["change_pct"] or 0
    change = price_data["change"] or 0
    emoji = "🟢" if change_pct >= 0 else "🔴"
    await update.message.reply_text(
        f"*{symbol}*\n"
        f"Price: {fmt_money(price_data['price'], currency)}\n"
        f"Change: {emoji} {change_pct:+.2f}% ({fmt_money(change, currency, show_sign=True)})",
        parse_mode="Markdown"
    )

async def _cmd_period_performance(update: Update, days: int, label: str):
    perf = get_period_performance(days)
    if not perf:
        await update.message.reply_text(
            f"📭 Not enough history yet for a {label} comparison — check back after a few more daily reports."
        )
        return

    privacy = get_setting("privacy_mode", "0") == "1"
    currency = perf["currency"]
    emoji = "🟢" if perf["change_pct"] >= 0 else "🔴"
    msg = (
        f"*{label.capitalize()} Performance ({currency})*\n\n"
        f"Since {perf['start_date']}: {fmt_money(perf['start_value'], currency, privacy)}\n"
        f"Now: {fmt_money(perf['current_value'], currency, privacy)}\n"
        f"Change: {emoji} {fmt_money(perf['change'], currency, privacy, show_sign=True)} ({perf['change_pct']:+.2f}%)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /week — portfolio value change over the trailing 7 days"""
    if not await check_user(update):
        return
    await _cmd_period_performance(update, 7, "week")

async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /month — portfolio value change over the trailing 30 days"""
    if not await check_user(update):
        return
    await _cmd_period_performance(update, 30, "month")

async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /export — dump holdings as /add commands you can paste back in to restore them"""
    if not await check_user(update):
        return

    holdings = get_all_holdings()
    if not holdings:
        await update.message.reply_text("📭 Portfolio is empty")
        return

    lines = [f"/add {h['symbol']} {h['shares']} {h['avg_cost']}" for h in holdings]

    await update.message.reply_text(
        "📤 Backup below. Copy the next message and paste it back in any time "
        "(even all at once) to restore these holdings."
    )
    # Sent with no formatting so a straight copy-paste back in works unmodified
    await update.message.reply_text("\n".join(lines))

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings — combined view of everything configurable via /privacy, /reportstyle, /settime"""
    if not await check_user(update):
        return

    privacy = get_setting("privacy_mode", "0") == "1"
    report_style = get_setting("report_style", "full")
    report_time = get_setting("daily_report_time", DAILY_REPORT_TIME)

    msg = (
        "⚙️ *Current Settings*\n\n"
        f"Privacy mode: {'ON' if privacy else 'OFF'} (/privacy)\n"
        f"Report style: {report_style} (/reportstyle)\n"
        f"Report time: {report_time} {TIMEZONE} (/settime)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /privacy, /privacy on, /privacy off"""
    if not await check_user(update):
        return

    parts = update.message.text.split()

    if len(parts) == 1:
        current = get_setting("privacy_mode", "0") == "1"
        await update.message.reply_text(
            f"🔒 Privacy mode is currently {'ON' if current else 'OFF'}\n\n"
            f"Use /privacy on or /privacy off to change it."
        )
        return

    arg = parts[1].lower()
    if arg not in ("on", "off"):
        await update.message.reply_text("❌ Usage: /privacy on|off")
        return

    set_setting("privacy_mode", "1" if arg == "on" else "0")
    await update.message.reply_text(f"✅ Privacy mode {'enabled' if arg == 'on' else 'disabled'}")

async def cmd_reportstyle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reportstyle, /reportstyle compact, /reportstyle full"""
    if not await check_user(update):
        return

    parts = update.message.text.split()

    if len(parts) == 1:
        current = get_setting("report_style", "full")
        await update.message.reply_text(
            f"📄 Report style is currently *{current}*\n\n"
            f"/reportstyle full — summary + full holdings table\n"
            f"/reportstyle compact — summary only, no per-holding table",
            parse_mode="Markdown"
        )
        return

    arg = parts[1].lower()
    if arg not in ("compact", "full"):
        await update.message.reply_text("❌ Usage: /reportstyle compact|full")
        return

    set_setting("report_style", arg)
    await update.message.reply_text(f"✅ Report style set to {arg}")

async def cmd_settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settime HH:MM — change the daily report time (24h, Asia/Singapore), effective immediately"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2 or ":" not in parts[1]:
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /settime HH:MM (24-hour, Asia/Singapore)\n"
            "Example: /settime 21:00"
        )
        return

    try:
        hour_str, minute_str = parts[1].split(":")
        hour, minute = int(hour_str), int(minute_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid time — use HH:MM in 24-hour format, e.g. 21:00")
        return

    time_str = f"{hour:02d}:{minute:02d}"
    set_setting("daily_report_time", time_str)

    scheduler = context.application.bot_data.get("scheduler")
    if scheduler:
        scheduler.reschedule_job(
            "daily_report",
            trigger=CronTrigger(hour=hour, minute=minute, day_of_week="mon-fri", timezone=pytz.timezone(TIMEZONE)),
        )
        await update.message.reply_text(f"✅ Daily report time set to {time_str} {TIMEZONE} — effective immediately")
    else:
        await update.message.reply_text(f"✅ Daily report time saved as {time_str} {TIMEZONE} — takes effect after next restart")

async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /alert SYMBOL above|below THRESHOLD"""
    if not await check_user(update):
        return

    try:
        parts = update.message.text.split()
        if len(parts) != 4 or parts[2].lower() not in ("above", "below"):
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /alert SYMBOL above|below THRESHOLD\n"
                "Example: /alert AAPL above 200"
            )
            return

        symbol = parts[1].upper()
        direction = parts[2].lower()
        threshold = float(parts[3])

        if not validate_symbol(symbol):
            await update.message.reply_text(f"❌ Invalid ticker: {symbol}")
            return

        currency = get_currency_for_symbol(symbol)

        # If an active alert already exists for this symbol+direction, edit it
        # in place instead of creating a duplicate (no more unalert+realert)
        existing = find_active_alert(symbol, direction)
        if existing:
            if update_alert_threshold(existing["id"], threshold):
                await update.message.reply_text(
                    f"✅ Alert #{existing['id']} updated: {symbol} {direction} "
                    f"{fmt_money(threshold, currency)} (was {fmt_money(existing['threshold'], currency)})"
                )
            else:
                await update.message.reply_text("❌ Error updating alert")
            return

        alert_id = create_alert(symbol, direction, threshold)
        if alert_id:
            await update.message.reply_text(f"✅ Alert #{alert_id} set: {symbol} {direction} {fmt_money(threshold, currency)}")
        else:
            await update.message.reply_text("❌ Error creating alert")

    except ValueError:
        await update.message.reply_text("❌ Threshold must be a number")
    except Exception as e:
        logger.error(f"Error in /alert: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /alerts — list active alerts with current price and distance to trigger"""
    if not await check_user(update):
        return

    alerts = get_active_alerts()
    if not alerts:
        await update.message.reply_text("📭 No active alerts")
        return

    msg = "🔔 *Active Alerts*\n\n"
    for a in alerts:
        currency = get_currency_for_symbol(a["symbol"])
        line = f"#{a['id']}: {a['symbol']} {a['direction']} {fmt_money(a['threshold'], currency)}"

        price_data = get_price(a["symbol"])
        if price_data and price_data["price"]:
            price = price_data["price"]
            gap_pct = abs(price - a["threshold"]) / price * 100 if price else 0
            line += f" — currently {fmt_money(price, currency)}, {gap_pct:.1f}% away"

        msg += line + "\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_unalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unalert ID — cancel an active alert"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text("❌ Usage: /unalert ID\nSee active alert IDs with /alerts")
        return

    try:
        alert_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ ID must be a number")
        return

    if deactivate_alert(alert_id):
        await update.message.reply_text(f"✅ Cancelled alert #{alert_id}")
    else:
        await update.message.reply_text(f"❌ Alert #{alert_id} not found or already inactive")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help"""
    if not await check_user(update):
        return

    help_text = """
*📊 Stock Portfolio Bot Commands*

*— Portfolio —*
/add SYMBOL SHARES AVG_COST — add, or merge into an existing holding
  e.g. /add ECHO 50 12.50 (SG stocks: /add D05.SI 100 30.00)
/update SYMBOL SHARES AVG_COST — overwrite a holding (asks to confirm)
/remove SYMBOL — delete a holding (asks to confirm)
/clear — delete everything (asks to confirm)
/list or /portfolio — current holdings with P&L
/export — backup as pasteable /add commands

*— Performance —*
/sync — manually trigger the daily report
/week / /month — value change over the trailing 7/30 days
(you'll also get a ping at each market's open/close — US and/or SG,
whichever you actually hold)

*— Alerts —*
/alert SYMBOL above|below THRESHOLD — notify me when a price crosses a level
  (re-running this on the same symbol+direction edits the threshold in place)
/alerts — list active alerts
/unalert ID — cancel an alert

*— Settings —*
/settings — see all current settings at a glance
/privacy [on|off] — mask $ amounts (percentages still shown)
/reportstyle [full|compact] — full holdings table, or summary only
/settime HH:MM — change daily report time (24h, Asia/Singapore)

/help — show this message
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start"""
    if not await check_user(update):
        return

    msg = "👋 Stock portfolio bot ready!\n\nType /help for commands"
    await update.message.reply_text(msg)
