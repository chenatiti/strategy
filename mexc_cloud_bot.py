#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 自動安裝缺少的套件
import subprocess
import sys

def install_package(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

# 檢查並安裝 requests
try:
    import requests
except ImportError:
    print("正在安裝 requests...")
    install_package("requests==2.31.0")
    import requests

# 檢查並安裝 python-dotenv
try:
    from dotenv import load_dotenv
except ImportError:
    print("正在安裝 python-dotenv...")
    install_package("python-dotenv==1.0.0")
    from dotenv import load_dotenv

# 加載環境變數
load_dotenv()

import time
import hashlib
import hmac
import os
from urllib.parse import urlencode
from datetime import datetime
import logging

# ==================== 配置區域 ====================

# MEXC API (支援環境變數)
API_KEY = os.getenv('MEXC_API_KEY', 'mx0vglaUUDV1VP6KTU')
SECRET_KEY = os.getenv('MEXC_SECRET_KEY', '0e3a3cb6b0e24b0fbdf82d0c1e15c4b1')

# 交易對
SYMBOL = "USDCUSDT"
GRID_TICK = 0.0001  # 價格最小變動單位

# 資金設定
CAPITAL_PERCENT = 0.5  # 每次用總資產的 50% 開單

# 時間設定
CHECK_PRICE_INTERVAL = 0.3  # 查價間隔（秒）
DISPLAY_STATUS_INTERVAL = 60  # 顯示狀態間隔（秒）

# 開單時間控制
ENABLE_SCHEDULE = True
SCHEDULE_MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]

# 訂單設定
ORDER_TIMEOUT = 10  # 限價單等待時間（秒）

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
    
    def place_limit_order(self, symbol, side, quantity, price):
        """下限價單"""
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'quantity': str(quantity),
            'price': str(price)
        }
        
        result = self._request('POST', "/api/v3/order", params)
        
        if result and 'orderId' in result:
            logging.info(f"✓ 限價單掛出: {side} {quantity} @ ${price}")
        else:
            logging.error(f"✗ 限價單失敗: {result}")
        
        return result
    
    def cancel_order(self, symbol, order_id):
        """取消訂單"""
        result = self._request('DELETE', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})
        if result:
            logging.info(f"訂單已取消: {order_id}")
        return result
    
    def query_order(self, symbol, order_id):
        """查詢訂單狀態"""
        return self._request('GET', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})

