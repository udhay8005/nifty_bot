import sqlite3
import logging
import json
from datetime import datetime, timedelta
import config

logger = logging.getLogger("Database")

def get_db():
    """Context manager for Database connections."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row 
    return conn

def init_db():
    """Initializes the database schema."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # 1. Configuration Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS params (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # 2. Trade History
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, mode TEXT, symbol TEXT, side TEXT,
                    entry_time TEXT, entry_price REAL,
                    exit_time TEXT, exit_price REAL,
                    quantity INTEGER, pnl REAL, status TEXT, meta TEXT
                )
            ''')
            
            # 3. Audit Log
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, user_id INTEGER, command TEXT, details TEXT
                )
            ''')
            
            # Seed Default Parameters
            cursor.execute("SELECT count(*) FROM params")
            if cursor.fetchone()[0] == 0:
                logger.info("⚡ DB Empty. Seeding Defaults...")
                defaults = {
                    'TARGET_PREMIUM': str(config.TARGET_PREMIUM),
                    'TARGET_POINTS': str(config.TARGET_POINTS),
                    'SL_POINTS': str(config.SL_POINTS),
                    'LOT_SIZE': str(config.LOT_SIZE),
                    'TRAILING_ON': '1' if config.TRAILING_ON else '0',
                    'UPSTOX_ACCESS_TOKEN': ''
                }
                for k, v in defaults.items():
                    cursor.execute("INSERT OR IGNORE INTO params (key, value) VALUES (?, ?)", (k, v))
            conn.commit()
            logger.info(f"✅ Database connected: {config.DB_PATH}")
            
    except Exception as e:
        logger.error(f"❌ Database Initialization Failed: {e}")
        raise e

# =========================================================
# 📊 ANALYTICS
# =========================================================

def get_weekly_pnl():
    try:
        today = datetime.now().date()
        start_of_week = today - timedelta(days=today.weekday())
        start_date_str = start_of_week.strftime('%Y-%m-%d')
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT SUM(pnl) FROM trades WHERE date >= ?', (start_date_str,))
            result = cursor.fetchone()[0]
            return float(result) if result else 0.0
    except Exception as e:
        logger.error(f"Failed to fetch Weekly PnL: {e}")
        return 0.0

def get_todays_pnl_summary():
    try:
        today_str = datetime.now().strftime('%Y-%m-%d')
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) as total_trades, SUM(pnl) as net_pnl,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses
                FROM trades WHERE date = ?
            ''', (today_str,))
            row = cursor.fetchone()
            if row:
                return {
                    'count': row['total_trades'] or 0,
                    'pnl': row['net_pnl'] or 0.0,
                    'wins': row['wins'] or 0,
                    'losses': row['losses'] or 0
                }
            return {'count': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0}
    except Exception as e:
        logger.error(f"Failed to fetch Daily Summary: {e}")
        return {'count': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0}

# =========================================================
# 🧹 MAINTENANCE (Fixed VACUUM Error)
# =========================================================

def cleanup_old_logs():
    """
    Deletes old logs and optimizes DB. 
    Fixes the 'VACUUM from within transaction' error.
    """
    try:
        retention_days = config.DB_LOG_RETENTION_DAYS
        cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')
        
        deleted_count = 0
        
        # 1. Delete Old Records (Transactional)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff_date,))
            deleted_count = cursor.rowcount
            conn.commit()
            
        # 2. Vacuum (MUST be outside a transaction)
        # We connect with isolation_level=None to enable autocommit mode
        conn = sqlite3.connect(config.DB_PATH, isolation_level=None)
        conn.execute("VACUUM")
        conn.close()
        
        if deleted_count > 0:
            logger.info(f"🧹 Maintenance: Cleaned {deleted_count} old logs.")
            
    except Exception as e:
        logger.error(f"DB Maintenance Failed: {e}")

# =========================================================
# 📝 LOGGING & HELPERS
# =========================================================

def log_trade(trade_data):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trades (date, mode, symbol, side, entry_time, entry_price, 
                                  exit_time, exit_price, quantity, pnl, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_data['date'], trade_data['mode'], trade_data['symbol'], 
                trade_data['side'], trade_data['entry_time'], trade_data['entry_price'],
                trade_data['exit_time'], trade_data['exit_price'], trade_data['quantity'],
                trade_data['pnl'], trade_data['status']
            ))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")

def log_audit(user_id, command, details):
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with get_db() as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, user_id, command, details) VALUES (?, ?, ?, ?)",
                (timestamp, user_id, command, details)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Audit Log Failed: {e}")

def get_trade_history(limit=5):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
    except Exception: return []

def get_param(key):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM params WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else None
    except Exception: return None

def set_param(key, value):
    try:
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO params (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()
    except Exception as e:
        logger.error(f"Set Param Failed: {e}")

def get_all_params():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM params")
            return {row['key']: row['value'] for row in cursor.fetchall()}
    except Exception: return {}

# 🛠️ ALIASES
get_setting = get_param
set_setting = set_param