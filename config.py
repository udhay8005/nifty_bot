import os
import sys
from datetime import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =========================================================
# 🔐 SECURITY & INFRASTRUCTURE
# =========================================================
# Encryption key for securing your Upstox Access Token
FERNET_KEY = os.getenv('FERNET_KEY')

# Database File Path
DB_PATH = os.getenv('DB_PATH', 'nifty_bot.db')

# Timezone (Critical for strict timing)
TZ_NAME = os.getenv('TZ', 'Asia/Kolkata')

# Telegram Admin IDs (Comma separated in .env -> List of Ints)
_admin_ids = os.getenv('BOT_ADMIN_CHAT_IDS', '')
try:
    ADMIN_CHAT_IDS = [int(x.strip()) for x in _admin_ids.split(',') if x.strip()]
except ValueError:
    print("⚠️ Error: BOT_ADMIN_CHAT_IDS in .env must be a comma-separated list of numbers.")
    ADMIN_CHAT_IDS = []

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Upstox API Endpoint
UPSTOX_API_BASE = 'https://api.upstox.com/v2'

# =========================================================
# ⚙️ SYSTEM CONSTANTS (Immutable)
# =========================================================
SYMBOL = "NIFTY"
EXCHANGE = "NSE_FO"
PRODUCT_TYPE = "I"        # Intraday
ORDER_TYPE_ENTRY = "MARKET"
ORDER_TYPE_SL = "SL-M"    # Stop Loss Market

# =========================================================
# 🕒 STRICT TRADING WINDOWS (The "Video Strategy" Timeline)
# =========================================================
# 1. Market Open
MARKET_START_TIME = time(9, 15)

# 2. Phase A: Observation (Scan for Premium ~180)
OBSERVATION_START_TIME = time(9, 25)

# 3. Phase B: Entry Window (Strict 2-minute breakout zone)
ENTRY_START_TIME = time(9, 30)
ENTRY_END_TIME = time(9, 32)

# 4. Phase C: Trailing Activation (Move SL to Cost)
TRAIL_ACTIVATION_TIME = time(9, 45)

# 5. Phase D: Hard Time Exit (Theta Decay Protection)
SQUARE_OFF_TIME = time(10, 0)

# 6. Market Close
MARKET_END_TIME = time(15, 30)

# =========================================================
# 🧠 STRATEGY DEFAULTS (Fallback Values)
# =========================================================
# NOTE: These values are used ONLY if the Database is empty.
# Once the bot runs, the "Brain" (DB) overrides these.
# You should change these settings via Telegram commands:
# /set_strategy <Target> <SL> <Qty>
# /set_trigger <Price>

TARGET_PREMIUM = 180.0   # The Breakout Trigger Price
TARGET_POINTS = 40.0     # Target Profit Points
SL_POINTS = 20.0         # Initial Stop Loss Points
LOT_SIZE = 50            # Quantity (1 Lot)

# Trailing Configuration
TRAILING_ON = True       # Master switch for trailing logic
TRAILING_TRIGGER = 20.0  # Points profit needed to start trailing
TRAILING_GAP = 15.0      # Distance to keep SL behind price