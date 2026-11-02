#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock V2 历史竞价倒序回补（通达信安全窗）

权威流程：文档存放/V2历史回补标准流程.md
平台限流：文档存放/取数平台与限流规范.md + data/platform_rate_limits.json
数据工程：文档存放/V2 历史数据补全规范.md（倒置；禁全天量冒充竞价量）

- 窗口：11:00–14:30（到点硬停）
- 平台：tdx/mootdx，经 platform_gate，实际 ≤ 硬上限×70%
- 倒序：从游标/今天往前，直到 2026-01-01（含）
- 预筛涨停池：共享门面（磁盘/ths/em），禁止只打东财
- 已有 mode=backfill_mootdx_0925 且 status 明确则跳过（FORCE=1 可重跑）
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, time as dtime, timedelta

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
from platform_gate import quota_status  # noqa: E402

UNTIL = os.environ.get("V2_HIST_UNTIL", "2026-01-01")
WINDOW_START = dtime(11, 0)
WINDOW_END = dtime(14, 30)
CURSOR_PATH = os.path.join(v2.CACHE_DIR, "hist_backfill_cursor.json")


def _now() -> datetime:
    return datetime.now()


def in_window(now: datetime | None = None) -> bool:
    t = (now or _now()).time()
    return WINDOW_START <= t <= WINDOW_END


def _max_deadline() -> datetime | None:
    """MAX_MINUTES>0 时：自启动起最多跑这么多分钟（人工补跑用）。"""
    raw = os.environ.get("MAX_MINUTES", "").strip()
    if not raw:
        return None
    try:
        mins = float(raw)
    except ValueError:
        return None
    if mins <= 0:
        return None
    return _now() + timedelta(minutes=mins)


def _wait_hour_reset_enabled() -> bool:
    """小时软上限到了：等下一小时继续（日软上限仍立即停）。默认 IGNORE_WINDOW=1 时开启。"""
    raw = os.environ.get("WAIT_HOUR_RESET", "").strip()
    if raw == "1":
        return True
    if raw == "0":
        return False
    return os.environ.get("IGNORE_WINDOW", "0") == "1"


def _seconds_to_next_hour(now: datetime | None = None) -> float:
    now = now or _now()
    nxt = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)) + timedelta(seconds=3)
    return max(1.0, (nxt - now).total_seconds())


def _tdx_day_exhausted() -> bool:
    st = (quota_status("tdx") or {}).get("tdx") or {}
    if st.get("stopped") and "day" in str(st.get("stop_reason") or ""):
        return True
    rem = st.get("remaining_day")
    return rem is not None and int(rem) <= 0


def _load_cursor() -> dict:
    try:
        with open(CURSOR_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cursor(obj: dict) -> None:
    os.makedirs(os.path.dirname(CURSOR_PATH), exist_ok=True)
    tmp = CURSOR_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CURSOR_PATH)


def trading_days_desc(start: datetime, until: datetime) -> list[str]:
    """start 往前到 until（含），倒序。"""
    days: list[str] = []
    cur = start
    while cur >= until:
        if fc.is_trading_day(cur):
            days.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return days


def day_done(d: str, force: bool) -> bool:
    if force:
        return False
    path = os.path.join(v2.RESULTS_DIR, f"{d}_recommendations.json")
    if not os.path.isfile(path):
        return False
    data = v2._load_json(path)
    mode = str(data.get("mode") or "")
    # 铁律：早盘 live 实盘结果禁止被历史回补覆盖（除非 FORCE=1）
    if mode == "live" and data.get("status") in ("OK", "NO_TRADE"):
        return True
    if mode != "backfill_mootdx_0925":
        return False
    # 配额中断的日子允许续跑（未扫完）
    if data.get("tdx_quota_stopped"):
        return False
    return data.get("status") in ("OK", "NO_TRADE")


