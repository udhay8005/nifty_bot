import threading
import logging
from datetime import datetime

# --- Local Imports ---
# We import 'get_all_params' to load the Strategy Rules (The Brain) from DB
# We import 'get_setting' (alias for get_param) and 'set_setting' (alias for set_param)
from infra.db import get_setting, set_setting, log_audit, get_all_params

# Import the Simulation Engine
from infra.paper_broker import PaperBroker

# Import Real Broker (Upstox)
try:
    from infra.upstox_client import UpstoxClient
except ImportError:
    UpstoxClient = None

# Import Strategy (The Brain)
from core.strategy import NiftyStrategy

logger = logging.getLogger("Context")

class TradingContext:
    def __init__(self):
        """
        The Central Hub. Holds state, broker connection, and strategy.
        """
        self.lock = threading.RLock()
        
        # --- System State ---
        self.mode = 'paper'   # 'live' or 'paper'
        self.paused = False
        self.killed = False
        
        # --- The Brain (Strategy Memory) ---
        # Stores dynamic rules: Target, SL, Trailing Settings, Lot Size
        self.params = {} 
        
        # --- Runtime Objects ---
        self.broker = None
        self.strategy = None
        self.kill_confirmations = {} # Stores 4-digit codes for kill command
        
        # --- Communication ---
        self._alert_callback = None
        
        # --- Startup Sequence ---
        self.reload_state()
        
        # Initialize Strategy Logic
        self.strategy = NiftyStrategy(self)

    def set_alert_callback(self, callback_func):
        """Allows Strategy to send Telegram messages via Context."""
        self._alert_callback = callback_func

    def telegram_alert(self, message):
        """Standard way for Strategy/System to notify Admin."""
        if self._alert_callback:
            try:
                self._alert_callback(message)
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")
        else:
            logger.warning(f"🔔 Alert (No Telegram): {message}")

    def reload_state(self):
        """
        Full System Sync: Loads Flags, Brain (Params), and Initializes Broker.
        Called on startup or hard reset.
        """
        with self.lock:
            # 1. Load System Flags from DB
            # Note: We use string '1'/'0' for booleans in DB to keep it simple
            db_mode = get_setting('BOT_MODE')
            self.mode = db_mode if db_mode in ['live', 'paper'] else 'paper'
            self.paused = (get_setting('PAUSED') == '1')
            self.killed = (get_setting('KILLED') == '1')
            
            # 2. Load Strategy Parameters (The Brain)
            # Fetches: TARGET_POINTS, SL_POINTS, LOT_SIZE, TRAILING_ON, etc.
            self.params = get_all_params()
            
            # 3. Initialize the Broker Engine
            self._init_broker()
            
            # Log Status
            status = "KILLED 💀" if self.killed else ("PAUSED ⏸️" if self.paused else "ACTIVE ✅")
            strat_info = f"Strat: Tgt {self.params.get('TARGET_POINTS')} / SL {self.params.get('SL_POINTS')}"
            logger.info(f"Context Loaded: Mode={self.mode.upper()} | Status={status} | {strat_info}")

    def refresh_params(self):
        """
        Lightweight Sync: Called by Telegram (/set_strategy, /set_risk).
        Updates strategy rules instantly without reconnecting the broker.
        """
        with self.lock:
            self.params = get_all_params()
            logger.info("🧠 Strategy Parameters Refreshed via Telegram.")

    def _init_broker(self):
        """
        Intelligent Broker Factory.
        - LIVE MODE: Connects to Upstox for Data & Execution.
        - PAPER MODE: Connects to Upstox for DATA, but uses PaperBroker for EXECUTION.
        """
        # Safety Check: If Killed, do not init broker (prevents accidental trades)
        if self.killed:
            self.broker = None
            return

        # 1. Always attempt to create the Real Broker (We need it for Data Feed!)
        token = get_setting('UPSTOX_ACCESS_TOKEN')
        
        real_broker = None
        if token and UpstoxClient:
            try:
                real_broker = UpstoxClient(access_token=token)
                # logger.info("✅ Upstox Client initialized (Data Feed Ready).")
            except Exception as e:
                logger.error(f"❌ Failed to init Data Broker: {e}")

        # 2. Assign Execution Engine based on Mode
        if self.mode == 'live':
            if real_broker:
                self.broker = real_broker
                logger.info("🚀 EXECUTION MODE: LIVE (Real Money)")
            else:
                logger.warning("⚠️ Live Mode requested but Token missing/invalid. Reverting to Paper.")
                self.mode = 'paper'
                # Pass real_broker (even if None) to PaperBroker so it knows it's blind
                self.broker = PaperBroker(real_broker) 
        else:
            # Paper Mode: Wraps the real broker to get live prices
            self.broker = PaperBroker(real_broker)
            logger.info("🧪 EXECUTION MODE: PAPER (Live Data / Fake Money)")

    # =========================================================
    # Runtime Control & Hot-Swapping
    # =========================================================

    def update_runtime_token(self, new_token):
        """Called by /set_token. Updates DB and hot-swaps the broker session."""
        with self.lock:
            # 1. Save new token
            set_setting('UPSTOX_ACCESS_TOKEN', new_token)
            
            # 2. Hot-Swap
            # If we are live, try to update the existing session to avoid downtime
            if self.mode == 'live' and self.broker and hasattr(self.broker, 'update_access_token'):
                logger.info("Hot-swapping token on existing broker...")
                self.broker.update_access_token(new_token)
            else:
                # Otherwise, Re-initialize (Essential for PaperBroker to pick up new data feed)
                self._init_broker()
            
            logger.info("Token updated successfully.")

    def switch_mode(self, new_mode):
        """Called by /mode. Switches engines instantly."""
        with self.lock:
            if new_mode == 'live':
                token = get_setting('UPSTOX_ACCESS_TOKEN')
                if not token:
                    raise ValueError("Cannot switch to Live: No Access Token found. Use /set_token first.")
            
            self.mode = new_mode
            set_setting('BOT_MODE', new_mode)
            self._init_broker()
            
            # Reset Strategy State on Switch
            if self.strategy:
                self.strategy.active_position = None
                self.strategy.entry_locked = False
                self.strategy.trade_taken_today = False
                logger.info("✨ Strategy State Reset.")
            
            return True

    def toggle_pause(self, should_pause):
        """Called by /pause and /resume."""
        with self.lock:
            val = '1' if should_pause else '0'
            set_setting('PAUSED', val)
            self.paused = should_pause
            status = "PAUSED" if self.paused else "RESUMED"
            logger.info(f"⏯️ System {status}")

    # =========================================================
    # Safety & Emergency Mechanisms
    # =========================================================

    def is_active(self):
        """Master check used by Strategy loop."""
        return (not self.killed) and (not self.paused)

    def get_flags(self):
        """Returns current system health for /status command."""
        return {
            'mode': self.mode,
            'paused': self.paused,
            'killed': self.killed,
            'broker_connected': (self.broker is not None)
        }

    def emergency_kill(self):
        """The Hard Kill Switch. Cancels everything and locks the bot."""
        with self.lock:
            logger.critical("🚨 EMERGENCY KILL INITIATED 🚨")
            set_setting('KILLED', '1')
            self.killed = True
            self.paused = True
            
            if self.broker:
                try:
                    logger.info("Kill: Cancelling all orders...")
                    self.broker.cancel_all_orders()
                except AttributeError: pass
                except Exception as e: logger.error(f"Kill Error: {e}")

                try:
                    logger.info("Kill: Exiting all positions...")
                    self.broker.close_all_positions() 
                except AttributeError: pass
                except Exception as e: logger.error(f"Kill Error: {e}")
            
            self.broker = None
            return True

    def system_reset(self):
        """
        Un-kills the system.
        Used by /system_reset to resume operations after a kill or crash.
        """
        with self.lock:
            logger.info("🔄 System Reset Initiated...")
            set_setting('KILLED', '0')
            self.killed = False
            self.paused = False
            
            # Re-initialize broker to reconnect
            self._init_broker()
            logger.info("✅ System Reset Complete. Bot is Active.")
            
    def stop(self):
        """Clean shutdown called by main.py signal handler."""
        logger.info("Context stopping...")
        # Add any necessary cleanup logic here if needed