"""
运行结果台账：每个程序每次执行落盘，供后续归因 / 优化追溯。

目录（按 profile 隔离）：
  data/runs/YYYY-MM-DD/{run_id}.json      # V1 明细
  data/runs/index.jsonl                   # V1 一行一条索引
  data_v2/runs/...                        # V2 同上

字段含：程序名、参数、起止时间、成功失败、摘要、完整结果、策略版本、触发来源。
"""
from __future__ import annotations

import json
import os
import socket
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _runs_dir(profile: str) -> str:
    root = "data_v2" if (profile or "v1").lower() == "v2" else "data"
    return os.path.join(BASE_DIR, root, "runs")


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def _append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def new_run_id(program: str) -> str:
    ts = datetime.now().strftime("%H%M%S")
    return f"{ts}_{program}_{uuid.uuid4().hex[:8]}"


def record_run(
    *,
    program: str,
    profile: str = "v1",
    phase: str | None = None,
    slot: str | None = None,
    asof: str | None = None,
    args: dict | None = None,
    ok: bool = True,
    error: str | None = None,
    summary: dict | None = None,
    result: Any = None,
    triggered_by: str = "cli",
    strategy_version: str | None = None,
    experiment_id: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    extra: dict | None = None,
) -> dict:
    """写入单次运行明细 + index.jsonl。返回完整记录（含路径）。"""
    profile = (profile or "v1").lower()
    now = datetime.now()
    day = (asof or now.strftime("%Y-%m-%d"))
    # 文件按「跑完日」归档，asof 另存业务日
    file_day = now.strftime("%Y-%m-%d")
    run_id = new_run_id(program.replace("/", "_").replace(" ", "_"))
    started = started_at or now.isoformat(timespec="seconds")
    finished = finished_at or now.isoformat(timespec="seconds")

    # 结果过大时裁剪（保留摘要，明细截断标记）
    result_payload = result
    result_truncated = False
    try:
        raw = json.dumps(result, ensure_ascii=False, default=str)
        if len(raw) > 800_000:
            result_truncated = True
            if isinstance(result, dict):
                result_payload = {
                    "_truncated": True,
                    "keys": list(result.keys()),
                    "preview": {k: result[k] for k in list(result.keys())[:12]},
                }
            else:
                result_payload = {"_truncated": True, "preview": raw[:2000]}
    except Exception:
        result_payload = {"_serialize_error": True}

    rec = {
        "run_id": run_id,
        "profile": profile,
        "program": program,
        "phase": phase,
        "slot": slot,
        "asof": day,
        "args": args or {},
        "ok": bool(ok),
        "error": error,
        "summary": summary or {},
        "result": result_payload,
        "result_truncated": result_truncated,
        "triggered_by": triggered_by,
        "strategy_version": strategy_version or ("V2.3" if profile == "v2" else "V1"),
        "experiment_id": experiment_id or ("V2_MAIN" if profile == "v2" else "V1_BASELINE"),
        "started_at": started,
        "finished_at": finished,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "extra": extra or {},
    }

    runs = _runs_dir(profile)
    detail_path = os.path.join(runs, file_day, f"{run_id}.json")
    index_path = os.path.join(runs, "index.jsonl")
    rec["detail_path"] = os.path.relpath(detail_path, BASE_DIR)

    _write_json(detail_path, rec)
    _append_jsonl(index_path, {
        "run_id": run_id,
        "profile": profile,
        "program": program,
        "phase": phase,
        "slot": slot,
        "asof": day,
        "ok": rec["ok"],
        "error": error,
        "summary": summary or {},
        "triggered_by": triggered_by,
        "strategy_version": rec["strategy_version"],
        "experiment_id": rec["experiment_id"],
        "started_at": started,
        "finished_at": finished,
        "detail_path": rec["detail_path"],
    })
    # 最新指针，方便 UI / 人工快速看
    _write_json(os.path.join(runs, "latest.json"), {
        "run_id": run_id,
        "program": program,
        "profile": profile,
        "finished_at": finished,
        "ok": rec["ok"],
        "detail_path": rec["detail_path"],
        "summary": summary or {},
    })
    return rec


