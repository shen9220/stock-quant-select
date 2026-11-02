#!/usr/bin/env python3
"""将 stock/source/stock.xlsx（5–8 月本地实跑）覆盖写入 stock/trades/。

规则（用户确认 2026-09-30）：
- 尾盘卖出价 → sell_price_1315；早盘 → sell_price_935
- 盈亏按 stock_UI.auto_calculate 重算（不用表内盈亏列）
- 名称自动反查代码；一字 = 留档、清空买卖价（与现系统一致）
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any

import openpyxl

_PROG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # stock-V1（默认原）
ROOT = os.path.dirname(os.path.dirname(_PROG))  # 首板战法
STOCK_DIR = _PROG
XLSX = os.path.join(STOCK_DIR, "source", "stock.xlsx")
TRADES_DIR = os.path.join(STOCK_DIR, "trades")
RECORDS_DIR = os.path.join(ROOT, "交易记录UI")

sys.path.insert(0, ROOT)
sys.path.insert(0, RECORDS_DIR)

import fetch_calendar as fc  # noqa: E402
from stock_UI import auto_calculate, empty_trade  # noqa: E402

# 表内简称/笔误 → 交易所证券简称
NAME_ALIASES = {
    "云南储业": "云南锗业",
    "四方股": "四方股份",
    "京东方A": "京东方Ａ",
    "北京君正": "君正股份",  # 300223
    "北路药业": "北陆药业",  # 300016，表内笔误
}


def _norm_name(s: str) -> str:
    s = str(s or "").strip()
    s = s.replace(" ", "").replace("\u3000", "")
    s = s.replace("A", "Ａ").replace("B", "Ｂ")  # 部分深市带全角
    # 再反转常见半角需求：京东方Ａ 在 SH/SZ 可能是 京东方Ａ
    return s


def build_name_map() -> dict[str, str]:
    """沪深码表 + 本地 trades 兜底（仅补漏，不覆盖权威）。"""
    import akshare as ak

    m: dict[str, str] = {}

    def put(name: str, code: str):
        name = str(name or "").strip()
        code = str(code or "").zfill(6)
        if not name or not re.match(r"^\d{6}$", code):
            return
        m[name] = code
        m[_norm_name(name)] = code
        # 半角/全角 A 变体
        m[name.replace("Ａ", "A").replace("Ｂ", "B")] = code
        m[name.replace("A", "Ａ").replace("B", "Ｂ")] = code

    sh = ak.stock_info_sh_name_code()
    for _, row in sh.iterrows():
        put(row["证券简称"], row["证券代码"])
    sz = ak.stock_info_sz_name_code()
    for _, row in sz.iterrows():
        put(row["A股简称"], row["A股代码"])

    # 本地已有 trades 补漏
    for fn in os.listdir(TRADES_DIR):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(TRADES_DIR, fn), encoding="utf-8") as f:
            for t in json.load(f):
                if t.get("stock_name") and t.get("stock_code"):
                    nm = str(t["stock_name"]).strip()
                    if nm not in m and _norm_name(nm) not in m:
                        put(nm, t["stock_code"])
    return m


def resolve_code(name: str, name_map: dict[str, str]) -> str | None:
    raw = str(name or "").strip()
    if not raw:
        return None
    cand = NAME_ALIASES.get(raw, raw)
    for key in (cand, _norm_name(cand), cand.replace("Ａ", "A"), _norm_name(cand).replace("Ａ", "A")):
        if key in name_map:
            return name_map[key]
    # 模糊：唯一包含
    hits = [(n, c) for n, c in name_map.items() if cand in n or n in cand]
    # 去重 by code
    codes = {c for _, c in hits}
    if len(codes) == 1:
        return next(iter(codes))
    return None


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        s = v.strip()
        if s in ("一字", "一字板", "涨停", "-", "—"):
            return None
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _is_yizi(buy_cell: Any) -> bool:
    if buy_cell is None:
        return False
    if isinstance(buy_cell, str) and buy_cell.strip() in ("一字", "一字板"):
        return True
    return False


def next_sell_date(buy: str) -> str:
    nxt = fc.next_trading_day(buy)
    if nxt:
        return nxt
    dt = datetime.strptime(buy, "%Y-%m-%d") + timedelta(days=1)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def max_trade_id() -> int:
    mx = 0
    for fn in os.listdir(TRADES_DIR):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(TRADES_DIR, fn), encoding="utf-8") as f:
            for t in json.load(f):
                try:
                    mx = max(mx, int(t.get("id") or 0))
                except (TypeError, ValueError):
                    pass
    return mx


def parse_xlsx(name_map: dict[str, str]) -> tuple[dict[str, list[dict]], list[str]]:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    by_day: dict[str, list[dict]] = {}
    unresolved: list[str] = []
    next_id = max_trade_id() + 1

    for sheet in wb.sheetnames:
        ws = wb[sheet]
        for r in range(3, ws.max_row + 1):
            d = ws.cell(r, 1).value
            if not isinstance(d, datetime):
                continue
            date_str = d.strftime("%Y-%m-%d")
            day_trades: list[dict] = []
            for rank, name_col in ((1, 2), (2, 10)):
                name = ws.cell(r, name_col).value
                if not name or not str(name).strip():
                    continue
                name = str(name).strip()
                buy_cell = ws.cell(r, name_col + 1).value
                am_sell = _num(ws.cell(r, name_col + 2).value)
                pm_sell = _num(ws.cell(r, name_col + 3).value)
                yizi = _is_yizi(buy_cell)
                buy = None if yizi else _num(buy_cell)

                code = resolve_code(name, name_map)
                if not code:
                    unresolved.append(f"{date_str} R{rank} {name}")
                    # 仍写入，代码空串占位会导致 upsert 跳过；这里用临时码禁止——必须有码
                    continue

                t = empty_trade(
                    id=next_id,
                    date=date_str,
                    program="stock",
                    stock_code=code,
                    stock_name=name,
                    rank=rank,
                )
                next_id += 1
                if yizi:
                    t["skip_reason"] = f"{date_str} 开盘涨停(一字/封板)，买不进，不记买卖价"
                    t["buy_price"] = None
                    t["sell_price_935"] = None
                    t["sell_price_1315"] = None
                    t["sell_price_half_935"] = None
                    t["sell_price_half_1315"] = None
                    t["sell_date"] = None
                    t["notes"] = "source=local_xlsx; yizi=1"
                else:
                    t["buy_price"] = buy
                    t["sell_price_935"] = am_sell
                    t["sell_price_1315"] = pm_sell
                    t["sell_price_half_935"] = am_sell
                    t["sell_price_half_1315"] = pm_sell
                    t["sell_date"] = next_sell_date(date_str) if (am_sell or pm_sell or buy) else None
                    t["skip_reason"] = None
                    t["notes"] = "source=local_xlsx"
                    t = auto_calculate(t)
                day_trades.append(t)
            if day_trades:
                by_day[date_str] = day_trades
    return by_day, unresolved


def main() -> int:
    if not os.path.isfile(XLSX):
        print(f"缺少 {XLSX}")
        return 1
    print("加载名称码表…")
    name_map = build_name_map()
    print(f"码表条目 {len(name_map)}")
    by_day, unresolved = parse_xlsx(name_map)
    if unresolved:
        print("⚠️ 未解析到代码（这些行未写入）:")
        for u in unresolved:
            print(" ", u)
        if unresolved:
            # 硬失败：宁可停手也不写半套
            print("请补 NAME_ALIASES 或修正表内名称后重跑")
            return 2

    os.makedirs(TRADES_DIR, exist_ok=True)
    written = 0
    yizi_n = 0
    for day, trades in sorted(by_day.items()):
        path = os.path.join(TRADES_DIR, f"{day}.json")
        for t in trades:
            if t.get("skip_reason") and "一字" in str(t.get("skip_reason")):
                yizi_n += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trades, f, ensure_ascii=False, indent=2, default=str)
        written += 1

    # 汇总：有买卖价的 pnl
    pnls_m, pnls_a = [], []
    for trades in by_day.values():
        for t in trades:
            if t.get("pnl_morning") is not None:
                pnls_m.append(float(t["pnl_morning"]))
            if t.get("pnl_afternoon") is not None:
                pnls_a.append(float(t["pnl_afternoon"]))
    print(f"✅ 覆盖写入 {written} 个交易日 → {TRADES_DIR}")
    print(f"   一字(没买) {yizi_n} 条")
    if pnls_m:
        print(f"   早盘盈亏合计 {sum(pnls_m):.2f}% / 均 {sum(pnls_m)/len(pnls_m):.2f}%  (n={len(pnls_m)})")
    if pnls_a:
        print(f"   尾盘(13:15)盈亏合计 {sum(pnls_a):.2f}% / 均 {sum(pnls_a)/len(pnls_a):.2f}%  (n={len(pnls_a)})")
    # 样例
    for sample in ("2026-05-07", "2026-06-01", "2026-08-03"):
        if sample in by_day:
            print(f"--- {sample} ---")
            for t in by_day[sample]:
                print(
                    f"  #{t['rank']} {t['stock_code']} {t['stock_name']} "
                    f"buy={t.get('buy_price')} 935={t.get('sell_price_935')} 1315={t.get('sell_price_1315')} "
                    f"pnl_m={t.get('pnl_morning')} pnl_a={t.get('pnl_afternoon')} skip={t.get('skip_reason')}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
