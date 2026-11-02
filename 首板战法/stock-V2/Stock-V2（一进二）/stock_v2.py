#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock V2 — 双路径首板选股（与 V1 stock.py 完全隔离）

权威规范：同目录 Stock_V2_双路径首板选股模型_开发规范.md
实施约定：Stock_V2_实施设计_待确认.md（已确认 A1/B1 + UI + 定时）

时间轴（交易日 D1）：
  09:24 cron 进场 → 等到 09:25（禁止秒退）
  09:25:00  集合竞价撮合后取真实竞价（腾讯快照；禁现价冒充；不占 tdx 配额）
  09:27–09:28  发起 PushPlus 单次（本人+好友）
  09:28  watchdog 缺结果/缺通知则补发（可 LATE）
  09:29:00  硬截止；过点仍须补发，禁止因过点不发
  历史回补用 --backfill（今开≈竞价价）；早盘通知优先于一切 hist

用法：
  python3 stock_v2.py --stage d0-preselect
  python3 stock_v2.py --stage auction-final --notify
  python3 stock_v2.py --stage probe-auction --codes 600000,000001
  python3 stock_v2.py --backfill --date 2026-10-27   # 历史：今开≈竞价价，标 BACKFILL_OPEN
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time as time_mod
from datetime import datetime, timedelta, time
from typing import Any, Optional

import pandas as pd
from requests.exceptions import RequestException

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHOU = os.path.dirname(os.path.dirname(_HERE))  # 首板战法（stock-V2/一进二 → 上两级）
_PUBLIC = os.path.join(_SHOU, "公共")
_SHARED_LIB = os.path.join(_SHOU, "规范文档", "共享取数", "lib")
_UI = os.path.join(_SHOU, "交易记录UI")
_UI_LIB = os.path.join(_UI, "lib")
_UI_JOBS = os.path.join(_UI, "jobs")
for p in (_SHARED_LIB, _PUBLIC, _SHOU, _UI, _UI_LIB, _UI_JOBS, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

_user_site = os.path.expanduser("~/.local/lib/python3.13/site-packages")
if _user_site not in sys.path:
    sys.path.insert(0, _user_site)

import akshare as ak  # noqa: E402
import paths  # noqa: E402
import stock_meta  # noqa: E402

PROGRAM = "stock_v2"
RESULTS_DIR = os.path.join(_HERE, "results")
CACHE_DIR = os.path.join(_HERE, "cache")
TRADES_DIR = os.path.join(_HERE, "trades")

AUCTION_OPEN = time(9, 25, 0)
PUSH_TARGET = time(9, 27, 0)       # 目标推送起点（用户 9:27–9:28 收到）
PUSH_DEADLINE = time(9, 28, 30)    # 扫票/推送软截止
HARD_DEADLINE = time(9, 29, 0)     # 硬截止：最迟 9:29 发出

TX_SLEEP = 0.4
TX_PROBE_SLEEP = 1.0
USER_PRICE_RUNUP_CAP = 0.90
STREAK_LOOKBACK_TD = 30
STREAK_REJECT_AT = 3
PATH_A_MIN, PATH_A_MAX = 2.0, 6.0
PATH_B_MIN, PATH_B_MAX_EXCL = -3.0, 2.0
SCORE_A_MIN, SCORE_B_MIN = 75.0, 82.0
MAX_RECS = 2  # 旧：A 优先凑满 2；对比期改为每路径各 2
MAX_PER_PATH = 2
# 对比期分配：A 第1/第2 + B 第1/第2 共最多 4 条（稳定后再收回正式阈值）
ALLOCATION_COMPARE = "A2_B2_COMPARE"
ALLOCATION_MODE = ALLOCATION_COMPARE
# 通知/正文路径标题（对比期模板）
PATH_TITLE = {
    "A": "高开一进二",
    "B": "平开或低开，求稳",
}
PATH_RANK_ZH = {1: "第一名", 2: "第二名"}

_ZT_CACHE: dict[str, pd.DataFrame] = {}
_HIST_CACHE: dict[str, pd.DataFrame] = {}
_ZT_COLS = (
    "序号", "代码", "名称", "涨跌幅", "最新价", "成交额",
    "流通市值", "总市值", "换手率", "封板资金", "首次封板时间",
    "最后封板时间", "炸板次数", "涨停统计", "连板数", "所属行业",
)


# ── 工具 ──────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ensure_dirs() -> None:
    for d in (RESULTS_DIR, CACHE_DIR, TRADES_DIR):
        os.makedirs(d, exist_ok=True)


def _retry(desc, fn, *a, max_attempts=4, base_delay=1.0, **kw):
    last = None
    for i in range(1, max_attempts + 1):
        try:
            return fn(*a, **kw)
        except (RequestException, OSError, ValueError) as e:
            last = e
            if i < max_attempts:
                time_mod.sleep(base_delay * (2 ** (i - 1)))
    raise last  # type: ignore


def beijing_now() -> datetime:
    return datetime.now()


def _wait_until(t: time, label: str) -> None:
    while beijing_now().time() < t:
        left = (
            datetime.combine(beijing_now().date(), t) - beijing_now()
        ).total_seconds()
        log(f"等待 {label}（约 {max(0, left):.0f}s）…")
        time_mod.sleep(min(2.0, max(0.2, left)))


def prev_trading_day(d: datetime) -> datetime:
    import fetch_calendar as fc
    return fc.prev_trading_day_dt(d)


def next_trading_day(d: datetime) -> datetime:
    import fetch_calendar as fc
    return fc.next_trading_day_dt(d)


def trading_days_back(end: datetime, n: int) -> list[datetime]:
    """含 end 共 n 个交易日，升序。"""
    out = [end]
    cur = end
    while len(out) < n:
        cur = prev_trading_day(cur)
        out.append(cur)
    return list(reversed(out))


def board_limit_pct(code: str) -> float:
    c = str(code).zfill(6)
    if c[:3] in ("300", "301", "688"):
        return 20.0
    if c[:1] in ("8", "4") or c[:2] == "92":
        return 30.0
    return 10.0


def allowed_code(code: str) -> bool:
    c = str(code).zfill(6)
    if c.startswith(("688", "92")):
        return False
    return c[:3] in (
        "600", "601", "603", "605", "000", "001", "002", "003", "300", "301"
    )


def piecewise_score(x: float, knots: list[tuple[float, float]], cap: float) -> float:
    if not knots:
        return 0.0
    knots = sorted(knots, key=lambda p: p[0])
    if x <= knots[0][0]:
        return float(max(0.0, min(cap, knots[0][1])))
    if x >= knots[-1][0]:
        return float(max(0.0, min(cap, knots[-1][1])))
    for i in range(len(knots) - 1):
        x0, y0 = knots[i]
        x1, y1 = knots[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return float(max(0.0, min(cap, y1)))
            y = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
            return float(max(0.0, min(cap, y)))
    return 0.0


# ── 涨停池 / 日线 ────────────────────────────────────────────────────

def fetch_zt_pool(date_yyyymmdd: str) -> pd.DataFrame:
    """
    Stage A 涨停池：走共享门面（磁盘→东财→同花顺），禁止只打东财。
    规范：首板战法/规范文档/共享取数/历史涨停池与买卖竞价取数规范.md
    """
    if date_yyyymmdd in _ZT_CACHE:
        return _ZT_CACHE[date_yyyymmdd]
    try:
        from hist_market_data import fetch_limit_up_pool, pool_meta

        df = fetch_limit_up_pool(date_yyyymmdd, prefer="em", allow_sparse=True)
        meta = pool_meta(df)
        log(f"涨停池 {date_yyyymmdd} shared → {meta}")
    except Exception as e:
        log(f"共享涨停池失败，回退东财单源: {e}")
        df = _retry(
            f"涨停池 {date_yyyymmdd}",
            ak.stock_zt_pool_em,
            date=date_yyyymmdd,
            max_attempts=2,
            base_delay=1.0,
        )
    if df is None or getattr(df, "empty", True) or "代码" not in getattr(df, "columns", []):
        df = pd.DataFrame(columns=list(_ZT_COLS))
    _ZT_CACHE[date_yyyymmdd] = df
    return df


def _http_get(url: str, *, timeout: float = 12, referer: str = "https://quote.eastmoney.com/") -> Optional[str]:
    """直连 HTTP（忽略坏代理 env），带短重试。"""
    import requests

    last = None
    for i in range(3):
        try:
            sess = requests.Session()
            sess.trust_env = False
            r = sess.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": referer},
                timeout=timeout,
            )
            if r.status_code == 200 and r.text:
                return r.text
            last = f"status={r.status_code}"
        except Exception as e:
            last = e
        time_mod.sleep(0.35 * (i + 1))
    if last:
        log(f"HTTP 重试耗尽: {last}")
    return None


def _clear_proxy_env():
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
    return {k: os.environ.pop(k) for k in keys if k in os.environ}


def fetch_daily_hist(code: str, start: str, end: str) -> pd.DataFrame:
    """日线；start/end=YYYYMMDD。腾讯→东财→akshare，全程绕开坏代理。"""
    code = str(code).zfill(6)
    cache_key = f"{code}_{start}_{end}"
    if cache_key in _HIST_CACHE:
        return _HIST_CACHE[cache_key]
    old = _clear_proxy_env()
    try:
        # 1) 腾讯日线（与 price_service 一致，较稳）
        try:
            prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
            df_tx = ak.stock_zh_a_hist_tx(
                symbol=f"{prefix}{code}", start_date=start, end_date=end, adjust="",
            )
            if df_tx is not None and not df_tx.empty:
                df = df_tx.rename(columns={
                    "date": "日期", "open": "开盘", "close": "收盘",
                    "high": "最高", "low": "最低",
                })
                time_mod.sleep(0.15)
                _HIST_CACHE[cache_key] = df
                return df
        except Exception as e:
            log(f"腾讯日线失败 {code}: {e}")

        # 2) 东财 kline 直连
        secid = f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&ut=7eea3edcaed734bea9cbfc24409ed989&klt=101&fqt=0&beg={start}&end={end}"
        )
        try:
            text = _http_get(url)
            if text:
                import json as _json
                klines = (((_json.loads(text) or {}).get("data") or {}).get("klines")) or []
                rows = []
                for line in klines:
                    parts = line.split(",")
                    if len(parts) < 7:
                        continue
                    rows.append({
                        "日期": parts[0],
                        "开盘": float(parts[1] or 0),
                        "收盘": float(parts[2] or 0),
                        "最高": float(parts[3] or 0),
                        "最低": float(parts[4] or 0),
                        "成交量": float(parts[5] or 0),
                        "成交额": float(parts[6] or 0),
                        "涨跌幅": float(parts[8] or 0) if len(parts) > 8 else 0.0,
                    })
                if rows:
                    time_mod.sleep(0.12)
                    df = pd.DataFrame(rows)
                    _HIST_CACHE[cache_key] = df
                    return df
        except Exception as e:
            log(f"东财日线失败 {code}: {e}")

        # 3) akshare 东财
        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end, adjust="",
            )
            if df is not None and not df.empty:
                _HIST_CACHE[cache_key] = df
                return df
        except Exception as e:
            log(f"日线失败 {code}: {e}")
        empty = pd.DataFrame()
        _HIST_CACHE[cache_key] = empty
        return empty
    finally:
        os.environ.update(old)


