#!/usr/bin/env python3
"""读取回填配额：probe≈7天/日，stable≈10天/日。"""
from __future__ import annotations

import json
import os
import sys

CFG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "backfill_quota_phase.json")

DEFAULT = {
    "phase": "probe",
    "probe": {"baostock_reverse": 3, "tencent_forward": 2, "ths_gap": 2},
    "stable": {"baostock_reverse": 4, "tencent_forward": 3, "ths_gap": 3},
}


def load() -> dict:
    if os.path.isfile(CFG):
        with open(CFG, encoding="utf-8") as f:
            d = json.load(f)
        for k, v in DEFAULT.items():
            d.setdefault(k, v)
        return d
    return dict(DEFAULT)


def days_for(slot: str) -> int:
    d = load()
    phase = d.get("phase", "probe")
    block = d.get(phase, d.get("probe", DEFAULT["probe"]))
    return int(block.get(slot, 2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps(load(), ensure_ascii=False, indent=2))
        sys.exit(0)
    print(days_for(sys.argv[1]))
