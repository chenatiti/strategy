import requests
import time
import hashlib
import hmac
import json
from urllib.parse import urlencode
from datetime import datetime
import logging

# ==================== 配置區域 ====================

# MEXC API
API_KEY = "mx0vglaUUDV1VP6KTU"
SECRET_KEY = "0e3a3cb6b0e24b0fbdf82d0c1e15c4b1"

# 交易對
SYMBOL = "XRPUSDT"

# 網格設定
GRID_COUNT = 10  # 網格數量（0-10 共 11 層）
GRID_SPACING_PERCENT = 0.5  # 每格間距 0.5%

# 資金設定
MIN_CAPITAL_PER_GRID = 1.2  # 每格最少 1.2 USDT

# 限價單設定
PRICE_BUFFER_PERCENT = 0.05  # 價格緩衝 0.05%
ORDER_TIMEOUT = 30  # 訂單超時（秒）

# 時間設定
CHECK_PRICE_INTERVAL = 2  # 檢查價格間隔（秒）
DISPLAY_STATUS_INTERVAL = 60  # 顯示狀態間隔（秒）

# 開單時間控制
ENABLE_SCHEDULE = True  # 是否啟用定時開單
SCHEDULE_MINUTES = [0, 10, 20, 30, 40, 50]  # 每小時的哪些分鐘開單

# ==================== 配置區域結束 ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def print_separator():
    print("=" * 60)

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
        return float(result['price']) if result and 'price' in result else None
    
    def get_balance(self, asset):
        result = self._request('GET', "/api/v3/account")
        if result and 'balances' in result:
            for balance in result['balances']:
                if balance['asset'] == asset:
                    return float(balance['free'])
        return 0
    
    def place_limit_order(self, symbol, side, quantity, price):
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'quantity': str(quantity),
            'price': str(price)
        }
        return self._request('POST', "/api/v3/order", params)
    
    def cancel_order(self, symbol, order_id):
        return self._request('DELETE', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})
    
    def query_order(self, symbol, order_id):
        return self._request('GET', "/api/v3/order", {'symbol': symbol, 'orderId': order_id})

