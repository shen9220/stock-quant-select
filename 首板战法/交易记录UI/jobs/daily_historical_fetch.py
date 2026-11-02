#!/usr/bin/env python3
"""
每日限量历史回填 — 近 3 年数据缓慢补全。

原则：单进程、低频率、可断点、不触 baostock login。
详见：A股历史数据免费获取方案：操作指南与风险规避.md

用法:
  python3 daily_historical_fetch.py --status
  python3 daily_historical_fetch.py --program slow --days 1
  python3 daily_historical_fetch.py --program stock --days 1 --mode ths-only
  python3 daily_historical_fetch.py --program all --days 1 --ignore-pause
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

import argparse
import json
import os
import sys
import time
from datetime import datetime

for p in (ROOT_DIR, BASE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import paths  # noqa: E402
import fetch_calendar as fc  # noqa: E402

# 铁纪律：默认禁用 baostock
os.environ.setdefault("DISABLE_BAOSTOCK", "1")

STATE_FILE = os.path.join(BASE_DIR, "data", "historical_fetch_state.json")
LOG_DIR = os.path.join(BASE_DIR, "data", "historical_fetch_logs")

# 每日硬上限（防封号）
DEFAULT_LIMITS = {
    "max_trading_days_per_run": 1,
    "max_ths_calls": 5,
    "max_kline_calls": 120,
    "max_enrich_trades": 12,
    "ths_interval_sec": 1.2,
    "kline_interval_sec": 0.45,
    "enrich_interval_sec": 0.35,
}

DEFAULT_RANGE = {"start": "2023-01-01", "end": "2025-12-31"}


class Quota:
    def __init__(self, limits: dict):
        self.limits = limits
        self.ths = 0
        self.kline = 0
        self.enrich = 0
        self.days = 0

    def ok_ths(self) -> bool:
        return self.ths < self.limits["max_ths_calls"]

    def ok_kline(self) -> bool:
        return self.kline < self.limits["max_kline_calls"]

    def ok_enrich(self) -> bool:
        return self.enrich < self.limits["max_enrich_trades"]

    def ok_days(self) -> bool:
        return self.days < self.limits["max_trading_days_per_run"]


def _load_state() -> dict:
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "range": dict(DEFAULT_RANGE),
        "programs": {
            "stock": {"cursor_d0": DEFAULT_RANGE["start"], "completed": []},
            "slow": {"cursor_d0": DEFAULT_RANGE["start"], "completed": []},
            "up": {"cursor_d0": DEFAULT_RANGE["start"], "completed": []},
        },
        "last_run": None,
        "stats": {"total_days": 0, "total_api_calls": 0},
    }


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _log(msg: str) -> None:
    line = f"[{datetime.now():%F %T}] {msg}"
    print(line)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "fetch.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _trade_exists(program: str, d0: str) -> bool:
    """D0 选股日是否已有买入日记录。"""
    d1 = fc.next_trading_day(d0)
    if not d1:
        return False
    path = os.path.join(paths.program_trades_dir(program), f"{d1}.json")
    return os.path.isfile(path)


def _md_exists(program: str, d0: str) -> bool:
    return os.path.isfile(paths.recommendation_md(program, d0))


def _pending_d0_list(state: dict, program: str) -> list[str]:
    prog = state["programs"].setdefault(program, {"cursor_d0": state["range"]["start"], "completed": []})
    start, end = state["range"]["start"], state["range"]["end"]
    all_days = fc.load_calendar(start, end)
    cursor = prog.get("cursor_d0") or start
    completed = set(prog.get("completed") or [])
    out = []
    for d in all_days:
        if d < cursor or d in completed:
            continue
        if _trade_exists(program, d):
            completed.add(d)
            continue
        out.append(d)
    if completed - set(prog.get("completed") or []):
        prog["completed"] = sorted(completed)
    return out


def _mark_done(state: dict, program: str, d0: str) -> None:
    prog = state["programs"][program]
    done = prog.setdefault("completed", [])
    if d0 not in done:
        done.append(d0)
    prog["cursor_d0"] = d0


def _fetch_ths_count(d0: str) -> int:
    import requests

    d8 = d0.replace("-", "")
    url = f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&date={d8}"
    r = requests.get(
        url, timeout=20,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.10jqka.com.cn/"},
    )
    info = ((r.json() or {}).get("data") or {}).get("info") or []
    return len(info)


def process_slow_or_up(program: str, d0: str, quota: Quota, limits: dict) -> bool:
    from backfill_slow_up_historical import (
        backfill_trades_from_md,
        generate_slow_md,
        generate_up_md,
    )
    from stock_UI import _next_global_id, _load_day, _save_day, enrich_trade_prices

    gen = generate_slow_md if program == "slow" else generate_up_md

    if not _md_exists(program, d0):
        if not quota.ok_ths():
            _log(f"  [{program}] {d0} 跳过：THS 配额用尽")
            return False
        quota.ths += 1
        time.sleep(limits["ths_interval_sec"])
        ok = gen(d0, force=False)
        if not ok and not _md_exists(program, d0):
            _log(f"  [{program}] {d0} 无推荐数据，标记跳过")
            return True

    if not _trade_exists(program, d0):
        nid = _next_global_id(program)
        _, n = backfill_trades_from_md(program, [d0], commit=True, force=False, next_id=nid)
        _log(f"  [{program}] {d0} 写入 {n} 条")

    d1 = fc.next_trading_day(d0)
    if d1:
        trades = _load_day(d1, program)
        if trades:
            changed = False
            for i, t in enumerate(trades):
                if not quota.ok_enrich():
                    break
                if t.get("buy_price") not in (None, "") and (
                    t.get("sell_price_935") not in (None, "") or (t.get("skip_reason") and "买不进" in str(t.get("skip_reason")))
                ):
                    continue
                quota.enrich += 1
                quota.kline += 2
                time.sleep(limits["enrich_interval_sec"])
                e = enrich_trade_prices(dict(t), force=False)
                e.pop("skip_buy", None)
                trades[i] = e
                changed = True
            if changed:
                _save_day(d1, trades, program)
    return True


def process_stock(d0: str, quota: Quota, limits: dict, mode: str) -> bool:
    sys.path.insert(0, os.path.join(ROOT_DIR, "stock", "scripts"))
    from backfill_stock_year import (  # noqa: E402
        fetch_first_board,
        rank_by_amount,
    )
    from stock_UI import upsert_from_stock_py, _load_day, _save_day, enrich_trade_prices

    d1 = fc.next_trading_day(d0)
    if not d1:
        return True

    if _trade_exists("stock", d0):
        _log(f"  [stock] {d0} 已有记录，仅补价")
    else:
        source = "ths" if mode == "ths-only" else "auto"
        if mode == "tx-scan":
            source = "tx"
        elif mode == "auto":
            if not quota.ok_ths():
                return False
            ths_cnt = _fetch_ths_count(d0)
            quota.ths += 1
            time.sleep(limits["ths_interval_sec"])
            if ths_cnt == 0:
                _log(f"  [stock] {d0} THS 无数据，auto 模式跳过（可用 --mode tx-scan 周末补）")
                return True
            source = "ths"

        if source == "tx":
            _log(f"  [stock] {d0} 腾讯全市场扫描（workers=1，耗时长）")

        first = fetch_first_board(d0, source=source, workers=1)
        quota.kline += max(len(first), 1) * 2
        if not first:
            _log(f"  [stock] {d0} 无首板")
            return True
        top2 = rank_by_amount(first, d0)[:2]
        records = [
            {"date": d1, "stock_code": h["code"], "stock_name": h["name"], "rank": i}
            for i, h in enumerate(top2, 1)
        ]
        upsert_from_stock_py(records, auto_enrich=False, program="stock")
        _log(f"  [stock] {d0}→{d1} " + ", ".join(r["stock_code"] for r in records))

    trades = _load_day(d1, "stock")
    if trades:
        changed = False
        for i, t in enumerate(trades):
            if not quota.ok_enrich():
                break
            if t.get("buy_price") not in (None, ""):
                continue
            quota.enrich += 1
            time.sleep(limits["enrich_interval_sec"])
            e = enrich_trade_prices(dict(t), force=False)
            e.pop("skip_buy", None)
            trades[i] = e
            changed = True
        if changed:
            _save_day(d1, trades, "stock")
    return True


def print_status(state: dict) -> None:
    print("=" * 60)
    print(f"历史回填进度  {state['range']['start']} ~ {state['range']['end']}")
    print(f"上次运行: {state.get('last_run') or '从未'}")
    for prog in ("stock", "slow", "up"):
        p = state["programs"].get(prog, {})
        pending = _pending_d0_list(state, prog)
        done = len(p.get("completed") or [])
        print(f"  {prog:5s}  cursor={p.get('cursor_d0')}  已完成={done}  待处理={len(pending)}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="每日限量历史回填")
    parser.add_argument("--program", choices=("stock", "slow", "up", "all"), default="all")
    parser.add_argument("--days", type=int, default=DEFAULT_LIMITS["max_trading_days_per_run"])
    parser.add_argument("--mode", choices=("auto", "ths-only", "tx-scan"), default="auto",
                        help="auto=有THS用THS无则跳过; ths-only=仅同花顺; tx-scan=允许腾讯全扫(慢)")
    parser.add_argument("--start", default="", help="覆盖起始 D0")
    parser.add_argument("--end", default="", help="覆盖结束 D0")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--ignore-pause", action="store_true", help="忽略 BACKFILL_PAUSED（cron 专用）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.ignore_pause and not args.status:
        import backfill_guard
        if backfill_guard.backfill_paused():
            _log("BACKFILL_PAUSED 存在，退出。加 --ignore-pause 强制执行。")
            sys.exit(2)

    state = _load_state()
    if args.start:
        state["range"]["start"] = args.start
    if args.end:
        state["range"]["end"] = args.end

    if args.status:
        print_status(state)
        return

    limits = dict(DEFAULT_LIMITS)
    limits["max_trading_days_per_run"] = max(1, args.days)
    quota = Quota(limits)

    programs = ["stock", "slow", "up"] if args.program == "all" else [args.program]
    # stock 2025 完成前不跑 slow/up
    for prog in list(programs):
        if prog in ("slow", "up") and state["programs"].get(prog, {}).get("paused_until_stock_2025_done"):
            _log(f"[{prog}] 已暂停，等待 stock 2025 完成")
            programs.remove(prog)
    if not programs:
        _log("无待执行程序")
        return
    _log(f"开始 daily fetch programs={programs} mode={args.mode} days={args.days}")

    if args.dry_run:
        for prog in programs:
            pending = _pending_d0_list(state, prog)[: args.days]
            _log(f"  [dry-run] {prog} 将处理: {pending}")
        return

    processed = 0
    for prog in programs:
        if not quota.ok_days():
            break
        for d0 in _pending_d0_list(state, prog):
            if not quota.ok_days():
                break
            _log(f"[{prog}] D0={d0}")
            try:
                if prog == "stock":
                    ok = process_stock(d0, quota, limits, args.mode)
                else:
                    ok = process_slow_or_up(prog, d0, quota, limits)
            except Exception as e:
                _log(f"  ❌ {prog} {d0} 异常: {e}")
                break
            if ok:
                _mark_done(state, prog, d0)
                # 前进 cursor 到下一交易日
                nxt = fc.next_trading_day(d0)
                if nxt:
                    state["programs"][prog]["cursor_d0"] = nxt
                quota.days += 1
                processed += 1
            else:
                _log(f"  配额不足，停止 {prog}")
                break

    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["stats"]["total_days"] = state["stats"].get("total_days", 0) + processed
    state["stats"]["total_api_calls"] = state["stats"].get("total_api_calls", 0) + quota.ths + quota.kline
    _save_state(state)
    _log(f"完成 processed={processed} ths={quota.ths} kline≈{quota.kline} enrich={quota.enrich}")


if __name__ == "__main__":
    main()
