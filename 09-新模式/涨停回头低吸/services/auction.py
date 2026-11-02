"""集合竞价：腾讯「今开 vs 昨收」——【铁律】禁止批量，仅单票请求。"""
from __future__ import annotations

import time as time_mod
from datetime import datetime, time
from typing import Optional

import requests

from rules import (
    AUCTION_HARD_DEADLINE,
    AUCTION_MIN_COVERAGE,
    AUCTION_OPEN_TIME,
    AUCTION_POLL_SEC,
    AUCTION_SCAN_START,
    AUCTION_SINGLE_SLEEP,
    AUCTION_SOFT_DEADLINE,
)

_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.qq.com",
})


def _t(hms: tuple[int, int, int]) -> time:
    return time(*hms)


def wait_until(target: time, label: str) -> None:
    while True:
        now = datetime.now()
        if now.time() >= target:
            return
        secs = (datetime.combine(now.date(), target) - now).total_seconds()
        sleep_for = min(max(secs, 0.2), 2.0)
        print(f"⏳ 等待 {label}（当前 {now.strftime('%H:%M:%S')}）…")
        time_mod.sleep(sleep_for)


def is_weekday_trading_approx(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5


def enforce_auction_window(*, force: bool = False) -> None:
    """交易日 9:24:40 起；超过 9:29 拒绝（除非 --force）。"""
    now = datetime.now()
    if force:
        print(f"⚠ --force：跳过时间窗（当前 {now.strftime('%H:%M:%S')}）")
        return
    if not is_weekday_trading_approx(now):
        print(f"非交易日近似（{now.strftime('%Y-%m-%d')}），退出")
        raise SystemExit(0)
    hard = _t(AUCTION_HARD_DEADLINE)
    start = _t(AUCTION_SCAN_START)
    if now.time() > hard:
        print(f"已过截止 {hard.strftime('%H:%M:%S')}（当前 {now.strftime('%H:%M:%S')}），退出")
        raise SystemExit(0)
    if now.time() < start:
        wait_until(start, f"扫描起点 {start.strftime('%H:%M:%S')}")
    print(
        f"▶ 进入集合竞价拉取（开盘 {_t(AUCTION_OPEN_TIME).strftime('%H:%M')}，"
        f"写库目标 {_t(AUCTION_SOFT_DEADLINE).strftime('%H:%M')}，"
        f"截止 {hard.strftime('%H:%M')}；单票·禁批量·不推送）"
    )


def _to_tencent_symbol(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("5", "6", "9")):
        return f"sh{c}"
    return f"sz{c}"


def fetch_one_open_snapshot(code: str) -> Optional[dict]:
    """【铁律】每次只请求 1 只。返回 {open, pre_close, pct, name} 或 None。"""
    code = str(code).zfill(6)
    sym = _to_tencent_symbol(code)
    try:
        r = _SESSION.get(f"http://qt.gtimg.cn/q={sym}", timeout=6)
        text = (r.text or "").strip()
    except Exception as e:
        print(f"  腾讯单票失败 {code}: {e}")
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
            pre = float(parts[4] or 0)
            open_p = float(parts[5] or 0)
            if pre <= 0 or open_p <= 0:
                return None
            pct = round((open_p - pre) / pre * 100.0, 3)
            return {"name": name, "open": open_p, "pre_close": pre, "pct": pct}
        except (ValueError, IndexError, TypeError):
            continue
    return None


def fetch_one_live_quote(code: str) -> Optional[dict]:
    """
    【铁律】每次只请求 1 只。返回现价快照：
    {name, price, open, pre_close, high, low, pct}；失败 None。
    """
    code = str(code).zfill(6)
    sym = _to_tencent_symbol(code)
    try:
        r = _SESSION.get(f"http://qt.gtimg.cn/q={sym}", timeout=6)
        text = (r.text or "").strip()
    except Exception as e:
        print(f"  腾讯现价失败 {code}: {e}")
        return None
    for line in text.split(";"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        try:
            parts = line.split('="', 1)[1].rstrip('";').split("~")
            if len(parts) < 6:
                continue
            name = parts[1]
            price = float(parts[3] or 0)
            pre = float(parts[4] or 0)
            open_p = float(parts[5] or 0)
            if price <= 0 and open_p > 0:
                price = open_p
            if price <= 0 or pre <= 0:
                return None
            high = float(parts[33] or 0) if len(parts) > 33 else 0.0
            low = float(parts[34] or 0) if len(parts) > 34 else 0.0
            # 腾讯字段：部分源 high/low 在 33/34；若无则用现价
            if high <= 0:
                high = price
            if low <= 0:
                low = price
            pct = round((price - pre) / pre * 100.0, 3)
            return {
                "name": name,
                "price": price,
                "open": open_p,
                "pre_close": pre,
                "high": high,
                "low": low,
                "pct": pct,
            }
        except (ValueError, IndexError, TypeError):
            continue
    return None


def fetch_open_snapshot(codes: list[str], chunk_size: int = 1) -> dict[str, dict]:
    """
    兼容旧名。chunk_size 忽略：强制逐只 + AUCTION_SINGLE_SLEEP。
    """
    _ = chunk_size
    out: dict[str, dict] = {}
    seen: set[str] = set()
    for c in codes:
        cc = str(c).zfill(6)
        if cc in seen:
            continue
        seen.add(cc)
        snap = fetch_one_open_snapshot(cc)
        time_mod.sleep(AUCTION_SINGLE_SLEEP)
        if snap:
            out[cc] = snap
    return out


def fetch_open_ready(
    codes: list[str],
    *,
    force: bool = False,
    min_coverage: float = AUCTION_MIN_COVERAGE,
    poll_sec: float = AUCTION_POLL_SEC,
) -> dict[str, dict]:
    """等到今开覆盖达标或触及截止。全程单票，禁止批量。"""
    n = max(len(codes), 1)
    soft = datetime.combine(datetime.now().date(), _t(AUCTION_SOFT_DEADLINE))
    hard = datetime.combine(datetime.now().date(), _t(AUCTION_HARD_DEADLINE))
    open_t = _t(AUCTION_OPEN_TIME)

    if not force and datetime.now().time() < open_t:
        wait_until(open_t, f"集合竞价开盘 {open_t.strftime('%H:%M')}")

    best: dict[str, dict] = {}
    while True:
        # 只补尚未命中的代码，减少请求
        missing = [c for c in codes if str(c).zfill(6) not in best]
        if not missing:
            return best
        cur = fetch_open_snapshot(missing)
        best.update(cur)
        cov = len(best) / n
        print(f"  今开覆盖 {len(best)}/{n} ({cov * 100:.0f}%)【逐只】")
        if cov >= min_coverage:
            return best
        if force:
            return best
        now = datetime.now()
        if now >= hard:
            print("  ⚠ 已到硬截止，使用当前已落地今开")
            return best
        if now >= soft and cov >= 0.5:
            print("  ⚠ 已过软截止且覆盖≥50%，先行写库")
            return best
        time_mod.sleep(poll_sec)


def classify_open(pct: Optional[float]) -> str:
    if pct is None:
        return "无竞价"
    if pct < -0.05:
        return "绿开"
    if pct > 0.05:
        return "红开"
    return "平开"