class MovingGridBot:
    def __init__(self, client, symbol):
        self.client = client
        self.symbol = symbol
        self.grids = {}
        self.grid_counter = 0
        self.total_profit = 0
        self.total_trades = 0
        self.initial_assets = self._get_total_assets()
        
        self._display_startup()
    
    def _get_total_assets(self):
        """獲取總資產"""
        usdt = self.client.get_balance('USDT')
        sol = self.client.get_balance('SOL')
        price = self.client.get_price(self.symbol)
        
        if price:
            total = usdt + (sol * price)
            return {
                'USDT': usdt,
                'SOL': sol,
                'price': price,
                'total': total,
                'timestamp': datetime.now()
            }
        return None
    
    def _display_startup(self):
        """顯示啟動資訊"""
        print_separator()
        logging.info("MEXC 移動網格交易策略")
        print_separator()
        
        if self.initial_assets:
            logging.info(f"當前價格: ${self.initial_assets['price']:.4f}")
            logging.info("")
            logging.info("資產變化:")
            logging.info(f"  初始: {self.initial_assets['total']:.2f} USDT")
            logging.info(f"  當前: {self.initial_assets['total']:.2f} USDT")
            logging.info(f"  盈虧: ±0.00 USDT (+0.00%)")
            logging.info(f"  USDT: {self.initial_assets['USDT']:.2f}")
            logging.info(f"  SOL: {self.initial_assets['SOL']:.4f}")
        print_separator()
    
    def _calculate_grid_prices(self, base_price):
        """計算網格價格"""
        prices = {}
        middle_level = GRID_COUNT // 2
        spacing = GRID_SPACING_PERCENT / 100
        
        for level in range(GRID_COUNT + 1):
            if level == middle_level:
                prices[level] = base_price
            elif level > middle_level:
                steps = level - middle_level
                prices[level] = base_price * ((1 + spacing) ** steps)
            else:
                steps = middle_level - level
                prices[level] = base_price / ((1 + spacing) ** steps)
        
        return prices
    
    def _calculate_quantity(self, price, target_value):
        """計算安全的購買數量"""
        safe_value = target_value * 1.05  # 加 5% 安全餘量
        quantity = round(safe_value / price, 3)
        
        # 驗證金額是否足夠
        final_value = quantity * price
        if final_value < MIN_CAPITAL_PER_GRID * 0.95:
            quantity = round((MIN_CAPITAL_PER_GRID * 1.1) / price, 3)
        
        return quantity
    
    def _place_buy_order(self, price):
        """掛買單"""
        buy_price = round(price * (1 + PRICE_BUFFER_PERCENT / 100), 2)
        quantity = self._calculate_quantity(buy_price, MIN_CAPITAL_PER_GRID)
        
        logging.info(f"掛買單: {quantity:.3f} SOL @ ${buy_price:.2f}")
        
        result = self.client.place_limit_order(self.symbol, 'BUY', quantity, buy_price)
        
        if result and 'orderId' in result:
            return {
                'order_id': result['orderId'],
                'quantity': quantity,
                'price': buy_price,
                'created_time': time.time(),
                'type': 'BUY'
            }
        else:
            logging.error(f"買單失敗: {result}")
            return None
    
    def _place_sell_order(self, quantity, price):
        """掛賣單"""
        sell_price = round(price * (1 - PRICE_BUFFER_PERCENT / 100), 2)
        actual_value = quantity * sell_price
        
        if actual_value < MIN_CAPITAL_PER_GRID * 0.95:
            logging.error(f"賣單金額過小: {actual_value:.2f} USDT")
            return None
        
        logging.info(f"掛賣單: {quantity:.3f} SOL @ ${sell_price:.2f}")
        
        result = self.client.place_limit_order(self.symbol, 'SELL', quantity, sell_price)
        
        if result and 'orderId' in result:
            return {
                'order_id': result['orderId'],
                'quantity': quantity,
                'price': sell_price,
                'created_time': time.time(),
                'type': 'SELL'
            }
        else:
            logging.error(f"賣單失敗: {result}")
            return None
    
    def _wait_for_order(self, order_info, timeout=60):
        """等待訂單成交"""
        if not order_info:
            return False, None
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            result = self.client.query_order(self.symbol, order_info['order_id'])
            
            if not result:
                return False, None
            
            status = result.get('status')
            
            if status == 'FILLED':
                logging.info("✓ 訂單成交")
                return True, result
            
            if status in ['NEW', 'PARTIALLY_FILLED']:
                if time.time() - order_info['created_time'] > ORDER_TIMEOUT:
                    logging.info("訂單超時，重新掛單...")
                    self.client.cancel_order(self.symbol, order_info['order_id'])
                    
                    current_price = self.client.get_price(self.symbol)
                    if not current_price:
                        return False, None
                    
                    # 重新掛單
                    if order_info['type'] == 'BUY':
                        new_order = self._place_buy_order(current_price)
                    else:
                        new_order = self._place_sell_order(order_info['quantity'], current_price)
                    
                    if new_order:
                        order_info.update(new_order)
                        start_time = time.time()
                    else:
                        return False, None
            
            elif status in ['CANCELED', 'REJECTED', 'EXPIRED', 'FAILED']:
                logging.error(f"訂單失敗: {status}")
                return False, result
            
            time.sleep(3)
        
        logging.error("訂單等待超時")
        self.client.cancel_order(self.symbol, order_info['order_id'])
        return False, None
    
    def create_grid(self):
        """創建新網格"""
        current_price = self.client.get_price(self.symbol)
        if not current_price:
            logging.error("無法獲取當前價格")
            return None
        
        usdt_balance = self.client.get_balance('USDT')
        if usdt_balance < MIN_CAPITAL_PER_GRID:
            logging.error(f"資金不足: 需要 {MIN_CAPITAL_PER_GRID} USDT，只有 {usdt_balance:.2f} USDT")
            return None
        
        self.grid_counter += 1
        grid_id = f"Grid_{self.grid_counter}"
        
        # 計算網格價格
        grid_prices = self._calculate_grid_prices(current_price)
        
        # 找出當前層級
        current_level = GRID_COUNT // 2
        for level in range(GRID_COUNT + 1):
            if level > 0 and grid_prices[level] > current_price:
                current_level = level - 1
                break
        
        print_separator()
        logging.info(f"創建網格 {grid_id}")
        logging.info(f"當前價格: ${current_price:.2f}")
        logging.info(f"起始層級: Level {current_level}")
        logging.info(f"價格範圍: ${grid_prices[0]:.2f} - ${grid_prices[GRID_COUNT]:.2f}")
        
        # 買入第一份
        order_info = self._place_buy_order(current_price)
        if not order_info:
            logging.error("初始買單失敗")
            return None
        
        # 等待成交
        success, result = self._wait_for_order(order_info)
        if not success:
            logging.error("初始買單未成交")
            return None
        
        filled_qty = float(result.get('executedQty', order_info['quantity']))
        filled_price = float(result.get('price', order_info['price']))
        
        # 保存網格資訊
        self.grids[grid_id] = {
            'id': grid_id,
            'grid_prices': grid_prices,
            'current_level': current_level,
            'position': {
                'level': current_level,
                'quantity': filled_qty,
                'buy_price': filled_price,
                'buy_time': time.time()
            },
            'profit': 0,
            'trade_count': 0,
            'created_time': datetime.now(),
            'active': True
        }
        
        logging.info(f"✓ 網格創建成功")
        print_separator()
        
        return grid_id
    
    def update_grids(self):
        """更新所有網格"""
        current_price = self.client.get_price(self.symbol)
        if not current_price:
            return
        
        for grid_id, grid in list(self.grids.items()):
            if not grid['active']:
                continue
            
            self._update_single_grid(grid_id, grid, current_price)
    
    def _update_single_grid(self, grid_id, grid, current_price):
        """更新單個網格"""
        grid_prices = grid['grid_prices']
        current_level = grid['current_level']
        
        # 判斷當前價格在哪一層
        new_level = None
        for level in range(GRID_COUNT + 1):
            if level < GRID_COUNT and grid_prices[level] <= current_price < grid_prices[level + 1]:
                new_level = level
                break
        
        # 價格超出範圍，關閉網格
        if new_level is None:
            if current_price <= grid_prices[0] or current_price >= grid_prices[GRID_COUNT]:
                logging.info(f"價格超出範圍，關閉網格 {grid_id}")
                self.close_grid(grid_id)
            return
        
        # 層級沒變
        if new_level == current_level:
            return
        
        logging.info(f"網格 {grid_id}: Level {current_level} → Level {new_level}")
        
        # 價格上漲 or 下跌
        if new_level > current_level:
            self._handle_price_increase(grid_id, grid, new_level, current_price)
        else:
            self._handle_price_decrease(grid_id, grid, new_level, current_price)
        
        grid['current_level'] = new_level
    
    def _handle_price_increase(self, grid_id, grid, new_level, current_price):
        """處理價格上漲：賣舊買新"""
        position = grid['position']
        
        # 1. 賣出舊倉位
        if position and position['quantity'] > 0:
            sell_order = self._place_sell_order(position['quantity'], current_price)
            
            if sell_order:
                success, result = self._wait_for_order(sell_order)
                
                if success:
                    filled_price = float(result.get('price', sell_order['price']))
                    profit = (filled_price - position['buy_price']) * position['quantity']
                    
                    grid['profit'] += profit
                    grid['trade_count'] += 1
                    self.total_profit += profit
                    self.total_trades += 1
                    
                    logging.info(f"✓ 賣出成功，利潤: {profit:.4f} USDT")
        
        # 2. 買入新倉位
        usdt_balance = self.client.get_balance('USDT')
        
        if usdt_balance >= MIN_CAPITAL_PER_GRID:
            buy_order = self._place_buy_order(current_price)
            
            if buy_order:
                success, result = self._wait_for_order(buy_order)
                
                if success:
                    filled_qty = float(result.get('executedQty', buy_order['quantity']))
                    filled_price = float(result.get('price', buy_order['price']))
                    
                    grid['position'] = {
                        'level': new_level,
                        'quantity': filled_qty,
                        'buy_price': filled_price,
                        'buy_time': time.time()
                    }
                else:
                    grid['position'] = None
        else:
            grid['position'] = None
            logging.warning("資金不足，無法買入")
    
    def _handle_price_decrease(self, grid_id, grid, new_level, current_price):
        """處理價格下跌：如果該層沒倉位就買入"""
        position = grid['position']
        
        # 如果該層已有倉位，不做任何事
        if position and position['level'] == new_level:
            return
        
        usdt_balance = self.client.get_balance('USDT')
        
        if usdt_balance >= MIN_CAPITAL_PER_GRID:
            buy_order = self._place_buy_order(current_price)
            
            if buy_order:
                success, result = self._wait_for_order(buy_order)
                
                if success:
                    filled_qty = float(result.get('executedQty', buy_order['quantity']))
                    filled_price = float(result.get('price', buy_order['price']))
                    
                    # 如果有舊倉位，先賣掉
                    if position and position['quantity'] > 0:
                        sell_order = self._place_sell_order(position['quantity'], current_price)
                        
                        if sell_order:
                            sell_success, sell_result = self._wait_for_order(sell_order)
                            
                            if sell_success:
                                sell_price = float(sell_result.get('price', sell_order['price']))
                                profit = (sell_price - position['buy_price']) * position['quantity']
                                
                                grid['profit'] += profit
                                grid['trade_count'] += 1
                                self.total_profit += profit
                                self.total_trades += 1
                                
                                logging.info(f"✓ 賣出舊倉位，利潤: {profit:.4f} USDT")
                    
                    grid['position'] = {
                        'level': new_level,
                        'quantity': filled_qty,
                        'buy_price': filled_price,
                        'buy_time': time.time()
                    }
        else:
            logging.warning("資金不足，無法買入")
    
    def close_grid(self, grid_id):
        """關閉網格"""
        grid = self.grids[grid_id]
        grid['active'] = False
        
        position = grid['position']
        if position and position['quantity'] > 0:
            current_price = self.client.get_price(self.symbol)
            
            if current_price:
                sell_order = self._place_sell_order(position['quantity'], current_price)
                
                if sell_order:
                    success, result = self._wait_for_order(sell_order)
                    
                    if success:
                        sell_price = float(result.get('price', sell_order['price']))
                        profit = (sell_price - position['buy_price']) * position['quantity']
                        
                        grid['profit'] += profit
                        grid['trade_count'] += 1
                        self.total_profit += profit
                        self.total_trades += 1
        
        logging.info(f"網格 {grid_id} 已關閉，總利潤: {grid['profit']:.4f} USDT")
    
    def display_status(self):
        """顯示狀態"""
        current_assets = self._get_total_assets()
        
        print_separator()
        logging.info("MEXC 移動網格交易策略")
        print_separator()
        logging.info(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info("")
        
        if current_assets and self.initial_assets:
            logging.info(f"當前價格: ${current_assets['price']:.2f}")
            logging.info("")
            
            initial_value = self.initial_assets['total']
            current_value = current_assets['total']
            change = current_value - initial_value
            percent = (change / initial_value * 100) if initial_value > 0 else 0
            symbol = "+" if change >= 0 else ""
            
            logging.info("資產變化:")
            logging.info(f"  初始: {initial_value:.2f} USDT")
            logging.info(f"  當前: {current_value:.2f} USDT")
            logging.info(f"  盈虧: {symbol}{change:.2f} USDT ({symbol}{percent:.2f}%)")
            logging.info(f"  USDT: {current_assets['USDT']:.2f}")
            logging.info(f"  SOL: {current_assets['SOL']:.4f}")
            logging.info("")
        
        logging.info("策略統計:")
        logging.info(f"  累計利潤: {self.total_profit:.4f} USDT")
        logging.info(f"  完成交易: {self.total_trades} 次")
        
        active_grids = [g for g in self.grids.values() if g['active']]
        logging.info(f"  活躍網格: {len(active_grids)}")
        logging.info("")
        
        if active_grids:
            for grid in active_grids:
                position = grid['position']
                if position:
                    logging.info(f"{grid['id']}: Level {position['level']} | 持倉 {position['quantity']:.3f} SOL @ ${position['buy_price']:.2f}")
                else:
                    logging.info(f"{grid['id']}: 無倉位")
        else:
            logging.info("當前無活躍網格，等待開單時間")
        
        logging.info("")
        logging.info(f"配置: {GRID_COUNT+1}層 | ±{GRID_SPACING_PERCENT}% | 每格{MIN_CAPITAL_PER_GRID}U")
        print_separator()

def should_create_grid():
    """判斷是否該創建網格"""
    if not ENABLE_SCHEDULE:
        return True
    
    now = datetime.now()
    return now.minute in SCHEDULE_MINUTES and now.second < 10

def main():
    logging.info("啟動 MEXC 移動網格交易機器人...")
    
    client = MEXCClient(API_KEY, SECRET_KEY)
    
    # 測試連接
    logging.info("測試 API 連接...")
    test_price = client.get_price(SYMBOL)
    if not test_price:
        logging.error("API 連接失敗")
        return
    
    logging.info(f"API 連接成功，當前價格: ${test_price:.2f}")
    
    # 檢查資金
    usdt = client.get_balance('USDT')
    sol = client.get_balance('SOL')
    logging.info(f"帳戶資產: USDT {usdt:.2f} | SOL {sol:.4f}")
    
    if usdt < MIN_CAPITAL_PER_GRID:
        logging.error(f"USDT 不足！需要至少 {MIN_CAPITAL_PER_GRID} USDT")
        return
    
    # 創建機器人
    bot = MovingGridBot(client, SYMBOL)
    
    last_create_minute = -1
    last_display_time = time.time()
    
    try:
        while True:
            now = datetime.now()
            
            # 檢查是否創建新網格
            if should_create_grid() and now.minute != last_create_minute:
                active_grids = [g for g in bot.grids.values() if g['active']]
                if len(active_grids) == 0:
                    logging.info("嘗試創建新網格...")
                    bot.create_grid()
                    last_create_minute = now.minute
            
            # 更新網格
            bot.update_grids()
            
            # 顯示狀態
            if time.time() - last_display_time >= DISPLAY_STATUS_INTERVAL:
                bot.display_status()
                last_display_time = time.time()
            
            time.sleep(CHECK_PRICE_INTERVAL)
    
    except KeyboardInterrupt:
        logging.info("停止中，正在關閉所有網格...")
        active_grids = [gid for gid, g in bot.grids.items() if g['active']]
        for grid_id in active_grids:
            bot.close_grid(grid_id)
        
        final_assets = bot._get_total_assets()
        if final_assets and bot.initial_assets:
            logging.info(f"最終資產: {final_assets['total']:.2f} USDT")
            change = final_assets['total'] - bot.initial_assets['total']
            logging.info(f"總盈虧: {change:.4f} USDT")
        
        logging.info("程序已退出")
    except Exception as e:
        logging.error(f"程序異常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()