def day_is_limit_up_from_ohlc(row, limit_pct: float) -> bool:
    try:
        pre = float(row.get("昨收") or 0)  # akshare 可能无昨收
    except Exception:
        pre = 0.0
    try:
        close = float(row["收盘"])
        # 无昨收时用开盘近似不判断
        if "收盘" not in row:
            return False
    except Exception:
        return False
    # akshare hist 通常无昨收列；用前一行收盘
    return False  # 由调用方用序列算


def mark_limit_ups(hist: pd.DataFrame, code: str) -> list[tuple[str, bool]]:
    """返回 [(date_str, is_limit_up), ...] 按日期升序。"""
    if hist is None or hist.empty or "收盘" not in hist.columns or "日期" not in hist.columns:
        return []
    lim = board_limit_pct(code)
    h = hist.copy()
    h["日期"] = pd.to_datetime(h["日期"]).dt.strftime("%Y-%m-%d")
    h = h.sort_values("日期")
    closes = h["收盘"].astype(float).tolist()
    dates = h["日期"].tolist()
    out = []
    for i, d in enumerate(dates):
        if i == 0:
            out.append((d, False))
            continue
        pre, close = closes[i - 1], closes[i]
        if pre <= 0:
            out.append((d, False))
            continue
        # 贴近涨停价（考虑最小价差）
        thr = pre * (1.0 + lim / 100.0)
        out.append((d, close + 1e-6 >= thr * 0.995))
    return out


# ── 腾讯竞价 ──────────────────────────────────────────────────────────

def _tx_symbol(code: str) -> str:
    c = str(code).zfill(6)
    return f"sh{c}" if c.startswith(("5", "6", "9")) else f"sz{c}"


