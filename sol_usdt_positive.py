# okx_grid_spot_sol.py
# -*- coding: utf-8 -*-
"""
OKX 現貨 SOL-USDT 多網格（A/B/C...）腳本
- 只在分針 0/15/30/45 開一個新網格（始於格位 5）
- 以 ± GRID_BOUND_PCT 定義上下邊界；分成 10 等分 => 11 個價位 (0..10)
- 價格每跨一格：先賣出「上一格的持倉」（若有），再在「新格」買一份
- 觸底 0 或觸頂 10 => 關閉該網格（把該網格未平倉全部賣出）
- 下單採 post-only 掛單（maker）；關閉網格時用市價保證出場
"""

import time, hmac, base64, json, requests, math, os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

# ==========【可調區｜請依需求修改】==========
# 1) OKX API（放最上面，方便改）
OKX_API_KEY        = "db4993e3-dd90-4b8a-9d54-84532194a48e"
OKX_SECRET_KEY     = "64B67F19057C9D358FC27B6382AD702D"
OKX_PASSPHRASE     = "Asdfghjkl1!"

# 2) 實盤 / 模擬 & 乾跑
USE_DEMO           = False         # True=模擬盤，False=實盤
DRY_RUN            = False         # True=不下單只印紀錄，False=真的下單

# 3) 交易標的
INST_ID            = "SOL-USDT"    # 目標現貨交易對（本腳本針對 SOL-USDT）

# 4) 下單金額設定（二選一，第二種開啟時會覆蓋第一種）
FIXED_USDT_PER_ORDER = 10.0        # 每次買入使用固定 USDT
USE_BALANCE_PERCENT  = True        # True=採「帳戶 USDT 餘額百分比」
BALANCE_PCT_PER_ORDER = 0.10       # 每筆訂單使用餘額的比例（例如 0.10=10%）

# 5) 網格設定
GRID_BOUND_PCT     = 0.003         # 上下邊界百分比（0.003=±0.3%）
GRID_SLOTS         = 10            # 固定為 10 格（0..10 共 11 個價位）
START_SLOT         = 5             # 起始格位固定為 5（照你的描述）
MAX_CONCURRENT_GRIDS = 10          # 最多同時跑幾個網格（避免爆倉）

# 6) 節奏與輸出
POLL_INTERVAL_SEC  = 2             # 價格輪詢頻率（秒）
PRINT_EVERY_TICK   = True          # 每次輪詢都印狀態
OPEN_MINUTES       = {59, 21, 30, 45}  # 僅在這些分針開新網格

# 7) 安全下限
MIN_NOTIONAL_USDT  = 5.0           # 單筆最小名目（避免太小下不了單/被忽略）
# ============================================

BASE_URL = "https://www.okx.com" if not USE_DEMO else "https://www.okx.com"
# OKX 模擬盤其實同域，但需在 Header 加 x-simulated-trading: 1

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})

def okx_timestamp():
    # ISO 格式時間
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

def sign(message, secret):
    return base64.b64encode(hmac.new(secret.encode(), message.encode(), digestmod='sha256').digest()).decode()

def okx_request(method, path, params=None, body=None, auth=False):
    url = BASE_URL + path
    if params:
        url += "?" + "&".join([f"{k}={v}" for k, v in params.items()])
    headers = {}
    data = ""
    if body:
        data = json.dumps(body)
    if auth:
        ts = okx_timestamp()
        prehash = ts + method.upper() + path + (("" if params else "") if method.upper()=="GET" else data)
        headers.update({
            "OK-ACCESS-KEY": OKX_API_KEY,
            "OK-ACCESS-SIGN": sign(prehash, OKX_SECRET_KEY),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
            "x-simulated-trading": "1" if USE_DEMO else "0"
        })
    r = session.request(method, url, headers=headers, data=data, timeout=10)
    r.raise_for_status()
    resp = r.json()
    if resp.get("code") != "0":
        raise RuntimeError(f"OKX API error: {resp.get('code')} {resp.get('msg')}")
    return resp

