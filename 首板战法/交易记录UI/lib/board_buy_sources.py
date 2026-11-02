#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块 tab 买价（D1 开盘）多源：每源单线程，分轨并行。

铁律：同一平台同时仅 1 个在途请求（platform_gate）；平台之间可并行。
腾讯系（ifzq 日线 / ak hist_tx / mkline）共用门禁名 `tencent`。

可用：
  - sohu  搜狐 hisHq
  - qq    腾讯 ifzq 日线（门禁 tencent）
  - tx    akshare hist_tx（门禁 tencent；勿与 qq 同时开两条买轨）

不可用：baostock 黑名单；AlphaFeed 无 Key 已排除；mootdx 本机行情空。
"""
from __future__ import annotations

import os
from typing import Callable, Optional

import requests

from platform_gate import platform_slot

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})


def _prefix(code: str) -> str:
    c = str(code).zfill(6)
    return "sh" if c.startswith(("5", "6", "9")) else "sz"


def open_sohu(code: str, day: str) -> Optional[float]:
    """搜狐：hq 行 [date, open, close, ...]；单日单请求。"""
    c = str(code).zfill(6)
    d8 = day.replace("-", "")
    url = (
        f"https://q.stock.sohu.com/hisHq?code=cn_{c}"
        f"&start={d8}&end={d8}&stat=1&order=D&period=d"
    )
    with platform_slot("sohu"):
        r = _SESSION.get(url, timeout=15)
        r.raise_for_status()
        j = r.json()
    if not j or not isinstance(j, list):
        return None
    hq = (j[0] or {}).get("hq") or []
    if not hq:
        return None
    for row in hq:
        if str(row[0]).replace("-", "") == d8 or str(row[0]) == day:
            return float(row[1])
    return float(hq[0][1])


def open_qq(code: str, day: str) -> Optional[float]:
    """腾讯 ifzq 日线；与其它腾讯入口共用 tencent 门禁。"""
    c = str(code).zfill(6)
    p = _prefix(c)
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={p}{c},day,{day},{day},320,qfq"
    )
    with platform_slot("tencent"):
        r = _SESSION.get(url, timeout=15)
        r.raise_for_status()
        node = ((r.json() or {}).get("data") or {}).get(f"{p}{c}") or {}
    rows = node.get("qfqday") or node.get("day") or []
    for row in rows:
        if str(row[0])[:10] == day:
            return float(row[1])
    return None


def open_tx(code: str, day: str) -> Optional[float]:
    """腾讯 ak 日线；门禁 tencent（勿与 qq 买轨并行开两条）。"""
    import akshare as ak

    c = str(code).zfill(6)
    p = _prefix(c)
    d8 = day.replace("-", "")
    with platform_slot("tencent"):
        df = ak.stock_zh_a_hist_tx(
            symbol=f"{p}{c}", start_date=d8, end_date=d8, adjust="")
    if df is None or df.empty:
        return None
    col = "open" if "open" in df.columns else "开盘"
    return float(df.iloc[0][col])


def open_em(code: str, day: str) -> Optional[float]:
    """东财日线（本机常失败）；保留接口，默认不启用。"""
    saved = {}
    for k in list(os.environ):
        if "proxy" in k.lower():
            saved[k] = os.environ.pop(k)
    try:
        import akshare as ak

        c = str(code).zfill(6)
        d8 = day.replace("-", "")
        with platform_slot("em"):
            df = ak.stock_zh_a_hist(
                symbol=c, period="daily", start_date=d8, end_date=d8, adjust="")
        if df is None or df.empty or "开盘" not in df.columns:
            return None
        return float(df.iloc[0]["开盘"])
    finally:
        os.environ.update(saved)


OPEN_FETCHERS: dict[str, Callable[[str, str], Optional[float]]] = {
    "sohu": open_sohu,
    "qq": open_qq,
    "tx": open_tx,
    "em": open_em,
}


def list_buy_lanes() -> list[dict]:
    # 默认只开 sohu + qq：腾讯只占一条买轨，分钟卖价再与之串行共用 tencent 门禁
    return [
        {"id": "sohu", "src": "sohu", "label": "搜狐日线开盘"},
        {"id": "qq", "src": "qq", "label": "腾讯QQ日线开盘"},
        {"id": "tx", "src": "tx", "label": "腾讯ak日线开盘(备用)"},
        {"id": "em", "src": "em", "label": "东财日线开盘(默认关)"},
    ]


def get_open(code: str, day: str, prefer: str) -> Optional[float]:
    from platform_gate import PlatformQuotaExceeded, mark_rate_limited

    fn = OPEN_FETCHERS.get(prefer)
    if not fn:
        return None
    try:
        return fn(code, day)
    except PlatformQuotaExceeded:
        raise
    except Exception as e:
        msg = str(e).lower()
        if any(k in msg for k in ("429", "too many", "限流", "频率", "forbidden", "封禁")):
            mark_rate_limited(prefer, f"buy_open:{type(e).__name__}:{e}")
            raise
        return None
