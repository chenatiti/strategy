#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys

def install_package(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import requests
except ImportError:
    print("正在安裝 requests...")
    install_package("requests==2.31.0")
    import requests

try:
    from dotenv import load_dotenv
except ImportError:
    print("正在安裝 python-dotenv...")
    install_package("python-dotenv==1.0.0")
    from dotenv import load_dotenv

load_dotenv()

import time
import hashlib
import hmac
import os
from urllib.parse import urlencode
from datetime import datetime
import logging

# ==================== 配置區域 ====================

API_KEY = os.getenv('MEXC_API_KEY', 'mx0vglaUUDV1VP6KTU')
SECRET_KEY = os.getenv('MEXC_SECRET_KEY', '0e3a3cb6b0e24b0fbdf82d0c1e15c4b1')

SYMBOL = "USDCUSDT"
TICK_SIZE = 0.0001

CAPITAL_PERCENT = 0.5
CHECK_PRICE_INTERVAL = 0.3
DISPLAY_STATUS_INTERVAL = 60

ENABLE_SCHEDULE = True
SCHEDULE_MINUTES = list(range(60))
OBSERVATION_SECONDS = 15
WAIT_BUY_SECONDS = 15

DEBUG_MODE = os.getenv('DEBUG_MODE', 'True').lower() == 'true'

# ==================== 配置區域結束 ====================

log_level = logging.DEBUG if DEBUG_MODE else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def print_separator():
    print("=" * 80)

class MEXCClient:
    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = "https://api.mexc.com"
    
    def _generate_signature(self, query_string):
        return hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def _request(self, method, endpoint, params=None):
        if params is None:
            params = {}
        
        params['timestamp'] = int(time.time() * 1000)
        params = {k: str(v) for k, v in params.items() if v is not None and str(v) != ''}
        sorted_params = dict(sorted(params.items()))
        query_string = urlencode(sorted_params)
        signature = self._generate_signature(query_string)
        sorted_params['signature'] = signature
        
        headers = {'X-MEXC-APIKEY': self.api_key}
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == 'POST':
                response = requests.post(url, data=urlencode(sorted_params), headers=headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, params=sorted_params, headers=headers, timeout=30)
            else:
                response = requests.get(url, params=sorted_params, headers=headers, timeout=30)
            
            if DEBUG_MODE and method in ['POST', 'DELETE']:
                logging.debug(f"API {method} {endpoint}: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"API 錯誤: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logging.error(f"請求異常: {e}")
            return None
    
    def get_price(self, symbol):
        result = self._request('GET', "/api/v3/ticker/price", {'symbol': symbol})
        if result and 'price' in result:
            return round(float(result['price']), 4)
        return None
    
    def get_balance(self, asset):
        result = self._request('GET', "/api/v3/account")
        if result and 'balances' in result:
            for balance in result['balances']:
                if balance['asset'] == asset:
                    return float(balance['free'])
        return 0
    
    def place_market_order(self, symbol, side, quantity):
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
        }
        
        if side == 'BUY':
            params['quoteOrderQty'] = str(quantity)
            logging.info(f"✓ 市價買單: 使用 {quantity} USDT 買入 USDC")
        else:
            params['quantity'] = str(quantity)
            logging.info(f"✓ 市價賣單: 賣出 {quantity} USDC")
        
        result = self._request('POST', "/api/v3/order", params)
        
        if result and 'orderId' in result:
            logging.info(f"  訂單ID: {result['orderId']}")
        else:
            logging.error(f"✗ 市價單失敗: {result}")
        
        return result
    
    def query_order(self, symbol, order_id):
        return self._request('GET', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})

class FixedGrid:
    def __init__(self, grid_id, min_price, max_price, capital):
        self.id = grid_id
        self.min_price = round(min_price, 4)
        self.max_price = round(max_price, 4)
        self.capital = capital
        self.created_time = datetime.now()
        self.active = True
        
        self.buy_price = self.min_price
        self.sell_price = self.max_price
        self.lower_stop = round(self.min_price - TICK_SIZE, 4)
        self.upper_stop = round(self.max_price + TICK_SIZE, 4)
        
        self.position = None
        self.total_profit = 0
        self.trade_count = 0
        self.pending_order = None
        self.initial_buy_done = False
        self.initial_buy_deadline = None
    
    def should_close(self, current_price):
        return current_price <= self.lower_stop or current_price >= self.upper_stop

