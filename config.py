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
# 20:30 SGT is US pre-market (~08:30 ET): both feeds are "settled" — SG's session
# has closed and the US figures reflect the last completed US session — so the
# report avoids partial-day noise. (Not literally the end of US trading.)
DAILY_REPORT_TIME = "20:30"

# Portfolio totals are converted to this currency for the combined grand total
HOME_CURRENCY = "SGD"

# IBKR Flex Web Service (optional — holdings reconciliation after market
# close). Both must be set for the feature to activate; leave unset to
# disable it entirely, nothing else breaks. Set up first in IBKR Account
# Management: Reports > Flex Queries > new "Activity"/"Open Positions" query
# including at minimum Symbol, Position, Cost Basis Price, Currency, and
# Asset Category columns, then Reports > Settings > Flex Web Service to
# generate the token.
IBKR_FLEX_TOKEN = os.getenv("IBKR_FLEX_TOKEN")
IBKR_FLEX_QUERY_ID = os.getenv("IBKR_FLEX_QUERY_ID")

# Second, later reconciliation pass (SGT, weekdays). IBKR's own Flex
# "Activity" data refreshes once/day at their own close-of-business batch —
# the 10-min-after-close pass (see main.py) often fires before that batch
# has actually finished, so it can silently re-read the *previous* day's
# snapshot. This fixed evening time gives IBKR's processing many more hours
# to complete and lands right before the daily report, so what you read at
# report time is as fresh as IBKR allows.
IBKR_RECONCILE_CATCHUP_TIME = "20:00"

# Anthropic API (optional — AI-generated market brief with web search,
# synthesizing overnight/company news for the daily report and /brief).
# Leave unset to disable it entirely, nothing else breaks.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Markets to ping open/close for — only fires if you actually hold something
# in that currency. Times are in each market's own timezone (weekdays only,
# no holiday calendar).
MARKETS = {
    "US": {
        "label": "US Market (NYSE/NASDAQ)",
        "currency": "USD",
        "timezone": "America/New_York",
        "open": (9, 30),
        "close": (16, 0),
    },
    "SG": {
        "label": "SG Market (SGX)",
        "currency": "SGD",
        "timezone": "Asia/Singapore",
        "open": (9, 0),
        "close": (17, 0),
    },
}

def daily_report_day_of_week(report_time):
    """Which SGT weekdays the daily report should fire on, given its
    configured HH:MM time.

    Before SG market open (09:00 SGT), neither market has traded yet that
    calendar day — so a Monday firing would just re-show Friday's already-
    reported close under a new date (nothing trades over the weekend),
    which reads as stale. "tue-sat" instead reports each weekday's close
    the following SGT morning, matching how the market-close and
    reconciliation jobs already land (Tue-Sat mornings from Mon-Fri
    closes, see main.py).

    At/after SG open, that day's own SG session is underway or complete,
    so a Monday firing has genuinely new content — "mon-fri" is correct,
    which is why the default 20:30 SGT report time is unaffected."""
    hour, minute = map(int, report_time.split(":"))
    sg_open_hour, sg_open_minute = MARKETS["SG"]["open"]
    return "tue-sat" if (hour, minute) < (sg_open_hour, sg_open_minute) else "mon-fri"

# Logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)