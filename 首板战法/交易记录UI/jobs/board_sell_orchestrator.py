#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主板/创业板 tab 卖价多源编排（近端窗口）。

本机实测可达窗口（2026-10-27）：
  - sina 1m ≈ 近 9 个交易日
  - tx m5  ≈ 近 15～17 个交易日
更深历史（≈9 成缺口）暂无免费分钟源；AlphaFeed 已排除；mootdx 本机行情空。

分轨：sina / tx，日期互斥，轨内单线程；tencent 与买价 qq 共用门禁。
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


os.environ.setdefault("DISABLE_BAOSTOCK", "1")

from platform_gate import is_stopped, quota_status  # noqa: E402
from stock_UI import _list_day_files, _load_day, _safe_float  # noqa: E402

LOCK_FILE = os.path.join(BASE_DIR, "data", "board_sell_night.lock")
LOG_DIR = os.path.join(BASE_DIR, "data", "board_price_logs")
PYTHON = "/usr/local/bin/python3"
os.makedirs(LOG_DIR, exist_ok=True)

# 保守：只扫近 20 个自然日买入记录（卖日出窗约在近 17 交易日内）
DEFAULT_LOOKBACK_DAYS = 28


def log(msg: str) -> None:
    line = f"[{datetime.now():%F %T}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _quiet() -> str | None:
    from backfill_window import quiet_reason  # noqa: WPS433
    return quiet_reason()


def _clamp_until(until: str, src: str) -> str:
    from backfill_window import default_until  # noqa: WPS433

    _ = src
    plat = default_until()
    g = datetime.strptime(until, "%Y-%m-%d %H:%M")
    p = datetime.strptime(plat, "%Y-%m-%d %H:%M")
    return min(g, p).strftime("%Y-%m-%d %H:%M")


def _alive_lanes(lanes: list[dict], *, force: bool) -> list[dict]:
    from backfill_window import filter_lanes_by_reserve  # noqa: WPS433

    alive = [ln for ln in lanes if not is_stopped(ln["src"])]
    if force:
        return alive
    return filter_lanes_by_reserve(alive)


def _sell_still_due(trade: dict, day: str) -> bool:
    """卖出日还没到的，不算待补，避免卡住更早的月份。"""
    hint = str(trade.get("sell_date") or "").strip()[:10]
    if not hint:
        import price_service as ps
        hint = ps.next_trading_day(day) or ""
    today = datetime.now().strftime("%Y-%m-%d")
    if hint and hint > today:
        return False
    if "尚未开盘" in str(trade.get("skip_reason") or ""):
        return False
    return (_safe_float(trade.get("sell_price_935")) is None
            or _safe_float(trade.get("sell_price_1315")) is None)


def _sell_work(lookback_days: int) -> list[tuple[str, str, str]]:
    from board_phase import FLOOR, trade_board

    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    floor = cutoff if cutoff > FLOOR else FLOOR
    items: list[tuple[str, str, str]] = []
    for fn in _list_day_files("stock"):
        day = fn[:-5]
        if day < floor:
            continue
        trades = _load_day(day, "stock")
        need_boards: set[str] = set()
        for t in trades:
            if t.get("board_rank") is None or t.get("is_board_placeholder"):
                continue
            if "买不进" in str(t.get("skip_reason") or ""):
                continue
            if not str(t.get("stock_code") or "").strip():
                continue
            if _sell_still_due(t, day):
                need_boards.add(trade_board(t))
        for board in need_boards:
            items.append((day[:7], board, day))
    return items


