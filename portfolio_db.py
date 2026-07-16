import sqlite3
from datetime import datetime
from config import DB_PATH
import logging

logger = logging.getLogger(__name__)

def init_db():
    """Initialize SQLite database with schema."""
    conn = sqlite3.connect(DB_PATH)
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
            daily_change_$ REAL,
            daily_change_% REAL,
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
            daily_change_$ REAL,
            daily_change_% REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized")

def add_holding(symbol, shares, avg_cost):
    """Add new holding to portfolio."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO holdings (symbol, shares, avg_cost)
            VALUES (?, ?, ?)
        """, (symbol.upper(), shares, avg_cost))
        conn.commit()
        conn.close()
        logger.info(f"✅ Added {symbol}: {shares} shares @ ${avg_cost}")
        return True
    except sqlite3.IntegrityError:
        logger.error(f"❌ {symbol} already exists in portfolio")
        return False
    except Exception as e:
        logger.error(f"❌ Error adding holding: {e}")
        return False

def remove_holding(symbol):
    """Remove holding from portfolio."""
    try:
        conn = sqlite3.connect(DB_PATH)
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
        conn = sqlite3.connect(DB_PATH)
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM holdings ORDER BY symbol")
        holdings = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return holdings
    except Exception as e:
        logger.error(f"❌ Error fetching holdings: {e}")
        return []

def get_holding(symbol):
    """Fetch single holding."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM holdings WHERE symbol = ?", (symbol.upper(),))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ Error fetching holding: {e}")
        return None

def clear_all_holdings():
    """Delete all holdings (use with caution)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM holdings")
        conn.commit()
        conn.close()
        logger.info("✅ Cleared all holdings")
        return True
    except Exception as e:
        logger.error(f"❌ Error clearing holdings: {e}")
        return False

def save_daily_snapshot(date, symbol, price, shares, daily_change_$, daily_change_%):
    """Save end-of-day snapshot."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        value = price * shares
        cursor.execute("""
            INSERT OR REPLACE INTO daily_snapshots 
            (date, symbol, price, shares, value, daily_change_$, daily_change_%)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date, symbol, price, shares, value, daily_change_$, daily_change_%))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error saving snapshot: {e}")

def save_portfolio_aggregate(date, total_value, total_cost_basis, daily_change_$, daily_change_%):
    """Save portfolio aggregate."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO portfolio_aggregates
            (date, total_value, total_cost_basis, daily_change_$, daily_change_%)
            VALUES (?, ?, ?, ?, ?)
        """, (date, total_value, total_cost_basis, daily_change_$, daily_change_%))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error saving aggregate: {e}")