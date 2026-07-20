"""Dummy env vars so config.py's _require_env() checks pass at import time —
must run before any test module imports an app module (portfolio, support,
fetcher, ...), since config.py sys.exit()s on a missing var. conftest.py is
imported by pytest before test collection, which is exactly what we need."""
import os

os.environ.setdefault("FINNHUB_API_KEY", "test-finnhub-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-telegram-token")
os.environ.setdefault("TELEGRAM_USER_ID", "1")
os.environ.setdefault("TURSO_DATABASE_URL", "libsql://test.turso.io")
os.environ.setdefault("TURSO_AUTH_TOKEN", "test-turso-token")
