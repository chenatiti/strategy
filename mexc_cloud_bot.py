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

# 固定網格參數
GRID_BUY_PRICE = 0.9994    # 精確買入價
GRID_SELL_PRICE = 0.9995   # 精確賣出價
CAPITAL_PER_TRADE = 10     # 每次交易 10 USDT

# 時間設定
RUN_DURATION = 600         # 運行 10 分鐘（600 秒）
CHECK_INTERVAL = 0.5       # 每 0.5 秒檢查一次價格

# ==================== 配置區域結束 ====================

logging.basicConfig(
    level=logging.INFO,
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
            
            if method in ['POST', 'DELETE']:
                logging.info(f"API {method} {endpoint}: {response.status_code}")
            
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
        }
        
        if side == 'BUY':
            params['quoteOrderQty'] = str(quantity)
            logging.info(f"🛒 市價買單: 使用 {quantity} USDT 買入 USDC")
        else:  # SELL
            params['quantity'] = str(quantity)
            logging.info(f"💰 市價賣單: 賣出 {quantity} USDC")
        
        result = self._request('POST', "/api/v3/order", params)
        
        if result and 'orderId' in result:
            logging.info(f"  訂單ID: {result['orderId']}")
        else:
            logging.error(f"✗ 市價單失敗: {result}")
        
        return result
    
    def query_order(self, symbol, order_id):
        """查詢訂單狀態"""
        return self._request('GET', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})