@contextmanager
def track_run(
    program: str,
    *,
    profile: str = "v1",
    phase: str | None = None,
    slot: str | None = None,
    asof: str | None = None,
    args: dict | None = None,
    triggered_by: str = "cli",
    strategy_version: str | None = None,
    experiment_id: str | None = None,
):
    """
    with track_run(...) as ctx:
        ctx['result'] = ...
        ctx['summary'] = {...}
    结束时自动落盘；异常也会记录 ok=False。
    """
    started = datetime.now().isoformat(timespec="seconds")
    ctx: dict[str, Any] = {
        "result": None,
        "summary": {},
        "extra": {},
        "ok": True,
        "error": None,
    }
    try:
        yield ctx
    except Exception as e:
        ctx["ok"] = False
        ctx["error"] = f"{type(e).__name__}: {e}"
        ctx.setdefault("extra", {})["traceback"] = traceback.format_exc()[-4000:]
        record_run(
            program=program,
            profile=profile,
            phase=phase,
            slot=slot,
            asof=asof,
            args=args,
            ok=False,
            error=ctx["error"],
            summary=ctx.get("summary") or {},
            result=ctx.get("result"),
            triggered_by=triggered_by,
            strategy_version=strategy_version,
            experiment_id=experiment_id,
            started_at=started,
            extra=ctx.get("extra"),
        )
        raise
    else:
        record_run(
            program=program,
            profile=profile,
            phase=phase,
            slot=slot,
            asof=asof,
            args=args,
            ok=bool(ctx.get("ok", True)),
            error=ctx.get("error"),
            summary=ctx.get("summary") or {},
            result=ctx.get("result"),
            triggered_by=triggered_by,
            strategy_version=strategy_version,
            experiment_id=experiment_id,
            started_at=started,
            extra=ctx.get("extra"),
        )


def list_recent(profile: str = "v1", limit: int = 50) -> list[dict]:
    path = os.path.join(_runs_dir(profile), "index.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:][::-1]


def summarize_pipeline_result(out: dict) -> dict:
    """从 pipeline 返回值抽摘要，避免索引过大。"""
    if not isinstance(out, dict):
        return {"raw_type": type(out).__name__}
    steps = out.get("steps") or {}
    summary = {
        "asof": out.get("asof"),
        "phase": out.get("phase"),
        "ok": out.get("ok", True),
        "paper": out.get("paper"),
        "profile": out.get("profile"),
    }
    lu = steps.get("limitup") or {}
    if lu:
        summary["limitup_added"] = lu.get("added")
        summary["limitup_days"] = lu.get("days")
    w = steps.get("watch") or {}
    if w:
        summary["watch_promoted"] = w.get("promoted")
        summary["watch_candidates"] = w.get("candidates")
    b = steps.get("buy") or {}
    if b:
        acts = b.get("actions") or []
        summary["buy_actions"] = len(acts)
        summary["buy_types"] = _count_types(acts)
    s = steps.get("sell") or {}
    if s:
        acts = s.get("actions") or []
        summary["sell_actions"] = len(acts)
        summary["sell_types"] = _count_types(acts)
    # 顶层直接是 scan 结果
    if "added" in out and "limitup_added" not in summary:
        summary["added"] = out.get("added")
    if "promoted" in out:
        summary["promoted"] = out.get("promoted")
    if "actions" in out:
        summary["actions"] = len(out.get("actions") or [])
        summary["action_types"] = _count_types(out.get("actions") or [])
    return summary


def _count_types(actions: list) -> dict:
    from collections import Counter
    c = Counter(str(a.get("type") or "?") for a in actions if isinstance(a, dict))
    return dict(c)
