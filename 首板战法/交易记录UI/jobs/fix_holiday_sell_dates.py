"""
补「卖出日落在非交易日」的历史脏数据。

问题：部分历史记录的 sell_date 指向法定节假日（当天不开盘），
      导致 09:35 / 13:15 分钟价永远取不到 → 记录长期挂在「待补全」。

修法：把 sell_date 顺延到**下一个真实交易日**（next_trading_day），
      再按正常铁律取两批分钟价（09:35 / 13:15，独立顺延）。

铁律：
  - 单条串行、每条间隔（禁并发/批量，防免费数据源限流）；
  - 卖出价必须是真实分钟价，禁止开盘/收盘冒充；
  - 只改价格相关字段，其它字段不动。

用法：
  python3 fix_holiday_sell_dates.py --dry-run
  python3 fix_holiday_sell_dates.py --all
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
)

PROGRAMS = paths.PROGRAMS
DEFAULT_SLEEP = 1.2
_SELL_KEYS = ("sell_price_935", "sell_price_1315",
              "sell_price_half_935", "sell_price_half_1315")


def _trade_day_set() -> set:
    cal = ps.get_trade_calendar()
    if cal is None or "trade_date" not in cal.columns:
        return set()
    return {str(x)[:10] for x in cal["trade_date"].tolist()}


def _has_missing_price(t: dict) -> bool:
    return any(_safe_float(t.get(k)) is None for k in _SELL_KEYS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", choices=PROGRAMS, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    args = ap.parse_args()

    progs = list(PROGRAMS) if args.all else ([args.program] if args.program else ["stock"])
    tdays = _trade_day_set()
    if not tdays:
        print("交易日历不可用，退出")
        return 1

    total_fixed = 0
    total_skip = 0
    for prog in progs:
        print(f"\n===== {prog} =====")
        for fname in _list_day_files(prog):
            day = fname[:-5]
            trades = _load_day(day, prog)
            changed = False
            for i, t in enumerate(trades):
                sd = str(t.get("sell_date") or "")
                if not sd or not _has_missing_price(t):
                    continue
                if "买不进" in str(t.get("skip_reason") or ""):
                    continue
                if sd in tdays:
                    continue  # 卖出日本身是交易日 → 不是本脚本要修的问题

                nxt = ps.next_trading_day(sd)
                if not nxt:
                    total_skip += 1
                    print(f"  SKIP  {prog} {day} {t.get('stock_code')} {t.get('stock_name')} "
                          f"sell_date={sd} 找不到下一个交易日")
                    continue

                code = str(t.get("stock_code") or "").zfill(6)
                time.sleep(args.sleep)   # 单条串行 + 间隔
                try:
                    resolved = ps.resolve_sell_day_prices(code, nxt)
                except Exception as e:
                    total_skip += 1
                    print(f"  FAIL  {prog} {day} {code} {t.get('stock_name')}: {e}")
                    continue

                p935 = resolved.get("sell_price_935")
                p1315 = resolved.get("sell_price_1315")
                if p935 is None and p1315 is None:
                    total_skip += 1
                    print(f"  HOLD  {prog} {day} {code} {t.get('stock_name')} "
                          f"sell_date={sd}→{nxt} 仍取不到分钟价 "
                          f"（{resolved.get('skip_reason')}）")
                    continue

                new_sd = resolved.get("sell_date") or nxt
                print(f"  FIX   {prog} {day} {code} {t.get('stock_name')} "
                      f"sell_date {sd}→{new_sd} "
                      f"935={_safe_float(t.get('sell_price_935'))}→{p935} "
                      f"1315={_safe_float(t.get('sell_price_1315'))}→{p1315} "
                      f"src={resolved.get('sell_price_source')}")

                t["sell_date"] = new_sd
                if p935 is not None:
                    t["sell_price_935"] = p935
                    t["sell_price_half_935"] = p935
                if p1315 is not None:
                    t["sell_price_1315"] = p1315
                    t["sell_price_half_1315"] = p1315
                t["sell_date_935"] = resolved.get("sell_date_935")
                t["sell_date_1315"] = resolved.get("sell_date_1315")
                if resolved.get("sell_price_source"):
                    t["sell_price_source"] = resolved["sell_price_source"]
                    t["sell_price_source_935"] = resolved.get("sell_price_source_935")
                    t["sell_price_source_1315"] = resolved.get("sell_price_source_1315")
                if resolved.get("skip_reason"):
                    t["skip_reason"] = resolved["skip_reason"].strip("; ")
                changed = True
                total_fixed += 1

            if changed and not args.dry_run:
                _save_day(day, trades, prog)

    print(f"\n===== 汇总 =====\n修复 {total_fixed} 笔，跳过/未解决 {total_skip} 笔")
    if args.dry_run:
        print("（dry-run，未写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
