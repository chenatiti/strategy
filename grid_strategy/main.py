# main.py

import schedule
import time
from utils import get_client, get_price
from config import SYMBOL

client = get_client()

def run_strategy():
    try:
        price = get_price(client, SYMBOL)
        print(f"✅ {SYMBOL} 現在價格是：{price}")
    except Exception as e:
        print(f"❌ 發生錯誤：{e}")

# 每15分鐘執行一次
schedule.every(15).minutes.do(run_strategy)

print("🚀 策略開始執行（每15分鐘一次）...")

# 第一次先馬上跑一次
run_strategy()

while True:
    schedule.run_pending()
    time.sleep(1)
