"""
一次性矫正脚本：把历史交易记录的买入价统一改为「D1 买入日开盘价」。

背景：
  stock/slow/up/stock_updata 的历史 buy_price 曾系统性取成 D0 涨停日的开盘价
  （而非 D1 买入日开盘价）。本脚本按 date 字段（即 D1 买入日）用
  price_service.get_open_price(code, date) 重新取开盘价覆盖，并重算盈亏。

【限速铁律】单票串行 + 请求间隔 sleep；支持断点续跑（进度写 progress 文件）。

用法：
  python3 fix_buy_price.py --program stock --dry-run      # 只看不写
  python3 fix_buy_price.py --program stock                # 只修 stock（限速+断点）
  python3 fix_buy_price.py --all                          # 修全部四个程序
  python3 fix_buy_price.py --program stock --sleep 1.5    # 自定义请求间隔
  python3 fix_buy_price.py --program stock --limit 50     # 本次最多处理 N 条
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

BASE = BASE_DIR
ROOT = os.path.dirname(BASE)
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)

import paths  # noqa: E402
import price_service as ps  # noqa: E402

from stock_UI import (  # noqa: E402
    _load_day,
    _save_day,
    _list_day_files,
    _safe_float,
    auto_calculate,
)

PROGRAMS = ("stock", "slow", "up", "stock_updata")
PROGRESS_FILE = os.path.join(BASE, "data", "fix_buy_price_progress.json")
DEFAULT_SLEEP = 1.2  # 每只票请求间隔（秒），腾讯日线接口保守限速


def _is_skip_buy(t: dict) -> bool:
    return "买不进" in str(t.get("skip_reason") or "")


def _load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _save_progress(prog: dict) -> None:
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)


def _iter_pending(program: str):
    """按日期顺序产出 (day, trade, index) 待处理项。"""
    files = _list_day_files(program)
    for fname in files:
        day = fname[:-5]
        trades = _load_day(day, program)
        for idx, t in enumerate(trades):
            yield day, t, idx


def fix_program(program: str, dry_run: bool, sleep_s: float, limit: int | None) -> dict:
    stats = {"files": 0, "fixed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    progress = _load_progress()
    prog_state = progress.get(program, {})
    done_keys = set(prog_state.get("done", []))  # "day|idx"
    seen_days = set()
    processed = 0

    # 收集当天所有改动，最后统一写盘
    day_cache: dict[str, list] = {}

    for day, t, idx in _iter_pending(program):
        seen_days.add(day)
        key = f"{day}|{idx}"
        if key in done_keys:
            continue

        code = str(t.get("stock_code") or "").zfill(6)
        if not code or not t.get("date"):
            stats["skipped"] += 1
            done_keys.add(key)
            continue
        if _is_skip_buy(t):
            stats["skipped"] += 1
            done_keys.add(key)
            continue

        old = _safe_float(t.get("buy_price"))
        # 请求前限速
        time.sleep(sleep_s)
        new_open = ps.get_open_price(code, t["date"])

        if new_open is None:
            stats["failed"] += 1
            done_keys.add(key)
            print(f"  [缺数据] {program} {day} {code} {t.get('stock_name')} 无 D1 开盘价")
            _save_progress({program: {"done": sorted(done_keys)}})
            continue

        # 已正确（当前值 == D1 开盘价）则跳过
        if old is not None and abs(old - new_open) < 0.005:
            stats["unchanged"] += 1
            done_keys.add(key)
            continue

        # 矫正（空值补入 / 错误值（如 D0 开盘价）纠正为 D1 开盘价）
        t["buy_price"] = new_open
        auto_calculate(t)
        stats["fixed"] += 1
        done_keys.add(key)
        day_cache.setdefault(day, _load_day(day, program))[idx] = t
        print(f"  [矫正] {program} {day} {code} {t.get('stock_name')}: {old} -> {new_open}")

        processed += 1
        # 每 20 条落一次进度，防止中断丢失
        if processed % 20 == 0:
            for d, lst in day_cache.items():
                if not dry_run:
                    _save_day(d, lst, program)
            day_cache.clear()
            _save_progress({program: {"done": sorted(done_keys)}})

        if limit and processed >= limit:
            print(f"  [限流] 已处理 {processed} 条，达到 --limit={limit}，本轮结束")
            break

    # 收尾：写盘剩余改动 + 进度
    for d, lst in day_cache.items():
        if not dry_run:
            _save_day(d, lst, program)
    stats["files"] = len(seen_days)
    _save_progress({program: {"done": sorted(done_keys)}})
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", choices=PROGRAMS, default=None)
    ap.add_argument("--all", action="store_true", help="修全部四个程序")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘（不限速）")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="每只票请求间隔秒数")
    ap.add_argument("--limit", type=int, default=None, help="本次最多处理 N 条（分批跑）")
    ap.add_argument("--reset", action="store_true", help="清除断点进度，从头开始")
    args = ap.parse_args()

    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("已清除断点进度")

    progs = PROGRAMS if args.all else ([args.program] if args.program else ["stock"])
    total = {"files": 0, "fixed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    for prog in progs:
        print(f"\n===== {prog} (sleep={args.sleep}s, dry_run={args.dry_run}) =====")
        s = fix_program(prog, args.dry_run, args.sleep, args.limit)
        for k in total:
            total[k] += s[k]
        print(f"[{prog}] 文件 {s['files']} | 矫正 {s['fixed']} | 未变 {s['unchanged']} | 跳过 {s['skipped']} | 缺数据 {s['failed']}")

    print("\n===== 汇总 =====")
    print(f"文件 {total['files']} | 矫正 {total['fixed']} | 未变 {total['unchanged']} | 跳过 {total['skipped']} | 缺数据 {total['failed']}")
    if args.dry_run:
        print("（dry-run，未写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