class FixedGridBot:
    def __init__(self, client):
        self.client = client
        self.current_grid = None
        self.grid_counter = 0
        self.total_profit = 0
        self.total_trades = 0
        self.initial_assets = self._get_total_assets()
        self._display_startup()
    
    def _get_total_assets(self):
        usdt = self.client.get_balance('USDT')
        usdc = self.client.get_balance('USDC')
        price = self.client.get_price(SYMBOL)
        
        if price:
            total = usdt + (usdc * price)
            return {
                'USDT': usdt,
                'USDC': usdc,
                'price': price,
                'total': total,
                'timestamp': datetime.now()
            }
        return None
    
    def _display_startup(self):
        print_separator()
        logging.info("USDC/USDT 震盪區間套利機器人")
        print_separator()
        
        if self.initial_assets:
            logging.info(f"當前價格: ${self.initial_assets['price']:.4f}")
            logging.info("")
            logging.info("💰 初始資產:")
            logging.info(f"  USDT: {self.initial_assets['USDT']:.2f}")
            logging.info(f"  USDC: {self.initial_assets['USDC']:.4f}")
            logging.info(f"  總值: {self.initial_assets['total']:.2f} USDT")
            logging.info("")
            logging.info("⚙️  策略配置:")
            logging.info(f"  每單資金: 總資產 × {CAPITAL_PERCENT * 100}%")
            logging.info(f"  觀察時間: {OBSERVATION_SECONDS} 秒")
            logging.info(f"  等待買入: {WAIT_BUY_SECONDS} 秒")
        print_separator()
    
    def _observe_price_range(self):
        logging.info(f"🔍 開始觀察價格區間 {OBSERVATION_SECONDS} 秒...")
        
        prices = []
        start_time = time.time()
        
        while time.time() - start_time < OBSERVATION_SECONDS:
            price = self.client.get_price(SYMBOL)
            if price:
                prices.append(price)
                if DEBUG_MODE:
                    logging.debug(f"  觀察價格: ${price:.4f}")
            time.sleep(CHECK_PRICE_INTERVAL)
        
        if not prices:
            logging.error("❌ 觀察期間無法獲取價格")
            return None, None
        
        min_price = min(prices)
        max_price = max(prices)
        
        logging.info(f"✓ 觀察完成: 震盪區間 ${min_price:.4f} ~ ${max_price:.4f}")
        
        return min_price, max_price
    
    def try_create_new_grid(self):
        if self.current_grid and self.current_grid.active:
            return
        
        min_price, max_price = self._observe_price_range()
        
        if min_price is None or max_price is None:
            return
        
        current_assets = self._get_total_assets()
        if not current_assets:
            logging.error("❌ 無法獲取資產資訊")
            return
        
        capital = current_assets['total'] * CAPITAL_PERCENT
        
        if capital < 5:
            logging.error(f"❌ 資金不足: {capital:.2f} USDT")
            return
        
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        print_separator()
        logging.info(f"📊 創建網格 {grid_id}")
        logging.info(f"  震盪區間: ${min_price:.4f} ~ ${max_price:.4f}")
        logging.info(f"  開單資金: {capital:.2f} USDT ({CAPITAL_PERCENT * 100}%)")
        
        grid = FixedGrid(grid_id, min_price, max_price, capital)
        grid.initial_total_assets = current_assets['total']
        grid.initial_buy_deadline = time.time() + WAIT_BUY_SECONDS
        
        logging.info(f"  買入價格: ${grid.buy_price:.4f}")
        logging.info(f"  賣出價格: ${grid.sell_price:.4f}")
        logging.info(f"  ⏳ 等待價格到達 ${grid.buy_price:.4f}，限時 {WAIT_BUY_SECONDS} 秒")
        
        self.current_grid = grid
        print_separator()
    
    def _try_initial_buy(self, grid, current_price):
        if grid.initial_buy_done:
            return
        
        if time.time() > grid.initial_buy_deadline:
            logging.warning(f"⏰ 首次買入超時，放棄網格 {grid.id}")
            grid.active = False
            self.current_grid = None
            return
        
        if grid.pending_order and grid.pending_order['side'] == 'BUY':
            return
        
        if current_price != grid.buy_price:
            if DEBUG_MODE:
                logging.debug(f"等待買入: 當前 ${current_price:.4f}, 目標 ${grid.buy_price:.4f}")
            return
        
        usdt_amount = round(grid.capital, 2)
        
        logging.info(f"🎯 價格到達 ${current_price:.4f}，執行買入！")
        
        result = self.client.place_market_order(SYMBOL, 'BUY', usdt_amount)
        
        if result and 'orderId' in result:
            grid.pending_order = {
                'order_id': result['orderId'],
                'side': 'BUY',
                'created_time': time.time(),
                'quantity': usdt_amount
            }
    
    def _try_buy(self, grid, current_price):
        if grid.position:
            return False
        
        if grid.pending_order and grid.pending_order['side'] == 'BUY':
            return False
        
        if current_price != grid.buy_price:
            return False
        
        usdt_amount = round(grid.capital, 2)
        
        logging.info(f"🔄 循環買入: 價格 ${current_price:.4f}")
        
        result = self.client.place_market_order(SYMBOL, 'BUY', usdt_amount)
        
        if result and 'orderId' in result:
            grid.pending_order = {
                'order_id': result['orderId'],
                'side': 'BUY',
                'created_time': time.time(),
                'quantity': usdt_amount
            }
            return True
        
        return False
    
    def _try_sell(self, grid, current_price):
        if not grid.position:
            return False
        
        if grid.pending_order and grid.pending_order['side'] == 'SELL':
            return False
        
        if current_price != grid.sell_price:
            return False
        
        actual_balance = self.client.get_balance('USDC')
        quantity = min(grid.position['quantity'], actual_balance) * 0.999
        quantity = round(quantity, 2)
        
        if quantity < 1.01:
            logging.error(f"數量不足: {quantity:.2f} USDC")
            return False
        
        logging.info(f"💰 賣出觸發: 價格 ${current_price:.4f}")
        
        result = self.client.place_market_order(SYMBOL, 'SELL', quantity)
        
        if result and 'orderId' in result:
            grid.pending_order = {
                'order_id': result['orderId'],
                'side': 'SELL',
                'created_time': time.time(),
                'quantity': quantity
            }
            return True
        
        return False
    
    def _check_pending_order(self, grid):
        if not grid.pending_order:
            return
        
        order_id = grid.pending_order['order_id']
        order_info = self.client.query_order(SYMBOL, order_id)
        
        if not order_info:
            return
        
        status = order_info.get('status')
        
        if status == 'FILLED':
            side = grid.pending_order['side']
            filled_qty = float(order_info.get('executedQty', grid.pending_order['quantity']))
            
            if side == 'BUY':
                filled_value = float(order_info.get('cummulativeQuoteQty', 0))
                filled_price = filled_value / filled_qty if filled_qty > 0 else grid.buy_price
                
                grid.position = {
                    'quantity': filled_qty,
                    'buy_price': filled_price,
                    'buy_time': time.time()
                }
                logging.info(f"✓ 買入成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
                
                if not grid.initial_buy_done:
                    grid.initial_buy_done = True
            else:
                if grid.position:
                    filled_value = float(order_info.get('cummulativeQuoteQty', 0))
                    filled_price = filled_value / filled_qty if filled_qty > 0 else grid.sell_price
                    
                    profit = (filled_price - grid.position['buy_price']) * filled_qty
                    grid.total_profit += profit
                    grid.trade_count += 1
                    self.total_profit += profit
                    self.total_trades += 1
                    logging.info(f"✓ 賣出成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}, 利潤 {profit:.6f} USDT")
                grid.position = None
            
            grid.pending_order = None
        
        elif status in ['CANCELED', 'REJECTED', 'EXPIRED', 'FAILED']:
            logging.error(f"訂單失敗: {status}")
            grid.pending_order = None
        
        elif status in ['NEW', 'PARTIALLY_FILLED']:
            if time.time() - grid.pending_order['created_time'] > 3:
                logging.warning(f"市價單異常緩慢: {status}")
    
    def update_grid(self):
        if not self.current_grid or not self.current_grid.active:
            return
        
        current_price = self.client.get_price(SYMBOL)
        if not current_price:
            return
        
        grid = self.current_grid
        
        if grid.should_close(current_price):
            logging.warning(f"⚠️  價格 ${current_price:.4f} 觸發止損/止盈")
            self.close_grid(grid, current_price)
            return
        
        self._check_pending_order(grid)
        
        if not grid.initial_buy_done:
            self._try_initial_buy(grid, current_price)
            return
        
        if not grid.pending_order:
            if not grid.position:
                self._try_buy(grid, current_price)
            else:
                self._try_sell(grid, current_price)
    
    def close_grid(self, grid, current_price):
        grid.active = False
        
        if grid.position:
            quantity = round(grid.position['quantity'] * 0.999, 2)
            logging.info(f"清倉持倉: {quantity:.2f} USDC")
            result = self.client.place_market_order(SYMBOL, 'SELL', quantity)
            
            if result and 'orderId' in result:
                time.sleep(2)
                order_info = self.client.query_order(SYMBOL, result['orderId'])
                
                if order_info and order_info.get('status') == 'FILLED':
                    filled_qty = float(order_info.get('executedQty', quantity))
                    filled_value = float(order_info.get('cummulativeQuoteQty', 0))
                    filled_price = filled_value / filled_qty if filled_qty > 0 else current_price
                    
                    profit = (filled_price - grid.position['buy_price']) * filled_qty
                    grid.total_profit += profit
                    self.total_profit += profit
                    logging.info(f"✓ 清倉成交: {profit:+.6f} USDT")
        
        time.sleep(1)
        remaining_usdc = self.client.get_balance('USDC')
        
        if remaining_usdc > 0.01:
            logging.info(f"清空剩餘 USDC: {remaining_usdc:.4f}")
            quantity = round(remaining_usdc * 0.999, 2)
            self.client.place_market_order(SYMBOL, 'SELL', quantity)
            time.sleep(2)
        
        logging.info(f"網格 {grid.id} 已關閉，利潤: {grid.total_profit:+.6f} USDT")
        self.current_grid = None
    
    def display_status(self):
        current_assets = self._get_total_assets()
        
        print_separator()
        logging.info("📊 狀態報告")
        print_separator()
        
        if current_assets and self.initial_assets:
            logging.info(f"💱 當前價格: ${current_assets['price']:.4f}")
            
            initial_value = self.initial_assets['total']
            current_value = current_assets['total']
            change = current_value - initial_value
            percent = (change / initial_value * 100) if initial_value > 0 else 0
            
            logging.info(f"💰 資產: {current_value:.2f} USDT (盈虧: {change:+.4f} USDT / {percent:+.2f}%)")
            logging.info(f"📈 累計套利: {self.total_trades} 次，利潤: {self.total_profit:+.6f} USDT")
        
        if self.current_grid and self.current_grid.active:
            grid = self.current_grid
            logging.info(f"📋 當前網格: {grid.id} @ ${grid.min_price:.4f}~${grid.max_price:.4f}")
            
            if not grid.initial_buy_done:
                remaining = grid.initial_buy_deadline - time.time()
                logging.info(f"  等待首次買入 (剩餘 {remaining:.0f} 秒)")
            elif grid.position:
                logging.info(f"  持倉: {grid.position['quantity']:.2f} USDC @ ${grid.position['buy_price']:.4f}")
            else:
                logging.info(f"  無持倉，等待買入")
        
        print_separator()

def should_observe(last_observe_minute):
    if not ENABLE_SCHEDULE:
        return False, -1
    
    now = datetime.now()
    
    if now.minute in SCHEDULE_MINUTES and now.minute != last_observe_minute and now.second < 10:
        return True, now.minute
    
    return False, last_observe_minute

def main():
    logging.info("🚀 啟動 USDC/USDT 震盪區間套利機器人...")
    
    client = MEXCClient(API_KEY, SECRET_KEY)
    
    test_price = client.get_price(SYMBOL)
    if not test_price:
        logging.error("❌ API 連接失敗")
        return
    
    logging.info(f"✓ API 連接成功，當前價格: ${test_price:.4f}")
    
    usdt = client.get_balance('USDT')
    usdc = client.get_balance('USDC')
    logging.info(f"💼 帳戶資產: USDT {usdt:.2f} | USDC {usdc:.4f}")
    
    total_assets = usdt + (usdc * test_price)
    required_capital = total_assets * CAPITAL_PERCENT
    
    if required_capital < 5:
        logging.error(f"❌ 資金不足！需要至少 10 USDT 總資產")
        return
    
    bot = FixedGridBot(client)
    
    last_observe_minute = -1
    last_display_time = time.time()
    
    try:
        while True:
            should_obs, new_minute = should_observe(last_observe_minute)
            if should_obs:
                bot.try_create_new_grid()
                last_observe_minute = new_minute
            
            bot.update_grid()
            
            if time.time() - last_display_time >= DISPLAY_STATUS_INTERVAL:
                bot.display_status()
                last_display_time = time.time()
            
            time.sleep(CHECK_PRICE_INTERVAL)
    
    except KeyboardInterrupt:
        logging.info("⛔ 停止中...")
        
        if bot.current_grid and bot.current_grid.active:
            current_price = client.get_price(SYMBOL)
            bot.close_grid(bot.current_grid, current_price)
        
        logging.info("👋 程序已退出")
    
    except Exception as e:
        logging.error(f"❌ 程序異常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()