"""
交易日 15:00 收盘后自动补全：
1) 买入信息（铁律：buy_price = D1 开盘价，禁止 D0 开/收；今日买入强制重写）
2) 卖出信息（9:35 / 13:15）
3) 盈亏比重算
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

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import price_service as ps
import paths  # noqa: E402

STATE_FILE = os.path.join(BASE_DIR, "data", "close_job_state.json")
PUSH_LOCK_FILE = os.path.join(BASE_DIR, "data", "push_once.lock")
RANK_RE = re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")

# up.py 拉取涨停池+逐股历史，7:50 起跑常需 90~120 分钟
SLOW_SCRIPT_TIMEOUT_SEC = 900
UP_SCRIPT_TIMEOUT_SEC = 7200  # 2 小时
UP_ENSURE_WAIT_SEC = 7200  # ensure 等待已在跑的 up.py 最长时间

_scheduler_started = False
_job_lock = threading.Lock()
# state 读-改-写的进程内串行锁（配合 _save_state 的唯一 tmp 名）
_state_lock = threading.RLock()


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    """
    原子写 state。

    临时文件名必须**每线程/每进程唯一**：原先固定用 `state.json.tmp`，
    多线程（UI 调度线程 + 推送线程）或多进程（UI + 15:05 补跑 CLI）并发写时，
    一个进程 os.replace 后另一个的 tmp 已消失 → ENOENT 抛错，
    导致「推送成功但幂等标记没落盘」→ 下轮重推。
    """
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = f"{STATE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def _push_once(state_key: str, value: str, sender, label: str) -> bool:
    """跨进程「只推一次」：同一 state_key==value 的推送，全机只发一次。

    背景（2026-10-23 用户反馈「收盘通知和周结果每个都通知了两遍」）：
      stock_UI 进程内的 15:00 调度线程 与 15:05 的外部补跑 CLI 会并行，
      原先「先读 state → 判断 → 发送 → 再写 state」不是原子操作，
      两个进程可能同时读到「未推送」，于是各发一次。

    现在的顺序：**先抢文件锁 → 重新读 state → 判重/占位落盘 → 释放锁 → 发送**。
      - 占位先写盘，另一个进程随后进来必然看到已占位 → 直接跳过；
      - 发送失败再把占位回滚，下一轮可重试（不会漏发）。
    """
    import fcntl

    os.makedirs(os.path.dirname(PUSH_LOCK_FILE), exist_ok=True)
    with open(PUSH_LOCK_FILE, "a+") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            st = _load_state()
            if st.get(state_key) == value:
                print(f"[close-job] {label} {value} 已推送过，跳过", flush=True)
                return False
            st[state_key] = value  # 占位，防并发重复
            _save_state(st)
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

    try:
        ok = sender()
    except Exception as e:
        ok = False
        print(f"[close-job] {label} 推送异常（不影响补全）: {e}", flush=True)

    if not ok:
        with open(PUSH_LOCK_FILE, "a+") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                st = _load_state()
                if st.get(state_key) == value:
                    st.pop(state_key, None)
                    _save_state(st)
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        print(f"[close-job] {label} 推送失败，已回滚占位，下轮重试", flush=True)
    return bool(ok)


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
    direct = paths.recommendation_md(program, d0)
    stocks = _parse_recommendations_md(direct)
    if stocks:
        return direct, stocks

    d0_8 = d0.replace("-", "")
    if program == "stock":
        fname = f"{d0_8}_recommendations.md"
    elif program == "up":
        fname = f"{d0_8}_up_recommendations.md"
    else:
        fname = f"{d0_8}_{program}_recommendations.md"
    for d in paths.legacy_results_dirs(program):
        path = os.path.join(d, fname)
        stocks = _parse_recommendations_md(path)
        if stocks:
            return path, stocks
    return None, []


def _buy_sync_incomplete(buy_sync: dict) -> bool:
    """买入同步失败时不应占用「今日 15:00 已完成」名额，便于调度器重试。"""
    if not buy_sync:
        return False
    reason = buy_sync.get("reason")
    if reason in ("missing_recommendations_md", "no_up_authoritative_source", "no_prev_day"):
        return True
    if buy_sync.get("synced", 0) == 0 and reason:
        return True
    return False


def _import_up_record_sync_module():
    """加载 up/scripts/up_record_sync.py（避免 ModuleNotFoundError）。"""
    import importlib.util

    candidates = [
        os.path.join(paths.program_dir("up"), "scripts", "up_record_sync.py"),
        os.path.join(ROOT_DIR, "up", "scripts", "up_record_sync.py"),
    ]
    script = next((p for p in candidates if os.path.isfile(p)), None)
    if not script:
        raise ModuleNotFoundError("up_record_sync.py not found under up/scripts/")
    spec = importlib.util.spec_from_file_location("up_record_sync", script)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sync_missing_buy_days_from_md(program: str, today: str) -> dict:
    """
    补写缺失的买入日 json：从已有最大买入日的下一交易日扫到 today，
    用各买入日的前一交易日 D0 的 recommendations md 写入基础记录。
    """
    from stock_UI import upsert_from_stock_py, _list_day_files

    synced_days: list[str] = []
    errors: list[str] = []
    existing = sorted(f[:-5] for f in _list_day_files(program))
    if not existing:
        return {"synced_days": [], "errors": ["no_existing_trades"]}

    trades_dir = paths.program_trades_dir(program)
    cur = ps.next_trading_day(existing[-1])
    while cur and cur <= today:
        if not _is_trading_day(cur):
            cur = ps.next_trading_day(cur)
            continue
        if os.path.isfile(os.path.join(trades_dir, f"{cur}.json")):
            cur = ps.next_trading_day(cur)
            continue
        d0 = ps.prev_trading_day(cur)
        if not d0:
            break
        stocks = []
        if program == "up":
            try:
                up_mod = _import_up_record_sync_module()
                stocks, _source, _jp = up_mod.resolve_up_top2(cur)
            except Exception as e:
                errors.append(f"up resolve {cur}: {e}")
        if not stocks:
            _path, stocks = _find_recommendations_md(d0, program)
        if not stocks:
            errors.append(f"no md {d0} for buy {cur}")
            cur = ps.next_trading_day(cur)
            continue
        records = [
            {
                "date": cur,
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "rank": i,
            }
            for i, s in enumerate(stocks, 1)
        ]
        try:
            upsert_from_stock_py(records, auto_enrich=False, replace_by_rank=True, program=program)
            synced_days.append(cur)
        except Exception as e:
            errors.append(f"{cur}: {e}")
        cur = ps.next_trading_day(cur)

    return {"synced_days": synced_days, "errors": errors}


def _sync_buy_candidates_for_today(today: str, program: str = "stock") -> dict:
    """
    补全买入信息：从选股程序 D0 recommendations 同步 Top2。
    找不到 md 时不瞎补（禁止同花顺涨停池顶替，避免与强度模型不一致）。
    """
    from stock_UI import upsert_from_stock_py

    if not _is_trading_day(today):
        return {"synced": 0, "source": None, "reason": "non_trading_day", "date": today, "program": program}

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


def _ensure_up_recommendations(d0: str) -> bool:
    """不再用 fast_gen — 仅检查 up.py 权威源是否存在。"""
    up_mod = _import_up_record_sync_module()
    find_up_result_json = up_mod.find_up_result_json
    top2_from_json = up_mod.top2_from_json

    buy = ps.next_trading_day(d0)
    if buy:
        path, data = find_up_result_json(buy)
        if data and top2_from_json(data):
            return True
    d8 = d0.replace("-", "")
    return os.path.exists(paths.recommendation_md("up", d0))


def _morning_sync_incomplete(result: dict) -> bool:
    """早盘同步未成功写入 Top2 时，9:30 仍需重试。"""
    if not result or result.get("skipped"):
        return False
    reason = result.get("reason")
    if reason in ("missing_recommendations_md", "no_up_authoritative_source", "no_prev_day"):
        return True
    if result.get("synced", 0) == 0 and reason:
        return True
    return False


def _morning_source_ready(program: str, today: str) -> tuple[bool, str]:
    """检查 slow/up 是否已有今日买入所需的权威推荐源（md 或 up JSON）。"""
    d0 = ps.prev_trading_day(today)
    if not d0:
        return False, "no_prev_day"
    if program == "up":
        try:
            up_mod = _import_up_record_sync_module()
            stocks, source, _ = up_mod.resolve_up_top2(today)
            if stocks:
                return True, source or "up_source"
        except Exception as e:
            return False, f"up_check_error:{e}"
        return False, "no_up_authoritative_source"
    _path, stocks = _find_recommendations_md(d0, program)
    if stocks:
        return True, "md"
    return False, "missing_recommendations_md"


def _morning_trades_ready(program: str, today: str) -> bool:
    """检查 trades 是否已有今日基础买入记录。"""
    from stock_UI import _load_day

    trades = _load_day(today, program)
    if not trades:
        return False
    top = trades[:2]
    return all(str(t.get("stock_code") or "").strip() and str(t.get("stock_name") or "").strip() for t in top)


def morning_recommendations_ready(program: str, today: str) -> tuple[bool, str]:
    """推荐源 + 今日 trades 均就绪才算完成。"""
    src_ok, reason = _morning_source_ready(program, today)
    if not src_ok:
        return False, reason
    if not _morning_trades_ready(program, today):
        return False, "trades_not_written"
    return True, "ok"


def _up_produce_in_progress() -> tuple[bool, int | None]:
    """避免多个 up.py 并行（会拖慢且易超时）。"""
    import subprocess as sp

    try:
        out = sp.check_output(["pgrep", "-f", r"up/up\.py"], text=True).strip()
        if out:
            pid = int(out.splitlines()[0])
            return True, pid
    except (sp.CalledProcessError, ValueError):
        pass
    return False, None


def _run_model_script(
    program: str,
    cmd: list[str],
    cwd: str,
    *,
    timeout_sec: int,
    label: str,
) -> dict:
    import subprocess

    print(f"[morning-produce] ({label}) {program} timeout={timeout_sec}s …")
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        return {
            "ok": r.returncode == 0,
            "code": r.returncode,
            "tail": (r.stderr or r.stdout or "")[-400:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "tail": f"timeout_{timeout_sec}s"}
    except Exception as e:
        return {"ok": False, "code": -1, "tail": str(e)}


def run_morning_produce_pipeline(
    as_of: Optional[str] = None,
    *,
    label: str = "9:00",
    programs: Optional[tuple[str, ...]] = None,
    skip_if_ready: bool = True,
    sync_trades: bool = True,
) -> dict:
    """
    跑 slow.py / up.py（无通知，落盘 md/json）并可选 sync_morning_records。
    skip_if_ready=True：已有 D0 推荐 md/json 时不再 subprocess 重跑（拉取/回补只读文件）。
    """
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    targets = programs or ("slow", "up")
    out: dict = {"date": today, "label": label, "produce": {}, "sync": {}, "skipped_run": []}

    if "slow" in targets:
        if skip_if_ready and _morning_source_ready("slow", today)[0]:
            print(f"[morning-produce] ({label}) slow 推荐 md 已存在，跳过重跑")
            out["skipped_run"].append("slow")
            out["produce"]["slow"] = {"ok": True, "skipped": True, "reason": "md_ready"}
        else:
            slow_py = paths.program_script("slow")
            slow_dir = paths.program_dir("slow")
            out["produce"]["slow"] = _run_model_script(
                "slow.py",
                [sys.executable, slow_py],
                slow_dir,
                timeout_sec=SLOW_SCRIPT_TIMEOUT_SEC,
                label=label,
            )

    if "up" in targets:
        if skip_if_ready and _morning_source_ready("up", today)[0]:
            print(f"[morning-produce] ({label}) up 推荐源已存在，跳过重跑")
            out["skipped_run"].append("up")
            out["produce"]["up"] = {"ok": True, "skipped": True, "reason": "source_ready"}
        else:
            busy, busy_pid = _up_produce_in_progress()
            if busy:
                print(f"[morning-produce] ({label}) up.py 已在运行 pid={busy_pid}，等待落盘…")
                out["produce"]["up"] = {"ok": False, "skipped": True, "reason": f"already_running:{busy_pid}"}
            else:
                up_py = paths.program_script("up")
                up_dir = paths.program_dir("up")
                up_json = os.path.join(up_dir, "strength_limit_up_result.json")
                out["produce"]["up"] = _run_model_script(
                    "up.py",
                    [sys.executable, up_py, "--output", up_json],
                    up_dir,
                    timeout_sec=UP_SCRIPT_TIMEOUT_SEC,
                    label=label,
                )

    if sync_trades:
        for program in targets:
            try:
                out["sync"][program] = sync_morning_records(today, program=program)
            except Exception as e:
                out["sync"][program] = {"error": str(e)}

    return out


def ensure_slow_up_ready(
    as_of: Optional[str] = None,
    programs: tuple[str, ...] = ("slow", "up"),
    *,
    force_produce: bool = False,
    label: str = "ensure",
) -> dict:
    """
    铁纪律：确保 slow/up 推荐源 + 今日 trades 就绪。
    有 md/json 则只 sync；缺失则跑脚本（up 最长 60 分钟）。
    """
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    if not _is_trading_day(today):
        return {"skipped": True, "reason": "non_trading_day", "date": today}

    with _ensure_lock:
        return _ensure_slow_up_ready_unlocked(today, programs, force_produce=force_produce, label=label)


def _ensure_slow_up_ready_unlocked(
    today: str,
    programs: tuple[str, ...],
    *,
    force_produce: bool,
    label: str,
) -> dict:
    out: dict = {"date": today, "label": label, "programs": {}, "produce": None}
    need_produce: list[str] = []

    for program in programs:
        ok, reason = morning_recommendations_ready(program, today)
        if ok:
            out["programs"][program] = {"ready": True, "reason": "ok"}
            continue

        src_ok, src_reason = _morning_source_ready(program, today)
        if src_ok:
            sync = sync_morning_records(today, program=program)
            ok2, reason2 = morning_recommendations_ready(program, today)
            out["programs"][program] = {"ready": ok2, "reason": reason2, "sync": sync}
            if not ok2:
                need_produce.append(program)
        else:
            out["programs"][program] = {"ready": False, "reason": reason or src_reason}
            need_produce.append(program)

    if need_produce:
        print(f"[ensure] {today} 缺失 {need_produce}，开始产出+同步 (force={force_produce})")
        out["produce"] = run_morning_produce_pipeline(
            today,
            label=label,
            programs=tuple(need_produce),
            skip_if_ready=not force_produce,
        )
        for program in need_produce:
            ok, reason = morning_recommendations_ready(program, today)
            out["programs"][program]["after_produce"] = {"ready": ok, "reason": reason}
            out["programs"][program]["ready"] = ok
            if program == "up" and not ok:
                busy, pid = _up_produce_in_progress()
                if busy:
                    print(f"[ensure] up.py 仍在运行 pid={pid}，等待最多 {UP_ENSURE_WAIT_SEC // 60} 分钟后 sync…")
                    waits = max(1, UP_ENSURE_WAIT_SEC // 10)
                    for _ in range(waits):
                        if not _up_produce_in_progress()[0]:
                            break
                        time.sleep(10)
                    sync = sync_morning_records(today, program="up")
                    ok, reason = morning_recommendations_ready("up", today)
                    out["programs"]["up"]["after_wait"] = {"sync": sync, "ready": ok, "reason": reason}
                    out["programs"]["up"]["ready"] = ok

    state = _load_state()
    state["last_ensure_slow_up"] = out
    _save_state(state)
    return out


def get_morning_readiness_status(as_of: Optional[str] = None) -> dict:
    """供 UI 展示 slow/up 早盘就绪与失败原因。"""
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    status = {"date": today, "is_trading_day": _is_trading_day(today), "programs": {}}
    for program in ("slow", "up"):
        src_ok, src_reason = _morning_source_ready(program, today)
        trades_ok = _morning_trades_ready(program, today)
        ok, reason = morning_recommendations_ready(program, today)
        status["programs"][program] = {
            "ready": ok,
            "reason": reason,
            "source_ready": src_ok,
            "source_reason": src_reason,
            "trades_ready": trades_ok,
        }
    return status


def run_morning_sync_from_files(as_of: Optional[str] = None, programs: tuple[str, ...] = ("slow", "up")) -> dict:
    """仅从已落盘的 md/json 同步 trades，绝不 subprocess 重跑选股脚本。"""
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    out: dict = {"date": today, "phase": "morning_sync_files", "sync": {}}
    for program in programs:
        try:
            out["sync"][program] = sync_morning_records(today, program=program)
        except Exception as e:
            out["sync"][program] = {"error": str(e)}
    return out


def verify_and_retry_morning(as_of: Optional[str] = None, *, phase: str = "9:30_verify") -> dict:
    """复检：推荐源或 trades 缺失则再跑一遍 produce + sync（up 最长 60 分钟）。"""
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    if not _is_trading_day(today):
        return {"skipped": True, "reason": "non_trading_day", "date": today, "phase": phase}

    status: dict = {"date": today, "phase": phase, "check": {}, "retry": None}
    need: list[str] = []
    for program in ("slow", "up"):
        ok, reason = morning_recommendations_ready(program, today)
        status["check"][program] = {"ready": ok, "reason": reason}
        if not ok:
            need.append(program)

    if need:
        print(f"[morning-verify] ({phase}) {today} 缺失 {need}，重跑 produce+sync")
        status["retry"] = run_morning_produce_pipeline(
            today, label=phase, programs=tuple(need), skip_if_ready=False,
        )
        for program in need:
            ok, reason = morning_recommendations_ready(program, today)
            status["check"][f"{program}_after_retry"] = {"ready": ok, "reason": reason}
    else:
        print(f"[morning-verify] ({phase}) {today} slow/up 均已就绪")

    state = _load_state()
    state["last_morning_verify"] = status
    state["last_morning_verify_date"] = today
    state[f"last_morning_retry_{phase}_date"] = today
    _save_state(state)
    return status


def _program_trades_ready_detail(program: str, today: str) -> tuple[bool, str]:
    """stock / stock_updata / stock_v2 / slow / up：源+今日 trades 是否齐。"""
    if program in ("slow", "up"):
        return morning_recommendations_ready(program, today)
    if program == "stock_v2":
        # V2 允许空仓：当日 results 存在且 status 明确即视为「已落盘」；
        # 有推荐时再要求 trades 非空。
        if _stock_v2_day_settled(today):
            return True, "ok"
        if _morning_trades_ready(program, today):
            return True, "ok"
        return False, "stock_v2_not_ready"
    src_ok, src_reason = _morning_source_ready(program, today)
    if not src_ok:
        return False, src_reason
    if not _morning_trades_ready(program, today):
        return False, "trades_not_written"
    return True, "ok"


def _stock_v2_results_path(today: str) -> str:
    return os.path.join(paths.program_results_dir("stock_v2"), f"{today}_recommendations.json")


def _stock_v2_day_settled(today: str) -> bool:
    path = _stock_v2_results_path(today)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("status") in ("OK", "NO_TRADE")
    except Exception:
        return False


def _sync_stock_v2_from_results(today: str) -> dict:
    """从 V2 results JSON 兜底写入 trades（不重跑竞价）。"""
    from stock_UI import upsert_from_stock_py, _load_day, _save_day, _data_lock

    path = _stock_v2_results_path(today)
    if not os.path.isfile(path):
        return {"synced": 0, "reason": "missing_results_json"}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    recs = data.get("recommendations") or []
    if not recs:
        with _data_lock:
            _save_day(today, [], "stock_v2")
        return {"synced": 0, "reason": "no_trade_empty_day", "status": data.get("status")}
    records = [
        {
            "date": today,
            "stock_code": r.get("code"),
            "stock_name": r.get("name"),
            "rank": int(r.get("rank") or 0),
            "notes": f"V2 path={r.get('path')} score={r.get('score_total')}",
        }
        for r in recs
    ]
    result = upsert_from_stock_py(records, auto_enrich=False, replace_by_rank=True, program="stock_v2")
    result["status"] = data.get("status")
    return result


def _ensure_board_tabs_for_day(today: str) -> dict:
    """主板/创业板：确保 stock 当日 board_rank Top2 写入（单线程，可幂等）。"""
    try:
        import backfill_board_records as bbr
    except Exception as e:
        return {"ready": False, "error": f"import_backfill_board_records:{e}"}

    if bbr.already_done("stock", today):
        return {"ready": True, "reason": "already_done"}
    try:
        should_mark, msg = bbr.process_one_day("stock", today)
        ready = bbr.already_done("stock", today)
        return {
            "ready": ready,
            "reason": "ok" if ready else "incomplete",
            "msg": msg,
            "should_mark": should_mark,
        }
    except Exception as e:
        return {"ready": False, "error": str(e)}


def ensure_all_ui_tabs_ready(
    as_of: Optional[str] = None,
    *,
    force_produce: bool = False,
    label: str = "9:30_ui_tabs",
) -> dict:
    """
    铁纪律（见《交易UI开盘六Tab落盘规范》）：
    交易日开盘后，Stock / 水下 / 主板 / 创业板 / Slow / Up 六个 Tab
    必须具备当日选票基础记录。

    - stock / stock_updata：只 sync（不重跑带通知脚本）
    - slow / up：走 ensure_slow_up_ready（可 produce）
    - 主板 / 创业板：补 stock 的 board_rank
    """
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    if not _is_trading_day(today):
        return {"skipped": True, "reason": "non_trading_day", "date": today, "label": label}

    out: dict = {
        "date": today,
        "label": label,
        "programs": {},
        "boards": None,
        "ready": False,
    }

    # 1) slow / up
    out["slow_up"] = ensure_slow_up_ready(
        today, ("slow", "up"), force_produce=force_produce, label=label,
    )

    # 2) stock / 水下：有 md 则 sync 进 trades；stock_v2 由独立脚本写盘，这里只核验/兜底读 results
    for program in ("stock", "stock_updata", "stock_v2"):
        ok0, reason0 = _program_trades_ready_detail(program, today)
        if ok0:
            out["programs"][program] = {"ready": True, "reason": "ok"}
            continue
        if program == "stock_v2":
            try:
                sync = _sync_stock_v2_from_results(today)
                ok1 = _morning_trades_ready(program, today)
                out["programs"][program] = {
                    "ready": ok1,
                    "reason": "ok" if ok1 else "trades_not_written",
                    "sync": sync,
                    "before": reason0,
                }
            except Exception as e:
                out["programs"][program] = {"ready": False, "reason": reason0, "error": str(e)}
            continue
        src_ok, src_reason = _morning_source_ready(program, today)
        if not src_ok:
            out["programs"][program] = {"ready": False, "reason": src_reason or reason0}
            continue
        try:
            sync = sync_morning_records(today, program=program)
            ok1, reason1 = _program_trades_ready_detail(program, today)
            out["programs"][program] = {
                "ready": ok1,
                "reason": reason1,
                "sync": sync,
                "before": reason0,
            }
        except Exception as e:
            out["programs"][program] = {"ready": False, "reason": reason0, "error": str(e)}

    for program in ("slow", "up"):
        pinfo = (out.get("slow_up") or {}).get("programs", {}).get(program) or {}
        out["programs"][program] = {
            "ready": bool(pinfo.get("ready")),
            "reason": pinfo.get("reason") or pinfo.get("after_produce", {}).get("reason"),
        }

    # 3) 主板 / 创业板（stock.board_rank）
    print(f"[ui-tabs] ({label}) {today} 检查主板/创业板 board_rank …")
    out["boards"] = _ensure_board_tabs_for_day(today)
    print(f"[ui-tabs] ({label}) boards={out['boards']}")

    prog_ok = all(
        bool((out["programs"].get(p) or {}).get("ready"))
        for p in ("stock", "stock_updata", "stock_v2", "slow", "up")
    )
    board_ok = bool((out.get("boards") or {}).get("ready"))
    out["ready"] = prog_ok and board_ok
    out["tabs"] = {
        "stock": bool((out["programs"].get("stock") or {}).get("ready")),
        "stock_v2": bool((out["programs"].get("stock_v2") or {}).get("ready")),
        "stock_updata": bool((out["programs"].get("stock_updata") or {}).get("ready")),
        "slow": bool((out["programs"].get("slow") or {}).get("ready")),
        "up": bool((out["programs"].get("up") or {}).get("ready")),
        "主板": board_ok,
        "创业板": board_ok,
    }

    state = _load_state()
    state["last_ui_tabs_ensure"] = out
    state["last_ui_tabs_ensure_date"] = today
    state["last_ui_tabs_ensure_label"] = label
    _save_state(state)
    print(f"[ui-tabs] ({label}) {today} ready={out['ready']} tabs={out['tabs']}")

    # 开盘六 Tab 落盘后必须同步 GitHub Pages（短生命周期 cron 不能依赖进程内 Timer）
    try:
        from pages_sync import schedule_pages_sync

        schedule_pages_sync(delay=5)
        out["pages_sync"] = "scheduled"
        print(f"[ui-tabs] ({label}) 已调度 Pages 同步（5s 后独立进程）", flush=True)
    except Exception as e:
        out["pages_sync"] = f"error:{e}"
        print(f"[ui-tabs] ({label}) Pages 同步调度失败: {e}", flush=True)
    return out


def sync_morning_records(as_of: Optional[str] = None, program: str = "up") -> dict:
    """
    早盘（约 9:00）同步基础买入记录：仅 code/name/rank，不补价格。
    up：严格读 up.py 的 strength_limit_up_result.json，与 PushPlus 通知一致。
    """
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    if not _is_trading_day(today):
        return {"skipped": True, "reason": "non_trading_day", "date": today, "program": program}

    if program == "up":
        up_mod = _import_up_record_sync_module()
        result = up_mod.sync_up_trades(today, force=True)
        result["date"] = today
        result["phase"] = "morning_sync"
    else:
        result = _sync_buy_candidates_for_today(today, program)
        result["date"] = today
        result["phase"] = "morning_sync"

    state = _load_state()
    state[f"last_morning_sync_{program}"] = result
    state[f"last_morning_sync_date_{program}"] = today
    _save_state(state)
    print(f"[morning-sync] {today} ({program}): {result}")
    return result


_morning_scheduler_started = False
_ensure_lock = threading.Lock()


STOCK_NOTIFY_FROM = (9, 5)
STOCK_NOTIFY_TO = (9, 45)
STOCK_NOTIFY_RETRY_SEC = 300


def _current_stock_buy_date() -> str:
    """读取 stock 当前结果 JSON 的买入日（buy_date），用于判断底层数据是否当天。"""
    try:
        path = os.path.join(paths.program_dir("stock"), "strength_stock_result.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("buy_date", "")
    except Exception:
        return ""


def _stock_notify_sent_today(today: str) -> bool:
    """今日、且数据买入日==今日都发过，才视为已完成。

    关键修正（2026-10-29）：之前只比对 date，导致「用过期数据发过一次」就误判
    已完成——今天 10-29 底层分析没刷新，兜底把昨天的票发出去后便不会再补发真数据。
    现在要求标记里记录的 buy_date 与当前结果 JSON 的买入日一致才算完成。
    """
    try:
        path = os.path.join(paths.program_dir("stock"), "last_notify.json")
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        if m.get("date") != today:
            return False
        return m.get("buy_date") == _current_stock_buy_date()
    except Exception:
        return False


def _ensure_stock_notify(today: str, state: dict) -> None:
    """09:05 兜底：底层分析漏跑/未刷新时，由常驻 UI 进程补做分析与通知。

    流程：
      1. 若保存的 stock 结果 JSON 买入日不是今天 → 先重跑 `stock.py` 分析刷新
         （今天 10-29 就吃过亏：08:50 的分析没跑，JSON 停在昨天，兜底把过期
          数据发了出去）；
      2. 仅当「今日且数据买入日==今日」都发过才跳过（见 _stock_notify_sent_today），
         否则在 09:05~09:45 subprocess 跑 `stock.py --notify-only`，最多 8 次、
         间隔 5 分钟，避免失败时刷屏。
    """
    # 1) 数据新鲜度保障：底层分析没刷新就先重跑
    if _current_stock_buy_date() != today:
        print("[morning-sync] stock 结果买入日非今日，先重跑分析刷新", flush=True)
        try:
            stock_py = paths.program_script("stock")
            _run_model_script(
                "stock.py (refresh)",
                [sys.executable, stock_py],
                paths.program_dir("stock"),
                timeout_sec=300,
                label="9:05_refresh",
            )
        except Exception as e:
            print(f"[morning-sync] stock 分析刷新异常: {e}", flush=True)

    # 2) 已为今日真数据发过则跳过
    if _stock_notify_sent_today(today):
        return
    now = datetime.now()
    last = state.get("last_stock_notify_try_ts") or 0
    try:
        if now.timestamp() - float(last) < STOCK_NOTIFY_RETRY_SEC:
            return
    except (TypeError, ValueError):
        pass
    cnt_key = f"stock_notify_attempts_{today}"
    cnt = int(state.get(cnt_key) or 0)
    if cnt >= 8:
        return
    state[cnt_key] = cnt + 1
    state["last_stock_notify_try_ts"] = now.timestamp()
    _save_state(state)
    print(f"[morning-sync] 09:05 stock 模型通知未发出，补发（第 {cnt + 1} 次）", flush=True)
    try:
        stock_py = paths.program_script("stock")
        res = _run_model_script(
            "stock.py --notify-only",
            [sys.executable, stock_py, "--notify-only"],
            paths.program_dir("stock"),
            timeout_sec=180,
            label="9:05_notify",
        )
        print(f"[morning-sync] stock 通知补发结果: {res}", flush=True)
    except Exception as e:
        print(f"[morning-sync] stock 通知补发异常: {e}", flush=True)


def _in_stock_notify_window(now: datetime) -> bool:
    hm = (now.hour, now.minute)
    return STOCK_NOTIFY_FROM <= hm < STOCK_NOTIFY_TO


def _morning_retry_window(now: Optional[datetime] = None) -> Optional[str]:
    """返回当前所处的早盘/午间复检窗口名，不在窗口内则 None。"""
    now = now or datetime.now()
    if now.hour == 7 and now.minute >= 50:
        return "7:50_up_early"
    if (now.hour == 8 and now.minute >= 50) or (now.hour == 9 and now.minute < 10):
        return "8:50_primary"
    if now.hour == 9 and 30 <= now.minute < 40:
        return "9:30_verify"
    if now.hour == 10 and 30 <= now.minute < 40:
        return "10:30_verify"
    if now.hour == 12 and 30 <= now.minute < 40:
        return "12:30_verify"
    if now.hour == 14 and 30 <= now.minute < 40:
        return "14:30_verify"
    return None


def _morning_scheduler_loop(check_interval_sec: int = 30):
    """交易日 8:50 产出 + 9:30/10:30/12:30/14:30 缺失则重试 slow/up。

    另含 stock 模型通知兜底：09:05~09:45 若当日未发，则 subprocess 跑
    `stock.py --notify-only`（与 crontab 那路共用 last_notify.json 去重）。
    """
    print("[morning-sync] 调度器已启动（7:50 up + 8:50 slow/stock + 9:30 六Tab落盘 + 午间复检）")
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            # stock 模型通知兜底（09:05~09:45）：crontab 漏跑时由常驻 UI 进程补发。
            # 函数内部已按 last_notify.json 判重 + 5 分钟重试间隔 + 最多 8 次。
            if _in_stock_notify_window(now):
                _ensure_stock_notify(today, _load_state())
            window = _morning_retry_window(now)
            if not window:
                time.sleep(check_interval_sec)
                continue
            if not _is_trading_day(today):
                time.sleep(check_interval_sec)
                continue

            state = _load_state()
            retry_key = f"last_morning_retry_{window}_date"
            if state.get(retry_key) == today:
                time.sleep(check_interval_sec)
                continue

            if window == "7:50_up_early":
                busy, pid = _up_produce_in_progress()
                if busy:
                    print(f"[morning-sync] 7:50 up.py 已在运行 pid={pid}，跳过")
                else:
                    print(f"[morning-sync] 7:50 提前启动 {today} up.py")
                    run_morning_produce_pipeline(
                        today, label="7:50_up_early", programs=("up",), skip_if_ready=True,
                    )
            elif window == "8:50_primary":
                print(f"[morning-sync] 8:50 触发 {today} slow/up 落盘+同步（与 stock 同批）")
                run_morning_produce_pipeline(today, label="8:50_ui", skip_if_ready=False)
            elif window == "9:30_verify":
                # 铁纪律：六 Tab（Stock/水下/主板/创业板/Slow/Up）当日选票必须落盘
                print(f"[morning-sync] {window} 触发 {today} 六 Tab 开盘落盘 ensure")
                ensure_all_ui_tabs_ready(today, label="9:30_ui_tabs")
            else:
                print(f"[morning-sync] {window} 触发 {today} 六 Tab 复检 ensure")
                ensure_all_ui_tabs_ready(today, label=window)

            state = _load_state()
            state[retry_key] = today
            if window == "8:50_primary":
                state["last_morning_produce_date"] = today
            _save_state(state)
        except Exception as e:
            print(f"[morning-sync] 调度异常: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(check_interval_sec)


def start_morning_scheduler():
    global _morning_scheduler_started
    if _morning_scheduler_started:
        return
    _morning_scheduler_started = True
    t = threading.Thread(target=_morning_scheduler_loop, name="morning-sync-job", daemon=True)
    t.start()


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

    if program in ("slow", "up"):
        ok, reason = morning_recommendations_ready(program, today)
        if not ok:
            print(f"[close-job] {program} 早盘未就绪({reason})，收盘前先 ensure 产出+同步")
            ensure_slow_up_ready(today, (program,), force_produce=True, label="15:00_ensure")

    # 非交易日永不写入买入记录（force 仅用于强制重拉价格，不能绕过日历）
    cal = ps.get_trade_calendar()
    if cal is not None:
        dt = datetime.strptime(today, "%Y-%m-%d")
        if cal[cal["trade_date"] == dt].empty:
            print(f"[close-job] {today} 非 A 股交易日，跳过（force 不生效）")
            return {"skipped": True, "reason": "non_trading_day", "date": today, "program": program}

    with _job_lock:
        # 1) 买入候选同步 + 开盘价（up 必须用 up.py JSON，与 9:00 通知一致）
        if program == "up":
            up_mod = _import_up_record_sync_module()
            buy_sync = up_mod.sync_up_trades(today, force=True)
        else:
            buy_sync = _sync_buy_candidates_for_today(today, program)
        if _buy_sync_incomplete(buy_sync):
            backfill = _sync_missing_buy_days_from_md(program, today)
            if backfill.get("synced_days"):
                print(f"[close-job] 回填缺失买入日: {backfill}")
                if buy_sync.get("reason") == "missing_recommendations_md":
                    buy_sync = {**buy_sync, "backfill": backfill, "reason": None}
        print(f"[close-job] 买入同步: {buy_sync}")

        buy_enriched = 0
        sell_enriched = 0
        removed = []
        touched_days = set()

        # 今日买入 + 昨买今卖 + 连板滚动中仍未卖出的历史买入日
        from stock_UI import pending_sell_buy_days

        candidate_days = {today}
        prev = ps.prev_trading_day(today)
        if prev:
            candidate_days.add(prev)
        candidate_days |= pending_sell_buy_days(program, today)

        for day in sorted(candidate_days):
            if not day:
                continue
            trades = _load_day(day, program)
            if not trades:
                continue
            keep = []
            changed = False
            for t in trades:
                is_today_buy = str(t.get("date") or "") == today
                # 铁律：收盘补全必须按 D1 开盘价重写今日买入价（覆盖早盘误写的 D0 开/收）
                need_buy = is_today_buy
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

                # 今日为卖出日：强制按 9:35/13:15 重拉（禁止沿用开盘/收盘错价）
                is_today_sell = bool(sell_date) and str(sell_date) == today
                if not (need_buy or (need_sell and sell_due) or force or is_today_sell):
                    from stock_UI import auto_calculate
                    keep.append(auto_calculate(t))
                    continue

                before = json.dumps(t, sort_keys=True, default=str)
                try:
                    # 今日买入 → 强制 D1 开盘；今日卖出 → 强制真 9:35/13:15
                    enriched = enrich_trade_prices(
                        t, force=(force or is_today_buy or is_today_sell)
                    )
                except Exception as e:
                    print(f"[close-job] 补价失败 {day} {t.get('stock_code')}: {e}")
                    from stock_UI import auto_calculate
                    enriched = auto_calculate(dict(t))
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
            "incomplete": _buy_sync_incomplete(buy_sync),
        }
        state = _load_state()
        state_key = f"last_run_{program}"
        state[state_key] = summary
        # 兼容旧字段；真正决定「今日 15:00 是否已跑」看 last_close_run_date
        state["last_success_date"] = today
        now = datetime.now()
        incomplete = _buy_sync_incomplete(buy_sync)
        # 铁纪律：只有 15:00 及之后的成功跑，才标记收盘完成（早盘手动点不算）
        if now.hour > 15 or (now.hour == 15 and now.minute >= 0):
            close_key = f"last_close_run_date_{program}"
            if incomplete:
                state.pop(close_key, None)
                print(
                    f"[close-job] 买入同步未完成({buy_sync.get('reason')})，"
                    f"不标记 {close_key}，15:00 调度将重试"
                )
            else:
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

        # 收盘补全落盘后，推送当天买入的 stock Top2 结果（用户 2026-10-19 要求）。
        # 失败不影响补全结果，单独告警即可；跨进程只推一次（_push_once）。
        if program == "stock":
            try:
                from push_top2_result import push_stock_top2

                _push_once(
                    "last_top2_push_date",
                    today,
                    lambda: push_stock_top2(today, "stock"),
                    "Top2 结果",
                )
            except Exception as e:
                print(f"[close-job] Top2 结果推送异常（不影响补全）: {e}", flush=True)

            # 周五（或当周最后一个交易日）收盘后叠加推送周结果（用户 2026-10-19 要求）。
            try:
                from push_weekly_result import is_week_end, _week_monday, push_weekly

                if is_week_end(today):
                    monday = _week_monday(today)
                    _push_once(
                        "last_weekly_push_monday",
                        monday,
                        lambda: push_weekly(today, "stock"),
                        "周结果",
                    )
                else:
                    print(f"[close-job] 今日非本周最后交易日，不推周报", flush=True)
            except Exception as e:
                print(f"[close-job] 周结果推送异常（不影响补全）: {e}", flush=True)

        return summary


def _skip_if_done(program: str, day: Optional[str], force: bool) -> bool:
    """CLI 补跑前的幂等判断：今日已收盘补全完成则跳过，避免与 UI 内调度双跑。

    用于 15:05 的外部兜底 CLI：UI 进程内的 15:00 调度已经跑过时直接跳过，
    只有 UI 没跑（进程没起/挂了）才会真正执行，兼顾兜底与不重复。
    """
    if force:
        return False
    today = day or datetime.now().strftime("%Y-%m-%d")
    st = _load_state()
    if st.get(f"last_close_run_date_{program}") != today:
        return False
    last = st.get(f"last_run_{program}") or {}
    if _buy_sync_incomplete(last.get("buy_sync") or {}):
        return False
    print(
        f"[close-job] {today} {program} 今日收盘补全已完成，跳过（--force 可强制重跑）",
        flush=True,
    )
    return True


SELL_RETRY_START = (15, 30)   # 15:30 起开始重试当日 D2 卖价
SELL_RETRY_END = (20, 30)     # 20:30 后不再重试（baostock 若仍未发布，次日再补）
SELL_RETRY_INTERVAL_SEC = 1800


def _sell_retry_due(program: str, state: dict, now: datetime, today: str) -> bool:
    """是否到了「补当日 D2 卖价」的重试窗口（每 30 分钟最多一次，每天最多 8 次）。"""
    hm = (now.hour, now.minute)
    if hm < SELL_RETRY_START or hm > SELL_RETRY_END:
        return False
    if int(state.get(f"sell_retry_count_{program}_{today}") or 0) >= 8:
        return False
    last = state.get(f"last_sell_retry_ts_{program}") or 0
    try:
        return (now.timestamp() - float(last)) >= SELL_RETRY_INTERVAL_SEC
    except (TypeError, ValueError):
        return True


def _pending_sell_trades(program: str, today: str, lookback_days: int = 12) -> list[str]:
    """列出「应卖出日已到、但 09:35/13:15 价还空」的记录（只看最近 12 天，避免全库扫描）。"""
    from stock_UI import _list_day_files, _load_day

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    out: list[str] = []
    for fname in _list_day_files(program):
        buy_day = fname[:-5]
        if buy_day < cutoff or buy_day >= today:   # 当日买入的，次日才卖，不催
            continue
        for t in _load_day(buy_day, program):
            if t.get("buy_price") in (None, ""):
                continue
            if "买不进" in str(t.get("skip_reason") or ""):
                continue
            planned = ps.next_trading_day(buy_day)
            if not planned or planned > today:
                continue
            # 涨停/跌停顺延到未来交易日的，不催；到那天再补
            sd = str(t.get("sell_date") or "")
            if sd and sd > today:
                continue
            sr = str(t.get("skip_reason") or "")
            if ("跌停卖不出" in sr or "涨停卖不出" in sr) and "分钟价缺失" not in sr:
                continue
            if t.get("sell_price_935") in (None, "") or t.get("sell_price_1315") in (None, ""):
                out.append(f"{buy_day}:{t.get('stock_code')}")
    return out


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
    铁纪律：每个交易日 15:00 后必须自动跑一次收盘补全
    （stock / slow / up / stock_updata 水下，与四个 Tab 对齐）。
    早盘/手动预跑不得占用「今日已完成」名额。
    """
    from stock_UI import VALID_PROGRAMS
    print(
        "[close-job] 调度器已启动（铁纪律：交易日 15:00 自动补全 "
        f"{'/'.join(VALID_PROGRAMS)} 买卖与盈亏）"
    )
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")

            # 09:05 stock 模型通知兜底（与 crontab 互斥：两边都以 last_notify.json 为准）
            if _in_stock_notify_window(now) and _is_trading_day(today):
                _ensure_stock_notify(today, _load_state())

            state = _load_state()

            for program in VALID_PROGRAMS:
                close_key = f"last_close_run_date_{program}"
                already = state.get(close_key) == today
                if already:
                    last = state.get(f"last_run_{program}") or {}
                    if _buy_sync_incomplete(last.get("buy_sync") or {}):
                        already = False
                        print(f"[close-job] {program} 上次买入同步未完成，15:00 重试")
                if already:
                    # 当日 D2 卖价补齐重试：15:00 首次跑时 baostock 当日 EOD 常未发布，
                    # 15:30~20:30 每 30 分钟补一次，直到 09:35/13:15 都有值。
                    if _sell_retry_due(program, state, now, today):
                        pending = _pending_sell_trades(program, today)
                        if pending:
                            already = False
                            state[f"last_sell_retry_ts_{program}"] = now.timestamp()
                            cnt_key = f"sell_retry_count_{program}_{today}"
                            state[cnt_key] = int(state.get(cnt_key) or 0) + 1
                            _save_state(state)
                            print(
                                f"[close-job] {program} 今日 D2 卖价仍有 {len(pending)} 条缺口，"
                                f"重试补价: {', '.join(pending[:6])}"
                                f"{' …' if len(pending) > 6 else ''}"
                            )
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
                last = state.get(f"last_run_{program}") or {}
                if not _buy_sync_incomplete(last.get("buy_sync") or {}):
                    print(f"[close-job] 启动检查：{program} 今日收盘补全已完成")
                    continue
                print(f"[close-job] 启动补跑：{program} 上次买入同步未完成，立即重试")
            else:
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
    from stock_UI import VALID_PROGRAMS

    parser = argparse.ArgumentParser(description="手动执行收盘补全")
    parser.add_argument("--date", default="", help="YYYY-MM-DD，默认今天")
    parser.add_argument("--program", default="stock", choices=VALID_PROGRAMS)
    parser.add_argument("--all-programs", action="store_true", help="依次补全 stock/slow/up/stock_updata(水下)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--morning-produce", action="store_true", help="跑 slow/up 落盘+同步")
    parser.add_argument("--morning-sync", action="store_true", help="仅从 md/json 同步 trades，不重跑脚本")
    parser.add_argument("--morning-verify", action="store_true", help="复检 slow/up，缺失则重跑")
    parser.add_argument(
        "--ensure-morning",
        action="store_true",
        help="铁纪律：开盘六 Tab（stock/水下/主板/创业板/slow/up）当日选票落盘",
    )
    parser.add_argument("--force-run", action="store_true", help="morning-produce 时强制重跑脚本")
    args = parser.parse_args()
    if args.morning_sync:
        print(run_morning_sync_from_files(args.date or None))
    elif args.ensure_morning:
        print(ensure_all_ui_tabs_ready(
            args.date or None,
            force_produce=args.force_run or args.force,
            label="cli_ensure_ui_tabs",
        ))
    elif args.morning_produce:
        print(run_morning_produce_pipeline(
            args.date or None,
            label="cli_produce",
            skip_if_ready=not args.force_run,
        ))
    elif args.morning_verify:
        print(verify_and_retry_morning(args.date or None))
    elif args.all_programs:
        for prog in VALID_PROGRAMS:
            if _skip_if_done(prog, args.date or None, args.force):
                continue
            print(run_after_close_job(args.date or None, force=args.force, program=prog))
    else:
        if _skip_if_done(args.program, args.date or None, args.force):
            raise SystemExit(0)
        print(run_after_close_job(args.date or None, force=args.force, program=args.program))
