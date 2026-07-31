import libsql
from datetime import datetime
from config import TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
import logging

logger = logging.getLogger(__name__)

def _connect():
    return libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)

def _rows_to_dicts(cursor, rows):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in rows]

def init_db():
    """Initialize database schema (Turso)."""
    conn = _connect()
    cursor = conn.cursor()
    
    # Holdings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            shares INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Daily snapshots
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            shares INTEGER NOT NULL,
            value REAL NOT NULL,
            daily_change_dollar REAL,
            daily_change_pct REAL,
            UNIQUE(date, symbol)
        )
    """)
    
    # Portfolio aggregates
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_aggregates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL UNIQUE,
            total_value REAL NOT NULL,
            total_cost_basis REAL NOT NULL,
            daily_change_dollar REAL,
            daily_change_pct REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Key-value settings (e.g. privacy mode)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Price alerts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            threshold REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            triggered_at TIMESTAMP
        )
    """)

    # Watchlist — stocks tracked for support levels without necessarily holding them
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    # Migration: currency column (added after the original schema). Existing
    # rows backfill to 'USD' automatically via the column default.
    try:
        cursor.execute("ALTER TABLE holdings ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD'")
        conn.commit()
    except ValueError as e:
        if "duplicate column name" not in str(e):
            raise

    # Migration: manual support-level override columns (added after the
    # original schema). NULL means "auto-compute this horizon" — the
    # original, still-default behavior.
    for column in ("manual_st_support", "manual_mt_support"):
        try:
            cursor.execute(f"ALTER TABLE watchlist ADD COLUMN {column} REAL")
            conn.commit()
        except ValueError as e:
            if "duplicate column name" not in str(e):
                raise

    conn.close()
    logger.info("✅ Database initialized")

def add_holding(symbol, shares, avg_cost, currency="USD"):
    """Add new holding to portfolio."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO holdings (symbol, shares, avg_cost, currency)
            VALUES (?, ?, ?, ?)
        """, (symbol.upper(), shares, avg_cost, currency))
        conn.commit()
        conn.close()
        logger.info(f"✅ Added {symbol}: {shares} shares @ ${avg_cost}")
        return True
    except ValueError as e:
        if "UNIQUE constraint failed" in str(e):
            logger.error(f"❌ {symbol} already exists in portfolio")
            return False
        logger.error(f"❌ Error adding holding: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Error adding holding: {e}")
        return False

def remove_holding(symbol):
    """Remove holding from portfolio."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM holdings WHERE symbol = ?", (symbol.upper(),))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            logger.info(f"✅ Removed {symbol}")
            return True
        else:
            logger.warning(f"⚠️ {symbol} not found")
            return False
    except Exception as e:
        logger.error(f"❌ Error removing holding: {e}")
        return False

def update_holding(symbol, shares, avg_cost):
    """Update existing holding."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE holdings
            SET shares = ?, avg_cost = ?, updated_at = CURRENT_TIMESTAMP
            WHERE symbol = ?
        """, (shares, avg_cost, symbol.upper()))
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        if updated > 0:
            logger.info(f"✅ Updated {symbol}: {shares} shares @ ${avg_cost}")
            return True
        else:
            logger.warning(f"⚠️ {symbol} not found")
            return False
    except Exception as e:
        logger.error(f"❌ Error updating holding: {e}")
        return False

def get_all_holdings():
    """Fetch all holdings from database."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM holdings ORDER BY symbol")
        holdings = _rows_to_dicts(cursor, cursor.fetchall())
        conn.close()
        return holdings
    except Exception as e:
        logger.error(f"❌ Error fetching holdings: {e}")
        return []

def get_holding(symbol):
    """Fetch single holding."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM holdings WHERE symbol = ?", (symbol.upper(),))
        row = cursor.fetchone()
        conn.close()
        return _rows_to_dicts(cursor, [row])[0] if row else None
    except Exception as e:
        logger.error(f"❌ Error fetching holding: {e}")
        return None

def clear_all_holdings():
    """Delete all holdings (use with caution)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM holdings")
        conn.commit()
        conn.close()
        logger.info("✅ Cleared all holdings")
        return True
    except Exception as e:
        logger.error(f"❌ Error clearing holdings: {e}")
        return False

def save_daily_snapshot(date, symbol, price, shares, daily_change_dollar, daily_change_pct):
    """Save end-of-day snapshot."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        value = price * shares
        cursor.execute("""
            INSERT OR REPLACE INTO daily_snapshots
            (date, symbol, price, shares, value, daily_change_dollar, daily_change_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date, symbol, price, shares, value, daily_change_dollar, daily_change_pct))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error saving snapshot: {e}")

def save_portfolio_aggregate(date, total_value, total_cost_basis, daily_change_dollar, daily_change_pct):
    """Save portfolio aggregate."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO portfolio_aggregates
            (date, total_value, total_cost_basis, daily_change_dollar, daily_change_pct)
            VALUES (?, ?, ?, ?, ?)
        """, (date, total_value, total_cost_basis, daily_change_dollar, daily_change_pct))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error saving aggregate: {e}")

