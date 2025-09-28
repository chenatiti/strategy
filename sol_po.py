# okx_grid_spot.py
# -*- coding: utf-8 -*-
"""
OKX 現貨網格（SOL-USDT）— 多網格（A/B/C...）定時啟動、Maker限價、終端回報、可調邊界%、輪詢頻率、Debug 與 Dry-run
策略要點（符合你的敘述）：
- 網格離散為 0..10，共 11 個價位，起始於 5（中心價），並把「當前價上下邊界」用百分比決定，均分為 10 格。
- 上行：每往上跨到新格 L 時，若 L-1 有倉則賣掉 L-1 同時在 L 買一份。
- 下行：每往下跨到新格 L-1 時，若 L-1 未持倉則買一份（累積底部籌碼）。
- 回撤上來：跨回 L 時，若 L-1 有倉則賣掉 L-1（實現該一格利潤）。
- 觸及 0 或 10（上下邊界格）時，關閉該網格並清倉。
- 每個網格最多動用「本金的 1/10」作為單一倉位金額（USDT），可選擇用固定金額或用帳戶可用 USDT 百分比自動換算。
- 分針 0/15/30/45 建立新網格（A、B、C… 可同時運行）。
"""

import time
import math
import hmac
import base64
import json
import threading
from datetime import datetime, timezone
import requests
from typing import Dict, List, Optional

# ========== 你會常改的參數（全部集中在這） ===================================

# ---- API 與環境 ----
OKX_BASE_URL = "https://www.okx.com"  # 實盤
API_KEY      = "db4993e3-dd90-4b8a-9d54-84532194a48e"  # 假的示範 key（請換成你的）
API_SECRET   = "64B67F19057C9D358FC27B6382AD702D"      # 假的示範 secret（請換成你的）
PASSPHRASE   = "Asdfghjkl1!"                            # 假的示範 passphrase（請換成你的）

# 是否啟用乾跑（不送單，只模擬與印出）
DRY_RUN      = True

# 額外輸出除錯資訊（包含即將送出的 request payload 等）
DEBUG        = True

# ---- 交易與網格設定 ----
INST_ID              = "SOL-USDT"   # 現貨品種
MAKER_ONLY           = True         # 用 Maker（掛單）為主，盡量避免吃單
GRID_BOUNDS_PCT      = 0.0001       # 上下邊界 ±0.01%（0.0001 = 0.01%）
GRID_LEVELS          = 10           # 0..10 共 10 段（11個點），保持 10
POLL_SECONDS         = 2.0          # 查價頻率（秒），可自行調整
NEW_GRID_MINUTES     = {0, 15, 30, 45}   # 在這些分針啟動新網格
MAX_CONCURRENT_GRIDS = 6            # 同時最多幾個網格（A、B、C…）
ORDER_USDT_FIXED     = 10.0         # 單筆下單 USDT 固定金額（若未啟用百分比）
USE_BALANCE_PERCENT  = False        # 若 True，忽略上面的固定值，用可用 USDT 的百分比
ORDER_USDT_PCT_OF_BAL= 0.10         # 若啟用，用可用 USDT 的 10% 當作單筆金額（示例）

# ---- 風險與精度 ----
SLIPPAGE_ALLOW_PCT   = 0.0002       # 限價相對網格價位之最大偏差（避免價格跳動掛不到）
ROUND_PRICE_TO_TICK  = True         # 依照 OKX tick size 四捨五入價格
ROUND_SZ_TO_LOT      = True         # 依照最小數量規則四捨五入數量

# ---- 顯示/標記 ----
CONSOLE_WIDTH        = 120          # 終端輸出寬度（美觀用）

# ============================================================================

# ====== OKX API 通用簽名與呼叫 ======
def _ts():
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _sign(timestamp, method, path, body):
    prehash = f"{timestamp}{method}{path}{body or ''}"
    h = hmac.new(API_SECRET.encode(), prehash.encode(), digestmod="sha256")
    return base64.b64encode(h.digest()).decode()

