#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出并同步交易 UI 到 GitHub Pages（docs/trade-ui）。

用法:
  python jobs/sync_trade_ui_pages.py
  python jobs/sync_trade_ui_pages.py --no-push
"""
from __future__ import annotations

import argparse
import os
import sys

_JOBS = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_JOBS)
_LIB = os.path.join(_BASE, "lib")
for p in (_BASE, _LIB, os.path.dirname(_BASE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from runtime_paths import ensure_sys_path  # noqa: E402

ensure_sys_path()

from pages_sync import sync_pages_now  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="同步交易 UI 到 GitHub Pages")
    ap.add_argument("--no-push", action="store_true", help="只导出，不 git push")
    ap.add_argument("--push", action="store_true", help="强制 push（默认跟环境变量）")
    args = ap.parse_args()
    push = False if args.no_push else (True if args.push else None)
    summary = sync_pages_now(push=push)
    print(summary)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
