#!/usr/bin/env python3
"""全局回填互斥锁：macOS 无 flock，用 fcntl。已有任务在跑则 exit 0 跳过。"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(BASE, "data", "stock_backfill_master.lock")
LOG = os.path.join(BASE, "data", "historical_fetch_logs", "master_lock.log")


def _log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = f"[{__import__('datetime').datetime.now():%F %T}] {msg}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: backfill_master_lock.py <slot> <command...>", file=sys.stderr)
        return 2
    slot = sys.argv[1]
    cmd = sys.argv[2:]
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log(f"SKIP slot={slot} — 另一线路运行中")
        return 0
    _log(f"LOCK slot={slot} pid={os.getpid()} cmd={' '.join(cmd[:3])}...")
    try:
        return subprocess.call(cmd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        _log(f"UNLOCK slot={slot}")


if __name__ == "__main__":
    raise SystemExit(main())
