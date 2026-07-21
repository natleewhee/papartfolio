from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
import os
import threading
import time
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# Import handlers
from bot_handlers import (
    cmd_add, cmd_remove, cmd_update, cmd_list, cmd_clear, cmd_sync,
    cmd_price, cmd_week, cmd_month, cmd_export, cmd_settings, cmd_schedule, cmd_privacy, cmd_reportstyle,
    cmd_settime, cmd_alert, cmd_alerts, cmd_unalert, cmd_alertsupport,
    cmd_support, cmd_resistance, cmd_watch, cmd_unwatch, cmd_watchlist,
    cmd_help, cmd_start, on_confirmation,
)
from telegram_handler import send_daily_report
from alerts import check_price_alerts
from market_notifications import notify_us_open, notify_us_close, notify_sg_open, notify_sg_close
from portfolio_db import init_db, get_setting
from config import TELEGRAM_BOT_TOKEN, TIMEZONE, DAILY_REPORT_TIME, MARKETS

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class _HealthCheckHandler(BaseHTTPRequestHandler):
    """Bare 200 OK so Render's Web Service port scan succeeds — this bot has
    no real HTTP surface, it only long-polls Telegram.

    Handles HEAD as well as GET: Render's own health checks and most uptime
    pingers (e.g. UptimeRobot) default to HEAD, and BaseHTTPRequestHandler
    returns 501 Not Implemented for any method without a handler — which is
    what was causing the repeated 501s in Render's activity log."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # don't spam the log with a line per health-check hit

def _start_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"✅ Health-check HTTP server listening on port {port}")

KEEP_ALIVE_INTERVAL_SECONDS = 10 * 60  # comfortably under Render's ~15-min idle window

def _start_keep_alive_pinger():
    """Render's free tier idles a Web Service after ~15 min without *inbound*
    HTTP traffic — fatal for this bot, whose Telegram traffic is all outbound
    long-polling. Instead of depending on an external pinger (UptimeRobot
    et al.), periodically GET our own public URL: the request goes out to the
    internet and back in through Render's proxy, so it counts as real inbound
    traffic and keeps the service awake.

    RENDER_EXTERNAL_URL is injected automatically by Render; when it's absent
    (local runs, other hosts) the pinger stays off. A failed ping is only
    logged — the loop must never die, since it's what keeps the lights on."""
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        logger.info("ℹ️ RENDER_EXTERNAL_URL not set — self keep-alive pinger disabled")
        return

    def _ping_loop():
        while True:
            time.sleep(KEEP_ALIVE_INTERVAL_SECONDS)
            try:
                response = requests.get(url, timeout=30)
                if response.status_code != 200:
                    logger.warning(f"⚠️ Keep-alive ping got HTTP {response.status_code}")
            except Exception as e:
                logger.warning(f"⚠️ Keep-alive ping failed: {e}")

    threading.Thread(target=_ping_loop, daemon=True).start()
    logger.info(f"✅ Self keep-alive pinger active: GET {url} every {KEEP_ALIVE_INTERVAL_SECONDS // 60} min")

def main():
    """Start the bot."""

    _start_health_check_server()
    _start_keep_alive_pinger()

    # Initialize database
    init_db()

    # Create bot
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).job_queue(None).build()

    # Add command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("portfolio", cmd_list))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("reportstyle", cmd_reportstyle))
    app.add_handler(CommandHandler("settime", cmd_settime))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("unalert", cmd_unalert))
    app.add_handler(CommandHandler("alertsupport", cmd_alertsupport))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CommandHandler("resistance", cmd_resistance))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))

    # Inline Confirm/Cancel buttons for /remove, /update, /clear
    app.add_handler(CallbackQueryHandler(on_confirmation))

    # Setup scheduler for daily report + periodic alert checks
    scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))
    app.bot_data["scheduler"] = scheduler  # so /settime can reschedule live

    # Report time is user-configurable via /settime; falls back to the config default
    report_time = get_setting("daily_report_time", DAILY_REPORT_TIME)
    hour, minute = map(int, report_time.split(":"))

    # Schedule daily report — weekdays only, markets are closed on weekends
    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=hour, minute=minute, day_of_week="mon-fri", timezone=pytz.timezone(TIMEZONE)),
        id="daily_report",
        name="Daily Portfolio Report",
        replace_existing=True,
    )

    # Check price alerts every 15 minutes
    scheduler.add_job(
        check_price_alerts,
        IntervalTrigger(minutes=15),
        id="price_alerts",
        name="Price Alert Check",
        replace_existing=True,
    )

    # Market open/close pings — each job always runs, but silently no-ops if
    # you hold nothing in that market's currency (see market_notifications.py)
    market_jobs = [
        ("US", "open", notify_us_open),
        ("US", "close", notify_us_close),
        ("SG", "open", notify_sg_open),
        ("SG", "close", notify_sg_close),
    ]
    for market_key, event, func in market_jobs:
        market = MARKETS[market_key]
        hour, minute = market[event]
        scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute, day_of_week="mon-fri", timezone=pytz.timezone(market["timezone"])),
            id=f"market_{market_key.lower()}_{event}",
            name=f"{market['label']} {event}",
            replace_existing=True,
        )

    scheduler.start()
    logger.info(f"✅ Scheduler started. Daily report at {report_time} {TIMEZONE} (weekdays), alert checks every 15 min, market open/close pings enabled")

    # Start bot
    logger.info("🤖 Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
