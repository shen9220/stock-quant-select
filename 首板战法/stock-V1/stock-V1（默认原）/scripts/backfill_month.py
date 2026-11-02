"""
回填指定月份的首板战法交易记录（默认 2026-07）。

优先解析 results/*_recommendations.md（非 slow）；
缺日则用涨停池轻量打分补 Top2。
写入后批量补价。

用法:
  python3 backfill_month.py
  python3 backfill_month.py --start 2026-07-01 --end 2026-07-28
"""
from __future__ import annotations

import argparse
import os
import sys
_SCRIPT = os.path.dirname(os.path.abspath(__file__))
_PROG = os.path.dirname(_SCRIPT)
_FAMILY = os.path.dirname(_PROG)  # stock-V1
_ROOT = os.path.dirname(_FAMILY)  # 首板战法
for _p in (_ROOT, os.path.join(_ROOT, "交易记录UI"), _PROG):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import paths  # noqa: E402
import re
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(BASE_DIR)
RESULTS_DIR = paths.program_results_dir("stock")

sys.path.insert(0, BASE_DIR)
sys.path.insert(0, PARENT)

import price_service as ps  # noqa: E402
from stock_UI import (  # noqa: E402
    _load_all_trades,
    _load_day,
    _save_day,
    enrich_trade_prices,
    upsert_from_stock_py,
)

RANK_RE = re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")


