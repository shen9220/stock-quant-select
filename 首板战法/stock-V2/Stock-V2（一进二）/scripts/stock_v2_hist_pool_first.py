#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 历史回补 · 优先级1：只保涨停池（轻量）

对每个 D1：
  1) fetch_limit_up_pool(D0) 暖磁盘缓存（ths/em）
  2) 若旧预筛为「D0涨停池为空」或无文件 → 写轻量预筛：
       candidates = 池内代码（仅剔 ST/科创/北交），不做连板/换手/竞价
       meta.pool_first = true
  3) 已有 OK/有候选 → 跳过

不占 tdx；不跑买卖价；完整 build_d0_universe / 竞价另阶段。
规范：文档存放/V2历史回补标准流程.md
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # Stock-V2（一进二）
_SHOU = os.path.dirname(os.path.dirname(_ROOT))  # 首板战法
_PUBLIC = os.path.join(_SHOU, "公共")
_SHARED = os.path.join(_SHOU, "规范文档", "共享取数", "lib")
_UI = os.path.join(_SHOU, "交易记录UI")
_UI_LIB = os.path.join(_UI, "lib")
for p in (_SHARED, _PUBLIC, _ROOT, _UI, _UI_LIB):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DISABLE_BAOSTOCK", "1")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)

import fetch_calendar as fc  # noqa: E402
import stock_meta  # noqa: E402
import stock_v2 as v2  # noqa: E402
from hist_market_data import fetch_limit_up_pool, pool_meta  # noqa: E402
from platform_gate import quota_status  # noqa: E402

UNTIL = os.environ.get("V2_HIST_UNTIL", "2026-01-01")
FORCE = os.environ.get("FORCE", "0") == "1"
SLEEP = float(os.environ.get("POOL_SLEEP", "0.4"))
CURSOR = os.path.join(v2.CACHE_DIR, "hist_pool_first_cursor.json")


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _save_cursor(d1: str, extra: dict | None = None) -> None:
    payload = {"d1": d1, "updated_at": datetime.now().isoformat(timespec="seconds")}
    if extra:
        payload.update(extra)
    tmp = CURSOR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CURSOR)


def _need_rewrite(d1s: str) -> bool:
    if FORCE:
        return True
    path = os.path.join(v2.CACHE_DIR, f"{d1s}_d0_preselect.json")
    if not os.path.isfile(path):
        return True
    try:
        pre = json.load(open(path, encoding="utf-8"))
    except Exception:
        return True
    reason = str(pre.get("reason") or "")
    n_cand = len(pre.get("candidates") or [])
    if "涨停池为空" in reason or reason == "无预筛候选":
        return True
    if pre.get("pool_first") and n_cand > 0 and not FORCE:
        return False
    if n_cand > 0 or pre.get("status") == "OK":
        return False
    return True


def _iter_d1(until: str) -> list[str]:
    until_d = datetime.strptime(until, "%Y-%m-%d")
    days: list[str] = []
    cur = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    while cur >= until_d:
        if fc.is_trading_day(cur):
            days.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return days


def _minimal_preselect(d1s: str, d0s: str, df) -> dict:
    cands = []
    rejects = []
    for _, row in df.iterrows():
        code = str(row.get("代码") or "").zfill(6)
        name = str(row.get("名称") or "")
        if not code or code.startswith(("688", "8", "4")):
            continue
        if "ST" in name.upper():
            rejects.append({"code": code, "name": name, "reject": "REJECT_BOARD_OR_ST"})
            continue
        if not v2.allowed_code(code):
            rejects.append({"code": code, "name": name, "reject": "REJECT_BOARD_OR_ST"})
            continue
        cands.append({
            "code": code,
            "name": name,
            "d0": d0s,
            "d1": d1s,
            "d0_pct": float(row.get("涨跌幅") or 0),
            "d0_amount": float(row.get("成交额") or 0),
            "d0_turnover_pct": float(row.get("换手率") or 0),
            "board": stock_meta.board_of_code(code) or "",
            "pool_first": True,
            "note": "轻量池：未做连板/换手硬过滤，待后续阶段",
        })
    return {
        "d0": d0s,
        "d1": d1s,
        "status": "OK" if cands else "NO_TRADE",
        "reason": None if cands else "D0涨停池为空",
        "pool_first": True,
        "candidates": cands,
        "rejects": rejects[:200],
        "reject_count": len(rejects),
        "candidate_count": len(cands),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "V2_POOL_FIRST lightweight preselect",
    }


def main() -> int:
    v2._ensure_dirs()
    days = _iter_d1(UNTIL)
    _log(f"pool_first(light) start n_days={len(days)} until={UNTIL} FORCE={int(FORCE)}")
    done = skip = fail = warm_ok = 0

    for i, d1s in enumerate(days, 1):
        try:
            d1 = datetime.strptime(d1s, "%Y-%m-%d")
            d0 = v2.prev_trading_day(d1)
            d0s = d0.strftime("%Y-%m-%d")
            d0_8 = d0.strftime("%Y%m%d")

            df = fetch_limit_up_pool(d0_8, prefer="em", allow_sparse=True)
            meta = pool_meta(df)
            if meta.get("n", 0) > 0:
                warm_ok += 1
            _log(f"{d1s} D0={d0s} pool={meta}")

            if not _need_rewrite(d1s):
                skip += 1
                _save_cursor(d1s, {"phase": "skip", "pool": meta})
            else:
                if meta.get("n", 0) <= 0:
                    out = {
                        "d0": d0s, "d1": d1s, "status": "NO_TRADE",
                        "reason": "D0涨停池为空", "pool_first": True,
                        "candidates": [], "rejects": [],
                        "candidate_count": 0, "reject_count": 0,
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                else:
                    out = _minimal_preselect(d1s, d0s, df)
                path = os.path.join(v2.CACHE_DIR, f"{d1s}_d0_preselect.json")
                v2._save_json(path, out)
                done += 1
                _log(
                    f"{d1s} wrote light preselect status={out.get('status')} "
                    f"cand={out.get('candidate_count')} reason={out.get('reason')}"
                )
                _save_cursor(
                    d1s,
                    {
                        "phase": "write",
                        "status": out.get("status"),
                        "cand": out.get("candidate_count"),
                        "pool": meta,
                    },
                )

            if i % 10 == 0:
                _log(f"progress {i}/{len(days)} done={done} skip={skip} fail={fail} warm_ok={warm_ok}")

            q = quota_status()
            for p in ("ths", "eastmoney"):
                st = q.get(p) or {}
                if st.get("stopped"):
                    _log(f"QUOTA_STOP {p} → 收工 at {d1s}")
                    return 2
            time.sleep(SLEEP)
        except Exception as e:
            fail += 1
            _log(f"{d1s} FAIL {type(e).__name__}: {e}")
            _log(traceback.format_exc()[-500:])
            _save_cursor(d1s, {"phase": "fail", "error": str(e)})
            time.sleep(max(1.0, SLEEP))

    _log(f"pool_first(light) complete done={done} skip={skip} fail={fail} warm_ok={warm_ok}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
