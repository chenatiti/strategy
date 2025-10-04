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
import json
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

# 網格設定
GRID_TICK = 0.0001  # 每個 TICK 的價格間距
CAPITAL_PER_LEVEL = 5.0  # 每層資金 5 USDT
MIN_CAPITAL_TO_OPEN = 10.0  # 開新網格最少需要 10 USDT

# 時間設定
CHECK_PRICE_INTERVAL = 0.5  # 檢查價格間隔（秒）- 快速響應
DISPLAY_STATUS_INTERVAL = 60  # 顯示狀態間隔（秒）

# 開單時間控制
ENABLE_SCHEDULE = True  # 是否啟用定時開單
SCHEDULE_MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]  # 每 5 分鐘

# DEBUG 模式
DEBUG_MODE = os.getenv('DEBUG_MODE', 'True').lower() == 'true'

# ==================== 配置區域結束 ====================

# 日誌設定
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
        self.market_order_method = None  # 記錄哪種 Market Order 方法可用
    
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
            
            if DEBUG_MODE:
                logging.debug(f"API {method} {endpoint}: {response.status_code}")
                if response.status_code != 200:
                    logging.debug(f"Response: {response.text}")
            
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
            price = float(result['price'])
            if DEBUG_MODE:
                logging.debug(f"當前價格: {price:.4f}")
            return price
        return None
    
    def get_balance(self, asset):
        """獲取餘額"""
        result = self._request('GET', "/api/v3/account")
        if result and 'balances' in result:
            for balance in result['balances']:
                if balance['asset'] == asset:
                    free = float(balance['free'])
                    if DEBUG_MODE:
                        logging.debug(f"{asset} 餘額: {free:.4f}")
                    return free
        return 0
    
    def place_market_order(self, symbol, side, amount_usdt=None, quantity=None):
        """
        下市價單 - 嘗試兩種方式
        方式 A: quoteOrderQty (指定花費的 USDT)
        方式 B: quantity (指定買入的 USDC 數量)
        """
        # 如果已經知道哪種方法可用，直接用
        if self.market_order_method == 'quoteOrderQty' and amount_usdt:
            result = self._place_market_order_quote(symbol, side, amount_usdt)
            if not result:
                logging.error(f"市價單失敗 (quoteOrderQty): side={side}, amount={amount_usdt}")
            return result
        elif self.market_order_method == 'quantity' and quantity:
            result = self._place_market_order_quantity(symbol, side, quantity)
            if not result:
                logging.error(f"市價單失敗 (quantity): side={side}, qty={quantity}")
            return result
        
        # 如果還不知道，先嘗試 quoteOrderQty
        if amount_usdt:
            logging.info(f"嘗試方式 A: quoteOrderQty = {amount_usdt} USDT")
            result = self._place_market_order_quote(symbol, side, amount_usdt)
            if result:
                self.market_order_method = 'quoteOrderQty'
                logging.info("✓ 方式 A 成功！之後都用這個方法")
                return result
            
            # 如果方式 A 失敗，嘗試方式 B
            if quantity:
                logging.info(f"方式 A 失敗，嘗試方式 B: quantity = {quantity}")
                result = self._place_market_order_quantity(symbol, side, quantity)
                if result:
                    self.market_order_method = 'quantity'
                    logging.info("✓ 方式 B 成功！之後都用這個方法")
                    return result
                else:
                    logging.error(f"兩種方式都失敗！檢查 API 回應")
        
        return None
    
    def _place_market_order_quote(self, symbol, side, amount_usdt):
        """方式 A: 使用 quoteOrderQty"""
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quoteOrderQty': str(amount_usdt)
        }
        return self._request('POST', "/api/v3/order", params)
    
    def _place_market_order_quantity(self, symbol, side, quantity):
        """方式 B: 使用 quantity"""
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': str(quantity)
        }
        return self._request('POST', "/api/v3/order", params)
    
    def query_order(self, symbol, order_id):
        """查詢訂單狀態"""
        return self._request('GET', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})

