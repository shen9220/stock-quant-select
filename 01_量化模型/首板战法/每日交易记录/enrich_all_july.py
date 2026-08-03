"""
全量补全 7 月 slow + up 交易价格与盈亏。
slow: 已有基础记录，直接补价
up: 先生成推荐 md → 回填基础记录 → 补价

用法:
  python3 enrich_all_july.py --program slow    # 只补 slow
  python3 enrich_all_july.py --program up      # 只补 up（含生成推荐）
  python3 enrich_all_july.py                   # 两个都补
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UP_DIR = os.path.dirname(BASE_DIR)
RESULTS_DIR = os.path.join(UP_DIR, "results")
TRADES_BASE = os.path.join(BASE_DIR, "trades")

sys.path.insert(0, BASE_DIR)
from stock_UI import (
    _load_day, _save_day, _data_lock,
    enrich_trade_prices, auto_calculate, VALID_PROGRAMS,
)

JULY_D0_DATES = [
    "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06",
    "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
    "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
    "2026-07-17", "2026-07-20", "2026-07-21", "2026-07-22",
    "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28",
    "2026-07-29", "2026-07-30",
]


def _next_trading_day(date_str: str) -> str | None:
    try:
        import price_service as ps
        return ps.next_trading_day(date_str)
    except Exception:
        pass
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt += timedelta(days=1)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def enrich_program_trades(program: str):
    """补全某个程序的所有 7 月交易价格。"""
    trades_dir = os.path.join(TRADES_BASE, program)
    if not os.path.isdir(trades_dir):
        print(f"  {program}: 无数据目录，跳过")
        return

    files = sorted(f for f in os.listdir(trades_dir) if f.endswith(".json") and f.startswith("2026-07"))
    if not files:
        print(f"  {program}: 无 7 月文件")
        return

    total = 0
    enriched = 0
    for fname in files:
        day = fname[:-5]
        trades = _load_day(day, program)
        if not trades:
            continue

        changed = False
        for i, t in enumerate(trades):
            total += 1
            # 跳过已完全补全的
            has_buy = t.get("buy_price") not in (None, "")
            has_sell = t.get("sell_price_935") not in (None, "") or t.get("sell_price_1315") not in (None, "")
            has_skip = t.get("skip_reason") and "买不进" in str(t.get("skip_reason", ""))

            if has_buy and (has_sell or has_skip):
                continue  # 已完整，跳过

            try:
                before = json.dumps(t, sort_keys=True, default=str)
                enriched_t = enrich_trade_prices(dict(t), force=False)
                enriched_t.pop("skip_buy", None)
                enriched_t.setdefault("program", program)
                after = json.dumps(enriched_t, sort_keys=True, default=str)

                if before != after:
                    trades[i] = enriched_t
                    changed = True
                    enriched += 1
                    pnl = enriched_t.get("pnl_batch_total")
                    skip = enriched_t.get("skip_reason", "")
                    status = f"pnl={pnl}" if pnl is not None else (f"skip={skip[:30]}" if skip else "空")
                    print(f"    #{t.get('id')} {t.get('stock_code')} {t.get('stock_name')} → {status}")
                else:
                    # 已是完整状态（可能之前误判）
                    pass
            except Exception as e:
                print(f"    ❌ #{t.get('id')} {t.get('stock_code')} 补价失败: {e}")

        if changed:
            _save_day(day, trades, program)
        print(f"  {day}: {len(trades)}条, 本次更新 {sum(1 for _ in [1] if changed)} 条")

    print(f"\n  {program} 总计: {total}条, 本次补全 {enriched}条")


def generate_up_recommendations():
    """检查 up 推荐文件是否存在，缺失的生成。"""
    missing_dates = []
    for d0 in JULY_D0_DATES:
        d8 = d0.replace("-", "")
        md_path = os.path.join(RESULTS_DIR, f"{d8}_up_recommendations.md")
        if not os.path.exists(md_path):
            missing_dates.append(d0)

    if not missing_dates:
        print("  up 推荐文件已全部存在，无需生成")
        return True

    print(f"  需要生成 {len(missing_dates)} 个 up 推荐文件")
    success = 0
    for d0 in missing_dates:
        d8 = d0.replace("-", "")
        print(f"    [{d0}] 运行 up.py ...")
        try:
            result = subprocess.run(
                ["python3", os.path.join(UP_DIR, "up.py"),
                 "--date", d0,
                 "--output", os.path.join(UP_DIR, f"up_result_{d8}.json")],
                cwd=UP_DIR,
                capture_output=True,
                text=True,
                timeout=300,
            )
            md_path = os.path.join(RESULTS_DIR, f"{d8}_up_recommendations.md")
            if os.path.exists(md_path):
                print(f"      ✅ 已生成")
                success += 1
            else:
                print(f"      ⚠️ 无推荐（可能当天无符合条件的票）")
                # 检查 stderr 最后 300 字符
                if result.stderr:
                    print(f"      stderr: {result.stderr[-200:]}")
        except subprocess.TimeoutExpired:
            print(f"      ❌ 超时")
        except Exception as e:
            print(f"      ❌ 失败: {e}")

    print(f"  生成完成: {success}/{len(missing_dates)}")
    return True


def create_up_trades_from_md():
    """从 up_recommendations.md 创建基础交易记录。"""
    import re as _re
    RANK_RE = _re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")

    up_dir = os.path.join(TRADES_BASE, "up")
    os.makedirs(up_dir, exist_ok=True)

    files = sorted(f for f in os.listdir(RESULTS_DIR)
                   if f.endswith("_up_recommendations.md") and f.startswith("202607"))
    if not files:
        print("  没有 up 推荐文件")
        return

    created = 0
    next_id = 1
    for fname in files:
        d0_str = fname[:8]
        d0 = f"{d0_str[:4]}-{d0_str[4:6]}-{d0_str[6:8]}"
        d1 = _next_trading_day(d0)
        if not d1:
            continue

        target = os.path.join(up_dir, f"{d1}.json")
        if os.path.exists(target):
            continue

        md_path = os.path.join(RESULTS_DIR, fname)
        with open(md_path, encoding="utf-8") as f:
            text = f.read()
        head = text.split("=== 其他未入选", 1)[0]

        stocks = []
        for m in RANK_RE.finditer(head):
            stocks.append((m.group(1), m.group(2).strip()))
            if len(stocks) >= 2:
                break

        if not stocks:
            continue

        trades = []
        for i, (code, name) in enumerate(stocks, 1):
            trades.append({
                "id": next_id, "date": d1, "program": "up",
                "stock_code": code, "stock_name": name,
                "buy_price": None, "sell_price_935": None, "sell_price_1315": None,
                "sell_price_half_935": None, "sell_price_half_1315": None,
                "sell_price_batch_avg": None, "sell_date": None, "hold_days": None,
                "pnl_morning": None, "pnl_afternoon": None,
                "pnl_morning_half": None, "pnl_afternoon_half": None,
                "pnl_batch_morning": None, "pnl_batch_afternoon": None,
                "pnl_batch_total": None, "pnl_batch_total_half": None,
                "is_second_board": None, "rank": i, "skip_reason": None, "notes": None,
            })
            next_id += 1
            created += 1

        with open(target, "w", encoding="utf-8") as f:
            json.dump(trades, f, ensure_ascii=False, indent=2, default=str)
        print(f"    {d1}: {[t['stock_code'] + ' ' + t['stock_name'] for t in trades]}")

    print(f"  共创建 {created} 条 up 基础记录")


if __name__ == "__main__":
    do_slow = "--program" not in " ".join(sys.argv) or "--program" in " ".join(sys.argv) and "slow" in " ".join(sys.argv)
    do_up = "--program" not in " ".join(sys.argv) or "--program" in " ".join(sys.argv) and "up" in " ".join(sys.argv)
    args = [a for a in sys.argv[1:] if not a.startswith("--program")]
    do_slow = not any(a == "up" for a in args)
    do_up = not any(a == "slow" for a in args)

    print("=" * 60)
    print("7 月交易数据全量补全")
    print(f"slow: {'是' if do_slow else '否'} | up: {'是' if do_up else '否'}")
    print("=" * 60)

    if do_slow:
        print("\n>>> 补全 slow 程序交易价格...")
        enrich_program_trades("slow")

    if do_up:
        print("\n>>> 步骤1: 生成 up 推荐文件...")
        generate_up_recommendations()

        print("\n>>> 步骤2: 创建 up 基础交易记录...")
        create_up_trades_from_md()

        print("\n>>> 步骤3: 补全 up 程序交易价格...")
        enrich_program_trades("up")

    print("\n🎉 全部补全完成！")
