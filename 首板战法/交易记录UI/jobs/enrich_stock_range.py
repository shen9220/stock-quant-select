#!/usr/bin/env python3
"""批量补价 stock 指定月份（买价/卖价/盈亏）。"""
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
import glob
import json
import os
import sys
import time


from stock_UI import _load_day, _save_day, enrich_trade_prices  # noqa: E402


def _needs_enrich(t: dict) -> bool:
    if t.get("skip_reason") and "买不进" in str(t.get("skip_reason", "")):
        return False
    has_buy = t.get("buy_price") not in (None, "", 0)
    has_sell = t.get("sell_price_935") not in (None, "", 0) or t.get("sell_price_1315") not in (
        None,
        "",
        0,
    )
    return not (has_buy and has_sell)


def enrich_month(program: str, ym: str, *, force: bool = False, limit: int = 0) -> tuple[int, int]:
    from paths import program_trades_dir

    root = program_trades_dir(program)
    pattern = os.path.join(root, f"{ym}-*.json")
    files = sorted(glob.glob(pattern))
    total = enriched = 0
    for path in files:
        day = os.path.basename(path)[:-5]
        trades = _load_day(day, program)
        if not trades:
            continue
        changed = False
        for i, t in enumerate(trades):
            if not force and not _needs_enrich(t):
                continue
            total += 1
            if limit and enriched >= limit:
                break
            try:
                before = json.dumps(t, sort_keys=True, default=str)
                e = enrich_trade_prices(dict(t), force=force)
                e.pop("skip_buy", None)
                after = json.dumps(e, sort_keys=True, default=str)
                if before != after:
                    trades[i] = e
                    changed = True
                    enriched += 1
                    print(
                        f"  {day} #{t.get('id')} {t.get('stock_code')} "
                        f"buy={e.get('buy_price')} sell935={e.get('sell_price_935')}"
                    )
                time.sleep(0.35)
            except Exception as ex:
                print(f"  ❌ {day} {t.get('stock_code')}: {ex}")
        if changed:
            _save_day(day, trades, program)
        if limit and enriched >= limit:
            break
    return total, enriched


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--program", default="stock")
    p.add_argument("--month", default="2025-07", help="YYYY-MM")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    print(f"补价 {args.program} {args.month} …")
    total, n = enrich_month(args.program, args.month, force=args.force, limit=args.limit)
    print(f"完成：处理 {total} 条，更新 {n} 条")


if __name__ == "__main__":
    main()
