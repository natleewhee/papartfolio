import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = int(os.getenv("TELEGRAM_USER_ID"))

# Database
DB_PATH = "portfolio.db"

# Technical analysis settings
EMA_PERIODS = [20, 50]
LOOKBACK_DAYS = 100

# Macro indicators to track
MACRO_SYMBOLS = ["SPY", "QQQ", "DXY"]  # S&P 500, Nasdaq, Dollar Index

# Daily report time (SGT)
TIMEZONE = "Asia/Singapore"
DAILY_REPORT_TIME = "20:30"  # 8:30 PM SGT (end of US trading)

# NewsAPI settings
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")  # Get from newsapi.org (free tier)

# Logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)