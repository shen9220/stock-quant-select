#!/usr/bin/env python3
"""
涨停回头低吸 · 日更流水线

阶段顺序（铁律）：
  1) limitup  涨停池写入
  2) watch    观察池：适宜度排序，可多选入选（每日必跑）
  3) hold     持有池：先卖后买（现价监控）

用法:
  python3 jobs/run_morning_0940.py --phase all
  python3 jobs/run_morning_0940.py --phase watch --force
  python3 jobs/run_morning_0940.py --phase hold --slot midday --force
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rules import (  # noqa: E402
    MAX_CONCURRENT_HOLDINGS,
    MORNING_JOB_HOUR,
    MORNING_JOB_MINUTE,
    MORNING_SCAN_DAYS,
)
from services import scanner, signals  # noqa: E402
from services import market as mkt  # noqa: E402
from services.run_recorder import track_run, summarize_pipeline_result  # noqa: E402
import store  # noqa: E402


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def run_limitup(*, asof: str, scan_days: int, skip_soft: bool) -> dict:
    print(f"[pipeline] ① 涨停池扫描（近 {scan_days} 日）…")
    scan = scanner.scan_recent_days(
        asof=asof, n=scan_days, skip_soft_filters=skip_soft, auto_promote=False,
    )
    print(f"[pipeline]    days={scan.get('days')} added={scan.get('added')}")
    return {"days": scan.get("days"), "added": scan.get("added")}


def run_watch(*, asof: str, use_live: bool = True) -> dict:
    print("[pipeline] ② 观察池更新（涨停池之后 · 适宜度多选）…")
    promo = scanner.promote_ready_to_watch(asof=asof, use_live=use_live)
    print(
        f"[pipeline]    candidates={promo.get('candidates')} "
        f"promoted={promo.get('promoted')} refreshed={promo.get('refreshed')} "
        f"expired={promo.get('expired')}"
    )
    for it in (promo.get("items") or [])[:15]:
        print(f"      + {it.get('code')} {it.get('name')} adj{it.get('adj')} depth={it.get('depth')}")
    return promo


def run_hold(*, asof: str, slot: str = "morning", use_live: bool = True,
             skip_buy: bool = False, skip_sell: bool = False) -> dict:
    out: dict = {"slot": slot}
    if not skip_sell:
        print(f"[pipeline] ③a 持有池卖出监控 slot={slot} live={use_live}…")
        sell = signals.check_sell_signals(asof=asof, use_live=use_live, slot=slot)
        out["sell"] = {"actions": len(sell.get("actions") or []), "detail": sell.get("actions")}
        print(f"[pipeline]    sell_actions={out['sell']['actions']}")
    if not skip_buy:
        holdings = store.holding_count()
        print(f"[pipeline] ③b 持有池买入扫描 {holdings}/{MAX_CONCURRENT_HOLDINGS} slot={slot}…")
        buy = signals.check_buy_signals(asof=asof, use_live=use_live, slot=slot)
        out["buy"] = {"actions": len(buy.get("actions") or []), "detail": buy.get("actions")}
        print(f"[pipeline]    buy_actions={out['buy']['actions']}")
    return out


def run_pipeline(
    *,
    asof: str | None = None,
    phase: str = "all",
    scan_days: int = MORNING_SCAN_DAYS,
    skip_soft: bool = False,
    skip_buy: bool = False,
    skip_sell: bool = False,
    slot: str = "morning",
    use_live: bool = True,
) -> dict:
    today = asof or datetime.now().strftime("%Y-%m-%d")
    out: dict = {"asof": today, "phase": phase, "steps": {}}
    try:
        mkt._ensure_fast_calendar()
    except Exception as e:
        print(f"[pipeline] calendar warn: {e}")

    print(f"[pipeline] ===== asof={today} phase={phase} slot={slot} =====")

    if phase in ("all", "limitup", "morning"):
        if scan_days and scan_days > 0:
            out["steps"]["limitup"] = run_limitup(asof=today, scan_days=scan_days, skip_soft=skip_soft)
        else:
            out["steps"]["limitup"] = {"skipped": True}

    if phase in ("all", "watch", "morning"):
        # 轻量均线：观察/持有 + 窗口内涨停（晋升内部也会补）
        if phase in ("all", "morning"):
            print("[pipeline] ②- 刷新均线（观察/持有）…")
            out["steps"]["ma"] = scanner.refresh_morning_ma(asof=today)
        out["steps"]["watch"] = run_watch(asof=today, use_live=use_live)

    if phase in ("all", "hold", "morning"):
        out["steps"]["hold"] = run_hold(
            asof=today, slot=slot, use_live=use_live,
            skip_buy=skip_buy, skip_sell=skip_sell,
        )

    store.meta_set({
        "last_pipeline": today,
        "last_pipeline_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_pipeline_phase": phase,
        "last_pipeline_slot": slot,
    })
    if phase in ("all", "morning"):
        store.meta_set({
            "last_morning_0940": today,
            "last_morning_0940_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_morning_0940_added": (out["steps"].get("limitup") or {}).get("added"),
            "last_morning_0940_promote": (out["steps"].get("watch") or {}).get("promoted"),
        })
    if phase == "hold":
        store.meta_set({
            f"last_hold_{slot}": today,
            f"last_hold_{slot}_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    print("[pipeline] ===== done =====")
    out["ok"] = True
    return out


def main():
    ap = argparse.ArgumentParser(description="涨停回头低吸流水线")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--phase", default="all", choices=["all", "morning", "limitup", "watch", "hold"])
    ap.add_argument("--slot", default="morning", choices=["morning", "midday", "tail"],
                    help="持有池监控档：上午 / 中午 / 尾盘")
    ap.add_argument("--backfill-days", type=int, default=MORNING_SCAN_DAYS)
    ap.add_argument("--skip-soft", action="store_true")
    ap.add_argument("--skip-buy", action="store_true")
    ap.add_argument("--skip-sell", action="store_true")
    ap.add_argument("--no-live", action="store_true", help="不用腾讯现价（仅收盘）")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    now = datetime.now()
    if not args.force and not args.asof:
        if not _is_weekday(now):
            print(f"[pipeline] 周末跳过（{now:%Y-%m-%d}）")
            return
        hm = now.hour * 60 + now.minute
        if args.phase in ("all", "morning", "limitup", "watch"):
            start = MORNING_JOB_HOUR * 60 + MORNING_JOB_MINUTE
            if hm < start - 2 and args.phase != "hold":
                print(f"[pipeline] 未到 {MORNING_JOB_HOUR:02d}:{MORNING_JOB_MINUTE:02d}，退出")
                return
        if hm > 15 * 60 + 30:
            print("[pipeline] 已过盘后窗，退出（可用 --force）")
            return

    with track_run(
        "run_morning_0940",
        profile="v1",
        phase=args.phase,
        slot=args.slot,
        asof=args.asof,
        args={
            "backfill_days": args.backfill_days,
            "skip_soft": args.skip_soft,
            "skip_buy": args.skip_buy,
            "skip_sell": args.skip_sell,
            "no_live": args.no_live,
            "force": args.force,
        },
        triggered_by="cli",
        strategy_version="V1",
        experiment_id="V1_BASELINE",
    ) as ctx:
        result = run_pipeline(
            asof=args.asof,
            phase=args.phase,
            scan_days=0 if args.phase == "hold" else args.backfill_days,
            skip_soft=args.skip_soft,
            skip_buy=args.skip_buy,
            skip_sell=args.skip_sell,
            slot=args.slot,
            use_live=not args.no_live,
        )
        ctx["result"] = result
        ctx["summary"] = summarize_pipeline_result(result)
        ctx["ok"] = bool(result.get("ok", True))
        print(result)


if __name__ == "__main__":
    main()