class GridTrader:
    def __init__(self, client, buy_price, sell_price, capital):
        self.client = client
        self.buy_price = buy_price
        self.sell_price = sell_price
        self.capital = capital
        
        # 狀態
        self.position = None  # {'quantity': xxx, 'buy_price': xxx, 'time': xxx}
        self.pending_order = None  # {'order_id': xxx, 'side': xxx, 'time': xxx}
        
        # 統計
        self.total_trades = 0
        self.total_profit = 0
        self.trade_history = []
        
        # 時間
        self.start_time = time.time()
        self.end_time = self.start_time + RUN_DURATION
        
    def should_stop(self, current_price):
        """判斷是否應該停止"""
        # 時間到了
        if time.time() >= self.end_time:
            logging.info("⏰ 運行時間到達 10 分鐘")
            return True
        
        # 價格超出網格區間
        if current_price < self.buy_price or current_price > self.sell_price:
            logging.info(f"⚠️  價格 ${current_price:.4f} 超出網格區間 ${self.buy_price:.4f}-${self.sell_price:.4f}")
            return True
        
        return False
    
    def check_pending_order(self):
        """檢查掛單狀態"""
        if not self.pending_order:
            return
        
        order_id = self.pending_order['order_id']
        order_info = self.client.query_order(SYMBOL, order_id)
        
        if not order_info:
            return
        
        status = order_info.get('status')
        
        if status == 'FILLED':
            side = self.pending_order['side']
            filled_qty = float(order_info.get('executedQty', 0))
            filled_value = float(order_info.get('cummulativeQuoteQty', 0))
            filled_price = filled_value / filled_qty if filled_qty > 0 else 0
            
            if side == 'BUY':
                # 買入成交
                self.position = {
                    'quantity': filled_qty,
                    'buy_price': filled_price,
                    'time': time.time()
                }
                logging.info(f"✅ 買入成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
                logging.info(f"   花費: {filled_value:.4f} USDT")
                
            else:  # SELL
                # 賣出成交
                if self.position:
                    profit = (filled_price - self.position['buy_price']) * filled_qty
                    self.total_profit += profit
                    self.total_trades += 1
                    
                    trade_record = {
                        'buy_price': self.position['buy_price'],
                        'sell_price': filled_price,
                        'quantity': filled_qty,
                        'profit': profit,
                        'time': datetime.now().strftime('%H:%M:%S')
                    }
                    self.trade_history.append(trade_record)
                    
                    logging.info(f"✅ 賣出成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
                    logging.info(f"   獲得: {filled_value:.4f} USDT")
                    logging.info(f"   利潤: {profit:+.6f} USDT (第 {self.total_trades} 次套利)")
                
                self.position = None
            
            self.pending_order = None
        
        elif status in ['CANCELED', 'REJECTED', 'EXPIRED', 'FAILED']:
            logging.error(f"❌ 訂單失敗: {status}")
            self.pending_order = None
        
        elif status in ['NEW', 'PARTIALLY_FILLED']:
            elapsed = time.time() - self.pending_order['time']
            if elapsed > 5:
                logging.warning(f"⚠️  市價單執行緩慢: {status} (已等待 {elapsed:.1f} 秒)")
    
    def try_buy(self, current_price):
        """嘗試買入"""
        # 已有持倉，不買入
        if self.position:
            return False
        
        # 已有掛單，不重複下單
        if self.pending_order:
            return False
        
        # 價格必須精確匹配
        if current_price != self.buy_price:
            return False
        
        logging.info(f"🎯 價格到達買入點 ${current_price:.4f}")
        
        # 下市價買單
        result = self.client.place_market_order(SYMBOL, 'BUY', self.capital)
        
        if result and 'orderId' in result:
            self.pending_order = {
                'order_id': result['orderId'],
                'side': 'BUY',
                'time': time.time()
            }
            return True
        
        return False
    
    def try_sell(self, current_price):
        """嘗試賣出"""
        # 沒有持倉，不賣出
        if not self.position:
            return False
        
        # 已有掛單，不重複下單
        if self.pending_order:
            return False
        
        # 價格必須精確匹配
        if current_price != self.sell_price:
            return False
        
        logging.info(f"🎯 價格到達賣出點 ${current_price:.4f}")
        
        # 計算賣出數量（預留 0.1% 避免餘額不足）
        quantity = round(self.position['quantity'] * 0.999, 2)
        
        if quantity < 1:
            logging.error(f"❌ 數量不足: {quantity:.4f} USDC")
            return False
        
        # 下市價賣單
        result = self.client.place_market_order(SYMBOL, 'SELL', quantity)
        
        if result and 'orderId' in result:
            self.pending_order = {
                'order_id': result['orderId'],
                'side': 'SELL',
                'time': time.time()
            }
            return True
        
        return False
    
    def force_close(self):
        """強制平倉"""
        if not self.position:
            return
        
        logging.info("🚨 執行強制平倉...")
        
        quantity = round(self.position['quantity'] * 0.999, 2)
        result = self.client.place_market_order(SYMBOL, 'SELL', quantity)
        
        if result and 'orderId' in result:
            time.sleep(3)
            order_info = self.client.query_order(SYMBOL, result['orderId'])
            
            if order_info and order_info.get('status') == 'FILLED':
                filled_qty = float(order_info.get('executedQty', quantity))
                filled_value = float(order_info.get('cummulativeQuoteQty', 0))
                filled_price = filled_value / filled_qty if filled_qty > 0 else 0
                
                profit = (filled_price - self.position['buy_price']) * filled_qty
                self.total_profit += profit
                
                logging.info(f"✅ 平倉成交: {filled_qty:.4f} USDC @ ${filled_price:.4f}")
                logging.info(f"   平倉利潤: {profit:+.6f} USDT")
        
        self.position = None
    
    def display_status(self):
        """顯示當前狀態"""
        elapsed = time.time() - self.start_time
        remaining = self.end_time - time.time()
        
        print_separator()
        logging.info(f"⏱️  運行時間: {elapsed:.0f}秒 / 剩餘: {remaining:.0f}秒")
        
        if self.position:
            logging.info(f"📦 持倉: {self.position['quantity']:.4f} USDC @ ${self.position['buy_price']:.4f}")
        else:
            logging.info("📦 持倉: 無")
        
        if self.pending_order:
            logging.info(f"📝 掛單: {self.pending_order['side']}")
        
        logging.info(f"📊 套利次數: {self.total_trades} 次")
        logging.info(f"💰 累計利潤: {self.total_profit:+.6f} USDT")
        print_separator()

def main():
    print_separator()
    logging.info("🤖 MEXC 固定網格交易測試")
    print_separator()
    
    client = MEXCClient(API_KEY, SECRET_KEY)
    
    # 測試連接
    logging.info("🔌 測試 API 連接...")
    current_price = client.get_price(SYMBOL)
    if not current_price:
        logging.error("❌ API 連接失敗")
        return
    
    logging.info(f"✅ API 連接成功")
    logging.info(f"💱 當前價格: ${current_price:.4f}")
    
    # 檢查餘額
    usdt = client.get_balance('USDT')
    usdc = client.get_balance('USDC')
    logging.info(f"💼 帳戶餘額: USDT {usdt:.2f} | USDC {usdc:.4f}")
    
    if usdt < CAPITAL_PER_TRADE:
        logging.error(f"❌ USDT 餘額不足！需要至少 {CAPITAL_PER_TRADE} USDT")
        return
    
    # 顯示策略配置
    print_separator()
    logging.info("⚙️  策略配置:")
    logging.info(f"  網格區間: ${GRID_BUY_PRICE:.4f} - ${GRID_SELL_PRICE:.4f}")
    logging.info(f"  買入價格: ${GRID_BUY_PRICE:.4f} (精確匹配)")
    logging.info(f"  賣出價格: ${GRID_SELL_PRICE:.4f} (精確匹配)")
    logging.info(f"  每次交易: {CAPITAL_PER_TRADE} USDT")
    logging.info(f"  運行時間: {RUN_DURATION} 秒 (10 分鐘)")
    logging.info(f"  檢查間隔: {CHECK_INTERVAL} 秒")
    print_separator()
    
    # 創建交易機器人
    trader = GridTrader(client, GRID_BUY_PRICE, GRID_SELL_PRICE, CAPITAL_PER_TRADE)
    
    initial_usdt = usdt
    initial_usdc = usdc
    
    logging.info("🚀 開始運行固定網格交易...")
    logging.info(f"⏳ 等待價格到達 ${GRID_BUY_PRICE:.4f}...")
    print_separator()
    
    last_status_time = time.time()
    last_price = None
    
    try:
        while True:
            # 獲取當前價格
            current_price = client.get_price(SYMBOL)
            
            if current_price is None:
                time.sleep(CHECK_INTERVAL)
                continue
            
            # 只在價格變化時顯示
            if current_price != last_price:
                logging.info(f"💱 當前價格: ${current_price:.4f}")
                last_price = current_price
            
            # 檢查是否應該停止
            if trader.should_stop(current_price):
                break
            
            # 檢查掛單狀態
            trader.check_pending_order()
            
            # 嘗試交易
            if not trader.pending_order:
                if not trader.position:
                    trader.try_buy(current_price)
                else:
                    trader.try_sell(current_price)
            
            # 每 30 秒顯示一次狀態
            if time.time() - last_status_time >= 30:
                trader.display_status()
                last_status_time = time.time()
            
            time.sleep(CHECK_INTERVAL)
    
    except KeyboardInterrupt:
        logging.info("\n⛔ 使用者中斷程式")
    
    # 強制平倉
    if trader.position:
        trader.force_close()
    
    # 清空剩餘 USDC
    time.sleep(2)
    remaining_usdc = client.get_balance('USDC')
    if remaining_usdc > 0.01:
        logging.info(f"🧹 清空剩餘 USDC: {remaining_usdc:.4f}")
        quantity = round(remaining_usdc * 0.999, 2)
        if quantity >= 1:
            client.place_market_order(SYMBOL, 'SELL', quantity)
            time.sleep(3)
    
    # 最終報告
    print_separator()
    logging.info("📊 最終報告")
    print_separator()
    
    final_usdt = client.get_balance('USDT')
    final_usdc = client.get_balance('USDC')
    
    logging.info("💼 餘額變化:")
    logging.info(f"  USDT: {initial_usdt:.2f} → {final_usdt:.2f} ({final_usdt - initial_usdt:+.4f})")
    logging.info(f"  USDC: {initial_usdc:.4f} → {final_usdc:.4f} ({final_usdc - initial_usdc:+.4f})")
    logging.info("")
    
    logging.info("📈 交易統計:")
    logging.info(f"  套利次數: {trader.total_trades} 次")
    logging.info(f"  累計利潤: {trader.total_profit:+.6f} USDT")
    
    if trader.trade_history:
        logging.info("")
        logging.info("📋 交易明細:")
        for i, trade in enumerate(trader.trade_history, 1):
            logging.info(f"  #{i} {trade['time']} | "
                        f"買 ${trade['buy_price']:.4f} → 賣 ${trade['sell_price']:.4f} | "
                        f"利潤 {trade['profit']:+.6f} USDT")
    
    print_separator()
    logging.info("✅ 測試完成！")
    print_separator()

if __name__ == "__main__":
    main()