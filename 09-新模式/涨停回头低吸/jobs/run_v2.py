#!/usr/bin/env python3
"""
涨停回头低吸 V2 日更流水线（GPT V2.3）
数据写入 data_v2/，不碰 V1 data/。

  python3 jobs/run_v2.py --phase all --force
  python3 jobs/run_v2.py --phase watch --force
  python3 jobs/run_v2.py --phase hold --slot midday --force
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import rules_v2 as R  # noqa: E402
from v2 import scanner as scanner_v2  # noqa: E402
from v2 import signals as signals_v2  # noqa: E402
from services.run_recorder import track_run, summarize_pipeline_result  # noqa: E402
import store as store_mod  # noqa: E402


def run_pipeline(
    *,
    asof: str | None = None,
    phase: str = "all",
    scan_days: int = R.MORNING_SCAN_DAYS,
    skip_soft: bool = False,
    slot: str = "morning",
    force: bool = False,
) -> dict:
    today = asof or datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    if not force and now.weekday() >= 5:
        return {"ok": False, "reason": "周末跳过（--force 可强制）"}

    out: dict = {"asof": today, "phase": phase, "profile": "v2", "paper": R.PAPER_MODE, "steps": {}}
    print(f"[v2] ===== {today} phase={phase} slot={slot} paper={R.PAPER_MODE} =====")

    if phase in ("all", "limitup", "morning"):
        print("[v2] ① 涨停池…")
        out["steps"]["limitup"] = scanner_v2.scan_recent_days(
            asof=today, n=scan_days, skip_soft_filters=skip_soft
        )
        print(f"[v2]    added={out['steps']['limitup'].get('added')}")

    if phase in ("all", "watch", "morning"):
        print("[v2] ②- 刷新均线…")
        out["steps"]["ma"] = scanner_v2.refresh_all_ma(asof=today)
        print("[v2] ② 观察池…")
        out["steps"]["watch"] = scanner_v2.promote_ready_to_watch(asof=today, use_live=False)
        print(f"[v2]    promoted={out['steps']['watch'].get('promoted')}")

    if phase in ("all", "hold", "morning"):
        print("[v2] ③a 卖出…")
        out["steps"]["sell"] = signals_v2.check_sell_signals(asof=today, slot=slot)
        print(f"[v2]    sell_actions={len(out['steps']['sell'].get('actions') or [])}")
        print("[v2] ③b 买入（确认挂单/次日执行）…")
        out["steps"]["buy"] = signals_v2.check_buy_signals(asof=today, slot=slot)
        print(f"[v2]    buy_actions={len(out['steps']['buy'].get('actions') or [])}")

    if phase == "ma":
        out["steps"]["ma"] = scanner_v2.refresh_all_ma(asof=today)

    store_mod.for_profile("v2").meta_set({
        "last_v2_pipeline": today,
        "last_v2_phase": phase,
        "last_v2_slot": slot,
        "last_v2_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "paper": R.PAPER_MODE,
        "experiment_id": R.EXPERIMENT_ID,
    })
    out["ok"] = True
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "limitup", "watch", "hold", "morning", "ma"])
    ap.add_argument("--asof", default="")
    ap.add_argument("--scan-days", type=int, default=R.MORNING_SCAN_DAYS)
    ap.add_argument("--skip-soft", action="store_true")
    ap.add_argument("--slot", default="morning", choices=["morning", "midday", "tail"])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    with track_run(
        "run_v2",
        profile="v2",
        phase=args.phase,
        slot=args.slot,
        asof=args.asof or None,
        args={
            "scan_days": args.scan_days,
            "skip_soft": args.skip_soft,
            "force": args.force,
        },
        triggered_by="cli",
        strategy_version=R.STRATEGY_VERSION,
        experiment_id=R.EXPERIMENT_ID,
    ) as ctx:
        res = run_pipeline(
            asof=args.asof or None,
            phase=args.phase,
            scan_days=0 if args.phase == "hold" else args.scan_days,
            skip_soft=args.skip_soft,
            slot=args.slot,
            force=args.force,
        )
        ctx["result"] = res
        ctx["summary"] = summarize_pipeline_result(res)
        ctx["ok"] = bool(res.get("ok", True))
        if not res.get("ok") and res.get("reason"):
            ctx["error"] = res.get("reason")
        print(res.get("ok"), res.get("reason") or "done")


if __name__ == "__main__":
    main()
