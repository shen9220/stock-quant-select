#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V1 原 results 按日期回补（倒序）。

铁律：
- 必须 --date / 脚本内传 D0，基因趋势 as_of=D0，同日可复现
- 默认 --results-only，不覆盖 trades（5–8 月 xlsx Top2 保留）
- 东财等走 platform_gate；触软上限立即停，次日续跑
- 已有 md 默认跳过（FORCE=1 可重写）

用法：
  python3 backfill_v1_results_by_date.py --start 2026-01-01 --end 2026-10-30
  python3 backfill_v1_results_by_date.py --limit 3
  FORCE=1 python3 backfill_v1_results_by_date.py --buy-date 2026-10-09
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timedelta

_SCRIPT = os.path.dirname(os.path.abspath(__file__))
_PROG = os.path.dirname(_SCRIPT)
_ROOT = os.path.dirname(os.path.dirname(_PROG))  # 首板战法
_UI_LIB = os.path.join(_ROOT, "交易记录UI", "lib")
for _p in (_PROG, _ROOT, _UI_LIB, os.path.join(_ROOT, "交易记录UI")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import paths as pkg_paths  # noqa: E402
import price_service as ps  # noqa: E402
import stock as stock_mod  # noqa: E402

try:
    from platform_gate import PlatformQuotaExceeded  # noqa: E402
except Exception:  # pragma: no cover
    class PlatformQuotaExceeded(RuntimeError):
        pass

CURSOR = os.path.join(_PROG, "cache", "v1_results_backfill_cursor.json")
os.makedirs(os.path.dirname(CURSOR), exist_ok=True)


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _load_cursor() -> dict:
    if not os.path.isfile(CURSOR):
        return {}
    try:
        import json
        with open(CURSOR, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cursor(state: dict) -> None:
    import json
    tmp = CURSOR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CURSOR)


def list_d0_dates(start: str, end: str) -> list[str]:
    """买入日窗口 → 对应 D0 列表（倒序）。"""
    buys: list[str] = []
    d = end
    guard = 0
    while d and d >= start and guard < 400:
        guard += 1
        buys.append(d)
        d = ps.prev_trading_day(d)

    d0s: list[str] = []
    seen: set[str] = set()
    for buy in buys:
        d0 = ps.prev_trading_day(buy)
        if not d0 or d0 in seen:
            continue
        seen.add(d0)
        d0s.append(d0)
    return d0s


def md_path_for_d0(d0: str) -> str:
    return pkg_paths.recommendation_md("stock", d0)


def main() -> int:
    ap = argparse.ArgumentParser(description="V1 results 按 D0 倒序回补")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="本批最多处理天数；0=不限天数，一直跑到配额软上限或补完（推荐）",
    )
    ap.add_argument("--sleep", type=float, default=2.0, help="日与日之间额外间隔秒")
    ap.add_argument("--force", action="store_true", help="已有 md 也重跑")
    ap.add_argument("--buy-date", default="", help="只跑某一个买入日")
    ap.add_argument("--date", default="", help="只跑某一个 D0")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--until-quota",
        action="store_true",
        default=True,
        help="触 platform_gate 软上限立即停（默认开）",
    )
    args = ap.parse_args()
    force = args.force or os.environ.get("FORCE", "0") == "1"

    if args.date:
        todo = [args.date.replace("/", "-")]
        if len(todo[0]) == 8 and todo[0].isdigit():
            todo = [f"{todo[0][:4]}-{todo[0][4:6]}-{todo[0][6:]}"]
    elif args.buy_date:
        d0 = ps.prev_trading_day(args.buy_date[:10])
        todo = [d0] if d0 else []
    else:
        todo = list_d0_dates(args.start, args.end)

    # 跳过已有
    pending = []
    for d0 in todo:
        path = md_path_for_d0(d0)
        if os.path.isfile(path) and not force:
            continue
        pending.append(d0)

    lim_txt = "∞(到配额停)" if args.limit <= 0 else str(args.limit)
    _log(f"待补 D0={len(pending)} / 扫描={len(todo)} force={force} limit={lim_txt}")
    if args.dry_run:
        show = pending if args.limit <= 0 else pending[: args.limit]
        for d0 in show[:30]:
            _log(f"  would {d0} → {os.path.basename(md_path_for_d0(d0))}")
        if len(show) > 30:
            _log(f"  ... 另有 {len(show) - 30} 日")
        return 0

    def _quota_hint() -> str:
        try:
            from platform_gate import quota_status
            snap = quota_status()
            bits = []
            for p in ("eastmoney", "ths", "tencent"):
                s = (snap or {}).get(p) or {}
                bits.append(
                    f"{p}:h={s.get('hour_used')}/{s.get('hour_limit')} "
                    f"n={s.get('night_used')}/{s.get('night_limit')} "
                    f"d={s.get('day_used')}/{s.get('day_limit')}"
                )
            return " | ".join(bits)
        except Exception as e:
            return f"(quota n/a: {e})"

    def _any_critical_quota_exhausted() -> str | None:
        """涨停池双源（eastmoney+ths）小时/夜班软上限都耗尽才停；单源耗尽仍可回退。"""
        try:
            from platform_gate import quota_status
            snap = quota_status()
            dead = []
            for p in ("eastmoney", "ths"):
                s = snap.get(p) or {}
                why = None
                if s.get("stopped"):
                    why = f"stopped:{s.get('stop_reason')}"
                else:
                    for key, label in (
                        ("remaining_hour", "hour"),
                        ("remaining_night", "night"),
                        ("remaining_day", "day"),
                    ):
                        v = s.get(key)
                        if v is not None and v <= 0:
                            why = f"{label} soft-cap exhausted"
                            break
                if why:
                    dead.append(f"{p}:{why}")
            if len(dead) >= 2:
                return "; ".join(dead)
        except Exception:
            return None
        return None

    _log(f"配额快照 {_quota_hint()}")
    pre = _any_critical_quota_exhausted()
    if pre:
        _log(f"QUOTA_STOP(preflight): {pre} — 立刻停，等下一小时/夜班续")
        state = _load_cursor()
        state["quota_stop"] = pre
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_cursor(state)
        return 2

    done = 0
    state = _load_cursor()
    for d0 in pending:
        if args.limit > 0 and done >= args.limit:
            _log(f"本批上限 {args.limit}，停止；下次续跑")
            break
        pre = _any_critical_quota_exhausted()
        if pre:
            _log(f"QUOTA_STOP: {pre} — 立刻停；{_quota_hint()}")
            state["quota_stop"] = pre
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_cursor(state)
            return 2
        _log(f"=== D0={d0} results_only skip_fundamentals ===")
        try:
            res = stock_mod.analyze_zhangting_stocks(
                date=d0,
                results_only=True,
                skip_fundamentals=True,
            )
            ok = bool(res and res.get("ok"))
            path = md_path_for_d0(d0)
            exists = os.path.isfile(path)
            _log(f"  ok={ok} md={exists} {os.path.basename(path)}")
            state["last_d0"] = d0
            state["last_ok"] = ok
            state.pop("quota_stop", None)
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_cursor(state)
            done += 1
        except PlatformQuotaExceeded as e:
            # 单源东财触限且 ths 仍有余量：跳过该异常日逻辑已在 stock 侧消化；
            # 若仍冒泡到这里，仅当双源都尽才整批停。
            both = _any_critical_quota_exhausted()
            if both or "platform=ths" in str(e):
                _log(f"QUOTA_STOP: {e} — 立刻停，不超限；{_quota_hint()}")
                state["quota_stop"] = str(e)
                state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                _save_cursor(state)
                return 2
            _log(f"东财触限但 ths 仍可用，跳过本日继续: {e}")
            state["last_error"] = f"{d0}: {e}"
            _save_cursor(state)
            done += 1
            if args.sleep > 0:
                time.sleep(args.sleep)
            continue
        except Exception as e:
            # 嵌套库有时把配额包成普通异常
            msg = str(e)
            if "QUOTA_STOP" in msg or "PlatformQuotaExceeded" in type(e).__name__:
                both = _any_critical_quota_exhausted()
                if both or "platform=ths" in msg:
                    _log(f"QUOTA_STOP(wrapped): {e} — 立刻停；{_quota_hint()}")
                    state["quota_stop"] = msg
                    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    _save_cursor(state)
                    return 2
                _log(f"东财触限(wrapped)但 ths 仍可用，跳过本日继续: {e}")
                state["last_error"] = f"{d0}: {e}"
                _save_cursor(state)
                done += 1
                if args.sleep > 0:
                    time.sleep(args.sleep)
                continue
            _log(f"FAIL {d0}: {e}")
            traceback.print_exc()
            state["last_error"] = f"{d0}: {e}"
            _save_cursor(state)
            done += 1
        if args.sleep > 0:
            time.sleep(args.sleep)

    _log(f"本批完成 {done} 天；剩余约 {max(0, len(pending) - done)}；{_quota_hint()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
