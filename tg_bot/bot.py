import threading
import logging
import config
from tg_bot.controller import TelegramController

logger = logging.getLogger("TelegramService")

def start_telegram_bot(context):
    """
    Initializes and starts the Telegram Bot in a background thread.
    
    :param context: The BotContext object (The 'Brain' & 'Memory')
    :return: The controller instance if successful, else None.
    """
    
    # 1. Validate Configuration
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        logger.critical("❌ TELEGRAM_BOT_TOKEN is missing in .env file.")
        logger.warning("⚠️ The bot will run in HEADLESS MODE (No Telegram control).")
        return None

    try:
        # 2. Initialize the Controller
        # The controller sets up all the command handlers (/start, /mode, /set_strategy, etc.)
        controller = TelegramController(context, token)
        
        # 3. Define the Thread Runner
        def run_bot_thread():
            try:
                # This calls updater.start_polling() inside the controller
                # It runs in a loop inside this thread.
                controller.start() 
            except Exception as e:
                logger.error(f"💥 Telegram Thread Crashed: {e}")

        # 4. Start the Background Thread
        # daemon=True means this thread automatically dies when main.py stops.
        bot_thread = threading.Thread(target=run_bot_thread, name="TelegramThread", daemon=True)
        bot_thread.start()
        
        logger.info("🚀 Telegram Service Started (Background Mode).")
        return controller

    except Exception as e:
        logger.error(f"❌ Failed to start Telegram Service: {e}")
        return None