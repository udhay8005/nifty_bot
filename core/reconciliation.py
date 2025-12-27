import logging
from datetime import datetime
from infra.db import get_db

logger = logging.getLogger("Reconciliation")

class Reconciler:
    def __init__(self, context):
        self.ctx = context

    def sync_at_startup(self, strategy):
        """
        Intelligent Sync Sequence:
        1. Memory Check: Did we already finish a trade today? (Checks DB)
           - Enforces '1 Trade Per Day' rule across restarts.
        2. Reality Check: Are we currently in a trade? (Checks Broker)
           - Recovers from crashes/restarts mid-trade.
        """
        if self.ctx.killed:
            logger.warning("Startup Sync Skipped: System is KILLED.")
            return

        logger.info(f"♻️ Starting Reconciliation (Mode: {self.ctx.mode})...")

        # 1. DB CHECK (Persistent Memory)
        self._check_db_history(strategy)

        # 2. BROKER CHECK (Live Market State)
        self._check_live_broker_state(strategy)

    def _check_db_history(self, strategy):
        """Checks the 'trades' table to see if work is already done today."""
        conn = get_db()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            current_mode = self.ctx.mode.upper()
            
            # Count trades for TODAY in CURRENT MODE
            # We assume any recorded trade implies the daily quota is used.
            cursor = conn.execute(
                "SELECT count(*) as cnt FROM trades WHERE date=? AND mode=?", 
                (today, current_mode)
            )
            row = cursor.fetchone()
            count = row['cnt'] if row else 0
            
            if count > 0:
                strategy.trade_taken_today = True
                logger.info(f"✅ DB Memory: Found {count} completed {current_mode} trades today.")
                logger.info("   -> 'Trade Taken' flag set to TRUE. Waiting for next session.")
            else:
                logger.info("ℹ️ DB Memory: No trades recorded today. Fresh start.")
                
        except Exception as e:
            logger.error(f"DB Reconciliation Failed: {e}")
        finally:
            conn.close()

    def _check_live_broker_state(self, strategy):
        """Queries the broker to rebuild active positions if the bot crashed."""
        broker = self.ctx.broker
        if not broker: return

        try:
            # A. CHECK FOR OPEN POSITIONS
            # In Live Mode, this fetches real positions from Upstox.
            # In Paper Mode, this might be empty on restart unless PaperBroker persists state (usually not).
            try:
                positions = broker.get_positions()
            except AttributeError:
                positions = []

            # Filter for Net Quantity != 0 (Open Position)
            open_pos = [p for p in positions if int(p.get('quantity', 0)) != 0]

            if open_pos:
                p = open_pos[0] # Assuming single-leg strategy for simplicity
                qty = int(p.get('quantity'))
                avg_price = float(p.get('average_price', 0.0))
                token = p.get('instrument_token')
                
                logger.warning(f"🚨 ACTIVE POSITION DETECTED! Resuming Management.")
                logger.info(f"   -> Token: {token} | Qty: {qty} | Entry: {avg_price}")

                # RECONSTRUCT STRATEGY STATE
                # We pull default rules from 'Brain' (Context Params) to fill gaps
                # Default to 40 pts target if we can't remember the original
                tgt_pts = float(self.ctx.params.get('TARGET_POINTS', 40))
                
                strategy.active_position = {
                    'key': token,
                    'type': 'UNKNOWN', # Cannot determine CE/PE easily without mapping, but logic still works
                    'entry_price': avg_price,
                    'quantity': qty,
                    'sl': 0.0,       # Will attempt to find actual SL below
                    'target': avg_price + tgt_pts,
                    'sl_order_id': None
                }
                
                # B. FIND ATTACHED STOP LOSS ORDERS
                # We look for pending SELL orders matching our position to regain control of Risk
                try:
                    open_orders = broker.get_open_orders()
                except AttributeError:
                    open_orders = []

                for o in open_orders:
                    # Look for SELL, SL-M/SL orders
                    # 'transaction_type' might be 'SELL' and 'order_type' 'SL-M'
                    if o.get('transaction_type') == 'SELL' and o.get('order_type') in ['SL', 'SL-M']:
                        # Verify it matches our token (Upstox instrument_token)
                        if o.get('instrument_token') == token:
                            strategy.active_position['sl_order_id'] = o.get('order_id')
                            strategy.active_position['sl'] = float(o.get('trigger_price', 0.0))
                            logger.info(f"   ✅ Attached SL Order Found: ID {o.get('order_id')} @ {o.get('trigger_price')}")
                            break 
                
                if not strategy.active_position['sl_order_id']:
                    logger.warning("   ⚠️ No SL Order found for active position! Strategy may exit manually or you should check broker.")

                # Alert Admin
                self.ctx.telegram_alert(
                    f"♻️ **Bot Restarted & Resumed**\n"
                    f"Managed Position:\n"
                    f"Qty: {qty} @ {avg_price}\n"
                    f"SL: {strategy.active_position['sl']}\n"
                    f"Target: {strategy.active_position['target']:.2f}"
                )

        except Exception as e:
            logger.error(f"Broker Reconciliation Failed: {e}")

    def run_check(self, strategy):
        """Optional: Periodic consistency check."""
        pass