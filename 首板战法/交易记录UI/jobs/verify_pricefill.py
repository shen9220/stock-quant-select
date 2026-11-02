"""校验补价后数据完整性：对比备份，逐字段 diff，确认只改了价格相关字段。"""
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
import json, os, sys, glob

BASE = BASE_DIR
ROOT = os.path.dirname(BASE)

PRICE_KEYS = {
    "buy_price", "sell_price_935", "sell_price_1315",
    "sell_price_half_935", "sell_price_half_1315",
    # 分批均价：由 auto_calculate() 依据两批卖价自动重算的价格派生字段
    "sell_price_batch_avg",
    "sell_date", "sell_date_935", "sell_date_1315",
    "sell_price_source", "sell_price_source_935", "sell_price_source_1315",
    "skip_reason",
    "pnl_morning", "pnl_afternoon", "pnl_morning_half", "pnl_afternoon_half",
    "pnl_batch_morning", "pnl_batch_afternoon", "pnl_batch_total",
    "pnl_batch_total_half",
    "is_second_board", "hold_days",
}

def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def main():
    bk = sys.argv[1]
    bad = 0
    checked = 0
    for prog in ("stock", "slow", "up", "stock_updata"):
        cur_dir = os.path.join(ROOT, prog, "trades")
        for cur_file in glob.glob(os.path.join(cur_dir, "*.json")):
            name = os.path.basename(cur_file)
            bk_file = os.path.join(bk, prog, name)
            if not os.path.exists(bk_file):
                continue
            checked += 1
            try:
                cur = load(cur_file); old = load(bk_file)
            except Exception as e:
                print(f"  JSON 解析失败 {prog}/{name}: {e}"); bad += 1; continue
            if len(cur) != len(old):
                print(f"  记录条数变化 {prog}/{name}: {len(old)}→{len(cur)}"); bad += 1; continue
            for i, (o, c) in enumerate(zip(old, cur)):
                # id 必须一致（不得增删/改序）
                if o.get("id") != c.get("id"):
                    print(f"  id 变化 {prog}/{name}[{i}]: {o.get('id')}→{c.get('id')}"); bad += 1
                for k in set(o) | set(c):
                    if o.get(k) != c.get(k):
                        if k not in PRICE_KEYS:
                            print(f"  ✗ 非价格字段被改 {prog}/{name}[{i}] {k}: "
                                  f"{o.get(k)!r}→{c.get(k)!r}"); bad += 1
    # 残留 tmp
    tmps = []
    for prog in ("stock", "slow", "up", "stock_updata"):
        tmps += glob.glob(os.path.join(ROOT, prog, "trades", "*.tmp"))
    print(f"\n对比文件 {checked} 个")
    print(f"非价格字段被改/结构异常: {bad} 处")
    print(f"残留 .tmp: {len(tmps)} 个")
    print("结论:", "✓ 数据完整性 OK" if bad == 0 and not tmps else "✗ 存在问题")

if __name__ == "__main__":
    main()
