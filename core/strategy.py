import time
import logging
from datetime import datetime
import pytz

# Import Configuration & Infrastructure
import config
from infra.db import log_trade, log_audit, get_db

logger = logging.getLogger("Strategy")

class NiftyStrategy:
    def __init__(self, context):
        self.ctx = context
        self.tz = pytz.timezone(config.TZ_NAME)
        
        # Strategy State
        self.selected_strikes = {}  # {'CE': {'key':..., 'ltp':...}, 'PE': ...}
        self.active_position = None # Current open trade details
        self.trade_taken_today = False
        
        # Logic Flags
        self.strikes_selected = False
        self.trailing_activated = False

        # On startup: Check if we already traded today (Crash Recovery)
        # This prevents taking a second trade if the bot restarts at 9:40 AM
        from core.reconciliation import Reconciler
        self.recon = Reconciler(context)
        self.recon.sync_at_startup(self)

    def is_market_open(self):
        """Checks strict trading window."""
        now = datetime.now(self.tz)
        if now.weekday() >= 5: return False # Sat/Sun
        return config.MARKET_START_TIME <= now.time() <= config.MARKET_END_TIME

    def run_tick(self):
        """The heartbeat method called every 1 second by main.py"""
        
        # 1. Safety Check: If Killed/Paused, do nothing
        if not self.ctx.is_active():
            return

        now = datetime.now(self.tz).time()

        # 2. PRIORITY: Manage Active Trade (Exit/Trail/Failsafe)
        if self.active_position:
            self._manage_active_trade(now)
            return

        # 3. RULE: Max 1 Trade Per Day (Strict)
        if self.trade_taken_today:
            return 

        # Phase A: Observation (9:25 - 9:30)
        # Scan for strikes closest to our Target Premium (e.g. 180)
        if config.OBSERVATION_START_TIME <= now < config.ENTRY_START_TIME:
            if not self.strikes_selected:
                self._select_strikes()
            return

        # Phase B: Entry Window (9:30 - 9:32)
        # STRICT: We only enter in these 2 minutes to catch the breakout.
        # If no signal by 9:32, we do not trade today.
        if config.ENTRY_START_TIME <= now < config.ENTRY_END_TIME:
            if not self.strikes_selected:
                self._select_strikes() # Retry selection if missed
            
            if self.strikes_selected:
                self._check_entry_signal(now)

    def _select_strikes(self):
        """Fetches Option Chain and finds CE/PE trading closest to Target Premium."""
        try:
            broker = self.ctx.broker
            if not broker: return

            # Get Target Premium from DB (Brain) or Default
            target_premium = float(self.ctx.params.get('TARGET_PREMIUM', config.TARGET_PREMIUM))

            logger.info(f"Scanning Option Chain for premiums ~{target_premium}...")
            
            # 1. Get Nifty Spot Price
            spot_ltp = broker.get_ltp("NSE_INDEX|Nifty 50") 
            if not spot_ltp: 
                logger.warning("Could not fetch Nifty Spot. Retrying...")
                return
            
            # 2. Get Option Chain (Uses intelligent fetch in UpstoxClient)
            chain_data = broker.get_option_chain_quotes(config.SYMBOL, spot_ltp)
            
            if not chain_data['CE'] or not chain_data['PE']:
                return

            # 3. Find Best Matches (Closest to 180 or target)
            best_ce = min(chain_data['CE'], key=lambda x: abs(x['ltp'] - target_premium))
            best_pe = min(chain_data['PE'], key=lambda x: abs(x['ltp'] - target_premium))
            
            self.selected_strikes = {
                'CE': {'key': best_ce['instrument_key'], 'ltp': best_ce['ltp']},
                'PE': {'key': best_pe['instrument_key'], 'ltp': best_pe['ltp']}
            }
            self.strikes_selected = True
            
            logger.info(f"Strikes Selected: CE={best_ce['ltp']} | PE={best_pe['ltp']}")
            self.ctx.telegram_alert(f"🧐 **Watchlist Set**\nCE: {best_ce['ltp']}\nPE: {best_pe['ltp']}")

        except Exception as e:
            logger.error(f"Strike Selection Failed: {e}")

    def _check_entry_signal(self, current_time):
        """Checks if selected strike crosses Trigger Price."""
        broker = self.ctx.broker
        
        # Load Strategy Trigger from Brain
        trigger_price = float(self.ctx.params.get('TARGET_PREMIUM', config.TARGET_PREMIUM))
        tgt_pts = float(self.ctx.params.get('TARGET_POINTS', 40.0))
        
        for type_, data in self.selected_strikes.items():
            instrument_key = data['key']
            
            # Get latest price
            ltp = broker.get_ltp(instrument_key)
            if not ltp: continue
            
            # --- INTELLIGENT ENTRY GUARDS ---
            
            # 1. Breakout Condition (Price crosses Trigger)
            is_breakout = ltp > trigger_price
            
            # 2. Price Cap Guard (Safety against late spikes)
            # If price is already near target (e.g., > 210), risk/reward is bad. Skip.
            safe_cap = trigger_price + (tgt_pts - 10)
            is_price_safe = ltp < safe_cap
            
            if is_breakout and is_price_safe:
                logger.info(f"🚀 Entry Signal: {type_} broke {trigger_price} at {ltp}")
                self._execute_trade(instrument_key, ltp, type_)
                break # Strict: Only one trade per day

    def _execute_trade(self, instrument_key, entry_price, type_):
        broker = self.ctx.broker
        
        # Load Dynamic Parameters from DB (The Brain)
        lot_size = int(self.ctx.params.get('LOT_SIZE', 50))
        sl_pts = float(self.ctx.params.get('SL_POINTS', 20.0))
        tgt_pts = float(self.ctx.params.get('TARGET_POINTS', 40.0))
        
        # Calculate Levels
        sl_price = entry_price - sl_pts
        target_price = entry_price + tgt_pts
        
        try:
            # 1. Place Entry Order (Market)
            entry_order_id = broker.place_order(
                instrument_key, 
                "BUY", 
                quantity=lot_size, 
                order_type=config.ORDER_TYPE_ENTRY
            )
            
            if entry_order_id:
                self.trade_taken_today = True
                self.active_position = {
                    'key': instrument_key,
                    'type': type_,
                    'entry_price': entry_price,
                    'quantity': lot_size,
                    'sl': sl_price,
                    'target': target_price,
                    'sl_order_id': None
                }
                
                # 2. Place System SL Order Immediately (SL-M)
                sl_order_id = broker.place_order(
                    instrument_key,
                    "SELL",
                    quantity=lot_size,
                    order_type=config.ORDER_TYPE_SL,
                    trigger_price=sl_price
                )
                self.active_position['sl_order_id'] = sl_order_id
                
                msg = (f"✅ **Trade Executed**\n"
                       f"Strike: {type_}\nEntry: {entry_price}\n"
                       f"Qty: {lot_size}\n"
                       f"SL: {sl_price} | Tgt: {target_price}")
                self.ctx.telegram_alert(msg)
                log_audit('SYSTEM', 'TRADE_ENTRY', f"{type_} @ {entry_price}")

        except Exception as e:
            logger.error(f"Trade Execution Failed: {e}")
            self.ctx.telegram_alert(f"❌ Execution Failed: {e}")

    def _manage_active_trade(self, current_time):
        if not self.active_position: return
        
        broker = self.ctx.broker
        key = self.active_position['key']
        ltp = broker.get_ltp(key)
        
        if not ltp: return

        # Load Trade Data
        target = self.active_position['target']
        entry = self.active_position['entry_price']
        current_sl = self.active_position['sl']
        
        # Is Trailing Enabled? (Default to True/1)
        trailing_enabled = (self.ctx.params.get('TRAILING_ON', '1') == '1')
        trail_trigger = float(self.ctx.params.get('TRAILING_TRIGGER', 20.0))
        trail_gap = float(self.ctx.params.get('TRAILING_GAP', 15.0))

        # ==========================================
        # 🛡️ 1. SAFETY WATCHDOG (Fail-Safe Exit)
        # ==========================================
        # If price falls BELOW Stop Loss (by 2 pts) and the broker
        # hasn't triggered the exit yet, WE FORCE EXIT MANUALLY.
        # This protects you if the broker freezes or price jumps the SL.
        
        failsafe_limit = current_sl - 2.0 
        if ltp < failsafe_limit:
            logger.warning(f"🚨 CRITICAL: Price ({ltp}) dropped below SL ({current_sl}). Force Exiting!")
            self._close_position("🚨 FAILSAFE: Manual Force Exit")
            return

        # ==========================================
        # 🎯 2. TARGET EXIT (Manual Execution)
        # ==========================================
        # We manually watch LTP. If it hits Target, we send Market Sell.
        # This acts as a "Virtual Limit Order" but guarantees execution.
        if ltp >= target:
            self._close_position("Target Hit 🎯")
            return

        # ==========================================
        # 📉 3. TRAILING LOGIC (The "Video" Strategy)
        # ==========================================
        if trailing_enabled:
            
            # Rule A: Time-Based (9:45 AM) -> Move SL to Cost
            # If trade is profitable at 9:45, eliminate risk.
            if current_time >= config.TRAIL_ACTIVATION_TIME and not self.trailing_activated:
                if ltp > entry:
                    if self._update_sl(entry):
                        self.trailing_activated = True
                        self.ctx.telegram_alert("Update: ⌚ 9:45 Rule - SL Moved to Cost.")

            # Rule B: Price-Based (Dynamic Trailing)
            # If price moves up by Trigger (e.g. 20pts), Trail SL with Gap (e.g. 15pts)
            # This locks in profit as the market rallies.
            profit_points = ltp - entry
            if profit_points >= trail_trigger:
                new_sl = ltp - trail_gap
                
                # Only move SL UP, never down
                if new_sl > current_sl:
                    if self._update_sl(new_sl):
                        logger.info(f"📈 SL Trailed: {current_sl} -> {new_sl} (LTP: {ltp})")

        # ==========================================
        # 🕙 4. TIME EXIT (10:00 AM)
        # ==========================================
        if current_time >= config.SQUARE_OFF_TIME:
            self._close_position("Time Exit 🕙")
            return

    def _update_sl(self, new_price):
        """Helper to modify SL order on Broker."""
        broker = self.ctx.broker
        order_id = self.active_position['sl_order_id']
        
        # Safety: Don't modify if order_id is missing (e.g. restart glitch)
        if not order_id: return False

        if broker.modify_order(order_id, trigger_price=new_price):
            self.active_position['sl'] = new_price
            return True
        return False

    def _close_position(self, reason):
        """Exits position at market and cancels SL."""
        broker = self.ctx.broker
        try:
            # 1. Cancel Pending SL Order (if it exists)
            if self.active_position.get('sl_order_id'):
                broker.cancel_order(self.active_position['sl_order_id'])
            
            # 2. Exit Market Immediately
            broker.place_order(
                self.active_position['key'],
                "SELL",
                quantity=self.active_position['quantity'],
                order_type="MARKET"
            )
            
            # 3. Calculate Final PnL for Records
            # Note: The actual execution price comes from broker, here we use LTP for logging estimate
            exit_price = broker.get_ltp(self.active_position['key']) or 0.0
            entry_price = self.active_position['entry_price']
            qty = self.active_position['quantity']
            pnl = (exit_price - entry_price) * qty
            status = 'WIN' if pnl > 0 else 'LOSS'
            
            # 4. Log to Database
            log_trade({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'mode': self.ctx.mode.upper(),
                'symbol': self.active_position['key'],
                'side': self.active_position['type'],
                'entry_time': '00:00', # Simplified for log
                'entry_price': entry_price,
                'exit_time': datetime.now().strftime('%H:%M:%S'),
                'exit_price': exit_price,
                'quantity': qty,
                'pnl': round(pnl, 2),
                'status': status
            })

            self.ctx.telegram_alert(f"🏁 **Position Closed**\nReason: {reason}\nPnL: ₹{pnl:.2f} ({status})")
            self.active_position = None
            
        except Exception as e:
            logger.error(f"Exit Failed: {e}")