-- =========================================================
-- 🚀 MIGRATION V2: INTELLIGENCE UPGRADE
-- =========================================================
-- This script adds the "Brain" (Strategy Params) and "Memory" (Trades)
-- to the database structure.

-- 1. System Settings Table
-- Stores core flags like Mode, Paused, and Access Token.
CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT,
    updated_at TEXT
);

-- 2. Strategy Parameters Table (THE BRAIN 🧠)
-- Stores dynamic rules that can be changed via Telegram.
CREATE TABLE IF NOT EXISTS strategy_params (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- 3. Trade History Table (THE MEMORY 📜)
-- Permanently stores every trade for PnL analysis and history checks.
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    mode TEXT,      -- 'LIVE' or 'PAPER'
    symbol TEXT,
    side TEXT,      -- 'CE' or 'PE'
    entry_time TEXT,
    entry_price REAL,
    exit_time TEXT,
    exit_price REAL,
    quantity INTEGER,
    pnl REAL,
    status TEXT     -- 'WIN', 'LOSS', 'COST'
);

-- 4. Audit Log Table
-- Tracks who sent what command for security.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    actor_chat_id TEXT,
    command TEXT,
    details TEXT
);

-- 5. Daily Run Table
-- Used for crash recovery (remembers if we sent the daily summary).
CREATE TABLE IF NOT EXISTS daily_run (
    date TEXT PRIMARY KEY,
    summary_sent BOOLEAN DEFAULT 0,
    pnl REAL DEFAULT 0.0
);

-- =========================================================
-- 🌱 SEED DEFAULT VALUES (Using INSERT OR IGNORE)
-- =========================================================

-- Strategy Defaults (The Video Strategy)
INSERT OR IGNORE INTO strategy_params (k, v) VALUES ('LOT_SIZE', '50');
INSERT OR IGNORE INTO strategy_params (k, v) VALUES ('TARGET_POINTS', '40');
INSERT OR IGNORE INTO strategy_params (k, v) VALUES ('SL_POINTS', '20');
INSERT OR IGNORE INTO strategy_params (k, v) VALUES ('TARGET_PREMIUM', '180.0'); -- Breakout Trigger
INSERT OR IGNORE INTO strategy_params (k, v) VALUES ('TRAILING_ON', '1');        -- 1 = Enabled
INSERT OR IGNORE INTO strategy_params (k, v) VALUES ('TRAILING_TRIGGER', '20');  -- Start trailing after 20 pts
INSERT OR IGNORE INTO strategy_params (k, v) VALUES ('TRAILING_GAP', '15');      -- Maintain 15 pts gap

-- System Defaults
INSERT OR IGNORE INTO settings (k, v, updated_at) VALUES ('BOT_MODE', 'paper', CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO settings (k, v, updated_at) VALUES ('PAUSED', '0', CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO settings (k, v, updated_at) VALUES ('KILLED', '0', CURRENT_TIMESTAMP);