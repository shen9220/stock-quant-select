#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首板口径。

统一时间轴：D0-1为涨停日，D0-2为前一交易日检查；D0为D1模式买入日。
D1模式D1卖出；D2模式D0不买、D1买入、D2卖出。
兼容旧函数参数 d0=涨停日（新D0-1），d0_prev=检查日（新D0-2），仅参数名保留。
前一日涨停池缺了某只，不能据此把它当成首板。
"""
from __future__ import annotations

import json
import os
from typing import Optional

_LIB = os.path.dirname(os.path.abspath(__file__))
# 本文件在 首板战法/交易记录UI/lib，上三级是 stock_quant_select
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_LIB)))
BARS_PATH = os.path.join(_ROOT, "workspace", "tmp", "v1_top2_bars.json")


def limit_threshold(code: str) -> float:
    c = str(code or "").zfill(6)
    if c.startswith(("300", "301", "688", "689")):
        return 19.0
    return 9.5


def load_bars(path: Optional[str] = None) -> dict:
    p = path or BARS_PATH
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _pct(series: dict, day: str, prev: str) -> Optional[float]:
    if not day or not prev or day not in series or prev not in series:
        return None
    base = (series.get(prev) or {}).get("c")
    close = (series.get(day) or {}).get("c")
    if not base or not close:
        return None
    return (float(close) / float(base) - 1.0) * 100.0


def classify_first_board(code: str, d0: str, d0_prev: str, bars: Optional[dict] = None) -> str:
    """返回 first / lianban / not_d0_limit / unknown。

    lianban：D0-1 已涨停，必须跳过。
    first：D0 涨停，且 D0-1 有行情但不是涨停。
    not_d0_limit：D0 有行情但不是涨停。
    unknown：日线不够，不能当成首板。
    """
    code = str(code or "").zfill(6)
    series = (bars or {}).get(code) or {}
    if not series or not d0 or not d0_prev:
        return "unknown"
    thr = limit_threshold(code)
    # D0-1 的涨跌要用 D0-1 相对再前一交易日。没有 D0-2 时，用序列里 D0-1 之前最近一根。
    earlier = sorted(d for d in series if d < d0_prev)
    d0_2 = earlier[-1] if earlier else None
    prev_pct = _pct(series, d0_prev, d0_2) if d0_2 else None
    if d0_prev in series and prev_pct is not None and prev_pct >= thr:
        return "lianban"
    if d0_prev in series and prev_pct is None:
        return "unknown"
    d0_pct = _pct(series, d0, d0_prev if d0_prev in series else d0_2)
    if d0 not in series:
        return "unknown"
    if d0_pct is None:
        return "unknown"
    if d0_pct < thr:
        return "not_d0_limit"
    if d0_prev not in series:
        # D0-1 没有这只的行情：当天没涨停。D0 相对更早收盘已达到涨停幅度，算首板。
        return "first"
    return "first"
