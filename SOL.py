# okx_grid_caseD_allinone.py
# 依賴：requests（pip install requests）
# 用法：
# 1) 直接在下方 INLINE_* 位置填入金鑰，或改用系統環境變數 OKX_API_KEY/OKX_SECRET_KEY/OKX_PASSPHRASE
# 2) python okx_grid_caseD_allinone.py
#
# 安全預設：USE_DEMO=1（模擬盤）；要實盤改成 0

import os, time, hmac, base64, json, math, requests
from datetime import datetime, timezone

# ============ A) 直接填金鑰（優先使用；留空則會讀環境變數） ============
INLINE_OKX_API_KEY     = "db4993e3-dd90-4b8a-9d54-84532194a48e"   # 直接填你的 KEY；留空則讀環境變數 OKX_API_KEY
INLINE_OKX_SECRET_KEY  = "64B67F19057C9D358FC27B6382AD702D"   # 直接填你的 SECRET；留空則讀環境變數 OKX_SECRET_KEY
INLINE_OKX_PASSPHRASE  = "Asdfghjkl1!"   # 直接填你的 PASSPHRASE；留空則讀環境變數 OKX_PASSPHRASE

# ============ B) 策略/接駁層設定（可改） ============
INST_ID         = "SOL-USDT"   # 交易對（現貨示例）
GRID_RANGE      = 0.003        # 區間 ±0.3%（切成 10 等份 L0..L10；中心 L5）
POLL_INTERVAL_S = 2.0          # 查價節奏（秒）
USE_DEMO        = 0            # 1=模擬盤 / 0=實盤
USE_POST_ONLY   = 0            # 0=市價（驗證最穩） / 1=掛單 post_only（避免吃單）
PRINCIPAL_SRC   = "BALANCE"    # BALANCE=用帳戶 USDT 餘額 / FIXED=用固定金額
PRINCIPAL_USDT  = 100.0        # 當 PRINCIPAL_SRC=FIXED 才會用到
UNIT_PARTS      = 10           # 本金切幾等份（預設10 -> 每格1/10）
MAX_MINUTES     = 60           # 最長運行分鐘數（超時自動嘗試全平並結束）

# ============ C) 介面契約（策略層只用這 3 個） ============
# 1) get_rules(inst) -> minSz/lotSz/tickSz/buyLmt/sellLmt/last
# 2) place(order_payload) -> ordId
# 3) fills(ordId) -> sum(fillSz)

# ================= D) OKX 接駁層（簽名、HTTP、API） =================
BASE        = "https://www.okx.com"
API_KEY     = INLINE_OKX_API_KEY    or os.getenv("OKX_API_KEY", "")
SECRET_KEY  = INLINE_OKX_SECRET_KEY or os.getenv("OKX_SECRET_KEY", "")
PASSPHRASE  = INLINE_OKX_PASSPHRASE or os.getenv("OKX_PASSPHRASE", "")

def _assert_keys():
    if not (API_KEY and SECRET_KEY and PASSPHRASE):
        raise RuntimeError("請設定金鑰：填在檔案上方 INLINE_* 欄位，或用環境變數 OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE")

def ts():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond/1000):03d}Z"

def sign(ts_, method, path, body=""):
    pre = f"{ts_}{method}{path}{body}"
    return base64.b64encode(hmac.new(SECRET_KEY.encode(), pre.encode(), "sha256").digest()).decode()

def hdr(ts_, sig):
    h = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts_,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
    }
    if USE_DEMO:
        h["x-simulated-trading"] = "1"
    return h

def http_get(path, query=""):
    ts0 = ts(); qp = (f"?{query}" if query else "")
    sig = sign(ts0, "GET", path+qp)
    r = requests.get(BASE+path+qp, headers=hdr(ts0, sig), timeout=10)
    return r.json()

def http_post(path, payload):
    body = json.dumps(payload, separators=(",", ":"))
    ts0 = ts(); sig = sign(ts0, "POST", path, body)
    r = requests.post(BASE+path, headers=hdr(ts0, sig), data=body, timeout=10)
    return r.json()

def place(order_payload):
    """下單；回 ordId（失敗直接 raise，避免靜默錯誤）"""
    r = http_post("/api/v5/trade/order", order_payload)
    if r.get("code") != "0":
        raise RuntimeError(f"place error: {r}")
    return r["data"][0]["ordId"]

