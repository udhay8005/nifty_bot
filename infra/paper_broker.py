import logging
import uuid
from datetime import datetime

logger = logging.getLogger("PaperBroker")

class PaperBroker:
    def __init__(self, real_broker):
        """
        The PaperBroker acts as a simulator.
        
        :param real_broker: Instance of UpstoxClient connected to the REAL API.
                            Used ONLY for fetching data (LTP, Chains, Candles).
        """
        self.real_broker = real_broker
        self.orders = {}   # Simulates the Exchange Order Book
        self.positions = [] # Simulates the Portfolio
        
        if not self.real_broker:
            logger.warning("⚠️ PaperBroker running in BLIND MODE (No Data Feed). Strategy will not function correctly.")
        else:
            logger.info("🧪 PaperBroker initialized. Using Real Broker for Market Data.")

    # =========================================================
    # 1. DATA FEED (Proxy to Real Broker)
    # =========================================================
    
    def get_ltp(self, instrument_key):
        """Fetches the REAL LIVE PRICE from Upstox."""
        if self.real_broker:
            return self.real_broker.get_ltp(instrument_key)
        return 180.0 # Dummy fallback for testing offline

    def get_option_chain_quotes(self, symbol, spot_price):
        """Fetches REAL OPTION CHAIN for strike selection."""
        if self.real_broker:
            return self.real_broker.get_option_chain_quotes(symbol, spot_price)
        return {'CE': [], 'PE': []}

    def get_profile(self):
        """Pass-through to Real Broker for /profile command."""
        if self.real_broker and hasattr(self.real_broker, 'get_profile'):
            return self.real_broker.get_profile()
        return {'name': 'Paper User', 'funds': 100000.0}

    def get_historical_candles(self, instrument_key, interval_str, limit=3):
        """Pass-through to Real Broker for Candle-Based Trailing."""
        if self.real_broker and hasattr(self.real_broker, 'get_historical_candles'):
            return self.real_broker.get_historical_candles(instrument_key, interval_str, limit)
        return []
    
    def get_holidays(self):
        """Pass-through for Holiday Checks."""
        if self.real_broker and hasattr(self.real_broker, 'get_holidays'):
            return self.real_broker.get_holidays()
        return []

    def restart_websocket(self):
        """Restarts the real data feed if needed."""
        if self.real_broker: 
            self.real_broker.restart_websocket()

    # =========================================================
    # 2. EXECUTION ENGINE (The Simulation)
    # =========================================================

    def place_order(self, instrument_key, transaction_type, quantity, order_type, trigger_price=0.0, price=0.0):
        """
        Simulates placing an order.
        NOTE: Added 'price' parameter to support Limit Orders.
        """
        # 1. Generate a realistic Order ID
        order_id = f"PAPER_{uuid.uuid4().hex[:8].upper()}"
        
        # 2. Get Live Price for realistic simulation
        ltp = self.get_ltp(instrument_key) or 0.0
        if ltp == 0.0: ltp = 180.0 # Fallback
        
        # 3. Simulate Fill Logic
        status = 'trigger pending'
        average_price = 0.0
        
        # MARKET ORDER: Fills immediately at LTP
        if order_type == 'MARKET':
            status = 'complete'
            average_price = ltp
            self._update_internal_position(instrument_key, transaction_type, quantity, ltp)

        # LIMIT ORDER: 
        # For BUY: If Limit Price >= LTP, it fills immediately (Marketable Limit).
        # For SELL: If Limit Price <= LTP, it fills immediately.
        elif order_type == 'LIMIT':
            limit_price = float(price)
            if transaction_type == 'BUY' and limit_price >= ltp:
                status = 'complete'
                average_price = ltp # You get filled at Market Price, not Limit Price (better fill)
                self._update_internal_position(instrument_key, transaction_type, quantity, ltp)
            elif transaction_type == 'SELL' and limit_price <= ltp:
                status = 'complete'
                average_price = ltp
                self._update_internal_position(instrument_key, transaction_type, quantity, ltp)
            else:
                status = 'open' # Passive Limit Order (Not used in this strategy but good for logic)

        # SL-M ORDER: Sits as pending until trigger is hit
        elif order_type == 'SL-M':
            status = 'trigger pending'

        # 4. Log the "Trade"
        logger.info(f"📝 PAPER ORDER: {transaction_type} {quantity} {instrument_key} | Type: {order_type} | Limit: {price} | Status: {status}")
        if status == 'complete':
             logger.info(f"   -> Filled @ {average_price}")

        # 5. Store in Memory
        self.orders[order_id] = {
            'order_id': order_id,
            'instrument_token': instrument_key,
            'transaction_type': transaction_type,
            'quantity': quantity,
            'order_type': order_type,
            'status': status,
            'average_price': average_price,
            'trigger_price': trigger_price,
            'price': price,
            'order_timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        return order_id

    def modify_order(self, order_id, trigger_price):
        """Simulates modifying an order (e.g., Trailing Stop Loss)."""
        if order_id in self.orders:
            old_trigger = self.orders[order_id].get('trigger_price')
            self.orders[order_id]['trigger_price'] = trigger_price
            
            logger.info(f"📝 PAPER MODIFY: Order {order_id} Trigger {old_trigger} -> {trigger_price}")
            return True
        
        logger.warning(f"❌ Paper Modify Failed: Order {order_id} not found.")
        return False

    def cancel_order(self, order_id):
        """Simulates cancelling an order."""
        if order_id in self.orders:
            self.orders[order_id]['status'] = 'cancelled'
            logger.info(f"📝 PAPER CANCEL: Order {order_id}")
            return True
        return False

    def cancel_all_orders(self):
        """Cancels all pending orders (used by Kill Switch)."""
        count = 0
        for oid in self.orders:
            if self.orders[oid]['status'] == 'trigger pending':
                self.orders[oid]['status'] = 'cancelled'
                count += 1
        if count > 0:
            logger.info(f"📝 PAPER: Cancelled {count} pending orders.")

    def close_all_positions(self):
        """Simulates closing all positions."""
        logger.info("📝 PAPER: Closing all simulated positions.")
        self.positions = []

    # =========================================================
    # 3. COMPATIBILITY LAYER (Mimic Upstox Response Formats)
    # =========================================================

    def get_todays_orders(self):
        return list(self.orders.values())
        
    def get_open_orders(self):
        """Returns list of dictionaries mimicking Upstox Order objects"""
        return [o for o in self.orders.values() if o['status'] == 'trigger pending']

    def get_positions(self):
        return self.positions

    def _update_internal_position(self, key, type_, qty, price):
        """Simple internal ledger to track net positions."""
        net_qty = qty if type_ == 'BUY' else -qty
        
        existing = next((p for p in self.positions if p['instrument_token'] == key), None)
        
        if existing:
            old_qty = int(existing['quantity'])
            new_qty = old_qty + net_qty
            existing['quantity'] = new_qty
            if new_qty != 0: existing['average_price'] = price
        else:
            self.positions.append({
                'instrument_token': key,
                'quantity': net_qty,
                'average_price': price,
                'product': 'I'
            })