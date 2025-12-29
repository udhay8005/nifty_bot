import time
import logging
from datetime import datetime, timedelta
import pytz

# Import Configuration & Infrastructure
import config
from infra.db import log_trade, log_audit, get_db, get_weekly_pnl, get_todays_pnl_summary

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
        self.entry_locked = False   # 🔒 RACE CONDITION LOCK
        self.risk_free_done = False # Tracks if we moved SL to Cost
        
        # Holiday Mode
        self.is_holiday = False
        self.holiday_checked = False

        # On startup: Check if we already traded today (Crash Recovery)
        from core.reconciliation import Reconciler
        self.recon = Reconciler(context)
        self.recon.sync_at_startup(self)

    def _check_holiday_status(self):
        """
        Checks if today is a trading holiday using the Broker API.
        If yes, puts the bot to sleep.
        """
        if self.holiday_checked: return

        try:
            broker = self.ctx.broker
            if not broker: return

            today_str = datetime.now(self.tz).strftime('%Y-%m-%d')
            
            # Fetch from Broker
            holidays = broker.get_holidays()
            
            if today_str in holidays:
                self.is_holiday = True
                msg = f"📅 **Holiday Detected**: {today_str}\nMarket is Closed. Bot Sleeping... 💤"
                logger.info(msg)
                # Send alert immediately
                self.ctx.telegram_alert(msg)
            
            self.holiday_checked = True
                
        except Exception as e:
            logger.error(f"Holiday check failed: {e}")

    def is_market_open(self):
        """Checks strict trading window."""
        now = datetime.now(self.tz)
        if now.weekday() >= 5: return False # Sat/Sun
        return config.MARKET_START_TIME <= now.time() <= config.MARKET_END_TIME

    def run_tick(self):
        """The heartbeat method called every 1 second by main.py"""
        
        # 0. Initial Holiday Check
        if not self.holiday_checked:
            self._check_holiday_status()

        # 1. Holiday / Safety Check
        if self.is_holiday: 
            return # Do absolutely nothing
            
        if not self.ctx.is_active():
            return

        now_dt = datetime.now(self.tz)
        now = now_dt.time()

        # 2. PRIORITY: Manage Active Trade (Exit/Trail/Failsafe)
        if self.active_position:
            self._manage_active_trade(now, now_dt)
            return

        # 3. RULE: Max 1 Trade Per Day (Strict)
        # If we already traded or locked an entry, do nothing.
        if self.trade_taken_today or self.entry_locked:
            return 

        # Phase A: Observation (9:25 - 9:30)
        # Scan for strikes closest to Target Premium (180)
        if config.OBSERVATION_START_TIME <= now < config.ENTRY_START_TIME:
            if not self.strikes_selected:
                self._select_strikes()
            return

        # Phase B: Entry Window (9:30 - 9:35)
        # STRICT: We only enter in these 5 minutes.
        if config.ENTRY_START_TIME <= now < config.ENTRY_END_TIME:
            if not self.strikes_selected:
                self._select_strikes() # Retry selection if missed
            
            if self.strikes_selected:
                self._check_entry_signal()

    def _select_strikes(self):
        """Fetches Option Chain and finds CE/PE trading closest to 180."""
        try:
            broker = self.ctx.broker
            if not broker: return

            target_premium = float(self.ctx.params.get('TARGET_PREMIUM', config.TARGET_PREMIUM))

            # 1. Get Nifty Spot Price
            spot_ltp = broker.get_ltp("NSE_INDEX|Nifty 50") 
            if not spot_ltp: 
                logger.warning("Could not fetch Nifty Spot. Retrying...")
                return
            
            # 2. Get Option Chain
            chain_data = broker.get_option_chain_quotes(config.SYMBOL, spot_ltp)
            
            if not chain_data['CE'] or not chain_data['PE']:
                return

            # 3. Find Best Matches (Closest to 180)
            best_ce = min(chain_data['CE'], key=lambda x: abs(x['ltp'] - target_premium))
            best_pe = min(chain_data['PE'], key=lambda x: abs(x['ltp'] - target_premium))
            
            self.selected_strikes = {
                'CE': {'key': best_ce['instrument_key'], 'ltp': best_ce['ltp'], 'strike': best_ce.get('strike')},
                'PE': {'key': best_pe['instrument_key'], 'ltp': best_pe['ltp'], 'strike': best_pe.get('strike')}
            }
            self.strikes_selected = True
            
            # 4. Explicit Log & Alert
            msg = (f"🧐 **Watchlist Selected**\n"
                   f"CE: {best_ce['strike']} @ {best_ce['ltp']}\n"
                   f"PE: {best_pe['strike']} @ {best_pe['ltp']}")
            
            logger.info(f"Watchlist: CE {best_ce['strike']} ({best_ce['ltp']}) | PE {best_pe['strike']} ({best_pe['ltp']})")
            self.ctx.telegram_alert(msg)

        except Exception as e:
            logger.error(f"Strike Selection Failed: {e}")

    def _check_entry_signal(self):
        """Checks if selected strike crosses Trigger Price with Sustain Logic."""
        # Double check lock to prevent race condition re-entry
        if self.entry_locked: return 

        # ==========================================
        # 🛡️ GLOBAL KILL SWITCH (Weekly Max Loss)
        # ==========================================
        # If we have lost too much this week, do not trade.
        weekly_pnl = get_weekly_pnl()
        max_loss = config.WEEKLY_MAX_LOSS # e.g. 10000
        
        # max_loss is usually positive in config (10000), so we check if pnl < -10000
        if weekly_pnl < -abs(max_loss):
            logger.warning(f"⛔ Weekly Loss Limit Hit (PnL: {weekly_pnl}). Entry Blocked.")
            # We lock entry to stop checking for the rest of the day
            self.entry_locked = True
            self.ctx.telegram_alert(f"⛔ **Weekly Max Loss Hit**\nCurrent PnL: ₹{weekly_pnl:.2f}\nTrading Disabled until Monday.")
            return

        broker = self.ctx.broker
        trigger_price = float(self.ctx.params.get('TARGET_PREMIUM', config.TARGET_PREMIUM))
        
        # Check both legs
        legs = ['CE', 'PE']
        
        for type_ in legs:
            if self.entry_locked: break

            data = self.selected_strikes.get(type_)
            if not data: continue
            
            instrument_key = data['key']
            
            # 1. Initial Check
            ltp = broker.get_ltp(instrument_key)
            if not ltp: continue
            
            if ltp > trigger_price:
                # ==========================================
                # ⏳ SUSTAIN LOGIC (Wick Trap Protection)
                # ==========================================
                logger.info(f"⚠️ Potential Breakout on {type_} @ {ltp}. Verifying sustain (5s)...")
                
                # Blocking wait is acceptable here as we are in the critical entry window
                time.sleep(5) 
                
                # Re-fetch Price
                verified_ltp = broker.get_ltp(instrument_key)
                
                if verified_ltp > trigger_price:
                    # ==========================================
                    # 🔒 RACE CONDITION LOCK
                    # ==========================================
                    if self.entry_locked:
                        logger.info(f"🚫 Race Condition: Other leg entered first. Skipping {type_}.")
                        break
                        
                    self.entry_locked = True # LOCK IMMEDIATELY
                    logger.info(f"🚀 Sustain Verified! {type_} @ {verified_ltp} > {trigger_price}")
                    self._execute_trade(instrument_key, verified_ltp, type_)
                    return

    def _execute_trade(self, instrument_key, entry_price, type_):
        broker = self.ctx.broker
        
        lot_size = int(self.ctx.params.get('LOT_SIZE', config.LOT_SIZE))
        sl_pts = float(self.ctx.params.get('SL_POINTS', config.SL_POINTS))
        tgt_pts = float(self.ctx.params.get('TARGET_POINTS', config.TARGET_POINTS))
        
        sl_price = entry_price - sl_pts
        target_price = entry_price + tgt_pts
        
        # 🛡️ SLIPPAGE PROTECTION (Marketable Limit Order)
        # We place a LIMIT order slightly above LTP (e.g., +5 pts).
        # This guarantees a fill (like Market) but protects against a freak 50-pt spike.
        buffer_points = 5.0
        limit_entry_price = entry_price + buffer_points

        try:
            # 1. Place Entry Order (LIMIT with Buffer)
            logger.info(f"🚀 Placing LIMIT Buy @ {limit_entry_price} (LTP: {entry_price})")
            
            # NOTE: We must ensure place_order in upstox_client accepts 'price' for LIMIT orders
            entry_order_id = broker.place_order(
                instrument_key, 
                "BUY", 
                quantity=lot_size, 
                order_type="LIMIT",  # Changed from config.ORDER_TYPE_ENTRY (MARKET)
                price=limit_entry_price
            )
            
            if entry_order_id:
                self.trade_taken_today = True
                self.active_position = {
                    'key': instrument_key,
                    'type': type_,
                    'entry_price': entry_price, # We track actual LTP at trigger as entry
                    'quantity': lot_size,
                    'sl': sl_price,
                    'target': target_price,
                    'sl_order_id': None,
                    'start_time': datetime.now(self.tz)
                }
                
                # 2. Place System SL Order Immediately (SL-M)
                # SL-M orders ensure we exit regardless of volatility
                sl_order_id = broker.place_order(
                    instrument_key,
                    "SELL",
                    quantity=lot_size,
                    order_type=config.ORDER_TYPE_SL,
                    trigger_price=sl_price
                )
                self.active_position['sl_order_id'] = sl_order_id
                
                msg = (f"🚀 **Entry Triggered**\n"
                       f"Strike: {type_} broke {config.TARGET_PREMIUM}!\n"
                       f"Entry (Limit): {limit_entry_price}\n"
                       f"SL: {sl_price} | Tgt: {target_price}")
                self.ctx.telegram_alert(msg)
                log_audit('SYSTEM', 'TRADE_ENTRY', f"{type_} @ {entry_price}")

        except Exception as e:
            logger.error(f"Trade Execution Failed: {e}")
            self.ctx.telegram_alert(f"❌ Execution Failed: {e}")
            self.entry_locked = False # Unlock if execution failed

    def _manage_active_trade(self, current_time, current_dt):
        """Manages Exits, Risk-Free Moves, and Trailing."""
        if not self.active_position: return
        
        broker = self.ctx.broker
        key = self.active_position['key']
        ltp = broker.get_ltp(key)
        
        if not ltp: return

        target = self.active_position['target']
        entry = self.active_position['entry_price']
        current_sl = self.active_position['sl']
        
        # ==========================================
        # 🛡️ 1. SAFETY WATCHDOG (Fail-Safe Exit)
        # ==========================================
        # If SL-M failed to trigger and price crashed
        if ltp < (current_sl - 2.0):
            logger.warning(f"🚨 CRITICAL: Price ({ltp}) dropped below SL ({current_sl}). Force Exiting!")
            self._close_position("🚨 FAILSAFE: Manual Force Exit")
            return

        # ==========================================
        # 🎯 2. TARGET EXIT (Manual Execution)
        # ==========================================
        if ltp >= target:
            self._close_position("Target Hit 🎯")
            return

        # ==========================================
        # 🕙 3. HARD TIME EXIT (10:00 AM STRICT)
        # ==========================================
        if current_time >= config.SQUARE_OFF_TIME:
            self._close_position("Time Exit (10:00 AM) 🕙")
            return

        # ==========================================
        # 🆓 4. RISK-FREE MOVE (At +20 pts)
        # ==========================================
        # If Price has moved 20 points in our favor, move SL to Cost.
        if not self.risk_free_done:
            if ltp >= (entry + 20.0):
                if current_sl < entry:
                    if self._update_sl(entry):
                        self.risk_free_done = True
                        self.ctx.telegram_alert(f"🛡️ **Risk Free:** Price hit {ltp}. SL moved to Entry ({entry}).")

        # ==========================================
        # 🕯️ 5. CANDLE-BASED TRAILING (After 9:45)
        # ==========================================
        if current_time >= config.TRAIL_ACTIVATION_TIME:
            try:
                # Fetch last 2 candles to ensure we get the completed one
                candles = broker.get_historical_candles(key, '5minute', 2) 
                if candles and len(candles) > 0:
                    last_candle = candles[-1]
                    candle_low = float(last_candle.get('low', 0.0))
                    
                    # Only trail UPWARDS
                    if candle_low > current_sl and candle_low < ltp:
                        logger.info(f"🕯️ Candle Trailing: Moving SL to {candle_low}")
                        if self._update_sl(candle_low):
                            self.ctx.telegram_alert(f"📉 **Trailing:** SL moved to {candle_low} (Candle Low)")
            
            except Exception as e:
                # Prevent log spamming
                if not getattr(self, '_candle_error_logged', False):
                    logger.warning(f"Trailing Error (Candle Fetch): {e}")
                    self._candle_error_logged = True

    def _update_sl(self, new_price):
        """Helper to modify SL order on Broker."""
        broker = self.ctx.broker
        order_id = self.active_position['sl_order_id']
        
        if not order_id: return False

        if broker.modify_order(order_id, trigger_price=new_price):
            self.active_position['sl'] = new_price
            return True
        return False

    def _close_position(self, reason):
        """Exits position at market and cancels SL."""
        broker = self.ctx.broker
        try:
            # 1. Cancel Pending SL Order
            if self.active_position.get('sl_order_id'):
                broker.cancel_order(self.active_position['sl_order_id'])
            
            # 2. Exit Market Immediately
            broker.place_order(
                self.active_position['key'],
                "SELL",
                quantity=self.active_position['quantity'],
                order_type="MARKET"
            )
            
            # 3. Log Result
            exit_price = broker.get_ltp(self.active_position['key']) or 0.0
            entry_price = self.active_position['entry_price']
            qty = self.active_position['quantity']
            pnl = (exit_price - entry_price) * qty
            status = 'WIN' if pnl > 0 else 'LOSS'
            
            log_trade({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'mode': self.ctx.mode.upper(),
                'symbol': self.active_position['key'],
                'side': self.active_position['type'],
                'entry_time': self.active_position.get('start_time', datetime.now()).strftime('%H:%M:%S'),
                'entry_price': entry_price,
                'exit_time': datetime.now().strftime('%H:%M:%S'),
                'exit_price': exit_price,
                'quantity': qty,
                'pnl': round(pnl, 2),
                'status': status
            })

            self.ctx.telegram_alert(f"🏁 **Position Closed**\nReason: {reason}\nPnL: ₹{pnl:.2f} ({status})")
            self.active_position = None
            self.entry_locked = True # Ensure no more trades today
            
        except Exception as e:
            logger.error(f"Exit Failed: {e}")

    # =========================================================
    # 📝 REPORTING (10:05 AM Job)
    # =========================================================
    
    def send_daily_summary(self):
        """
        Fetches today's trade stats and sends a summary message.
        Called by Scheduler at 10:05 AM.
        """
        try:
            stats = get_todays_pnl_summary()
            count = stats.get('count', 0)
            pnl = stats.get('pnl', 0.0)
            
            status = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "NEUTRAL")
            if count == 0:
                msg = "📅 **Day Summary**\nNo trades taken today."
            else:
                icon = "🟢" if pnl > 0 else "🔴"
                msg = (f"📅 **Day Summary**\n"
                       f"Trades: {count} | Status: {status}\n"
                       f"{icon} PnL: ₹{pnl:.2f}\n"
                       f"Bot is Done for the Day. ✅")
            
            self.ctx.telegram_alert(msg)
            
        except Exception as e:
            logger.error(f"Daily Summary Failed: {e}")