def fills(ordId, inst_id):
    """回傳 sum(fillSz) 作為實際幣數"""
    r = http_get("/api/v5/trade/fills-history", f"instType=SPOT&instId={inst_id}&ordId={ordId}&limit=100")
    ds = r.get("data", [])
    return sum(float(d["fillSz"]) for d in ds if d.get("ordId")==ordId)

def ticker(inst_id):
    tk = http_get("/api/v5/market/ticker", f"instId={inst_id}")["data"][0]
    return {"bid": float(tk["bidPx"]), "ask": float(tk["askPx"]), "last": float(tk["last"])}

def account_usdt():
    bal = http_get("/api/v5/account/balance")
    for d in bal["data"][0]["details"]:
        if d["ccy"] == "USDT":
            return float(d["cashBal"])
    return 0.0

def get_rules(inst_id):
    ins = http_get("/api/v5/public/instruments", f"instType=SPOT&instId={inst_id}")["data"][0]
    minSz  = float(ins["minSz"])
    lotSz  = float(ins["lotSz"])
    tickSz = float(ins["tickSz"])
    tk = ticker(inst_id)
    best_bid = tk["bid"]; best_ask = tk["ask"]
    return {"minSz":minSz, "lotSz":lotSz, "tickSz":tickSz,
            "buyLmt":best_ask, "sellLmt":best_bid, "last":tk["last"]}

# ================= E) Case D 策略（唯一標準邏輯） =================
class GridCaseD:
    """
    - L0..L10（中心 L5），中心價=啟動當下 last；區間=±GRID_RANGE
    - 上行：買新格(L+1)，賣前格(L) -> 只保留當前格，不堆疊
    - 下行：若新格(L-1)尚未持有才買，不賣其他 -> 下行累積
    - 回升：每跨一格賣掉前一格（由上行規則自然達成）
    - 觸 L0/L10：全平倉並結束 Grid
    - 同一格最多 1 份（Unit）
    - 單位倉 = 本金/UNIT_PARTS；買入以「USDT 金額市價」或 post_only 掛單（對帳以 fills 為準）
    """
    def __init__(self, inst_id, grid_range, unit_usdt, rules, use_post_only=False):
        self.inst = inst_id
        self.grid_range = grid_range
        self.unit_usdt = unit_usdt
        self.rules = rules
        self.use_post_only = use_post_only

        self.center = rules["last"]
        self.lower  = self.center * (1 - grid_range)
        self.upper  = self.center * (1 + grid_range)
        self.step_px = (self.upper - self.lower) / 10.0
        self.minSz = rules["minSz"]
        self.tickSz = rules["tickSz"]

        self.held = {}          # level -> qty
        self.prev_level = 5     # 啟動視為在 L5

        # 起點：買 L5
        qty = self._buy_unit(level=5)
        self.held[5] = self.held.get(5, 0.0) + qty
        print(f"[INIT] center={self.center:.6f} lower={self.lower:.6f} upper={self.upper:.6f} step={self.step_px:.6f}")
        self._print_position()

    # --- 輔助 ---
    def price_to_level(self, px):
        if px <= self.lower: return 0
        if px >= self.upper: return 10
        rel = (px - self.lower) / (self.upper - self.lower)  # 0..1
        lv = int(math.floor(rel * 10))
        return max(0, min(10, lv))

    def _buy_unit(self, level):
        if not self.use_post_only:
            payload = {
                "instId": self.inst, "tdMode": "cash", "side": "buy", "ordType": "market",
                "tgtCcy": "quote_ccy", "sz": f"{self.unit_usdt:.2f}"
            }
            ordId = place(payload)
            qty = fills(ordId, self.inst)
            print(f"[BUY MKT] L{level} ${self.unit_usdt:.2f} -> qty {qty:.8f}")
            return qty
        else:
            tk = ticker(self.inst)
            px = tk["bid"] + self.tickSz
            px = round(px / self.tickSz) * self.tickSz
            est_qty = max(self.minSz, (self.unit_usdt / px))
            est_qty = math.floor(est_qty / self.minSz) * self.minSz
            payload = {
                "instId": self.inst, "tdMode": "cash", "side": "buy", "ordType": "post_only",
                "px": f"{px:.8f}", "sz": f"{est_qty:.8f}"
            }
            ordId = place(payload)
            qty = fills(ordId, self.inst)
            print(f"[BUY PO ] L{level} px={px} est={est_qty:.8f} -> fills {qty:.8f}")
            return qty

    def _sell_qty(self, level, qty):
        if qty < self.minSz:
            print(f"[SKIP SELL] L{level} qty<{self.minSz} ({qty:.8f})")
            return 0.0
        if not self.use_post_only:
            payload = {
                "instId": self.inst, "tdMode": "cash", "side": "sell", "ordType": "market",
                "sz": f"{qty:.8f}"
            }
            ordId = place(payload)
            fq = fills(ordId, self.inst)
            print(f"[SELL MKT] L{level} qty {qty:.8f} -> fills {fq:.8f}")
            return fq
        else:
            tk = ticker(self.inst)
            px = tk["ask"] - self.tickSz
            px = round(px / self.tickSz) * self.tickSz
            payload = {
                "instId": self.inst, "tdMode": "cash", "side": "sell", "ordType": "post_only",
                "px": f"{px:.8f}", "sz": f"{qty:.8f}"
            }
            ordId = place(payload)
            fq = fills(ordId, self.inst)
            print(f"[SELL PO ] L{level} px={px} qty={qty:.8f} -> fills {fq:.8f}")
            return fq

    # --- 核心：處理跨格 ---
    def on_price(self, px):
        lv = self.price_to_level(px)
        if lv == self.prev_level:
            return
        step = 1 if lv > self.prev_level else -1
        cur = self.prev_level
        while cur != lv:
            nxt = cur + step
            if step == 1:
                # 上行：買新格(nxt)，賣前格(cur)
                qty_new = self._buy_unit(level=nxt)
                self.held[nxt] = self.held.get(nxt, 0.0) + qty_new
                if cur in self.held and self.held[cur] > 0:
                    sold = self._sell_qty(cur, self.held[cur])
                    self.held[cur] = max(0.0, self.held[cur]-sold)
                    if self.held[cur] <= 0: self.held.pop(cur, None)
                self._print_position()
            else:
                # 下行：新格尚未持有才買，不賣其他
                if self.held.get(nxt, 0.0) <= 0:
                    qty_new = self._buy_unit(level=nxt)
                    self.held[nxt] = self.held.get(nxt, 0.0) + qty_new
                    self._print_position()
            cur = nxt

        self.prev_level = lv

        # 邊界：L0 / L10 全平並結束
        if lv in (0, 10):
            print(f"[BOUNDARY] L{lv} reached -> flatten all and stop")
            self.flatten_all()
            raise SystemExit(0)

    def flatten_all(self):
        for lv in sorted(list(self.held.keys()), reverse=True):
            qty = self.held.get(lv, 0.0)
            if qty > 0:
                sold = self._sell_qty(lv, qty)
                self.held[lv] = max(0.0, qty - sold)
                if self.held[lv] <= 0: self.held.pop(lv, None)
        self._print_position(tag="[FLATTENED]")

    def _print_position(self, tag="[POS]"):
        ks = sorted(self.held.keys())
        view = ",".join([f"L{k}:{self.held[k]:.6f}" for k in ks])
        print(f"{tag} {{ {view} }}")

