"""
批量运行 up.py 生成 7 月推荐文件，然后回填到交易记录。
用法:
  python3 run_up_july_batch.py              # dry-run
  python3 run_up_july_batch.py --run        # 实际运行 up.py 生成推荐
  python3 run_up_july_batch.py --backfill   # 从已有推荐文件回填交易记录
  python3 run_up_july_batch.py --run --backfill  # 先运行再回填
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UP_DIR = os.path.dirname(BASE_DIR)  # 首板战法目录
RESULTS_DIR = os.path.join(UP_DIR, "results")
TRADES_BASE = os.path.join(BASE_DIR, "trades")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 7 月所有交易日（D0=涨停日）
JULY_DATES = [
    "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06",
    "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
    "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
    "2026-07-17", "2026-07-20", "2026-07-21", "2026-07-22",
    "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28",
    "2026-07-29", "2026-07-30",
]


def _next_trading_day(date_str: str) -> str | None:
    """简易交易日推算，优先 baostock。"""
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


def run_up_for_date(date_str: str) -> bool:
    """运行 up.py 为指定日期生成推荐。"""
    date8 = date_str.replace("-", "")
    md_path = os.path.join(RESULTS_DIR, f"{date8}_up_recommendations.md")
    if os.path.exists(md_path):
        print(f"  {date_str} 推荐文件已存在，跳过")
        return True

    print(f"  运行: python3 up.py --date {date_str}")
    try:
        result = subprocess.run(
            ["python3", os.path.join(UP_DIR, "up.py"),
             "--date", date_str,
             "--output", os.path.join(UP_DIR, f"up_result_{date8}.json")],
            cwd=UP_DIR,
            capture_output=True,
            text=True,
            timeout=300,  # 5分钟超时
        )
        if result.returncode != 0:
            print(f"    up.py 返回错误: {result.stderr[-200:] if result.stderr else 'unknown'}")
            return False
        if os.path.exists(md_path):
            print(f"    ✅ 已生成 {md_path}")
            return True
        else:
            print(f"    ⚠️ up.py 运行完成但未生成 md 文件（可能当天无符合条件的股票）")
            return False
    except subprocess.TimeoutExpired:
        print(f"    ❌ 超时")
        return False
    except Exception as e:
        print(f"    ❌ 失败: {e}")
        return False


def backfill_up_from_md(dry_run: bool = False):
    """从 results/*_up_recommendations.md 回填 up 交易记录。"""
    from backfill_slow_up_july import _parse_recommendations_md

    up_dir = os.path.join(TRADES_BASE, "up")
    os.makedirs(up_dir, exist_ok=True)

    files = sorted([
        f for f in os.listdir(RESULTS_DIR)
        if f.endswith("_up_recommendations.md") and f.startswith("202607")
    ])
    print(f"找到 {len(files)} 个 up 推荐文件")

    if not files:
        print("没有 up 推荐文件，请先运行 --run")
        return

    global_id = 1
    results = []

    for fname in files:
        d0_str = fname[:8]
        d0 = f"{d0_str[:4]}-{d0_str[4:6]}-{d0_str[6:8]}"
        d1 = _next_trading_day(d0)
        if not d1:
            continue

        path = os.path.join(RESULTS_DIR, fname)
        stocks = _parse_recommendations_md(path)
        if not stocks:
            continue

        target_file = os.path.join(up_dir, f"{d1}.json")
        if os.path.exists(target_file) and not dry_run:
            print(f"  {d1} 已存在，跳过")
            continue

        trades = []
        for i, s in enumerate(stocks, 1):
            trade = {
                "id": global_id,
                "date": d1,
                "program": "up",
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "buy_price": None, "sell_price_935": None, "sell_price_1315": None,
                "sell_price_half_935": None, "sell_price_half_1315": None,
                "sell_price_batch_avg": None, "sell_date": None, "hold_days": None,
                "pnl_morning": None, "pnl_afternoon": None,
                "pnl_morning_half": None, "pnl_afternoon_half": None,
                "pnl_batch_morning": None, "pnl_batch_afternoon": None,
                "pnl_batch_total": None, "pnl_batch_total_half": None,
                "is_second_board": None, "rank": i, "skip_reason": None, "notes": None,
            }
            trades.append(trade)
            global_id += 1

        results.append({"d0": d0, "d1": d1, "trades": trades, "file": target_file})

        if not dry_run:
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(trades, f, ensure_ascii=False, indent=2, default=str)
        _stocks = [t["stock_code"] + " " + t["stock_name"] for t in trades]
        _mode = "[DRY-RUN]" if dry_run else "[WRITE]"
        print(f"  {_mode} D0={d0} → D1={d1}: {_stocks}")

    total = sum(len(r['trades']) for r in results)
    print(f"\n共 {'将' if dry_run else '已'}写入 {total} 条 up 交易记录")


if __name__ == "__main__":
    do_run = "--run" in sys.argv
    do_backfill = "--backfill" in sys.argv
    dry_run_backfill = "--dry-run" in sys.argv

    if do_run:
        print("=" * 60)
        print("批量运行 up.py 生成 7 月推荐文件")
        print("=" * 60)
        success = 0
        for d in JULY_DATES:
            print(f"\n[{d}]")
            if run_up_for_date(d):
                success += 1
        print(f"\n完成: {success}/{len(JULY_DATES)} 成功")

    if do_backfill:
        print("\n" + "=" * 60)
        print("回填 up 交易记录")
        print("=" * 60)
        backfill_up_from_md(dry_run=dry_run_backfill)

    if not do_run and not do_backfill:
        print("请指定操作：")
        print("  --run      批量运行 up.py 生成推荐")
        print("  --backfill 从推荐文件回填交易记录")
        print("  --run --backfill  两者都做")
