import time
import signal
import sys
import logging
import pytz # Explicit import for scheduler fix
from apscheduler.schedulers.background import BackgroundScheduler

# Local Imports
import config
from infra.lock import acquire_lock, release_lock
from infra.db import init_db
from core.context import BotContext
from core.strategy import NiftyStrategy
from tg_bot.bot import start_telegram_bot

# =========================================================
# 🔧 WINDOWS FIXES
# =========================================================
# Force standard output to handle UTF-8 (Emojis)
# This prevents "UnicodeEncodeError" on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# =========================================================
# 📝 LOGGING SETUP
# =========================================================
# We explicitly set encoding='utf-8' for the file handler
file_handler = logging.FileHandler("bot_activity.log", encoding='utf-8')
console_handler = logging.StreamHandler(sys.stdout)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger("Main")

# Global Shutdown Flag
SHUTDOWN_FLAG = False

def signal_handler(sig, frame):
    """Catches Ctrl+C to shut down gracefully."""
    global SHUTDOWN_FLAG
    logger.info("🛑 Stop Signal Received! Shutting down...")
    SHUTDOWN_FLAG = True

def send_morning_briefing(ctx):
    """Sends a status report at 9:00 AM."""
    if not ctx.is_active(): 
        return

    logger.info("⏰ Sending Morning Briefing...")
    
    # Try to fetch Nifty Spot for context
    spot_price = 0
    if ctx.broker:
        spot_price = ctx.broker.get_ltp("NSE_INDEX|Nifty 50")

    msg = (
        f"🌅 **Morning Briefing**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• Status: {'✅ Ready' if not ctx.paused else '⏸ Paused'}\n"
        f"• Mode: `{ctx.mode.upper()}`\n"
        f"• Nifty Spot: `{spot_price}`\n\n"
        f"🧠 **Strategy Plan**\n"
        f"• Target: {ctx.params.get('TARGET_POINTS')} pts\n"
        f"• Entry Trigger: {ctx.params.get('TARGET_PREMIUM')}\n"
        f"• Waiting for 9:30 AM Breakout..."
    )
    ctx.telegram_alert(msg)

def main():
    # 1. Acquire Single-Instance Lock
    try:
        acquire_lock()
    except RuntimeError:
        sys.exit(1) # Exit quietly if already running

    logger.info("--- 🚀 Nifty Option Bot Starting ---")

    # 2. Initialize Database (The Brain)
    logger.info("🧠 Initializing Database & Memory...")
    init_db()

    # 3. Load Runtime Context (The System State)
    context = BotContext()

    # 4. Start Telegram Service (Background Thread)
    tg_controller = start_telegram_bot(context)
    
    if not tg_controller:
        logger.warning("⚠️ Telegram Service failed to start. Running in Headless Mode.")

    # 5. Initialize Strategy Engine
    logger.info("📈 Initializing Strategy Engine...")
    strategy = NiftyStrategy(context)

    # 6. Schedule Morning Briefing (9:00 AM Mon-Fri)
    # FIX: Explicitly pass the pytz timezone object to avoid Windows TypeError
    tz = pytz.timezone(config.TZ_NAME) 
    scheduler = BackgroundScheduler(timezone=tz)
    
    scheduler.add_job(
        lambda: send_morning_briefing(context), 
        'cron', 
        hour=9, 
        minute=0, 
        day_of_week='mon-fri'
    )
    scheduler.start()
    logger.info(f"⏰ Scheduler Active (Briefing @ 9:00 AM {config.TZ_NAME}).")

    # 7. Register Signal Handlers (Ctrl+C)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 8. Main Execution Loop
    logger.info("✅ System Online. Entering Main Loop.")
    
    while not SHUTDOWN_FLAG:
        try:
            # The Heartbeat: Checks market every second
            strategy.run_tick()
            
            # Sleep to prevent high CPU usage
            time.sleep(1)

        except KeyboardInterrupt:
            break # Handle manual stop
        except Exception as e:
            logger.error(f"💥 Main Loop Error: {e}")
            time.sleep(5) # Cooldown before retrying

    # 9. Cleanup & Exit
    logger.info("👋 Exiting...")
    if scheduler.running:
        scheduler.shutdown()
    
    release_lock()
    logger.info("--- Bot Stopped ---")

if __name__ == "__main__":
    main()