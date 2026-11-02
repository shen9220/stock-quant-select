#!/usr/bin/env python3
"""
集合竞价拉取任务（涨停回头低吸）——只写库 / 打日志，不推送。

说明：PushPlus 通知仅属于首板 `stock/stock_updata.py`，本程序不发通知。

时间窗（与 stock_updata 对齐）：
  9:24:40 开始 → 9:25 等今开落地 → 目标 9:28 写完 → 最迟 9:29

数据源：腾讯行情批量「今开/昨收」（绝不用现价冒充竞价）
标的：观察池 watching + 持有池 holding（通常很少，低频分批，避免拉黑）

用法:
  python3 jobs/auction_fetch.py            # 交易日早盘
  python3 jobs/auction_fetch.py --force    # 盘后补跑（跳过时间窗）
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import store
from rules import BUILD_ADJ_DAYS, WATCH_ADJ_DAYS, WATCH_WATCHING, POS_HOLDING
from services import auction as auc
from services import market as mkt


def _collect_targets() -> list[dict]:
    """观察中 + 持仓中。"""
    rows: list[dict] = []
    seen: set[str] = set()

    for w in store.load_watches():
        if w.get("status") != WATCH_WATCHING:
            continue
        code = str(w.get("stock_code") or "").zfill(6)
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append({
            "pool": "watch",
            "id": w.get("id"),
            "stock_code": code,
            "stock_name": w.get("stock_name"),
            "limit_up_date": w.get("limit_up_date"),
            "adj_day": w.get("adj_day"),
            "in_dip_zone": w.get("in_dip_zone"),
            "ma10": w.get("ma10"),
            "ma20": w.get("ma20"),
            "is_auto_added": w.get("is_auto_added"),
        })

    for p in store.load_positions():
        if p.get("status") != POS_HOLDING:
            continue
        code = str(p.get("stock_code") or "").zfill(6)
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append({
            "pool": "position",
            "id": p.get("id"),
            "stock_code": code,
            "stock_name": p.get("stock_name"),
            "limit_up_date": p.get("limit_up_date"),
            "adj_day": p.get("adj_day"),
            "buy_price": p.get("buy_price"),
            "hold_days": p.get("hold_days"),
        })
    return rows


def _enrich_adj(rows: list[dict], asof: str) -> None:
    for r in rows:
        lup = r.get("limit_up_date")
        if lup:
            r["adj_day"] = mkt.adj_day_of(lup, asof)


def _persist_auction(rows: list[dict], snaps: dict[str, dict], asof: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        code = r["stock_code"]
        snap = snaps.get(code) or {}
        patch = {
            "auction_date": asof,
            "auction_open": snap.get("open"),
            "auction_pre_close": snap.get("pre_close"),
            "auction_pct": snap.get("pct"),
            "auction_tag": auc.classify_open(snap.get("pct")),
            "auction_fetched_at": now,
        }
        if r["pool"] == "watch" and r.get("id") is not None:
            store.update_watch(int(r["id"]), patch)
        elif r["pool"] == "position" and r.get("id") is not None:
            store.update_position(int(r["id"]), patch)


def format_summary(rows: list[dict], snaps: dict[str, dict], asof: str) -> str:
    lines = [
        f"=== 涨停回头低吸 · 集合竞价拉取 {asof} ===",
        f"时间 {datetime.now().strftime('%H:%M:%S')} | 今开 vs 昨收（非现价）| 不推送",
        "",
    ]
    watch_rows = [r for r in rows if r["pool"] == "watch"]
    pos_rows = [r for r in rows if r["pool"] == "position"]

    def _line(r: dict) -> str:
        code = r["stock_code"]
        snap = snaps.get(code) or {}
        pct = snap.get("pct")
        tag = auc.classify_open(pct)
        pct_s = f"{pct:+.2f}%" if pct is not None else "—"
        open_s = f"{snap['open']:.2f}" if snap.get("open") is not None else "—"
        adj = r.get("adj_day")
        adj_s = f"D{adj}" if adj not in (None, "") else "—"
        name = r.get("stock_name") or snap.get("name") or ""
        extra = []
        if r["pool"] == "watch":
            if r.get("in_dip_zone"):
                extra.append("低吸区")
            if adj in BUILD_ADJ_DAYS:
                extra.append("建仓窗")
            elif adj in WATCH_ADJ_DAYS:
                extra.append("观察窗")
            extra.append("自动" if r.get("is_auto_added") else "手动")
        else:
            bp = r.get("buy_price")
            if bp not in (None, "") and snap.get("open") is not None:
                gap = (float(snap["open"]) - float(bp)) / float(bp) * 100
                extra.append(f"相对成本{gap:+.1f}%")
            if r.get("hold_days"):
                extra.append(f"持{r['hold_days']}天")
        ex = (" | " + ",".join(extra)) if extra else ""
        return f"• {code} {name} | {tag} {pct_s} 开{open_s} | {adj_s}{ex}"

    lines.append(f"【观察池 {len(watch_rows)}】")
    if not watch_rows:
        lines.append("  （空）")
    else:
        def _sort_key(r):
            pct = (snaps.get(r["stock_code"]) or {}).get("pct")
            return (0 if pct is not None and pct < 0 else 1, pct if pct is not None else 999)

        for r in sorted(watch_rows, key=_sort_key):
            lines.append(_line(r))

    lines.append("")
    lines.append(f"【持有池 {len(pos_rows)}】")
    if not pos_rows:
        lines.append("  （空）")
    else:
        for r in pos_rows:
            lines.append(_line(r))

    miss = [r["stock_code"] for r in rows if r["stock_code"] not in snaps]
    if miss:
        lines.append("")
        lines.append(f"⚠ 未拿到今开: {', '.join(miss)}")

    lines.append("")
    lines.append("（本任务不推送；通知仅 stock_updata）")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="涨停回头低吸 · 集合竞价拉取（不推送）")
    ap.add_argument("--force", action="store_true", help="忽略 9:24:40–9:29 时间窗")
    args = ap.parse_args()

    auc.enforce_auction_window(force=args.force)
    mkt._ensure_fast_calendar()

    asof = datetime.now().strftime("%Y-%m-%d")
    rows = _collect_targets()
    _enrich_adj(rows, asof)
    print(f"[auction] 标的 {len(rows)}（观察+持仓去重）")

    if not rows:
        print("观察池/持有池均为空，无需拉取竞价。")
        store.meta_set({"last_auction_fetch": asof, "auction_count": 0})
        return 0

    codes = [r["stock_code"] for r in rows]
    snaps = auc.fetch_open_ready(codes, force=args.force)
    print(f"[auction] 今开命中 {len(snaps)}/{len(codes)}")

    _persist_auction(rows, snaps, asof)
    print(format_summary(rows, snaps, asof))

    store.meta_set({
        "last_auction_fetch": asof,
        "auction_count": len(snaps),
        "auction_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
