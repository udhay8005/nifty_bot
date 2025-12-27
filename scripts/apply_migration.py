import sqlite3
import os
import logging
from datetime import datetime

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Migration")

# Define DB Path (Same as config)
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'nifty_bot.db')

def apply_migration():
    """
    Intelligently upgrades the database schema to the latest version.
    - Creates missing tables.
    - Seeds default strategy parameters.
    - Preserves existing data.
    """
    logger.info(f"📂 Checking Database at: {DB_PATH}")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    try:
        # 1. SETTINGS TABLE (System Flags)
        c.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                k TEXT PRIMARY KEY,
                v TEXT,
                updated_at TEXT
            )
        ''')
        logger.info("✅ Table 'settings' checked.")

        # 2. STRATEGY PARAMS (The Brain 🧠)
        # This is the new table for dynamic Telegram control
        c.execute('''
            CREATE TABLE IF NOT EXISTS strategy_params (
                k TEXT PRIMARY KEY,
                v TEXT
            )
        ''')
        logger.info("✅ Table 'strategy_params' checked.")

        # 3. TRADES TABLE (History & PnL)
        # Tracks entry, exit, quantity, and PnL for analysis
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
        logger.info("✅ Table 'trades' checked.")

        # 4. AUDIT LOG (Security)
        c.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                actor_chat_id TEXT,
                command TEXT,
                details TEXT
            )
        ''')
        logger.info("✅ Table 'audit_log' checked.")

        # 5. DAILY RUN (Crash Recovery State)
        c.execute('''
            CREATE TABLE IF NOT EXISTS daily_run (
                date TEXT PRIMARY KEY,
                summary_sent BOOLEAN DEFAULT 0,
                pnl REAL DEFAULT 0.0
            )
        ''')
        logger.info("✅ Table 'daily_run' checked.")

        # ==========================================
        # 🌱 SEEDING DEFAULT DATA
        # ==========================================

        # A. Default Strategy Rules (Video Strategy)
        strat_defaults = {
            'LOT_SIZE': '50',
            'TARGET_POINTS': '40',
            'SL_POINTS': '20',
            'TARGET_PREMIUM': '180.0', 
            'TRAILING_ON': '1',        # Enabled
            'TRAILING_TRIGGER': '20',
            'TRAILING_GAP': '15'
        }
        
        seeded_count = 0
        for k, v in strat_defaults.items():
            # Only insert if it doesn't exist (don't overwrite user customization)
            c.execute("INSERT OR IGNORE INTO strategy_params (k, v) VALUES (?, ?)", (k, v))
            if c.rowcount > 0: seeded_count += 1
            
        if seeded_count > 0:
            logger.info(f"🌱 Seeded {seeded_count} new strategy parameters.")

        # B. Default System Flags
        sys_defaults = {
            'BOT_MODE': 'paper', 
            'PAUSED': '0', 
            'KILLED': '0'
        }
        for k, v in sys_defaults.items():
            c.execute("INSERT OR IGNORE INTO settings (k, v, updated_at) VALUES (?, ?, ?)", 
                      (k, v, datetime.now().isoformat()))

        conn.commit()
        logger.info("🚀 Migration Complete! Database is ready for Intelligence Mode.")

    except Exception as e:
        logger.error(f"❌ Migration Failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    apply_migration()