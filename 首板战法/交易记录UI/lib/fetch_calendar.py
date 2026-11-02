"""A 股交易日历：本地缓存 + 周末/法定节假日（不依赖 py_mini_racer / baostock）。

口径：上交所休市安排。办公室「调休上班」的周末仍休市，不计入交易日。
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

import json
import os
from datetime import datetime, timedelta
from typing import Optional, Union

CAL_FILE = os.path.join(BASE_DIR, "data", "trade_calendar.json")

# 变更节假日表时递增，强制重建缓存
HOLIDAY_REV = 3

# 非周末的休市日（上交所口径；周末由 weekday 规则排除）
_CN_HOLIDAYS = {
    # 2023
    "2023-01-02", "2023-01-23", "2023-01-24", "2023-01-25", "2023-01-26", "2023-01-27",
    "2023-04-05", "2023-05-01", "2023-05-02", "2023-05-03",
    "2023-06-22", "2023-06-23",
    "2023-09-29",
    "2023-10-02", "2023-10-03", "2023-10-04", "2023-10-05", "2023-10-06",
    # 2024
    "2024-01-01", "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
    "2024-04-04", "2024-04-05", "2024-05-01", "2024-05-02", "2024-05-03",
    "2024-06-10", "2024-09-16", "2024-09-17",
    "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
    # 2025
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-03", "2025-02-04",
    "2025-04-04", "2025-05-01", "2025-05-02", "2025-05-05",
    "2025-06-02", "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
    # 2026（国办发明电〔2025〕7号 + 上交所休市公告）
    # 元旦 1/1–1/3；春节 2/15–2/23；清明 4/4–4/6；劳动节 5/1–5/5；
    # 端午 6/19–6/21；中秋 9/25–9/27；国庆 10/1–10/7
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
    "2026-04-06",
    "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19",
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}


def _to_dt(date_like: Union[str, datetime]) -> datetime:
    if isinstance(date_like, datetime):
        return date_like.replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime.strptime(str(date_like)[:10], "%Y-%m-%d")


def _is_trading_day(dt: datetime) -> bool:
    s = dt.strftime("%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    return s not in _CN_HOLIDAYS


def is_trading_day(date_like: Union[str, datetime]) -> bool:
    return _is_trading_day(_to_dt(date_like))


def build_calendar(start: str, end: str) -> list[str]:
    s = datetime.strptime(start[:10], "%Y-%m-%d")
    e = datetime.strptime(end[:10], "%Y-%m-%d")
    out = []
    cur = s
    while cur <= e:
        if _is_trading_day(cur):
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def load_calendar(start: str = "2023-01-01", end: str = "2026-12-31") -> list[str]:
    """加载交易日列表；缓存缺年/节假日版本变更时自动重建。"""
    start, end = start[:10], end[:10]
    if os.path.isfile(CAL_FILE):
        try:
            with open(CAL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            days = data.get("trade_dates") or []
            if (
                days
                and data.get("holiday_rev") == HOLIDAY_REV
                and days[0] <= start
                and days[-1] >= end
            ):
                return [d for d in days if start <= d <= end]
        except Exception:
            pass
    days = build_calendar(start, end)
    os.makedirs(os.path.dirname(CAL_FILE), exist_ok=True)
    with open(CAL_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "trade_dates": days,
                "source": "weekday+cn_holiday",
                "holiday_rev": HOLIDAY_REV,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return days


def next_trading_day(date_like: Union[str, datetime]) -> Optional[str]:
    dt = _to_dt(date_like)
    key = dt.strftime("%Y-%m-%d")
    days = load_calendar()
    for d in days:
        if d > key:
            return d
    # 缓存末尾不够时现场推算
    cur = dt + timedelta(days=1)
    for _ in range(30):
        if _is_trading_day(cur):
            return cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)
    return None


def prev_trading_day(date_like: Union[str, datetime]) -> Optional[str]:
    dt = _to_dt(date_like)
    key = dt.strftime("%Y-%m-%d")
    days = load_calendar()
    past = [d for d in days if d < key]
    if past:
        return past[-1]
    cur = dt - timedelta(days=1)
    for _ in range(30):
        if _is_trading_day(cur):
            return cur.strftime("%Y-%m-%d")
        cur -= timedelta(days=1)
    return None


def prev_trading_day_dt(date_like: Union[str, datetime]) -> datetime:
    s = prev_trading_day(date_like)
    if not s:
        raise ValueError(f"无法推算前一交易日: {date_like}")
    return datetime.strptime(s, "%Y-%m-%d")


def next_trading_day_dt(date_like: Union[str, datetime]) -> datetime:
    s = next_trading_day(date_like)
    if not s:
        raise ValueError(f"无法推算下一交易日: {date_like}")
    return datetime.strptime(s, "%Y-%m-%d")
