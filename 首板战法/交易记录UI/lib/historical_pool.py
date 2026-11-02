"""
历史日期涨停池回退：东财接口无数据时用同花顺首板池 + baostock 日K 补字段。
供 slow / up 1-6 月回填使用。
"""
from __future__ import annotations

import os
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_LIB_DIR)
_JOBS_DIR = os.path.join(_BASE_DIR, "jobs")
for _p in (_BASE_DIR, _LIB_DIR, _JOBS_DIR, os.path.dirname(_BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import BASE_DIR, ROOT_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import os
import re
import sys
from datetime import datetime

import requests

import paths  # noqa: E402

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.10jqka.com.cn/"}


def _is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


def _is_chinext(code: str) -> bool:
    return str(code).zfill(6).startswith("300")


def fetch_ths_first_board(d0_8: str) -> list[dict]:
    url = f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&date={d0_8}"
    try:
        r = requests.get(url, timeout=20, headers=HEADERS)
        r.raise_for_status()
        info = ((r.json() or {}).get("data") or {}).get("info") or []
    except Exception as e:
        print(f"    [ths] 涨停池 {d0_8} 失败: {e}")
        return []

    out, seen = [], set()
    for x in info:
        code = str(x.get("code") or "").zfill(6)
        name = str(x.get("name") or "").strip()
        tag = str(x.get("change_tag") or "")
        again = x.get("is_again_limit")
        if not code or code.startswith(("688", "8", "4", "92")):
            continue
        if again in (1, "1", True) and tag != "FIRST_LIMIT":
            continue
        if tag == "LIMIT_BACK" and again in (1, "1", True):
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name})
    return out


def _day_bar(code: str, d0: str) -> dict:
    """日 K：腾讯/东财日线（price_service），不调用 baostock。"""
    import price_service as ps

    out = {"close": 0.0, "amount": 0.0, "pct": 0.0, "turn": 0.0}
    code6 = str(code).zfill(6)
    prev = ps.prev_trading_day(d0)
    start = prev or d0
    df = ps._fetch_daily(code6, start, d0)
    if df is None or df.empty:
        return out
    target = ps._to_dt(d0)
    prev_dt = ps._to_dt(prev) if prev else None
    row = df[df["日期"] == target]
    if row.empty:
        return out
    r = row.iloc[0]
    close = float(r["收盘"] or 0)
    out["close"] = close
    if "amount" in r.index:
        out["amount"] = float(r["amount"] or 0)
    prev_close = 0.0
    if prev_dt is not None:
        prow = df[df["日期"] == prev_dt]
        if not prow.empty:
            prev_close = float(prow.iloc[0]["收盘"] or 0)
    if prev_close > 0 and close > 0:
        out["pct"] = round((close - prev_close) / prev_close * 100, 2)
    return out


def enrich_candidates(stocks: list[dict], d0: str) -> list[dict]:
    enriched = []
    for s in stocks:
        bar = _day_bar(s["code"], d0)
        if bar["close"] <= 0 and bar["amount"] <= 0:
            continue
        enriched.append({
            **s,
            "close": bar["close"],
            "amount": bar["amount"],
            "pct": bar["pct"],
            "turn": bar["turn"],
            "is_st": _is_st(s["name"]),
            "is_chinext": _is_chinext(s["code"]),
        })
    return enriched


def _slow_score(c: dict) -> float:
    price = c["close"]
    if c["is_st"]:
        if price <= 0 or price > 6.0:
            return -1
        turn_s = min(max(c["turn"], 0), 45) / 45 * 30
        return 40 + turn_s + min(c["amount"] / 1e8, 5) * 4
    if price <= 0 or price > 12.0:
        return -1
    turn_s = 20 - abs(c["turn"] - 10) / 10 * 20 if c["is_chinext"] else min(c["turn"], 25) / 25 * 20
    amt_s = min(c["amount"] / 1e8, 5) / 5 * 25
    pct_s = min(max(c["pct"], 0), 10) / 10 * 25
    track = 10 if c["is_chinext"] else 0
    return turn_s + amt_s + pct_s + track


