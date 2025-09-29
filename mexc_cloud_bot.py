#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MEXC 網格交易策略 - 雲端部署修復版
主要修復：移除所有 input() 互動式輸入，適配雲端環境
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
INITIAL_CAPITAL = 100  # 初始資金 (USDT) - 建議最小50 USDT
GRID_COUNT = 10  # 網格數量 (0-10級)
GRID_BOUNDARY_PERCENT = 0.01  # 網格邊界百分比 (1% = 0.01, 建議0.01-0.05)

# 時間配置
TRADING_MINUTES = [0, 15, 30, 45]  # 開單時間 (分鐘)
PRICE_CHECK_INTERVAL = 0.1  # 價格檢查間隔 (秒) - 建議0.1-1.0
DISPLAY_INTERVAL = 1.0  # 終端顯示間隔 (秒)

# 安全配置
MIN_ORDER_VALUE = 1.0  # MEXC最小訂單金額 (USDT)
SOL_MIN_QUANTITY = 0.0001  # SOL最小交易精度
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
    
    def place_maker_order_with_retry(self, symbol, side, quantity, price, max_retries=3):
        """下MAKER單並處理過單問題"""
        for attempt in range(max_retries):
            try:
                # 調整價格以確保MAKER訂單不會立即成交
                if side == 'BUY':
                    # 買單價格稍微調低一點確保掛單
                    adjusted_price = price * 0.999  # 調低0.1%
                else:
                    # 賣單價格稍微調高一點確保掛單
                    adjusted_price = price * 1.001  # 調高0.1%
                
                adjusted_price = round(adjusted_price, 4)
                
                order_result = self.place_maker_order(symbol, side, quantity, adjusted_price)
                
                if order_result and 'orderId' in order_result:
                    logging.info(f"MAKER訂單成功: {side} {quantity} {symbol} @ {adjusted_price}")
                    return order_result
                else:
                    logging.warning(f"MAKER訂單失敗 (嘗試 {attempt + 1}/{max_retries}): {order_result}")
                    if attempt < max_retries - 1:
                        time.sleep(1)  # 等待1秒後重試
                        
            except Exception as e:
                logging.error(f"下單異常 (嘗試 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
        
        return None
    
    def check_order_status(self, symbol, order_id):
        """檢查訂單狀態"""
        try:
            endpoint = "/api/v3/order"
            params = {
                'symbol': symbol,
                'orderId': order_id
            }
            result = self._make_request('GET', endpoint, params)
            return result
        except Exception as e:
            logging.error(f"檢查訂單狀態異常: {e}")
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
        self.grids = {}  # 活躍網格 {grid_id: GridInfo}
        self.grid_counter = 0
        self.total_profit = 0
        self.total_trades = 0
        
        # 價格監控
        self.current_price = 0
        self.last_check_time = time.time()
        
    def calculate_quantity(self, price):
        """計算下單數量，確保符合精度要求"""
        if 'SOL' in self.symbol:
            quantity = self.capital_per_grid / price
            # 確保數量符合最小精度要求
            quantity = max(quantity, SOL_MIN_QUANTITY)
            # 四捨五入到4位小數
            quantity = round(quantity, 4)
            
            # 驗證訂單金額是否滿足最小要求
            order_value = quantity * price
            if order_value < MIN_ORDER_VALUE:
                # 調整數量以滿足最小訂單金額
                quantity = MIN_ORDER_VALUE / price
                quantity = round(quantity, 4)
            
            return quantity
        
        # 其他交易對的處理
        quantity = self.capital_per_grid / price
        return round(quantity, 6)
    
    def create_new_grid(self):
        """創建新網格，包含完整的可行性檢查"""
        current_price = self.trader.get_current_price(self.symbol)
        if not current_price:
            logging.error("無法獲取當前價格，跳過網格創建")
            return None
        
        # 檢查本金是否足夠
        if self.capital_per_grid < MIN_ORDER_VALUE:
            logging.error(f"每格資金 {self.capital_per_grid:.2f} USDT 低於最小要求 {MIN_ORDER_VALUE} USDT")
            return None
        
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        # 計算網格邊界
        price_range = current_price * self.boundary_percent
        lower_bound = current_price - price_range
        upper_bound = current_price + price_range
        
        # 計算網格間距
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
            'current_level': 5,  # 從中間開始 (0-10的第5級)
            'positions': {},  # {level: {'order_id': xxx, 'quantity': xxx, 'price': xxx}}
            'profit': 0,
            'trade_count': 0,
            'created_time': datetime.now(),
            'active': True,
            'last_update': time.time()
        }
        
        # 在起始價格下第一單
        initial_level = 5
        initial_price = lower_bound + initial_level * grid_step
        quantity = self.calculate_quantity(initial_price)
        
        # 驗證訂單參數
        if quantity * initial_price < MIN_ORDER_VALUE:
            logging.error(f"初始訂單金額 {quantity * initial_price:.4f} 低於最小要求")
            return None
        
        logging.info(f"  初始訂單: {quantity:.4f} SOL @ ${initial_price:.4f}")
        
        order_result = self.trader.place_maker_order_with_retry(
            self.symbol, 'BUY', quantity, initial_price
        )
        
        if order_result and 'orderId' in order_result:
            grid_info['positions'][initial_level] = {
                'order_id': order_result['orderId'],
                'quantity': quantity,
                'price': initial_price,
                'side': 'BUY',
                'created_time': time.time()
            }
            self.grids[grid_id] = grid_info
            logging.info(f"✓ 網格 {grid_id} 創建成功，訂單ID: {order_result['orderId']}")
            return grid_id
        else:
            logging.error(f"✗ 網格創建失敗: {order_result}")
            return None
    
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
        
        # 檢查是否觸及邊界
        if current_price <= grid['lower_bound'] or current_price >= grid['upper_bound']:
            self.close_grid(grid_id)
            return
        
        # 計算當前應該在的級別
        target_level = int((current_price - grid['lower_bound']) / grid['grid_step'])
        target_level = max(0, min(self.grid_count - 1, target_level))
        
        current_level = grid['current_level']
        
        if target_level != current_level:
            self.execute_grid_trade(grid_id, current_level, target_level)
    
    def execute_grid_trade(self, grid_id, from_level, to_level):
        """執行網格交易"""
        grid = self.grids[grid_id]
        
        # 價格上漲 - 賣出並在新位置買入
        if to_level > from_level:
            for level in range(from_level, to_level):
                if level in grid['positions']:
                    # 賣出當前位置
                    self.sell_position(grid_id, level)
            
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
                    'side': 'BUY'
                }
        
        # 價格下跌 - 在新位置買入
        elif to_level < from_level:
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
                            'side': 'BUY'
                        }
        
        grid['current_level'] = to_level
    
    def sell_position(self, grid_id, level):
        """賣出指定位置"""
        grid = self.grids[grid_id]
        if level in grid['positions']:
            position = grid['positions'][level]
            
            # 先取消原買單
            self.trader.cancel_order(self.symbol, position['order_id'])
            
            # 下賣單
            sell_price = position['price'] * 1.002  # 稍微高一點確保成交
            order_result = self.trader.place_maker_order_with_retry(
                self.symbol, 'SELL', position['quantity'], sell_price
            )
            
            if order_result:
                # 計算利潤
                profit = (sell_price - position['price']) * position['quantity']
                grid['profit'] += profit
                grid['trade_count'] += 1
                self.total_profit += profit
                self.total_trades += 1
                
                del grid['positions'][level]
                logging.info(f"網格 {grid_id} 賣出級別 {level}，利潤: {profit:.4f}")
    
    def close_grid(self, grid_id):
        """關閉網格"""
        grid = self.grids[grid_id]
        grid['active'] = False
        
        # 取消所有未完成訂單
        for level, position in grid['positions'].items():
            self.trader.cancel_order(self.symbol, position['order_id'])
        
        logging.info(f"網格 {grid_id} 已關閉，總利潤: {grid['profit']:.4f}，交易次數: {grid['trade_count']}")
    
    def get_status_report(self):
        """獲取狀態報告"""
        report = []
        report.append(f"{'='*20} MEXC 網格交易狀態 {'='*20}")
        report.append(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"當前價格: ${self.current_price:.4f} USDT")
        report.append(f"總利潤: {self.total_profit:.4f} USDT")
        report.append(f"總交易次數: {self.total_trades}")
        
        active_grids = [g for g in self.grids.values() if g['active']]
        report.append(f"活躍網格數: {len(active_grids)}")
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
                price_range = f"${grid['lower_bound']:.2f}-${grid['upper_bound']:.2f}"
                profit_str = f"+{grid['profit']:.4f}" if grid['profit'] > 0 else f"{grid['profit']:.4f}"
                
                # 計算運行時間
                runtime = datetime.now() - grid['created_time']
                runtime_str = f"{runtime.seconds//60}分{runtime.seconds%60}秒"
                
                status_line = (f"🟢 {grid_id}: 級別{current_level} | "
                             f"持倉{position_count} | 利潤{profit_str}💰 | "
                             f"範圍{price_range} | 交易{grid['trade_count']}次 | "
                             f"運行{runtime_str}")
                report.append(status_line)
        
        report.append("")
        report.append(f"💡 配置: {self.grid_count}網格 | ±{self.boundary_percent*100:.1f}%邊界 | {self.initial_capital}U本金")
        
        # 添加下次開單時間提醒
        now = datetime.now()
        next_minute = None
        for minute in TRADING_MINUTES:
            if minute > now.minute:
                next_minute = minute
                break
        if next_minute is None:
            next_minute = TRADING_MINUTES[0] + 60  # 下一小時的第一個時間點
        
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
    # 驗證配置參數
    if INITIAL_CAPITAL / GRID_COUNT < MIN_ORDER_VALUE:
        error_msg = f"❌ 配置錯誤: 每格資金 {INITIAL_CAPITAL/GRID_COUNT:.2f} USDT 低於最小要求 {MIN_ORDER_VALUE} USDT"
        logging.error(error_msg)
        logging.error(f"請將 INITIAL_CAPITAL 調整至至少 {MIN_ORDER_VALUE * GRID_COUNT} USDT")
        return
    
    # 初始化交易者和策略
    trader = MEXCTrader(API_KEY, SECRET_KEY)
    
    # 測試API連接
    logging.info("🔌 測試API連接...")
    test_price = trader.get_current_price(SYMBOL)
    if not test_price:
        logging.error("❌ API連接失敗，請檢查API密鑰")
        return
    
    logging.info(f"✓ API連接成功，當前 {SYMBOL} 價格: ${test_price:.4f}")
    
    # 檢查帳戶餘額（雲端部署時可自動跳過）
    if not SKIP_BALANCE_CHECK:
        usdt_balance = trader.get_account_balance('USDT')
        if usdt_balance < INITIAL_CAPITAL * 1.2:  # 預留20%緩衝
            warning_msg = f"⚠️  警告: USDT餘額 {usdt_balance:.2f} 可能不足，建議至少 {INITIAL_CAPITAL*1.2:.2f} USDT"
            logging.warning(warning_msg)
            logging.warning("雲端部署模式：繼續執行（如需停止請調整 SKIP_BALANCE_CHECK 配置）")
    else:
        logging.info("⏭️  跳過餘額檢查（雲端部署模式）")
    
    strategy = GridStrategy(trader, SYMBOL, INITIAL_CAPITAL, GRID_COUNT, GRID_BOUNDARY_PERCENT)
    
    logging.info("🚀 MEXC 網格交易策略啟動")
    logging.info(f"📊 配置: {GRID_COUNT}網格 | ±{GRID_BOUNDARY_PERCENT*100}%邊界 | {INITIAL_CAPITAL}U本金")
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
            
            # 更新網格狀態
            strategy.update_grids()
            
            # 顯示狀態 (每秒一次)
            if current_time - last_display_time >= DISPLAY_INTERVAL:
                # 雲端環境不清屏，直接輸出
                status_report = strategy.get_status_report()
                logging.info(f"\n{status_report}")
                last_display_time = current_time
            
            time.sleep(PRICE_CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        logging.info("\n🛑 程序被用戶中斷，正在安全關閉...")
        # 關閉所有網格
        active_grids = [grid_id for grid_id, grid in strategy.grids.items() if grid['active']]
        if active_grids:
            logging.info(f"🔄 正在關閉 {len(active_grids)} 個活躍網格...")
            for grid_id in active_grids:
                strategy.close_grid(grid_id)
            logging.info("✅ 所有網格已安全關閉")
        logging.info("👋 程序已退出")
    except Exception as e:
        logging.error(f"程序異常: {e}", exc_info=True)
        # 嘗試關閉所有網格
        try:
            for grid_id in list(strategy.grids.keys()):
                if strategy.grids[grid_id]['active']:
                    strategy.close_grid(grid_id)
        except:
            pass
        raise

if __name__ == "__main__":
    main()