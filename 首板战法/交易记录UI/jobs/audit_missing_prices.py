"""
历史价格缺失全面排查（只读，不写盘）。

输出：
  1. 每程序总记录数 / 缺卖价数 / 缺买入价数 / 仍在持仓数
  2. 按买入月分组的缺失明细
  3. 逐条列出需要补的记录（含判定原因）

铁律：本脚本只读，绝不写盘、绝不并发抓数据。
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

import json
import os
import sys
from collections import defaultdict

BASE = BASE_DIR
ROOT = os.path.dirname(BASE)
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)

import paths  # noqa: E402
from stock_UI import _list_day_files, _load_day, _safe_float  # noqa: E402

PROGRAMS = paths.PROGRAMS


def analyze(program: str):
    rows = []
    for fname in _list_day_files(program):
        day = fname[:-5]
        try:
            trades = _load_day(day, program)
        except Exception as e:
            print(f"  [读取失败] {program} {day}: {e}")
            continue
        for t in trades:
            rows.append((day, t))
    return rows


def main():
    grand_need = []
    for prog in PROGRAMS:
        rows = analyze(prog)
        if not rows:
            print(f"\n===== {prog}: 无数据 =====")
            continue

        need = []          # 需要补的
        no_code = []       # 无代码，补不了
        skip = []          # 买不进等，主动跳过
        done = []          # 已完成

        for day, t in rows:
            code = str(t.get("stock_code") or "").strip()
            s935 = _safe_float(t.get("sell_price_935"))
            s1315 = _safe_float(t.get("sell_price_1315"))
            bp = _safe_float(t.get("buy_price"))
            reason = str(t.get("skip_reason") or "")

            if "买不进" in reason:
                skip.append((day, t, "买不进"))
                continue
            if not code:
                no_code.append((day, t, "无股票代码"))
                continue

            # 判定：缺卖价（任一缺）→ 需补；也缺买入价 → 需补
            miss_sell = s935 is None or s1315 is None
            miss_buy = bp is None
            if miss_sell or miss_buy:
                why = []
                if miss_buy:
                    why.append("缺买入价")
                if s935 is None:
                    why.append("缺早盘卖价")
                if s1315 is None:
                    why.append("缺午盘卖价")
                need.append((day, t, "、".join(why), s935, s1315, bp))
            else:
                done.append((day, t))

        print(f"\n===== {prog} =====")
        print(f"总记录 {len(rows)} | 已完整 {len(done)} | 待补 {len(need)} | "
              f"无代码 {len(no_code)} | 买不进跳过 {len(skip)}")

        # 按月分组
        bymonth = defaultdict(list)
        for day, t, why, s935, s1315, bp in need:
            bymonth[day[:7]].append((day, t, why))
        if bymonth:
            print("  按买入月分布：")
            for m in sorted(bymonth):
                print(f"    {m}: {len(bymonth[m])} 笔待补")

        # 明细（前 200 条）
        print("  明细：")
        for day, t, why, s935, s1315, bp in need:
            print(f"    {day} {str(t.get('stock_code')):>8} {str(t.get('stock_name')):<10} "
                  f"{why:<28} buy={bp} 935={s935} 1315={s1315} sell_date={t.get('sell_date')}")
            grand_need.append((prog, day, t, why))

        if no_code:
            print("  无代码（无法自动补）：")
            for day, t, why in no_code[:50]:
                print(f"    {day} {str(t.get('stock_name')):<12} {why}")
        if skip:
            print(f"  买不进跳过 {len(skip)} 笔（不补）")

    print(f"\n===== 全局待补合计：{len(grand_need)} 笔 =====")
    print("按程序：")
    cnt = defaultdict(int)
    for prog, *_ in grand_need:
        cnt[prog] += 1
    for p, c in sorted(cnt.items()):
        print(f"  {p}: {c}")


if __name__ == "__main__":
    main()
