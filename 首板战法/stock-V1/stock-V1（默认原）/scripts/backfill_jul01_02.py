"""
用同花顺涨停池快速补齐 7/1、7/2 买入日数据，并重算盈亏。
"""
from __future__ import annotations

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

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import price_service as ps
from stock_UI import (
    _load_day,
    _save_day,
    enrich_trade_prices,
    recalculate_all_trades,
    upsert_from_stock_py,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.10jqka.com.cn/",
}


def fetch_first_board(d0_8: str) -> list[dict]:
    url = f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&date={d0_8}"
    r = requests.get(url, timeout=20, headers=HEADERS)
    r.raise_for_status()
    info = ((r.json() or {}).get("data") or {}).get("info") or []
    first = []
    for x in info:
        code = str(x.get("code") or "").zfill(6)
        name = str(x.get("name") or "").strip()
        tag = str(x.get("change_tag") or "")
        again = x.get("is_again_limit")
        if code.startswith(("688", "8", "4")):
            continue
        if "ST" in name.upper():
            continue
        # 首板
        if tag == "FIRST_LIMIT" or again in (0, "0", False):
            if tag == "LIMIT_BACK" and again in (1, "1", True):
                continue
            if again in (1, "1", True) and tag != "FIRST_LIMIT":
                continue
            first.append({"code": code, "name": name})
    # 去重
    seen = set()
    out = []
    for h in first:
        if h["code"] in seen:
            continue
        seen.add(h["code"])
        out.append(h)
    return out


def rank_by_amount(stocks: list[dict], d0: str) -> list[dict]:
    """用 baostock 取成交额排序。"""
    ranked = []
    for s in stocks:
        ohlc = None
        # amount from daily via open proxy: use minute not needed
        try:
            import baostock as bs
            if not ps._bs_ensure_login():
                break
            bs_code = ps.to_bs_code(s["code"])
            with ps._BS_LOCK:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,amount,pctChg",
                    start_date=d0,
                    end_date=d0,
                    frequency="d",
                    adjustflag="3",
                )
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
            amt = float(rows[0][1]) if rows else 0.0
            pct = float(rows[0][2]) if rows else 0.0
        except Exception:
            amt, pct = 0.0, 0.0
        ranked.append({**s, "amount": amt, "pct": pct})
    ranked.sort(key=lambda x: x["amount"], reverse=True)
    return ranked


def main():
    # D0 -> buy
    jobs = [
        ("20260630", "2026-06-30"),
        ("20260701", "2026-07-01"),
    ]
    records = []
    for d0_8, d0 in jobs:
        buy = ps.next_trading_day(d0)
        print(f"\n=== D0 {d0} → D1 {buy} ===")
        first = fetch_first_board(d0_8)
        print(f"  同花顺首板候选 {len(first)}")
        ranked = rank_by_amount(first, d0)
        top2 = ranked[:2]
        for i, h in enumerate(top2, 1):
            print(f"  #{i} {h['code']} {h['name']} amt={h['amount']:.0f} pct={h['pct']:.2f}")
            records.append({
                "date": buy,
                "stock_code": h["code"],
                "stock_name": h["name"],
                "rank": i,
            })

    if not records:
        print("无记录可写")
        return

    print("\n写入…", upsert_from_stock_py(records, auto_enrich=False))
    for day in sorted({r["date"] for r in records}):
        trades = _load_day(day)
        for i, t in enumerate(trades):
            print(f"补价 {day} {t.get('stock_code')} {t.get('stock_name')}")
            trades[i] = enrich_trade_prices(t, force=True)
            time.sleep(0.1)
        _save_day(day, trades)

    print("重算", recalculate_all_trades())


if __name__ == "__main__":
    main()