def main() -> int:
    force = os.environ.get("FORCE", "0") == "1"
    ignore_window = os.environ.get("IGNORE_WINDOW", "0") == "1"
    now = _now()
    if not in_window(now) and not ignore_window:
        print(f"[{now:%H:%M:%S}] 非 11:00–14:30 窗口，退出（安全）", flush=True)
        return 0

    deadline = _max_deadline()
    until = datetime.strptime(UNTIL, "%Y-%m-%d")
    # 倒序起点：游标 next_date 或今天
    cur = _load_cursor()
    start_s = cur.get("next_date") or now.strftime("%Y-%m-%d")
    start = datetime.strptime(start_s, "%Y-%m-%d")
    if start > now.replace(hour=0, minute=0, second=0, microsecond=0):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    days = trading_days_desc(start, until)
    print(
        f"[{now:%H:%M:%S}] V2 hist daily start={start_s} until={UNTIL} "
        f"days={len(days)} force={force} ignore_window={ignore_window} "
        f"deadline={deadline.isoformat() if deadline else '-'}",
        flush=True,
    )
    print(f"[{now:%H:%M:%S}] tdx quota={quota_status('tdx').get('tdx')}", flush=True)

    for d in days:
        if deadline and _now() >= deadline:
            print(f"[{_now():%H:%M:%S}] MAX_MINUTES 到期，停（游标保留 {d}）", flush=True)
            _save_cursor({
                "next_date": d,
                "updated_at": _now().isoformat(),
                "stop": "max_minutes",
            })
            return 0
        if not ignore_window and not in_window():
            print(f"[{_now():%H:%M:%S}] 到达 14:30，停（游标保留 {d}）", flush=True)
            _save_cursor({"next_date": d, "updated_at": _now().isoformat(), "stop": "window_end"})
            return 0
        if day_done(d, force):
            print(f"[{_now():%H:%M:%S}] skip {d}", flush=True)
            _save_cursor({"next_date": d, "updated_at": _now().isoformat(), "last_done": d})
            continue
        print(f"[{_now():%H:%M:%S}] ▶ backfill {d}", flush=True)
        try:
            res = v2.run_auction_final(d, backfill=True, do_notify=False)
            print(
                f"[{_now():%H:%M:%S}] ✓ {d} {res.get('status')} "
                f"n={len(res.get('recommendations') or [])}",
                flush=True,
            )
            # 下一未完成日：当前日的前一交易日
            prev = fc.prev_trading_day(d)
            _save_cursor({
                "next_date": prev or UNTIL,
                "last_done": d,
                "updated_at": _now().isoformat(),
            })
        except v2.TdxQuotaStop as e:
            detail = str(e)
            print(f"[{_now():%H:%M:%S}] TDX soft-cap @ {d}: {detail}", flush=True)
            print(f"[{_now():%H:%M:%S}] tdx quota={quota_status('tdx').get('tdx')}", flush=True)
            _save_cursor({
                "next_date": d,
                "updated_at": _now().isoformat(),
                "stop": "tdx_soft_cap",
                "detail": detail,
            })
            # 仅小时软上限：等下一整点继续（铁律仍守日≤70%）
            if _wait_hour_reset_enabled() and not _tdx_day_exhausted():
                wait_s = _seconds_to_next_hour()
                print(
                    f"[{_now():%H:%M:%S}] 小时软上限，等待 {wait_s:.0f}s 到下一小时后续跑 {d}",
                    flush=True,
                )
                time.sleep(wait_s)
                print(f"[{_now():%H:%M:%S}] 续跑 {d} tdx={quota_status('tdx').get('tdx')}", flush=True)
                continue
            return 2
        except Exception as e:
            print(f"[{_now():%H:%M:%S}] ✗ {d} {e}", flush=True)
            _save_cursor({"next_date": d, "updated_at": _now().isoformat(), "last_error": str(e)})
            # 单日失败不推进游标，下轮重试
            time.sleep(2)
            continue
        time.sleep(0.3)

    print(f"[{_now():%H:%M:%S}] ===== hist until {UNTIL} complete =====", flush=True)
    _save_cursor({"next_date": UNTIL, "updated_at": _now().isoformat(), "stop": "complete"})
    return 0


if __name__ == "__main__":
    # mootdx/线程池退出时 Py_Finalize 可能死等；硬退避免卡住后续 pages 同步
    code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


