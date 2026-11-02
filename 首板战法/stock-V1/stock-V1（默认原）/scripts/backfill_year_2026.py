"""
回填 2026-01 ~ 2026-07 全部交易日首板 Top2。
数据源：同花顺涨停池（首板）→ 成交额排序 Top2 → 写入买入日 → 补价。
跳过买入日开盘涨停。按月倒序：7→6→…→1。

用法:
  python3 backfill_year_2026.py
  python3 backfill_year_2026.py --months 2026-06,2026-05
  python3 backfill_year_2026.py --enrich-only
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
import sys
import time
from datetime import datetime

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import price_service as ps
from stock_UI import (
    _load_day,
    _save_day,
    enrich_trade_prices,
    purge_open_limit_buys,
    recalculate_all_trades,
    upsert_from_stock_py,
)

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.10jqka.com.cn/"}


def fetch_first_board(d0_8: str) -> list[dict]:
    url = f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&date={d0_8}"
    try:
        r = requests.get(url, timeout=20, headers=HEADERS)
        r.raise_for_status()
        info = ((r.json() or {}).get("data") or {}).get("info") or []
    except Exception as e:
        print(f"  [warn] 涨停池 {d0_8}: {e}")
        return []
    out, seen = [], set()
    for x in info:
        code = str(x.get("code") or "").zfill(6)
        name = str(x.get("name") or "").strip()
        tag = str(x.get("change_tag") or "")
        again = x.get("is_again_limit")
        if not code or code.startswith(("688", "8", "4")):
            continue
        if "ST" in name.upper():
            continue
        if again in (1, "1", True) and tag != "FIRST_LIMIT":
            continue
        if tag == "LIMIT_BACK" and again in (1, "1", True):
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name})
    return out


def rank_by_amount(stocks: list[dict], d0: str, top_n: int = 12) -> list[dict]:
    """只对前 top_n 候选拉成交额，避免全量过慢。"""
    ranked = []
    for s in stocks[:top_n]:
        amt = pct = 0.0
        try:
            if ps._bs_ensure_login():
                with ps._BS_LOCK:
                    import baostock as bs
                    rs = bs.query_history_k_data_plus(
                        ps.to_bs_code(s["code"]),
                        "date,amount,pctChg",
                        start_date=d0,
                        end_date=d0,
                        frequency="d",
                        adjustflag="3",
                    )
                    rows = []
                    while rs.error_code == "0" and rs.next():
                        rows.append(rs.get_row_data())
                if rows:
                    amt = float(rows[0][1] or 0)
                    pct = float(rows[0][2] or 0)
        except Exception:
            pass
        ranked.append({**s, "amount": amt, "pct": pct})
    ranked.sort(key=lambda x: x["amount"], reverse=True)
    return ranked


def trading_days_in_month(ym: str) -> list[str]:
    """ym=YYYY-MM → 该月交易日列表。"""
    y, m = map(int, ym.split("-"))
    if m == 12:
        end = f"{y+1}-01-01"
    else:
        end = f"{y}-{m+1:02d}-01"
    start = f"{y}-{m:02d}-01"
    # 取到月末
    from datetime import timedelta
    end_dt = datetime.strptime(end, "%Y-%m-%d") - timedelta(days=1)
    end_s = end_dt.strftime("%Y-%m-%d")
    cal = ps.get_trade_calendar()
    if cal is None:
        return []
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end_s, "%Y-%m-%d")
    mask = (cal["trade_date"] >= s) & (cal["trade_date"] <= e)
    return [d.strftime("%Y-%m-%d") for d in cal.loc[mask, "trade_date"]]


def backfill_month(ym: str) -> int:
    days = trading_days_in_month(ym)
    print(f"\n######## {ym} 共 {len(days)} 个交易日 ########")
    records = []
    for d0 in days:
        buy = ps.next_trading_day(d0)
        if not buy:
            continue
        # 买入日超出 2026-07-28 仍写入基础，补价可能为空
        first = fetch_first_board(d0.replace("-", ""))
        if not first:
            print(f"  [D0 {d0}] 无首板")
            time.sleep(0.2)
            continue
        top2 = rank_by_amount(first, d0)[:2]
        print(f"  [D0 {d0}→D1 {buy}] " + ", ".join(f"{h['code']}{h['name']}" for h in top2))
        for i, h in enumerate(top2, 1):
            records.append({
                "date": buy,
                "stock_code": h["code"],
                "stock_name": h["name"],
                "rank": i,
            })
        time.sleep(0.25)

    if not records:
        return 0
    # 去重
    seen, uniq = set(), []
    for r in records:
        k = (r["date"], r["stock_code"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    result = upsert_from_stock_py(uniq, auto_enrich=False)
    print(f"  写入 created={result['created']} updated={result['updated']}")

    # 补价该月涉及的买入日
    days_buy = sorted({r["date"] for r in uniq})
    for day in days_buy:
        trades = _load_day(day)
        keep = []
        for t in trades:
            enriched = enrich_trade_prices(t, force=False)
            if enriched.get("skip_buy"):
                print(f"  删除开盘涨停 {day} {t.get('stock_code')} {t.get('stock_name')}")
                continue
            keep.append(enriched)
            time.sleep(0.08)
        _save_day(day, keep)
    return len(uniq)


def enrich_all():
    from stock_UI import _list_day_files, _load_all_trades
    trades = _load_all_trades()
    print(f"全量补空价 {len(trades)} 条…")
    by_day = {}
    for t in trades:
        by_day.setdefault(t.get("date"), []).append(t)
    for day in sorted(by_day.keys()):
        if not day:
            continue
        loaded = _load_day(day)
        keep = []
        for t in loaded:
            e = enrich_trade_prices(t, force=False)
            if e.get("skip_buy"):
                print(f"  删 {day} {t.get('stock_code')}")
                continue
            keep.append(e)
            time.sleep(0.05)
        _save_day(day, keep)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", default="", help="逗号分隔 YYYY-MM，默认 7→1 倒序")
    parser.add_argument("--enrich-only", action="store_true")
    args = parser.parse_args()

    if args.enrich_only:
        enrich_all()
        print("purge", len(purge_open_limit_buys()))
        print("recalc", recalculate_all_trades())
        return

    if args.months:
        months = [m.strip() for m in args.months.split(",") if m.strip()]
    else:
        months = [f"2026-{m:02d}" for m in range(7, 0, -1)]

    total = 0
    for ym in months:
        total += backfill_month(ym)

    print("\n清理开盘涨停…", len(purge_open_limit_buys()))
    print("重算盈亏…", recalculate_all_trades())
    print(f"完成，本轮写入约 {total} 条候选")


if __name__ == "__main__":
    main()
