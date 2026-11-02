#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-10 Stock V2 倒序历史回补（今开≈竞价，BACKFILL_OPEN）。"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SHOU = os.path.dirname(os.path.dirname(_ROOT))
_UI = os.path.join(_SHOU, "交易记录UI")
_PUBLIC = os.path.join(_SHOU, "公共")
_UI_LIB = os.path.join(_UI, "lib")
_UI_JOBS = os.path.join(_UI, "jobs")
for p in (_ROOT, _PUBLIC, _UI, _UI_LIB, _UI_JOBS):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DISABLE_BAOSTOCK", "1")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

import fetch_calendar as fc  # noqa: E402
import stock_v2 as v2  # noqa: E402


def october_days_desc(year: int = 2026) -> list[str]:
    start = datetime(year, 10, 1)
    end = datetime(year, 10, 28)
    days: list[str] = []
    cur = end
    while cur >= start:
        if fc.is_trading_day(cur):
            days.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return days


def main() -> None:
    force = os.environ.get("FORCE", "0") == "1"
    days = october_days_desc()
    print(f"[{datetime.now():%H:%M:%S}] October backfill days={len(days)} force={force}", flush=True)
    for d in days:
        out = os.path.join(v2.RESULTS_DIR, f"{d}_recommendations.json")
        if os.path.isfile(out) and not force:
            prev = v2._load_json(out)
            print(f"[{datetime.now():%H:%M:%S}] skip {d} ({prev.get('mode') or prev.get('status')})", flush=True)
            continue
        print(f"[{datetime.now():%H:%M:%S}] ▶ backfill {d}", flush=True)
        try:
            res = v2.run_auction_final(d, backfill=True, do_notify=False)
            print(
                f"[{datetime.now():%H:%M:%S}] ✓ {d} {res.get('status')} "
                f"n={len(res.get('recommendations') or [])} mode={res.get('mode')}",
                flush=True,
            )
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] ✗ {d} {e}", flush=True)
        time.sleep(0.5)
    print(f"[{datetime.now():%H:%M:%S}] ===== October backfill end =====", flush=True)


if __name__ == "__main__":
    main()
