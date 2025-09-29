#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEXC 網格交易策略 - 完整優化版
主要改進：
1. 加入訂單狀態檢查（確認成交才賣出）
2. 縮短邊界到 1%
3. 改成 10 分鐘開一單
4. 加入資產追蹤功能
"""

import requests
import time
import hashlib
import hmac
import json
import threading
import os
from urllib.parse import urlencode
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
import logging

# ================== 配置區域 - 請在此處修改參數 ==================

# MEXC API 配置
API_KEY = "mx0vglaUUDV1VP6KTU"
SECRET_KEY = "0e3a3cb6b0e24b0fbdf82d0c1e15c4b1"

# 交易配置
SYMBOL = "SOLUSDT"  # 交易對
INITIAL_CAPITAL = 50  # 初始資金 (USDT) - 建議最小 50 USDT
GRID_COUNT = 5  # 網格數量 (建議 3-5 格)
GRID_BOUNDARY_PERCENT = 0.01  # 網格邊界百分比 (1% = 0.01) ✅ 已優化

# 時間配置
TRADING_MINUTES = [0, 10, 20, 30, 40, 50]  # 10分鐘開一單 ✅ 已優化
PRICE_CHECK_INTERVAL = 0.5  # 價格檢查間隔 (秒)
DISPLAY_INTERVAL = 2.0  # 終端顯示間隔 (秒)
ORDER_CHECK_INTERVAL = 3.0  # 訂單狀態檢查間隔 (秒) ✅ 新增

# 安全配置
MIN_ORDER_VALUE = 1.0  # MEXC 官方最小訂單金額 (USDT)
SOL_MIN_QUANTITY = 0.047  # SOL最小交易數量
SKIP_BALANCE_CHECK = True  # 雲端部署時跳過餘額確認

# Debug 配置
DEBUG_MODE = True
LOG_FILE = "mexc_grid_trading.log"

# ==================== 配置區域結束 ====================

# 設置日誌
logging.basicConfig(
    level=logging.INFO if DEBUG_MODE else logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class MEXCTrader:
    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = "https://api.mexc.com"
        
    def _generate_signature(self, query_string):
        """生成 HMAC-SHA256 簽名"""
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method, endpoint, params=None):
        """發送請求"""
        if params is None:
            params = {}
        
        # 添加時間戳
        timestamp = int(time.time() * 1000)
        params['timestamp'] = timestamp
        
        # 清理空值參數
        clean_params = {}
        for k, v in params.items():
            if v is not None and str(v) != '':
                clean_params[k] = str(v)
        
        # 按字母順序排序參數
        sorted_params = dict(sorted(clean_params.items()))
        
        # 生成查詢字串用於簽名
        query_string = urlencode(sorted_params)
        
        # 生成簽名
        signature = self._generate_signature(query_string)
        sorted_params['signature'] = signature
        
        # 設置請求頭
        headers = {'X-MEXC-APIKEY': self.api_key}
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == 'POST':
                body_data = urlencode(sorted_params)
                response = requests.post(url, data=body_data, headers=headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, params=sorted_params, headers=headers, timeout=30)
            else:
                response = requests.get(url, params=sorted_params, headers=headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"請求失敗: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logging.error(f"請求異常: {e}")
            return None
    
    def get_current_price(self, symbol):
        """獲取當前價格"""
        endpoint = "/api/v3/ticker/price"
        params = {'symbol': symbol}
        result = self._make_request('GET', endpoint, params)
        if result and 'price' in result:
            return float(result['price'])
        return None
    
    def get_account_balance(self, asset):
        """獲取帳戶餘額"""
        endpoint = "/api/v3/account"
        result = self._make_request('GET', endpoint)
        if result and 'balances' in result:
            for balance in result['balances']:
                if balance['asset'] == asset:
                    return float(balance['free'])
        return 0
    
    def get_account_info(self):
        """獲取完整帳戶資訊 ✅ 新增"""
        endpoint = "/api/v3/account"
        return self._make_request('GET', endpoint)
    
    def place_maker_order(self, symbol, side, quantity, price):
        """下 MAKER 單"""
        endpoint = "/api/v3/order"
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'quantity': str(quantity),
            'price': str(price)
        }
        return self._make_request('POST', endpoint, params)
    
    def cancel_order(self, symbol, order_id):
        """取消訂單"""
        endpoint = "/api/v3/order"
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        return self._make_request('DELETE', endpoint, params)
    
    def get_open_orders(self, symbol):
        """獲取未完成訂單"""
        endpoint = "/api/v3/openOrders"
        params = {'symbol': symbol}
        return self._make_request('GET', endpoint, params)
    
    def query_order(self, symbol, order_id):
        """查詢訂單狀態 ✅ 新增"""
        endpoint = "/api/v3/order"
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        result = self._make_request('GET', endpoint, params)
        return result
    
    def place_maker_order_with_retry(self, symbol, side, quantity, price, max_retries=3):
        """下MAKER單並處理過單問題"""
        for attempt in range(max_retries):
            try:
                # 調整價格以確保MAKER訂單不會立即成交
                if side == 'BUY':
                    adjusted_price = price * 0.999
                else:
                    adjusted_price = price * 1.001
                
                adjusted_price = round(adjusted_price, 4)
                
                order_result = self.place_maker_order(symbol, side, quantity, adjusted_price)
                
                if order_result and 'orderId' in order_result:
                    logging.info(f"MAKER訂單成功: {side} {quantity} {symbol} @ {adjusted_price}")
                    return order_result
                else:
                    logging.warning(f"MAKER訂單失敗 (嘗試 {attempt + 1}/{max_retries}): {order_result}")
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        
            except Exception as e:
                logging.error(f"下單異常 (嘗試 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
        
        return None

class GridStrategy:
    def __init__(self, trader, symbol, initial_capital, grid_count, boundary_percent):
        self.trader = trader
        self.symbol = symbol
        self.initial_capital = initial_capital
        self.grid_count = grid_count
        self.boundary_percent = boundary_percent
        self.capital_per_grid = initial_capital / grid_count
        
        # 網格狀態
        self.grids = {}
        self.grid_counter = 0
        self.total_profit = 0
        self.total_trades = 0
        
        # 價格監控
        self.current_price = 0
        self.last_check_time = time.time()
        self.last_order_check_time = time.time()
        
        # ✅ 資產追蹤 - 新增
        self.initial_assets = {}
        self.current_assets = {}
        self.asset_change = {}
        self._record_initial_assets()
        
    def _record_initial_assets(self):
        """記錄初始資產 ✅ 新增"""
        try:
            usdt_balance = self.trader.get_account_balance('USDT')
            sol_balance = self.trader.get_account_balance('SOL')
            current_price = self.trader.get_current_price(self.symbol)
            
            if current_price:
                total_value = usdt_balance + (sol_balance * current_price)
                
                self.initial_assets = {
                    'USDT': usdt_balance,
                    'SOL': sol_balance,
                    'total_value': total_value,
                    'sol_price': current_price,
                    'timestamp': datetime.now()
                }
                
                logging.info(f"📊 初始資產記錄:")
                logging.info(f"  USDT: {usdt_balance:.2f}")
                logging.info(f"  SOL: {sol_balance:.4f}")
                logging.info(f"  總價值: {total_value:.2f} USDT")
        except Exception as e:
            logging.error(f"記錄初始資產失敗: {e}")
    
    def _update_current_assets(self):
        """更新當前資產 ✅ 新增"""
        try:
            usdt_balance = self.trader.get_account_balance('USDT')
            sol_balance = self.trader.get_account_balance('SOL')
            current_price = self.trader.get_current_price(self.symbol)
            
            if current_price and self.initial_assets:
                total_value = usdt_balance + (sol_balance * current_price)
                
                self.current_assets = {
                    'USDT': usdt_balance,
                    'SOL': sol_balance,
                    'total_value': total_value,
                    'sol_price': current_price
                }
                
                # 計算變化
                self.asset_change = {
                    'USDT': usdt_balance - self.initial_assets['USDT'],
                    'SOL': sol_balance - self.initial_assets['SOL'],
                    'total_value': total_value - self.initial_assets['total_value'],
                    'profit_percent': ((total_value - self.initial_assets['total_value']) / self.initial_assets['total_value'] * 100) if self.initial_assets['total_value'] > 0 else 0
                }
        except Exception as e:
            logging.error(f"更新當前資產失敗: {e}")
    
    def calculate_quantity(self, price):
        """計算下單數量，確保符合精度要求"""
        if 'SOL' in self.symbol:
            quantity = self.capital_per_grid / price
            quantity = max(quantity, SOL_MIN_QUANTITY)
            quantity = round(quantity, 3)
            
            order_value = quantity * price
            if order_value < MIN_ORDER_VALUE:
                quantity = (MIN_ORDER_VALUE / price) * 1.02
                quantity = round(quantity, 3)
            
            return quantity
        
        quantity = self.capital_per_grid / price
        return round(quantity, 6)
    
    def check_order_filled(self, symbol, order_id):
        """檢查訂單是否成交 ✅ 新增"""
        try:
            order_info = self.trader.query_order(symbol, order_id)
            if order_info and 'status' in order_info:
                status = order_info['status']
                # FILLED = 完全成交, PARTIALLY_FILLED = 部分成交
                if status == 'FILLED':
                    return True, order_info
                elif status == 'PARTIALLY_FILLED':
                    logging.info(f"訂單 {order_id} 部分成交，等待完全成交")
                    return False, order_info
                elif status == 'NEW':
                    return False, order_info
                else:
                    logging.warning(f"訂單 {order_id} 狀態: {status}")
                    return False, order_info
            return False, None
        except Exception as e:
            logging.error(f"檢查訂單狀態異常: {e}")
            return False, None
    
    def create_new_grid(self):
        """創建新網格"""
        current_price = self.trader.get_current_price(self.symbol)
        if not current_price:
            logging.error("無法獲取當前價格，跳過網格創建")
            return None
        
        if self.capital_per_grid < MIN_ORDER_VALUE:
            logging.error(f"❌ 每格資金 {self.capital_per_grid:.2f} USDT 低於最小要求 {MIN_ORDER_VALUE} USDT")
            return None
        
        usdt_balance = self.trader.get_account_balance('USDT')
        logging.info(f"💰 當前 USDT 餘額: {usdt_balance:.2f} USDT")
        
        if usdt_balance < self.capital_per_grid * 1.1:
            logging.warning(f"⚠️  USDT 餘額不足: {usdt_balance:.2f} < {self.capital_per_grid * 1.1:.2f}")
            return None
        
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        price_range = current_price * self.boundary_percent
        lower_bound = current_price - price_range
        upper_bound = current_price + price_range
        grid_step = (upper_bound - lower_bound) / self.grid_count
        
        logging.info(f"創建網格 {grid_id}:")
        logging.info(f"  當前價格: ${current_price:.4f}")
        logging.info(f"  價格範圍: ${lower_bound:.4f} - ${upper_bound:.4f}")
        logging.info(f"  網格間距: ${grid_step:.4f}")
        
        grid_info = {
            'id': grid_id,
            'start_price': current_price,
            'lower_bound': lower_bound,
            'upper_bound': upper_bound,
            'grid_step': grid_step,
            'current_level': 2,
            'positions': {},
            'profit': 0,
            'trade_count': 0,
            'created_time': datetime.now(),
            'active': True,
            'last_update': time.time()
        }
        
        initial_level = 2
        initial_price = lower_bound + initial_level * grid_step
        quantity = self.calculate_quantity(initial_price)
        
        order_value = quantity * initial_price
        if order_value < MIN_ORDER_VALUE:
            logging.error(f"❌ 初始訂單金額 {order_value:.2f} 低於最小要求 {MIN_ORDER_VALUE}")
            return None
        
        logging.info(f"  📍 初始買單: {quantity:.3f} SOL @ ${initial_price:.4f} (總額 ${order_value:.2f})")
        
        order_result = self.trader.place_maker_order_with_retry(
            self.symbol, 'BUY', quantity, initial_price
        )
        
        if order_result and 'orderId' in order_result:
            grid_info['positions'][initial_level] = {
                'order_id': order_result['orderId'],
                'quantity': quantity,
                'price': initial_price,
                'side': 'BUY',
                'status': 'NEW',  # ✅ 記錄訂單狀態
                'filled': False,  # ✅ 是否成交
                'created_time': time.time()
            }
            self.grids[grid_id] = grid_info
            logging.info(f"✅ 網格 {grid_id} 創建成功，訂單ID: {order_result['orderId']}")
            return grid_id
        else:
            logging.error(f"❌ 網格創建失敗: {order_result}")
            return None
    
    def check_all_orders_status(self):
        """檢查所有訂單狀態 ✅ 新增"""
        current_time = time.time()
        if current_time - self.last_order_check_time < ORDER_CHECK_INTERVAL:
            return
        
        self.last_order_check_time = current_time
        
        for grid_id, grid in self.grids.items():
            if not grid['active']:
                continue
            
            for level, position in list(grid['positions'].items()):
                if position['side'] == 'BUY' and not position.get('filled', False):
                    # 檢查買單是否成交
                    is_filled, order_info = self.check_order_filled(
                        self.symbol, 
                        position['order_id']
                    )
                    
                    if is_filled:
                        position['filled'] = True
                        position['status'] = 'FILLED'
                        logging.info(f"✅ 網格 {grid_id} 級別 {level} 買單已成交")
    
    def update_grids(self):
        """更新所有網格狀態"""
        current_price = self.trader.get_current_price(self.symbol)
        if not current_price:
            return
        
        self.current_price = current_price
        
        for grid_id, grid_info in list(self.grids.items()):
            if not grid_info['active']:
                continue
            
            self.update_single_grid(grid_id, current_price)
    
    def update_single_grid(self, grid_id, current_price):
        """更新單個網格"""
        grid = self.grids[grid_id]
        
        if current_price <= grid['lower_bound'] or current_price >= grid['upper_bound']:
            self.close_grid(grid_id)
            return
        
        target_level = int((current_price - grid['lower_bound']) / grid['grid_step'])
        target_level = max(0, min(self.grid_count - 1, target_level))
        
        current_level = grid['current_level']
        
        if target_level != current_level:
            self.execute_grid_trade(grid_id, current_level, target_level)
    
    def execute_grid_trade(self, grid_id, from_level, to_level):
        """執行網格交易"""
        grid = self.grids[grid_id]
        
        if to_level > from_level:
            # 價格上漲 - 賣出已成交的買單
            for level in range(from_level, to_level):
                if level in grid['positions']:
                    position = grid['positions'][level]
                    # ✅ 關鍵修正：只賣出已成交的持倉
                    if position['side'] == 'BUY' and position.get('filled', False):
                        self.sell_position(grid_id, level)
                    elif position['side'] == 'BUY' and not position.get('filled', False):
                        # 買單未成交，取消訂單
                        logging.info(f"取消未成交買單: 網格 {grid_id} 級別 {level}")
                        self.trader.cancel_order(self.symbol, position['order_id'])
                        del grid['positions'][level]
            
            # 在新位置買入
            new_price = grid['lower_bound'] + to_level * grid['grid_step']
            quantity = self.calculate_quantity(new_price)
            
            order_result = self.trader.place_maker_order_with_retry(
                self.symbol, 'BUY', quantity, new_price
            )
            
            if order_result and 'orderId' in order_result:
                grid['positions'][to_level] = {
                    'order_id': order_result['orderId'],
                    'quantity': quantity,
                    'price': new_price,
                    'side': 'BUY',
                    'status': 'NEW',
                    'filled': False,
                    'created_time': time.time()
                }
        
        elif to_level < from_level:
            # 價格下跌 - 在新位置買入
            for level in range(to_level, from_level):
                if level not in grid['positions']:
                    new_price = grid['lower_bound'] + level * grid['grid_step']
                    quantity = self.calculate_quantity(new_price)
                    
                    order_result = self.trader.place_maker_order_with_retry(
                        self.symbol, 'BUY', quantity, new_price
                    )
                    
                    if order_result and 'orderId' in order_result:
                        grid['positions'][level] = {
                            'order_id': order_result['orderId'],
                            'quantity': quantity,
                            'price': new_price,
                            'side': 'BUY',
                            'status': 'NEW',
                            'filled': False,
                            'created_time': time.time()
                        }
        
        grid['current_level'] = to_level
    
    def sell_position(self, grid_id, level):
        """賣出指定位置 - 只賣出已成交的持倉 ✅ 已修正"""
        grid = self.grids[grid_id]
        if level in grid['positions']:
            position = grid['positions'][level]
            
            # 確認是已成交的買單
            if position['side'] != 'BUY' or not position.get('filled', False):
                logging.warning(f"網格 {grid_id} 級別 {level} 無法賣出：未成交或非買單")
                return
            
            # 下賣單
            sell_price = position['price'] * 1.002
            order_result = self.trader.place_maker_order_with_retry(
                self.symbol, 'SELL', position['quantity'], sell_price
            )
            
            if order_result:
                profit = (sell_price - position['price']) * position['quantity']
                grid['profit'] += profit
                grid['trade_count'] += 1
                self.total_profit += profit
                self.total_trades += 1
                
                del grid['positions'][level]
                logging.info(f"✅ 網格 {grid_id} 賣出級別 {level}，利潤: {profit:.4f} USDT")
    
    def close_grid(self, grid_id):
        """關閉網格"""
        grid = self.grids[grid_id]
        grid['active'] = False
        
        for level, position in grid['positions'].items():
            self.trader.cancel_order(self.symbol, position['order_id'])
        
        logging.info(f"網格 {grid_id} 已關閉，總利潤: {grid['profit']:.4f}，交易次數: {grid['trade_count']}")
    
    def get_status_report(self):
        """獲取狀態報告 ✅ 加入資產追蹤"""
        # 更新當前資產
        self._update_current_assets()
        
        report = []
        report.append(f"{'='*20} MEXC 網格交易狀態 {'='*20}")
        report.append(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"當前價格: ${self.current_price:.4f} USDT")
        report.append("")
        
        # ✅ 資產變化報告
        if self.initial_assets and self.current_assets:
            report.append("💰 資產變化:")
            report.append(f"  初始總資產: {self.initial_assets['total_value']:.2f} USDT")
            report.append(f"  當前總資產: {self.current_assets['total_value']:.2f} USDT")
            
            change = self.asset_change['total_value']
            percent = self.asset_change['profit_percent']
            change_symbol = "📈" if change >= 0 else "📉"
            change_prefix = "+" if change >= 0 else ""
            
            report.append(f"  資產變化: {change_prefix}{change:.2f} USDT ({change_prefix}{percent:.2f}%) {change_symbol}")
            report.append(f"  USDT: {self.current_assets['USDT']:.2f} ({change_prefix}{self.asset_change['USDT']:.2f})")
            report.append(f"  SOL: {self.current_assets['SOL']:.4f} ({change_prefix}{self.asset_change['SOL']:.4f})")
            report.append("")
        
        report.append(f"策略統計:")
        report.append(f"  累計利潤: {self.total_profit:.4f} USDT")
        report.append(f"  交易次數: {self.total_trades}")
        
        active_grids = [g for g in self.grids.values() if g['active']]
        report.append(f"  活躍網格: {len(active_grids)}")
        report.append("")
        
        if not active_grids:
            report.append("📝 當前無活躍網格")
            report.append("⏰ 等待下一個開單時間點...")
        else:
            report.append("📊 網格詳情:")
            for grid in active_grids:
                grid_id = grid['id']
                current_level = grid['current_level']
                position_count = len(grid['positions'])
                
                # 統計已成交訂單數
                filled_count = sum(1 for p in grid['positions'].values() if p.get('filled', False))
                
                price_range = f"${grid['lower_bound']:.2f}-${grid['upper_bound']:.2f}"
                profit_str = f"+{grid['profit']:.4f}" if grid['profit'] > 0 else f"{grid['profit']:.4f}"
                
                runtime = datetime.now() - grid['created_time']
                runtime_str = f"{runtime.seconds//60}分{runtime.seconds%60}秒"
                
                status_line = (f"🟢 {grid_id}: 級別{current_level} | "
                             f"持倉{position_count}(成交{filled_count}) | 利潤{profit_str}💰 | "
                             f"範圍{price_range} | 交易{grid['trade_count']}次 | "
                             f"運行{runtime_str}")
                report.append(status_line)
        
        report.append("")
        report.append(f"💡 配置: {self.grid_count}網格 | ±{self.boundary_percent*100:.1f}%邊界 | {self.initial_capital}U本金")
        
        now = datetime.now()
        next_minute = None
        for minute in TRADING_MINUTES:
            if minute > now.minute:
                next_minute = minute
                break
        if next_minute is None:
            next_minute = TRADING_MINUTES[0] + 60
        
        time_to_next = next_minute - now.minute
        if time_to_next <= 0:
            time_to_next += 60
        report.append(f"⏰ 下次開單: {time_to_next}分鐘後")
        
        return "\n".join(report)

def should_create_new_grid():
    """檢查是否應該創建新網格"""
    now = datetime.now()
    if now.minute in TRADING_MINUTES and now.second < 5:
        return True
    return False

def main():
    if INITIAL_CAPITAL / GRID_COUNT < MIN_ORDER_VALUE:
        error_msg = f"❌ 配置錯誤: 每格資金 {INITIAL_CAPITAL/GRID_COUNT:.2f} USDT 低於最小要求 {MIN_ORDER_VALUE} USDT"
        logging.error(error_msg)
        logging.error(f"請將 INITIAL_CAPITAL 調整至至少 {MIN_ORDER_VALUE * GRID_COUNT} USDT")
        return
    
    trader = MEXCTrader(API_KEY, SECRET_KEY)
    
    logging.info("🔌 測試API連接...")
    test_price = trader.get_current_price(SYMBOL)
    if not test_price:
        logging.error("❌ API連接失敗，請檢查API密鑰")
        return
    
    logging.info(f"✓ API連接成功，當前 {SYMBOL} 價格: ${test_price:.4f}")
    
    if not SKIP_BALANCE_CHECK:
        usdt_balance = trader.get_account_balance('USDT')
        if usdt_balance < INITIAL_CAPITAL * 1.2:
            warning_msg = f"⚠️  警告: USDT餘額 {usdt_balance:.2f} 可能不足，建議至少 {INITIAL_CAPITAL*1.2:.2f} USDT"
            logging.warning(warning_msg)
            logging.warning("雲端部署模式：繼續執行")
    else:
        logging.info("⏭️  跳過餘額檢查（雲端部署模式）")
    
    strategy = GridStrategy(trader, SYMBOL, INITIAL_CAPITAL, GRID_COUNT, GRID_BOUNDARY_PERCENT)
    
    logging.info("🚀 MEXC 網格交易策略啟動")
    logging.info(f"📊 配置: {GRID_COUNT}網格 | ±{GRID_BOUNDARY_PERCENT*100}%邊界 | {INITIAL_CAPITAL}U本金")
    logging.info(f"⏰ 開單時間: 每小時 {TRADING_MINUTES} 分")
    logging.info("雲端部署模式運行中...")
    
    last_grid_create_minute = -1
    last_display_time = time.time()
    
    try:
        while True:
            current_time = time.time()
            now = datetime.now()
            
            # 檢查是否需要創建新網格
            if (should_create_new_grid() and 
                now.minute != last_grid_create_minute):
                logging.info(f"🕐 {now.strftime('%H:%M')} - 創建新網格...")
                grid_id = strategy.create_new_grid()
                if grid_id:
                    logging.info(f"✅ 網格 {grid_id} 創建成功")
                else:
                    logging.error(f"❌ 網格創建失敗")
                last_grid_create_minute = now.minute
            
            # ✅ 檢查所有訂單狀態
            strategy.check_all_orders_status()
            
            # 更新網格狀態
            strategy.update_grids()
            
            # 顯示狀態
            if current_time - last_display_time >= DISPLAY_INTERVAL:
                status_report = strategy.get_status_report()
                logging.info(f"\n{status_report}")
                last_display_time = current_time
            
            time.sleep(PRICE_CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        logging.info("\n🛑 程序被用戶中斷，正在安全關閉...")
        active_grids = [grid_id for grid_id, grid in strategy.grids.items() if grid['active']]
        if active_grids:
            logging.info(f"🔄 正在關閉 {len(active_grids)} 個活躍網格...")
            for grid_id in active_grids:
                strategy.close_grid(grid_id)
            logging.info("✅ 所有網格已安全關閉")
        
        # 顯示最終資產報告
        strategy._update_current_assets()
        if strategy.asset_change:
            logging.info("\n" + "="*50)
            logging.info("📊 最終資產報告:")
            logging.info(f"初始資產: {strategy.initial_assets['total_value']:.2f} USDT")
            logging.info(f"最終資產: {strategy.current_assets['total_value']:.2f} USDT")
            change = strategy.asset_change['total_value']
            percent = strategy.asset_change['profit_percent']
            logging.info(f"總變化: {'+' if change >= 0 else ''}{change:.2f} USDT ({'+' if change >= 0 else ''}{percent:.2f}%)")
            logging.info("="*50)
        
        logging.info("👋 程序已退出")
    except Exception as e:
        logging.error(f"程序異常: {e}", exc_info=True)
        try:
            for grid_id in list(strategy.grids.keys()):
                if strategy.grids[grid_id]['active']:
                    strategy.close_grid(grid_id)
        except:
            pass
        raise

if __name__ == "__main__":
    main()