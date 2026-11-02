#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
稽查：主板/创业板 board_rank 是否与 V1 原 results 全量评分一致。

口径（铁律）
----
- results 文件名日期 = 涨停日 D0
- trades 文件名日期 = 买入日 D1（results 头「买入日(D1)」）
- 板 Top2 = 同一份 results 内按综合得分筛该板最高两只
- 本稽查按 D1 对齐；若用 D0 去对 trades[D0] 会系统性差一天（假阳性）

用法
----
  python3 audit_board_vs_results.py
  python3 audit_board_vs_results.py --json
  python3 audit_board_vs_results.py --focus 2026-10-08
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_JOBS = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_JOBS)
_LIB = os.path.join(_BASE, "lib")
for _p in (_BASE, _LIB, _JOBS, os.path.dirname(_BASE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import ensure_sys_path  # noqa: E402
ensure_sys_path()

import backfill_board_records as bbr  # noqa: E402
import paths as pkg_paths  # noqa: E402
import stock_UI  # noqa: E402
from stock_meta import BOARD_CHUANGYEBAN, BOARD_ZHUBAN  # noqa: E402


def _actual_board_map(trades: list) -> dict[str, dict[int, tuple[str, str]]]:
    out = {BOARD_ZHUBAN: {}, BOARD_CHUANGYEBAN: {}}
    for t in trades:
        br = t.get("board_rank")
        if br is None:
            continue
        board = t.get("board") or ""
        if board not in out:
            continue
        code = str(t.get("stock_code") or "").zfill(6)
        out[board][int(br)] = (code, str(t.get("stock_name") or ""))
    return out


def audit_one(path: str) -> dict:
    name = os.path.basename(path)
    buy_day, scored = bbr.parse_v1_results_scored(path)
    d0_8 = name.split("_", 1)[0]
    d0 = f"{d0_8[:4]}-{d0_8[4:6]}-{d0_8[6:]}" if len(d0_8) == 8 else ""
    if not scored or not buy_day:
        return {"md": name, "d0": d0, "buy": buy_day, "status": "skip_empty"}
    expected = bbr.board_top2_from_scored(buy_day, scored)
    trades = stock_UI._load_day(buy_day, "stock")
    if not trades:
        return {
            "md": name, "d0": d0, "buy": buy_day,
            "status": "missing_trades", "bad": [],
        }
    actual = _actual_board_map(trades)
    bad = []
    for board in (BOARD_ZHUBAN, BOARD_CHUANGYEBAN):
        exp_rows = expected.get(board) or []
        for row in exp_rows:
            er = int(row["board_rank"])
            ec = str(row["stock_code"]).zfill(6)
            got = actual[board].get(er)
            if not got or got[0] != ec:
                bad.append({
                    "board": board,
                    "board_rank": er,
                    "expected": f"{ec} {row.get('stock_name')}",
                    "actual": (f"{got[0]} {got[1]}" if got else None),
                })
        for br, (c, n) in sorted(actual[board].items()):
            if not any(int(r["board_rank"]) == br for r in exp_rows):
                bad.append({
                    "board": board,
                    "board_rank": br,
                    "expected": None,
                    "actual": f"{c} {n}",
                    "note": "extra",
                })
    # D0 误当买入日时的「看起来不一致」（说明用，不算硬失败）
    confuse = None
    if d0 and d0 != buy_day:
        t0 = stock_UI._load_day(d0, "stock")
        cy_scored = [
            r for r in scored
            if str(r["stock_code"]).startswith(("300", "301"))
        ]
        if t0 and cy_scored:
            want = str(cy_scored[0]["stock_code"]).zfill(6)
            got1 = None
            for t in t0:
                if t.get("board") == BOARD_CHUANGYEBAN and int(t.get("board_rank") or 0) == 1:
                    got1 = str(t.get("stock_code") or "").zfill(6)
            if got1 and got1 != want:
                confuse = {
                    "hint": "UI若按涨停日D0点开会看到别票；正确买入日见 buy",
                    "results_cy1": f"{want} {cy_scored[0].get('stock_name')}",
                    "trades_on_d0_cy1": got1,
                }
    return {
        "md": name,
        "d0": d0,
        "buy": buy_day,
        "status": "mismatch" if bad else "ok",
        "bad": bad,
        "d0_date_confusion": confuse,
    }


def run(focus: str = "") -> dict:
    results_dir = pkg_paths.program_results_dir("stock")
    rows = []
    for name in sorted(os.listdir(results_dir)):
        if not name.endswith("_recommendations.md") or not name.startswith("2026"):
            continue
        if focus:
            f = focus.replace("-", "")
            if f not in name and focus not in name:
                # also allow focus as buy day — include if buy matches after parse
                pass
        rec = audit_one(os.path.join(results_dir, name))
        if focus:
            f8 = focus.replace("-", "")[:8]
            if focus not in (rec.get("buy") or "", rec.get("d0") or "", rec.get("md") or "") and f8 not in (rec.get("md") or ""):
                continue
        rows.append(rec)
    summary = {
        "checked": sum(1 for r in rows if r["status"] in ("ok", "mismatch", "missing_trades")),
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "mismatch": sum(1 for r in rows if r["status"] == "mismatch"),
        "missing_trades": sum(1 for r in rows if r["status"] == "missing_trades"),
        "d0_confusion_hints": sum(1 for r in rows if r.get("d0_date_confusion")),
        "rows": rows,
    }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="稽查主板/创业板 vs V1 results")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--focus", default="", help="如 2026-10-08（匹配 D0 或买入日）")
    args = ap.parse_args()
    summary = run(args.focus)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["mismatch"] == 0 and summary["missing_trades"] == 0 else 2
    print(
        f"稽查完成 checked={summary['checked']} ok={summary['ok']} "
        f"mismatch={summary['mismatch']} missing_trades={summary['missing_trades']} "
        f"d0错位提示={summary['d0_confusion_hints']}"
    )
    print("口径: results文件名=涨停日D0；trades文件名=买入日D1；板Top2按同md得分筛。")
    for r in summary["rows"]:
        if r["status"] == "ok" and not args.focus:
            continue
        if r["status"] == "skip_empty":
            continue
        print(f"- {r['status']} buy={r['buy']} d0={r['d0']} md={r['md']}")
        for b in r.get("bad") or []:
            print(f"    {b}")
        if r.get("d0_date_confusion"):
            print(f"    [D0错位提示] {r['d0_date_confusion']}")
    if summary["mismatch"] or summary["missing_trades"]:
        return 2
    print("结论: 按买入日D1对齐，board_rank 与 results 一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
