from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from apscheduler.triggers.cron import CronTrigger
import pytz
from fetcher import validate_symbol, get_price, get_currency_for_symbol, fetch_extended_hours, get_prices_bulk
from portfolio_db import (
    add_holding, remove_holding, update_holding,
    get_all_holdings, get_holding, clear_all_holdings,
    get_setting, set_setting,
    create_alert, get_active_alerts, deactivate_alert, find_active_alert, update_alert_threshold,
    add_to_watchlist, remove_from_watchlist, get_watchlist, is_watched,
)
from portfolio import (
    calculate_portfolio_metrics, get_period_performance, get_currency_breakdown,
    fmt_money,
)
from support import (
    compute_support_levels, compute_support_levels_bulk, compute_resistance_levels,
    format_support_table, format_resistance_compact,
    near_support_flags, format_near_support_line,
)
from earnings import fetch_earnings, fetch_earnings_bulk, earnings_flags, format_earnings_line, UPCOMING_WINDOW_DAYS
from ibkr_flex import run_reconciliation, is_configured as ibkr_configured
from telegram_handler import send_daily_report
from config import TELEGRAM_USER_ID, TIMEZONE, DAILY_REPORT_TIME, MARKETS
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Inline Yes/Cancel keyboard for destructive-action confirmations. Using
# callback buttons (rather than a text "reply YES" ConversationHandler) means
# an unrelated command typed mid-confirmation is never swallowed, and two
# pending confirmations can't cross-fire.
_CONFIRM_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
    InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
]])

