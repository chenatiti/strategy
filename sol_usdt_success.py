import os, time, hmac, base64, json, requests
from datetime import datetime, timezone
from dotenv import load_dotenv

"""
多網格版本（A/B/C/D... 同步跑） + 真實成交數量對帳 + 最小下單量檢查
- 每到 0/15/30/45 分，自動啟動一個新的『網格實例』(Grid)。
- 每個 Grid 在啟動當下：以現價為中心建立 ±GRID_RANGE 的區間（切 10 格），並立即在 L5 開一筆。
- 之後該 Grid 獨立運作：
    * 往上：每跨到新格 (5→6→7→8→9) 就各買一份（每格只買一次）。
    * 往下：任一倉位若 score = 現在格 - 開倉格 = -1，立刻平掉該倉。
    * 觸邊界：到 L10 或 L0 → 平倉並結束該 Grid。
- 下單金額採『動態複利』：每次開倉前抓 USDT 餘額，單筆金額 = 餘額 / SLOTS。
- 新增：
    * 實際成交數量對帳（根據 ordId 從 fills 取得 fillSz 合計）。
    * 依交易規則檢查最小下單量(minSz)，不足則不賣、直接移除該倉避免報 51008/51020。
"""

# ========= 配置 =========
USE_DEMO = False                    # True=模擬盤；False=實盤
INST_ID  = "SOL-USDT"              # 交易對（現貨）
SLOTS = 10                         # 分10份 → 單筆金額 = 餘額 / SLOTS
TICK_MINUTES = {0, 20, 23, 45,}     # 啟動新網格的時間點（每小時四次）
TIMEOUT = 10
GRID_RANGE = 0.003                  # 網格上下界百分比（±1% 區間 → 共 10 格）

# 讀金鑰
load_dotenv()
API_KEY    = os.getenv("OKX_API_KEY") or "db4993e3-dd90-4b8a-9d54-84532194a48e"
SECRET_KEY = os.getenv("OKX_SECRET_KEY") or "64B67F19057C9D358FC27B6382AD702D"
PASSPHRASE = os.getenv("OKX_PASSPHRASE") or "Asdfghjkl1!"

BASE = "https://www.okx.com"
PATH_ORDER   = "/api/v5/trade/order"
PATH_TICKER  = "/api/v5/market/ticker"
PATH_BAL     = "/api/v5/account/balance"
PATH_FILLS   = "/api/v5/trade/fills-history"
PATH_INSTR   = "/api/v5/public/instruments"

# ========= 工具 =========
def iso_ts():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

def sign(msg: str) -> str:
    return base64.b64encode(hmac.new(SECRET_KEY.encode(), msg.encode(), "sha256").digest()).decode()

def headers(method: str, path: str, body_str: str = ""):
    ts = iso_ts()
    sig = sign(ts + method + path + body_str)
    h = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }
    if USE_DEMO:
        h["x-simulated-trading"] = "1"
    return h

def get_price():
    # 模擬盤抓模擬行情，實盤抓實盤行情，避免脫節
    if USE_DEMO:
        r = requests.get(BASE+PATH_TICKER, params={"instId": INST_ID}, headers=headers("GET", PATH_TICKER), timeout=TIMEOUT)
    else:
        r = requests.get(BASE+PATH_TICKER, params={"instId": INST_ID}, timeout=TIMEOUT)
    r.raise_for_status()
    return float(r.json()["data"][0]["last"])

def get_usdt_balance():
    r = requests.get(BASE+PATH_BAL, headers=headers("GET", PATH_BAL), timeout=TIMEOUT)
    r.raise_for_status()
    details = r.json()["data"][0]["details"]
    for d in details:
        if d["ccy"] == "USDT":
            return float(d["cashBal"])
    return 0.0

def get_instrument_info():
    """取得 minSz/lotSz/tickSz 等規則"""
    r = requests.get(BASE+PATH_INSTR, params={"instType":"SPOT","instId":INST_ID}, timeout=TIMEOUT)
    r.raise_for_status()
    info = r.json()["data"][0]
    return {
        "minSz": float(info["minSz"]),
        "lotSz": float(info["lotSz"]),
        "tickSz": float(info["tickSz"])
    }