def parse_recommendations_md(path: str) -> list[dict]:
    """解析 md，按出现顺序取前 2 只（即强度 Top2）。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    # 只取「其他未入选」之前
    head = text.split("=== 其他未入选", 1)[0]
    stocks = []
    for m in RANK_RE.finditer(head):
        stocks.append({"stock_code": m.group(1), "stock_name": m.group(2).strip()})
        if len(stocks) >= 2:
            break
    return stocks


def light_top2_for_d0(d0: str) -> list[dict]:
    """
    无历史 md 时：取 D0 涨停池，排除连板/科创/北交/ST，按简易得分取 Top2。
    """
    try:
        import akshare as ak
        from stock import fetch_zt_pool_em, _zt_pool_codes, get_previous_trading_day
    except Exception as e:
        print(f"  [skip] 无法加载选股依赖: {e}")
        return []

    d0_dt = datetime.strptime(d0, "%Y-%m-%d")
    d0_8 = d0.replace("-", "")
    try:
        zt = fetch_zt_pool_em(d0_8, max_attempts=3)
    except Exception as e:
        print(f"  [skip] 涨停池 {d0} 失败: {e}")
        return []
    if zt is None or zt.empty or "代码" not in zt.columns:
        print(f"  [skip] {d0} 无涨停数据")
        return []

    prev = get_previous_trading_day(d0_dt)
    prev_8 = prev.strftime("%Y%m%d")
    try:
        prev_zt = fetch_zt_pool_em(prev_8, max_attempts=3)
    except Exception:
        prev_zt = None
    prev_codes = _zt_pool_codes(prev_zt) if prev_zt is not None else set()

    df = zt[~zt["代码"].astype(str).isin(prev_codes)].copy()
    df = df[~df["代码"].astype(str).str.startswith(("688", "92"))]
    if "名称" in df.columns:
        df = df[~df["名称"].astype(str).str.contains("ST|st", case=False, na=False)]
    if df.empty:
        print(f"  [skip] {d0} 无首板候选")
        return []

    def _score(row):
        try:
            zf = float(row.get("涨跌幅") or 0) * 0.4
            cj = float(row.get("成交额") or 0) / 1e8 * 0.2
            hs = float(row.get("换手率") or 0) * 0.2
            return zf + cj + hs
        except Exception:
            return 0

    df["_score"] = df.apply(_score, axis=1)
    top = df.sort_values("_score", ascending=False).head(2)
    out = []
    for _, row in top.iterrows():
        out.append({
            "stock_code": str(row["代码"]).zfill(6),
            "stock_name": str(row.get("名称") or ""),
        })
    return out


def iter_trading_days(start: str, end: str) -> list[str]:
    cal = ps.get_trade_calendar()
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    if cal is not None:
        mask = (cal["trade_date"] >= s) & (cal["trade_date"] <= e)
        days = cal.loc[mask, "trade_date"]
        return [d.strftime("%Y-%m-%d") for d in days]
    out = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def collect_july_records(start: str, end: str) -> list[dict]:
    """D0 在 [start, end]：产出买入日 D1 的 records。"""
    records = []
    d0_list = iter_trading_days(start, end)
    for d0 in d0_list:
        d0_8 = d0.replace("-", "")
        md_path = os.path.join(RESULTS_DIR, f"{d0_8}_recommendations.md")
        stocks = []
        source = ""
        if os.path.exists(md_path):
            stocks = parse_recommendations_md(md_path)
            source = "md"
        if not stocks:
            print(f"[D0 {d0}] 无 md，尝试轻量选股…")
            stocks = light_top2_for_d0(d0)
            source = "live"
        if not stocks:
            continue

        buy_date = ps.next_trading_day(d0)
        if not buy_date:
            print(f"  [skip] {d0} 无下一交易日")
            continue
        # 买入日若超出今天+缓冲仍写入（便于补历史）
        print(f"[D0 {d0} → D1 {buy_date}] {source}: " +
              ", ".join(f"{s['stock_code']} {s['stock_name']}" for s in stocks))
        for i, s in enumerate(stocks, start=1):
            records.append({
                "date": buy_date,
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "rank": i,
            })
        time.sleep(0.3)
    return records


def enrich_all(force: bool = True):
    trades = _load_all_trades()
    print(f"\n开始补价，共 {len(trades)} 条…")
    # 按日处理，避免长锁
    by_day: dict[str, list] = {}
    for t in trades:
        by_day.setdefault(t.get("date"), []).append(t)

    done = 0
    for day, day_trades in sorted(by_day.items(), key=lambda x: x[0] or ""):
        if not day:
            continue
        loaded = _load_day(day)
        id_map = {int(t.get("id") or 0): i for i, t in enumerate(loaded)}
        changed = False
        for t in day_trades:
            tid = int(t.get("id") or 0)
            idx = id_map.get(tid)
            if idx is None:
                continue
            print(f"  补价 #{tid} {t.get('stock_code')} {t.get('stock_name')} ({day}) …")
            enriched = enrich_trade_prices(loaded[idx], force=force)
            loaded[idx] = enriched
            changed = True
            done += 1
            time.sleep(0.4)
        if changed:
            _save_day(day, loaded)
    print(f"补价完成: {done} 条")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--skip-enrich", action="store_true")
    parser.add_argument("--enrich-only", action="store_true")
    args = parser.parse_args()

    if not args.enrich_only:
        print(f"=== 回填 {args.start} ~ {args.end} ===")
        records = collect_july_records(args.start, args.end)
        if not records:
            print("未收集到任何记录")
        else:
            # 去重：同日同代码
            seen = set()
            uniq = []
            for r in records:
                k = (r["date"], r["stock_code"])
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(r)
            print(f"\n写入 {len(uniq)} 条基础记录…")
            result = upsert_from_stock_py(uniq, auto_enrich=False)
            print(f"新建 {result['created']} / 更新 {result['updated']}")

    if not args.skip_enrich:
        enrich_all(force=True)

    trades = _load_all_trades()
    july = [t for t in trades if str(t.get("date") or "").startswith("2026-07")]
    filled = sum(1 for t in july if t.get("buy_price") and (t.get("sell_price_935") or t.get("sell_price_1315")))
    print(f"\n七月记录: {len(july)} 条，已有买卖价: {filled} 条")
    print("打开 UI 刷新即可查看。")


if __name__ == "__main__":
    main()
