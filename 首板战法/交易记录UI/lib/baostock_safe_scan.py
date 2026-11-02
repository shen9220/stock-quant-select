#!/usr/bin/env python3
"""
Baostock 安全首板扫描 — 专供 16:00 调度器。

铁纪律（防再次黑名单）:
  - 全进程仅 1 次 login（由调用方保证，本模块不反复 login）
  - workers 永远 = 1（禁止线程池）
  - 每股一次 query（prev+d0 同窗），串行 + 固定间隔
  - 命中黑名单关键词立即抛出，由调度器写暂停文件并停跑
  - 禁止从 CLI 直接 bulk 调用；请用 baostock_backfill_scheduler.py
"""
from __future__ import annotations

import os
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_LIB_DIR)
_JOBS_DIR = os.path.join(_BASE_DIR, "jobs")
for _p in (_BASE_DIR, _LIB_DIR, _JOBS_DIR, os.path.dirname(_BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import BASE_DIR, ROOT_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import time
from typing import Callable

import price_service as ps

_BLACKLIST_KEYS = ("黑名单", "black", "封禁", "禁止", "频繁", "user login reject")


class BaostockBannedError(RuntimeError):
    """账号/IP 被限流或拉黑，必须立刻停跑。"""


def _raise_if_banned(msg: str) -> None:
    m = (msg or "").lower()
    if any(k.lower() in m for k in _BLACKLIST_KEYS):
        raise BaostockBannedError(msg)


def _limit_pct_threshold(code6: str) -> float:
    if code6.startswith(("300", "301")):
        return 19.5
    return 9.5


def scan_first_board_day(
    d0: str,
    universe: list[tuple[str, str, str]],
    *,
    interval_sec: float = 0.15,
    progress_every: int = 400,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """
    串行扫描全市场，返回首板候选（含 amount），按成交额降序。
    每股只打 1 次 query_history_k_data_plus（窗口=prev~d0）。
    """
    import baostock as bs

    if not ps.baostock_enabled() or not ps._bs_ensure_login():
        raise BaostockBannedError(ps._BS_DISABLE_MSG or "baostock 登录失败/已禁用")

    prev = ps.prev_trading_day(d0)
    if not prev:
        return []

    hits: list[dict] = []
    total = len(universe)
    for i, (code6, name, _secid) in enumerate(universe):
        thr = _limit_pct_threshold(code6)
        try:
            with ps._BS_LOCK:
                rs = bs.query_history_k_data_plus(
                    ps.to_bs_code(code6),
                    "date,pctChg,amount",
                    start_date=prev,
                    end_date=d0,
                    frequency="d",
                    adjustflag="3",
                )
            msg = rs.error_msg or ""
            if rs.error_code != "0":
                _raise_if_banned(msg)
                # 普通单票失败：跳过，不整批中断
            else:
                by_date: dict[str, tuple[float, float]] = {}
                while rs.error_code == "0" and rs.next():
                    row = rs.get_row_data()
                    if not row or len(row) < 3:
                        continue
                    try:
                        pct = float(row[1] or 0)
                        amt = float(row[2] or 0)
                    except (TypeError, ValueError):
                        continue
                    by_date[str(row[0])] = (pct, amt)
                cur = by_date.get(d0)
                prv = by_date.get(prev)
                if cur and cur[0] >= thr and (not prv or prv[0] < thr):
                    hits.append({"code": code6, "name": name, "amount": cur[1], "pct": cur[0]})
        except BaostockBannedError:
            raise
        except Exception as e:
            _raise_if_banned(str(e))

        if interval_sec > 0:
            time.sleep(interval_sec)
        if on_progress and ((i + 1) % progress_every == 0 or i + 1 == total):
            on_progress(i + 1, total, len(hits))

    hits.sort(key=lambda x: float(x.get("amount") or 0), reverse=True)
    return hits
