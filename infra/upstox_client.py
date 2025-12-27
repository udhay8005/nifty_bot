import logging
import time
import requests
import json
import urllib.parse
from datetime import datetime, date

# Upstox SDK Imports
import upstox_client
from upstox_client.rest import ApiException
from upstox_client.configuration import Configuration
from upstox_client.api_client import ApiClient

import config

logger = logging.getLogger("UpstoxClient")

class UpstoxClient:
    def __init__(self, access_token):
        """
        Initializes the connection to Upstox.
        """
        self.access_token = access_token
        self.api_version = '2.0'
        
        # 1. Setup SDK Configuration
        self.conf = Configuration()
        self.conf.access_token = access_token
        self.api_client = ApiClient(self.conf)
        
        # 2. Initialize API Instances
        self.order_api = upstox_client.OrderApi(self.api_client)
        self.portfolio_api = upstox_client.PortfolioApi(self.api_client)
        self.quote_api = upstox_client.MarketQuoteApi(self.api_client)
        self.history_api = upstox_client.HistoryApi(self.api_client) # Useful for charts if needed
        
        # 3. Intelligent Cache (The Map)
        # Stores: '24100_CE' -> 'NSE_FO|12345'
        self.instrument_cache = {} 
        self.current_expiry = None
        self.contracts_loaded = False
        
        # Auto-load contracts on startup
        self._load_nifty_contracts()

    # =========================================================
    # 🧠 INTELLIGENT DATA FEED (The "Smart" Parts)
    # =========================================================

    def _load_nifty_contracts(self):
        """
        Downloads ALL Nifty Option contracts, filters for THIS WEEK'S expiry,
        and creates a fast lookup map.
        """
        logger.info("⏳ Downloading Nifty Option Chain Map...")
        try:
            url = "https://api.upstox.com/v2/option/contract"
            params = {
                "instrument_key": "NSE_INDEX|Nifty 50",
            }
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}"
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch contracts. HTTP {response.status_code}")
                return

            data = response.json()
            if data.get('status') != 'success':
                logger.error("API Error in fetching contracts.")
                return

            contracts = data.get('data', [])
            if not contracts:
                logger.error("No contracts returned from API.")
                return

            # 1. Find the Nearest Expiry
            # Sort all contracts by expiry date
            today = date.today().isoformat()
            # Filter out past expiries
            future_contracts = [c for c in contracts if c['expiry'] >= today]
            
            if not future_contracts:
                logger.error("No future contracts found.")
                return
            
            # Sort by date (nearest first)
            future_contracts.sort(key=lambda x: x['expiry'])
            self.current_expiry = future_contracts[0]['expiry']
            
            logger.info(f"📅 Current Weekly Expiry: {self.current_expiry}")

            # 2. Build the Cache
            # We only care about contracts for THIS expiry
            count = 0
            self.instrument_cache = {}
            
            for c in future_contracts:
                if c['expiry'] == self.current_expiry:
                    # Key format: "24000_CE" or "24000_PE"
                    # Note: API returns strike_price as float (e.g., 24000.0)
                    strike = int(float(c['strike_price']))
                    key = f"{strike}_{c['instrument_type']}"
                    
                    self.instrument_cache[key] = {
                        'instrument_key': c['instrument_key'],
                        'strike': strike,
                        'type': c['instrument_type'],
                        'lot_size': c.get('minimum_lot_size', 50)
                    }
                    count += 1
            
            self.contracts_loaded = True
            logger.info(f"✅ Cached {count} instruments for {self.current_expiry}.")

        except Exception as e:
            logger.error(f"Critical Error loading contracts: {e}")

    def get_ltp(self, instrument_key):
        """
        Fetches the Last Traded Price for a single key.
        """
        try:
            # Full Market Quote is heavy, use lightweight LTP API if possible.
            # Upstox V2 Quote API: v2/market-quote/ltp
            url = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={urllib.parse.quote(instrument_key)}"
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}"
            }
            resp = requests.get(url, headers=headers, timeout=3)
            data = resp.json()
            
            if data.get('status') == 'success':
                payload = data.get('data', {}).get(instrument_key, {})
                return payload.get('last_price', 0.0)
                
        except Exception: 
            pass # Silent fail to avoid spamming logs
        return 0.0

    def get_option_chain_quotes(self, symbol, spot_price):
        """
        1. Calculates ATM Strike.
        2. Generates a list of strikes +/- 500 points.
        3. Looks up their keys in the cache.
        4. Batch fetches LTP for all of them.
        """
        if not self.contracts_loaded:
            self._load_nifty_contracts()
            if not self.contracts_loaded:
                return {'CE': [], 'PE': []}

        try:
            # 1. Calculate Range
            # Round Spot to nearest 50
            atm_strike = round(spot_price / 50) * 50
            
            # We want to scan a wide range to find the Target Premium (e.g. 180)
            # +/- 600 points covers most volatility
            strikes_to_scan = range(atm_strike - 600, atm_strike + 600, 50)
            
            # 2. Gather Keys
            keys_to_fetch = []
            valid_items = [] # Stores metadata to map back later
            
            for strike in strikes_to_scan:
                ce_key_id = f"{strike}_CE"
                pe_key_id = f"{strike}_PE"
                
                # Check CE
                if ce_key_id in self.instrument_cache:
                    meta = self.instrument_cache[ce_key_id]
                    keys_to_fetch.append(meta['instrument_key'])
                    valid_items.append(meta)
                
                # Check PE
                if pe_key_id in self.instrument_cache:
                    meta = self.instrument_cache[pe_key_id]
                    keys_to_fetch.append(meta['instrument_key'])
                    valid_items.append(meta)
            
            if not keys_to_fetch:
                return {'CE': [], 'PE': []}

            # 3. Batch Fetch Prices (Optimization)
            # Upstox allows up to 100 keys per call
            quotes_map = self.get_batch_ltp(keys_to_fetch)
            
            # 4. Map Prices back to Structure
            ce_list = []
            pe_list = []
            
            for item in valid_items:
                ikey = item['instrument_key']
                if ikey in quotes_map:
                    item_data = item.copy()
                    item_data['ltp'] = quotes_map[ikey]
                    
                    if item['type'] == 'CE':
                        ce_list.append(item_data)
                    else:
                        pe_list.append(item_data)
            
            return {'CE': ce_list, 'PE': pe_list}

        except Exception as e:
            logger.error(f"Option Chain Logic Error: {e}")
            return {'CE': [], 'PE': []}

    def get_batch_ltp(self, instrument_keys):
        """
        Fetches LTP for multiple keys in a single HTTP request.
        """
        try:
            # Split into chunks if > 100 (API limit)
            chunks = [instrument_keys[i:i + 90] for i in range(0, len(instrument_keys), 90)]
            result_map = {}
            
            for chunk in chunks:
                key_str = ",".join(chunk)
                url = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={key_str}"
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.access_token}"
                }
                
                resp = requests.get(url, headers=headers, timeout=5)
                data = resp.json()
                
                if data.get('status') == 'success':
                    # data['data'] = { "NSE_FO|...": { "last_price": 123.4, ... } }
                    for k, v in data.get('data', {}).items():
                        result_map[k] = v.get('last_price', 0.0)
            
            return result_map
            
        except Exception as e:
            logger.error(f"Batch Quote Error: {e}")
            return {}

    # =========================================================
    # ⚙️ EXECUTION & ORDER MANAGEMENT (Standard)
    # =========================================================

    def place_order(self, instrument_key, transaction_type, quantity, order_type, trigger_price=0.0):
        """
        Places an order using the Upstox API.
        """
        try:
            body = {
                "quantity": int(quantity),
                "product": config.PRODUCT_TYPE, # 'I' (Intraday) usually
                "validity": "DAY",
                "price": 0.0, # Market order has 0 price
                "tag": "ALGO_BOT",
                "instrument_token": instrument_key,
                "order_type": order_type, # MARKET, SL-M
                "transaction_type": transaction_type, # BUY, SELL
                "disclosed_quantity": 0,
                "trigger_price": float(trigger_price),
                "is_amo": False
            }
            
            response = self.order_api.place_order(body, self.api_version)
            if response and response.status == 'success':
                return response.data.order_id
            return None

        except ApiException as e:
            # Intelligent Error Handling: Parse JSON body if possible
            try:
                err_body = json.loads(e.body)
                msg = err_body.get('errors', [{}])[0].get('message', str(e))
                logger.error(f"Order Placement Failed: {msg}")
            except:
                logger.error(f"Order Placement Failed: {e}")
            raise e

    def modify_order(self, order_id, trigger_price):
        """
        Modifies an open order (used for Trailing SL).
        """
        try:
            body = {
                "order_id": order_id,
                "trigger_price": float(trigger_price),
                "order_type": "SL-M", # Usually we modify SL-M orders
                "quantity": 0, # 0 means no change
                "price": 0.0,
                "validity": "DAY",
                "disclosed_quantity": 0
            }
            self.order_api.modify_order(body, self.api_version)
            return True
        except Exception as e:
            logger.error(f"Modify Failed: {e}")
            return False

    def cancel_order(self, order_id):
        try:
            self.order_api.cancel_order(order_id, self.api_version)
            return True
        except Exception:
            return False

    def update_access_token(self, new_token):
        """
        Allows hot-swapping the token without restart.
        """
        self.access_token = new_token
        self.conf.access_token = new_token
        # Re-init APIs with new config
        self.api_client = ApiClient(self.conf)
        self.order_api = upstox_client.OrderApi(self.api_client)
        self.portfolio_api = upstox_client.PortfolioApi(self.api_client)
        self.quote_api = upstox_client.MarketQuoteApi(self.api_client)
        logger.info("Token Refreshed in UpstoxClient.")

    # =========================================================
    # 📥 RECONCILIATION HELPERS
    # =========================================================

    def get_positions(self):
        try:
            resp = self.portfolio_api.get_positions(self.api_version)
            return resp.data.net if resp and resp.data else []
        except Exception: return []

    def get_open_orders(self):
        try:
            resp = self.order_api.get_order_book(self.api_version)
            if resp and resp.data:
                # Filter for Open/Trigger Pending
                return [o for o in resp.data if o.status in ['open', 'trigger pending']]
            return []
        except Exception: return []

    def cancel_all_orders(self):
        orders = self.get_open_orders()
        for o in orders:
            self.cancel_order(o.order_id)
            time.sleep(0.1) # Rate limit protection

    def close_all_positions(self):
        """
        Emergency Exits: Sells/Buys to flatten all positions.
        """
        positions = self.get_positions()
        for p in positions:
            qty = int(p.quantity)
            if qty != 0:
                tx_type = "SELL" if qty > 0 else "BUY"
                try:
                    self.place_order(p.instrument_token, tx_type, abs(qty), "MARKET")
                except Exception: pass