def select_slow_top2(candidates: list[dict]) -> list[dict]:
    scored = []
    for c in candidates:
        s = _slow_score(c)
        if s < 0:
            continue
        scored.append({**c, "score": s})
    if not scored:
        return []

    scored.sort(key=lambda x: x["score"], reverse=True)
    st_pool = [x for x in scored if x["is_st"]]
    chi_pool = [x for x in scored if x["is_chinext"] and not x["is_st"]]
    picks, used = [], set()

    if st_pool:
        picks.append(st_pool[0])
        used.add(st_pool[0]["code"])
    if chi_pool:
        for x in chi_pool:
            if x["code"] not in used:
                picks.append(x)
                used.add(x["code"])
                break
    for x in scored:
        if len(picks) >= 2:
            break
        if x["code"] not in used:
            picks.append(x)
            used.add(x["code"])

    return [
        {"stock_code": p["code"], "stock_name": p["name"], "rank": i + 1, "score": round(p["score"], 2)}
        for i, p in enumerate(picks[:2])
    ]


def _up_score(c: dict) -> float:
    if c["is_st"]:
        return -1
    zf = min(max(c["pct"], 0), 10) / 10 * 40
    amt = min(c["amount"] / 1e8, 5) / 5 * 20
    turn = 20 - abs(c["turn"] - 10) / 10 * 20
    return zf + amt + max(turn, 0)


def select_up_top2(candidates: list[dict]) -> list[dict]:
    scored = []
    for c in candidates:
        s = _up_score(c)
        if s < 0:
            continue
        scored.append({**c, "score": s})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return [
        {"stock_code": p["code"], "stock_name": p["name"], "rank": i + 1, "score": round(p["score"], 2)}
        for i, p in enumerate(scored[:2])
    ]


def _save_md(program: str, d0: str, stocks: list[dict], subtitle: str):
    os.makedirs(paths.program_results_dir(program), exist_ok=True)
    display = f"{d0[:4]}年{d0[5:7]}月{d0[8:10]}日"
    lines = [
        f"=== {display} {subtitle}（前2只，历史回填）===",
        "数据来源: 同花顺首板池 + baostock 日K（东财历史池不可用时的回退）",
        "",
    ]
    for s in stocks:
        lines.append(f"排名 {s['rank']}: {s['stock_code']} - {s['stock_name']}")
        lines.append(f"  综合得分: {s.get('score', 0):.2f}")
        lines.append("")
    lines.append("=== 其他未入选前二原因 ===")
    lines.append("历史回填模式，详见 backfill_slow_up_historical.py")
    path = paths.recommendation_md(program, d0)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def generate_slow_via_ths(d0: str) -> list[dict]:
    d8 = d0.replace("-", "")
    first = fetch_ths_first_board(d8)
    if not first:
        return []
    candidates = enrich_candidates(first, d0)
    stocks = select_slow_top2(candidates)
    if stocks:
        _save_md("slow", d0, stocks, "低价一进二推荐")
        print(f"    ✅ [ths] slow Top2: {[s['stock_code'] for s in stocks]}")
    return stocks


def generate_up_via_ths(d0: str) -> list[dict]:
    d8 = d0.replace("-", "")
    first = fetch_ths_first_board(d8)
    if not first:
        return []
    candidates = enrich_candidates(first, d0)
    stocks = select_up_top2(candidates)
    if stocks:
        _save_md("up", d0, stocks, "强度首板推荐")
        print(f"    ✅ [ths] up Top2: {[s['stock_code'] for s in stocks]}")
    return stocks


def em_has_zt_pool(d0: str) -> bool:
    try:
        import akshare as ak

        df = ak.stock_zt_pool_em(date=d0.replace("-", ""))
        return df is not None and not df.empty and "代码" in df.columns
    except Exception:
        return False