class GridLevel:
    """單個網格層級"""
    def __init__(self, price):
        self.price = round(price, 4)
        self.positions = []  # 該層的持倉列表
        self.trade_count = 0  # 該層的交易次數
        self.realized_profit = 0  # 該層的已實現利潤
    
    def add_position(self, quantity, buy_price, buy_time):
        """添加持倉"""
        self.positions.append({
            'quantity': quantity,
            'buy_price': buy_price,
            'buy_time': buy_time
        })
        logging.info(f"  Level {self.price:.4f}: 新增持倉 {quantity:.4f} USDC @ ${buy_price:.4f}")
    
    def sell_position(self, sell_price):
        """賣出該層最早的持倉"""
        if not self.positions:
            return 0
        
        position = self.positions.pop(0)
        quantity = position['quantity']
        profit = (sell_price - position['buy_price']) * quantity
        
        self.trade_count += 1
        self.realized_profit += profit
        
        logging.info(f"  Level {self.price:.4f}: 賣出 {quantity:.4f} USDC @ ${sell_price:.4f}")
        logging.info(f"    利潤: {profit:.6f} USDT (買入價: ${position['buy_price']:.4f})")
        
        return profit
    
    def has_position(self):
        """是否有持倉"""
        return len(self.positions) > 0
    
    def total_quantity(self):
        """總持倉數量"""
        return sum(p['quantity'] for p in self.positions)
    
    def unrealized_pnl(self, current_price):
        """未實現盈虧"""
        total = 0
        for pos in self.positions:
            total += (current_price - pos['buy_price']) * pos['quantity']
        return total

class MovingGrid:
    """單個移動網格"""
    def __init__(self, grid_id, open_price):
        self.id = grid_id
        self.open_price = round(open_price, 4)
        self.created_time = datetime.now()
        self.active = True
        
        # 計算網格邊界
        self.upper_bound = round(open_price + GRID_TICK, 4)  # 0.9996
        self.lower_bound = round(open_price - GRID_TICK, 4)  # 0.9994
        self.close_upper = round(open_price + 2 * GRID_TICK, 4)  # 0.9997
        self.close_lower = round(open_price - 2 * GRID_TICK, 4)  # 0.9993
        
        # 三個層級
        self.levels = {
            self.upper_bound: GridLevel(self.upper_bound),
            self.open_price: GridLevel(self.open_price),
            self.lower_bound: GridLevel(self.lower_bound)
        }
        
        self.total_profit = 0
        self.total_trades = 0
    
    def get_level(self, price):
        """獲取最接近的層級"""
        price = round(price, 4)
        for level_price in self.levels.keys():
            if abs(price - level_price) < GRID_TICK / 2:
                return self.levels[level_price]
        return None
    
    def should_close(self, current_price):
        """是否應該關閉網格"""
        return current_price <= self.close_lower or current_price >= self.close_upper
    
    def get_summary(self, current_price):
        """獲取網格摘要"""
        unrealized = sum(level.unrealized_pnl(current_price) for level in self.levels.values())
        
        positions_info = []
        for price, level in sorted(self.levels.items()):
            if level.has_position():
                positions_info.append(f"{price:.4f}({level.total_quantity():.4f})")
        
        return {
            'positions': ' + '.join(positions_info) if positions_info else '無持倉',
            'realized': self.total_profit,
            'unrealized': unrealized,
            'trades': self.total_trades
        }

