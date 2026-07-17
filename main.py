from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# Import handlers
from bot_handlers import (
    cmd_add, cmd_remove, cmd_update, cmd_list, cmd_clear, cmd_sync,
    cmd_price, cmd_week, cmd_month, cmd_export, cmd_privacy, cmd_reportstyle,
    cmd_settime, cmd_alert, cmd_alerts, cmd_unalert,
    cmd_help, cmd_start, clear_confirmation, remove_confirmation, update_confirmation,
    CONFIRM_CLEAR, CONFIRM_REMOVE, CONFIRM_UPDATE,
)
from telegram_handler import send_daily_report
from alerts import check_price_alerts
from portfolio_db import init_db, get_setting
from config import TELEGRAM_BOT_TOKEN, TIMEZONE, DAILY_REPORT_TIME

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class _HealthCheckHandler(BaseHTTPRequestHandler):
    """Bare 200 OK so Render's Web Service port scan succeeds — this bot has
    no real HTTP surface, it only long-polls Telegram."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # don't spam the log with a line per health-check hit

def _start_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"✅ Health-check HTTP server listening on port {port}")

def main():
    """Start the bot."""

    _start_health_check_server()

    # Initialize database
    init_db()

    # Create bot
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).job_queue(None).build()

    # Add command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("portfolio", cmd_list))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("reportstyle", cmd_reportstyle))
    app.add_handler(CommandHandler("settime", cmd_settime))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("unalert", cmd_unalert))

    # Clear conversation handler
    clear_handler = ConversationHandler(
        entry_points=[CommandHandler("clear", cmd_clear)],
        states={CONFIRM_CLEAR: [MessageHandler(filters.TEXT, clear_confirmation)]},
        fallbacks=[CommandHandler("help", cmd_help)],
    )
    app.add_handler(clear_handler)

    # Remove conversation handler (asks for confirmation before deleting a holding)
    remove_handler = ConversationHandler(
        entry_points=[CommandHandler("remove", cmd_remove)],
        states={CONFIRM_REMOVE: [MessageHandler(filters.TEXT, remove_confirmation)]},
        fallbacks=[CommandHandler("help", cmd_help)],
    )
    app.add_handler(remove_handler)

    # Update conversation handler (asks for confirmation before overwriting a holding)
    update_handler = ConversationHandler(
        entry_points=[CommandHandler("update", cmd_update)],
        states={CONFIRM_UPDATE: [MessageHandler(filters.TEXT, update_confirmation)]},
        fallbacks=[CommandHandler("help", cmd_help)],
    )
    app.add_handler(update_handler)

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

    scheduler.start()
    logger.info(f"✅ Scheduler started. Daily report at {report_time} {TIMEZONE} (weekdays), alert checks every 15 min")

    # Start bot
    logger.info("🤖 Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