async def check_user(update: Update) -> bool:
    """Verify message is from authorized user."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return False
    return True

async def _delete_quietly(msg):
    """Delete a transient 'working…' status message once its task is done, so
    the chat is left with just the result. No-op if it's already gone."""
    if not msg:
        return
    try:
        await msg.delete()
    except Exception:
        pass  # already deleted, too old, or races with the user — never surface this

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
        status = await update.message.reply_text(f"🔍 Validating {symbol}...")
        try:
            await update.message.reply_text(_apply_add(symbol, shares, avg_cost))
        except Exception as e:
            logger.error(f"Error in /add: {e}")
            await update.message.reply_text(f"❌ Error: {e}")
        finally:
            await _delete_quietly(status)
        return

    # Multiple lines: bulk add/merge, one result per line
    status = await update.message.reply_text(f"🔄 Processing {len(lines)} holdings...")
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
    await _delete_quietly(status)

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

    context.user_data["pending_action"] = ("remove", symbol)
    await update.message.reply_text(
        f"⚠️ Remove *{symbol}* from your portfolio?",
        parse_mode="Markdown",
        reply_markup=_CONFIRM_KEYBOARD,
    )

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
        context.user_data["pending_action"] = ("update", symbol, shares, avg_cost)
        await update.message.reply_text(
            f"⚠️ Overwrite *{symbol}*?\n"
            f"  {existing['shares']} @ {fmt_money(existing['avg_cost'], currency)} → "
            f"{shares} @ {fmt_money(avg_cost, currency)}",
            parse_mode="Markdown",
            reply_markup=_CONFIRM_KEYBOARD,
        )

    except ValueError:
        await update.message.reply_text("❌ Shares and cost must be numbers")
    except Exception as e:
        logger.error(f"Error in /update: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

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

        for currency, rate in metrics["fx_rates"].items():
            msg += f"\nFX: 1 {currency} = {fmt_money(rate, home_currency, decimals=4)}"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error in /list: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear (with confirmation)"""
    if not await check_user(update):
        return

    context.user_data["pending_action"] = ("clear",)
    await update.message.reply_text(
        "⚠️ This will delete *ALL* holdings!",
        parse_mode="Markdown",
        reply_markup=_CONFIRM_KEYBOARD,
    )

async def on_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the inline Confirm/Cancel button for /remove, /update, /clear."""
    query = update.callback_query
    if query.from_user.id != TELEGRAM_USER_ID:
        await query.answer("❌ Unauthorized", show_alert=True)
        return

    await query.answer()
    pending = context.user_data.pop("pending_action", None)

    # "cancel" tapped, or the prompt expired (e.g. bot restarted — user_data is in-memory)
    if query.data == "cancel" or not pending:
        await query.edit_message_text("❌ Cancelled")
        return

    action = pending[0]
    try:
        if action == "remove":
            symbol = pending[1]
            ok = remove_holding(symbol)
            await query.edit_message_text(f"✅ Removed {symbol}" if ok else f"❌ {symbol} not found in portfolio")
        elif action == "clear":
            clear_all_holdings()
            await query.edit_message_text("✅ Portfolio cleared")
        elif action == "update":
            _, symbol, shares, avg_cost = pending
            ok = update_holding(symbol, shares, avg_cost)
            await query.edit_message_text(
                f"✅ Updated {symbol}: {shares} @ {avg_cost}" if ok else f"❌ Error updating {symbol}"
            )
    except Exception as e:
        logger.error(f"Error in confirmation ({action}): {e}")
        await query.edit_message_text(f"❌ Error: {e}")

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sync (manually trigger daily report). The 'generating' status is a
    throwaway spinner — deleted once the report lands — and there's no 'sent'
    confirmation, so the chat is left with just the report itself."""
    if not await check_user(update):
        return

    status = await update.message.reply_text("🔄 Generating daily report...")
    try:
        await send_daily_report(context)
    except Exception as e:
        logger.error(f"Error in /sync: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        await _delete_quietly(status)

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /price SYMBOL — quick lookup without adding to the portfolio.
    For US tickers, also shows pre/post-market price when the market is
    currently in one of those sessions (SGX has no extended-hours trading)."""
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

    price_line = f"Price: {fmt_money(price_data['price'], currency)}"
    extended = fetch_extended_hours(symbol)
    if extended:
        label = "Pre-mkt" if extended["market_state"] == "PRE" else "Post-mkt"
        ext_pct = extended["change_pct"]
        ext_pct_str = f" ({ext_pct:+.2f}%)" if ext_pct is not None else ""
        price_line += f" → {label} {fmt_money(extended['price'], currency)}{ext_pct_str}"

    await update.message.reply_text(
        f"*{symbol}*\n"
        f"{price_line}\n"
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
        f"Report time: {report_time} {TIMEZONE} (/settime)\n\n"
        "ℹ️ *Notes*\n"
        "• 'Today' change is each stock's own-currency move — it excludes FX "
        "shifts. Use /week or /month for FX-inclusive tracking.\n"
        "• Cost basis is marked to today's FX rate, so it can drift overnight "
        "from currency moves alone (this cancels out in Gain)."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

def _to_sgt(market_tz_name, hour, minute):
    """Convert a market-local HH:MM to today's equivalent SGT time (DST-correct)."""
    market_now = datetime.now(pytz.timezone(market_tz_name))
    dt = market_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.astimezone(pytz.timezone(TIMEZONE)).strftime("%H:%M")

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule — show all time-based notification times (daily report + market open/close)"""
    if not await check_user(update):
        return

    report_time = get_setting("daily_report_time", DAILY_REPORT_TIME)
    held_currencies = {(h.get("currency") or "USD") for h in get_all_holdings()}

    lines = ["📅 *Notification Schedule*", "", f"Daily report: {report_time} {TIMEZONE}", ""]

    for market in MARKETS.values():
        tz = market["timezone"]
        oh, om = market["open"]
        ch, cm = market["close"]
        held = "" if market["currency"] in held_currencies else " — none held, muted"
        lines.append(f"*{market['label']}*{held}")
        if tz == TIMEZONE:
            lines.append(f"  Open {oh:02d}:{om:02d} · Close {ch:02d}:{cm:02d} SGT")
        else:
            lines.append(f"  Open {oh:02d}:{om:02d} → {_to_sgt(tz, oh, om)} SGT")
            lines.append(f"  Close {ch:02d}:{cm:02d} → {_to_sgt(tz, ch, cm)} SGT")
        lines.append("")

    lines.append("_Weekdays only. Market pings fire only for markets you hold._")
    lines.append("_(Price alerts are separate & event-based — see /alerts.)_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

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

        if a["direction"] == "near_support":
            line = f"#{a['id']}: {a['symbol']} near support (within {a['threshold']:.1f}%)"
            data = compute_support_levels(a["symbol"])
            if data:
                dists = [sl["distance_pct"] for sl in (data["short_term"], data["mid_term"]) if sl]
                if dists:
                    line += f" — currently {min(dists):.1f}% away"
        else:
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

async def cmd_alertsupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /alertsupport SYMBOL [PCT] — notify when price comes within PCT%
    of a short- or mid-term support level (default 5%)"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) not in (2, 3):
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /alertsupport SYMBOL [PCT]\n"
            "Example: /alertsupport AAPL 5  (default 5%)"
        )
        return

    try:
        symbol = parts[1].upper()
        pct = float(parts[2]) if len(parts) == 3 else 5.0
        if pct <= 0:
            await update.message.reply_text("❌ Percent must be > 0")
            return

        if not validate_symbol(symbol):
            await update.message.reply_text(f"❌ Invalid ticker: {symbol}")
            return

        # If an active support alert already exists for this symbol, edit its
        # percent in place instead of creating a duplicate
        existing = find_active_alert(symbol, "near_support")
        if existing:
            if update_alert_threshold(existing["id"], pct):
                await update.message.reply_text(
                    f"✅ Alert #{existing['id']} updated: {symbol} within {pct:.1f}% of support "
                    f"(was {existing['threshold']:.1f}%)"
                )
            else:
                await update.message.reply_text("❌ Error updating alert")
            return

        alert_id = create_alert(symbol, "near_support", pct)
        if alert_id:
            await update.message.reply_text(f"✅ Alert #{alert_id} set: {symbol} within {pct:.1f}% of support")
        else:
            await update.message.reply_text("❌ Error creating alert")

    except ValueError:
        await update.message.reply_text("❌ Percent must be a number")
    except Exception as e:
        logger.error(f"Error in /alertsupport: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

def _support_rows(symbols):
    """Compute support levels for many symbols concurrently and shape them
    into rows ready for support.format_support_table(). Fetches a live quote
    first (concurrently) so PRICE/%CHG reflect today rather than yesterday's
    close, and ST/MT are computed against that same live price; falls back to
    the history-derived price if the quote fetch fails. A symbol with no data
    at all still gets a row (current_price=None), rendered as a friendly
    'n/a' row rather than dropped from the table."""
    quotes = get_prices_bulk(symbols)
    price_by_symbol = {s: (q["price"] if q and q.get("price") else None) for s, q in quotes.items()}
    change_by_symbol = {s: (q["change_pct"] if q and q.get("change_pct") is not None else None) for s, q in quotes.items()}

    results = compute_support_levels_bulk((s, price_by_symbol.get(s)) for s in symbols)
    rows = []
    for s in symbols:
        data = results.get(s)
        current_price = price_by_symbol.get(s)
        if current_price is None and data:
            current_price = data["current_price"]  # history-derived fallback (yesterday's close)
        rows.append({
            "symbol": s,
            "currency": get_currency_for_symbol(s),
            "current_price": current_price,
            "daily_change_%": change_by_symbol.get(s),
            "short_term": data["short_term"] if data else None,
            "mid_term": data["mid_term"] if data else None,
        })
    return rows

async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /watch SYMBOL — track a stock's support levels without holding it"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /watch SYMBOL\n"
            "Example: /watch AAPL (SG stocks: /watch D05.SI)"
        )
        return

    symbol = parts[1].upper()
    status = await update.message.reply_text(f"🔍 Validating {symbol}...")
    if not validate_symbol(symbol):
        await update.message.reply_text(f"❌ Invalid ticker: {symbol}")
        await _delete_quietly(status)
        return

    if add_to_watchlist(symbol):
        await update.message.reply_text(f"👁️ Added {symbol} to your watchlist")
    else:
        await update.message.reply_text(f"ℹ️ {symbol} is already on your watchlist")
    await _delete_quietly(status)

async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unwatch SYMBOL — stop tracking a watchlist stock"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text("❌ Usage: /unwatch SYMBOL\nSee your watchlist with /watchlist")
        return

    symbol = parts[1].upper()
    if remove_from_watchlist(symbol):
        await update.message.reply_text(f"✅ Removed {symbol} from your watchlist")
    else:
        await update.message.reply_text(f"❌ {symbol} is not on your watchlist")

async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /watchlist — list watched stocks with their support levels"""
    if not await check_user(update):
        return

    watched = get_watchlist()
    if not watched:
        await update.message.reply_text(
            "📭 Watchlist is empty. Use /watch SYMBOL to track a stock's support levels."
        )
        return

    status = await update.message.reply_text("🔄 Computing support levels...")
    rows = _support_rows([w["symbol"] for w in watched])
    msg = (
        "👁️ *Watchlist — Support Levels*\n"
        f"```\n{format_support_table(rows, fmt_money)}\n```\n"
        "_ST=short-term · MT=mid-term support (below price)_"
    )
    flag_line = format_near_support_line(near_support_flags(rows))
    if flag_line:
        msg += "\n" + flag_line
    await update.message.reply_text(msg, parse_mode="Markdown")
    await _delete_quietly(status)

async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /support SYMBOL — detailed short & mid-term support for one stock
    (works for any ticker, not just watchlist ones). For all your watchlist
    stocks at once, see /watchlist instead."""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /support SYMBOL\n"
            "Example: /support AAPL\n\n"
            "For all your watchlist stocks at once, use /watchlist"
        )
        return

    symbol = parts[1].upper()
    status = await update.message.reply_text(f"🔄 Computing support for {symbol}...")
    currency = get_currency_for_symbol(symbol)
    data = compute_support_levels(symbol)
    if not data:
        await update.message.reply_text(
            f"❌ Couldn't compute support for {symbol} — not enough price history or invalid ticker."
        )
        await _delete_quietly(status)
        return

    def leg(sl):
        if not sl:
            return "n/a (price near its own low)"
        return f"{fmt_money(sl['level'], currency)} (-{sl['distance_pct']:.1f}%) — {sl['basis']}"

    msg = (
        f"📉 *{data['symbol']} — Support Levels*\n\n"
        f"Price: {fmt_money(data['current_price'], currency)}\n"
        f"Short-term: {leg(data['short_term'])}\n"
        f"Mid-term: {leg(data['mid_term'])}\n\n"
        "_(%) = the drop from today's price down to that support._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    await _delete_quietly(status)

async def cmd_resistance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /resistance SYMBOL — nearest short & mid-term resistance and the rise needed to reach it"""
    if not await check_user(update):
        return

    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /resistance SYMBOL\n"
            "Example: /resistance AAPL"
        )
        return

    symbol = parts[1].upper()
    status = await update.message.reply_text(f"🔄 Computing resistance for {symbol}...")
    currency = get_currency_for_symbol(symbol)
    data = compute_resistance_levels(symbol)
    if not data:
        await update.message.reply_text(
            f"❌ Couldn't compute resistance for {symbol} — not enough price history or invalid ticker."
        )
        await _delete_quietly(status)
        return

    def leg(sl):
        if not sl:
            return "n/a (price near its own high)"
        return f"{fmt_money(sl['level'], currency)} (+{sl['distance_pct']:.1f}%) — {sl['basis']}"

    msg = (
        f"📈 *{data['symbol']} — Resistance Levels*\n\n"
        f"Price: {fmt_money(data['current_price'], currency)}\n"
        f"Short-term: {leg(data['short_term'])}\n"
        f"Mid-term: {leg(data['mid_term'])}\n\n"
        "_(%) = the rise needed from today's price up to that resistance._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    await _delete_quietly(status)

async def cmd_earnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /earnings [SYMBOL] — next earnings date + last reported result
    for one stock, or a sweep of holdings + watchlist showing only what's
    upcoming (next 2 weeks) or recently released (last few days)."""
    if not await check_user(update):
        return

    parts = update.message.text.split()

    # /earnings SYMBOL — detailed single-stock view
    if len(parts) == 2:
        symbol = parts[1].upper()
        status = await update.message.reply_text(f"🔄 Checking earnings for {symbol}...")
        currency = get_currency_for_symbol(symbol)
        data = fetch_earnings(symbol)
        if not data or (not data["next"] and not data["last"]):
            await update.message.reply_text(f"❌ No earnings data found for {symbol}.")
            await _delete_quietly(status)
            return

        lines = [f"📅 *{symbol} — Earnings*", ""]

        nxt = data["next"]
        if nxt:
            when = nxt["date"].strftime("%a %d %b %Y")
            timing = nxt["timing"] or "timing not yet confirmed"
            lines.append(f"Next report: {when} (in {nxt['days_until']} days, {timing})")
        else:
            lines.append("Next report: not scheduled yet")

        lst = data["last"]
        if lst:
            actual_str = fmt_money(lst["eps_actual"], currency) if lst["eps_actual"] is not None else "n/a"
            if lst["surprise_pct"] is not None and lst["eps_estimate"] is not None:
                est_str = fmt_money(lst["eps_estimate"], currency)
                lines.append(
                    f"Last quarter: EPS {actual_str} vs {est_str} est "
                    f"({lst['surprise_pct']:+.1f}%), reported {lst['days_since']} days ago"
                )
            else:
                lines.append(f"Last quarter: EPS {actual_str}, reported {lst['days_since']} days ago")
        else:
            lines.append("Last quarter: no reported data available")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        await _delete_quietly(status)
        return

    if len(parts) != 1:
        await update.message.reply_text(
            "❌ Invalid format\n\n"
            "Usage: /earnings [SYMBOL]\n"
            "Example: /earnings AAPL"
        )
        return

    # /earnings — sweep holdings + watchlist for anything upcoming/recent
    held = [h["symbol"] for h in get_all_holdings()]
    watched = [w["symbol"] for w in get_watchlist()]
    symbols = sorted(set(held) | set(watched))
    if not symbols:
        await update.message.reply_text(
            "📭 Nothing to check. Add holdings with /add or track stocks with /watch."
        )
        return

    status = await update.message.reply_text("🔄 Checking earnings calendar...")
    results = fetch_earnings_bulk(symbols)
    upcoming, recent = earnings_flags(results)

    if not upcoming and not recent:
        await update.message.reply_text(
            "📭 Nothing upcoming or recently released for your holdings/watchlist."
        )
        await _delete_quietly(status)
        return

    lines = ["📅 *Earnings Watch — Holdings & Watchlist*", ""]
    if upcoming:
        lines.append(f"_Upcoming (next {UPCOMING_WINDOW_DAYS} days):_")
        for symbol, next_event in upcoming:
            currency = get_currency_for_symbol(symbol)
            lines.append(format_earnings_line(symbol, currency, fmt_money, next_event=next_event))
        lines.append("")
    if recent:
        lines.append("_Recently released:_")
        for symbol, last_event in recent:
            currency = get_currency_for_symbol(symbol)
            lines.append(format_earnings_line(symbol, currency, fmt_money, last_event=last_event))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    await _delete_quietly(status)

async def cmd_reconcile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reconcile — manually trigger the IBKR holdings reconciliation
    that otherwise runs automatically 10 min after each market close."""
    if not await check_user(update):
        return

    if not ibkr_configured():
        await update.message.reply_text(
            "❌ IBKR reconciliation isn't configured — set IBKR_FLEX_TOKEN and "
            "IBKR_FLEX_QUERY_ID to enable it."
        )
        return

    status = await update.message.reply_text("🔄 Fetching IBKR Flex report...")
    try:
        await run_reconciliation()
    except Exception as e:
        logger.error(f"Error in /reconcile: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        await _delete_quietly(status)

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

*— Support & Resistance —*
/support SYMBOL — short & mid-term support for one stock (any ticker)
/resistance SYMBOL — short & mid-term resistance + rise needed to reach it
/watch SYMBOL — track a stock's support levels without holding it
/unwatch SYMBOL — stop tracking a watchlist stock
/watchlist — all your watched stocks' support levels at a glance

*— Earnings —*
/earnings [SYMBOL] — next earnings date + last result for one stock
  (no symbol = holdings + watchlist, upcoming 14 days or reported last 3 days)

*— IBKR Reconciliation —*
/reconcile — manually sync holdings from IBKR (normally runs automatically
  ~10 min after each market close; requires IBKR_FLEX_TOKEN + QUERY_ID)

*— Alerts —*
/alert SYMBOL above|below THRESHOLD — notify me when a price crosses a level
  (re-running this on the same symbol+direction edits the threshold in place)
/alertsupport SYMBOL [PCT] — notify when price comes within PCT% of support
  (default 5%; re-running on the same symbol edits the percent in place)
/alerts — list active alerts
/unalert ID — cancel an alert

*— Settings —*
/settings — see all current settings at a glance
/schedule — when the daily report & market pings fire
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
