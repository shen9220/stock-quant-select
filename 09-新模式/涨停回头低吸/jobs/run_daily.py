"""兼容入口：转调流水线。"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from jobs.run_morning_0940 import run_pipeline  # noqa: E402
from rules import MORNING_SCAN_DAYS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None)
    ap.add_argument("--phase", default="all", choices=["all", "morning", "limitup", "watch", "hold"])
    ap.add_argument("--slot", default="morning", choices=["morning", "midday", "tail"])
    ap.add_argument("--skip-scan", action="store_true")
    ap.add_argument("--skip-soft", action="store_true")
    ap.add_argument("--backfill-days", type=int, default=MORNING_SCAN_DAYS)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    print(run_pipeline(
        asof=args.asof,
        phase="hold" if args.skip_scan else args.phase,
        scan_days=0 if args.skip_scan else args.backfill_days,
        skip_soft=args.skip_soft,
        slot=args.slot,
    ))


if __name__ == "__main__":
    main()