# 取得交易規格（tickSz, lotSz）
def get_instrument_details(inst_id):
    resp = okx_request("GET", "/api/v5/public/instruments", params={"instType": "SPOT", "instId": inst_id})
    data = resp["data"][0]
    tickSz = Decimal(data["tickSz"])
    lotSz  = Decimal(data["lotSz"])
    return tickSz, lotSz

def get_ticker_last(inst_id):
    resp = okx_request("GET", "/api/v5/market/ticker", params={"instId": inst_id})
    return float(resp["data"][0]["last"])

def get_usdt_balance():
    # 現貨餘額（available）
    resp = okx_request("GET", "/api/v5/account/balance", auth=True)
    for d in resp["data"][0]["details"]:
        if d["ccy"] == "USDT":
            return float(d["availBal"])
    return 0.0

def quantize_price(px, tickSz, direction="down"):
    dpx = Decimal(str(px))
    if direction == "down":
        return float((dpx / tickSz).to_integral_value(rounding=ROUND_DOWN) * tickSz)
    else:
        return float((dpx / tickSz).to_integral_value(rounding=ROUND_UP) * tickSz)

def quantize_size(sz, lotSz, direction="down"):
    dsz = Decimal(str(sz))
    return float((dsz / lotSz).to_integral_value(rounding=ROUND_DOWN) * lotSz)

def place_order(inst_id, side, px, sz, tag, post_only=True, market=False):
    if DRY_RUN:
        print(f"[DRY] place_order: {side} {sz} {inst_id} @ {px} tag={tag} post_only={post_only} market={market}")
        return {"ordId": "DRY-RUN"}
    body = {
        "instId": inst_id,
        "tdMode": "cash",  # spot 現貨
        "side": side,      # buy/sell
    }
    if market:
        body["ordType"] = "market"
        # 市價單對現貨用 sz（基礎幣），或使用 tgtCcy="quote_ccy" + sz 以 USDT 名目下單（新介面）
        body["sz"] = str(sz)
    else:
        body["ordType"] = "post_only" if post_only else "limit"
        body["px"] = str(px)
        body["sz"] = str(sz)
    if tag:
        body["tag"] = tag
    resp = okx_request("POST", "/api/v5/trade/order", body=body, auth=True)
    return resp["data"][0]

# ===== 網格類別 =====
class Grid:
    def __init__(self, name, p0, tickSz, lotSz):
        self.name = name  # 'A', 'B', 'C', ...
        self.tickSz = tickSz
        self.lotSz = lotSz
        # 計算上下界與價位
        self.lower = p0 * (1 - GRID_BOUND_PCT)
        self.upper = p0 * (1 + GRID_BOUND_PCT)
        self.step  = (self.upper - self.lower) / GRID_SLOTS
        self.levels = [self.lower + i * self.step for i in range(GRID_SLOTS + 1)]
        # 狀態
        self.active = True
        self.last_slot = None
        self.holdings = {}  # slot_index -> 累計持倉（以 SOL 計）
        # 先在起始格位 5 買一份
        start_slot = START_SLOT
        self.last_slot = start_slot
        qty = self.calc_order_size(self.levels[start_slot])
        if qty > 0:
            self.buy_at_slot(start_slot, qty)

    def calc_order_size(self, ref_price):
        # 以固定 USDT 或 餘額百分比 計算單筆名目
        if USE_BALANCE_PERCENT:
            bal = get_usdt_balance()
            usdt = max(bal * BALANCE_PCT_PER_ORDER, MIN_NOTIONAL_USDT)
        else:
            usdt = max(FIXED_USDT_PER_ORDER, MIN_NOTIONAL_USDT)
        sz = usdt / ref_price
        sz = quantize_size(sz, self.lotSz, "down")
        return sz

    def slot_of_price(self, price):
        # 將價格對映到 0..10 的格位（就近四捨五入）
        pos = (price - self.lower) / self.step
        k = int(round(pos))
        return max(0, min(GRID_SLOTS, k))

    def buy_at_slot(self, k, qty):
        px = quantize_price(self.levels[k], self.tickSz, "down")
        place_order(INST_ID, "buy", px, qty, tag=f"{self.name}-BUY-{k}", post_only=True, market=False)
        self.holdings[k] = self.holdings.get(k, 0.0) + qty

    def sell_at_slot(self, k, qty, force_market=False):
        if qty <= 0:
            return
        if force_market:
            place_order(INST_ID, "sell", 0, qty, tag=f"{self.name}-CLS-{k}", post_only=False, market=True)
        else:
            px = quantize_price(self.levels[k], self.tickSz, "up")
            place_order(INST_ID, "sell", px, qty, tag=f"{self.name}-SEL-{k}", post_only=True, market=False)
        self.holdings[k] = max(0.0, self.holdings.get(k, 0.0) - qty)
        if self.holdings[k] == 0.0:
            self.holdings.pop(k, None)

    def close_all(self):
        # 關閉整個網格：未平倉全部「市價賣出」
        for k, qty in list(self.holdings.items()):
            self.sell_at_slot(k, qty, force_market=True)
        self.holdings.clear()
        self.active = False

    def on_price(self, price):
        if not self.active:
            return
        k = self.slot_of_price(price)
        # 邊界：0 或 10 => 關閉
        if k == 0 or k == GRID_SLOTS:
            self.close_all()
            print(f"[{self.name}] 觸及邊界 {k}，關閉網格。")
            return
        if self.last_slot is None:
            self.last_slot = k
            return
        if k == self.last_slot:
            return

        # 每跨一格：先賣出「上一格位」若有，再在「新格位」買一份
        prev = self.last_slot
        # 賣出上一格的持倉（若存在）
        if prev in self.holdings and self.holdings[prev] > 0:
            qty_to_sell = self.holdings[prev]
            self.sell_at_slot(prev, qty_to_sell, force_market=False)
        # 在新格位買一份
        qty = self.calc_order_size(self.levels[k])
        if qty > 0:
            self.buy_at_slot(k, qty)
        self.last_slot = k

    def holdings_slots_str(self):
        # 回報等待賣出的格位清單（有持倉的格位）
        slots = sorted(self.holdings.keys())
        return "[" + ",".join(str(s) for s in slots) + "]" if slots else "[]"

