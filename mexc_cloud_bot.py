import os
import time
import hmac
import hashlib
import requests
from datetime import datetime
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

class MEXCGridBot:
    def __init__(self):
        # 載入環境變數
        load_dotenv()
        
        self.api_key = os.getenv('MEXC_API_KEY')
        self.api_secret = os.getenv('MEXC_API_SECRET')
        
        # 檢查API密鑰是否存在
        if not self.api_key or not self.api_secret:
            raise ValueError(
                "錯誤：API密鑰未設定！\n"
                "請確認 .env 文件存在且包含：\n"
                "MEXC_API_KEY=你的API密鑰\n"
                "MEXC_API_SECRET=你的秘密密鑰"
            )
        
        self.base_url = 'https://api.mexc.com'
        
        self.symbol = 'USDCUSDT'
        self.quote_asset = 'USDT'  # 用來買的資產
        self.base_asset = 'USDC'   # 要買的資產
        
        self.grid_active = False
        self.buy_price = None
        self.sell_price = None
        self.lower_bound = None
        self.upper_bound = None
        self.position_open = False  # 是否持有倉位
        self.bought_amount = 0  # 買入的USDC數量
        
        print(f"[{self.get_time()}] MEXC 網格交易機器人啟動")
        print(f"[{self.get_time()}] 交易對: {self.symbol}")
        print(f"[{self.get_time()}] API密鑰已載入: {self.api_key[:10]}...")
        print(f"[{self.get_time()}] 查價間隔: 0.3秒")
        print("-" * 50)
    
    def get_time(self):
        """獲取當前時間字符串"""
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    def generate_signature(self, params):
        """生成簽名"""
        # 將所有值轉換為字符串
        str_params = {k: str(v) for k, v in params.items()}
        query_string = '&'.join([f"{k}={str_params[k]}" for k in sorted(str_params.keys())])
        
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def get_current_price(self):
        """獲取當前市場價格"""
        try:
            url = f"{self.base_url}/api/v3/ticker/price"
            params = {'symbol': self.symbol}
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return float(data['price'])
            else:
                print(f"[{self.get_time()}] 獲取價格失敗: {response.text}")
                return None
        except Exception as e:
            print(f"[{self.get_time()}] 獲取價格錯誤: {e}")
            return None
    
    def get_account_balance(self, asset):
        """獲取賬戶餘額"""
        try:
            timestamp = int(time.time() * 1000)
            params = {
                'timestamp': timestamp,
                'recvWindow': 5000
            }
            params['signature'] = self.generate_signature(params)
            
            headers = {'X-MEXC-APIKEY': self.api_key}
            url = f"{self.base_url}/api/v3/account"
            
            print(f"[{self.get_time()}] 查詢餘額 - API Key: {self.api_key[:10]}...")
            print(f"[{self.get_time()}] 請求URL: {url}")
            print(f"[{self.get_time()}] 參數: timestamp={timestamp}")
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            print(f"[{self.get_time()}] 回應狀態碼: {response.status_code}")
            print(f"[{self.get_time()}] 回應內容: {response.text[:200]}")
            
            if response.status_code == 200:
                data = response.json()
                for balance in data['balances']:
                    if balance['asset'] == asset:
                        free_balance = float(balance['free'])
                        print(f"[{self.get_time()}] {asset} 可用餘額: {free_balance}")
                        return free_balance
                print(f"[{self.get_time()}] 未找到 {asset} 資產")
                return 0.0
            else:
                print(f"[{self.get_time()}] 獲取餘額失敗 - 狀態碼: {response.status_code}")
                print(f"[{self.get_time()}] 錯誤訊息: {response.text}")
                return 0.0
        except Exception as e:
            print(f"[{self.get_time()}] 獲取餘額錯誤: {e}")
            import traceback
            traceback.print_exc()
            return 0.0
    
    def place_market_order(self, side, quantity=None, quote_qty=None):
        """下市價單
        side: 'BUY' 或 'SELL'
        quantity: 買入/賣出的USDC數量
        quote_qty: 使用的USDT數量(僅用於買入)
        """
        try:
            timestamp = int(time.time() * 1000)
            params = {
                'symbol': self.symbol,
                'side': side,
                'type': 'MARKET',
                'timestamp': timestamp,
                'recvWindow': 5000
            }
            
            if side == 'BUY' and quote_qty is not None:
                # 確保是字符串格式，保留足夠精度
                params['quoteOrderQty'] = f"{quote_qty:.8f}".rstrip('0').rstrip('.')
            elif quantity is not None:
                # 確保是字符串格式，保留足夠精度
                params['quantity'] = f"{quantity:.8f}".rstrip('0').rstrip('.')
            
            params['signature'] = self.generate_signature(params)
            
            headers = {
                'X-MEXC-APIKEY': self.api_key,
                'Content-Type': 'application/json'
            }
            
            url = f"{self.base_url}/api/v3/order"
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[{self.get_time()}] 下單失敗: {response.text}")
                return None
        except Exception as e:
            print(f"[{self.get_time()}] 下單錯誤: {e}")
            return None
    
    def observe_market(self, duration=15):
        """觀察市場15秒，記錄最高價和最低價"""
        print(f"[{self.get_time()}] 開始觀察市場 {duration} 秒...")
        
        prices = []
        start_time = time.time()
        
        while time.time() - start_time < duration:
            price = self.get_current_price()
            if price:
                prices.append(price)
                print(f"[{self.get_time()}] 當前價格: {price:.4f}")
            time.sleep(0.3)
        
        if not prices:
            print(f"[{self.get_time()}] 觀察期間未獲取到價格")
            return None, None
        
        low = min(prices)
        high = max(prices)
        
        print(f"[{self.get_time()}] 觀察完成 - 最低價: {low:.4f}, 最高價: {high:.4f}")
        return low, high
    
    def start_grid(self):
        """啟動網格"""
        if self.grid_active:
            return
        
        print(f"\n{'='*50}")
        print(f"[{self.get_time()}] 啟動新網格")
        
        # 觀察市場
        low, high = self.observe_market(15)
        
        if low is None or high is None:
            print(f"[{self.get_time()}] 無法啟動網格，價格數據不足")
            return
        
        # 設定網格邊界
        self.buy_price = low   # 買入價格
        self.sell_price = high  # 賣出價格
        self.lower_bound = low  # 下突破邊界
        self.upper_bound = high  # 上突破邊界
        
        self.grid_active = True
        self.position_open = False
        
        print(f"[{self.get_time()}] 網格設定:")
        print(f"  買入價格: {self.buy_price:.4f}")
        print(f"  賣出價格: {self.sell_price:.4f}")
        print(f"  下突破邊界: {self.lower_bound:.4f}")
        print(f"  上突破邊界: {self.upper_bound:.4f}")
        print(f"{'='*50}\n")
    
    def close_grid(self, reason=""):
        """關閉網格"""
        if not self.grid_active:
            return
        
        print(f"\n[{self.get_time()}] 關閉網格 - 原因: {reason}")
        
        # 如果持有USDC，全部賣回USDT
        if self.position_open and self.bought_amount > 0:
            print(f"[{self.get_time()}] 將持有的 {self.bought_amount:.6f} USDC 賣回 USDT")
            result = self.place_market_order('SELL', quantity=self.bought_amount)
            if result:
                print(f"[{self.get_time()}] 平倉成功: {result}")
            else:
                print(f"[{self.get_time()}] 平倉失敗")
        
        self.grid_active = False
        self.position_open = False
        self.bought_amount = 0
        print(f"[{self.get_time()}] 網格已關閉\n")
    
    def run_grid_logic(self):
        """執行網格交易邏輯"""
        if not self.grid_active:
            return
        
        current_price = self.get_current_price()
        if current_price is None:
            return
        
        # 檢查是否突破邊界
        if current_price < self.lower_bound:
            self.close_grid(f"價格 {current_price:.4f} 跌破下邊界 {self.lower_bound:.4f}")
            return
        
        if current_price > self.upper_bound:
            self.close_grid(f"價格 {current_price:.4f} 突破上邊界 {self.upper_bound:.4f}")
            return
        
        # 買入邏輯
        if current_price == self.buy_price and not self.position_open:
            usdt_balance = self.get_account_balance(self.quote_asset)
            buy_amount = usdt_balance * 0.5  # 使用50%資金
            
            if buy_amount > 0:
                print(f"[{self.get_time()}] 觸發買入 - 價格: {current_price:.4f}, 使用金額: {buy_amount:.2f} USDT")
                result = self.place_market_order('BUY', quote_qty=buy_amount)
                
                if result:
                    # 計算實際買到的USDC數量
                    self.bought_amount = float(result.get('executedQty', 0))
                    self.position_open = True
                    print(f"[{self.get_time()}] 買入成功，持有 {self.bought_amount:.6f} USDC")
                else:
                    print(f"[{self.get_time()}] 買入失敗")
        
        # 賣出邏輯
        elif current_price == self.sell_price and self.position_open:
            if self.bought_amount > 0:
                print(f"[{self.get_time()}] 觸發賣出 - 價格: {current_price:.4f}, 數量: {self.bought_amount:.6f} USDC")
                result = self.place_market_order('SELL', quantity=self.bought_amount)
                
                if result:
                    print(f"[{self.get_time()}] 賣出成功")
                    self.position_open = False
                    self.bought_amount = 0
                else:
                    print(f"[{self.get_time()}] 賣出失敗")
    
    def run(self):
        """主運行循環"""
        print(f"[{self.get_time()}] 機器人開始運行...")
        print(f"[{self.get_time()}] 等待秒針=0時啟動網格\n")
        
        try:
            while True:
                current_second = datetime.now().second
                
                # 當秒針為0且沒有活躍網格時，啟動新網格
                if current_second == 0 and not self.grid_active:
                    self.start_grid()
                
                # 執行網格邏輯
                if self.grid_active:
                    self.run_grid_logic()
                
                time.sleep(0.3)
                
        except KeyboardInterrupt:
            print(f"\n[{self.get_time()}] 收到停止信號")
            if self.grid_active:
                self.close_grid("手動停止")
            print(f"[{self.get_time()}] 機器人已停止")

if __name__ == "__main__":
    bot = MEXCGridBot()
    bot.run()