"""
矫正脚本：卖出两批独立滚动规则（用户 2026-10-16 确认）。

铁律（见 IRON_RULES.md「卖出两批独立滚动」）：
    D1 买入 → D2 起分两批卖：早盘批在 9:35，午盘批在 13:15。
    两批**完全独立**判断、独立顺延，互不影响：
      - 早盘批 9:35 遇涨停/跌停 → 顺延到下一交易日的 9:35；
      - 午盘批 13:15 遇涨停/跌停 → 顺延到下一交易日的 13:15。
    顺延后仍落在原本时点。午盘涨停不得牵连早盘批（旧 bug 会整天滚动）。

旧 bug：resolve_sell_day_prices 里 `if zt_1315: 整日 roll`，
    把本来 9:35 就能卖的早盘批一起拖到后面，早盘卖出价被写成更晚日期的价格。
    事故样本：605058 澳弘电子（stock_updata / D1=10-12）
        旧：sell_price_935=65.04（10-16）
        新：早盘批 10-13 9:35 = 56.41；午盘批 10-16 13:15 = 66.69。

判定需要重算的记录（避免无谓拉取全量分钟数据）：
    1. 有卖出价，且卖出路径中出现「滚动」痕迹（skip_reason 含涨停/跌停/滚到）；
    2. 早盘卖出价等于午盘卖出价（常见于整日滚动后两批同价）；
    3. 早盘卖出价等于其成交日 13:15 的价格（说明早盘批被午盘绑架）；
    4. --force-all：凡有卖出价一律重算。

用法：
    python3 fix_sell_roll.py --dry-run --all
    python3 fix_sell_roll.py --all
    python3 fix_sell_roll.py --program stock --since 2026-09-01 --force-all
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
import time

BASE = BASE_DIR
ROOT = os.path.dirname(BASE)
for p in (ROOT, BASE):
    if p not in sys.path:
        sys.path.insert(0, p)

import paths  # noqa: E402
import price_service as ps  # noqa: E402

# 静音 akshare/tqdm 进度条噪声（本脚本日志需可读）
for _noisy in ("Akshare", "urllib3"):
    _lg = __import__("logging").getLogger(_noisy)
    _lg.setLevel(__import__("logging").WARNING)
try:  # tqdm 写 stderr，会污染重定向的输出
    import tqdm as _tqdm_mod

    _tqdm_mod.tqdm.__init__ = (lambda orig: (lambda self, *a, **k: orig(self, *a, **{**k, "disable": True})))(_tqdm_mod.tqdm.__init__)
except Exception:
    pass

from stock_UI import (  # noqa: E402
    _list_day_files,
    _load_day,
    _save_day,
    _safe_float,
    auto_calculate,
    enrich_trade_prices,
)

PROGRAMS = paths.PROGRAMS
DEFAULT_SLEEP = 0.8
PROGRESS_DIR = os.path.join(BASE, "data")

_ROLL_MARKERS = ("涨停", "跌停", "滚到", "卖不出", "跳过")
_NEXT_DAY_CACHE: dict[str, str | None] = {}


def _next_day(day: str) -> str | None:
    """带进程内缓存的下一交易日（判据里会大量重复调用）。"""
    if day not in _NEXT_DAY_CACHE:
        _NEXT_DAY_CACHE[day] = ps.next_trading_day(day)
    return _NEXT_DAY_CACHE[day]


def _progress_path(program: str) -> str:
    os.makedirs(PROGRESS_DIR, exist_ok=True)
    return os.path.join(PROGRESS_DIR, f"fix_sell_roll_{program}.json")


def _load_progress(program: str) -> set:
    path = _progress_path(program)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("done", []))
    except (json.JSONDecodeError, OSError, ValueError):
        return set()


def _save_progress(program: str, done: set) -> None:
    path = _progress_path(program)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done)}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _needs_fix(t: dict, force_all: bool) -> bool:
    """是否可能受「两批独立滚动」规则影响。"""
    if "买不进" in str(t.get("skip_reason") or ""):
        return False
    code = str(t.get("stock_code") or "").zfill(6)
    buy_day = str(t.get("date") or "")
    s935 = _safe_float(t.get("sell_price_935"))
    s1315 = _safe_float(t.get("sell_price_1315"))
    if s935 is None and s1315 is None:
        return False
    if force_all:
        return True
    # 1) 有滚动痕迹
    sr = str(t.get("skip_reason") or "")
    if any(m in sr for m in _ROLL_MARKERS):
        return True
    # 2) 两批同价（整日滚动后常出现）
    if s935 is not None and s1315 is not None and abs(s935 - s1315) < 0.015:
        return True
    # 3) 已记录批次成交日时，早盘成交日晚于/等于午盘成交日 → 必被绑架过
    d935 = str(t.get("sell_date_935") or "")
    d1315 = str(t.get("sell_date_1315") or "")
    if d935 and d1315 and d935 >= d1315 and s935 is not None:
        return True
    # 4) 早盘价 == 卖出日 13:15 价 → 早盘批被午盘价顶替
    #    （只在两批同价时才去拉分钟数据，否则开销过大）
    sd = str(t.get("sell_date") or "")
    if sd and code and s935 is not None and s1315 is not None and abs(s935 - s1315) < 0.015:
        p1315 = ps.get_minute_price(code, sd, "13:15")
        if p1315 is not None and abs(s935 - p1315) < 0.015:
            return True
    # 5) 【核心判据】早盘批成交日晚于 D2 → 说明早盘本可在 D2 卖出却被拖后。
    #    旧代码把两批捆在一起整天滚动，skip_reason 事后被清空，只能靠日期发现。
    #    仅在 sell_date 明显晚于 D2 时才去拉分钟数据，避免无谓的网络开销。
    if buy_day and code and s935 is not None and sd:
        d2 = _next_day(buy_day)
        if d2 and str(sd) > str(d2):
            p_d2_935 = ps.get_minute_price(code, d2, "09:35")
            prev_d2 = ps.get_prev_close(code, d2)
            # D2 早盘本来可卖（非涨跌停）→ 早盘批被无谓拖后
            if (
                p_d2_935 is not None
                and prev_d2 is not None
                and not ps.is_at_limit_up(p_d2_935, prev_d2, code)
                and not ps.is_at_limit_down(p_d2_935, prev_d2, code)
            ):
                return True
    return False


def fix_program(
    program: str,
    *,
    dry_run: bool,
    sleep_s: float,
    since: str | None,
    force_all: bool,
) -> dict:
    stats = {"checked": 0, "fixed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    done = _load_progress(program)
    for fname in _list_day_files(program):
        day = fname[:-5]
        if since and day < since:
            continue
        trades = _load_day(day, program)
        changed = False
        for idx, t in enumerate(trades):
            stats["checked"] += 1
            if not _needs_fix(t, force_all):
                stats["skipped"] += 1
                continue
            code = str(t.get("stock_code") or "").zfill(6)
            if not code:
                stats["skipped"] += 1
                continue
            # 断点续跑：已处理过且非 force 的组合跳过
            tag = f"{day}:{t.get('id')}"
            if tag in done and not force_all:
                stats["unchanged"] += 1
                continue

            old_935 = _safe_float(t.get("sell_price_935"))
            old_1315 = _safe_float(t.get("sell_price_1315"))
            old_sd = t.get("sell_date")
            time.sleep(sleep_s)
            # baostock 是权威口径。被服务端限流/断连时重试并退避，
            # 连续失败达阈值则**整体长退避**，避免硬冲导致 IP 被封。
            nt = None
            consec_fail = 0
            for attempt in range(3):
                try:
                    nt = enrich_trade_prices(dict(t), force=True)
                except Exception as e:
                    print(f"  FAIL(try{attempt + 1}) {program} {day} {code}: {e}", flush=True)
                    time.sleep(2.0 * (attempt + 1))
                    continue
                new_try_935 = _safe_float(nt.get("sell_price_935"))
                # 早盘日期会变（规则修正），但若新价缺失而旧价存在 → 疑似行情源失败
                if new_try_935 is None and old_935 is not None:
                    print(f"  RETRY {program} {day} {code}: 早盘价缺失(行情源可能失败)", flush=True)
                    time.sleep(2.0 * (attempt + 1))
                    continue
                break
            if nt is None:
                consec_fail += 1
                stats["failed"] += 1
                if consec_fail >= 3:
                    wait = 180
                    print(
                        f"  !! baostock 连续 {consec_fail} 次失败，长退避 {wait}s 等待限流恢复"
                        f"（继续硬冲有封禁风险）",
                        flush=True,
                    )
                    time.sleep(wait)
                    consec_fail = 0
                continue
            consec_fail = 0

            new_935 = _safe_float(nt.get("sell_price_935"))
            new_1315 = _safe_float(nt.get("sell_price_1315"))

            # 行情源失败时 enrich 会把卖出价清成 None。历史数据宁可保持旧值，
            # 也不能因为一次网络抖动把有效成交价抹掉 —— 直接放弃本条，不写盘。
            if old_935 is not None and new_935 is None:
                print(
                    f"  SKIP {program} {day} {code} {t.get('stock_name')}: "
                    f"行情源未取到早盘价(旧值 {old_935})，保留原值不写盘",
                    flush=True,
                )
                stats["failed"] += 1
                continue
            if old_1315 is not None and new_1315 is None:
                print(
                    f"  SKIP {program} {day} {code} {t.get('stock_name')}: "
                    f"行情源未取到午盘价(旧值 {old_1315})，保留原值不写盘",
                    flush=True,
                )
                stats["failed"] += 1
                continue

            def eq(a, b):
                if a is None and b is None:
                    return True
                if a is None or b is None:
                    return False
                return abs(a - b) < 0.015

            same = (
                eq(old_935, new_935)
                and eq(old_1315, new_1315)
                and str(old_sd or "") == str(nt.get("sell_date") or "")
            )
            if same:
                stats["unchanged"] += 1
                done.add(tag)
                continue

            print(
                f"  FIX {program} {day} {code} {t.get('stock_name')} | "
                f"早盘 {old_935}({t.get('sell_date_935') or old_sd}) -> "
                f"{new_935}({nt.get('sell_date_935')}) | "
                f"午盘 {old_1315}({t.get('sell_date_1315') or old_sd}) -> "
                f"{new_1315}({nt.get('sell_date_1315')}) | "
                f"sell_date {old_sd} -> {nt.get('sell_date')}",
                flush=True,
            )
            trades[idx] = nt
            changed = True
            stats["fixed"] += 1
            done.add(tag)
        if changed and not dry_run:
            _save_day(day, trades, program)
    if not dry_run:
        _save_progress(program, done)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", choices=PROGRAMS, default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    ap.add_argument("--since", default="2026-06-01", help="只处理买入日≥该日")
    ap.add_argument("--force-all", action="store_true", help="凡有卖出价一律重算")
    args = ap.parse_args()
    progs = list(PROGRAMS) if args.all else ([args.program] if args.program else ["stock"])
    total = {"checked": 0, "fixed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    for prog in progs:
        print(f"\n===== {prog} since={args.since} dry_run={args.dry_run} =====", flush=True)
        s = fix_program(
            prog,
            dry_run=args.dry_run,
            sleep_s=args.sleep,
            since=args.since,
            force_all=args.force_all,
        )
        for k in total:
            total[k] += s[k]
        print(
            f"[{prog}] checked={s['checked']} fixed={s['fixed']} "
            f"unchanged={s['unchanged']} skipped={s['skipped']} failed={s['failed']}",
            flush=True,
        )
    print("\n===== 汇总 =====")
    print(total, flush=True)
    if args.dry_run:
        print("（dry-run，未写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