INSTR_RULES = None  # 啟動時抓一次

def get_filled_qty(ordId: str) -> float:
    """根據 ordId 從成交歷史彙總實際成交幣數（可能多筆成交）"""
    try:
        params = {"instType":"SPOT","instId":INST_ID, "ordId": ordId, "limit":"100"}
        r = requests.get(BASE+PATH_FILLS, headers=headers("GET", PATH_FILLS), params=params, timeout=TIMEOUT)
        r.raise_for_status()
        fills = r.json().get("data", [])
        qty = 0.0
        for f in fills:
            if f.get("ordId") == ordId:
                qty += float(f["fillSz"])  # 單位：幣數
        return qty
    except Exception as e:
        print("[WARN] get_filled_qty exception:", e)
        return 0.0

def place_market_buy_amount(usdt_amount: float, note: str = ""):
    body = {
        "instId": INST_ID,
        "tdMode": "cash",
        "side": "buy",
        "ordType": "market",
        "sz": str(round(usdt_amount, 4)),  # 用金額下單（quote_ccy）
        "tgtCcy": "quote_ccy"
    }
    body_str = json.dumps(body)
    res = requests.post(BASE+PATH_ORDER, headers=headers("POST", PATH_ORDER, body_str), data=body_str, timeout=TIMEOUT)
    print(f"[BUY  ${usdt_amount:.2f}] {note} ->", res.json())
    return res.json()

def place_market_sell_qty(qty: float, note: str = ""):
    body = {
        "instId": INST_ID,
        "tdMode": "cash",
        "side": "sell",
        "ordType": "market",
        "sz": f"{qty:.8f}"
    }
    body_str = json.dumps(body)
    res = requests.post(BASE+PATH_ORDER, headers=headers("POST", PATH_ORDER, body_str), data=body_str, timeout=TIMEOUT)
    print(f"[SELL {qty:.8f}] {note} ->", res.json())
    return res.json()

# ========= Grid 類別（可同時跑多個） =========
class Grid:
    def __init__(self, label: str, p0: float):
        self.label = label           # 例如 A/B/C 或時間戳
        self.p0 = p0
        # 建立 ±GRID_RANGE 的區間，切 10 格 → 每格寬度 = GRID_RANGE/10（上下各5格）
        step = p0 * (GRID_RANGE / 5)
        self.gmin = p0 - 5 * step
        self.gmax = p0 + 5 * step
        self.positions = {}          # key=level, val={qty, entry_px, ordId}
        self.active = True
        # 啟動就買 L5 一份（下單後對帳實際成交量）
        self._open_if_absent(5, p0)
        print(f"[GRID {self.label}] init p0={p0:.2f} range=[{self.gmin:.2f},{self.gmax:.2f}]")

    def level_of(self, price: float) -> int:
        if price <= self.gmin: return 0
        if price >= self.gmax: return 10
        rel = (price - self.gmin) / (self.gmax - self.gmin)
        return round(rel * 10)

    def _open_if_absent(self, level: int, px: float):
        if not self.active: return
        if level in self.positions: return
        total_cap = get_usdt_balance()
        if total_cap < 10: 
            print(f"[GRID {self.label}] SKIP open L{level}（餘額不足）")
            return
        unit = total_cap / SLOTS
        buy_res = place_market_buy_amount(unit, note=f"Grid {self.label} open L{level} @~{px:.2f}")
        ordId = None
        try:
            if buy_res.get("code") == "0":
                ordId = buy_res["data"][0].get("ordId")
        except Exception:
            pass
        # 稍等撮合，再查實際成交量
        time.sleep(0.6)
        qty_real = get_filled_qty(ordId) if ordId else 0.0
        if qty_real <= 0:
            # 退回估算值，避免完全沒有數量（但賣出前仍會再檢查 minSz）
            qty_real = unit / px
        self.positions[level] = {"qty": qty_real, "entry_px": px, "ordId": ordId}
        print(f"[GRID {self.label}] OPEN L{level} qty≈{qty_real:.8f}")

    def _close(self, level: int, reason: str, px: float):
        if level not in self.positions: return
        qty = self.positions[level]["qty"]
        # 最小下單量檢查
        if INSTR_RULES and qty < INSTR_RULES["minSz"]:
            print(f"[GRID {self.label}] SKIP close L{level}（qty {qty:.8f} < minSz {INSTR_RULES['minSz']:.8f}）→ 直接移除")
            self.positions.pop(level, None)
            return
        place_market_sell_qty(qty, note=f"Grid {self.label} close L{level} {reason} @~{px:.2f}")
        self.positions.pop(level, None)
        print(f"[GRID {self.label}] CLOSE L{level} qty={qty:.8f} reason={reason}")

    def _close_all(self, reason: str, px: float):
        for lv in list(self.positions.keys()):
            self._close(lv, reason, px)
        self.active = False
        print(f"[GRID {self.label}] END ({reason})")

    def on_price(self, price: float):
        if not self.active: return
        lvl = self.level_of(price)
        # 觸邊界 → 平倉並結束
        if lvl == 10:
            # 你的規則：5~9 的倉到 10 一次賣掉
            for lv in [lv for lv in list(self.positions.keys()) if 5 <= lv <= 9]:
                self._close(lv, "hit_10", price)
            self._close_all("hit_10", price)
            return
        if lvl == 0:
            self._close_all("hit_0", price)
            return
        # 往下：score=-1 就砍
        for lv in list(self.positions.keys()):
            if (lvl - lv) == -1:
                self._close(lv, "score_-1", price)
        # 往下：每到更低新格也開倉（例如 5→4、4→3）
        if 1 <= lvl < 5:
            self._open_if_absent(lvl, price)
        # 往上：每跨到新格就補該格（5~9）
        if lvl > 5:
            for L in range(5, min(lvl, 9) + 1):
                self._open_if_absent(L, price)

