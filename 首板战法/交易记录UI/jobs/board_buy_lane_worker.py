#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买价分轨 worker：只用本轨日线源写 buy_price，单线程。"""
from __future__ import annotations

import os
import sys

_JOBS_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_JOBS_DIR)
_LIB_DIR = os.path.join(_BASE_DIR, "lib")
for _p in (_BASE_DIR, _LIB_DIR, _JOBS_DIR, os.path.dirname(_BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import BASE_DIR, ROOT_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime


import board_buy_sources as bbs  # noqa: E402
from platform_gate import PlatformQuotaExceeded, is_stopped  # noqa: E402
from stock_UI import _load_day, _save_day, _safe_float, auto_calculate  # noqa: E402

LOCK_DIR = os.path.join(BASE_DIR, "data", "board_buy_locks")
OUT_DIR = os.path.join(BASE_DIR, "data", "board_buy_out")
os.makedirs(LOCK_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _deadline_passed(until: str) -> bool:
    if not until:
        return False
    now = datetime.now()
    try:
        if len(until) <= 5:
            hh, mm = map(int, until.split(":"))
            return (now.hour, now.minute) >= (hh, mm)
        return now >= datetime.strptime(until, "%Y-%m-%d %H:%M")
    except Exception:
        return False


def _lock(day: str):
    path = os.path.join(LOCK_DIR, f"{day}.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def process_day(day: str, src: str, board: str = "") -> dict:
    from board_phase import trade_board

    trades = _load_day(day, "stock")
    filled = hold = fail = 0
    changed = False
    for i, t in enumerate(trades):
        if t.get("board_rank") is None:
            continue
        if board and trade_board(t) != board:
            continue
        if "买不进" in str(t.get("skip_reason") or ""):
            continue
        if _safe_float(t.get("buy_price")) is not None:
            continue
        code = str(t.get("stock_code") or "").zfill(6)
        if not code or code == "000000":
            continue
        try:
            op = bbs.get_open(code, day, prefer=src)
        except PlatformQuotaExceeded as e:
            log(f"  QUOTA_STOP {e}")
            raise
        except Exception as e:
            fail += 1
            log(f"  FAIL {day} {code}: {e}")
            continue
        if op is None:
            hold += 1
            log(f"  HOLD {day} {code} {t.get('stock_name')} src={src}")
            continue
        nt = dict(t)
        nt["buy_price"] = op
        nt = auto_calculate(nt)
        trades[i] = nt
        filled += 1
        changed = True
        log(f"  FILL {day} {code} {t.get('stock_name')} buy={op} src={src}")
    if changed:
        _save_day(day, trades, "stock")
    return {"filled": filled, "hold": hold, "fail": fail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True)
    ap.add_argument("--src", required=True, choices=list(bbs.OPEN_FETCHERS))
    ap.add_argument("--dates", required=True)
    ap.add_argument("--sleep", type=float, default=0.3, help="日与日之间额外间隔")
    ap.add_argument("--until", default="")
    ap.add_argument("--board", default="", choices=("", "创业板", "主板"))
    args = ap.parse_args()

    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    log(f"[buy-lane:{args.lane}] start n={len(dates)} src={args.src}")
    results = []
    for d in dates:
        if _deadline_passed(args.until):
            log(f"[buy-lane:{args.lane}] 硬停 {args.until}")
            break
        if is_stopped(args.src):
            log(f"[buy-lane:{args.lane}] 平台配额已关停 src={args.src}，本轨退出")
            results.append({"date": d, "status": "quota_stop"})
            break
        fd = _lock(d)
        if fd is None:
            results.append({"date": d, "status": "skip_locked"})
            continue
        try:
            st = process_day(d, args.src, args.board)
            results.append({"date": d, "status": "ok", **st})
        except PlatformQuotaExceeded as e:
            log(f"[buy-lane:{args.lane}] {e}")
            results.append({"date": d, "status": "quota_stop", "msg": str(e)})
            stop_lane = True
        except Exception as e:
            log(f"[buy-lane:{args.lane}] err {d}: {e}")
            results.append({"date": d, "status": "err", "msg": str(e)})
            stop_lane = False
        else:
            stop_lane = False
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except Exception:
                pass
        if stop_lane:
            break
        time.sleep(args.sleep)

    out = {
        "lane": args.lane, "src": args.src,
        "finished_at": datetime.now().isoformat(),
        "results": results,
    }
    path = os.path.join(OUT_DIR, f"{args.lane}_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"[buy-lane:{args.lane}] done → {path}")


if __name__ == "__main__":
    main()
