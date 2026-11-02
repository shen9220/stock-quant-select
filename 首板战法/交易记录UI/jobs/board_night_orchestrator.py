#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock 主板/创业板 2026 夜班多源编排器（v11）。

- 多轨并行：每轨绑定不同 涨停池源×日线源；轨内单线程
- 每轨每轮最多 --per-lane 天（默认 3）
- 时间窗：见 lib/backfill_window.py（生产源 10:00→00:00；总窗非生产到 05:00）
- 硬停 --until 默认会话硬停（次日 05:00 / 非交易日 23:59）；轨内再按 zt/hist 预留过滤
"""
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
_HERE = _JOBS_DIR

import argparse
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta


import backfill_board_records as bbr  # noqa: E402
import board_data_sources as bds  # noqa: E402

LOCK_FILE = os.path.join(BASE_DIR, "data", "board_night.lock")
LOG_DIR = os.path.join(BASE_DIR, "data", "board_night_logs")
PYTHON = "/usr/local/bin/python3"
os.makedirs(LOG_DIR, exist_ok=True)


def log(msg: str) -> None:
    line = f"[{datetime.now():%F %T}] {msg}"
    print(line, flush=True)
    day = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(LOG_DIR, f"{day}.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _in_quiet_zone(now: datetime | None = None) -> str | None:
    """返回安静原因；可跑则 None。"""
    from backfill_window import quiet_reason  # noqa: WPS433
    return quiet_reason(now)


def _default_until() -> str:
    from backfill_window import default_until  # noqa: WPS433
    return default_until()


def _filter_lanes(lanes: list[dict], *, force: bool) -> list[dict]:
    """zt/hist 任一落入生产源早盘预留则整轨跳过。"""
    if force:
        return lanes
    from backfill_window import platform_backfill_allowed  # noqa: WPS433

    out = []
    for ln in lanes:
        if platform_backfill_allowed(ln.get("zt", "")) and platform_backfill_allowed(
            ln.get("hist", "")
        ):
            out.append(ln)
    return out


def _acquire() -> int | None:
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def assign_dates(todo: list[str], lanes: list[dict], per_lane: int) -> dict[str, list[str]]:
    """日期互不重叠，轮询分给各轨，每轨最多 per_lane。"""
    out: dict[str, list[str]] = {ln["id"]: [] for ln in lanes}
    if not lanes or not todo:
        return out
    idx = 0
    for d in todo:
        # 找还有配额的轨
        tried = 0
        while tried < len(lanes):
            ln = lanes[idx % len(lanes)]
            idx += 1
            tried += 1
            if len(out[ln["id"]]) < per_lane:
                out[ln["id"]].append(d)
                break
        if all(len(out[ln["id"]]) >= per_lane for ln in lanes):
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-lane", type=int, default=3, help="每轨本轮天数")
    ap.add_argument("--sleep", type=float, default=1.2)
    ap.add_argument("--until", default="", help="硬停 YYYY-MM-DD HH:MM 或 HH:MM")
    ap.add_argument("--force", action="store_true", help="忽略安静窗（慎用）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--rounds", type=int, default=1, help="分配轮数（默认1轮；夜班可多轮）")
    args = ap.parse_args()

    todo = bbr.list_todo_dates("stock", "2026-01-01")
    lanes = bds.list_available_lanes()
    if args.status:
        print(f"待补 {len(todo)} 天 | 可用分轨 {len(lanes)}")
        for ln in lanes:
            print(f"  - {ln['id']}: {ln['label']}")
        if todo:
            print("最近待补:", ", ".join(todo[:12]), ("…" if len(todo) > 12 else ""))
        return

    quiet = _in_quiet_zone()
    if quiet and not args.force:
        log(f"拒绝启动：{quiet}")
        return

    until = args.until or _default_until()
    # 若 until 仅 HH:MM，补全日期
    if len(until) <= 5:
        now = datetime.now()
        hh, mm = map(int, until.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        until = target.strftime("%Y-%m-%d %H:%M")

    lock = _acquire()
    if lock is None:
        log("另一 orchestrator 运行中，跳过")
        return

    log(f"=== board night start lanes={len(lanes)} per_lane={args.per_lane} "
        f"todo={len(todo)} until={until} ===")
    try:
        for round_i in range(1, max(1, args.rounds) + 1):
            if _in_quiet_zone() and not args.force:
                log("进入安静窗，停止后续轮次")
                break
            # 硬停检查
            try:
                if datetime.now() >= datetime.strptime(until, "%Y-%m-%d %H:%M"):
                    log(f"已到硬停 {until}")
                    break
            except Exception:
                pass

            todo = bbr.list_todo_dates("stock", "2026-01-01")
            if not todo:
                log("待补为空，收工")
                break
            alive = _filter_lanes(lanes, force=args.force)
            if not alive:
                log("分轨全部落入早盘预留/不可用，本轮收工")
                break
            assignment = assign_dates(todo, alive, args.per_lane)
            procs = []
            for ln in alive:
                dates = assignment.get(ln["id"]) or []
                if not dates:
                    continue
                cmd = [
                    PYTHON, os.path.join(_HERE, "board_lane_worker.py"),
                    "--lane", ln["id"],
                    "--zt", ln["zt"],
                    "--hist", ln["hist"],
                    "--dates", ",".join(dates),
                    "--sleep", str(args.sleep),
                    "--until", until,
                    "--program", "stock",
                ]
                env = os.environ.copy()
                # 非 baostock 轨禁止误触 bs login（历史封禁风险）
                if ln.get("hist") != "bs":
                    env["DISABLE_BAOSTOCK"] = "1"
                else:
                    env.pop("DISABLE_BAOSTOCK", None)
                log(f"round{round_i} spawn {ln['id']} dates={dates}")
                procs.append(subprocess.Popen(cmd, cwd=_HERE, env=env))

            if not procs:
                log("本轮无任务可派，结束")
                break
            # 等待本轮全部结束（或超时到硬停）
            while procs:
                still = []
                for p in procs:
                    rc = p.poll()
                    if rc is None:
                        still.append(p)
                procs = still
                if not procs:
                    break
                try:
                    if datetime.now() >= datetime.strptime(until, "%Y-%m-%d %H:%M"):
                        log("硬停到达，终止子进程")
                        for p in procs:
                            p.terminate()
                        time.sleep(2)
                        for p in procs:
                            if p.poll() is None:
                                p.kill()
                        procs = []
                        break
                except Exception:
                    pass
                time.sleep(5)
            log(f"round{round_i} 完成，剩余待补≈{len(bbr.list_todo_dates('stock'))}")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
        log("=== board night end ===")


if __name__ == "__main__":
    main()
