#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys

# 強制無緩衝輸出
os.environ['PYTHONUNBUFFERED'] = '1'
sys.stdout.flush()
sys.stderr.flush()

print("=" * 80, flush=True)
print("🚀 程式啟動中...", flush=True)
print("=" * 80, flush=True)

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
TICK_SIZE = 0.0001  # 價格最小變動單位

# 資金設定
CAPITAL_PERCENT = 0.5  # 每次用總資產的 50%

# 時間設定
CHECK_PRICE_INTERVAL = 0.3  # 查價間隔（秒）
DISPLAY_STATUS_INTERVAL = 60  # 顯示狀態間隔（秒）

# 觀察與等待時間
OBSERVATION_SECONDS = 15  # 觀察價格區間 15 秒
WAIT_BUY_SECONDS = 15     # 等待首次買入 15 秒

# 開單時間控制
ENABLE_SCHEDULE = True
SCHEDULE_MINUTES = list(range(60))  # 每分鐘

# DEBUG 模式
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
        """獲取當前價格"""
        result = self._request('GET', "/api/v3/ticker/price", {'symbol': symbol})
        if result and 'price' in result:
            return round(float(result['price']), 4)
        return None
    
    def get_balance(self, asset):
        """獲取餘額"""
        result = self._request('GET', "/api/v3/account")
        if result and 'balances' in result:
            for balance in result['balances']:
                if balance['asset'] == asset:
                    return float(balance['free'])
        return 0
    
    def place_market_order(self, symbol, side, quantity):
        """下市價單"""
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': str(quantity)
        }
        
        result = self._request('POST', "/api/v3/order", params)
        
        if result and 'orderId' in result:
            logging.info(f"✓ 市價單提交: {side} {quantity}")
        else:
            logging.error(f"✗ 市價單失敗: {result}")
        
        return result
    
    def query_order(self, symbol, order_id):
        """查詢訂單狀態"""
        return self._request('GET', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})

class OscillationGrid:
    """震盪區間網格"""
    def __init__(self, grid_id, min_price, max_price, capital):
        self.id = grid_id
        self.min_price = round(min_price, 4)
        self.max_price = round(max_price, 4)
        self.capital = capital
        self.created_time = datetime.now()
        self.active = True
        
        # 交易價格（精確匹配）
        self.buy_price = self.min_price
        self.sell_price = self.max_price
        
        # 止損止盈（超出震盪區間 ±1 tick）
        self.lower_stop = round(self.min_price - TICK_SIZE, 4)
        self.upper_stop = round(self.max_price + TICK_SIZE, 4)
        
        # 狀態
        self.position = None  # {'quantity': float, 'buy_price': float, 'buy_time': float}
        self.total_profit = 0
        self.trade_count = 0
        self.pending_order = None  # {'order_id': str, 'side': str, 'created_time': float}
        
        # 首次買入狀態
        self.initial_buy_done = False
        self.initial_buy_deadline = None
    
    def should_close(self, current_price):
        """檢查是否應該關閉網格"""
        return current_price <= self.lower_stop or current_price >= self.upper_stop

