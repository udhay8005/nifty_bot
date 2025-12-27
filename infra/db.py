import sqlite3
import os
import logging
from datetime import datetime
from threading import Lock

# Security Imports
try:
    from infra.security import encrypt_value, decrypt_value
except ImportError:
    # Fallback prevents crash if security.py is broken/missing during initial setup
    def encrypt_value(x): return x
    def decrypt_value(x): return x

# Configuration
DB_PATH = os.getenv('DB_PATH', 'nifty_bot.db')
_db_lock = Lock()
logger = logging.getLogger("DB")

def get_db():
    """Establishes a thread-safe connection to SQLite."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # Access columns by name
    return conn

def init_db():
    """Creates necessary tables and seeds default 'Brain' values."""
    with _db_lock:
        conn = get_db()
        c = conn.cursor()

        # 1. System Settings (Token, Mode, Flags)
        c.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                k TEXT PRIMARY KEY,
                v TEXT,
                updated_at TEXT
            )
        ''')

        # 2. Strategy Parameters (THE BRAIN 🧠)
        # Stores dynamic rules: Target, SL, Trailing Settings
        c.execute('''
            CREATE TABLE IF NOT EXISTS strategy_params (
                k TEXT PRIMARY KEY,
                v TEXT
            )
        ''')

        # 3. Trade History (Performance Tracking)
        c.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                mode TEXT,
                symbol TEXT,
                side TEXT,
                entry_time TEXT,
                entry_price REAL,
                exit_time TEXT,
                exit_price REAL,
                quantity INTEGER,
                pnl REAL,
                status TEXT
            )
        ''')

        # 4. Audit Log (Security)
        c.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                actor_chat_id TEXT,
                command TEXT,
                details TEXT
            )
        ''')

        # 5. Daily Run (Summary Tracking)
        c.execute('''
            CREATE TABLE IF NOT EXISTS daily_run (
                date TEXT PRIMARY KEY,
                summary_sent BOOLEAN DEFAULT 0,
                pnl REAL DEFAULT 0.0
            )
        ''')

        # --- SEED DEFAULTS ---
        
        # A. Strategy Defaults (Smart Logic)
        strat_defaults = {
            'LOT_SIZE': '50',
            'TARGET_POINTS': '40',
            'SL_POINTS': '20',
            'TARGET_PREMIUM': '180.0', # The breakout trigger price
            'TRAILING_ON': '1',        # 1 = True (Enabled)
            'TRAILING_TRIGGER': '20',  # Start trailing after 20pts profit
            'TRAILING_GAP': '15'       # Keep SL 15pts away from LTP
        }
        for k, v in strat_defaults.items():
            c.execute("INSERT OR IGNORE INTO strategy_params (k, v) VALUES (?, ?)", (k, v))

        # B. System Defaults (Safety Flags)
        sys_defaults = {'BOT_MODE': 'paper', 'PAUSED': '0', 'KILLED': '0'}
        for k, v in sys_defaults.items():
            c.execute("INSERT OR IGNORE INTO settings (k, v, updated_at) VALUES (?, ?, ?)", 
                      (k, v, datetime.now().isoformat()))

        conn.commit()
        conn.close()
        logger.info("Database initialized (Brain & Memory Ready).")

# ==========================================
# 🧠 BRAIN FUNCTIONS (Strategy Params)
# ==========================================

def set_param(key, value):
    """Updates a strategy rule (e.g., Change Target to 50) via Telegram."""
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("INSERT OR REPLACE INTO strategy_params (k, v) VALUES (?, ?)", (str(key), str(value)))
            conn.commit()
        finally:
            conn.close()

def get_all_params():
    """Fetches the entire strategy configuration to load into Context."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT k, v FROM strategy_params").fetchall()
        return {row['k']: row['v'] for row in rows}
    finally:
        conn.close()

# ==========================================
# ⚙️ SYSTEM SETTINGS (Tokens & Flags)
# ==========================================

def set_setting(key, value, encrypt=False):
    if value is None: return
    with _db_lock:
        val_to_store = encrypt_value(value) if encrypt else value
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (k, v, updated_at) VALUES (?, ?, ?)",
                (key, val_to_store, datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()

def get_setting(key, decrypt=False):
    conn = get_db()
    try:
        row = conn.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        if row:
            return decrypt_value(row['v']) if decrypt else row['v']
        return None
    finally:
        conn.close()

# ==========================================
# 📜 LOGGING & HISTORY (Trades & Audits)
# ==========================================

def log_trade(t):
    """Saves a completed trade result to the database."""
    with _db_lock:
        conn = get_db()
        try:
            conn.execute('''
                INSERT INTO trades (
                    date, mode, symbol, side, entry_time, entry_price, 
                    exit_time, exit_price, quantity, pnl, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                t['date'], t['mode'], t['symbol'], t['side'],
                t['entry_time'], t['entry_price'], t['exit_time'], t['exit_price'],
                t['quantity'], t['pnl'], t['status']
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
        finally:
            conn.close()

def get_trade_history(limit=5):
    """Fetches recent trades for the /history command."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def log_audit(chat_id, command, details):
    """Logs admin actions for security."""
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("INSERT INTO audit_log (ts, actor_chat_id, command, details) VALUES (?, ?, ?, ?)",
                         (datetime.now().isoformat(), str(chat_id), command, details))
            conn.commit()
        finally:
            conn.close()