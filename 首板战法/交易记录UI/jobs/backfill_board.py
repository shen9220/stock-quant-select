"""
交易记录「板块（board）」字段回填。

用途：给历史交易记录补上 `board` 字段（创业板/主板/科创板/北交所）。
板块由**股票代码**判定（stock_meta.board_of_code），不信任任何手工值。

用法：
    /usr/local/bin/python3 交易记录UI/backfill_board.py            # 全部程序
    /usr/local/bin/python3 交易记录UI/backfill_board.py --dry-run  # 只看统计不写盘
    /usr/local/bin/python3 交易记录UI/backfill_board.py --program stock

写盘约定：
  - 只改 board 字段，其它字段一律不动（不重排、不重写价格）。
  - 板块本来就对的记录跳过（幂等，可重复跑）。
  - 写盘用 atomic_write + 唯一 tmp 文件名（与 stock_UI 一致）。
  - 逐文件处理，不并发（免费数据源纪律）。
"""
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

import argparse
import json
import os
import sys
import threading
from collections import Counter


import paths  # noqa: E402
import stock_meta  # noqa: E402


def _atomic_write(path: str, data: list) -> None:
    """与 stock_UI._atomic_write 同口径：唯一 tmp + fsync + os.replace。"""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def backfill_program(program: str, dry_run: bool = False) -> dict:
    trades_dir = paths.program_trades_dir(program)
    stats = Counter()
    boards = Counter()

    files = sorted(f for f in os.listdir(trades_dir) if f.endswith(".json"))
    for fname in files:
        path = os.path.join(trades_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [跳过] {fname}: {e}")
            stats["read_error"] += 1
            continue
        if not isinstance(data, list):
            stats["not_list"] += 1
            continue

        changed = False
        for t in data:
            if not isinstance(t, dict):
                continue
            stats["total"] += 1
            code = str(t.get("stock_code") or "").strip()
            board = stock_meta.board_of_code(code)
            if not board:
                stats["no_code"] += 1
                boards["(无法判定)"] += 1
                continue
            boards[board] += 1
            if t.get("board") == board:
                stats["already_ok"] += 1
                continue
            if t.get("board"):
                stats["corrected"] += 1   # 原值与代码判定不一致 → 纠正
            else:
                stats["filled"] += 1      # 原本缺字段 → 补上
            t["board"] = board
            changed = True

        if changed and not dry_run:
            _atomic_write(path, data)

    return {"stats": stats, "boards": boards, "files": len(files)}


def main() -> int:
    ap = argparse.ArgumentParser(description="回填交易记录的板块（board）字段")
    ap.add_argument("--program", default="", help="只处理指定程序，默认全部")
    ap.add_argument("--dry-run", action="store_true", help="只看统计，不写盘")
    args = ap.parse_args()

    programs = [args.program] if args.program else list(paths.PROGRAMS)
    grand = Counter()
    all_boards = Counter()

    for prog in programs:
        if prog not in paths.PROGRAMS:
            print(f"✗ 未知程序: {prog}")
            return 1
        print(f"\n=== {prog} ===")
        res = backfill_program(prog, dry_run=args.dry_run)
        s, b = res["stats"], res["boards"]
        grand.update(s)
        all_boards.update(b)
        print(f"  文件 {res['files']} 个，记录 {s['total']} 条")
        print(f"  补上 {s['filled']} / 纠正 {s['corrected']} / 已正确 {s['already_ok']}"
              f" / 无代码 {s['no_code']} / 读错 {s['read_error']}")
        for k, v in b.most_common():
            print(f"    {k:12} {v:5} 条")

    print("\n=== 合计 ===")
    print(f"  记录 {grand['total']} 条")
    print(f"  补上 {grand['filled']} / 纠正 {grand['corrected']} / 已正确 {grand['already_ok']}"
          f" / 无代码 {grand['no_code']} / 读错 {grand['read_error']}")
    for k, v in all_boards.most_common():
        print(f"    {k:12} {v:5} 条")
    if args.dry_run:
        print("\n[dry-run] 未写盘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