def _headers(method, path, body=""):
    ts = _ts()
    sign = _sign(ts, method.upper(), path, body)
    return {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }

def okx_get(path, params=None):
    url = OKX_BASE_URL + path
    if params:
        url += "?" + "&".join([f"{k}={v}" for k,v in params.items()])
        body = ""
    else:
        body = ""
    if DEBUG:
        print(f"[DEBUG] GET {path} {params or ''}")
    r = requests.get(url, headers=_headers("GET", path, body), timeout=10)
    r.raise_for_status()
    return r.json()

def okx_post(path, payload):
    url = OKX_BASE_URL + path
    body = json.dumps(payload)
    if DEBUG:
        print(f"[DEBUG] POST {path} payload={payload}")
    if DRY_RUN:
        # 乾跑：不送網路請求，但模擬一個 OKX 成功回覆
        return {"code":"0","msg":"","data":[{"ordId":"DRYRUN-"+str(int(time.time()*1000))}]}
    r = requests.post(url, headers=_headers("POST", path, body), data=body, timeout=10)
    r.raise_for_status()
    return r.json()

# ====== 市場、規則、下單 ======
def get_ticker_price(instId: str) -> float:
    data = okx_get("/api/v5/market/ticker", {"instId":instId})
    return float(data["data"][0]["last"])

def get_instrument_info(instId: str):
    data = okx_get("/api/v5/public/instruments", {"instType":"SPOT", "instId":instId})
    d = data["data"][0]
    return {
        "tickSz": float(d["tickSz"]),
        "lotSz": float(d["lotSz"]),
        "minSz": float(d["minSz"])
    }

def get_balance_usdt() -> float:
    # 使用資金帳戶（Funding）或交易帳戶（Trading）可依需求調整；此處取交易帳戶可用 USDT
    # /api/v5/account/balance
    data = okx_get("/api/v5/account/balance")
    total = 0.0
    for acc in data.get("data", []):
        for d in acc.get("details", []):
            if d["ccy"] == "USDT":
                total += float(d["availBal"])
    return total

def round_to_step(x: float, step: float) -> float:
    if step <= 0: 
        return x
    return math.floor(x / step + 1e-12) * step

def place_limit_order(instId: str, side: str, px: float, usdt_amt: float, tickSz: float, lotSz: float, minSz: float):
    # 現貨用 tdMode='cash'；數量使用 base 幣（SOL）數量
    if ROUND_PRICE_TO_TICK:
        px = round_to_step(px, tickSz)
    # 以 USDT 金額轉為 SOL 數量
    if px <= 0:
        raise ValueError("價格不可為 0")
    sz = usdt_amt / px
    if ROUND_SZ_TO_LOT and lotSz > 0:
        sz = round_to_step(sz, lotSz)
    if sz < minSz:
        raise ValueError(f"下單數量 {sz} 小於最小數量 {minSz}")
    payload = {
        "instId": instId,
        "tdMode": "cash",
        "side": side,           # "buy" or "sell"
        "ordType": "limit",
        "px": str(px),
        "sz": str(sz)
    }
    if MAKER_ONLY:
        payload["tgtCcy"] = "base_ccy"  # 現貨以 base 幣為主
        payload["reduceOnly"] = "false"
        # OKX 沒有直接的 postOnly 旗標，若要更嚴格可用價格略優於買/賣一跳、或失敗時重試掛更遠價
        # 實務上可用「timeInForce":"postOnly"」但 OKX v5 現貨不支援該字段；這裡透過掛遠一點避免吃單。
    resp = okx_post("/api/v5/trade/order", payload)
    ok = (resp.get("code") == "0")
    if not ok:
        raise RuntimeError(f"下單失敗: {resp}")
    return resp["data"][0]["ordId"]