def list_todo_days(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[str]:
    from board_phase import phase_days
    _m, _b, days = phase_days(_sell_work(lookback_days))
    return days


def current_phase(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    from board_phase import phase_days
    month, board, _days = phase_days(_sell_work(lookback_days))
    return month, board


def assign(todo: list[str], lanes: list[dict], per_lane: int) -> dict[str, list[str]]:
    out = {ln["id"]: [] for ln in lanes}
    idx = 0
    for d in todo:
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
    ap.add_argument("--per-lane", type=int, default=2, help="每轨每轮天数（保守）")
    ap.add_argument("--rounds", type=int, default=60)
    ap.add_argument("--until", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = ap.parse_args()

    lanes = [
        {"id": "sina", "src": "sina"},
        {"id": "tx", "src": "tx"},
    ]
    todo = list_todo_days(args.lookback_days)
    if args.status:
        print(f"近端待补卖价日期 {len(todo)} | 分轨 {[ln['id'] for ln in lanes]} | 门禁=sina∥tencent")
        print("最近:", ", ".join(todo[:12]), ("…" if len(todo) > 12 else ""))
        qs = quota_status()
        for p, st in qs.items():
            print(
                f"  quota[{p}] night {st['night_used']}/{st['night_limit']} "
                f"hour {st['hour_used']}/{st['hour_limit']} "
                f"interval={st['min_interval_sec']}s stopped={st['stopped']}"
            )
        return

    if _quiet() and not args.force:
        log(f"拒绝启动：{_quiet()}")
        return

    until = args.until
    if not until:
        from backfill_window import default_until  # noqa: WPS433
        until = default_until()
    elif len(until) <= 5:
        hh, mm = map(int, until.split(":"))
        target = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= datetime.now():
            target += timedelta(days=1)
        until = target.strftime("%Y-%m-%d %H:%M")

    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        log("另一卖价编排器运行中，跳过")
        return

    phase_m, phase_b = current_phase(args.lookback_days)
    log(f"=== sell night start lanes={[ln['id'] for ln in lanes]} "
        f"phase={phase_m} {phase_b} todo_days={len(todo)} "
        f"lookback={args.lookback_days} until={until} ===")
    try:
        for r in range(1, max(1, args.rounds) + 1):
            if _quiet() and not args.force:
                log("进入安静窗，停止")
                break
            if datetime.now() >= datetime.strptime(until, "%Y-%m-%d %H:%M"):
                log(f"硬停 {until}")
                break
            todo = list_todo_days(args.lookback_days)
            phase_m, phase_b = current_phase(args.lookback_days)
            if not todo:
                log("近端卖价待补为空，收工")
                break
            log(f"round{r} phase={phase_m} {phase_b} days={todo[:6]}")
            alive = _alive_lanes(lanes, force=args.force)
            if not alive:
                log("卖价分轨全部不可用（配额或早盘预留），本轮收工")
                break
            asg = assign(todo, alive, args.per_lane)
            procs = []
            lane_stops: list[datetime] = []
            for ln in alive:
                dates = asg.get(ln["id"]) or []
                if not dates:
                    continue
                ln_until = _clamp_until(until, ln["src"])
                ln_stop = datetime.strptime(ln_until, "%Y-%m-%d %H:%M")
                if datetime.now() >= ln_stop:
                    log(f"round{r} skip {ln['id']}: until={ln_until} already passed")
                    continue
                lane_stops.append(ln_stop)
                cmd = [
                    PYTHON, os.path.join(_HERE, "board_sell_lane_worker.py"),
                    "--lane", ln["id"], "--src", ln["src"],
                    "--dates", ",".join(dates),
                    "--until", ln_until,
                    "--board", phase_b,
                ]
                log(
                    f"round{r} spawn {ln['id']} dates={dates} "
                    f"until={ln_until} quota={quota_status(ln['src'])}"
                )
                procs.append(subprocess.Popen(cmd, cwd=_HERE))
            if not procs:
                break
            hard = min(lane_stops) if lane_stops else datetime.strptime(until, "%Y-%m-%d %H:%M")
            while procs:
                procs = [p for p in procs if p.poll() is None]
                if not procs:
                    break
                if datetime.now() >= hard:
                    for p in procs:
                        p.terminate()
                    time.sleep(2)
                    for p in procs:
                        if p.poll() is None:
                            p.kill()
                    procs = []
                    break
                time.sleep(3)
            log(f"round{r} 完成，近端剩余≈{len(list_todo_days(args.lookback_days))}")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        log("=== sell night end ===")


if __name__ == "__main__":
    main()
