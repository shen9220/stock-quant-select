#!/usr/bin/env python3
"""
stock × Baostock 回填调度器 — 优先倒序（7 月→1 月），倒序完成后继续正序。

铁纪律:
  - 仅本调度器允许 baostock（入口脚本 unset DISABLE_BAOSTOCK）
  - 单进程、单次 login、串行扫市、禁止 workers>1
  - 默认 3 天/次；试探期固定 3；有压力→2→1；无压力→升至 4
  - 已有买入日 JSON 跳过；独立锁，不与 17:00 抢同一 lock
  - 黑名单/封禁 → 写 BAOSTOCK_PAUSED 并停跑，禁止重试轰炸

用法:
  python3 baostock_backfill_scheduler.py --status
  python3 baostock_backfill_scheduler.py --run
  python3 baostock_backfill_scheduler.py --run --days 3
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
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta

for p in (ROOT_DIR, BASE_DIR, os.path.join(ROOT_DIR, "stock", "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 本任务必须允许 baostock；若外部环境误设为 1，入口脚本应已 unset
if os.environ.get("DISABLE_BAOSTOCK", "").lower() in ("1", "true", "yes"):
    print("[baostock-sched] DISABLE_BAOSTOCK=1，拒绝运行（请用 16:00 入口脚本）")
    sys.exit(2)

import fetch_calendar as fc  # noqa: E402
import paths  # noqa: E402
import price_service as ps  # noqa: E402
from baostock_safe_scan import BaostockBannedError, scan_first_board_day  # noqa: E402

STATE_FILE = os.path.join(BASE_DIR, "data", "baostock_backfill_schedule.json")
LOCK_FILE = os.path.join(BASE_DIR, "data", "baostock_backfill.lock")
PAUSE_FILE = os.path.join(BASE_DIR, "data", "BAOSTOCK_PAUSED")
LOG_FILE = os.path.join(BASE_DIR, "data", "historical_fetch_logs", "baostock_scheduler.log")

START_DATE = "2025-01-02"
END_DATE = "2025-12-31"
REVERSE_START = "2025-01-01"
REVERSE_END = "2025-07-31"


def _build_reverse_weeks() -> list[list[str]]:
    """1–7 月交易日按自然周倒序（周五截断）。"""
    cal = fc.load_calendar(REVERSE_START, REVERSE_END)
    weeks: list[list[str]] = []
    wk: list[str] = []
    for d in cal:
        wk.append(d)
        if datetime.strptime(d, "%Y-%m-%d").weekday() == 4:
            weeks.append(wk)
            wk = []
    if wk:
        weeks.append(wk)
    return list(reversed(weeks))


REVERSE_WEEKS_JAN_JUL = _build_reverse_weeks()

DEFAULT_STATE = {
    "phase": "reverse",  # reverse（优先）| forward | done
    "priority": "reverse_first",
    "cursor_d0": START_DATE,  # forward 用
    "forward_cursor_saved": None,
    "reverse_week_index": 0,
    "reverse_day_offset": 0,
    "completed_d0": [],
    "max_days_per_run": 3,
    "bs_interval_sec": 0.15,
    "enrich_interval_sec": 0.4,
    "last_run": None,
    "last_error": None,
    "adaptive": {
        "enabled": True,
        "probe_runs_remaining": 0,
        "current_quota": 3,
        "target_quota": 4,
        "min_quota": 1,
        "clean_runs": 0,
        "runs_for_upgrade": 2,
        "last_run_metrics": None,
    },
    "risk": {
        "consecutive_failures": 0,
        "paused_until": None,
        "pressure_events": 0,
        "banned": False,
    },
}


def _log(msg: str) -> None:
    line = f"[{datetime.now():%F %T}] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_state() -> dict:
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        for k, v in DEFAULT_STATE.items():
            if k not in s:
                s[k] = json.loads(json.dumps(v))
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    s[k].setdefault(kk, vv)
    else:
        s = json.loads(json.dumps(DEFAULT_STATE))

    # 一次性迁移：旧正序任务改为倒序优先，保留正序游标以便倒序完成后继续
    if not s.get("migrated_to_reverse_first"):
        s["forward_cursor_saved"] = s.get("cursor_d0") or START_DATE
        s["phase"] = "reverse"
        s["reverse_week_index"] = 0
        s["reverse_day_offset"] = 0
        s["priority"] = "reverse_first"
        s["migrated_to_reverse_first"] = True
        _log(
            f"迁移：Baostock 改为倒序优先（保存正序游标 {s['forward_cursor_saved']}）"
        )
    return s


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _write_pause(reason: str) -> None:
    os.makedirs(os.path.dirname(PAUSE_FILE), exist_ok=True)
    with open(PAUSE_FILE, "w", encoding="utf-8") as f:
        f.write(
            f"# Baostock 回填已紧急暂停 — {datetime.now():%F %T}\n"
            f"# 原因: {reason}\n"
            f"# 恢复前请确认未再触发黑名单，删除本文件后再跑。\n"
        )


def _paused() -> str | None:
    if os.path.isfile(PAUSE_FILE):
        try:
            return open(PAUSE_FILE, encoding="utf-8").read().strip()
        except OSError:
            return "BAOSTOCK_PAUSED"
    return None


def _buy_date_exists(d0: str) -> bool:
    d1 = fc.next_trading_day(d0)
    if not d1:
        return False
    path = os.path.join(paths.program_trades_dir("stock"), f"{d1}.json")
    return os.path.isfile(path)


def _pending_days_forward(state: dict) -> list[str]:
    cal = fc.load_calendar(START_DATE, END_DATE)
    done = set(state.get("completed_d0") or [])
    cursor = state.get("cursor_d0") or START_DATE
    out = []
    for d in cal:
        if d < cursor:
            continue
        if d in done:
            continue
        if _buy_date_exists(d):
            continue
        out.append(d)
    return out


def _pending_days_reverse(state: dict) -> list[str]:
    """从当前倒序周偏移起，列出尚未有买入日文件的 D0。"""
    idx = int(state.get("reverse_week_index", 0))
    offset = int(state.get("reverse_day_offset", 0))
    out: list[str] = []
    for wi in range(idx, len(REVERSE_WEEKS_JAN_JUL)):
        week = REVERSE_WEEKS_JAN_JUL[wi]
        start = offset if wi == idx else 0
        for d0 in week[start:]:
            if not _buy_date_exists(d0):
                out.append(d0)
    return out


def _pending_days(state: dict) -> list[str]:
    phase = state.get("phase") or "reverse"
    if phase == "reverse":
        return _pending_days_reverse(state)
    if phase == "forward":
        return _pending_days_forward(state)
    return []


def _advance_reverse_cursor(state: dict, d0: str) -> None:
    """将倒序游标推进到 d0 之后（按周结构）。"""
    for wi, week in enumerate(REVERSE_WEEKS_JAN_JUL):
        if d0 not in week:
            continue
        pos = week.index(d0) + 1
        if pos >= len(week):
            state["reverse_week_index"] = wi + 1
            state["reverse_day_offset"] = 0
        else:
            state["reverse_week_index"] = wi
            state["reverse_day_offset"] = pos
        return


def _acquire_lock() -> int | None:
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def _effective_quota(state: dict, override: int = 0) -> int:
    if override > 0:
        return override
    ad = state.setdefault("adaptive", dict(DEFAULT_STATE["adaptive"]))
    q = int(ad.get("current_quota", 3))
    if ad.get("enabled") and ad.get("probe_runs_remaining", 0) > 0:
        q = 3  # 试探期固定 3 天
    return max(int(ad.get("min_quota", 1)), min(4, q))


def _run_metrics_start() -> dict:
    return {
        "started_at": time.time(),
        "planned_days": 0,
        "done_days": 0,
        "skipped_existing": 0,
        "queries_est": 0,
        "errors": 0,
        "pressure": False,
        "pressure_reasons": [],
    }


def _mark_pressure(metrics: dict, reason: str) -> None:
    metrics["pressure"] = True
    metrics.setdefault("pressure_reasons", []).append(reason)


def _adjust_quota_after_run(state: dict, metrics: dict) -> None:
    ad = state.setdefault("adaptive", dict(DEFAULT_STATE["adaptive"]))
    metrics["duration_sec"] = round(time.time() - metrics.get("started_at", time.time()), 1)
    ad["last_run_metrics"] = {
        k: metrics[k]
        for k in (
            "planned_days",
            "done_days",
            "skipped_existing",
            "errors",
            "pressure",
            "pressure_reasons",
            "duration_sec",
        )
        if k in metrics
    }

    if ad.get("probe_runs_remaining", 0) > 0:
        ad["probe_runs_remaining"] = max(0, ad["probe_runs_remaining"] - 1)
        ad["current_quota"] = 3
        state["max_days_per_run"] = 3
        _log(
            f"试探期剩余 {ad['probe_runs_remaining']} 次，配额保持 3 天/次 "
            f"(耗时 {metrics['duration_sec']}s, 完成 {metrics.get('done_days', 0)} 天)"
        )
        if ad["probe_runs_remaining"] == 0:
            _log("试探期结束，将按压力自动升降（1↔2↔3↔4）")
        return

    min_q = int(ad.get("min_quota", 1))
    target = int(ad.get("target_quota", 4))
    cur = int(ad.get("current_quota", 3))
    need_upgrade = int(ad.get("runs_for_upgrade", 2))

    if metrics.get("pressure") or metrics.get("errors", 0) > 0 or state.get("last_error"):
        ad["clean_runs"] = 0
        new_q = max(min_q, cur - 1)
        if new_q != cur:
            _log(
                f"⚠ 有压力 ({metrics.get('pressure_reasons') or state.get('last_error')})，"
                f"配额 {cur}→{new_q} 天/次"
            )
        ad["current_quota"] = new_q
        state["risk"]["pressure_events"] = state["risk"].get("pressure_events", 0) + 1
    else:
        ad["clean_runs"] = ad.get("clean_runs", 0) + 1
        if ad["clean_runs"] >= need_upgrade and cur < target:
            new_q = min(target, cur + 1)
            _log(f"✓ 连续 {ad['clean_runs']} 次无压力，配额 {cur}→{new_q} 天/次")
            ad["current_quota"] = new_q
            ad["clean_runs"] = 0
        else:
            _log(
                f"✓ 本次无压力，配额维持 {cur} 天/次 "
                f"(clean_runs={ad['clean_runs']}/{need_upgrade})"
            )

    state["max_days_per_run"] = ad["current_quota"]


def _process_day(d0: str, state: dict, universe: list, metrics: dict, *, enrich: bool = True) -> bool:
    from backfill_stock_year import rank_by_amount
    from stock_UI import _load_day, _save_day, enrich_trade_prices, upsert_from_stock_py

    phase = state.get("phase") or "reverse"

    if _buy_date_exists(d0):
        d1 = fc.next_trading_day(d0)
        trades = _load_day(d1, "stock") if d1 else []
        need_enrich = enrich and trades and any(
            t.get("buy_price") in (None, "", 0)
            or (
                t.get("sell_price_935") in (None, "", 0)
                and t.get("sell_price_1315") in (None, "", 0)
                and not (t.get("skip_reason") and "买不进" in str(t.get("skip_reason", "")))
            )
            for t in trades
        )
        if need_enrich:
            _log(f"  {d0} 已有记录，补价 {d1} …")
            for i, t in enumerate(trades):
                time.sleep(float(state.get("enrich_interval_sec", 0.4)))
                e = enrich_trade_prices(dict(t), force=False)
                e.pop("skip_buy", None)
                trades[i] = e
            _save_day(d1, trades, "stock")
        else:
            _log(f"  {d0} 已有记录，跳过（不扫 baostock）")
        metrics["skipped_existing"] = metrics.get("skipped_existing", 0) + 1
        state.setdefault("completed_d0", []).append(d0)
        if phase == "reverse":
            _advance_reverse_cursor(state, d0)
        else:
            state["cursor_d0"] = fc.next_trading_day(d0) or d0
        return True

    d1 = fc.next_trading_day(d0)
    if not d1:
        return True

    interval = float(state.get("bs_interval_sec", 0.15))
    _log(f"  {d0} baostock 串行扫市 universe={len(universe)} interval={interval}s enrich={enrich} …")

    def _prog(done: int, total: int, hits: int) -> None:
        _log(f"    进度 {done}/{total} 首板候选={hits}")

    first = scan_first_board_day(
        d0,
        universe,
        interval_sec=interval,
        on_progress=_prog,
    )
    metrics["queries_est"] = metrics.get("queries_est", 0) + len(universe)

    if not first:
        _log(f"  {d0} 无首板，记完成")
        state.setdefault("completed_d0", []).append(d0)
        if phase == "reverse":
            _advance_reverse_cursor(state, d0)
        else:
            state["cursor_d0"] = d1
        return True

    top2 = (first if "amount" in first[0] else rank_by_amount(first, d0))[:2]
    records = [
        {"date": d1, "stock_code": h["code"], "stock_name": h["name"], "rank": i}
        for i, h in enumerate(top2, 1)
    ]
    upsert_from_stock_py(records, auto_enrich=False, program="stock")
    _log(f"  {d0}→{d1} " + ", ".join(f"{r['stock_code']}{r['stock_name']}" for r in records))

    if enrich:
        trades = _load_day(d1, "stock")
        if trades:
            for i, t in enumerate(trades):
                time.sleep(float(state.get("enrich_interval_sec", 0.4)))
                e = enrich_trade_prices(dict(t), force=False)
                e.pop("skip_buy", None)
                trades[i] = e
            _save_day(d1, trades, "stock")
    else:
        _log(f"  {d1} 跳过补价")

    state.setdefault("completed_d0", []).append(d0)
    if phase == "reverse":
        _advance_reverse_cursor(state, d0)
    else:
        state["cursor_d0"] = d1
    return True


def print_status(state: dict) -> None:
    pending = _pending_days(state)
    ad = state.get("adaptive", {})
    print("=" * 60)
    print(
        f"阶段: {state.get('phase')} | priority={state.get('priority')} | 末次: {state.get('last_run')}"
    )
    if state.get("phase") == "reverse":
        idx = int(state.get("reverse_week_index", 0))
        print(f"倒序 week[{idx}] offset={state.get('reverse_day_offset', 0)}")
        if idx < len(REVERSE_WEEKS_JAN_JUL):
            print(f"  当前周: {REVERSE_WEEKS_JAN_JUL[idx]}")
        print(f"正序游标已保存: {state.get('forward_cursor_saved') or state.get('cursor_d0')}")
    else:
        print(f"正序 cursor: {state.get('cursor_d0')}")
    print(f"待处理约: {len(pending)} 天 | 已完成标记: {len(state.get('completed_d0') or [])}")
    if pending[:5]:
        print(f"下一批预览: {pending[:5]}")
    print(f"单次配额: {_effective_quota(state)} 天/次")
    print(
        f"  试探剩余: {ad.get('probe_runs_remaining', 0)} | "
        f"current={ad.get('current_quota')} target={ad.get('target_quota')} min={ad.get('min_quota')}"
    )
    lm = ad.get("last_run_metrics")
    if lm:
        print(
            f"  上次: {lm.get('done_days')}天 耗时{lm.get('duration_sec')}s "
            f"压力={lm.get('pressure')} skip={lm.get('skipped_existing')}"
        )
    print(
        f"风险: fail={state.get('risk', {}).get('consecutive_failures', 0)} "
        f"pressure_events={state.get('risk', {}).get('pressure_events', 0)} "
        f"banned={state.get('risk', {}).get('banned', False)}"
    )
    if os.path.isfile(PAUSE_FILE):
        print(f"⚠ 存在暂停文件: {PAUSE_FILE}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--days", type=int, default=0, help="覆盖本次天数配额")
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="只写明细不补价（03:00 可复用）",
    )
    args = parser.parse_args()

    state = _load_state()
    if args.status:
        print_status(state)
        return
    if not args.run:
        parser.print_help()
        return

    pause_reason = _paused()
    if pause_reason:
        _log(f"已暂停，拒绝运行。文件: {PAUSE_FILE}\n{pause_reason[:200]}")
        return

    if state.get("risk", {}).get("banned"):
        _log("state.risk.banned=true，拒绝运行（需人工确认后清 banned 并删 BAOSTOCK_PAUSED）")
        return

    paused_until = state.get("risk", {}).get("paused_until")
    if paused_until and paused_until > datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
        _log(f"风险暂停中 until {paused_until}")
        return

    if state["risk"].get("consecutive_failures", 0) >= 3:
        until = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        state["risk"]["paused_until"] = until
        _save_state(state)
        _log(f"连续失败≥3，自动暂停至 {until}")
        return

    lock = _acquire_lock()
    if lock is None:
        _log("另一 Baostock 实例运行中，跳过（防重复批量）")
        return

    max_days = _effective_quota(state, args.days)
    enrich = not args.no_enrich
    metrics = _run_metrics_start()
    _log(
        f"=== baostock 回填开始 phase={state.get('phase')} "
        f"quota={max_days}天/次 enrich={enrich} ==="
    )

    if not ps._bs_ensure_login():
        reason = ps._BS_DISABLE_MSG or "login 失败"
        _mark_pressure(metrics, reason)
        state["last_error"] = reason
        state["risk"]["consecutive_failures"] = state["risk"].get("consecutive_failures", 0) + 1
        if any(k in reason for k in ("黑名单", "black", "封禁", "禁止", "频繁")):
            state["risk"]["banned"] = True
            _write_pause(reason)
            _log(f"⛔ 疑似封禁，已写 {PAUSE_FILE}")
        _adjust_quota_after_run(state, metrics)
        state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_state(state)
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
        return

    try:
        from backfill_stock_year import _load_universe

        universe = _load_universe()
        if not universe or len(universe) < 1500:
            raise RuntimeError(f"universe 异常 size={len(universe) if universe else 0}")

        # 倒序完成后切回正序
        if state.get("phase") == "reverse" and not _pending_days_reverse(state):
            saved = state.get("forward_cursor_saved") or state.get("cursor_d0") or START_DATE
            state["cursor_d0"] = saved
            state["phase"] = "forward"
            _log(f"倒序完成 → 恢复正序 cursor={saved}")

        pending = _pending_days(state)
        batch = pending[:max_days]
        metrics["planned_days"] = len(batch)
        _log(f"本次计划 {batch}")

        for d0 in batch:
            try:
                _process_day(d0, state, universe, metrics, enrich=enrich)
                metrics["done_days"] = metrics.get("done_days", 0) + 1
                state["last_error"] = None
                state["risk"]["consecutive_failures"] = 0
                _save_state(state)
            except BaostockBannedError as e:
                reason = str(e)
                state["last_error"] = reason
                state["risk"]["banned"] = True
                state["risk"]["consecutive_failures"] = state["risk"].get("consecutive_failures", 0) + 1
                _mark_pressure(metrics, reason)
                _write_pause(reason)
                _log(f"⛔ BaostockBannedError: {reason}")
                break
            except Exception as e:
                state["last_error"] = str(e)
                state["risk"]["consecutive_failures"] = state["risk"].get("consecutive_failures", 0) + 1
                metrics["errors"] = metrics.get("errors", 0) + 1
                _mark_pressure(metrics, str(e))
                _log(f"  ❌ {d0}: {e}")
                break

        if state.get("phase") == "reverse" and not _pending_days_reverse(state):
            saved = state.get("forward_cursor_saved") or state.get("cursor_d0") or START_DATE
            state["cursor_d0"] = saved
            state["phase"] = "forward"
            _log(f"倒序完成 → 恢复正序 cursor={saved}")
        elif state.get("phase") == "forward" and not _pending_days_forward(state):
            state["phase"] = "done"
            _log("Baostock 正序也已完成")

    except BaostockBannedError as e:
        reason = str(e)
        state["last_error"] = reason
        state["risk"]["banned"] = True
        _mark_pressure(metrics, reason)
        _write_pause(reason)
        _log(f"⛔ {reason}")
    except Exception as e:
        state["last_error"] = str(e)
        _mark_pressure(metrics, str(e))
        _log(f"运行异常: {e}")
    finally:
        try:
            import baostock as bs

            bs.logout()
            setattr(ps, "_BS_LOGGED_IN", False)
        except Exception:
            pass
        if metrics.get("duration_sec") is None:
            metrics["duration_sec"] = round(time.time() - metrics.get("started_at", time.time()), 1)
        if metrics["duration_sec"] > 9000:
            _mark_pressure(metrics, "单次运行超过2.5小时")
        _adjust_quota_after_run(state, metrics)
        state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_state(state)
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
        _log(
            f"=== 结束 phase={state.get('phase')} done={metrics.get('done_days')} "
            f"skip={metrics.get('skipped_existing')} "
            f"quota→{state.get('max_days_per_run')} ==="
        )


if __name__ == "__main__":
    main()
