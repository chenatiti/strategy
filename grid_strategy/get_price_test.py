# get_price_test.py

from utils import get_client, get_price
from config import SYMBOL

client = get_client()
price = get_price(client, SYMBOL)
print(f"📈 現在 {SYMBOL} 價格為：{price}")
