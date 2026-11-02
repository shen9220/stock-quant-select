"""
历史价格一次性补全（单条串行，严守补数据纪律）。

铁律（补数据纪律）：
  - **只能单条串行调接口，严禁批量/并行/并发**；
  - 每次网络请求一个一个来，两次请求之间留间隔（默认 1.2s，可 --sleep 调）；
  - 免费数据源对频率敏感，批量/高频/并行极易触发限流甚至 IP 拉黑。

流程：
  1. 扫描全部 program 的历史记录，找出缺买入价 / 缺卖价的条目；
  2. 逐条调用 enrich_trade_prices(force=False) 补空字段；
  3. 补完后写盘（atomic_write），只改价格相关字段，其它字段不动。

用法：
  python3 backfill_prices.py --dry-run            # 只看会补哪些
  python3 backfill_prices.py --program stock      # 只补 stock
  python3 backfill_prices.py --all                # 全部 program
  python3 backfill_prices.py --all --sleep 1.5
  python3 backfill_prices.py --program stock --board-rank-only --buy-only --start 2026-01-01 --limit 40
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
import os
import sys
import time
from datetime import datetime

BASE = BASE_DIR
ROOT = os.path.dirname(BASE)
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)

import paths  # noqa: E402
from stock_UI import (  # noqa: E402
    _list_day_files,
    _load_day,
    _save_day,
    _safe_float,
    enrich_trade_prices,
)

PROGRAMS = paths.PROGRAMS
DEFAULT_SLEEP = 1.2

_BUY_KEYS = ("buy_price",)
_SELL_KEYS = (
    "sell_price_935", "sell_price_1315",
    "sell_price_half_935", "sell_price_half_1315",
)


def _missing(t: dict, *, buy_only: bool = False) -> list[str]:
    """返回缺失的价格字段名列表（空列表 = 完整）。"""
    keys = _BUY_KEYS if buy_only else (_BUY_KEYS + _SELL_KEYS)
    miss = []
    for k in keys:
        if _safe_float(t.get(k)) is None:
            miss.append(k)
    return miss


def _is_skipped(t: dict) -> bool:
    return "买不进" in str(t.get("skip_reason") or "")


def _deadline_passed(until: str | None) -> bool:
    if not until:
        return False
    now = datetime.now()
    try:
        if len(until) <= 5:
            hh, mm = map(int, until.split(":"))
            return (now.hour, now.minute) >= (hh, mm)
        return now >= datetime.strptime(until, "%Y-%m-%d %H:%M")
    except Exception:
        return False


def scan(
    program: str,
    *,
    start: str = "",
    board_rank_only: bool = False,
    buy_only: bool = False,
):
    """找出需要补的 (day, trades, indices)。倒序（新→旧）。"""
    todo = []
    for fname in sorted(_list_day_files(program), reverse=True):
        day = fname[:-5]
        if start and day < start:
            continue
        trades = _load_day(day, program)
        idxs = []
        for i, t in enumerate(trades):
            if _is_skipped(t):
                continue
            if not str(t.get("stock_code") or "").strip():
                continue
            if board_rank_only and t.get("board_rank") is None:
                continue
            if not _missing(t, buy_only=buy_only):
                continue
            idxs.append(i)
        if idxs:
            todo.append((day, trades, idxs))
    return todo


def fill_program(
    program: str,
    *,
    dry_run: bool,
    sleep_s: float,
    limit: int = 0,
    start: str = "",
    board_rank_only: bool = False,
    buy_only: bool = False,
    until: str = "",
) -> dict:
    stats = {"todo": 0, "filled": 0, "partial": 0, "failed": 0, "unchanged": 0}
    todo = scan(
        program, start=start, board_rank_only=board_rank_only, buy_only=buy_only)
    total = sum(len(idxs) for _, _, idxs in todo)
    stats["todo"] = total
    mode = []
    if board_rank_only:
        mode.append("board_rank")
    if buy_only:
        mode.append("buy_only")
    print(
        f"\n===== {program}：待补 {total} 笔（{len(todo)} 日） "
        f"mode={'+'.join(mode) or 'all'} start={start or '-'} until={until or '-'} =====",
        flush=True,
    )

    if total == 0 or dry_run:
        if dry_run:
            shown = 0
            for day, trades, idxs in todo:
                for i in idxs:
                    t = trades[i]
                    print(
                        f"  would {day} {t.get('stock_code')} {t.get('stock_name')} "
                        f"br={t.get('board_rank')} miss={_missing(t, buy_only=buy_only)}",
                        flush=True,
                    )
                    shown += 1
                    if shown >= 20:
                        print("  …", flush=True)
                        return stats
        return stats

    processed = 0
    for day, trades, idxs in todo:
        if _deadline_passed(until or None):
            print(f"到达硬停 {until}，收工（已处理 {processed}）", flush=True)
            break
        changed = False
        for i in idxs:
            if limit and processed >= limit:
                break
            if _deadline_passed(until or None):
                break
            t = trades[i]
            code = str(t.get("stock_code") or "").zfill(6)
            name = t.get("stock_name")
            before_missing = _missing(t, buy_only=buy_only)
            old_buy = _safe_float(t.get("buy_price"))
            old_935 = _safe_float(t.get("sell_price_935"))
            old_1315 = _safe_float(t.get("sell_price_1315"))

            time.sleep(sleep_s)
            try:
                nt = enrich_trade_prices(dict(t), force=False)
            except Exception as e:
                stats["failed"] += 1
                print(f"  FAIL {program} {day} {code} {name}: {e}", flush=True)
                processed += 1
                continue

            after_missing = _missing(nt, buy_only=buy_only)
            trades[i] = nt

            if after_missing and len(after_missing) < len(before_missing):
                stats["partial"] += 1
                changed = True
                print(
                    f"  PARTIAL {program} {day} {code} {name} "
                    f"缺{len(before_missing)}→缺{len(after_missing)} "
                    f"buy={old_buy}→{_safe_float(nt.get('buy_price'))} "
                    f"935={old_935}→{_safe_float(nt.get('sell_price_935'))} "
                    f"1315={old_1315}→{_safe_float(nt.get('sell_price_1315'))} "
                    f"sell_date={nt.get('sell_date')} "
                    f"仍缺={after_missing}",
                    flush=True,
                )
            elif after_missing:
                stats["unchanged"] += 1
                print(
                    f"  HOLD    {program} {day} {code} {name} 仍缺={after_missing} "
                    f"（取不到分钟价，留待后续补）",
                    flush=True,
                )
            else:
                stats["filled"] += 1
                changed = True
                print(
                    f"  FILL    {program} {day} {code} {name} "
                    f"buy={old_buy}→{_safe_float(nt.get('buy_price'))} "
                    f"935={old_935}→{_safe_float(nt.get('sell_price_935'))} "
                    f"1315={old_1315}→{_safe_float(nt.get('sell_price_1315'))} "
                    f"sell_date={nt.get('sell_date')} "
                    f"src={nt.get('sell_price_source')}",
                    flush=True,
                )
            processed += 1
        if changed:
            _save_day(day, trades, program)
        if limit and processed >= limit:
            break
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", choices=PROGRAMS, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少笔（0=不限）")
    ap.add_argument("--start", default="", help="起始买入日 YYYY-MM-DD（含）")
    ap.add_argument("--board-rank-only", action="store_true",
                    help="只补带 board_rank 的板块 tab 记录")
    ap.add_argument("--buy-only", action="store_true",
                    help="只要求买价齐（卖价缺分钟源时用）")
    ap.add_argument("--until", default="", help="硬停 HH:MM 或 YYYY-MM-DD HH:MM")
    args = ap.parse_args()

    progs = list(PROGRAMS) if args.all else ([args.program] if args.program else ["stock"])
    total = {"todo": 0, "filled": 0, "partial": 0, "failed": 0, "unchanged": 0}
    for prog in progs:
        s = fill_program(
            prog,
            dry_run=args.dry_run,
            sleep_s=args.sleep,
            limit=args.limit,
            start=args.start,
            board_rank_only=args.board_rank_only,
            buy_only=args.buy_only,
            until=args.until,
        )
        for k in total:
            total[k] += s[k]
        print(
            f"[{prog}] todo={s['todo']} filled={s['filled']} partial={s['partial']} "
            f"hold={s['unchanged']} failed={s['failed']}",
            flush=True,
        )
    print(f"\n===== 汇总 {datetime.now():%Y-%m-%d %H:%M:%S} =====")
    print(total)
    if args.dry_run:
        print("（dry-run，未写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
