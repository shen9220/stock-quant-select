"""
up 程序权威数据源同步 — 与 9:00 PushPlus「up模型通知」严格一致。

铁纪律：禁止用 fast_gen_up_july 覆盖 up.py 的选股结果。
权威源优先级：
  1) strength_limit_up_result.json（source_script=up.py，analysis_date=买入日）
  2) results/{D0}_up_recommendations.md（仅当无 json 时只读，不自动生成）
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UP_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.dirname(UP_DIR)
for p in (ROOT_DIR, os.path.join(ROOT_DIR, "交易记录UI")):
    if p not in sys.path:
        sys.path.insert(0, p)

import paths  # noqa: E402
import price_service as ps

RESULTS_DIR = paths.program_results_dir("up")
RANK_RE = re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")
_UP_JSON_CANDIDATES = paths.up_result_json_candidates()


def _load_up_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def canonical_buy_date_from_json(data: dict) -> str | None:
    """
    由 up.py JSON 推断唯一买入日，避免同一 JSON 重复匹配 8/13 与 8/14。
    新版：analysis_date = 买入日 D1；旧版：analysis_date = D0 → next_trading_day(D0)。
    """
    analysis = str(data.get("analysis_date") or "").strip()
    if not analysis:
        return None
    cal = ps.get_trade_calendar()

    def _is_td(d: str) -> bool:
        if cal is None:
            return True
        dt = datetime.strptime(d, "%Y-%m-%d")
        return not cal[cal["trade_date"] == dt].empty

    if _is_td(analysis):
        return analysis
    nxt = ps.next_trading_day(analysis)
    return nxt if nxt and _is_td(nxt) else None


def find_up_result_json(buy_date: str) -> tuple[str | None, dict | None]:
    """
    查找与买入日匹配的 up.py 结果 JSON（每个 JSON 仅对应一个 canonical 买入日）。
    buy_date = D1（买入日）。
    """
    for path in _UP_JSON_CANDIDATES:
        if not os.path.isfile(path):
            continue
        data = _load_up_json(path)
        if not data:
            continue
        if data.get("source_script") not in (None, "up.py"):
            continue
        if canonical_buy_date_from_json(data) == buy_date:
            return path, data
    return None, None


def top2_from_json(data: dict) -> list[dict]:
    stocks = []
    for i, s in enumerate(data.get("top_stocks") or [], 1):
        code = str(s.get("stock_code") or "").zfill(6)
        name = str(s.get("stock_name") or "").strip()
        if not code:
            continue
        stocks.append({
            "stock_code": code,
            "stock_name": name,
            "rank": int(s.get("rank") or i),
        })
        if len(stocks) >= 2:
            break
    return stocks


def top2_from_md(d0: str) -> list[dict]:
    d8 = d0.replace("-", "")
    path = paths.recommendation_md("up", d0)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if "fast_gen" in text.lower() or "历史回填" in text:
        return []
    head = text.split("=== 其他未入选", 1)[0]
    stocks = []
    for m in RANK_RE.finditer(head):
        stocks.append({"stock_code": m.group(1), "stock_name": m.group(2).strip(), "rank": len(stocks) + 1})
        if len(stocks) >= 2:
            break
    return stocks


def resolve_up_top2(buy_date: str) -> tuple[list[dict], str, str | None]:
    """
    返回 (top2, source, json_path)。
    source: up_py_json | up_md | none
    """
    path, data = find_up_result_json(buy_date)
    if data:
        stocks = top2_from_json(data)
        if stocks:
            return stocks, "up_py_json", path

    d0 = ps.prev_trading_day(buy_date)
    if d0:
        stocks = top2_from_md(d0)
        if stocks:
            for i, s in enumerate(stocks, 1):
                s["rank"] = i
            return stocks, "up_md", None

    return [], "none", None


def write_up_md_from_json(data: dict, d0: str) -> str:
    """用 up.py JSON 覆写 recommendations md，保证与推送一致。"""
    d8 = d0.replace("-", "")
    display = f"{d0[:4]}年{d0[5:7]}月{d0[8:10]}日"
    d1 = ps.next_trading_day(d0) or "?"
    lines = [
        f"=== {display} 强度首板推荐（前2只，up.py 权威结果）===",
        f"交易时序: D0首板复盘选出 → D1({d1})开盘集合竞价买入 → D2收盘前卖出",
        "数据: up.py strength_limit_up_result.json（与 PushPlus 通知一致）",
        "",
    ]
    for s in data.get("top_stocks") or []:
        rank = s.get("rank", "?")
        lines.append(f"排名 {rank}: {s['stock_code']} - {s['stock_name']}")
        lines.append(f"  综合得分: {s.get('score', 0):.2f}")
        lines.append(f"  涨停原因分析: {s.get('zt_reason', '未知')}")
        lines.append("")
    lines.append("=== 其他未入选前二原因 ===")
    lines.append("详见 up.py 完整输出。")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    md_path = paths.recommendation_md("up", d0)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return md_path


def _is_trading_day(day: str) -> bool:
    cal = ps.get_trade_calendar()
    if cal is None:
        return True
    dt = datetime.strptime(day, "%Y-%m-%d")
    return not cal[cal["trade_date"] == dt].empty


def sync_up_trades(buy_date: str | None = None, force: bool = True) -> dict:
    """同步 up 基础交易记录，必须与 9:00 通知 Top2 一致。"""
    buy_date = buy_date or datetime.now().strftime("%Y-%m-%d")
    if not _is_trading_day(buy_date):
        return {
            "synced": 0,
            "buy_date": buy_date,
            "reason": "non_trading_day",
            "hint": "买入日必须是 A 股交易日",
        }
    d0 = ps.prev_trading_day(buy_date)

    stocks, source, json_path = resolve_up_top2(buy_date)
    if not stocks:
        return {
            "synced": 0,
            "buy_date": buy_date,
            "d0": d0,
            "source": source,
            "reason": "no_up_authoritative_source",
            "hint": "请先运行 up.py --notify，确保 strength_limit_up_result.json 存在",
        }

    if source == "up_py_json" and json_path and d0:
        data = _load_up_json(json_path)
        if data:
            write_up_md_from_json(data, d0)
            print(f"[up-sync] 已用 up.py JSON 覆写 md（D0={d0}）")

    from stock_UI import upsert_from_stock_py

    records = [
        {"date": buy_date, "stock_code": s["stock_code"], "stock_name": s["stock_name"], "rank": s["rank"]}
        for s in stocks
    ]
    result = upsert_from_stock_py(records, auto_enrich=False, replace_by_rank=True, program="up")
    out = {
        "synced": result.get("created", 0) + result.get("updated", 0),
        "created": result.get("created", 0),
        "updated": result.get("updated", 0),
        "buy_date": buy_date,
        "d0": d0,
        "source": source,
        "json_path": json_path,
        "records": records,
        "stocks": [f"{s['stock_code']} {s['stock_name']}" for s in stocks],
    }
    print(f"[up-sync] {buy_date} ← {source}: {out['stocks']}")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="同步 up.py 权威结果到 up/trades")
    ap.add_argument("--date", default="", help="买入日 YYYY-MM-DD，默认今天")
    args = ap.parse_args()
    buy = args.date or datetime.now().strftime("%Y-%m-%d")
    print(sync_up_trades(buy, force=True))