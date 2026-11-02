#!/usr/bin/env python3
"""统计三条回填线每日产出（D0 写出天数）。"""
from __future__ import annotations

import os
import sys

_JOBS_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_JOBS_DIR)
_LIB_DIR = os.path.join(_BASE_DIR, "lib")
for _p in (_BASE_DIR, _LIB_DIR, _JOBS_DIR, os.path.dirname(_BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import BASE_DIR, ROOT_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import re
from pathlib import Path

LOGS = {
    "03倒序(无补价)": Path(__file__).resolve().parent / "data/historical_fetch_logs/baostock_scheduler.log",
    "16倒序(含补价)": Path(__file__).resolve().parent / "data/historical_fetch_logs/baostock_scheduler.log",
    "18正序(腾讯)": Path(__file__).resolve().parent / "data/historical_fetch_logs/stock_scheduler.log",
}

def tail_events(path: Path, n: int = 80) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def main():
    print("=" * 60)
    print("最近回填产出（看日志末段）")
    print("=" * 60)
    bs = tail_events(LOGS["03倒序(无补价)"], 120)
    for tag, pat in [
        ("03/16 baostock", r"=== baostock 回填开始|本次计划|结束 phase=|→20"),
        ("18:30 腾讯", r"stock 回填开始|正序|本次共处理"),
    ]:
        print(f"\n--- {tag} ---")
        rx = re.compile(pat)
        for ln in bs if "baostock" in tag else tail_events(LOGS["18正序(腾讯)"], 80):
            if rx.search(ln):
                print(ln)
    print("\n目标：倒序 3–5 + 正序 1–3 ≈ 10 天/日")
    print("四批（全局互斥，一次只跑一条线）：")
    print("  07:00 A 纯补价 | 11:00 B Baostock倒序写明细(3天) | 15:00 C 腾讯正序(2天) | 19:00 D 补价+THS查缺")
    print("  Amphetamine: 04:00 开 → 21:30 关")


if __name__ == "__main__":
    main()