# ========= 主循環：同時管理多個 Grid =========
def main():
    global INSTR_RULES
    print("=== Multi-Grid bot start ===")
    # 啟動時抓一次交易規則
    try:
        INSTR_RULES = get_instrument_info()
        print(f"[RULES] minSz={INSTR_RULES['minSz']} lotSz={INSTR_RULES['lotSz']} tickSz={INSTR_RULES['tickSz']}")
    except Exception as e:
        print("[WARN] get_instrument_info failed:", e)
        INSTR_RULES = None

    grids = {}                # key=label（A/B/C/... or 時間），value=Grid
    label_cycle = ["A","B","C","D","E","F","G","H"]
    next_label_idx = 0
    last_spawn_key = None     # 用來避免同一分鐘重複建網格

    while True:
        # 取得最新價
        try:
            price = get_price()
        except Exception as e:
            print("[ERROR] get_price:", e)
            time.sleep(2)
            continue

        # 價格驅動：讓所有仍在運行的 Grid 各自更新（即時執行買/賣邏輯）
        for g in list(grids.values()):
            g.on_price(price)

        # 定時啟動新的 Grid（0/15/30/45 的前 2 秒容忍窗）
        now = datetime.now()
        spawn_key = (now.year, now.month, now.day, now.hour, now.minute)
        if now.minute in TICK_MINUTES and now.second < 2 and spawn_key != last_spawn_key:
            label = label_cycle[next_label_idx % len(label_cycle)]
            next_label_idx += 1
            grids[label] = Grid(label, price)
            last_spawn_key = spawn_key

        # 可視化狀態（debug）
        live_labels = [f"{k}(pos:{sorted(v.positions.keys())})" for k,v in grids.items() if v.active]
        print(f"[STATE] t={now.strftime('%H:%M:%S')} px={price:.2f} live_grids={live_labels}")

        time.sleep(0.5)

if __name__ == "__main__":
    if not all([API_KEY, SECRET_KEY, PASSPHRASE]) or "YOUR_DEMO" in API_KEY:
        print("⚠️ 請先在 .env 設定 OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE")
    else:
        main()
