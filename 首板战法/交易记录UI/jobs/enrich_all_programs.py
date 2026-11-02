"""补全 slow / up 全部缺失的买卖价与盈亏。"""
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

import json
import os
import sys
import time

import paths  # noqa: E402

from stock_UI import _load_day, _save_day, enrich_trade_prices  # noqa: E402


def enrich_program(program: str) -> tuple[int, int]:
    trades_dir = paths.program_trades_dir(program)
    if not os.path.isdir(trades_dir):
        return 0, 0
    files = sorted(f for f in os.listdir(trades_dir) if f.endswith(".json"))
    total = enriched = 0
    for fname in files:
        day = fname[:-5]
        trades = _load_day(day, program)
        changed = False
        for i, t in enumerate(trades):
            total += 1
            has_buy = t.get("buy_price") not in (None, "")
            has_sell = t.get("sell_price_935") not in (None, "") or t.get("sell_price_1315") not in (None, "")
            has_skip = t.get("skip_reason") and "买不进" in str(t.get("skip_reason", ""))
            if has_buy and (has_sell or has_skip):
                continue
            before = json.dumps(t, sort_keys=True, default=str)
            e = enrich_trade_prices(dict(t), force=False)
            e.pop("skip_buy", None)
            e.setdefault("program", program)
            if json.dumps(e, sort_keys=True, default=str) != before:
                trades[i] = e
                changed = True
                enriched += 1
            time.sleep(0.06)
        if changed:
            _save_day(day, trades, program)
    return total, enriched


if __name__ == "__main__":
    for prog in ("slow", "up"):
        print(f"\n>>> 补全 {prog} ...")
        total, n = enrich_program(prog)
        print(f"  {prog}: {n}/{total} 条更新")