class FixedGrid:
    """單個固定網格"""
    def __init__(self, grid_id, open_price, capital):
        self.id = grid_id
        self.open_price = round(open_price, 4)
        self.capital = capital
        self.created_time = datetime.now()
        self.active = True
        
        # 計算網格價格
        self.buy_price = self.open_price
        self.sell_price = round(self.open_price + GRID_TICK, 4)
        self.upper_close = round(self.open_price + 2 * GRID_TICK, 4)
        self.lower_close = round(self.open_price - GRID_TICK, 4)
        
        # 狀態
        self.position = None  # {'quantity': float, 'buy_price': float, 'buy_time': float}
        self.total_profit = 0
        self.trade_count = 0
        
        # 當前訂單
        self.pending_order = None  # {'order_id': str, 'side': str, 'created_time': float}
    
    def should_close(self, current_price):
        """是否應該關閉網格"""
        return current_price <= self.lower_close or current_price >= self.upper_close

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
        logging.info("USDC/USDT 固定網格套利機器人")
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
            logging.info(f"  價格 TICK: {GRID_TICK}")
            logging.info(f"  開單時間: 每小時 {SCHEDULE_MINUTES} 分")
            logging.info(f"  查價間隔: {CHECK_PRICE_INTERVAL} 秒")
        print_separator()
    
    def create_grid(self):
        """創建新網格"""
        if self.current_grid and self.current_grid.active:
            logging.warning("已有活躍網格，跳過開單")
            return None
        
        current_price = self.client.get_price(SYMBOL)
        if not current_price:
            logging.error("無法獲取當前價格")
            return None
        
        # 計算開單資金
        current_assets = self._get_total_assets()
        if not current_assets:
            logging.error("無法獲取資產資訊")
            return None
        
        capital = current_assets['total'] * CAPITAL_PERCENT
        
        if capital < 5:
            logging.error(f"資金不足: {capital:.2f} USDT")
            return None
        
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        print_separator()
        logging.info(f"📊 創建網格 {grid_id}")
        logging.info(f"開單價格: ${current_price:.4f}")
        logging.info(f"開單資金: {capital:.2f} USDT ({CAPITAL_PERCENT * 100}%)")
        
        grid = FixedGrid(grid_id, current_price, capital)
        
        logging.info(f"買入價格: ${grid.buy_price:.4f}")
        logging.info(f"賣出價格: ${grid.sell_price:.4f}")
        logging.info(f"關閉條件: < ${grid.lower_close:.4f} 或 > ${grid.upper_close:.4f}")
        logging.info("")
        
        # 立即嘗試買入
        if self._try_buy(grid, current_price):
            self.current_grid = grid
            logging.info(f"✓ 網格 {grid_id} 創建成功")
            print_separator()
            return grid_id
        else:
            logging.error(f"✗ 網格 {grid_id} 創建失敗")
            print_separator()
            return None
    
    def _try_buy(self, grid, current_price):
        """嘗試買入"""
        # 如果已有持倉，不買
        if grid.position:
            return False
        
        # 如果有掛單，不重複掛
        if grid.pending_order and grid.pending_order['side'] == 'BUY':
            return False
        
        # 只在買入價時買入
        if current_price != grid.buy_price:
            return False
        
        # 計算買入數量
        quantity = round(grid.capital / grid.buy_price, 4)
        
        logging.info(f"🛒 買入: {quantity:.4f} USDC @ ${grid.buy_price:.4f}")
        
        result = self.client.place_limit_order(SYMBOL, 'BUY', quantity, grid.buy_price)
        
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
        # 如果沒持倉，不賣
        if not grid.position:
            return False
        
        # 如果有掛單，不重複掛
        if grid.pending_order and grid.pending_order['side'] == 'SELL':
            return False
        
        # 只在賣出價時賣出
        if current_price != grid.sell_price:
            return False
        
        quantity = grid.position['quantity']
        
        logging.info(f"💰 賣出: {quantity:.4f} USDC @ ${grid.sell_price:.4f}")
        
        result = self.client.place_limit_order(SYMBOL, 'SELL', quantity, grid.sell_price)
        
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
            # 成交
            side = grid.pending_order['side']
            filled_qty = float(order_info.get('executedQty', grid.pending_order['quantity']))
            filled_price = float(order_info.get('price', 0))
            
            if side == 'BUY':
                grid.position = {
                    'quantity': filled_qty,
                    'buy_price': filled_price,
                    'buy_time': time.time()
                }
                logging.info(f"✓ 買入成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
            else:  # SELL
                if grid.position:
                    profit = (filled_price - grid.position['buy_price']) * filled_qty
                    grid.total_profit += profit
                    grid.trade_count += 1
                    self.total_profit += profit
                    self.total_trades += 1
                    logging.info(f"✓ 賣出成交: 利潤 {profit:.6f} USDT")
                grid.position = None
            
            grid.pending_order = None
        
        elif status in ['CANCELED', 'REJECTED', 'EXPIRED', 'FAILED']:
            logging.error(f"訂單失敗: {status}")
            grid.pending_order = None
        
        elif status in ['NEW', 'PARTIALLY_FILLED']:
            # 檢查是否超時
            if time.time() - grid.pending_order['created_time'] > ORDER_TIMEOUT:
                logging.warning("訂單超時，取消")
                self.client.cancel_order(SYMBOL, order_id)
                grid.pending_order = None
    
    def update_grid(self):
        """更新網格"""
        if not self.current_grid or not self.current_grid.active:
            return
        
        current_price = self.client.get_price(SYMBOL)
        if not current_price:
            return
        
        grid = self.current_grid
        
        # 檢查是否需要關閉
        if grid.should_close(current_price):
            logging.info(f"⚠️  價格 ${current_price:.4f} 超出範圍，關閉網格")
            self.close_grid(grid, current_price)
            return
        
        # 檢查掛單狀態
        self._check_pending_order(grid)
        
        # 嘗試交易
        if not grid.pending_order:
            if not grid.position:
                self._try_buy(grid, current_price)
            else:
                self._try_sell(grid, current_price)
    
    def close_grid(self, grid, current_price):
        """關閉網格"""
        grid.active = False
        
        # 取消掛單
        if grid.pending_order:
            self.client.cancel_order(SYMBOL, grid.pending_order['order_id'])
            grid.pending_order = None
        
        # 止損/止盈賣出
        if grid.position:
            quantity = grid.position['quantity']
            
            # 用市價 - 0.0001 確保成交
            sell_price = round(current_price - GRID_TICK, 4)
            
            logging.info(f"清倉: {quantity:.4f} USDC @ ${sell_price:.4f}")
            result = self.client.place_limit_order(SYMBOL, 'SELL', quantity, sell_price)
            
            if result and 'orderId' in result:
                # 等待成交
                time.sleep(2)
                order_info = self.client.query_order(SYMBOL, result['orderId'])
                
                if order_info and order_info.get('status') == 'FILLED':
                    filled_price = float(order_info.get('price', sell_price))
                    profit = (filled_price - grid.position['buy_price']) * quantity
                    grid.total_profit += profit
                    self.total_profit += profit
                    logging.info(f"✓ 清倉成交: {profit:+.6f} USDT")
        
        logging.info(f"網格 {grid.id} 已關閉")
        logging.info(f"  交易次數: {grid.trade_count}")
        logging.info(f"  總利潤: {grid.total_profit:+.6f} USDT")
        
        self.current_grid = None
    
    def display_status(self):
        """顯示狀態"""
        current_assets = self._get_total_assets()
        
        print_separator()
        logging.info("📊 USDC/USDT 固定網格套利 - 狀態報告")
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
            
            logging.info("💰 資產變化:")
            logging.info(f"  初始: {initial_value:.2f} USDT")
            logging.info(f"  當前: {current_value:.2f} USDT")
            logging.info(f"  盈虧: {change:+.4f} USDT ({percent:+.2f}%)")
            logging.info(f"  ├─ USDT: {current_assets['USDT']:.2f}")
            logging.info(f"  └─ USDC: {current_assets['USDC']:.4f}")
            logging.info("")
        
        logging.info("📈 策略統計:")
        logging.info(f"  累計套利: {self.total_trades} 次")
        logging.info(f"  已實現利潤: {self.total_profit:+.6f} USDT")
        logging.info("")
        
        if self.current_grid and self.current_grid.active:
            grid = self.current_grid
            logging.info("📋 當前網格:")
            logging.info(f"  {grid.id} @ ${grid.open_price:.4f}")
            logging.info(f"  買入價: ${grid.buy_price:.4f} | 賣出價: ${grid.sell_price:.4f}")
            
            if grid.position:
                logging.info(f"  持倉: {grid.position['quantity']:.4f} USDC @ ${grid.position['buy_price']:.4f}")
            else:
                logging.info(f"  持倉: 無")
            
            if grid.pending_order:
                logging.info(f"  掛單: {grid.pending_order['side']} {grid.pending_order['quantity']:.4f}")
            
            logging.info(f"  套利: {grid.trade_count} 次 | 利潤: {grid.total_profit:+.6f} USDT")
        else:
            logging.info("當前無活躍網格")
        
        print_separator()

def should_create_grid(last_create_minute):
    """判斷是否該創建網格"""
    if not ENABLE_SCHEDULE:
        return True, -1
    
    now = datetime.now()
    if now.minute in SCHEDULE_MINUTES and now.minute != last_create_minute and now.second < 10:
        return True, now.minute
    
    return False, last_create_minute

def main():
    logging.info("🚀 啟動 USDC/USDT 固定網格套利機器人...")
    
    client = MEXCClient(API_KEY, SECRET_KEY)
    
    # 測試連接
    logging.info("🔌 測試 API 連接...")
    test_price = client.get_price(SYMBOL)
    if not test_price:
        logging.error("❌ API 連接失敗")
        return
    
    logging.info(f"✓ API 連接成功，{SYMBOL} 當前價格: ${test_price:.4f}")
    
    # 檢查資金
    usdt = client.get_balance('USDT')
    usdc = client.get_balance('USDC')
    logging.info(f"💼 帳戶資產: USDT {usdt:.2f} | USDC {usdc:.4f}")
    
    total_assets = usdt + (usdc * test_price)
    required_capital = total_assets * CAPITAL_PERCENT
    
    if required_capital < 5:
        logging.error(f"❌ 資金不足！需要至少 10 USDT 總資產")
        return
    
    # 創建機器人
    bot = FixedGridBot(client)
    
    last_create_minute = -1
    last_display_time = time.time()
    
    try:
        while True:
            # 檢查是否創建新網格
            should_create, new_minute = should_create_grid(last_create_minute)
            if should_create:
                if not bot.current_grid or not bot.current_grid.active:
                    logging.info("⏰ 開單時間到，嘗試創建新網格...")
                    bot.create_grid()
                    last_create_minute = new_minute
            
            # 更新網格
            bot.update_grid()
            
            # 顯示狀態
            if time.time() - last_display_time >= DISPLAY_STATUS_INTERVAL:
                bot.display_status()
                last_display_time = time.time()
            
            time.sleep(CHECK_PRICE_INTERVAL)
    
    except KeyboardInterrupt:
        logging.info("⛔ 停止中...")
        
        if bot.current_grid and bot.current_grid.active:
            current_price = client.get_price(SYMBOL)
            bot.close_grid(bot.current_grid, current_price)
        
        final_assets = bot._get_total_assets()
        if final_assets and bot.initial_assets:
            print_separator()
            logging.info("📊 最終統計:")
            logging.info(f"  初始資產: {bot.initial_assets['total']:.2f} USDT")
            logging.info(f"  最終資產: {final_assets['total']:.2f} USDT")
            change = final_assets['total'] - bot.initial_assets['total']
            percent = (change / bot.initial_assets['total'] * 100) if bot.initial_assets['total'] > 0 else 0
            logging.info(f"  總盈虧: {change:+.4f} USDT ({percent:+.2f}%)")
            logging.info(f"  總套利: {bot.total_trades} 次")
            logging.info(f"  已實現利潤: {bot.total_profit:+.6f} USDT")
            print_separator()
        
        logging.info("👋 程序已退出")
    
    except Exception as e:
        logging.error(f"❌ 程序異常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()