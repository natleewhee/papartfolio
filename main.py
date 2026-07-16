from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from datetime import datetime

# Import handlers
from bot_handlers import (
    cmd_add, cmd_remove, cmd_update, cmd_list, cmd_clear, cmd_sync,
    cmd_help, cmd_start, clear_confirmation, CONFIRM_CLEAR
)
from telegram_handler import send_daily_report
from portfolio_db import init_db
from config import TELEGRAM_BOT_TOKEN, TIMEZONE, DAILY_REPORT_TIME

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Start the bot."""
    
    # Initialize database
    init_db()
    
    # Create bot
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("portfolio", cmd_list))
    app.add_handler(CommandHandler("sync", cmd_sync))
    
    # Clear conversation handler
    clear_handler = ConversationHandler(
        entry_points=[CommandHandler("clear", cmd_clear)],
        states={CONFIRM_CLEAR: [MessageHandler(filters.TEXT, clear_confirmation)]},
        fallbacks=[CommandHandler("help", cmd_help)],
    )
    app.add_handler(clear_handler)
    
    # Setup scheduler for daily report
    scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))
    
    # Parse time (e.g., "20:30")
    hour, minute = map(int, DAILY_REPORT_TIME.split(":"))
    
    # Schedule daily report
    scheduler.add_job(
        send_daily_report,
        CronTrigger(hour=hour, minute=minute, timezone=pytz.timezone(TIMEZONE)),
        id="daily_report",
        name="Daily Portfolio Report",
        replace_existing=True,
    )
    
    scheduler.start()
    logger.info(f"✅ Scheduler started. Daily report at {DAILY_REPORT_TIME} {TIMEZONE}")
    
    # Start bot
    logger.info("🤖 Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()