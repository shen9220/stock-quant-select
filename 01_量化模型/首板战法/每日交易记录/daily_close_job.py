"""
交易日 15:00 收盘后自动补全：
1) 买入信息（当日开盘价等）
2) 卖出信息（9:35 / 13:15）
3) 盈亏比重算
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import price_service as ps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(os.path.dirname(BASE_DIR), "results")
# stock.py 可能跑在并列工作区（如 new_stock/...），一并检索
_EXTRA_RESULTS_DIRS = [
    os.path.expanduser("~/new_stock/stock-quant-select/01_量化模型/首板战法/results"),
]
STATE_FILE = os.path.join(BASE_DIR, "data", "close_job_state.json")
RANK_RE = re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")

_scheduler_started = False
_job_lock = threading.Lock()


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def _parse_recommendations_md(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    head = text.split("=== 其他未入选", 1)[0]
    stocks = []
    for m in RANK_RE.finditer(head):
        stocks.append({"stock_code": m.group(1), "stock_name": m.group(2).strip()})
        if len(stocks) >= 2:
            break
    return stocks


def _find_recommendations_md(d0: str, program: str = "stock") -> tuple[Optional[str], list[dict]]:
    """读取选股推荐 md 文件。
    program=stock: {date}_recommendations.md
    program=slow: {date}_slow_recommendations.md
    program=up: {date}_up_recommendations.md
    """
    d0_8 = d0.replace("-", "")
    if program == "stock":
        fname = f"{d0_8}_recommendations.md"
    elif program == "up":
        fname = f"{d0_8}_up_recommendations.md"
    else:
        fname = f"{d0_8}_{program}_recommendations.md"
    dirs = [RESULTS_DIR] + [p for p in _EXTRA_RESULTS_DIRS if p]
    for d in dirs:
        path = os.path.join(d, fname)
        stocks = _parse_recommendations_md(path)
        if stocks:
            return path, stocks
    return None, []


def _sync_buy_candidates_for_today(today: str, program: str = "stock") -> dict:
    """
    补全买入信息：从选股程序 D0 recommendations 同步 Top2。
    找不到 md 时不瞎补（禁止同花顺涨停池顶替，避免与强度模型不一致）。
    """
    from stock_UI import upsert_from_stock_py

    d0 = ps.prev_trading_day(today)
    if not d0:
        return {"synced": 0, "source": None, "reason": "no_prev_day"}

    path, stocks = _find_recommendations_md(d0, program)
    if not stocks:
        print(f"[close-job] 未找到 {d0} 的 {program}.py recommendations，跳过买入同步")
        return {
            "synced": 0,
            "source": None,
            "d0": d0,
            "program": program,
            "reason": "missing_recommendations_md",
            "hint": f"请先运行 {program}.py，确保存在 results/{d0.replace('-', '')}_{program}_recommendations.md",
        }

    records = []
    for i, s in enumerate(stocks, 1):
        records.append({
            "date": today,
            "stock_code": s["stock_code"],
            "stock_name": s["stock_name"],
            "rank": i,
        })
    result = upsert_from_stock_py(records, auto_enrich=False, replace_by_rank=True, program=program)

    # 同日 rank1/2 只保留该程序 Top2 代码，清掉误写入的杂票
    from stock_UI import _load_day, _save_day, _data_lock
    keep_codes = {str(s["stock_code"]).zfill(6) for s in stocks}
    removed_extra = []
    with _data_lock:
        day_trades = _load_day(today, program)
        cleaned = []
        for t in day_trades:
            rk = int(t.get("rank") or 0)
            code = str(t.get("stock_code") or "").zfill(6)
            if rk in (1, 2) and code not in keep_codes:
                removed_extra.append(t)
                continue
            cleaned.append(t)
        if removed_extra:
            _save_day(today, cleaned, program)

    return {
        "synced": result.get("created", 0) + result.get("updated", 0),
        "created": result.get("created", 0),
        "updated": result.get("updated", 0),
        "source": f"{program}_py_md",
        "md_path": path,
        "d0": d0,
        "program": program,
        "records": records,
        "removed_extra": [
            {"code": t.get("stock_code"), "name": t.get("stock_name"), "rank": t.get("rank")}
            for t in removed_extra
        ],
    }


def run_after_close_job(as_of: Optional[str] = None, force: bool = False, program: str = "stock") -> dict:
    """
    收盘补全主流程。as_of=YYYY-MM-DD，默认今天。
    program: stock / slow / up
    """
    from stock_UI import (
        _list_day_files,
        _load_day,
        _save_day,
        enrich_trade_prices,
        VALID_PROGRAMS,
        DEFAULT_PROGRAM,
    )

    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM

    today = as_of or datetime.now().strftime("%Y-%m-%d")
    print(f"[close-job] ===== 开始收盘补全 {today} ({program}) =====")

    # 非交易日跳过（force 除外）
    cal = ps.get_trade_calendar()
    if cal is not None and not force:
        dt = datetime.strptime(today, "%Y-%m-%d")
        if cal[cal["trade_date"] == dt].empty:
            print(f"[close-job] {today} 非交易日，跳过")
            return {"skipped": True, "reason": "non_trading_day", "date": today, "program": program}

    with _job_lock:
        # 1) 买入候选同步 + 开盘价
        buy_sync = _sync_buy_candidates_for_today(today, program)
        print(f"[close-job] 买入同步: {buy_sync}")

        buy_enriched = 0
        sell_enriched = 0
        removed = []
        touched_days = set()

        # 仅今日买入 + 昨买今卖；不要扫全年空字段（会卡死 UI）
        candidate_days = {today}
        prev = ps.prev_trading_day(today)
        if prev:
            candidate_days.add(prev)

        for day in sorted(candidate_days):
            if not day:
                continue
            trades = _load_day(day, program)
            if not trades:
                continue
            keep = []
            changed = False
            for t in trades:
                need_buy = (str(t.get("date") or "") == today) and t.get("buy_price") in (None, "")
                need_sell = any(
                    t.get(k) in (None, "")
                    for k in (
                        "sell_price_935",
                        "sell_price_1315",
                        "sell_price_half_935",
                        "sell_price_half_1315",
                    )
                )
                sell_date = t.get("sell_date")
                sell_due = (not sell_date) or (str(sell_date) <= today)

                if not (need_buy or (need_sell and sell_due) or force):
                    from stock_UI import auto_calculate
                    keep.append(auto_calculate(t))
                    continue

                before = json.dumps(t, sort_keys=True, default=str)
                enriched = enrich_trade_prices(t, force=force)
                enriched.pop("skip_buy", None)
                enriched.setdefault("program", program)
                after = json.dumps(enriched, sort_keys=True, default=str)
                if need_buy and enriched.get("buy_price") in (None, ""):
                    print(f"[close-job] 警告: {day} {enriched.get('stock_code')} 买入价仍空")
                if str(enriched.get("date") or "") == today and enriched.get("buy_price") not in (None, ""):
                    if need_buy or force:
                        buy_enriched += 1
                if any(enriched.get(k) not in (None, "") for k in (
                    "sell_price_935", "sell_price_1315"
                )) and (need_sell or force):
                    sell_enriched += 1
                if enriched.get("skip_reason") and "买不进" in str(enriched.get("skip_reason")):
                    removed.append({
                        "id": t.get("id"),
                        "date": day,
                        "code": t.get("stock_code"),
                        "name": t.get("stock_name"),
                        "note": "open_limit_unfilled",
                    })

                if before != after:
                    changed = True
                keep.append(enriched)

            # 今日文件即使未改动也写回，避免漏落盘
            if changed or day == today:
                _save_day(day, keep, program)
                touched_days.add(day)

        # 只重算触达日，避免全库 recalculate 卡住
        from stock_UI import auto_calculate as _ac
        recalc_n = 0
        for day in sorted(touched_days):
            trades = _load_day(day, program)
            for i, t in enumerate(trades):
                trades[i] = _ac(t)
                recalc_n += 1
            _save_day(day, trades, program)
        summary = {
            "date": today,
            "program": program,
            "buy_sync": buy_sync,
            "buy_enriched": buy_enriched,
            "sell_enriched": sell_enriched,
            "removed_open_limit": removed,
            "touched_days": sorted(touched_days),
            "recalculated": recalc_n,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        state = _load_state()
        state_key = f"last_run_{program}"
        state[state_key] = summary
        # 兼容旧字段；真正决定「今日 15:00 是否已跑」看 last_close_run_date
        state["last_success_date"] = today
        now = datetime.now()
        # 铁纪律：只有 15:00 及之后的成功跑，才标记收盘完成（早盘手动点不算）
        if now.hour > 15 or (now.hour == 15 and now.minute >= 0):
            close_key = f"last_close_run_date_{program}"
            state[close_key] = today
            state[f"last_close_run_{program}"] = summary
            # 兼容旧字段
            state["last_close_run_date"] = today
            state["last_close_run"] = summary
            print(f"[close-job] 已标记今日收盘补全完成 {close_key}={today}")
        else:
            print(f"[close-job] 当前未到 15:00，本次不记入收盘完成（避免挡住盘后自动跑）")
        _save_state(state)
        print(f"[close-job] 完成: {summary}")
        return summary


def _is_trading_day(day: str) -> bool:
    cal = ps.get_trade_calendar()
    if cal is None:
        return True
    dt = datetime.strptime(day, "%Y-%m-%d")
    return not cal[cal["trade_date"] == dt].empty


def _after_close_window(now: Optional[datetime] = None) -> bool:
    """交易日 15:00 之后（含 15:00）。"""
    now = now or datetime.now()
    return now.hour > 15 or (now.hour == 15 and now.minute >= 0)


def _scheduler_loop(check_interval_sec: int = 20):
    """
    铁纪律：每个交易日 15:00 后必须自动跑一次收盘补全（所有三个程序）。
    早盘/手动预跑不得占用「今日已完成」名额。
    """
    from stock_UI import VALID_PROGRAMS
    print("[close-job] 调度器已启动（铁纪律：交易日 15:00 自动补全 stock/slow/up 买卖与盈亏）")
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            state = _load_state()

            for program in VALID_PROGRAMS:
                close_key = f"last_close_run_date_{program}"
                already = state.get(close_key) == today
                if already:
                    continue

                if not _after_close_window(now):
                    continue

                if not _is_trading_day(today):
                    state[close_key] = today
                    state[f"last_close_run_{program}"] = {
                        "skipped": True,
                        "reason": "non_trading_day",
                        "date": today,
                        "program": program,
                        "finished_at": now.isoformat(timespec="seconds"),
                    }
                    _save_state(state)
                    print(f"[close-job] {today} 非交易日，标记 {program} 跳过")
                    continue

                # 15:00:00~15:00:45 稍等，给行情落库一点时间
                if now.hour == 15 and now.minute == 0 and now.second < 45:
                    wait = 45 - now.second
                    print(f"[close-job] 15:00 等待行情落库 {wait}s…")
                    time.sleep(wait)

                print(f"[close-job] 铁纪律触发：开始 {today} 收盘自动补全 ({program})")
                run_after_close_job(today, force=False, program=program)
        except Exception as e:
            print(f"[close-job] 调度异常: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(check_interval_sec)


def _startup_catchup():
    """进程启动时：若已过 15:00 且今日尚未收盘补全，立刻补跑所有程序。"""
    from stock_UI import VALID_PROGRAMS
    try:
        time.sleep(8)
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        state = _load_state()
        if not _after_close_window(now):
            print(f"[close-job] 启动检查：未到 15:00，等待调度")
            return
        if not _is_trading_day(today):
            print(f"[close-job] 启动检查：今日非交易日")
            return
        for program in VALID_PROGRAMS:
            close_key = f"last_close_run_date_{program}"
            if state.get(close_key) == today:
                print(f"[close-job] 启动检查：{program} 今日收盘补全已完成")
                continue
            print(f"[close-job] 启动补跑：{program} 今日尚未完成 15:00 收盘补全，立即执行")
            run_after_close_job(today, force=False, program=program)
    except Exception as e:
        print(f"[close-job] 启动补跑失败: {e}")


def start_close_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_startup_catchup, name="close-job-catchup", daemon=True).start()
    t = threading.Thread(target=_scheduler_loop, name="after-close-job", daemon=True)
    t.start()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="手动执行收盘补全")
    parser.add_argument("--date", default="", help="YYYY-MM-DD，默认今天")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(run_after_close_job(args.date or None, force=args.force))
