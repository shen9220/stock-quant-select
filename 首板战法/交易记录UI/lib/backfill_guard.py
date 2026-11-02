"""历史回填暂停检查 — 见 00_项目必读/行情数据拉取规范.md"""
from __future__ import annotations

import os
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_LIB_DIR)  # 交易记录UI
PAUSE_FILE = os.path.join(_BASE, "data", "BACKFILL_PAUSED")
SPEC_DOC = "00_项目必读/行情数据拉取规范.md"


def backfill_paused() -> bool:
    return os.path.isfile(PAUSE_FILE)


def assert_backfill_allowed() -> None:
    if not backfill_paused():
        return
    reason = ""
    try:
        with open(PAUSE_FILE, "r", encoding="utf-8") as f:
            reason = f.read().strip()
    except OSError:
        pass
    print(
        f"[backfill] 已暂停：存在 {PAUSE_FILE}\n"
        f"请先阅读 {SPEC_DOC}，确认安全后删除暂停文件再执行。\n"
        f"{reason}",
        file=sys.stderr,
    )
    sys.exit(2)