class GridBot:
    def __init__(self, client):
        self.client = client
        self.current_grid = None
        self.grid_counter = 0
        self.total_profit = 0
        self.total_trades = 0
        self.initial_assets = self._get_total_assets()
        
        self._display_startup()
    
    def _get_total_assets(self):
        """獲取總資產"""
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
        """顯示啟動資訊"""
        print_separator()
        logging.info("🎯 USDC/USDT 震盪區間套利機器人")
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
            logging.info(f"  價格精度: {TICK_SIZE}")
        print_separator()
    
    def _observe_price_range(self):
        """觀察價格區間"""
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
        logging.info(f"  價格範圍: {(max_price - min_price) / TICK_SIZE:.0f} ticks")
        
        return min_price, max_price
    
    def try_create_new_grid(self):
        """嘗試創建新網格"""
        # 如果已有活躍網格，跳過
        if self.current_grid and self.current_grid.active:
            return
        
        # 觀察價格區間
        min_price, max_price = self._observe_price_range()
        
        if min_price is None or max_price is None:
            return
        
        # 計算開單資金
        current_assets = self._get_total_assets()
        if not current_assets:
            logging.error("❌ 無法獲取資產資訊")
            return
        
        capital = current_assets['total'] * CAPITAL_PERCENT
        
        if capital < 5:
            logging.error(f"❌ 資金不足: {capital:.2f} USDT")
            return
        
        # 創建網格
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        print_separator()
        logging.info(f"📊 創建網格 {grid_id}")
        logging.info(f"  震盪區間: ${min_price:.4f} ~ ${max_price:.4f}")
        logging.info(f"  開單資金: {capital:.2f} USDT ({CAPITAL_PERCENT * 100}%)")
        logging.info(f"  總資產: {current_assets['total']:.2f} USDT")
        
        grid = OscillationGrid(grid_id, min_price, max_price, capital)
        grid.initial_total_assets = current_assets['total']
        
        logging.info("")
        logging.info(f"✅ 買入價格: ${grid.buy_price:.4f} (震盪區間下限)")
        logging.info(f"✅ 賣出價格: ${grid.sell_price:.4f} (震盪區間上限)")
        logging.info(f"⚠️  下止損: ${grid.lower_stop:.4f} (跌破關閉)")
        logging.info(f"⚠️  上止盈: ${grid.upper_stop:.4f} (突破關閉)")
        logging.info("")
        
        # 設定首次買入截止時間
        grid.initial_buy_deadline = time.time() + WAIT_BUY_SECONDS
        
        self.current_grid = grid
        
        logging.info(f"⏳ 等待價格到達 ${grid.buy_price:.4f} 進行首次買入...")
        logging.info(f"   限時 {WAIT_BUY_SECONDS} 秒，超時則放棄本次網格")
        print_separator()
    
    def _try_initial_buy(self, grid, current_price):
        """嘗試首次買入（限時）"""
        # 檢查是否已完成首次買入
        if grid.initial_buy_done:
            return
        
        # 檢查是否超時
        if time.time() > grid.initial_buy_deadline:
            logging.warning(f"⏰ 首次買入超時 ({WAIT_BUY_SECONDS}秒)，放棄網格 {grid.id}")
            grid.active = False
            self.current_grid = None
            return
        
        # 檢查是否有掛單
        if grid.pending_order and grid.pending_order['side'] == 'BUY':
            return
        
        # 精確匹配買入價
        if current_price != grid.buy_price:
            return
        
        # 計算買入數量
        quantity = round(grid.capital / current_price, 2)
        
        logging.info(f"🎯 價格到達 ${current_price:.4f}，執行首次買入！")
        logging.info(f"🛒 市價買入: {quantity:.2f} USDC (約 {grid.capital:.2f} USDT)")
        
        result = self.client.place_market_order(SYMBOL, 'BUY', quantity)
        
        if result and 'orderId' in result:
            grid.pending_order = {
                'order_id': result['orderId'],
                'side': 'BUY',
                'created_time': time.time(),
                'quantity': quantity
            }
    
    def _try_buy(self, grid, current_price):
        """嘗試買入（循環交易中）"""
        # 必須沒有持倉
        if grid.position:
            return False
        
        # 不能有掛單
        if grid.pending_order and grid.pending_order['side'] == 'BUY':
            return False
        
        # 精確匹配買入價
        if current_price != grid.buy_price:
            return False
        
        # 計算買入數量
        quantity = round(grid.capital / current_price, 2)
        
        logging.info(f"🔄 循環買入: 價格 ${current_price:.4f}")
        logging.info(f"🛒 市價買入: {quantity:.2f} USDC")
        
        result = self.client.place_market_order(SYMBOL, 'BUY', quantity)
        
        if result and 'orderId' in result:
            grid.pending_order = {
                'order_id': result['orderId'],
                'side': 'BUY',
                'created_time': time.time(),
                'quantity': quantity
            }
            return True
        
        return False
    
    def _try_sell(self, grid, current_price):
        """嘗試賣出"""
        # 必須有持倉
        if not grid.position:
            return False
        
        # 不能有掛單
        if grid.pending_order and grid.pending_order['side'] == 'SELL':
            return False
        
        # 精確匹配賣出價
        if current_price != grid.sell_price:
            return False
        
        # 查詢實際 USDC 餘額
        actual_balance = self.client.get_balance('USDC')
        quantity = min(grid.position['quantity'], actual_balance) * 0.999
        quantity = round(quantity, 2)
        
        if quantity < 1.01:
            logging.error(f"❌ 數量不足: {quantity:.2f} USDC")
            return False
        
        logging.info(f"💰 賣出觸發: 價格 ${current_price:.4f}")
        logging.info(f"💵 市價賣出: {quantity:.2f} USDC")
        
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
        """檢查掛單狀態"""
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
                # 買入成交
                filled_value = float(order_info.get('cummulativeQuoteQty', 0))
                filled_price = filled_value / filled_qty if filled_qty > 0 else grid.buy_price
                
                grid.position = {
                    'quantity': filled_qty,
                    'buy_price': filled_price,
                    'buy_time': time.time()
                }
                
                logging.info(f"✓ 買入成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
                logging.info(f"   等待價格到達 ${grid.sell_price:.4f} 賣出...")
                
                # 標記首次買入完成
                if not grid.initial_buy_done:
                    grid.initial_buy_done = True
                    logging.info(f"✓ 網格 {grid.id} 首次買入成功，進入循環交易模式")
            
            else:  # SELL
                # 賣出成交
                if grid.position:
                    filled_value = float(order_info.get('cummulativeQuoteQty', 0))
                    filled_price = filled_value / filled_qty if filled_qty > 0 else grid.sell_price
                    
                    profit = (filled_price - grid.position['buy_price']) * filled_qty
                    grid.total_profit += profit
                    grid.trade_count += 1
                    self.total_profit += profit
                    self.total_trades += 1
                    
                    logging.info(f"✓ 賣出成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
                    logging.info(f"   買入價: ${grid.position['buy_price']:.4f}")
                    logging.info(f"   利潤: {profit:+.6f} USDT")
                    logging.info(f"   等待價格回到 ${grid.buy_price:.4f} 再次買入...")
                
                grid.position = None
            
            grid.pending_order = None
        
        elif status in ['CANCELED', 'REJECTED', 'EXPIRED', 'FAILED']:
            logging.error(f"❌ 訂單失敗: {status}")
            grid.pending_order = None
        
        elif status in ['NEW', 'PARTIALLY_FILLED']:
            if time.time() - grid.pending_order['created_time'] > 3:
                logging.warning(f"⚠️  市價單異常緩慢: {status}")
    
    def update_grid(self):
        """更新網格狀態"""
        if not self.current_grid or not self.current_grid.active:
            return
        
        current_price = self.client.get_price(SYMBOL)
        if not current_price:
            return
        
        grid = self.current_grid
        
        # 檢查止損止盈
        if grid.should_close(current_price):
            logging.warning(f"⚠️  價格 ${current_price:.4f} 超出震盪區間，觸發止損/止盈")
            self.close_grid(grid, current_price)
            return
        
        # 檢查掛單狀態
        self._check_pending_order(grid)
        
        # 如果還沒完成首次買入
        if not grid.initial_buy_done:
            self._try_initial_buy(grid, current_price)
            return
        
        # 循環交易
        if not grid.pending_order:
            if not grid.position:
                self._try_buy(grid, current_price)
            else:
                self._try_sell(grid, current_price)
    
    def close_grid(self, grid, current_price):
        """關閉網格（複利結算）"""
        grid.active = False
        
        logging.info(f"🔴 關閉網格 {grid.id}")
        
        # 如果有持倉，市價平倉
        if grid.position:
            quantity = round(grid.position['quantity'] * 0.999, 2)
            
            logging.info(f"   清倉持倉: {quantity:.2f} USDC (市價)")
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
                    logging.info(f"   清倉利潤: {profit:+.6f} USDT")
        
        # 清空所有剩餘 USDC（複利機制）
        time.sleep(1)
        remaining_usdc = self.client.get_balance('USDC')
        
        if remaining_usdc > 0.01:
            logging.info(f"   清空剩餘 USDC: {remaining_usdc:.4f} → 轉為 USDT (複利)")
            quantity = round(remaining_usdc * 0.999, 2)
            
            result = self.client.place_market_order(SYMBOL, 'SELL', quantity)
            
            if result and 'orderId' in result:
                time.sleep(2)
                order_info = self.client.query_order(SYMBOL, result['orderId'])
                
                if order_info and order_info.get('status') == 'FILLED':
                    logging.info(f"   ✓ USDC 已清空，資產已轉為 USDT")
        
        # 網格統計
        logging.info("")
        logging.info(f"📊 網格 {grid.id} 結算:")
        logging.info(f"   震盪區間: ${grid.min_price:.4f} ~ ${grid.max_price:.4f}")
        logging.info(f"   交易次數: {grid.trade_count}")
        logging.info(f"   已實現利潤: {grid.total_profit:+.6f} USDT")
        
        # 計算新的總資產（複利）
        new_assets = self._get_total_assets()
        if new_assets and hasattr(grid, 'initial_total_assets'):
            change = new_assets['total'] - grid.initial_total_assets
            logging.info(f"   本輪資產變化: {change:+.4f} USDT")
            logging.info(f"   新總資產: {new_assets['total']:.2f} USDT (用於下輪)")
        
        self.current_grid = None
        print_separator()
    
    def display_status(self):
        """顯示狀態"""
        current_assets = self._get_total_assets()
        
        print_separator()
        logging.info("📊 震盪區間套利 - 狀態報告")
        print_separator()
        logging.info(f"⏰ 時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info("")
        
        if current_assets and self.initial_assets:
            logging.info(f"💱 當前價格: ${current_assets['price']:.4f}")
            logging.info("")
            
            initial_value = self.initial_assets['total']
            current_value = current_assets['total']
            change = current_value - initial_value
            percent = (change / initial_value * 100) if initial_value > 0 else 0
            
            logging.info("💰 資產變化 (複利):")
            logging.info(f"  初始: {initial_value:.2f} USDT")
            logging.info(f"  當前: {current_value:.2f} USDT")
            logging.info(f"  盈虧: {change:+.4f} USDT ({percent:+.2f}%)")
            logging.info(f"  ├─ USDT: {current_assets['USDT']:.2f}")
            logging.info(f"  └─ USDC: {current_assets['USDC']:.4f}")
            logging.info("")
        
        logging.info("📈 策略統計:")
        logging.info(f"  累計套利次數: {self.total_trades}")
        logging.info(f"  已實現利潤: {self.total_profit:+.6f} USDT")
        logging.info("")
        
        if self.current_grid and self.current_grid.active:
            grid = self.current_grid
            logging.info("📋 當前網格:")
            logging.info(f"  {grid.id}")
            logging.info(f"  震盪區間: ${grid.min_price:.4f} ~ ${grid.max_price:.4f}")
            logging.info(f"  買入價: ${grid.buy_price:.4f} | 賣出價: ${grid.sell_price:.4f}")
            logging.info(f"  止損/止盈: ${grid.lower_stop:.4f} / ${grid.upper_stop:.4f}")
            
            if not grid.initial_buy_done:
                remaining = grid.initial_buy_deadline - time.time()
                logging.info(f"  狀態: 等待首次買入 (剩餘 {remaining:.0f} 秒)")
            elif grid.position:
                logging.info(f"  持倉: {grid.position['quantity']:.2f} USDC @ ${grid.position['buy_price']:.4f}")
                logging.info(f"  等待: 價格到達 ${grid.sell_price:.4f} 賣出")
            else:
                logging.info(f"  持倉: 無")
                logging.info(f"  等待: 價格回到 ${grid.buy_price:.4f} 買入")
            
            if grid.pending_order:
                logging.info(f"  掛單: {grid.pending_order['side']} {grid.pending_order['quantity']:.2f}")
            
            logging.info(f"  套利次數: {grid.trade_count}")
            logging.info(f"  已實現利潤: {grid.total_profit:+.6f} USDT")
        else:
            logging.info("當前無活躍網格，等待下一個開單時機")
        
        print_separator()

def should_observe(last_observe_minute):
    """判斷是否該觀察"""
    if not ENABLE_SCHEDULE:
        return False, -1
    
    now = datetime.now()
    
    if now.minute in SCHEDULE_MINUTES and now.minute != last_observe_minute and now.second < 10:
        logging.info(f"⏰ 觸發觀察時機: {now.strftime('%H:%M:%S')}")
        return True, now.minute
    
    return False, last_observe_minute

def main():
    logging.info("🚀 啟動 USDC/USDT 震盪區間套利機器人...")
    
    client = MEXCClient(API_KEY, SECRET_KEY)
    
    logging.info("🔌 測試 API 連接...")
    test_price = client.get_price(SYMBOL)
    if not test_price:
        logging.error("❌ API 連接失敗")
        return
    
    logging.info(f"✓ API 連接成功，{SYMBOL} 當前價格: ${test_price:.4f}")