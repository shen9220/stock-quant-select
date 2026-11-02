#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回补时间窗（买卖价 / 历史取数共用）。

权威口径（2026-11-02 统一）
----
- **每日统一窗**：10:00 → **次日 00:00**；过零点硬停，第二天 10:00 再开。
- **00:00–10:00**：全体安静（把配额留给 07:00–10:00 正式通知；亦避免午夜尾巴抢跑）。
- **收盘闸** 15:00–15:25：仅交易日拒绝（保护 close job）。
- 生产源清单仍见 production_platform_reserve.json（门禁/观测用）；窗口本身已不再区分 sohu/生产源。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_RESERVE_FILE = os.path.join(_DATA_DIR, "production_platform_reserve.json")


def _is_trading_day(day: str) -> bool:
    try:
        import fetch_calendar as fc  # noqa: WPS433

        return bool(fc.is_trading_day(day))
    except Exception:
        try:
            import price_service as ps  # noqa: WPS433

            prev = ps.prev_trading_day(day)
            nxt = ps.next_trading_day(prev) if prev else None
            return nxt == day[:10]
        except Exception:
            return datetime.strptime(day, "%Y-%m-%d").weekday() < 5


def is_trading_day(day: Optional[str] = None) -> bool:
    day = day or datetime.now().strftime("%Y-%m-%d")
    return _is_trading_day(day[:10])


@lru_cache(maxsize=1)
def _load_reserve() -> dict:
    try:
        with open(_RESERVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "reserved_platforms": [
                "eastmoney", "ths", "tencent", "baostock", "tdx", "sina", "sohu",
            ],
            "non_reserved_platforms": [],
            "aliases": {
                "em": "eastmoney", "qq": "tencent", "tx": "tencent",
                "10jqka": "ths", "mootdx": "tdx", "pytdx": "tdx",
            },
        }


def canon_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    aliases = (_load_reserve().get("aliases") or {})
    if p in aliases:
        return str(aliases[p]).lower()
    if p in ("qq", "tx", "tencent"):
        return "tencent"
    if p in ("mootdx", "pytdx", "tongdaxin", "通达信"):
        return "tdx"
    if p in ("10jqka", "同花顺", "ths"):
        return "ths"
    if p in ("东财", "em_kline", "eastmoney", "em"):
        return "eastmoney"
    return p


def reserved_platforms() -> frozenset[str]:
    raw = _load_reserve().get("reserved_platforms") or []
    return frozenset(canon_platform(x) for x in raw)


def is_production_reserved(platform: str) -> bool:
    """兼容旧调用：统一窗后所有回补平台等同对待。"""
    return True


def production_model_map() -> dict:
    return dict(_load_reserve().get("models") or {})


def quiet_reason(now: Optional[datetime] = None) -> Optional[str]:
    """整段回补入口是否可跑。None=可跑。统一：00:00–10:00 静；交易日另拦 15:00–15:25。"""
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    hm = now.hour * 60 + now.minute

    # 每日统一：过零点到 10:00 前不跑
    if hm < 10 * 60:
        return "quiet 00:00-10:00 (unified window 10:00→00:00)"

    if is_trading_day(day) and 15 * 60 <= hm < 15 * 60 + 25:
        return "quiet 15:00-15:25 (protect close job)"
    return None


def in_allowed_window(now: Optional[datetime] = None) -> bool:
    return quiet_reason(now) is None


def platform_backfill_block_reason(
    platform: str, now: Optional[datetime] = None
) -> Optional[str]:
    """回补场景下某平台不可用原因；统一跟总窗。"""
    _ = canon_platform(platform)
    return quiet_reason(now)


def platform_backfill_allowed(platform: str, now: Optional[datetime] = None) -> bool:
    return platform_backfill_block_reason(platform, now) is None


def filter_lanes_by_reserve(
    lanes: list[dict],
    *,
    src_key: str = "src",
    now: Optional[datetime] = None,
) -> list[dict]:
    """过滤编排器分轨：去掉当前不可回补的平台。"""
    out = []
    for ln in lanes:
        src = str(ln.get(src_key) or ln.get("id") or "")
        if platform_backfill_allowed(src, now):
            out.append(ln)
    return out


def session_hard_stop(
    now: Optional[datetime] = None, *, for_reserved: bool = False
) -> datetime:
    """本段作业硬停：统一次日/今日 **00:00**（for_reserved 参数保留兼容，忽略）。"""
    _ = for_reserved
    now = now or datetime.now()
    if now.hour < 10:
        # 窗外：促停
        return now + timedelta(minutes=1)
    nxt = now + timedelta(days=1)
    return nxt.replace(hour=0, minute=0, second=0, microsecond=0)


def hour_slot_until(
    now: Optional[datetime] = None, *, for_reserved: bool = False
) -> str:
    now = now or datetime.now()
    hour_end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    hard = session_hard_stop(now, for_reserved=for_reserved)
    stop = min(hour_end, hard)
    if stop <= now:
        stop = now + timedelta(minutes=1)
    return stop.strftime("%Y-%m-%d %H:%M")


def default_until(now: Optional[datetime] = None, *, for_reserved: bool = False) -> str:
    return session_hard_stop(now, for_reserved=for_reserved).strftime("%Y-%m-%d %H:%M")


def describe(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    td = is_trading_day(day)
    q = quiet_reason(now)
    plats = sorted(reserved_platforms() | {"sohu"})
    blocks = {
        p: (platform_backfill_block_reason(p, now) or "OK")
        for p in plats
    }
    return (
        f"day={day} trading={td} allowed={q is None} quiet={q or '-'} "
        f"until={default_until(now)} hour_until={hour_slot_until(now)} "
        f"window=10:00→00:00 platform_blocks={blocks}"
    )


if __name__ == "__main__":
    print(describe())
    print("models:", json.dumps(production_model_map(), ensure_ascii=False, indent=2))
