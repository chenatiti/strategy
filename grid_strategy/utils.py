# utils.py

from binance.client import Client
from config import API_KEY, API_SECRET, BASE_URL

def get_client():
    """建立 Binance API 客戶端（使用 Testnet 現貨）"""
    client = Client(API_KEY, API_SECRET)
    client.API_URL = BASE_URL  # 指定 testnet URL
    return client

def get_price(client, symbol):
    """查詢指定交易對的即時價格"""
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker['price'])
