"""
矫正脚本：把误用「日线开盘≈9:35 / 日线收盘≈13:15」的卖出价
改回真实分钟价（09:35 / 13:15）。

铁律：早盘卖出价=9:35，午盘卖出价=13:15；禁止开盘/收盘冒充。

用法：
  python3 fix_sell_prices.py --dry-run
  python3 fix_sell_prices.py --all
  python3 fix_sell_prices.py --program stock --since 2026-10-01
  python3 fix_sell_prices.py --all --force-all-with-sell   # 凡有卖出价都按分钟重拉
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
    _list_day_files,
    _load_day,
    _save_day,
    _safe_float,
    enrich_trade_prices,
)

PROGRAMS = paths.PROGRAMS
DEFAULT_SLEEP = 0.8


def _needs_fix(t: dict, force_all: bool) -> bool:
    if "买不进" in str(t.get("skip_reason") or ""):
        return False
    sd = t.get("sell_date")
    s935 = t.get("sell_price_935")
    s1315 = t.get("sell_price_1315")
    if s935 in (None, "") and s1315 in (None, ""):
        return False
    if force_all:
        return True
    if not sd:
        return False
    code = str(t.get("stock_code") or "").zfill(6)
    return ps.sell_prices_look_like_ohlc_fallback(code, sd, s935, s1315)


def fix_program(
    program: str,
    *,
    dry_run: bool,
    sleep_s: float,
    since: str | None,
    force_all: bool,
) -> dict:
    stats = {"checked": 0, "fixed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    for fname in _list_day_files(program):
        day = fname[:-5]
        if since and day < since:
            continue
        trades = _load_day(day, program)
        changed = False
        for idx, t in enumerate(trades):
            stats["checked"] += 1
            if not _needs_fix(t, force_all):
                stats["skipped"] += 1
                continue
            code = str(t.get("stock_code") or "").zfill(6)
            old_935 = _safe_float(t.get("sell_price_935"))
            old_1315 = _safe_float(t.get("sell_price_1315"))
            time.sleep(sleep_s)
            try:
                nt = enrich_trade_prices(dict(t), force=True)
            except Exception as e:
                stats["failed"] += 1
                print(f"  FAIL {program} {day} {code}: {e}")
                continue
            new_935 = _safe_float(nt.get("sell_price_935"))
            new_1315 = _safe_float(nt.get("sell_price_1315"))
            same = (
                (old_935 is None and new_935 is None or old_935 is not None and new_935 is not None and abs(old_935 - new_935) < 0.015)
                and (
                    old_1315 is None and new_1315 is None
                    or old_1315 is not None and new_1315 is not None and abs(old_1315 - new_1315) < 0.015
                )
            )
            if same:
                stats["unchanged"] += 1
                continue
            print(
                f"  FIX {program} {day} {code} {t.get('stock_name')} "
                f"935 {old_935}->{new_935} 1315 {old_1315}->{new_1315} "
                f"sell_date={nt.get('sell_date')}"
            )
            trades[idx] = nt
            changed = True
            stats["fixed"] += 1
        if changed and not dry_run:
            _save_day(day, trades, program)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", choices=PROGRAMS, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    ap.add_argument("--since", default="2026-07-01", help="只处理买入日≥该日")
    ap.add_argument(
        "--force-all-with-sell",
        action="store_true",
        help="凡有卖出价都强制按分钟重拉（不只指纹匹配）",
    )
    args = ap.parse_args()
    progs = list(PROGRAMS) if args.all else ([args.program] if args.program else ["stock"])
    total = {"checked": 0, "fixed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    for prog in progs:
        print(f"\n===== {prog} since={args.since} dry_run={args.dry_run} =====")
        s = fix_program(
            prog,
            dry_run=args.dry_run,
            sleep_s=args.sleep,
            since=args.since,
            force_all=args.force_all_with_sell,
        )
        for k in total:
            total[k] += s[k]
        print(
            f"[{prog}] checked={s['checked']} fixed={s['fixed']} "
            f"unchanged={s['unchanged']} skipped={s['skipped']} failed={s['failed']}"
        )
    print("\n===== 汇总 =====")
    print(total)
    if args.dry_run:
        print("（dry-run，未写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