def next_grid_letter(n):
    # A, B, C, ... Z, AA, AB ...
    s = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def main():
    print("=== OKX SOL-USDT 多網格（現貨）===")
    print(f"USE_DEMO={USE_DEMO} DRY_RUN={DRY_RUN} INST={INST_ID}")
    # 交易規格
    tickSz, lotSz = get_instrument_details(INST_ID)
    print(f"規格: tickSz={tickSz} lotSz={lotSz}")

    grids = []         # 活動網格清單
    started_marks = set()  # 避免同一分鐘重複開網格
    grid_count = 0

    while True:
        try:
            now = datetime.now()
            minute = now.minute
            second = now.second

            # 開新網格（在 0/15/30/45 的第一秒內，且未在該分鐘開過）
            if minute in OPEN_MINUTES and second < POLL_INTERVAL_SEC:
                key = (now.year, now.month, now.day, now.hour, minute)
                if key not in started_marks and len(grids) < MAX_CONCURRENT_GRIDS:
                    price = get_ticker_last(INST_ID)
                    name = next_grid_letter(grid_count)
                    g = Grid(name, price, Decimal(str(tickSz)), Decimal(str(lotSz)))
                    grids.append(g)
                    started_marks.add(key)
                    grid_count += 1
                    print(f"[{name}] 新網格啟動 @ {now.strftime('%H:%M:%S')} 參考價={price:.6f} "
                          f"範圍=({g.lower:.6f}, {g.upper:.6f}) step={g.step:.6f}")

            # 抓最新價，更新每個網格狀態
            price = get_ticker_last(INST_ID)
            for g in grids:
                if g.active:
                    g.on_price(price)

            # 螢幕輸出
            if PRINT_EVERY_TICK:
                now_str = now.strftime('%Y-%m-%d %H:%M:%S')
                grids_state = " ".join([f"{g.name} {g.holdings_slots_str()}" for g in grids if g.active])
                if not grids_state:
                    grids_state = "(尚無活動網格)"
                print(f"{now_str} | px={price:.6f} | {grids_state}")

            # 清掉已結束的網格
            grids = [g for g in grids if g.active]

            time.sleep(POLL_INTERVAL_SEC)
        except Exception as e:
            print(f"[ERR] {e}")
            time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    main()
