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

# 測試參數
TEST_BUY_AMOUNT = 10  # 買入 10 USDC (用 USDT 支付)

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
        """下市價單
        
        - side='BUY': 買入 USDC，使用 quoteOrderQty (花多少 USDT)
        - side='SELL': 賣出 USDC，使用 quantity (賣多少 USDC)
        """
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
        }
        
        if side == 'BUY':
            params['quoteOrderQty'] = str(quantity)
            logging.info(f"✓ 市價買單: 使用 {quantity} USDT 買入 USDC")
        else:  # SELL
            params['quantity'] = str(quantity)
            logging.info(f"✓ 市價賣單: 賣出 {quantity} USDC")
        
        result = self._request('POST', "/api/v3/order", params)
        
        if result and 'orderId' in result:
            logging.info(f"  訂單ID: {result['orderId']}")
        else:
            logging.error(f"✗ 市價單失敗: {result}")
        
        return result
    
    def query_order(self, symbol, order_id):
        """查詢訂單狀態"""
        return self._request('GET', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})

def main():
    print_separator()
    logging.info("🧪 MEXC API 簡單測試 - 買入賣出 USDC")
    print_separator()
    
    client = MEXCClient(API_KEY, SECRET_KEY)
    
    # 步驟 1: 測試連接
    logging.info("🔌 步驟 1: 測試 API 連接...")
    current_price = client.get_price(SYMBOL)
    if not current_price:
        logging.error("❌ API 連接失敗")
        return
    
    logging.info(f"✓ API 連接成功，{SYMBOL} 當前價格: ${current_price:.4f}")
    
    # 步驟 2: 檢查初始餘額
    logging.info("\n💼 步驟 2: 檢查初始餘額...")
    initial_usdt = client.get_balance('USDT')
    initial_usdc = client.get_balance('USDC')
    logging.info(f"  USDT: {initial_usdt:.2f}")
    logging.info(f"  USDC: {initial_usdc:.4f}")
    
    if initial_usdt < TEST_BUY_AMOUNT:
        logging.error(f"❌ USDT 餘額不足！需要至少 {TEST_BUY_AMOUNT} USDT")
        return
    
    # 步驟 3: 買入 USDC
    print_separator()
    logging.info(f"🛒 步驟 3: 買入 {TEST_BUY_AMOUNT} USDC...")
    buy_result = client.place_market_order(SYMBOL, 'BUY', TEST_BUY_AMOUNT)
    
    if not buy_result or 'orderId' not in buy_result:
        logging.error("❌ 買單失敗")
        return
    
    buy_order_id = buy_result['orderId']
    logging.info(f"✓ 買單已提交，訂單ID: {buy_order_id}")
    
    # 等待買單成交
    logging.info("⏳ 等待買單成交...")
    time.sleep(3)
    
    buy_order_info = client.query_order(SYMBOL, buy_order_id)
    if buy_order_info:
        status = buy_order_info.get('status')
        executed_qty = float(buy_order_info.get('executedQty', 0))
        executed_value = float(buy_order_info.get('cummulativeQuoteQty', 0))
        
        logging.info(f"  訂單狀態: {status}")
        logging.info(f"  成交數量: {executed_qty:.4f} USDC")
        logging.info(f"  花費金額: {executed_value:.4f} USDT")
        
        if status != 'FILLED':
            logging.warning(f"⚠️  買單未完全成交，狀態: {status}")
    
    # 步驟 4: 檢查買入後餘額
    logging.info("\n💼 步驟 4: 檢查買入後餘額...")
    time.sleep(1)
    after_buy_usdt = client.get_balance('USDT')
    after_buy_usdc = client.get_balance('USDC')
    logging.info(f"  USDT: {after_buy_usdt:.2f} (變化: {after_buy_usdt - initial_usdt:+.2f})")
    logging.info(f"  USDC: {after_buy_usdc:.4f} (變化: {after_buy_usdc - initial_usdc:+.4f})")
    
    if after_buy_usdc <= initial_usdc:
        logging.error("❌ USDC 餘額未增加，買入可能失敗")
        return
    
    # 步驟 5: 賣出所有 USDC
    print_separator()
    usdc_to_sell = round(after_buy_usdc * 0.999, 2)  # 保留 0.1% 避免餘額不足
    logging.info(f"💰 步驟 5: 賣出 {usdc_to_sell:.2f} USDC...")
    
    sell_result = client.place_market_order(SYMBOL, 'SELL', usdc_to_sell)
    
    if not sell_result or 'orderId' not in sell_result:
        logging.error("❌ 賣單失敗")
        return
    
    sell_order_id = sell_result['orderId']
    logging.info(f"✓ 賣單已提交，訂單ID: {sell_order_id}")
    
    # 等待賣單成交
    logging.info("⏳ 等待賣單成交...")
    time.sleep(3)
    
    sell_order_info = client.query_order(SYMBOL, sell_order_id)
    if sell_order_info:
        status = sell_order_info.get('status')
        executed_qty = float(sell_order_info.get('executedQty', 0))
        executed_value = float(sell_order_info.get('cummulativeQuoteQty', 0))
        
        logging.info(f"  訂單狀態: {status}")
        logging.info(f"  成交數量: {executed_qty:.4f} USDC")
        logging.info(f"  獲得金額: {executed_value:.4f} USDT")
        
        if status != 'FILLED':
            logging.warning(f"⚠️  賣單未完全成交，狀態: {status}")
    
    # 步驟 6: 檢查最終餘額
    print_separator()
    logging.info("📊 步驟 6: 最終結果")
    time.sleep(1)
    final_usdt = client.get_balance('USDT')
    final_usdc = client.get_balance('USDC')
    
    usdt_change = final_usdt - initial_usdt
    usdc_change = final_usdc - initial_usdc
    
    logging.info(f"\n💼 餘額變化:")
    logging.info(f"  USDT: {initial_usdt:.2f} → {final_usdt:.2f} ({usdt_change:+.4f})")
    logging.info(f"  USDC: {initial_usdc:.4f} → {final_usdc:.4f} ({usdc_change:+.4f})")
    
    print_separator()
    logging.info("✅ 測試完成！")
    
    if usdt_change < 0:
        loss_percent = abs(usdt_change / TEST_BUY_AMOUNT * 100)
        logging.info(f"💸 損失: {abs(usdt_change):.4f} USDT ({loss_percent:.2f}% 手續費+滑價)")
    elif usdt_change > 0:
        logging.info(f"💰 獲利: {usdt_change:.4f} USDT (不太可能，請檢查)")
    else:
        logging.info("📊 損益: 0 (完美)")
    
    print_separator()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("\n⛔ 使用者中斷測試")
    except Exception as e:
        logging.error(f"❌ 程序異常: {e}")
        import traceback
        traceback.print_exc()