"""
回填 2026年7月 slow / up 程序交易记录。
用法:
  python3 backfill_slow_up_july.py              # 仅 dry-run，打印将要写入的内容
  python3 backfill_slow_up_july.py --commit     # 实际写入 trades/slow/ 和 trades/up/
  python3 backfill_slow_up_july.py --commit --enrich  # 写入后再补全价格（慢，需网络）
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(os.path.dirname(BASE_DIR), "results")
TRADES_BASE = os.path.join(BASE_DIR, "trades")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RANK_RE = re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")

# 将当前目录加入 sys.path 方便导入
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def _parse_recommendations_md(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    head = text.split("=== 其他未入选", 1)[0]
    stocks = []
    for m in RANK_RE.finditer(head):
        stocks.append({"stock_code": m.group(1), "stock_name": m.group(2).strip()})
        if len(stocks) >= 2:
            break
    return stocks


def next_trading_day(date_str: str) -> str | None:
    """简易交易日推算，优先 baostock 日历。"""
    try:
        import price_service as ps
        return ps.next_trading_day(date_str)
    except Exception:
        pass
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt += timedelta(days=1)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def backfill_slow(dry_run: bool = True, enrich: bool = False) -> list[dict]:
    """从 results/*_slow_recommendations.md 回填 slow 交易记录。"""
    print("=" * 60)
    print("回填 slow 程序 (低价一进二) 7月交易记录")
    print("=" * 60)

    files = sorted([
        f for f in os.listdir(RESULTS_DIR)
        if f.endswith("_slow_recommendations.md") and f.startswith("202607")
    ])
    print(f"找到 {len(files)} 个 slow 推荐文件")

    slow_dir = os.path.join(TRADES_BASE, "slow")
    os.makedirs(slow_dir, exist_ok=True)
    results = []
    global_id = 1

    for fname in files:
        d0_str = fname[:8]  # 20260716
        d0 = f"{d0_str[:4]}-{d0_str[4:6]}-{d0_str[6:8]}"
        d1 = next_trading_day(d0)
        if not d1:
            print(f"  {d0} 无法计算买入日，跳过")
            continue

        path = os.path.join(RESULTS_DIR, fname)
        stocks = _parse_recommendations_md(path)
        if not stocks:
            print(f"  {d0} 解析为空，跳过")
            continue

        # 检查是否已存在
        target_file = os.path.join(slow_dir, f"{d1}.json")
        if os.path.exists(target_file):
            print(f"  {d1} 已存在，跳过（D0={d0}, {len(stocks)}只）")
            continue

        trades = []
        for i, s in enumerate(stocks, 1):
            trade = {
                "id": global_id,
                "date": d1,
                "program": "slow",
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "buy_price": None,
                "sell_price_935": None,
                "sell_price_1315": None,
                "sell_price_half_935": None,
                "sell_price_half_1315": None,
                "sell_price_batch_avg": None,
                "sell_date": None,
                "hold_days": None,
                "pnl_morning": None,
                "pnl_afternoon": None,
                "pnl_morning_half": None,
                "pnl_afternoon_half": None,
                "pnl_batch_morning": None,
                "pnl_batch_afternoon": None,
                "pnl_batch_total": None,
                "pnl_batch_total_half": None,
                "is_second_board": None,
                "rank": i,
                "skip_reason": None,
                "notes": None,
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

    print(f"\n共 {'将' if dry_run else '已'}写入 {sum(len(r['trades']) for r in results)} 条 slow 交易记录 ({len(results)} 个买入日)")

    if enrich and not dry_run:
        print("\n补全价格...")
        from stock_UI import _load_day, _save_day, enrich_trade_prices, _data_lock
        for r in results:
            trades = _load_day(r["d1"], "slow")
            changed = False
            for i, t in enumerate(trades):
                enriched = enrich_trade_prices(dict(t), force=False)
                enriched.setdefault("program", "slow")
                if enriched != t:
                    trades[i] = enriched
                    changed = True
                    print(f"    #{enriched.get('id')} {enriched.get('stock_code')} buy={enriched.get('buy_price')} sell={enriched.get('sell_price_935')}")
            if changed:
                _save_day(r["d1"], trades, "slow")

    return results


def backfill_up(dry_run: bool = True, enrich: bool = False) -> list[dict]:
    """
    回填 up 程序交易记录。
    尝试从 results/*_up_recommendations.md 或 运行 up.py 生成。
    如果都没有，创建空记录供后续补全。
    """
    print("\n" + "=" * 60)
    print("回填 up 程序 (强度首板) 7月交易记录")
    print("=" * 60)

    up_dir = os.path.join(TRADES_BASE, "up")
    os.makedirs(up_dir, exist_ok=True)

    # 尝试找已有的 up 推荐文件
    up_files = sorted([
        f for f in os.listdir(RESULTS_DIR)
        if f.endswith("_up_recommendations.md") and f.startswith("202607")
    ]) if os.path.isdir(RESULTS_DIR) else []

    if not up_files:
        print("未找到 up_recommendations.md 文件")
        print("请运行 up.py 生成推荐: cd .. && python3 up.py --date YYYY-MM-DD")
        # 尝试从 strength_limit_up_result.json 读取（仅单日快照）
        json_path = os.path.join(os.path.dirname(BASE_DIR), "strength_limit_up_result.json")
        if os.path.exists(json_path):
            try:
                import json as _json
                with open(json_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                top = data.get("top_stocks", [])
                if top:
                    print(f"\n从 strength_limit_up_result.json 找到 {len(top)} 只（日期={data.get('analysis_date')}）")
                    print("但这仅是单日快照，建议运行 up.py 批量生成 7 月推荐。")
            except Exception:
                pass
        print("\n提示：可先运行以下命令生成 up 每日推荐文件：")
        print("  cd ../ && for d in 20260706 20260707 20260708 20260709 20260710 20260713 20260714 20260715 20260716 20260717 20260720 20260721 20260722 20260723 20260724 20260727 20260728 20260729 20260730; do python3 up.py --date $(echo $d | sed 's/..../&-/;s/..$/&-/') --output results/${d}_up_recommendations.md; done")
        return []

    print(f"找到 {len(up_files)} 个 up 推荐文件")
    results = []
    global_id = 1

    for fname in up_files:
        d0_str = fname[:8]
        d0 = f"{d0_str[:4]}-{d0_str[4:6]}-{d0_str[6:8]}"
        d1 = next_trading_day(d0)
        if not d1:
            continue

        path = os.path.join(RESULTS_DIR, fname)
        stocks = _parse_recommendations_md(path)
        if not stocks:
            continue

        target_file = os.path.join(up_dir, f"{d1}.json")
        if os.path.exists(target_file):
            print(f"  {d1} 已存在，跳过（D0={d0}）")
            continue

        trades = []
        for i, s in enumerate(stocks, 1):
            trade = {
                "id": global_id,
                "date": d1,
                "program": "up",
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "buy_price": None,
                "sell_price_935": None,
                "sell_price_1315": None,
                "sell_price_half_935": None,
                "sell_price_half_1315": None,
                "sell_price_batch_avg": None,
                "sell_date": None,
                "hold_days": None,
                "pnl_morning": None,
                "pnl_afternoon": None,
                "pnl_morning_half": None,
                "pnl_afternoon_half": None,
                "pnl_batch_morning": None,
                "pnl_batch_afternoon": None,
                "pnl_batch_total": None,
                "pnl_batch_total_half": None,
                "is_second_board": None,
                "rank": i,
                "skip_reason": None,
                "notes": None,
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

    print(f"\n共 {'将' if dry_run else '已'}写入 {sum(len(r['trades']) for r in results)} 条 up 交易记录")
    return results


if __name__ == "__main__":
    commit = "--commit" in sys.argv
    enrich = "--enrich" in sys.argv

    print(f"模式: {'实际写入' if commit else 'DRY-RUN（预览，不写入）'}")
    print(f"补价: {'是' if enrich else '否'}")
    print()

    slow_results = backfill_slow(dry_run=not commit, enrich=enrich)
    up_results = backfill_up(dry_run=not commit, enrich=enrich)

    total_slow = sum(len(r['trades']) for r in slow_results)
    total_up = sum(len(r['trades']) for r in up_results)
    print(f"\n{'=' * 60}")
    print(f"汇总: slow {total_slow} 条 + up {total_up} 条 = {total_slow + total_up} 条")
    if not commit:
        print("这是 DRY-RUN 模式。使用 --commit 参数实际写入。")
        print("写入后可在 stock_UI.py 中通过「补全空值」按钮拉取价格。")