def fetch_tencent_snapshot(code: str) -> Optional[dict[str, Any]]:
    """
    腾讯单票快照。9:25 后：今开=竞价成交价；量/额为截至当前累计（9:25~9:26 近似竞价）。
    今开未落地 → None。绝不用现价冒充开盘价。
    """
    import requests

    code = str(code).zfill(6)
    try:
        sess = requests.Session()
        sess.trust_env = False
        r = sess.get(
            f"http://qt.gtimg.cn/q={_tx_symbol(code)}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"},
            timeout=6,
        )
        text = (r.text or "").strip()
    except Exception as e:
        log(f"腾讯失败 {code}: {e}")
        return None
    for line in text.split(";"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        try:
            payload = line.split('="', 1)[1].rstrip('";')
            parts = payload.split("~")
            if len(parts) < 6:
                continue
            name = parts[1]
            last = float(parts[3] or 0)
            pre = float(parts[4] or 0)
            open_p = float(parts[5] or 0)
            # 手；腾讯常见 [6]/[36] 成交量（手）
            vol_hand = float(parts[6] or parts[36] or 0)
            # [37] 常见为成交额（万元）
            amt_wan = float(parts[37] or 0) if len(parts) > 37 else 0.0
            if pre <= 0 or open_p <= 0:
                return None
            gap = round((open_p - pre) / pre * 100.0, 3)
            vol_shares = vol_hand * 100.0
            amount = amt_wan * 10000.0 if amt_wan > 0 else open_p * vol_shares
            return {
                "code": code,
                "name": name,
                "pre_close": pre,
                "auction_price": open_p,
                "last": last,
                "auction_gap_pct": gap,
                "auction_volume_shares": vol_shares,
                "auction_amount": amount,
                "source": "tencent_qt",
                "asof": beijing_now().isoformat(timespec="seconds"),
                "quality": "VERIFIED" if vol_shares > 0 and amount > 0 else "PARTIAL",
            }
        except (ValueError, IndexError, TypeError):
            continue
    return None


def in_live_auction_window(now: Optional[datetime] = None) -> bool:
    """生产终选仅允许 09:25:00–09:29:00（含边界）。"""
    t = (now or beijing_now()).time()
    return AUCTION_OPEN <= t <= HARD_DEADLINE


def wait_auction_ready(*, force: bool = False, probe: str = "600000") -> None:
    if force:
        return
    if beijing_now().time() < AUCTION_OPEN:
        _wait_until(AUCTION_OPEN, "集合竞价 09:25")
    deadline = datetime.combine(beijing_now().date(), HARD_DEADLINE)
    while beijing_now() < deadline:
        snap = fetch_tencent_snapshot(probe)
        time_mod.sleep(TX_SLEEP)
        if snap and snap.get("auction_price"):
            log(f"竞价已落地（探针 {probe} gap={snap['auction_gap_pct']:+.2f}%）")
            return
        log("竞价未落地，继续探测…")
        time_mod.sleep(TX_PROBE_SLEEP)
    log("⚠ 硬截止前探针仍无完整竞价，后续按逐票质量门禁处理")


class TdxQuotaStop(RuntimeError):
    """通达信/mootdx 日/小时软上限已到（≤硬上限×70%），必须停跑防封号。"""


def fetch_mootdx_auction_0925(code: str, d1: str) -> Optional[dict[str, Any]]:
    """
    历史真实竞价：通达信/mootdx 历史分笔中 time=09:25 撮合行。
    铁律：每次 transactions 经 platform_gate(tdx)，日/小时实际 ≤ 硬上限×70%。
    vol=手；amount=price*vol*100。缺票则 None（禁止填 0 冒充）。
    """
    from platform_gate import platform_slot, PlatformQuotaExceeded, mark_rate_limited

    code = str(code).zfill(6)
    d8 = d1.replace("-", "")
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std", multithread=False, heartbeat=True)
        hit_rows = []
        for start in range(0, 30000, 800):
            try:
                with platform_slot("tdx"):
                    df = client.transactions(symbol=code, start=start, offset=800, date=d8)
            except PlatformQuotaExceeded as e:
                raise TdxQuotaStop(str(e)) from e
            except Exception as e:
                err = str(e).lower()
                if any(x in err for x in ("429", "ban", "forbidden", "拒绝", "封")):
                    mark_rate_limited("tdx", f"observed:{e}")
                    raise TdxQuotaStop(f"tdx_rate_limited:{e}") from e
                raise
            if df is None or len(df) == 0:
                break
            hit = df[df["time"].astype(str).str.startswith("09:25")]
            if len(hit):
                hit_rows = hit.to_dict("records")
                break
            times = df["time"].astype(str).tolist()
            if any(t.startswith("09:15") for t in times) and not any(t.startswith("09:25") for t in times):
                break
        if not hit_rows:
            return None
        row = hit_rows[-1]
        price = float(row.get("price") or 0)
        vol_hand = float(row.get("vol") or row.get("volume") or 0)
        if price <= 0 or vol_hand <= 0:
            return None
        amount = price * vol_hand * 100.0
        hist = fetch_daily_hist(
            code,
            (datetime.strptime(d1, "%Y-%m-%d") - timedelta(days=12)).strftime("%Y%m%d"),
            d8,
        )
        pre = 0.0
        if hist is not None and not hist.empty:
            h = hist.copy()
            h["日期"] = pd.to_datetime(h["日期"]).dt.strftime("%Y-%m-%d")
            prev = h[h["日期"] < d1].sort_values("日期")
            if not prev.empty:
                pre = float(prev.iloc[-1].get("收盘") or 0)
        if pre <= 0:
            return None
        gap = round((price - pre) / pre * 100.0, 3)
        return {
            "code": code,
            "name": None,
            "pre_close": pre,
            "auction_price": price,
            "last": price,
            "auction_gap_pct": gap,
            "auction_volume_shares": vol_hand * 100.0,
            "auction_amount": amount,
            "source": "mootdx_tick_0925",
            "asof": f"{d1}T09:25:00",
            "quality": "VERIFIED",
        }
    except TdxQuotaStop:
        raise
    except Exception as e:
        log(f"mootdx 竞价失败 {code} {d1}: {e}")
        return None


def fetch_open_proxy_auction(code: str, d1: str) -> Optional[dict[str, Any]]:
    """已废弃作主回补：仅作探针对照，不参与正式评分。"""
    return None


_SAME_DAY_MOOTDX_READY: Optional[bool] = None


def fetch_backfill_auction(code: str, d1: str) -> Optional[dict[str, Any]]:
    """历史回补入口。

    优先通达信 09:25 真分笔（VERIFIED）。
    若为「今天」且分笔尚未入库（常见于盘中），降级为腾讯今开价差 + D0 因子
    （quality=BACKFILL_OPEN，走 score_backfill_gap_d0，禁止用累计成交量冒充竞价量）。
    """
    global _SAME_DAY_MOOTDX_READY
    today = beijing_now().strftime("%Y-%m-%d")
    try_mootdx = True
    if d1 == today:
        # 盘中（约 09:30–15:30）通达信当日分笔通常未入库，且 transactions 会空转/卡住；
        # 直接走今开 OPEN 回补。收盘后再允许探测真分笔。
        hhmm = beijing_now().hour * 100 + beijing_now().minute
        if 930 <= hhmm <= 1530:
            _SAME_DAY_MOOTDX_READY = False
            try_mootdx = False
        elif _SAME_DAY_MOOTDX_READY is False:
            try_mootdx = False
        elif _SAME_DAY_MOOTDX_READY is None:
            # 只探一次，避免 25 票空扫烧通达信配额
            probe = fetch_mootdx_auction_0925(code, d1)
            _SAME_DAY_MOOTDX_READY = bool(probe)
            if probe:
                return probe
            try_mootdx = False
            log(f"当日通达信分笔未入库（probe {code}），改用今开 BACKFILL_OPEN")
    if try_mootdx:
        hit = fetch_mootdx_auction_0925(code, d1)
        if hit:
            if d1 == today:
                _SAME_DAY_MOOTDX_READY = True
            return hit
    if d1 != today:
        return None
    snap = fetch_tencent_snapshot(code)
    if not snap or snap.get("auction_price") in (None, 0):
        return None
    # 去掉累计量额，避免盘后/盘中总量污染评分
    return {
        "code": snap["code"],
        "name": snap.get("name"),
        "pre_close": snap.get("pre_close"),
        "auction_price": snap["auction_price"],
        "last": snap.get("last"),
        "auction_gap_pct": snap["auction_gap_pct"],
        "auction_volume_shares": None,
        "auction_amount": None,
        "source": "tencent_open_same_day",
        "asof": snap.get("asof") or beijing_now().isoformat(timespec="seconds"),
        "quality": "BACKFILL_OPEN",
    }


# ── D0 预筛 ──────────────────────────────────────────────────────────

def build_d0_universe(as_of: Optional[str] = None) -> dict[str, Any]:
    """
    as_of=D1(买入日) YYYY-MM-DD；默认今天。
    D0=prev_trading_day(D1)。
    """
    _ensure_dirs()
    d1 = datetime.strptime(as_of, "%Y-%m-%d") if as_of else beijing_now()
    d1 = d1.replace(hour=0, minute=0, second=0, microsecond=0)
    d0 = prev_trading_day(d1)
    d_m1 = prev_trading_day(d0)
    d0s, d1s = d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")
    d0_8, dm1_8 = d0.strftime("%Y%m%d"), d_m1.strftime("%Y%m%d")

    log(f"D0预筛 D1={d1s} D0={d0s} D-1={d_m1.strftime('%Y-%m-%d')}")

    zt0 = fetch_zt_pool(d0_8)
    zt_m1 = fetch_zt_pool(dm1_8)
    m1_codes = set(zt_m1["代码"].astype(str)) if not zt_m1.empty and "代码" in zt_m1.columns else set()
    m1_pool_status = "ok" if not zt_m1.empty else "empty_or_fail"

    candidates = []
    rejects = []

    if zt0.empty or "代码" not in zt0.columns:
        out = {
            "d0": d0s, "d1": d1s, "status": "NO_TRADE",
            "reason": "D0涨停池为空", "candidates": [], "rejects": [],
        }
        _save_json(os.path.join(CACHE_DIR, f"{d1s}_d0_preselect.json"), out)
        return out

    # 倒置 Stage B：名单已有但换手稀疏时，先补换手再套换手带（禁止因换手=0直接丢池）
    # V2_POOL_FIRST=1 / V2_SKIP_TURNOVER_ENRICH=1：本阶段只保涨停池，换手后补
    # 东财/腾讯触限时必须 baostock 兜底，否则全员 turnover_missing → 假空仓（2026-11-02）
    skip_enrich = os.environ.get("V2_POOL_FIRST", "").strip() == "1" or (
        os.environ.get("V2_SKIP_TURNOVER_ENRICH", "").strip() == "1"
    )

    def _stage_b_enrich(frame: pd.DataFrame) -> pd.DataFrame:
        try:
            from hist_market_data import enrich_turnover, enrich_turnover_baostock, pool_meta

            meta0 = pool_meta(frame)
            if skip_enrich:
                log(f"D0池 {meta0}（POOL_FIRST：跳过 Stage B 换手补齐）")
                return frame
            if not meta0.get("sparse"):
                return frame
            log(f"D0池稀疏 {meta0}，Stage B 补换手…")
            try:
                frame = enrich_turnover(frame, d0_8, max_codes=60, sleep_sec=0.15)
            except Exception as e:
                log(f"Stage B enrich_turnover 失败: {e} → baostock 兜底")
                frame = enrich_turnover_baostock(frame, d0_8, max_codes=60)
            if pool_meta(frame).get("sparse"):
                log("Stage B 仍稀疏 → baostock 再补")
                frame = enrich_turnover_baostock(frame, d0_8, max_codes=60)
            log(f"D0池补齐后 {pool_meta(frame)}")
            return frame
        except Exception as e:
            log(f"Stage B 换手补齐跳过: {e}")
            try:
                from hist_market_data import enrich_turnover_baostock, pool_meta

                if pool_meta(frame).get("sparse"):
                    frame = enrich_turnover_baostock(frame, d0_8, max_codes=60)
                    log(f"Stage B 异常后 baostock 补齐 {pool_meta(frame)}")
            except Exception as e2:
                log(f"Stage B baostock 兜底也失败: {e2}")
            return frame

    def _filter_universe(frame: pd.DataFrame) -> tuple[list, list]:
        cands: list = []
        rejs: list = []
        for _, row in frame.iterrows():
            code = str(row["代码"]).zfill(6)
            name = str(row.get("名称", ""))
            base = {
                "code": code, "name": name,
                "d0": d0s, "d1": d1s,
                "d0_pct": float(row.get("涨跌幅") or 0),
                "d0_amount": float(row.get("成交额") or 0),
                "d0_turnover_pct": float(row.get("换手率") or 0),
                "d0_seal_funds": row.get("封板资金"),
                "first_seal_time": str(row.get("首次封板时间") or ""),
                "open_board_count": int(float(row.get("炸板次数") or 0)),
                "industry": str(row.get("所属行业") or ""),
                "board": stock_meta.board_of_code(code) or "",
            }
            if not allowed_code(code) or "ST" in name.upper():
                rejs.append({**base, "reject": "REJECT_BOARD_OR_ST"})
                continue
            if code in m1_codes:
                rejs.append({**base, "reject": "REJECT_NOT_FIRST_BOARD", "detail": "in_d-1_zt_pool"})
                continue
            hist = fetch_daily_hist(code, (d_m1 - timedelta(days=10)).strftime("%Y%m%d"), d0_8)
            marks = mark_limit_ups(hist, code)
            by_d = {d: lu for d, lu in marks}
            dm1_s = d_m1.strftime("%Y-%m-%d")
            if dm1_s in by_d and by_d[dm1_s]:
                rejs.append({**base, "reject": "REJECT_NOT_FIRST_BOARD", "detail": "d-1_daily_limitup"})
                continue
            if m1_pool_status != "ok" and dm1_s not in by_d:
                rejs.append({
                    **base, "reject": "REVIEW_REQUIRED",
                    "detail": f"d-1_pool={m1_pool_status};daily_missing",
                })
                continue
            streak_ok, streak_meta = check_no_3plus_streak(code, d0)
            if not streak_ok:
                rejs.append({**base, "reject": "REJECT_RECENT_3PLUS_STREAK", **streak_meta})
                continue
            run_ok, run_meta = check_runup_cap(code, d0)
            if not run_ok:
                rejs.append({**base, "reject": "REJECT_USER_RUNUP_CAP", **run_meta})
                continue
            to = base["d0_turnover_pct"]
            if to is None or float(to) <= 0:
                rejs.append({
                    **base, "reject": "REVIEW_REQUIRED",
                    "detail": "turnover_missing_after_enrich", "d0_turnover_pct": to,
                })
                continue
            if to < 5.0 or to > 20.0:
                rejs.append({**base, "reject": "REJECT_D0_TURNOVER_BAND", "d0_turnover_pct": to})
                continue
            fst = "".join(ch for ch in base["first_seal_time"] if ch.isdigit())
            if len(fst) >= 4:
                hhmm = int(fst[:4]) if len(fst) == 4 else int(fst[:4])
                if len(fst) >= 5:
                    hhmm = int(fst[:-2][-4:]) if len(fst) >= 6 else int(fst[:4])
                try:
                    tval = int(fst)
                    if tval >= 143000 or (len(fst) <= 4 and hhmm >= 1430):
                        rejs.append({**base, "reject": "REJECT_LATE_SEAL"})
                        continue
                except ValueError:
                    pass
            base["d_minus_1_check_status"] = "ok"
            base["max_consecutive_limitups_30td"] = streak_meta.get("max_consecutive_limitups_30td")
            base.update({k: v for k, v in run_meta.items() if k.startswith("runup") or k.startswith("projected")})
            cands.append(base)
            time_mod.sleep(0.15)
        return cands, rejs

    zt0 = _stage_b_enrich(zt0)
    candidates, rejects = _filter_universe(zt0)

    # 假空仓自愈：大量缺换手且候选为空 → 强制 baostock 后再筛一遍（禁止写成真 NO_TRADE）
    missing_n = sum(
        1 for r in rejects if (r.get("detail") or "") == "turnover_missing_after_enrich"
    )
    if (not candidates) and missing_n >= 3 and (not skip_enrich):
        log(f"假空仓风险：{missing_n} 只 turnover_missing → 强制 baostock 重筛")
        try:
            from hist_market_data import enrich_turnover_baostock

            zt0 = enrich_turnover_baostock(zt0, d0_8, max_codes=60)
            candidates, rejects = _filter_universe(zt0)
            missing_n = sum(
                1 for r in rejects if (r.get("detail") or "") == "turnover_missing_after_enrich"
            )
        except Exception as e:
            log(f"假空仓自愈失败: {e}")

    if candidates:
        status, reason = "OK", None
    elif missing_n >= 3:
        # 数据缺口，不是策略空仓；看门狗必须重试，禁止当 NO_TRADE 放过
        status, reason = "DATA_GAP", f"换手补齐失败（{missing_n}只 turnover_missing，疑似假空仓）"
    else:
        status, reason = "NO_TRADE", "预筛后无候选"

    out = {
        "d0": d0s,
        "d1": d1s,
        "status": status,
        "reason": reason,
        "d_minus_1_limitup_pool_status": m1_pool_status,
        "candidates": candidates,
        "rejects": rejects[:500],
        "reject_count": len(rejects),
        "candidate_count": len(candidates),
        "turnover_missing_count": missing_n,
        "generated_at": beijing_now().isoformat(timespec="seconds"),
    }
    path = os.path.join(CACHE_DIR, f"{d1s}_d0_preselect.json")
    _save_json(path, out)
    log(f"D0预筛完成 status={status} 候选={len(candidates)} 剔除={len(rejects)} → {path}")
    return out


def check_no_3plus_streak(code: str, d0: datetime) -> tuple[bool, dict]:
    days = trading_days_back(d0, STREAK_LOOKBACK_TD + 2)
    start = days[0].strftime("%Y%m%d")
    end = d0.strftime("%Y%m%d")
    hist = fetch_daily_hist(code, start, end)
    marks = mark_limit_ups(hist, code)
    if len(marks) < 5:
        return False, {"reason": "hist_insufficient", "max_consecutive_limitups_30td": None}

    # 只统计落在 lookback 窗口内的
    window_start = days[-(STREAK_LOOKBACK_TD)].strftime("%Y-%m-%d")
    seq = [(d, lu) for d, lu in marks if d >= window_start]
    max_streak = cur = 0
    last_end = None
    for d, lu in seq:
        if lu:
            cur += 1
            if cur >= STREAK_REJECT_AT:
                last_end = d
            max_streak = max(max_streak, cur)
        else:
            cur = 0
    meta = {"max_consecutive_limitups_30td": max_streak, "last_3plus_streak_end_date": last_end}
    if max_streak >= STREAK_REJECT_AT:
        return False, meta
    return True, meta


def check_runup_cap(code: str, d0: datetime) -> tuple[bool, dict]:
    days = trading_days_back(d0, 30)
    start = days[0].strftime("%Y%m%d")
    end = d0.strftime("%Y%m%d")
    hist = fetch_daily_hist(code, start, end)
    if hist is None or hist.empty or "收盘" not in hist.columns:
        return False, {"reason": "runup_hist_missing"}
    h = hist.copy()
    h["日期"] = pd.to_datetime(h["日期"]).dt.strftime("%Y-%m-%d")
    h = h.sort_values("日期")
    if len(h) < 20:
        return False, {"reason": "runup_hist_short", "n": len(h)}
    p_base = float(h.iloc[0]["收盘"])
    p_d0 = float(h.iloc[-1]["收盘"])
    if p_base <= 0:
        return False, {"reason": "bad_base"}
    lim = board_limit_pct(code)
    p_d1_lim = round(p_d0 * (1 + lim / 100.0) + 1e-9, 2)
    p_d2_lim = round(p_d1_lim * (1 + lim / 100.0) + 1e-9, 2)
    runup_d1 = p_d1_lim / p_base - 1.0
    runup_d2 = p_d2_lim / p_base - 1.0
    meta = {
        "runup_30td": round(p_d0 / p_base - 1.0, 4),
        "projected_d1_runup": round(runup_d1, 4),
        "projected_d2_runup": round(runup_d2, 4),
    }
    if max(runup_d1, runup_d2) >= USER_PRICE_RUNUP_CAP:
        return False, meta
    return True, meta


# ── 评分 ──────────────────────────────────────────────────────────────

def _seal_before_1030(fst: Optional[str]) -> bool:
    digits = "".join(ch for ch in str(fst or "") if ch.isdigit())
    if not digits:
        return False
    try:
        v = int(digits)
        if v < 10000:
            v *= 100  # HHMM -> HHMMSS approx
        return v <= 103000
    except ValueError:
        return False


def score_backfill_gap_d0(c: dict, auc: dict, path: str) -> tuple[float, dict]:
    """
    历史回补专用：仅今开 gap + D0 因子（无真实竞价量额，不编造）。
    满分 100；门槛低于实盘（见 SCORE_*_BACKFILL）。
    """
    gap = float(auc["auction_gap_pct"])
    if path == "A":
        gap_sc = piecewise_score(gap, [(2.0, 10), (3.0, 35), (5.0, 40), (6.0, 20)], 40)
    else:
        gap_sc = piecewise_score(gap, [(-3.0, 8), (-2.0, 18), (-1.0, 28), (0.0, 35), (1.0, 40), (2.0, 25)], 40)
    d0_sc = piecewise_score(
        float(c.get("d0_turnover_pct") or 0),
        [(5, 0), (6, 12), (7, 25), (15, 30), (18, 15), (20, 0)], 30,
    )
    fst = "".join(ch for ch in str(c.get("first_seal_time") or "") if ch.isdigit())
    seal_sc = 15.0 if _seal_before_1030(fst) else 6.0
    ob = int(c.get("open_board_count") or 0)
    ob_sc = 15.0 if ob == 0 else (10.0 if ob == 1 else 4.0)
    total = round(gap_sc + d0_sc + seal_sc + ob_sc, 2)
    return total, {
        "gap": gap_sc, "d0_turnover": d0_sc, "seal": seal_sc, "open_board": ob_sc,
        "auction_turnover_pct": None, "auction_amount_to_d0_ratio": None,
        "backfill": True,
    }


SCORE_A_BACKFILL_MIN = 55.0
SCORE_B_BACKFILL_MIN = 55.0


def score_path_a(c: dict, auc: dict) -> tuple[float, dict]:
    gap = float(auc["auction_gap_pct"])
    a_to = _auction_turnover_pct(c, auc)
    a_ratio = _auction_amount_ratio(c, auc)
    comps = {
        "auction_turnover": piecewise_score(
            a_to, [(0.3, 0), (0.6, 8), (0.8, 20), (1.5, 20), (2.0, 10), (3.0, 0)], 20),
        "auction_amt_ratio": piecewise_score(
            a_ratio * 100, [(5, 4), (10, 10), (20, 10), (30, 4), (50, 0)], 10),
        "auction_gap": piecewise_score(
            gap, [(2.0, 10), (3.0, 20), (5.0, 20), (6.0, 8)], 20),
        "d0_turnover": piecewise_score(
            float(c.get("d0_turnover_pct") or 0),
            [(5, 0), (6, 10), (7, 15), (15, 15), (18, 8), (20, 0)], 15),
        "d0_vol_proxy": 8.0,  # 无前5日均量时给中性分；完整量比留待有数据源
        "first_seal": 5.0 if _seal_before_1030(c.get("first_seal_time")) else 2.0,
        "open_board": 5.0 if int(c.get("open_board_count") or 0) <= 1 else 2.0,
        "theme": 0.0,  # 缺题材不造分
        "mkt": 0.0,
    }
    # 量能相对分：有量才给竞价量能 5 分档
    if auc.get("quality") == "VERIFIED":
        comps["auction_vol_vs_hist"] = 3.0
    else:
        comps["auction_vol_vs_hist"] = 0.0
    total = sum(comps.values())
    comps["auction_turnover_pct"] = a_to
    comps["auction_amount_to_d0_ratio"] = a_ratio
    return round(total, 2), comps


def score_path_b(c: dict, auc: dict) -> tuple[float, dict]:
    gap = float(auc["auction_gap_pct"])
    a_to = _auction_turnover_pct(c, auc)
    a_ratio = _auction_amount_ratio(c, auc)
    if gap < 0 and a_to < 0.8:
        return 0.0, {"reject": "B_WEAK_AUCTION_ON_LOW_OPEN", "auction_turnover_pct": a_to}
    comps = {
        "d0_turnover": piecewise_score(
            float(c.get("d0_turnover_pct") or 0),
            [(5, 0), (7, 15), (15, 15), (20, 0)], 15),
        "d0_vol_proxy": 8.0,
        "first_seal": 10.0 if _seal_before_1030(c.get("first_seal_time")) else 3.0,
        "open_board": 10.0 if int(c.get("open_board_count") or 0) <= 1 else 3.0,
        "auction_turnover": piecewise_score(
            a_to, [(0.6, 8), (0.8, 20), (1.5, 20), (2.5, 8)], 20),
        "auction_amt_ratio": piecewise_score(
            a_ratio * 100, [(5, 4), (10, 10), (20, 10), (40, 0)], 10),
        "auction_gap": piecewise_score(
            gap, [(-3.0, 2), (-2.0, 4), (-1.0, 7), (0.0, 10), (1.0, 10), (1.99, 6)], 10),
        "theme": 0.0,
        "mkt": 0.0,
    }
    total = sum(comps.values())
    comps["auction_turnover_pct"] = a_to
    comps["auction_amount_to_d0_ratio"] = a_ratio
    return round(total, 2), comps


def _auction_turnover_pct(c: dict, auc: dict) -> float:
    """竞价换手% = 竞价量 / 估算流通股本 * 100。流通股本≈流通市值/昨收。"""
    vol = float(auc.get("auction_volume_shares") or 0)
    pre = float(auc.get("pre_close") or 0)
    # 候选若带流通市值更好；否则用成交额/换手反推粗糙股本
    float_mv = float(c.get("d0_float_mv") or 0)
    if float_mv <= 0 and c.get("d0_turnover_pct") and c.get("d0_amount") and pre > 0:
        # amount ≈ turnover% * float_mv / 100 → float_mv ≈ amount * 100 / turnover
        to = float(c["d0_turnover_pct"])
        if to > 0:
            float_mv = float(c["d0_amount"]) * 100.0 / to
    if float_mv <= 0 or pre <= 0 or vol <= 0:
        return 0.0
    shares = float_mv / pre
    if shares <= 0:
        return 0.0
    return round(vol / shares * 100.0, 4)


def _auction_amount_ratio(c: dict, auc: dict) -> float:
    d0_amt = float(c.get("d0_amount") or 0)
    a_amt = float(auc.get("auction_amount") or 0)
    if d0_amt <= 0 or a_amt <= 0:
        return 0.0
    return a_amt / d0_amt


# ── 竞价终选 ──────────────────────────────────────────────────────────

def run_auction_final(
    as_of: Optional[str] = None,
    *,
    force: bool = False,
    do_notify: bool = False,
    backfill: bool = False,
) -> dict[str, Any]:
    _ensure_dirs()
    d1s = as_of or beijing_now().strftime("%Y-%m-%d")
    today = beijing_now().strftime("%Y-%m-%d")

    # 生产铁律（早盘通知优先于一切历史回补）：
    # - cron 常 09:24 进场：未到 09:25 **必须等待**，禁止秒退 / 禁止当成 SKIPPED
    # - 过 09:29：优先读盘补发；无结果则允许用「今开=竞价价」补算并标 LATE（禁止用现价冒充）
    # - 历史日必须 --backfill
    # - 早盘 live 走腾讯快照，不占 tdx 日/小时配额
    if not backfill:
        if d1s != today and not force:
            log(f"历史日 {d1s} 请用 --backfill；拒绝用现价冒充历史竞价")
            return {
                "d1": d1s, "status": "SKIPPED", "reason": "NEED_BACKFILL_FLAG",
                "recommendations": [],
            }
        # 先等到 09:25，再做预筛/扫票（避免 09:24 秒退「非竞价窗」）
        if d1s == today and beijing_now().time() < AUCTION_OPEN:
            log(f"未到竞价窗 {AUCTION_OPEN}，等待后继续（cron 09:24 提前进场属正常）")
            _wait_until(AUCTION_OPEN, "集合竞价 09:25")

    # 铁律：历史回补不得覆盖当日早盘 live 实盘结果（除非 FORCE / force=True）
    if backfill and not force:
        existing_live = _load_json(os.path.join(RESULTS_DIR, f"{d1s}_recommendations.json"))
        if (
            existing_live.get("mode") == "live"
            and existing_live.get("status") in ("OK", "NO_TRADE")
            and existing_live.get("recommendations") is not None
        ):
            log(f"跳过回补 {d1s}：已有 live 实盘结果，禁止覆盖（FORCE=1 才可重算）")
            return existing_live

    def _preselect_looks_false_empty(pre_obj: dict) -> bool:
        if not pre_obj:
            return False
        if pre_obj.get("status") == "DATA_GAP":
            return True
        if pre_obj.get("candidates"):
            return False
        miss = int(pre_obj.get("turnover_missing_count") or 0)
        if miss >= 3:
            return True
        rej = pre_obj.get("rejects") or []
        miss2 = sum(1 for r in rej if (r.get("detail") or "") == "turnover_missing_after_enrich")
        return miss2 >= 3

    pre_path = os.path.join(CACHE_DIR, f"{d1s}_d0_preselect.json")
    if not os.path.isfile(pre_path):
        log("无预筛缓存，先跑 d0-preselect…")
        build_d0_universe(d1s)
    pre = _load_json(pre_path)
    # 假空仓：预筛 DATA_GAP / 大量缺换手 → 强制重跑预筛一次，再决定是否真空仓
    if (not backfill) and _preselect_looks_false_empty(pre):
        log("预筛疑似假空仓（DATA_GAP/turnover_missing）→ 强制重跑 d0-preselect")
        build_d0_universe(d1s)
        pre = _load_json(pre_path)
    if not pre or not pre.get("candidates"):
        # DATA_GAP 不得伪装成策略 NO_TRADE（看门狗需继续重试）
        st = "DATA_GAP" if _preselect_looks_false_empty(pre) else "NO_TRADE"
        reason = (pre or {}).get("reason") or (
            "换手补齐失败（疑似假空仓）" if st == "DATA_GAP" else "无预筛候选"
        )
        result = {
            "d1": d1s, "status": st, "reason": reason,
            "recommendations": [], "tabs": {}, "mode": "backfill" if backfill else "live",
        }
        _emit_result(result, do_notify=do_notify and not backfill)
        return result

    late_rescue = False
    if not backfill and d1s == today and beijing_now().time() >= HARD_DEADLINE:
        existing = _load_json(os.path.join(RESULTS_DIR, f"{d1s}_recommendations.json"))
        # 已落盘假空仓（DATA_GAP / 无票但预筛缺换手）→ 即使无 --force 也必须重算
        existing_false_empty = (
            existing.get("status") == "DATA_GAP"
            or (
                existing.get("status") == "NO_TRADE"
                and not (existing.get("recommendations") or [])
                and _preselect_looks_false_empty(pre)
            )
        )
        # --force：允许覆盖窗内错误空仓/脏结果（如空壳涨停池导致的假 NO_TRADE）
        if (
            (not force)
            and (not existing_false_empty)
            and existing.get("recommendations") is not None
            and existing.get("status") in ("OK", "NO_TRADE")
        ):
            log("已过 09:29：使用窗内已落盘结果补发通知（不重算；覆盖请加 --force）")
            _emit_result(existing, do_notify=do_notify, late_resend=True)
            return existing
        if existing_false_empty and not force:
            log("已过 09:29：检测到假空仓/DATA_GAP → 自动 LATE 重算")
            force = True
        # 无窗内落盘，或 force 重算：用腾讯「今开」（竞价价，非现价）补算，通知标 LATE
        if force and existing.get("recommendations") is not None:
            log("已过 09:29 + --force：覆盖已落盘结果并 LATE 重算")
        else:
            log("已过 09:29 且无窗内结果：用今开（竞价价）补算并 LATE 通知")
        late_rescue = True
        force = True

    # 铁律：历史回补不得覆盖已落盘的 live 早盘结果（除非 force）
    if backfill and not force:
        existing = _load_json(os.path.join(RESULTS_DIR, f"{d1s}_recommendations.json"))
        if (
            existing.get("mode") == "live"
            and existing.get("status") in ("OK", "NO_TRADE")
            and existing.get("recommendations") is not None
        ):
            log(f"跳过回补 {d1s}：已有 live 结果（禁止 hist 覆盖早盘）")
            return existing

    if not backfill:
        wait_auction_ready(force=force)

    a_pool, b_pool = [], []          # 过门槛
    a_scored, b_scored = [], []      # 对比期：路径内全部已评分（含未过门槛）
    scan_log = []
    quota_stopped = False
    for c in pre["candidates"]:
        if (not backfill) and (not force) and beijing_now().time() >= PUSH_DEADLINE:
            log("临近推送截止，停止扫票")
            break
        code = c["code"]
        try:
            if backfill:
                auc = fetch_backfill_auction(code, d1s)
            else:
                auc = fetch_tencent_snapshot(code)
                time_mod.sleep(TX_SLEEP)
        except TdxQuotaStop as e:
            log(f"通达信配额软停（≤70%铁律）: {e} — 本日停扫防封号")
            scan_log.append({"code": code, "reject": "TDX_QUOTA_SOFT_STOP", "detail": str(e)})
            quota_stopped = True
            break
        if not auc:
            scan_log.append({"code": code, "reject": "DATA_UNAVAILABLE", "field": "auction"})
            continue
        gap = float(auc["auction_gap_pct"])
        if gap > PATH_A_MAX:
            scan_log.append({"code": code, "reject": "REJECT_GAP_GT_6", "gap": gap})
            continue
        if gap < PATH_B_MIN:
            scan_log.append({"code": code, "reject": "REJECT_GAP_LT_BMIN", "gap": gap})
            continue

        q = auc.get("quality")
        use_open_backfill = bool(backfill and q == "BACKFILL_OPEN")
        # 实盘 / 真分笔回补：必须 VERIFIED（A/B）；OPEN 回补走 gap+D0 低门槛
        if use_open_backfill:
            pass
        elif q not in ("VERIFIED", "PARTIAL"):
            scan_log.append({"code": code, "reject": "DATA_QUALITY", "quality": q})
            continue

        if PATH_A_MIN <= gap <= PATH_A_MAX:
            if use_open_backfill:
                sc, comps = score_backfill_gap_d0(c, auc, "A")
                thr = SCORE_A_BACKFILL_MIN
            else:
                if q != "VERIFIED":
                    scan_log.append({"code": code, "reject": "AUCTION_PARTIAL_FOR_A", "gap": gap})
                    continue
                sc, comps = score_path_a(c, auc)
                thr = SCORE_A_MIN
            rec = _pack_rec(c, auc, "A", sc, comps)
            rec["score_threshold"] = thr
            rec["above_threshold"] = bool(sc >= thr)
            a_scored.append(rec)
            if sc >= thr:
                a_pool.append(rec)
            else:
                scan_log.append({"code": code, "path": "A", "score": sc, "reject": "SCORE_A_LOW"})
        elif PATH_B_MIN <= gap < PATH_B_MAX_EXCL:
            if use_open_backfill:
                sc, comps = score_backfill_gap_d0(c, auc, "B")
                thr = SCORE_B_BACKFILL_MIN
                if comps.get("reject"):
                    scan_log.append({"code": code, "path": "B", **comps})
                    continue
            else:
                if q != "VERIFIED":
                    scan_log.append({"code": code, "reject": "AUCTION_PARTIAL_FOR_B", "gap": gap})
                    continue
                sc, comps = score_path_b(c, auc)
                thr = SCORE_B_MIN
                if comps.get("reject"):
                    scan_log.append({"code": code, "path": "B", **comps})
                    continue
            rec = _pack_rec(c, auc, "B", sc, comps)
            rec["score_threshold"] = thr
            rec["above_threshold"] = bool(sc >= thr)
            b_scored.append(rec)
            if sc >= thr:
                b_pool.append(rec)
            else:
                scan_log.append({"code": code, "path": "B", "score": sc, "reject": "SCORE_B_LOW"})
        else:
            scan_log.append({"code": code, "reject": "GAP_OUT_OF_PATH", "gap": gap})

    a_pool.sort(key=lambda x: x["score_total"], reverse=True)
    b_pool.sort(key=lambda x: x["score_total"], reverse=True)
    a_scored.sort(key=lambda x: x["score_total"], reverse=True)
    b_scored.sort(key=lambda x: x["score_total"], reverse=True)

    picks: list[dict] = []
    if ALLOCATION_MODE == ALLOCATION_COMPARE:
        # 对比期：A 第1/第2 + B 第1/第2；不过门槛也出数，便于对照
        for path_label, scored in (("A", a_scored), ("B", b_scored)):
            for i, rec in enumerate(scored[:MAX_PER_PATH], start=1):
                item = dict(rec)
                item["path_rank"] = i
                item["slot"] = f"{path_label}{i}"
                picks.append(item)
        for i, p in enumerate(picks, 1):
            p["rank"] = i  # 通知/UI 总序 1..4
    else:
        for rec in a_pool:
            if len(picks) >= MAX_RECS:
                break
            picks.append(rec)
        for rec in b_pool:
            if len(picks) >= MAX_RECS:
                break
            if any(p["code"] == rec["code"] for p in picks):
                continue
            picks.append(rec)
        for i, p in enumerate(picks, 1):
            p["rank"] = i

    reason = None
    if picks:
        status = "OK"
    elif quota_stopped:
        status = "NO_TRADE"
        reason = "TDX_QUOTA_SOFT_STOP"
    else:
        status = "NO_TRADE"
        reason = "无合格A/B候选或竞价数据不足"
    result = {
        "d0": pre.get("d0"),
        "d1": d1s,
        "status": status,
        "reason": reason,
        "allocation": ALLOCATION_MODE,
        "mode": (
            "backfill_mootdx_0925"
            if backfill and any(
                (r.get("data_quality") == "VERIFIED") for r in picks
            )
            else ("backfill_open_same_day" if backfill else "live")
        ),
        "recommendations": picks,
        "scan_log_head": scan_log[:80],
        "scan_log_n": len(scan_log),
        "a_pool_n": len(a_pool),
        "b_pool_n": len(b_pool),
        "a_scored_n": len(a_scored),
        "b_scored_n": len(b_scored),
        "tdx_quota_stopped": quota_stopped,
        "generated_at": beijing_now().isoformat(timespec="seconds"),
        "push_deadline": str(PUSH_DEADLINE),
        "hard_deadline": str(HARD_DEADLINE),
        "late": bool(late_rescue),
    }
    _emit_result(
        result,
        do_notify=do_notify and not backfill,
        late_resend=bool(late_rescue),
    )
    if quota_stopped:
        raise TdxQuotaStop(reason or "TDX_QUOTA_SOFT_STOP")
    return result


def _pack_rec(c: dict, auc: dict, path: str, score: float, comps: dict) -> dict:
    return {
        "trade_date": c["d1"],
        "d0_date": c["d0"],
        "code": c["code"],
        "name": c.get("name") or auc.get("name"),
        "board": c.get("board"),
        "path": path,
        "candidate_status": "SELECTED",
        "data_quality": auc.get("quality"),
        "data_asof": auc.get("asof"),
        "source_ids": [auc.get("source")],
        "auction_gap_pct": auc.get("auction_gap_pct"),
        "auction_turnover_pct": comps.get("auction_turnover_pct"),
        "auction_amount": auc.get("auction_amount"),
        "auction_amount_to_d0_ratio": comps.get("auction_amount_to_d0_ratio"),
        "auction_volume_shares": auc.get("auction_volume_shares"),
        "d0_turnover_pct": c.get("d0_turnover_pct"),
        "first_seal_time": c.get("first_seal_time"),
        "open_board_count": c.get("open_board_count"),
        "max_streak_30td": c.get("max_consecutive_limitups_30td"),
        "runup_30td": c.get("runup_30td"),
        "projected_d1_runup": c.get("projected_d1_runup"),
        "projected_d2_runup": c.get("projected_d2_runup"),
        "score_components": {k: v for k, v in comps.items() if not str(k).startswith("auction_")},
        "score_total": score,
        "selection_reason": f"path={path} score={score}",
        "execution_status": "SIGNAL_ONLY",
    }


def _emit_result(result: dict, *, do_notify: bool, late_resend: bool = False) -> None:
    d1 = result["d1"]
    jp = os.path.join(RESULTS_DIR, f"{d1}_recommendations.json")
    mp = os.path.join(RESULTS_DIR, f"{d1}_recommendations.md")
    _save_json(jp, result)
    _save_text(mp, _format_md(result))
    log(f"结果已写 {jp}")
    try:
        _sync_trades_ui(result)
    except Exception as e:
        log(f"UI trades 同步失败: {e}")
    if do_notify:
        _notify_pushplus(result, late_resend=late_resend)


def _sync_trades_ui(result: dict) -> None:
    """写入 program=stock_v2 的 trades（独立于 V1）。

    UI 第二层按 path=A/B + path_rank 过滤；缺 path/path_rank 会在「一进二」页被滤光。
    """
    recs = list(result.get("recommendations") or [])
    d1 = result["d1"]
    # 补 path_rank：同 path 内按已有 path_rank → score → 全局 rank
    by_path: dict[str, list[dict]] = {}
    for r in recs:
        path = str(r.get("path") or "").strip().upper() or "?"
        by_path.setdefault(path, []).append(r)
    for path, group in by_path.items():
        ranked = sorted(
            group,
            key=lambda x: (
                int(x.get("path_rank") or 0) or 99,
                -float(x.get("score_total") or 0),
                int(x.get("rank") or 99),
            ),
        )
        for i, r in enumerate(ranked, 1):
            if not int(r.get("path_rank") or 0):
                r["path_rank"] = i
            if not r.get("slot") and path in ("A", "B"):
                r["slot"] = f"{path}{int(r.get('path_rank') or i)}"

    records = []
    for r in recs:
        path = str(r.get("path") or "").strip().upper() or None
        path_rank = int(r.get("path_rank") or 0) or None
        slot = r.get("slot")
        records.append({
            "date": d1,
            "stock_code": r["code"],
            "stock_name": r.get("name"),
            # 全局序（对比期 A1/A2/B1/B2 → 1..4）；UI 第二层用 path + path_rank
            "rank": int(r.get("rank") or 0),
            "path": path,
            "path_rank": path_rank,
            "slot": slot,
            "notes": (
                f"V2 {slot or ((path or '') + str(path_rank or ''))} "
                f"score={r.get('score_total')} gap={r.get('auction_gap_pct')} "
                f"thr={'Y' if r.get('above_threshold') else 'N'}"
            ),
        })
    import stock_UI
    if records:
        # 对比期最多 4 条（A1/A2/B1/B2）；按 rank 覆盖当日
        res = stock_UI.upsert_from_stock_py(records, auto_enrich=False, replace_by_rank=True, program=PROGRAM)
        log(f"UI sync stock_v2: {res}")
    else:
        # 清空当日 rank1/2 残留：写空列表覆盖
        with stock_UI._data_lock:
            stock_UI._save_day(d1, [], PROGRAM)
        log(f"UI sync stock_v2: 空仓，已写空日 {d1}")


_QUALITY_ZH = {
    "VERIFIED": "真竞价已核验",
    "PARTIAL": "竞价部分字段",
    "BACKFILL_OPEN": "今开回补（无分笔量）",
}


def _fmt_pct(v: Any, *, digits: int = 2, as_ratio: bool = False) -> str:
    """as_ratio=True 时把 0.5899 显示为 58.99%。"""
    try:
        if v is None or v == "":
            return "暂无"
        x = float(v)
        if as_ratio:
            x *= 100.0
        return f"{x:.{digits}f}%"
    except (TypeError, ValueError):
        return "暂无"


def _turnover_zh(r: dict) -> tuple[str, str]:
    """
    换手展示：优先竞价换手；今开回补无分笔量时必须回退涨停日换手（不得显示 None）。
    返回 (标签, 百分比文案)。
    """
    auc = r.get("auction_turnover_pct")
    try:
        if auc is not None and str(auc).strip() != "" and float(auc) > 0:
            return "竞价换手", _fmt_pct(auc)
    except (TypeError, ValueError):
        pass
    d0 = r.get("d0_turnover_pct")
    try:
        if d0 is not None and str(d0).strip() != "" and float(d0) > 0:
            return "涨停日换手", _fmt_pct(d0)
    except (TypeError, ValueError):
        pass
    return "换手率", "暂无"


def _quality_zh(q: Any) -> str:
    s = str(q or "").strip()
    return _QUALITY_ZH.get(s, s or "未知")


def _risk_zh(r: dict) -> str:
    streak = r.get("max_streak_30td")
    try:
        streak_s = f"{int(streak)}天" if streak is not None else "暂无"
    except (TypeError, ValueError):
        streak_s = "暂无"
    run30 = _fmt_pct(r.get("runup_30td"), as_ratio=True)
    run_d2 = _fmt_pct(r.get("projected_d2_runup"), as_ratio=True)
    return (
        f"近30日最大连板{streak_s}；"
        f"相对约30日前涨幅{run30}；"
        f"若连板两日预估涨幅{run_d2}"
    )


def _path_rank_zh(r: dict) -> str:
    try:
        n = int(r.get("path_rank") or 0)
    except (TypeError, ValueError):
        n = 0
    return PATH_RANK_ZH.get(n, f"第{n}名" if n else "候选")


def _format_rec_block(r: dict) -> list[str]:
    """单票四行：名次 / 代码名称得分 / 涨幅换手质量 / 风险核验。"""
    to_label, to_val = _turnover_zh(r)
    thr_ok = r.get("above_threshold")
    thr_txt = "过门槛" if thr_ok else ("未过门槛" if thr_ok is False else "")
    score = r.get("score_total")
    score_s = f"{score}" if score is not None else "暂无"
    return [
        _path_rank_zh(r),
        f"{r.get('code')} {r.get('name')} 得分{score_s}({thr_txt})",
        (
            f"竞价涨幅{_fmt_pct(r.get('auction_gap_pct'))} "
            f"{to_label}{to_val} 数据质量={_quality_zh(r.get('data_quality'))}"
        ),
        f"风险核验: {_risk_zh(r)}",
        "",
    ]


def _format_path_sections(result: dict, *, empty_msg: str) -> list[str]:
    """路径A/B 分段（通知与 md 共用版式）。"""
    lines: list[str] = []
    recs = result.get("recommendations") or []
    if not recs:
        lines.append(empty_msg)
        return lines
    by_path: dict[str, list] = {"A": [], "B": []}
    for r in recs:
        by_path.setdefault(str(r.get("path") or "?"), []).append(r)
    for path in ("A", "B"):
        group = by_path.get(path) or []
        title = PATH_TITLE.get(path, "")
        header = f"—— 路径{path} ——{title}" if title else f"—— 路径{path} ——"
        if not group:
            lines.append(header)
            lines.append("暂无候选")
            lines.append("")
            continue
        # 按 path_rank 排序
        group = sorted(group, key=lambda x: int(x.get("path_rank") or 99))
        lines.append(header)
        for r in group:
            lines.extend(_format_rec_block(r))
    return lines


def _format_md(result: dict) -> str:
    lines = [
        f"# Stock V2 推荐 {result.get('d1')}",
        f"- 涨停日(D0): {result.get('d0')}",
        f"- 状态: **{result.get('status')}** {result.get('reason') or ''}",
        f"- 分配: {result.get('allocation')}",
        "",
    ]
    lines.extend(
        _format_path_sections(
            result,
            empty_msg=(
                "_数据缺口 DATA_GAP（疑似假空仓，须重跑预筛）_"
                if result.get("status") == "DATA_GAP"
                else "_今日空仓（NO_TRADE / 无合格票）_"
            ),
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def _notify_pushplus(result: dict, *, late_resend: bool = False) -> None:
    """
    早盘通知：目标 09:27–09:28，SLA 最迟 09:29。
    铁律（2026-10-28）：过点也必须发——迟到标 LATE/补发，禁止因 09:29 直接不发。
    模板：路径标题 + 第一名/第二名 四行块。
    """
    now = beijing_now()
    is_today = now.strftime("%Y-%m-%d") == result.get("d1")
    late = bool(late_resend) or (is_today and now.time() > HARD_DEADLINE)
    if is_today and now.time() < PUSH_TARGET and not late:
        _wait_until(PUSH_TARGET, "推送窗口 09:27")
        now = beijing_now()
        late = now.time() > HARD_DEADLINE
    status = result.get("status")
    late_tag = " 迟到补发" if late else ""
    title = f"StockV2 {result.get('d1')} {status}{late_tag}"
    lines = [
        f"【Stock V2】{result.get('d1')} {status}{late_tag}",
        f"涨停日={result.get('d0')}",
        f"发出时刻={beijing_now().strftime('%H:%M:%S')} 最迟09:29{' ⚠已迟到须立即补发' if late else ''}",
        "",
    ]
    lines.extend(
        _format_path_sections(
            result,
            empty_msg=f"空仓：{result.get('reason') or '无推荐'}",
        )
    )
    content = "\n".join(lines).rstrip() + "\n"
    if late:
        log("⚠ 已过 09:29 SLA，仍立即补发（禁止因过点不发）")
    elif beijing_now().time() > PUSH_DEADLINE:
        log(f"⚠ 已过软截止 {PUSH_DEADLINE}，赶在/越过 09:29 仍发送")
    try:
        sys.path.insert(0, _SHOU)
        from pushplus_notify import send_pushplus, KIND_STOCK_V2
        ok = send_pushplus(title=title, content=content, kind=KIND_STOCK_V2)
        log(f"PushPlus 发送 {'成功' if ok else '失败'}（kind={KIND_STOCK_V2} 本人+好友 late={late}）")
        if not ok:
            # 失败再试一次（发现问题立即补发）
            time_mod.sleep(1.5)
            ok2 = send_pushplus(title=title + " 重试", content=content, kind=KIND_STOCK_V2)
            log(f"PushPlus 重试 {'成功' if ok2 else '失败'}")
    except Exception as e:
        log(f"PushPlus 异常: {e}")


def probe_auction(codes: list[str]) -> None:
    log("竞价字段探针（腾讯单票）")
    for c in codes:
        snap = fetch_tencent_snapshot(c.strip())
        time_mod.sleep(TX_SLEEP)
        print(json.dumps(snap, ensure_ascii=False, indent=2))


# ── IO ────────────────────────────────────────────────────────────────

def _save_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Stock V2 双路径首板（隔离于 V1）")
    ap.add_argument("--date", default="", help="D1 YYYY-MM-DD，默认今天")
    ap.add_argument(
        "--stage",
        default="auction-final",
        choices=["d0-preselect", "auction-final", "probe-auction"],
    )
    ap.add_argument("--notify", action="store_true", help="PushPlus（仅早盘窗；本人+好友）")
    ap.add_argument(
        "--force",
        action="store_true",
        help="跳过竞价等待；过 09:29 与 watchdog 补发时使用（今开=竞价价，通知标 LATE）",
    )
    ap.add_argument(
        "--backfill",
        action="store_true",
        help="历史回补：今开≈竞价价，quality=BACKFILL_OPEN；不发推",
    )
    ap.add_argument("--codes", default="600000,000001,300750", help="probe-auction 代码")
    args = ap.parse_args()
    d1 = args.date.strip() or None

    if args.stage == "probe-auction":
        probe_auction(args.codes.split(","))
        return
    if args.stage == "d0-preselect":
        build_d0_universe(d1)
        return
    run_auction_final(
        d1,
        force=args.force,
        do_notify=args.notify,
        backfill=args.backfill,
    )


if __name__ == "__main__":
    main()
