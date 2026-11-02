"""
回填 slow / up 程序历史交易记录（默认 2026-01 ~ 2026-06）。

流程：
  1) 逐交易日 D0 运行 slow.py / fast_gen_up 生成 recommendations.md
  2) 写入 trades/slow/ 与 trades/up/ 基础记录
  3) 可选 --enrich 补全买卖价与盈亏

用法:
  python3 backfill_slow_up_historical.py --dry-run
  python3 backfill_slow_up_historical.py --commit
  python3 backfill_slow_up_historical.py --commit --enrich
  python3 backfill_slow_up_historical.py --months 2026-01,2026-02 --program slow --commit
  python3 backfill_slow_up_historical.py --skip-generate --commit   # 仅从已有 md 回填
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
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

import paths  # noqa: E402

RANK_RE = re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")

import price_service as ps  # noqa: E402


def _program_results(program: str) -> str:
    return paths.program_results_dir(program)


def _program_trades(program: str) -> str:
    return paths.program_trades_dir(program)


def _trading_days_in_month(ym: str) -> list[str]:
    y, m = map(int, ym.split("-"))
    start = f"{y}-{m:02d}-01"
    if m == 12:
        end = f"{y + 1}-01-01"
    else:
        end = f"{y}-{m + 1:02d}-01"
    end_dt = datetime.strptime(end, "%Y-%m-%d") - timedelta(days=1)
    end_s = end_dt.strftime("%Y-%m-%d")
    cal = ps.get_trade_calendar()
    if cal is None:
        out = []
        cur = datetime.strptime(start, "%Y-%m-%d")
        end_d = datetime.strptime(end_s, "%Y-%m-%d")
        while cur <= end_d:
            if cur.weekday() < 5:
                out.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return out
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end_s, "%Y-%m-%d")
    mask = (cal["trade_date"] >= s) & (cal["trade_date"] <= e)
    return [d.strftime("%Y-%m-%d") for d in cal.loc[mask, "trade_date"]]


def _months_range(start_ym: str, end_ym: str) -> list[str]:
    sy, sm = map(int, start_ym.split("-"))
    ey, em = map(int, end_ym.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


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


def _empty_trade(program: str, tid: int, d1: str, stock: dict, rank: int) -> dict:
    return {
        "id": tid,
        "date": d1,
        "program": program,
        "stock_code": stock["stock_code"],
        "stock_name": stock["stock_name"],
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
        "rank": rank,
        "skip_reason": None,
        "notes": None,
    }


def generate_slow_md(d0: str, force: bool = False) -> bool:
    d8 = d0.replace("-", "")
    md_path = os.path.join(_program_results("slow"), f"{d8}_slow_recommendations.md")
    if os.path.exists(md_path) and not force:
        return True

    from historical_pool import em_has_zt_pool, generate_slow_via_ths

    if not em_has_zt_pool(d0):
        print(f"  [slow] 东财无 {d0} 数据，走同花顺回退 …")
        stocks = generate_slow_via_ths(d0)
        return bool(stocks)

    slow_py = paths.program_script("slow")
    print(f"  [slow] 运行 slow.py {d8} …")
    try:
        result = subprocess.run(
            ["python3", slow_py, d8],
            cwd=paths.program_dir("slow"),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "")[-300:]
            print(f"    ❌ slow.py 失败: {tail}")
            return False
        if os.path.exists(md_path):
            print(f"    ✅ {md_path}")
            return True
        print(f"    ⚠️ 无推荐（可能当天无符合条件的票）")
        return False
    except subprocess.TimeoutExpired:
        print(f"    ❌ 超时")
        return False
    except Exception as e:
        print(f"    ❌ {e}")
        return False


def generate_up_md(d0: str, force: bool = False) -> bool:
    d8 = d0.replace("-", "")
    md_path = paths.recommendation_md("up", d0)
    if os.path.exists(md_path) and not force:
        return True

    from daily_close_job import _import_up_record_sync_module
    up_mod = _import_up_record_sync_module()
    find_up_result_json = up_mod.find_up_result_json
    top2_from_json = up_mod.top2_from_json
    write_up_md_from_json = up_mod.write_up_md_from_json
    import price_service as ps

    buy = ps.next_trading_day(d0)
    if buy:
        path, data = find_up_result_json(buy)
        if data and top2_from_json(data):
            write_up_md_from_json(data, d0)
            print(f"    ✅ [up.py JSON] {d0}")
            return True

    from historical_pool import em_has_zt_pool, generate_up_via_ths

    if not em_has_zt_pool(d0):
        print(f"  [up] 东财无 {d0} 数据，走同花顺回退（仅历史回填）…")
        stocks = generate_up_via_ths(d0)
        return bool(stocks)

    from fast_gen_up_july import generate_up_for_date, save_up_md

    print(f"  [up] 生成 {d0} …")
    try:
        stocks = generate_up_for_date(d0)
        if not stocks:
            print(f"    ⚠️ 无符合条件的股票")
            return False
        save_up_md(d0, stocks)
        print(f"    ✅ Top2: {[s['stock_code'] + ' ' + s['stock_name'] for s in stocks]}")
        return True
    except Exception as e:
        print(f"    ❌ {e}")
        return False


def backfill_trades_from_md(
    program: str,
    d0_list: list[str],
    *,
    commit: bool,
    force: bool,
    next_id: int,
) -> tuple[int, int]:
    suffix = f"_{program}_recommendations.md" if program == "slow" else "_up_recommendations.md"
    prog_dir = _program_trades(program)
    if commit:
        os.makedirs(prog_dir, exist_ok=True)

    written_days = 0
    written_trades = 0

    for d0 in d0_list:
        d8 = d0.replace("-", "")
        md_path = paths.recommendation_md(program, d0)
        stocks = _parse_recommendations_md(md_path)
        if not stocks:
            continue

        d1 = ps.next_trading_day(d0)
        if not d1:
            continue

        target = os.path.join(prog_dir, f"{d1}.json")
        if os.path.exists(target) and not force:
            continue

        trades = []
        for i, s in enumerate(stocks, 1):
            trades.append(_empty_trade(program, next_id, d1, s, i))
            next_id += 1

        mode = "[WRITE]" if commit else "[DRY-RUN]"
        names = [t["stock_code"] + " " + t["stock_name"] for t in trades]
        print(f"  {mode} {program} D0={d0} → D1={d1}: {names}")

        if commit:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(trades, f, ensure_ascii=False, indent=2, default=str)

        written_days += 1
        written_trades += len(trades)

    return next_id, written_trades


def enrich_program(program: str, prefix: str = "2026-"):
    from stock_UI import _load_day, _save_day, enrich_trade_prices

    trades_dir = _program_trades(program)
    if not os.path.isdir(trades_dir):
        print(f"  {program}: 无数据目录")
        return

    files = sorted(
        f for f in os.listdir(trades_dir)
        if f.endswith(".json") and f.startswith(prefix)
    )
    if not files:
        print(f"  {program}: 无待补价文件")
        return

    total = enriched = 0
    for fname in files:
        day = fname[:-5]
        trades = _load_day(day, program)
        if not trades:
            continue
        changed = False
        for i, t in enumerate(trades):
            total += 1
            has_buy = t.get("buy_price") not in (None, "")
            has_sell = t.get("sell_price_935") not in (None, "") or t.get("sell_price_1315") not in (None, "")
            has_skip = t.get("skip_reason") and "买不进" in str(t.get("skip_reason", ""))
            if has_buy and (has_sell or has_skip):
                continue
            try:
                before = json.dumps(t, sort_keys=True, default=str)
                e = enrich_trade_prices(dict(t), force=False)
                e.pop("skip_buy", None)
                e.setdefault("program", program)
                if json.dumps(e, sort_keys=True, default=str) != before:
                    trades[i] = e
                    changed = True
                    enriched += 1
                    print(f"    {day} #{e.get('id')} {e.get('stock_code')} buy={e.get('buy_price')} pnl={e.get('pnl_batch_total')}")
            except Exception as ex:
                print(f"    ❌ {day} {t.get('stock_code')}: {ex}")
            time.sleep(0.08)
        if changed:
            _save_day(day, trades, program)

    print(f"  {program} 补价: {enriched}/{total} 条更新")


def main():
    import backfill_guard  # noqa: E402

    backfill_guard.assert_backfill_allowed()
    parser = argparse.ArgumentParser(description="回填 slow/up 历史交易记录")
    parser.add_argument("--start", default="2026-01", help="起始月 YYYY-MM")
    parser.add_argument("--end", default="2026-06", help="结束月 YYYY-MM")
    parser.add_argument("--months", default="", help="逗号分隔 YYYY-MM，覆盖 start/end")
    parser.add_argument("--program", choices=("slow", "up", "both"), default="both")
    parser.add_argument("--commit", action="store_true", help="实际写入（默认 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览")
    parser.add_argument("--enrich", action="store_true", help="写入后补全价格")
    parser.add_argument("--force", action="store_true", help="覆盖已有 md/交易文件")
    parser.add_argument("--skip-generate", action="store_true", help="跳过生成 md，仅从已有文件回填")
    args = parser.parse_args()

    commit = args.commit and not args.dry_run
    do_slow = args.program in ("slow", "both")
    do_up = args.program in ("up", "both")

    if args.months:
        months = [m.strip() for m in args.months.split(",") if m.strip()]
    else:
        months = _months_range(args.start, args.end)

    d0_list: list[str] = []
    for ym in months:
        d0_list.extend(_trading_days_in_month(ym))
    d0_list = sorted(set(d0_list))

    print("=" * 60)
    print(f"回填 slow/up 历史: {months[0]} ~ {months[-1]} ({len(d0_list)} 个交易日)")
    print(f"模式: {'写入' if commit else 'DRY-RUN'} | 补价: {'是' if args.enrich else '否'}")
    print(f"程序: slow={'是' if do_slow else '否'} up={'是' if do_up else '否'}")
    print("=" * 60)

    if not args.skip_generate:
        for d0 in d0_list:
            print(f"\n[{d0}]")
            if do_slow:
                generate_slow_md(d0, force=args.force)
                time.sleep(0.5)
            if do_up:
                generate_up_md(d0, force=args.force)
                time.sleep(0.3)

    total_trades = 0
    if do_slow:
        from stock_UI import _next_global_id  # noqa: E402

        print("\n>>> 回填 slow 交易记录 …")
        next_id, n = backfill_trades_from_md(
            "slow", d0_list, commit=commit, force=args.force, next_id=_next_global_id("slow")
        )
        total_trades += n
    if do_up:
        from stock_UI import _next_global_id  # noqa: E402

        print("\n>>> 回填 up 交易记录 …")
        next_id, n = backfill_trades_from_md(
            "up", d0_list, commit=commit, force=args.force, next_id=_next_global_id("up")
        )
        total_trades += n

    print(f"\n共 {'将' if not commit else '已'}写入 {total_trades} 条交易记录")

    if args.enrich and commit:
        print("\n>>> 补全价格 …")
        enrich_prefix = months[0][:5]  # e.g. 2025- or 2026-
        if do_slow:
            enrich_program("slow", prefix=enrich_prefix)
        if do_up:
            enrich_program("up", prefix=enrich_prefix)

    if not commit:
        print("\n这是 DRY-RUN。加 --commit 实际写入，加 --enrich 补价。")


if __name__ == "__main__":
    main()
