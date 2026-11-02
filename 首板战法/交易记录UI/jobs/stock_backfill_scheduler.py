#!/usr/bin/env python3
"""
stock 历史回填调度器 — 查缺补漏 + 1-7 月倒序按周补数。

铁纪律：
  - DISABLE_BAOSTOCK=1（禁止 baostock）
  - workers=1 串行腾讯扫描
  - 单次运行天数上限（自适应：试探 2 天 → 无压力升至 3 → 有压力降至 1）
  - 进程锁防重叠

用法:
  python3 stock_backfill_scheduler.py --status
  python3 stock_backfill_scheduler.py --run                    # cron 自动判断阶段
  python3 stock_backfill_scheduler.py --run --phase gap        # 8-12 查缺
  python3 stock_backfill_scheduler.py --run --phase reverse --days 3
  python3 stock_backfill_scheduler.py --run --slot night --no-enrich --days 3
      # 03:00：接 1-7 月倒序游标，只写明细不补价（不改 17:00 自适应配额）
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
from datetime import datetime

for p in (ROOT_DIR, BASE_DIR, os.path.join(ROOT_DIR, "stock", "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DISABLE_BAOSTOCK", "1")

import fetch_calendar as fc  # noqa: E402
import paths  # noqa: E402

STATE_FILE = os.path.join(BASE_DIR, "data", "stock_backfill_schedule.json")
LOCK_FILE = os.path.join(BASE_DIR, "data", "stock_backfill.lock")
LOG_FILE = os.path.join(BASE_DIR, "data", "historical_fetch_logs", "stock_scheduler.log")

def _build_reverse_weeks() -> list[list[str]]:
    """1-7 月交易日按自然周倒序（周五截断）。"""
    from datetime import datetime

    cal = fc.load_calendar("2025-01-01", "2025-07-31")
    weeks: list[list[str]] = []
    wk: list[str] = []
    for d in cal:
        wk.append(d)
        dt = datetime.strptime(d, "%Y-%m-%d")
        if dt.weekday() == 4:
            weeks.append(wk)
            wk = []
    if wk:
        weeks.append(wk)
    return list(reversed(weeks))


REVERSE_WEEKS_JAN_JUL = _build_reverse_weeks()

DEFAULT_STATE = {
    "phase": "forward",  # forward（正序续跑）| reverse | gap | done
    "gap_deadline": "2025-08-24",
    "gap_dates_done": [],
    "forward_cursor_d0": "2025-01-02",  # 正序：接 Baostock 已填之后的空洞
    "reverse_week_index": 0,
    "reverse_day_offset": 0,
    "max_days_per_run": 2,
    "tx_interval_sec": 0.85,
    "ths_interval_sec": 1.2,
    "last_run": None,
    "last_error": None,
    "adaptive": {
        "enabled": True,
        "probe_runs_remaining": 0,
        "current_quota": 3,
        "target_quota": 3,
        "min_quota": 1,
        "clean_runs": 0,
        "runs_for_upgrade": 2,
        "last_run_metrics": None,
    },
    "risk": {
        "consecutive_failures": 0,
        "paused_until": None,
        "pressure_events": 0,
    },
}


def _log(msg: str) -> None:
    line = f"[{datetime.now():%F %T}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_state() -> dict:
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        for k, v in DEFAULT_STATE.items():
            s.setdefault(k, v if not isinstance(v, dict) else dict(v))
        return s
    return json.loads(json.dumps(DEFAULT_STATE))


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _buy_date_exists(d0: str) -> bool:
    d1 = fc.next_trading_day(d0)
    if not d1:
        return False
    path = os.path.join(paths.program_trades_dir("stock"), f"{d1}.json")
    return os.path.isfile(path)


def _gap_dates_aug_dec() -> list[str]:
    cal = fc.load_calendar("2025-08-01", "2025-12-31")
    return [d for d in cal if not _buy_date_exists(d)]


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
    q = int(ad.get("current_quota", 2))
    if ad.get("enabled") and ad.get("probe_runs_remaining", 0) > 0:
        q = min(q, 2)  # 试探期上限 2 天
    return max(int(ad.get("min_quota", 1)), q)


def _run_metrics_start() -> dict:
    return {
        "started_at": time.time(),
        "planned_days": 0,
        "done_days": 0,
        "errors": 0,
        "pressure": False,
        "pressure_reasons": [],
    }


def _mark_pressure(metrics: dict, reason: str) -> None:
    metrics["pressure"] = True
    metrics.setdefault("pressure_reasons", []).append(reason)


def _adjust_quota_after_run(state: dict, metrics: dict) -> None:
    """根据本次运行结果自动调整次日配额。"""
    ad = state.setdefault("adaptive", dict(DEFAULT_STATE["adaptive"]))
    metrics["duration_sec"] = round(time.time() - metrics.get("started_at", time.time()), 1)
    ad["last_run_metrics"] = {
        k: metrics[k]
        for k in ("planned_days", "done_days", "errors", "pressure", "pressure_reasons", "duration_sec")
        if k in metrics
    }

    if ad.get("probe_runs_remaining", 0) > 0:
        ad["probe_runs_remaining"] = max(0, ad["probe_runs_remaining"] - 1)
        ad["current_quota"] = 2
        state["max_days_per_run"] = 2
        _log(
            f"试探期剩余 {ad['probe_runs_remaining']} 次，配额保持 2 天/次 "
            f"(耗时 {metrics['duration_sec']}s, 完成 {metrics.get('done_days',0)} 天)"
        )
        if ad["probe_runs_remaining"] == 0:
            _log("试探期结束，将根据 API 反馈自动升降配额")
        return

    min_q = int(ad.get("min_quota", 1))
    target = int(ad.get("target_quota", 3))
    cur = int(ad.get("current_quota", 2))
    need_upgrade = int(ad.get("runs_for_upgrade", 2))

    if metrics.get("pressure") or metrics.get("errors", 0) > 0 or state.get("last_error"):
        ad["clean_runs"] = 0
        new_q = max(min_q, cur - 1) if cur > min_q else min_q
        if new_q != cur:
            _log(f"⚠ API 有压力 ({metrics.get('pressure_reasons') or state.get('last_error')})，配额 {cur}→{new_q} 天/次")
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
            _log(f"✓ 本次无压力，配额维持 {cur} 天/次 (clean_runs={ad['clean_runs']}/{need_upgrade})")

    state["max_days_per_run"] = ad["current_quota"]


def _process_stock_day(
    d0: str,
    state: dict,
    force_tx: bool = False,
    metrics: dict | None = None,
    *,
    enrich: bool = True,
    resilient: bool = True,
) -> str:
    """
    处理单日。返回:
      written | skip_exists | empty_confirmed | empty_unconfirmed | failed
    """
    from backfill_stock_year import fetch_first_board, fetch_first_board_resilient, rank_by_amount
    from stock_UI import upsert_from_stock_py, _load_day, _save_day, enrich_trade_prices

    if _buy_date_exists(d0):
        _log(f"  {d0} 已有记录，跳过")
        return "skip_exists"

    d1 = fc.next_trading_day(d0)
    if not d1:
        return "skip_exists"

    source_used = "tx" if force_tx else "auto"
    _log(f"  {d0} 拉取首板 resilient={resilient} force_tx={force_tx} enrich={enrich} …")
    time.sleep(state.get("ths_interval_sec", 1.2))
    try:
        if resilient:
            first, source_used = fetch_first_board_resilient(d0, workers=1)
        else:
            source = "tx" if force_tx else "auto"
            first = fetch_first_board(d0, source=source, workers=1)
            source_used = source
            if not first and not force_tx:
                _log(f"  {d0} THS 无数据，改腾讯扫描 …")
                time.sleep(state.get("ths_interval_sec", 1.2))
                first = fetch_first_board(d0, source="tx", workers=1)
                source_used = "tx"
    except Exception as e:
        if metrics is not None:
            metrics["errors"] = metrics.get("errors", 0) + 1
            _mark_pressure(metrics, f"fetch异常:{e}")
        raise

    if not first:
        if source_used == "empty_unconfirmed":
            _log(f"  {d0} 多源未确认空（不推进游标，次日重试）")
            if metrics is not None:
                _mark_pressure(metrics, f"{d0}:empty_unconfirmed")
            return "empty_unconfirmed"
        _log(f"  {d0} 确认无首板数据 source={source_used}")
        return "empty_confirmed"

    top2 = rank_by_amount(first, d0)[:2]
    records = [
        {"date": d1, "stock_code": h["code"], "stock_name": h["name"], "rank": i}
        for i, h in enumerate(top2, 1)
    ]
    upsert_from_stock_py(records, auto_enrich=False, program="stock")
    _log(
        f"  {d0}→{d1} [{source_used}] "
        + ", ".join(r["stock_code"] for r in records)
    )

    if not enrich:
        _log(f"  {d1} 跳过补价（--no-enrich / night）")
        return "written"

    trades = _load_day(d1, "stock")
    if trades:
        changed = False
        for i, t in enumerate(trades):
            time.sleep(0.4)
            e = enrich_trade_prices(dict(t), force=False)
            e.pop("skip_buy", None)
            trades[i] = e
            changed = True
        if changed:
            _save_day(d1, trades, "stock")
    return "written"


def run_gap(state: dict, max_days: int, metrics: dict | None = None, *, enrich: bool = True) -> int:
    pending = [d for d in _gap_dates_aug_dec() if d not in state.get("gap_dates_done", [])]
    if not pending:
        _log("8-12 月无遗漏，切换 reverse 阶段")
        state["phase"] = "reverse"
        state["reverse_week_index"] = 0
        state["reverse_day_offset"] = 0
        return 0

    _log(f"查缺补漏 8-12 月：待补 {len(pending)} 天，本次最多 {max_days} 天")
    if metrics is not None:
        metrics["planned_days"] = metrics.get("planned_days", 0) + min(max_days, len(pending))
    n = 0
    for d0 in pending[:max_days]:
        try:
            status = _process_stock_day(
                d0, state, force_tx=False, metrics=metrics, enrich=enrich, resilient=True
            )
            if status == "empty_unconfirmed":
                break
            state.setdefault("gap_dates_done", []).append(d0)
            if status == "written":
                n += 1
                if metrics is not None:
                    metrics["done_days"] = metrics.get("done_days", 0) + 1
            elif status in ("skip_exists", "empty_confirmed"):
                if metrics is not None:
                    metrics["done_days"] = metrics.get("done_days", 0) + 1
        except Exception as e:
            state["last_error"] = str(e)
            state["risk"]["consecutive_failures"] = state["risk"].get("consecutive_failures", 0) + 1
            if metrics is not None:
                _mark_pressure(metrics, str(e))
            _log(f"  ❌ {d0}: {e}")
            break
    if n and not state.get("last_error"):
        state["risk"]["consecutive_failures"] = 0
    if not [d for d in _gap_dates_aug_dec() if d not in state.get("gap_dates_done", [])]:
        state["phase"] = "reverse"
        state["reverse_week_index"] = 0
        state["reverse_day_offset"] = 0
        _log("8-12 查缺完成 → 进入 1-7 月倒序")
    return n


def run_forward(state: dict, max_days: int, metrics: dict | None = None, *, enrich: bool = True) -> int:
    """
    正序补洞：目标每天写出 1–3 天有效明细。
    - THS → Baostock 单日 → 腾讯全量
    - 空日若未确认则不推进游标并停止本轮（次日重试）
    - 确认空 / 已有记录：跳过并继续找下一天，直到写满 quota 或候选耗尽
    """
    cursor = state.get("forward_cursor_d0") or "2025-01-02"
    cal = fc.load_calendar("2025-01-02", "2025-12-31")
    pending = [d for d in cal if d >= cursor and not _buy_date_exists(d)]
    if not pending:
        _log("正序无待补日期")
        return 0

    target = max(1, min(3, max_days))
    # 允许多试几天，避免连续「确认空」导致当天 0 产出
    max_attempts = max(target * 4, 6)
    batch_preview = pending[:max_attempts]
    _log(f"正序批次 cursor>={cursor} 目标写出 {target} 天，候选 {batch_preview}")
    if metrics is not None:
        metrics["planned_days"] = metrics.get("planned_days", 0) + target

    written = 0
    attempts = 0
    for d0 in pending:
        if written >= target or attempts >= max_attempts:
            break
        attempts += 1
        try:
            status = _process_stock_day(
                d0, state, force_tx=True, metrics=metrics, enrich=enrich, resilient=True
            )
            nxt = fc.next_trading_day(d0) or d0
            if status == "written":
                written += 1
                if metrics is not None:
                    metrics["done_days"] = metrics.get("done_days", 0) + 1
                state["forward_cursor_d0"] = nxt
                state["last_error"] = None
            elif status == "skip_exists":
                state["forward_cursor_d0"] = nxt
            elif status == "empty_confirmed":
                # 真无首板：推进，继续找下一天凑满目标
                state["forward_cursor_d0"] = nxt
                _log(f"  {d0} 确认空，继续下一天凑满目标 ({written}/{target})")
            elif status == "empty_unconfirmed":
                # 不推进游标，本轮结束，保证次日重试同一天
                _log(f"  正序本轮因未确认空提前结束，已写出 {written}/{target}")
                break
        except Exception as e:
            state["last_error"] = str(e)
            state["risk"]["consecutive_failures"] = state["risk"].get("consecutive_failures", 0) + 1
            if metrics is not None:
                _mark_pressure(metrics, str(e))
            _log(f"  ❌ {d0}: {e}")
            break

    if written < 1:
        _log(f"⚠ 正序本日有效写出 0 天（目标 {target}），标压力")
        if metrics is not None:
            _mark_pressure(metrics, "正序本日写出0天")
    else:
        _log(f"✓ 正序本日有效写出 {written} 天（目标 {target}）")
        state["risk"]["consecutive_failures"] = 0
    return written


def run_reverse(state: dict, max_days: int, metrics: dict | None = None, *, enrich: bool = True) -> int:
    idx = state.get("reverse_week_index", 0)
    offset = state.get("reverse_day_offset", 0)
    if idx >= len(REVERSE_WEEKS_JAN_JUL):
        state["phase"] = "done"
        _log("1-7 月倒序全部完成")
        return 0

    week = REVERSE_WEEKS_JAN_JUL[idx]
    remaining = week[offset:]
    # 倒序也尽量写出满额：向后多看几天
    target = max(1, min(3, max_days))
    scan_list = remaining[: max(target * 3, max_days)]
    _log(f"倒序批次 week[{idx}] offset={offset} 目标写出 {target} 扫描 {scan_list}")
    if metrics is not None:
        metrics["planned_days"] = metrics.get("planned_days", 0) + target

    written = 0
    stepped = 0
    for d0 in scan_list:
        if written >= target:
            break
        try:
            status = _process_stock_day(
                d0, state, force_tx=True, metrics=metrics, enrich=enrich, resilient=True
            )
            stepped += 1
            state["reverse_day_offset"] = offset + stepped
            if status == "written":
                written += 1
                if metrics is not None:
                    metrics["done_days"] = metrics.get("done_days", 0) + 1
            elif status == "empty_unconfirmed":
                # 回退一步游标，次日重试
                state["reverse_day_offset"] = max(offset, offset + stepped - 1)
                _log(f"  倒序遇未确认空，保留游标 offset={state['reverse_day_offset']}")
                break
        except Exception as e:
            state["last_error"] = str(e)
            state["risk"]["consecutive_failures"] = state["risk"].get("consecutive_failures", 0) + 1
            if metrics is not None:
                _mark_pressure(metrics, str(e))
            _log(f"  ❌ {d0}: {e}")
            break

    if state["reverse_day_offset"] >= len(week):
        state["reverse_week_index"] = idx + 1
        state["reverse_day_offset"] = 0
        _log(f"week[{idx}] 完成 → week[{idx+1}]")

    if written < 1 and not state.get("last_error"):
        if metrics is not None:
            _mark_pressure(metrics, "倒序本日写出0天")
    if not state.get("last_error") and written >= 1:
        state["risk"]["consecutive_failures"] = 0
    return written


def print_status(state: dict) -> None:
    gap = _gap_dates_aug_dec()
    gap_done = state.get("gap_dates_done", [])
    idx = state.get("reverse_week_index", 0)
    print("=" * 60)
    print(f"阶段: {state.get('phase')} | 末次: {state.get('last_run')} | night: {state.get('last_night_run')}")
    print(f"正序 cursor: {state.get('forward_cursor_d0')}")
    print(f"8-12 遗漏: {len(gap)} 天 {gap}")
    print(f"8-12 已补: {len(gap_done)}")
    if idx < len(REVERSE_WEEKS_JAN_JUL):
        w = REVERSE_WEEKS_JAN_JUL[idx]
        print(f"倒序当前 week[{idx}]: {w}")
        print(f"  进度 offset={state.get('reverse_day_offset',0)}/{len(w)}")
    print(f"单次配额: {_effective_quota(state)} 天/次 (adaptive)")
    ad = state.get("adaptive", {})
    if ad:
        print(f"  试探剩余: {ad.get('probe_runs_remaining', 0)} 次 | current={ad.get('current_quota')} target={ad.get('target_quota')}")
        lm = ad.get("last_run_metrics")
        if lm:
            print(f"  上次: {lm.get('done_days')}天 耗时{lm.get('duration_sec')}s 压力={lm.get('pressure')}")
    print(f"连续失败: {state.get('risk',{}).get('consecutive_failures',0)} | 压力事件: {state.get('risk',{}).get('pressure_events',0)}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--phase", choices=("auto", "gap", "reverse", "forward"), default="auto")
    parser.add_argument("--days", type=int, default=0, help="覆盖 max_days_per_run")
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="只写首板明细，不补价（03:00 night 用）",
    )
    parser.add_argument(
        "--slot",
        choices=("day", "night"),
        default="day",
        help="night=接倒序游标、默认不补价、不改 17:00 自适应配额",
    )
    args = parser.parse_args()

    state = _load_state()
    if args.status:
        print_status(state)
        return

    if not args.run:
        parser.print_help()
        return

    night = args.slot == "night"
    enrich = not (args.no_enrich or night)

    # 风险：连续失败 3 次暂停 24h
    paused = state.get("risk", {}).get("paused_until")
    if paused and paused > datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
        _log(f"风险暂停中 until {paused}")
        return

    if state["risk"].get("consecutive_failures", 0) >= 3:
        _log("连续失败≥3，自动暂停 24h")
        from datetime import timedelta
        until = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        state["risk"]["paused_until"] = until
        _save_state(state)
        return

    lock = _acquire_lock()
    if lock is None:
        _log("另一实例运行中，跳过")
        return

    # night：默认 3 天/次，与 16/17 合计约 6–9；不强制改 day 线自适应
    if night and args.days <= 0:
        max_days = max(1, min(4, int(state.get("adaptive", {}).get("current_quota", 3) or 3)))
        if max_days < 2:
            max_days = 2
        if max_days > 3:
            max_days = 3
    else:
        max_days = _effective_quota(state, args.days)

    phase = state.get("phase", "forward")
    if args.phase == "forward":
        phase = "forward"
        state["phase"] = "forward"
    elif args.phase == "gap":
        phase = "gap"
    elif args.phase == "reverse":
        phase = "reverse"
    elif night:
        # 夜间默认正序补洞（倒序已交给 Baostock 优先线）
        phase = "forward"
        state["phase"] = "forward"
    elif args.phase == "auto":
        # 默认正序；仅显式 gap/reverse 才改
        if phase not in ("forward", "gap", "reverse", "done"):
            phase = "forward"
            state["phase"] = "forward"

    _log(
        f"=== stock 回填开始 slot={'night' if night else 'day'} "
        f"phase={phase} quota={max_days}天/次 enrich={enrich} ==="
    )
    metrics = _run_metrics_start()
    had_error = False
    try:
        used = 0
        if phase == "forward":
            used += run_forward(state, max_days, metrics, enrich=enrich)
        elif phase == "gap":
            used += run_gap(state, max_days, metrics, enrich=enrich)
            remaining = max_days - used
            if remaining > 0 and state.get("phase") == "reverse":
                _log(f"查缺剩余配额 {remaining} 天，继续倒序 7 月 …")
                used += run_reverse(state, remaining, metrics, enrich=enrich)
        elif phase == "reverse":
            used += run_reverse(state, max_days, metrics, enrich=enrich)
        else:
            _log("全部完成")
        _log(f"本次共处理约 {used} 天")
    except Exception as e:
        had_error = True
        state["last_error"] = str(e)
        _mark_pressure(metrics, str(e))
        _log(f"运行异常: {e}")
    finally:
        metrics["duration_sec"] = round(time.time() - metrics.get("started_at", time.time()), 1)
        if metrics["duration_sec"] > 7200:
            _mark_pressure(metrics, "单次运行超过2小时")
        if not had_error and not state.get("last_error"):
            state["last_error"] = None
        # night 只推进游标，不改 17:00 自适应配额（避免夜间长扫误降白天额度）
        if night:
            state.setdefault("night", {})["last_run_metrics"] = {
                k: metrics.get(k)
                for k in ("planned_days", "done_days", "errors", "pressure", "pressure_reasons", "duration_sec")
            }
            _log(
                f"night 槽结束 done={metrics.get('done_days')} "
                f"耗时{metrics.get('duration_sec')}s（不调整 day 配额）"
            )
        else:
            _adjust_quota_after_run(state, metrics)
        state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if night:
            state["last_night_run"] = state["last_run"]
        _save_state(state)
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


if __name__ == "__main__":
    main()
