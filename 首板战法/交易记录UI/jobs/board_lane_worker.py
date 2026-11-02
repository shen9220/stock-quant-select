#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单平台分轨 worker：内部严格单线程，每次只处理一天。"""
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


import backfill_board_records as bbr  # noqa: E402

LOCK_DIR = os.path.join(BASE_DIR, "data", "board_day_locks")
OUT_DIR = os.path.join(BASE_DIR, "data", "board_lane_out")
os.makedirs(LOCK_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)


def _deadline_passed(until: str | None) -> bool:
    if not until:
        return False
    # until = "HH:MM" or "YYYY-MM-DD HH:MM"
    now = datetime.now()
    try:
        if len(until) <= 5:
            hh, mm = map(int, until.split(":"))
            return (now.hour, now.minute) >= (hh, mm)
        dt = datetime.strptime(until, "%Y-%m-%d %H:%M")
        return now >= dt
    except Exception:
        return False


def _lock_day(day: str):
    path = os.path.join(LOCK_DIR, f"{day}.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True)
    ap.add_argument("--zt", default="em")
    ap.add_argument("--hist", default="tx")
    ap.add_argument("--dates", required=True, help="逗号分隔 YYYY-MM-DD")
    ap.add_argument("--sleep", type=float, default=1.2)
    ap.add_argument("--until", default="", help="硬停 HH:MM（本地）")
    ap.add_argument("--program", default="stock")
    args = ap.parse_args()

    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    results = []
    bbr.log(f"[lane:{args.lane}] start n={len(dates)} zt={args.zt} hist={args.hist} until={args.until or '-'}")

    for d in dates:
        if _deadline_passed(args.until or None):
            bbr.log(f"[lane:{args.lane}] 到达硬停 {args.until}，收工")
            break
        if bbr.already_done(args.program, d):
            bbr.log(f"[lane:{args.lane}] {d} 已齐，跳过")
            results.append({"date": d, "status": "skip_done"})
            continue
        fd = _lock_day(d)
        if fd is None:
            bbr.log(f"[lane:{args.lane}] {d} 其他轨占用，跳过")
            results.append({"date": d, "status": "skip_locked"})
            continue
        try:
            try:
                mark, msg = bbr.process_one_day(
                    args.program, d, zt_prefer=args.zt, hist_prefer=args.hist)
                if mark:
                    bbr.mark_attempted(args.program, [d])
                results.append({"date": d, "status": "ok" if mark else "retry", "msg": msg})
            except Exception as e:
                bbr.log(f"[lane:{args.lane}] [err] {d}: {e}")
                results.append({"date": d, "status": "err", "msg": str(e)})
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        time.sleep(args.sleep)

    out = {
        "lane": args.lane,
        "zt": args.zt,
        "hist": args.hist,
        "finished_at": datetime.now().isoformat(),
        "results": results,
    }
    path = os.path.join(OUT_DIR, f"{args.lane}_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    bbr.log(f"[lane:{args.lane}] done → {path}")


if __name__ == "__main__":
    main()