# ====== 網格物件 ======
class GridState:
    """
    一個獨立網格的狀態：
    - levels_holding: set，紀錄手上持有的格位（如 {2,3,4,5}）
    - last_level: 前一次所在格（用來判斷跨格方向）
    - alive: 是否仍運行（觸頂/觸底即 False）
    - tag: 'A','B','C'... 代表此網格名稱
    - entry_time: 啟動時間
    """
    def __init__(self, tag: str, levels: int, usdt_each: float, px_center: float, px_low: float, px_high: float):
        self.tag = tag
        self.levels = levels
        self.usdt_each = usdt_each
        self.px_low = px_low
        self.px_high = px_high
        self.px_center = px_center
        self.level_prices = [px_low + (px_high - px_low) * (i/levels) for i in range(levels+1)]
        self.levels_holding = set([5])  # 起始於 5 買一份
        self.last_level = 5
        self.alive = True
        self.entry_time = datetime.now()

    def summarize(self) -> str:
        held = sorted(list(self.levels_holding))
        return f"{self.tag} {held}"

    def price_to_level(self, px: float) -> int:
        # 找離散格位（0..10）
        # 使用最接近的格位（boundary 投影）
        if px <= self.level_prices[0]:
            return 0
        if px >= self.level_prices[-1]:
            return self.levels
        # 找最接近的 index
        rel = (px - self.px_low)/(self.px_high - self.px_low)
        lvl = int(round(rel * self.levels))
        return max(0, min(self.levels, lvl))

# ====== 主管理器 ======
class GridManager:
    def __init__(self):
        self.grids: Dict[str, GridState] = {}
        self.next_tag_ord = 0           # 0->A,1->B...
        self.last_grid_minute = None
        self.ins_info = get_instrument_info(INST_ID)

    def _next_tag(self) -> str:
        tag = chr(ord('A') + (self.next_tag_ord % 26))
        self.next_tag_ord += 1
        return tag

    def _calc_bounds(self, px_now: float):
        pct = GRID_BOUNDS_PCT
        low = px_now * (1 - pct)
        high = px_now * (1 + pct)
        center = (low + high)/2
        return center, low, high

    def _usdt_each_order(self) -> float:
        if USE_BALANCE_PERCENT:
            bal = get_balance_usdt()
            return max(1.0, bal * ORDER_USDT_PCT_OF_BAL)
        return ORDER_USDT_FIXED

    def maybe_start_new_grid(self, minute_now: int, px_now: float):
        if minute_now not in NEW_GRID_MINUTES:
            return
        if self.last_grid_minute == minute_now:
            return
        if len(self.grids) >= MAX_CONCURRENT_GRIDS:
            return
        tag = self._next_tag()
        center, low, high = self._calc_bounds(px_now)
        g = GridState(tag, GRID_LEVELS, self._usdt_each_order(), center, low, high)
        # 起手：在 5 買一份（若 DRY_RUN 就記錄；實單就掛單）
        try:
            self._ensure_level_bought(g, 5, px_now)
        except Exception as e:
            print(f"[{ts()}] [WARN] {tag} 初始買入失敗：{e}")
        self.grids[tag] = g
        self.last_grid_minute = minute_now
        print(f"[{ts()}] 啟動新網格 {tag}，區間[{low:.6f}, {high:.6f}] 中心{center:.6f}，單筆USDT≈{g.usdt_each:.4f}")

    def _ensure_level_bought(self, g: GridState, lvl: int, px_now: float):
        if lvl in g.levels_holding:
            return
        # 下買單：以該格理論價位為目標；為降低吃單機率，掛在理論價位*(1 - SLIPPAGE_ALLOW_PCT)
        target_px = g.level_prices[lvl] * (1 - SLIPPAGE_ALLOW_PCT if MAKER_ONLY else 1.0)
        _ = place_limit_order(INST_ID, "buy", target_px, g.usdt_each, 
                              self.ins_info["tickSz"], self.ins_info["lotSz"], self.ins_info["minSz"])
        g.levels_holding.add(lvl)
        print(f"[{ts()}] {g.tag} 買入 level {lvl} @ ~{target_px:.6f}（理論價 {g.level_prices[lvl]:.6f}）")

    def _ensure_level_sold(self, g: GridState, lvl: int):
        if lvl not in g.levels_holding:
            return
        # 以該格理論價位略高掛單
        target_px = g.level_prices[lvl] * (1 + SLIPPAGE_ALLOW_PCT if MAKER_ONLY else 1.0)
        _ = place_limit_order(INST_ID, "sell", target_px, g.usdt_each, 
                              self.ins_info["tickSz"], self.ins_info["lotSz"], self.ins_info["minSz"])
        g.levels_holding.remove(lvl)
        print(f"[{ts()}] {g.tag} 賣出 level {lvl} @ ~{target_px:.6f}（理論價 {g.level_prices[lvl]:.6f}）")

    def _close_all_levels(self, g: GridState):
        # 逐一清倉（以略優價掛單）
        for lvl in sorted(list(g.levels_holding)):
            try:
                self._ensure_level_sold(g, lvl)
            except Exception as e:
                print(f"[{ts()}] [WARN] {g.tag} 清倉失敗 level {lvl}: {e}")
        g.alive = False
        print(f"[{ts()}] {g.tag} 觸頂/觸底，網格已關閉並清倉。")

    def on_price(self, px_now: float):
        # 更新每個網格
        to_delete = []
        for tag, g in self.grids.items():
            if not g.alive:
                to_delete.append(tag)
                continue
            lvl_now = g.price_to_level(px_now)
            # 觸邊界：關閉並清倉
            if lvl_now == 0 or lvl_now == g.levels:
                self._close_all_levels(g)
                to_delete.append(tag)
                continue

            # 往上跨格：賣掉 L-1，並在 L 買一份（若未持有）
            if lvl_now > g.last_level:
                # 先賣掉 last_level（若有持倉）
                if g.last_level in g.levels_holding:
                    self._ensure_level_sold(g, g.last_level)
                # 再確保新格位買到
                self._ensure_level_bought(g, lvl_now, px_now)

            # 往下跨格：在新較低格位買一份（若未持有）
            elif lvl_now < g.last_level:
                self._ensure_level_bought(g, lvl_now, px_now)

            # 回撤到同格不動作
            g.last_level = lvl_now

        for tag in to_delete:
            self.grids.pop(tag, None)

    def status_line(self) -> str:
        parts = [g.summarize() for g in self.grids.values()]
        return " | ".join(parts) if parts else "(無網格運行)"