# ================= F) 主流程：啟動一個 Grid =================
def main():
    _assert_keys()
    rules = get_rules(INST_ID)
    print(f"[RULES] minSz={rules['minSz']} lotSz={rules['lotSz']} tickSz={rules['tickSz']} last={rules['last']}  MODE={'DEMO' if USE_DEMO else 'LIVE'} PO={USE_POST_ONLY}")

    if PRINCIPAL_SRC.upper() == "BALANCE":
        principal = account_usdt()
        if principal <= 0:
            raise RuntimeError("USDT 餘額為 0，請入金或改用 FIXED 模式（PRINCIPAL_SRC='FIXED'）")
    else:
        principal = float(PRINCIPAL_USDT)
        if principal <= 0:
            raise RuntimeError("請設定 PRINCIPAL_USDT > 0")

    unit_usdt = principal / float(UNIT_PARTS)
    print(f"[FUNDS] principal={principal:.2f} USDT  unit={unit_usdt:.2f} USDT  parts={UNIT_PARTS}")

    grid = GridCaseD(INST_ID, GRID_RANGE, unit_usdt, rules, use_post_only=bool(USE_POST_ONLY))

    t0 = time.time()
    while True:
        tk = ticker(INST_ID)
        last = tk["last"]
        try:
            grid.on_price(last)
        except SystemExit:
            print("[EXIT] Grid finished by boundary.")
            break
        if (time.time() - t0) > MAX_MINUTES * 60:
            print("[TIMEUP] 超時 -> 嘗試全平並結束")
            grid.flatten_all()
            break
        time.sleep(POLL_INTERVAL_S)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] 手動中斷")
