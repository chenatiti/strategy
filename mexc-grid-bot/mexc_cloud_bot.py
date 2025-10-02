#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEXC 網格交易策略 - 最終修正版
主要改進：
1. 修正網格邏輯：在整個區間布滿掛單，不是一開始就買入
2. 資產追蹤包含 SOL 市值
3. 邊界改為 ±0.5%
4. 10 分鐘開一單
5. 資金不足時不開單
6. 確保每格至少 1.2 USDT
"""

import requests
import time
import hashlib
import hmac
import json
import os
from urllib.parse import urlencode
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
import logging

# ================== 配置區域 ==================

# MEXC API 配置
API_KEY = "mx0vglaUUDV1VP6KTU"
SECRET_KEY = "0e3a3cb6b0e24b0fbdf82d0c1e15c4b1"

# 交易配置
SYMBOL = "SOLUSDT"
GRID_COUNT = 10  # 網格數量
GRID_BOUNDARY_PERCENT = 0.005  # ±0.5%
MIN_CAPITAL_PER_GRID = 1.2  # 每格至少 1.2 USDT（留緩衝）

# 時間配置
TRADING_MINUTES = [0, 10, 20, 30, 40, 50]  # 10分鐘一次
PRICE_CHECK_INTERVAL = 0.5
DISPLAY_INTERVAL = 2.0
ORDER_CHECK_INTERVAL = 3.0

# 安全配置
MIN_ORDER_VALUE = 1.0  # MEXC 最小訂單金額
SKIP_BALANCE_CHECK = True

# Debug 配置
DEBUG_MODE = True
LOG_FILE = "mexc_grid_trading.log"

# ==================== 配置區域結束 ====================

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
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method, endpoint, params=None):
        if params is None:
            params = {}
        
        timestamp = int(time.time() * 1000)
        params['timestamp'] = timestamp
        
        clean_params = {}
        for k, v in params.items():
            if v is not None and str(v) != '':
                clean_params[k] = str(v)
        
        sorted_params = dict(sorted(clean_params.items()))
        query_string = urlencode(sorted_params)
        signature = self._generate_signature(query_string)
        sorted_params['signature'] = signature
        
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
        endpoint = "/api/v3/ticker/price"
        params = {'symbol': symbol}
        result = self._make_request('GET', endpoint, params)
        if result and 'price' in result:
            return float(result['price'])
        return None
    
    def get_account_balance(self, asset):
        endpoint = "/api/v3/account"
        result = self._make_request('GET', endpoint)
        if result and 'balances' in result:
            for balance in result['balances']:
                if balance['asset'] == asset:
                    return float(balance['free'])
        return 0
    
    def place_limit_order(self, symbol, side, quantity, price):
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
        endpoint = "/api/v3/order"
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        return self._make_request('DELETE', endpoint, params)
    
    def query_order(self, symbol, order_id):
        endpoint = "/api/v3/order"
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        return self._make_request('GET', endpoint, params)

class GridStrategy:
    def __init__(self, trader, symbol, grid_count, boundary_percent):
        self.trader = trader
        self.symbol = symbol
        self.grid_count = grid_count
        self.boundary_percent = boundary_percent
        
        self.grids = {}
        self.grid_counter = 0
        self.total_profit = 0
        self.total_trades = 0
        
        self.current_price = 0
        self.last_check_time = time.time()
        self.last_order_check_time = time.time()
        
        # 資產追蹤
        self.initial_assets = {}
        self.current_assets = {}
        self.asset_change = {}
        self._record_initial_assets()
        
    def _record_initial_assets(self):
        try:
            usdt = self.trader.get_account_balance('USDT')
            sol = self.trader.get_account_balance('SOL')
            price = self.trader.get_current_price(self.symbol)
            
            if price:
                total = usdt + (sol * price)
                self.initial_assets = {
                    'USDT': usdt,
                    'SOL': sol,
                    'total_value': total,
                    'sol_price': price,
                    'timestamp': datetime.now()
                }
                logging.info(f"初始資產: {total:.2f} USDT (USDT:{usdt:.2f} + SOL:{sol:.4f})")
        except Exception as e:
            logging.error(f"記錄初始資產失敗: {e}")
    
    def _update_current_assets(self):
        try:
            usdt = self.trader.get_account_balance('USDT')
            sol = self.trader.get_account_balance('SOL')
            price = self.trader.get_current_price(self.symbol)
            
            if price and self.initial_assets:
                total = usdt + (sol * price)
                self.current_assets = {
                    'USDT': usdt,
                    'SOL': sol,
                    'total_value': total,
                    'sol_price': price
                }
                
                self.asset_change = {
                    'USDT': usdt - self.initial_assets['USDT'],
                    'SOL': sol - self.initial_assets['SOL'],
                    'total_value': total - self.initial_assets['total_value'],
                    'profit_percent': ((total - self.initial_assets['total_value']) / self.initial_assets['total_value'] * 100) if self.initial_assets['total_value'] > 0 else 0
                }
        except Exception as e:
            logging.error(f"更新資產失敗: {e}")
    
    def get_available_capital(self):
        """獲取可用資金（USDT）"""
        return self.trader.get_account_balance('USDT')
    
    def calculate_quantity(self, price, order_value):
        """根據訂單金額計算數量"""
        quantity = order_value / price
        quantity = round(quantity, 3)
        return quantity
    
    def check_order_filled(self, symbol, order_id):
        try:
            order_info = self.trader.query_order(symbol, order_id)
            if order_info and 'status' in order_info:
                if order_info['status'] == 'FILLED':
                    return True, order_info
                return False, order_info
            return False, None
        except Exception as e:
            logging.error(f"檢查訂單失敗: {e}")
            return False, None
    
    def create_new_grid(self):
        """創建新網格 - 在整個區間布滿掛單"""
        current_price = self.trader.get_current_price(self.symbol)
        if not current_price:
            logging.error("無法獲取當前價格")
            return None
        
        # 檢查資金是否足夠
        available_usdt = self.get_available_capital()
        required_capital = MIN_CAPITAL_PER_GRID * self.grid_count
        
        if available_usdt < required_capital:
            logging.warning(f"資金不足: 需要 {required_capital:.2f} USDT，但只有 {available_usdt:.2f} USDT")
            return None
        
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        # 計算網格範圍
        price_range = current_price * self.boundary_percent
        lower_bound = current_price - price_range
        upper_bound = current_price + price_range
        grid_step = (upper_bound - lower_bound) / self.grid_count
        
        logging.info(f"創建網格 {grid_id}:")
        logging.info(f"  當前價格: ${current_price:.4f}")
        logging.info(f"  範圍: ${lower_bound:.4f} - ${upper_bound:.4f} (±{self.boundary_percent*100}%)")
        logging.info(f"  間距: ${grid_step:.4f}")
        logging.info(f"  可用資金: {available_usdt:.2f} USDT")
        
        grid_info = {
            'id': grid_id,
            'start_price': current_price,
            'lower_bound': lower_bound,
            'upper_bound': upper_bound,
            'grid_step': grid_step,
            'buy_orders': {},  # 買單掛單
            'sell_orders': {},  # 賣單掛單
            'filled_positions': {},  # 已成交持倉
            'profit': 0,
            'trade_count': 0,
            'created_time': datetime.now(),
            'active': True
        }
        
        # 在整個區間布滿買單（從低到高）
        orders_placed = 0
        for level in range(self.grid_count):
            buy_price = lower_bound + level * grid_step
            
            # 只在當前價格以下掛買單
            if buy_price < current_price:
                quantity = self.calculate_quantity(buy_price, MIN_CAPITAL_PER_GRID)
                
                order_result = self.trader.place_limit_order(
                    self.symbol, 'BUY', quantity, buy_price
                )
                
                if order_result and 'orderId' in order_result:
                    grid_info['buy_orders'][level] = {
                        'order_id': order_result['orderId'],
                        'quantity': quantity,
                        'price': buy_price,
                        'created_time': time.time()
                    }
                    orders_placed += 1
                    logging.info(f"  掛買單 Level {level}: {quantity:.3f} SOL @ ${buy_price:.4f}")
                    time.sleep(0.2)  # 避免請求過快
        
        if orders_placed > 0:
            self.grids[grid_id] = grid_info
            logging.info(f"網格 {grid_id} 創建成功，掛了 {orders_placed} 個買單")
            return grid_id
        else:
            logging.error("沒有成功掛任何買單")
            return None
    
    def check_all_orders_status(self):
        """檢查所有訂單狀態"""
        current_time = time.time()
        if current_time - self.last_order_check_time < ORDER_CHECK_INTERVAL:
            return
        
        self.last_order_check_time = current_time
        
        for grid_id, grid in self.grids.items():
            if not grid['active']:
                continue
            
            # 檢查買單是否成交
            for level, order in list(grid['buy_orders'].items()):
                is_filled, order_info = self.check_order_filled(
                    self.symbol, order['order_id']
                )
                
                if is_filled:
                    # 買單成交，移到已成交持倉，並掛賣單
                    grid['filled_positions'][level] = {
                        'quantity': order['quantity'],
                        'buy_price': order['price'],
                        'buy_time': time.time()
                    }
                    del grid['buy_orders'][level]
                    
                    # 在更高價位掛賣單
                    sell_price = order['price'] * 1.005  # 漲 0.5% 就賣
                    sell_order = self.trader.place_limit_order(
                        self.symbol, 'SELL', order['quantity'], sell_price
                    )
                    
                    if sell_order and 'orderId' in sell_order:
                        grid['sell_orders'][level] = {
                            'order_id': sell_order['orderId'],
                            'quantity': order['quantity'],
                            'price': sell_price,
                            'buy_price': order['price']
                        }
                        logging.info(f"網格 {grid_id} Level {level} 買單成交，掛賣單 @ ${sell_price:.4f}")
            
            # 檢查賣單是否成交
            for level, order in list(grid['sell_orders'].items()):
                is_filled, order_info = self.check_order_filled(
                    self.symbol, order['order_id']
                )
                
                if is_filled:
                    # 賣單成交，計算利潤，重新掛買單
                    profit = (order['price'] - order['buy_price']) * order['quantity']
                    grid['profit'] += profit
                    grid['trade_count'] += 1
                    self.total_profit += profit
                    self.total_trades += 1
                    
                    del grid['sell_orders'][level]
                    if level in grid['filled_positions']:
                        del grid['filled_positions'][level]
                    
                    # 重新在原位置掛買單
                    buy_price = order['buy_price']
                    quantity = self.calculate_quantity(buy_price, MIN_CAPITAL_PER_GRID)
                    
                    buy_order = self.trader.place_limit_order(
                        self.symbol, 'BUY', quantity, buy_price
                    )
                    
                    if buy_order and 'orderId' in buy_order:
                        grid['buy_orders'][level] = {
                            'order_id': buy_order['orderId'],
                            'quantity': quantity,
                            'price': buy_price,
                            'created_time': time.time()
                        }
                        logging.info(f"網格 {grid_id} Level {level} 完成交易，利潤 {profit:.4f} USDT，重新掛買單")
    
    def update_grids(self):
        """更新網格狀態"""
        current_price = self.trader.get_current_price(self.symbol)
        if not current_price:
            return
        
        self.current_price = current_price
        
        for grid_id, grid in list(self.grids.items()):
            if not grid['active']:
                continue
            
            # 檢查是否超出邊界
            if current_price <= grid['lower_bound'] or current_price >= grid['upper_bound']:
                self.close_grid(grid_id)
    
    def close_grid(self, grid_id):
        """關閉網格"""
        grid = self.grids[grid_id]
        grid['active'] = False
        
        # 取消所有買單
        for level, order in grid['buy_orders'].items():
            self.trader.cancel_order(self.symbol, order['order_id'])
        
        # 取消所有賣單並市價賣出持倉
        for level, order in grid['sell_orders'].items():
            self.trader.cancel_order(self.symbol, order['order_id'])
        
        logging.info(f"網格 {grid_id} 已關閉，總利潤: {grid['profit']:.4f} USDT，完成 {grid['trade_count']} 次交易")
    
    def get_status_report(self):
        """狀態報告"""
        self._update_current_assets()
        
        report = []
        report.append(f"{'='*20} MEXC 網格交易狀態 {'='*20}")
        report.append(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"當前價格: ${self.current_price:.4f}")
        report.append("")
        
        # 資產變化
        if self.initial_assets and self.current_assets:
            report.append("資產變化:")
            report.append(f"  初始: {self.initial_assets['total_value']:.2f} USDT")
            report.append(f"  當前: {self.current_assets['total_value']:.2f} USDT")
            
            change = self.asset_change['total_value']
            percent = self.asset_change['profit_percent']
            symbol = "+" if change >= 0 else ""
            
            report.append(f"  盈虧: {symbol}{change:.2f} USDT ({symbol}{percent:.2f}%)")
            report.append(f"  USDT: {self.current_assets['USDT']:.2f}")
            report.append(f"  SOL: {self.current_assets['SOL']:.4f}")
            report.append("")
        
        report.append(f"策略統計:")
        report.append(f"  累計利潤: {self.total_profit:.4f} USDT")
        report.append(f"  完成交易: {self.total_trades} 次")
        
        active_grids = [g for g in self.grids.values() if g['active']]
        report.append(f"  活躍網格: {len(active_grids)}")
        report.append("")
        
        if active_grids:
            for grid in active_grids:
                buy_count = len(grid['buy_orders'])
                sell_count = len(grid['sell_orders'])
                filled_count = len(grid['filled_positions'])
                
                runtime = datetime.now() - grid['created_time']
                runtime_str = f"{runtime.seconds//60}分"
                
                report.append(f"{grid['id']}: 買單{buy_count} | 賣單{sell_count} | 持倉{filled_count} | 利潤{grid['profit']:.4f} | {runtime_str}")
        else:
            report.append("當前無活躍網格，等待開單時間")
        
        report.append("")
        report.append(f"配置: {self.grid_count}格 | ±{self.boundary_percent*100}% | 每格{MIN_CAPITAL_PER_GRID}U")
        
        return "\n".join(report)

def should_create_new_grid():
    now = datetime.now()
    return now.minute in TRADING_MINUTES and now.second < 5

def main():
    trader = MEXCTrader(API_KEY, SECRET_KEY)
    
    logging.info("測試 API 連接...")
    test_price = trader.get_current_price(SYMBOL)
    if not test_price:
        logging.error("API 連接失敗")
        return
    
    logging.info(f"API 連接成功，當前 {SYMBOL} 價格: ${test_price:.4f}")
    
    strategy = GridStrategy(trader, SYMBOL, GRID_COUNT, GRID_BOUNDARY_PERCENT)
    
    logging.info("MEXC 網格交易策略啟動")
    logging.info(f"配置: {GRID_COUNT}格 | ±{GRID_BOUNDARY_PERCENT*100}% | 每格≥{MIN_CAPITAL_PER_GRID}U")
    logging.info(f"開單時間: {TRADING_MINUTES}")
    
    last_grid_create_minute = -1
    last_display_time = time.time()
    
    try:
        while True:
            current_time = time.time()
            now = datetime.now()
            
            # 檢查是否開新網格
            if (should_create_new_grid() and now.minute != last_grid_create_minute):
                logging.info(f"{now.strftime('%H:%M')} - 嘗試創建新網格...")
                grid_id = strategy.create_new_grid()
                if grid_id:
                    logging.info(f"網格 {grid_id} 創建成功")
                else:
                    logging.warning("網格創建失敗（可能資金不足）")
                last_grid_create_minute = now.minute
            
            # 檢查訂單狀態
            strategy.check_all_orders_status()
            
            # 更新網格
            strategy.update_grids()
            
            # 顯示狀態
            if current_time - last_display_time >= DISPLAY_INTERVAL:
                status = strategy.get_status_report()
                logging.info(f"\n{status}")
                last_display_time = current_time
            
            time.sleep(PRICE_CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        logging.info("\n程序停止，正在關閉網格...")
        active_grids = [gid for gid, g in strategy.grids.items() if g['active']]
        for grid_id in active_grids:
            strategy.close_grid(grid_id)
        
        strategy._update_current_assets()
        if strategy.asset_change:
            logging.info(f"\n最終資產: {strategy.current_assets['total_value']:.2f} USDT")
            logging.info(f"總盈虧: {strategy.asset_change['total_value']:.2f} USDT")
        
        logging.info("程序已退出")
    except Exception as e:
        logging.error(f"程序異常: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()