# ====== 小工具 ======
def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def banner_line(ch="-"):
    return ch * CONSOLE_WIDTH

# ====== 主程式 ======
def main():
    print(banner_line("="))
    print(f"[{ts()}] 啟動 OKX SOL-USDT 現貨網格（DRY_RUN={DRY_RUN}, DEBUG={DEBUG}）")
    print(f"分針建立新網格於：{sorted(list(NEW_GRID_MINUTES))}；輪詢頻率：{POLL_SECONDS}s；邊界±{GRID_BOUNDS_PCT*100:.4f}%")
    print(banner_line("="))

    mgr = GridManager()
    last_minute = None

    while True:
        try:
            px = get_ticker_price(INST_ID)
            now = datetime.now()
            minute_now = now.minute

            # 分針到點 -> 新網格（A/B/C...）
            mgr.maybe_start_new_grid(minute_now, px)

            # 推進所有網格狀態
            mgr.on_price(px)

            # 終端輸出
            print(f"[{ts()}] px={px:.6f} | 運行：{mgr.status_line()}")

            time.sleep(POLL_SECONDS)

        except requests.HTTPError as e:
            print(f"[{ts()}] [HTTP ERROR] {e}")
            if DEBUG:
                try:
                    print(e.response.text)
                except Exception:
                    pass
            time.sleep(2.5)
        except Exception as e:
            print(f"[{ts()}] [ERROR] {e}")
            time.sleep(2.5)

if __name__ == "__main__":
    main()