def get_earliest_aggregate_since(cutoff_date):
    """Fetch the earliest portfolio_aggregates row on/after cutoff_date (YYYY-MM-DD)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM portfolio_aggregates WHERE date >= ? ORDER BY date ASC LIMIT 1",
            (cutoff_date,)
        )
        row = cursor.fetchone()
        conn.close()
        return _rows_to_dicts(cursor, [row])[0] if row else None
    except Exception as e:
        logger.error(f"❌ Error fetching aggregate since {cutoff_date}: {e}")
        return None

# ==================== SETTINGS ====================

def get_setting(key, default=None):
    """Fetch a single setting value (always a string), or default if unset."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception as e:
        logger.error(f"❌ Error fetching setting {key}: {e}")
        return default

def set_setting(key, value):
    """Create or update a setting value."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Error saving setting {key}: {e}")
        return False

# ==================== ALERTS ====================

def create_alert(symbol, direction, threshold):
    """Create a new active price alert. direction is 'above' or 'below'."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO alerts (symbol, direction, threshold) VALUES (?, ?, ?)",
            (symbol.upper(), direction, threshold)
        )
        conn.commit()
        alert_id = cursor.lastrowid
        conn.close()
        return alert_id
    except Exception as e:
        logger.error(f"❌ Error creating alert: {e}")
        return None

def get_active_alerts():
    """Fetch all currently-active alerts."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts WHERE active = 1 ORDER BY symbol")
        alerts = _rows_to_dicts(cursor, cursor.fetchall())
        conn.close()
        return alerts
    except Exception as e:
        logger.error(f"❌ Error fetching alerts: {e}")
        return []

def find_active_alert(symbol, direction):
    """Find an existing active alert for this symbol+direction, if any (so /alert can edit in place)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM alerts WHERE active = 1 AND symbol = ? AND direction = ? LIMIT 1",
            (symbol.upper(), direction)
        )
        row = cursor.fetchone()
        conn.close()
        return _rows_to_dicts(cursor, [row])[0] if row else None
    except Exception as e:
        logger.error(f"❌ Error finding alert for {symbol} {direction}: {e}")
        return None

def update_alert_threshold(alert_id, threshold):
    """Update an existing active alert's threshold in place."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE alerts SET threshold = ? WHERE id = ? AND active = 1",
            (threshold, alert_id)
        )
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return updated
    except Exception as e:
        logger.error(f"❌ Error updating alert {alert_id}: {e}")
        return False

def deactivate_alert(alert_id):
    """Deactivate an alert (whether triggered or cancelled by the user)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE alerts SET active = 0, triggered_at = CURRENT_TIMESTAMP WHERE id = ? AND active = 1",
            (alert_id,)
        )
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return updated
    except Exception as e:
        logger.error(f"❌ Error deactivating alert {alert_id}: {e}")
        return False

# ==================== WATCHLIST ====================

def add_to_watchlist(symbol):
    """Add a symbol to the watchlist. Returns True if added, False if it was
    already there (or on error)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO watchlist (symbol) VALUES (?)", (symbol.upper(),))
        conn.commit()
        conn.close()
        logger.info(f"✅ Added {symbol} to watchlist")
        return True
    except ValueError as e:
        if "UNIQUE constraint failed" in str(e):
            return False
        logger.error(f"❌ Error adding {symbol} to watchlist: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Error adding {symbol} to watchlist: {e}")
        return False

def remove_from_watchlist(symbol):
    """Remove a symbol from the watchlist. Returns True if a row was deleted."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted > 0
    except Exception as e:
        logger.error(f"❌ Error removing {symbol} from watchlist: {e}")
        return False

def get_watchlist():
    """Fetch all watchlist symbols (list of dicts), ordered by symbol."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM watchlist ORDER BY symbol")
        rows = _rows_to_dicts(cursor, cursor.fetchall())
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"❌ Error fetching watchlist: {e}")
        return []

def is_watched(symbol):
    """Whether a symbol is already on the watchlist."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        logger.error(f"❌ Error checking watchlist for {symbol}: {e}")
        return False

def set_watchlist_support(symbol, st_support=None, mt_support=None):
    """Set (or clear, by passing None) manual ST/MT support prices for a
    watchlist symbol — these override the auto-computed levels for that
    symbol wherever support is shown or alerted on. Returns True if a row
    was updated (i.e. the symbol is actually on the watchlist)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE watchlist SET manual_st_support = ?, manual_mt_support = ? WHERE symbol = ?",
            (st_support, mt_support, symbol.upper()),
        )
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        return updated > 0
    except Exception as e:
        logger.error(f"❌ Error setting support levels for {symbol}: {e}")
        return False

def get_watchlist_entry(symbol):
    """Fetch one watchlist row (including its manual support levels, if
    set), or None if the symbol isn't watched."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        row = cursor.fetchone()
        result = _rows_to_dicts(cursor, [row])[0] if row else None
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error fetching watchlist entry for {symbol}: {e}")
        return None