class USDCUSDTGridBot:
    def __init__(self, client):
        self.client = client
        self.grids = {}
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
        logging.info("🚀 USDC/USDT 移動網格交易機器人")
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
            logging.info(f"  網格間距: ±{GRID_TICK:.4f}")
            logging.info(f"  每層資金: {CAPITAL_PER_LEVEL:.1f} USDT")
            logging.info(f"  開單條件: 餘額 >= {MIN_CAPITAL_TO_OPEN:.1f} USDT")
            logging.info(f"  開單時間: 每小時 {SCHEDULE_MINUTES} 分")
            logging.info(f"  查價間隔: {CHECK_PRICE_INTERVAL} 秒")
            logging.info(f"  DEBUG 模式: {'開啟' if DEBUG_MODE else '關閉'}")
        print_separator()
    
    def create_grid(self):
        """創建新網格"""
        current_price = self.client.get_price(SYMBOL)
        if not current_price:
            logging.error("無法獲取當前價格")
            return None
        
        usdt_balance = self.client.get_balance('USDT')
        if usdt_balance < MIN_CAPITAL_TO_OPEN:
            logging.warning(f"💸 資金不足: 需要 {MIN_CAPITAL_TO_OPEN} USDT，只有 {usdt_balance:.2f} USDT")
            return None
        
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        print_separator()
        logging.info(f"📊 創建網格 {grid_id}")
        logging.info(f"開單價格: ${current_price:.4f}")
        
        # 創建網格對象
        grid = MovingGrid(grid_id, current_price)
        
        logging.info(f"網格範圍: ${grid.lower_bound:.4f} - ${grid.upper_bound:.4f}")
        logging.info(f"關閉條件: < ${grid.close_lower:.4f} 或 > ${grid.close_upper:.4f}")
        logging.info("")
        
        # 在開單價買入第一份
        success = self._buy_at_level(grid, current_price)
        
        if success:
            self.grids[grid_id] = grid
            logging.info(f"✓ 網格 {grid_id} 創建成功")
            print_separator()
            return grid_id
        else:
            logging.error(f"✗ 網格 {grid_id} 創建失敗")
            print_separator()
            return None
    
    def _buy_at_level(self, grid, price):
        """在指定價格層級買入"""
        level = grid.get_level(price)
        if not level:
            logging.error(f"價格 {price:.4f} 不在網格層級內")
            return False
        
        # 計算買入數量
        quantity = round(CAPITAL_PER_LEVEL / price, 4)
        
        logging.info(f"🛒 買入: {CAPITAL_PER_LEVEL:.2f} USDT @ ${price:.4f} (約 {quantity:.4f} USDC)")
        
        # 下市價單
        result = self.client.place_market_order(
            SYMBOL, 
            'BUY', 
            amount_usdt=CAPITAL_PER_LEVEL,
            quantity=quantity
        )
        
        if not result or 'orderId' not in result:
            logging.error(f"買入失敗: {result}")
            return False
        
        # 查詢訂單詳情
        time.sleep(0.5)
        order_info = self.client.query_order(SYMBOL, result['orderId'])
        
        if order_info and order_info.get('status') == 'FILLED':
            filled_qty = float(order_info.get('executedQty', quantity))
            filled_price = float(order_info.get('cummulativeQuoteQty', CAPITAL_PER_LEVEL)) / filled_qty
            
            level.add_position(filled_qty, filled_price, time.time())
            logging.info(f"✓ 買入成功: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
            return True
        else:
            logging.error(f"訂單未成交: {order_info.get('status') if order_info else 'Unknown'}")
            return False
    
    def _sell_at_level(self, grid, level, price):
        """在指定層級賣出"""
        if not level.has_position():
            return False
        
        # 獲取持倉數量
        quantity = level.positions[0]['quantity']
        
        logging.info(f"💰 賣出: {quantity:.4f} USDC @ ${price:.4f}")
        
        # 下市價單
        result = self.client.place_market_order(
            SYMBOL,
            'SELL',
            quantity=quantity
        )
        
        if not result or 'orderId' not in result:
            logging.error(f"❌ 賣出失敗!")
            logging.error(f"   交易對: {SYMBOL}")
            logging.error(f"   數量: {quantity:.4f} USDC")
            logging.error(f"   價格: ${price:.4f}")
            logging.error(f"   API 回應: {result}")
            return False
        
        # 查詢訂單詳情
        time.sleep(0.5)
        order_info = self.client.query_order(SYMBOL, result['orderId'])
        
        if order_info and order_info.get('status') == 'FILLED':
            filled_price = float(order_info.get('cummulativeQuoteQty', 0)) / quantity
            
            profit = level.sell_position(filled_price)
            grid.total_profit += profit
            grid.total_trades += 1
            self.total_profit += profit
            self.total_trades += 1
            
            logging.info(f"✓ 賣出成功: 利潤 {profit:.6f} USDT")
            return True
        else:
            status = order_info.get('status') if order_info else 'Unknown'
            logging.error(f"❌ 訂單未成交: {status}")
            logging.error(f"   訂單資訊: {order_info}")
            return False
    
    def update_grids(self):
        """更新所有網格"""
        current_price = self.client.get_price(SYMBOL)
        if not current_price:
            return
        
        for grid_id, grid in list(self.grids.items()):
            if not grid.active:
                continue
            
            # 檢查是否需要關閉
            if grid.should_close(current_price):
                logging.info(f"⚠️  價格超出範圍，關閉網格 {grid_id}")
                self.close_grid(grid_id, current_price)
                continue
            
            # 更新網格狀態
            self._update_single_grid(grid, current_price)
    
    def _update_single_grid(self, grid, current_price):
        """更新單個網格"""
        current_price = round(current_price, 4)
        
        # 判斷當前在哪個層級
        if abs(current_price - grid.upper_bound) < GRID_TICK / 2:
            # 在上層 0.9996
            self._handle_upper_level(grid, current_price)
        elif abs(current_price - grid.open_price) < GRID_TICK / 2:
            # 在中層 0.9995
            self._handle_middle_level(grid, current_price)
        elif abs(current_price - grid.lower_bound) < GRID_TICK / 2:
            # 在下層 0.9994
            self._handle_lower_level(grid, current_price)
    
    def _handle_upper_level(self, grid, price):
        """處理上層 (0.9996) - 賣出中層持倉"""
        middle_level = grid.levels[grid.open_price]
        
        if middle_level.has_position():
            logging.info(f"📈 價格上漲到 {price:.4f}，賣出中層持倉")
            self._sell_at_level(grid, middle_level, price)
    
    def _handle_middle_level(self, grid, price):
        """處理中層 (0.9995) - 賣出下層持倉 或 買入中層"""
        lower_level = grid.levels[grid.lower_bound]
        middle_level = grid.levels[grid.open_price]
        
        # 如果下層有持倉，賣出下層
        if lower_level.has_position():
            logging.info(f"📈 價格回升到 {price:.4f}，賣出下層持倉")
            self._sell_at_level(grid, lower_level, price)
        
        # 如果中層沒持倉且資金足夠，買入中層
        elif not middle_level.has_position():
            usdt_balance = self.client.get_balance('USDT')
            if usdt_balance >= CAPITAL_PER_LEVEL:
                logging.info(f"💹 價格在 {price:.4f}，中層無持倉，買入")
                self._buy_at_level(grid, price)
    
    def _handle_lower_level(self, grid, price):
        """處理下層 (0.9994) - 買入下層"""
        lower_level = grid.levels[grid.lower_bound]
        
        # 如果下層沒持倉且資金足夠，買入
        if not lower_level.has_position():
            usdt_balance = self.client.get_balance('USDT')
            if usdt_balance >= CAPITAL_PER_LEVEL:
                logging.info(f"📉 價格下跌到 {price:.4f}，買入下層")
                self._buy_at_level(grid, price)
    
    def close_grid(self, grid_id, current_price):
        """關閉網格"""
        grid = self.grids[grid_id]
        grid.active = False
        
        logging.info(f"🔴 關閉網格 {grid_id}")
        
        # 賣出所有持倉
        for level in grid.levels.values():
            while level.has_position():
                self._sell_at_level(grid, level, current_price)
        
        logging.info(f"網格 {grid_id} 統計:")
        logging.info(f"  總交易: {grid.total_trades} 次")
        logging.info(f"  總利潤: {grid.total_profit:.6f} USDT")
    
    def display_status(self):
        """顯示詳細狀態"""
        current_assets = self._get_total_assets()
        current_price = current_assets['price'] if current_assets else None
        
        print_separator()
        logging.info("📊 USDC/USDT 移動網格交易機器人 - 狀態報告")
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
            symbol = "+" if change >= 0 else ""
            
            logging.info("💰 資產變化:")
            logging.info(f"  初始總值: {initial_value:.2f} USDT")
            logging.info(f"  當前總值: {current_value:.2f} USDT")
            logging.info(f"  總盈虧: {symbol}{change:.4f} USDT ({symbol}{percent:.2f}%)")
            logging.info(f"  ├─ USDT: {current_assets['USDT']:.2f}")
            logging.info(f"  └─ USDC: {current_assets['USDC']:.4f} (≈ {current_assets['USDC'] * current_price:.2f} USDT)")
            logging.info("")
        
        logging.info("📈 策略統計:")
        logging.info(f"  累計套利: {self.total_trades} 次")
        logging.info(f"  已實現利潤: {self.total_profit:.6f} USDT")
        
        active_grids = [g for g in self.grids.values() if g.active]
        logging.info(f"  活躍網格: {len(active_grids)} 個")
        logging.info("")
        
        if active_grids and current_price:
            logging.info("📋 網格詳情:")
            total_unrealized = 0
            
            for grid in active_grids:
                summary = grid.get_summary(current_price)
                total_unrealized += summary['unrealized']
                
                logging.info(f"  {grid.id} @ ${grid.open_price:.4f}:")
                logging.info(f"    持倉: {summary['positions']}")
                logging.info(f"    套利: {summary['trades']} 次")
                logging.info(f"    已實現: {summary['realized']:.6f} USDT")
                logging.info(f"    未實現: {summary['unrealized']:+.6f} USDT")
            
            logging.info("")
            logging.info(f"  總未實現盈虧: {total_unrealized:+.6f} USDT")
        else:
            logging.info("  當前無活躍網格")
        
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
    logging.info("🚀 啟動 USDC/USDT 移動網格交易機器人...")
    
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
    
    if usdt < MIN_CAPITAL_TO_OPEN:
        logging.error(f"❌ USDT 不足！需要至少 {MIN_CAPITAL_TO_OPEN} USDT")
        return
    
    # 創建機器人
    bot = USDCUSDTGridBot(client)
    
    last_create_minute = -1
    last_display_time = time.time()
    
    try:
        while True:
            # 檢查是否創建新網格
            should_create, new_minute = should_create_grid(last_create_minute)
            if should_create:
                active_grids = [g for g in bot.grids.values() if g.active]
                if len(active_grids) == 0 or not ENABLE_SCHEDULE:
                    logging.info("⏰ 開單時間到，嘗試創建新網格...")
                    bot.create_grid()
                    last_create_minute = new_minute
            
            # 更新網格
            bot.update_grids()
            
            # 顯示狀態
            if time.time() - last_display_time >= DISPLAY_STATUS_INTERVAL:
                bot.display_status()
                last_display_time = time.time()
            
            time.sleep(CHECK_PRICE_INTERVAL)
    
    except KeyboardInterrupt:
        logging.info("⛔ 停止中，正在關閉所有網格...")
        current_price = client.get_price(SYMBOL)
        
        active_grids = [gid for gid, g in bot.grids.items() if g.active]
        for grid_id in active_grids:
            bot.close_grid(grid_id, current_price)
        
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
            logging.info(f"  已實現利潤: {bot.total_profit:.6f} USDT")
            print_separator()
        
        logging.info("👋 程序已退出")
    
    except Exception as e:
        logging.error(f"❌ 程序異常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()