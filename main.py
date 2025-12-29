import time
import sys
import logging
import signal
import warnings

# --- Fix Warnings ---
# Suppress annoying deprecation warnings from libraries
warnings.filterwarnings("ignore", category=UserWarning, module="pkg_resources")
warnings.filterwarnings("ignore", category=UserWarning, module="telegram.utils.request")

from logging.handlers import RotatingFileHandler
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

# Local Imports
import config
from infra import db
from core.context import TradingContext
from tg_bot.controller import TelegramController

# =========================================================
# 📝 LOGGING CONFIGURATION (Self-Cleaning & Windows Fix)
# =========================================================
def setup_logging():
    """
    Sets up a robust logging system with file rotation.
    Fixes Unicode errors on Windows consoles.
    """
    # 1. Force Windows Console to use UTF-8 (Fixes Emoji Crash)
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')

    log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 2. File Handler (Rotating)
    # Added encoding='utf-8' to ensure file logs support emojis too
    file_handler = RotatingFileHandler(
        config.LOG_FILENAME, 
        maxBytes=config.LOG_MAX_BYTES, 
        backupCount=config.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(log_formatter)
    
    # 3. Console Handler (Standard Output)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    
    # 4. Root Logger Setup
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )
    # Silence the scheduler's noisy logs
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

setup_logging()
logger = logging.getLogger("Main")

# =========================================================
# 🚀 MAIN APPLICATION
# =========================================================

def main():
    logger.info("🚀 Nifty Option Bot Starting Up...")
    
    # 1. Database Initialization & Maintenance
    try:
        db.init_db()
        # Auto-Clean old data on startup (Keep DB light)
        db.cleanup_old_logs()
    except Exception as e:
        logger.critical(f"🔥 Database Error: {e}")
        return

    # 2. Context Initialization
    try:
        ctx = TradingContext()
    except Exception as e:
        logger.critical(f"🔥 Context Init Failed: {e}", exc_info=True)
        return

    # 3. Telegram Bot Initialization
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN missing in .env")
        return
        
    try:
        bot_controller = TelegramController(ctx, config.TELEGRAM_BOT_TOKEN)
        bot_controller.start() 
        logger.info("✅ Telegram Bot Online.")
    except Exception as e:
        logger.critical(f"🔥 Telegram Init Failed: {e}")
        return

    # 4. Job Scheduler Setup
    scheduler = BackgroundScheduler(timezone=config.TZ_NAME)
    
    # Job A: Token Reminder (08:30 AM)
    def send_token_reminder():
        ctx.telegram_alert("⏰ **Morning Reminder**\nRun `/set_token` to update your Upstox access token before market opens!")

    scheduler.add_job(
        send_token_reminder,
        'cron',
        day_of_week='mon-fri',
        hour=8,
        minute=30
    )

    # Job B: Daily Summary (10:05 AM)
    scheduler.add_job(
        ctx.strategy.send_daily_summary, 
        'cron', 
        day_of_week='mon-fri', 
        hour=config.DAILY_REPORT_TIME.hour, 
        minute=config.DAILY_REPORT_TIME.minute
    )
    
    scheduler.start()
    logger.info("⏰ Scheduler Started (Token Reminder @ 08:30, Daily Report @ 10:05).")

    # 🆕 NEW: Startup Alert to Admin
    # This confirms the bot successfully restarted after a crash/reboot
    try:
        startup_msg = (
            f"🟢 **System Online**\n"
            f"Mode: `{ctx.mode.upper()}`\n"
            f"Strategy: {config.TARGET_POINTS} Tgt / {config.SL_POINTS} SL"
        )
        ctx.telegram_alert(startup_msg)
    except Exception as e:
        logger.error(f"Failed to send startup alert: {e}")

    # 5. Signal Handling
    def signal_handler(sig, frame):
        logger.info("🛑 Shutdown Signal Received. Cleaning up...")
        scheduler.shutdown()
        ctx.stop()
        bot_controller.updater.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 6. Main Execution Loop
    logger.info(f"🟢 System Ready. Mode: {ctx.mode.upper()}")
    
    while True:
        try:
            ctx.strategy.run_tick()
            time.sleep(1)
            
        except KeyboardInterrupt:
            signal_handler(None, None)
        except Exception as e:
            logger.error(f"⚠️ Main Loop Error: {e}", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    main()