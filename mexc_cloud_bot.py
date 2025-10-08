import os
import time
import hmac
import hashlib
import requests
from datetime import datetime
from dotenv import load_dotenv

# ==================== 配置區 (可修改) ====================
OBSERVATION_PERIOD = 15  # 觀察市場秒數
CHECK_PRICE_INTERVAL = 0.3  # 查價間隔（秒）
WAIT_BEFORE_NEXT_CYCLE = 60  # 量化交易結束後等待秒數
TRADE_PERCENTAGE = 0.5  # 使用資金比例 (50%)
SYMBOL = "USDC_USDT"  # 交易對
MIN_TICK = 0.0001  # 最小價格變動
BASE_CURRENCY = "USDC"  # 基礎貨幣
QUOTE_CURRENCY = "USDT"  # 計價貨幣

# ==================== API 配置 ====================
load_dotenv()
API_KEY = os.getenv('MEXC_API_KEY')
API_SECRET = os.getenv('MEXC_API_SECRET')
BASE_URL = "https://api.mexc.com"

# ==================== 全域變數 ====================
total_trades = 0
total_profit = 0.0
holding_usdc = False
usdc_amount = 0.0
buy_price = 0.0

# ==================== 工具函數 ====================
def log(message, level="INFO"):
    """統一日誌格式"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] [{level}] {message}")

def generate_signature(params):
    """生成 MEXC API 簽名"""
    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def get_current_price():
    """獲取當前市場價格"""
    try:
        url = f"{BASE_URL}/api/v3/ticker/price"
        params = {'symbol': SYMBOL}
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        return float(data['price'])
    except Exception as e:
        log(f"獲取價格失敗: {e}", "ERROR")
        return None

def get_account_balance():
    """獲取帳戶餘額"""
    try:
        timestamp = int(time.time() * 1000)
        params = {
            'timestamp': timestamp,
            'recvWindow': 5000
        }
        params['signature'] = generate_signature(params)
        
        headers = {'X-MEXC-APIKEY': API_KEY}
        url = f"{BASE_URL}/api/v3/account"
        
        response = requests.get(url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        balances = {}
        for balance in data['balances']:
            if balance['asset'] in [BASE_CURRENCY, QUOTE_CURRENCY]:
                balances[balance['asset']] = float(balance['free'])
        
        return balances
    except Exception as e:
        log(f"獲取餘額失敗: {e}", "ERROR")
        return None

def place_market_order(side, quantity):
    """下市價單
    side: 'BUY' 或 'SELL'
    quantity: USDC 數量（賣出時）或 USDT 金額（買入時）
    """
    global total_trades, total_profit, holding_usdc, usdc_amount, buy_price
    
    try:
        timestamp = int(time.time() * 1000)
        
        # 買入時用 quoteOrderQty（USDT金額），賣出時用 quantity（USDC數量）
        params = {
            'symbol': SYMBOL,
            'side': side,
            'type': 'MARKET',
            'timestamp': timestamp,
            'recvWindow': 5000
        }
        
        if side == 'BUY':
            params['quoteOrderQty'] = round(quantity, 2)  # USDT 金額
        else:
            params['quantity'] = round(quantity, 4)  # USDC 數量
        
        params['signature'] = generate_signature(params)
        
        headers = {'X-MEXC-APIKEY': API_KEY}
        url = f"{BASE_URL}/api/v3/order"
        
        response = requests.post(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # 計算成交均價
        executed_qty = float(data.get('executedQty', 0))
        cumulative_quote_qty = float(data.get('cummulativeQuoteQty', 0))
        
        if executed_qty > 0:
            avg_price = cumulative_quote_qty / executed_qty
        else:
            avg_price = 0
        
        if side == 'BUY':
            holding_usdc = True
            usdc_amount = executed_qty
            buy_price = avg_price
            log(f"✅ 買入: {quantity:.2f} USDT → {executed_qty:.4f} USDC (價格: {avg_price:.4f})")
        else:
            profit = cumulative_quote_qty - (usdc_amount * buy_price)
            total_profit += profit
            holding_usdc = False
            log(f"✅ 賣出: {executed_qty:.4f} USDC → {cumulative_quote_qty:.2f} USDT (價格: {avg_price:.4f}, 利潤: {profit:+.4f} USDT)")
        
        total_trades += 1
        log(f"📊 累計交易: {total_trades} 次 | 總利潤: {total_profit:+.4f} USDT")
        
        return True
    except Exception as e:
        log(f"下單失敗 ({side}): {e}", "ERROR")
        return False

def observe_market():
    """觀察市場，返回價格邊界"""
    log(f"👀 開始觀察市場 {OBSERVATION_PERIOD} 秒...")
    
    prices = []
    end_time = time.time() + OBSERVATION_PERIOD
    
    while time.time() < end_time:
        price = get_current_price()
        if price:
            prices.append(price)
        time.sleep(CHECK_PRICE_INTERVAL)
    
    if not prices:
        log("觀察期間未獲取到價格", "ERROR")
        return None, None
    
    lower_bound = min(prices)
    upper_bound = max(prices)
    
    log(f"📈 邊界設定: {lower_bound:.4f} - {upper_bound:.4f}")
    return lower_bound, upper_bound

def force_close_position():
    """強制平倉"""
    global holding_usdc, usdc_amount
    
    if holding_usdc and usdc_amount > 0:
        log("⚠️ 強制平倉所有 USDC...", "WARNING")
        if place_market_order('SELL', usdc_amount):
            usdc_amount = 0
            holding_usdc = False
            return True
    return False

def trading_cycle():
    """單次量化交易循環"""
    global holding_usdc, usdc_amount
    
    # 1. 觀察市場
    lower_bound, upper_bound = observe_market()
    if not lower_bound or not upper_bound:
        return False
    
    # 2. 獲取初始餘額
    balances = get_account_balance()
    if not balances:
        return False
    
    available_usdt = balances.get(QUOTE_CURRENCY, 0)
    log(f"💰 可用餘額: {available_usdt:.2f} USDT")
    
    if available_usdt < 1:
        log("餘額不足 1 USDT，無法交易", "ERROR")
        return False
    
    trade_amount = available_usdt * TRADE_PERCENTAGE
    log(f"💵 本次交易金額: {trade_amount:.2f} USDT ({TRADE_PERCENTAGE*100}%)")
    
    # 3. 開始交易循環
    log("🚀 開始量化交易...")
    
    while True:
        current_price = get_current_price()
        
        if not current_price:
            time.sleep(CHECK_PRICE_INTERVAL)
            continue
        
        # 檢查是否突破邊界
        if current_price > upper_bound or current_price < lower_bound:
            log(f"🛑 價格突破邊界 (當前: {current_price:.4f})，關閉量化交易", "WARNING")
            force_close_position()
            break
        
        # 買入邏輯：價格 = lower_bound 且未持倉
        if not holding_usdc and abs(current_price - lower_bound) < MIN_TICK / 2:
            place_market_order('BUY', trade_amount)
        
        # 賣出邏輯：價格 = upper_bound 且持有倉位
        elif holding_usdc and abs(current_price - upper_bound) < MIN_TICK / 2:
            place_market_order('SELL', usdc_amount)
        
        time.sleep(CHECK_PRICE_INTERVAL)
    
    return True

def main():
    """主程式"""
    log("=" * 60)
    log("🤖 MEXC USDC/USDT 量化交易機器人啟動")
    log("=" * 60)
    
    if not API_KEY or not API_SECRET:
        log("未設定 API Key，請檢查 .env 文件", "ERROR")
        return
    
    cycle_count = 0
    
    while True:
        cycle_count += 1
        log(f"\n{'=' * 60}")
        log(f"🔄 第 {cycle_count} 輪量化交易")
        log(f"{'=' * 60}")
        
        try:
            trading_cycle()
        except KeyboardInterrupt:
            log("\n👋 收到停止信號，正在安全退出...", "WARNING")
            force_close_position()
            break
        except Exception as e:
            log(f"交易循環出現錯誤: {e}", "ERROR")
        
        log(f"⏳ 等待 {WAIT_BEFORE_NEXT_CYCLE} 秒後開始下一輪...")
        time.sleep(WAIT_BEFORE_NEXT_CYCLE)

if __name__ == "__main__":
    main()