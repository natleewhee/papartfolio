import os
import sys
from dotenv import load_dotenv

load_dotenv()

def _require_env(name):
    value = os.getenv(name)
    if not value:
        sys.exit(
            f"❌ Missing required environment variable: {name}\n"
            f"   Set it in your .env file (see .env.example) or in your host's environment settings."
        )
    return value

# API Keys
FINNHUB_API_KEY = _require_env("FINNHUB_API_KEY")
TELEGRAM_BOT_TOKEN = _require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = int(_require_env("TELEGRAM_USER_ID"))

# Database (Turso — persists across Render restarts/redeploys, unlike local disk)
TURSO_DATABASE_URL = _require_env("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _require_env("TURSO_AUTH_TOKEN")

# Daily report time (SGT) — overridable at runtime via /settime, this is just the startup default
TIMEZONE = "Asia/Singapore"
DAILY_REPORT_TIME = "20:30"  # 8:30 PM SGT (end of US trading)

# Portfolio totals are converted to this currency for the combined grand total
HOME_CURRENCY = "SGD"

# Logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)