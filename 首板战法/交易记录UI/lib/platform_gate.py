#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨进程平台门禁：互斥 + 最小间隔 + 小时/夜班配额硬停。

铁律：
  - 同一平台同时只能 1 个在途请求
  - 平台之间可以并行
  - 读 data/platform_rate_limits.json；到 max_per_hour / max_per_night 必须关停
  - 不追求一两天跑完；超限宁停勿撞
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_LIB_DIR)
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)
    sys.path.insert(0, _LIB_DIR)
from runtime_paths import BASE_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

_LOCK_DIR = os.path.join(DATA_DIR, "platform_gates")
_LIMIT_FILE = os.path.join(DATA_DIR, "platform_rate_limits.json")
_STATE_DIR = os.path.join(DATA_DIR, "platform_quota_state")
os.makedirs(_LOCK_DIR, exist_ok=True)
os.makedirs(_STATE_DIR, exist_ok=True)

# 配额未加载时的兜底（更严）
_FALLBACK = {
    "sohu": {"min_interval_sec": 2.0, "max_per_hour": 60, "max_per_night": 200, "soft_cap_pct": 0.80},
    "tencent": {"min_interval_sec": 2.5, "max_per_hour": 80, "max_per_night": 400, "soft_cap_pct": 0.80},
    "sina": {"min_interval_sec": 3.0, "max_per_hour": 40, "max_per_night": 150, "soft_cap_pct": 0.80},
    "em": {"min_interval_sec": 3.0, "max_per_hour": 30, "max_per_night": 80, "soft_cap_pct": 0.80},
    "eastmoney": {"min_interval_sec": 3.0, "max_per_hour": 40, "max_per_night": 120, "max_per_day": 400, "soft_cap_pct": 0.80},
    "ths": {"min_interval_sec": 2.0, "max_per_hour": 50, "max_per_night": 150, "max_per_day": 300, "soft_cap_pct": 0.80},
    "baostock": {"min_interval_sec": 1.5, "max_per_hour": 200, "max_per_day": 800, "soft_cap_pct": 0.80},
    "tdx": {"min_interval_sec": 1.0, "max_per_hour": 600, "max_per_day": 2000, "soft_cap_pct": 0.70},
    # 别名映射到 tencent
    "qq": None,
    "tx": None,
}


class PlatformQuotaExceeded(RuntimeError):
    """该平台已达小时或夜班上限，必须关停。"""

    def __init__(self, platform: str, kind: str, used: int, limit: int):
        self.platform = platform
        self.kind = kind
        self.used = used
        self.limit = limit
        super().__init__(
            f"QUOTA_STOP platform={platform} kind={kind} used={used} limit={limit}"
        )


class PlatformMorningReserve(RuntimeError):
    """回补场景下生产源落入早盘预留窗（00:00–10:00），禁止占用配额。"""

    def __init__(self, platform: str, detail: str):
        self.platform = platform
        self.detail = detail
        super().__init__(f"MORNING_RESERVE platform={platform} {detail}")


def _backfill_respect_reserve() -> bool:
    """看板/买卖价回补入口置 BACKFILL_RESPECT_RESERVE=1；正式早盘模型勿设。"""
    v = (os.environ.get("BACKFILL_RESPECT_RESERVE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _morning_reserve_block(platform: str) -> str | None:
    if not _backfill_respect_reserve():
        return None
    try:
        from backfill_window import platform_backfill_block_reason  # noqa: WPS433
        return platform_backfill_block_reason(platform)
    except Exception:
        return None


def _canon(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p in ("qq", "tx", "tencent"):
        return "tencent"
    # 通达信族：mootdx / pytdx / tdx 共用配额，防封号
    if p in ("mootdx", "pytdx", "tongdaxin", "通达信"):
        return "tdx"
    if p in ("10jqka", "同花顺", "ths"):
        return "ths"
    if p in ("东财", "em_kline", "eastmoney"):
        return "eastmoney"
    return p


def day_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d")


def soft_limit(hard: int, pct: float | None = None) -> int:
    """铁律：实际可用 = floor(硬上限 × soft_cap_pct)。
    默认 80%（sohu/tencent/sina/baostock…）；tdx/mootdx 在 JSON 里强制 70%。"""
    if hard <= 0:
        return 0
    if pct is None:
        pct = 0.80
    return max(1, int(hard * float(pct)))


def _load_limits() -> dict:
    try:
        with open(_LIMIT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(_FALLBACK)


def limits_for(platform: str) -> dict:
    cfg = _load_limits()
    p = _canon(platform)
    block = cfg.get(p) or _FALLBACK.get(p) or {
        "min_interval_sec": 2.5,
        "max_per_hour": 50,
        "max_per_night": 150,
    }
    return block


def night_id(now: datetime | None = None) -> str:
    """回补会话 ID：与统一窗对齐 —— 当日 10:00 → 次日 00:00 同属一夜。

    00:00–10:00 仍记为「前一开窗日」会话（窗外通常已硬停，仅供状态查询）。
    旧口径（12:00 换日）会在 10:00–12:00 误吃昨夜软上限，已废。
    """
    now = now or datetime.now()
    if now.hour < 10:
        d = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        d = now.strftime("%Y-%m-%d")
    return f"{d}_night"


def hour_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%dT%H")


def _state_path(platform: str) -> str:
    return os.path.join(_STATE_DIR, f"{_canon(platform)}.json")


def _empty_state() -> dict:
    return {
        "night_id": "",
        "night_count": 0,
        "day_id": "",
        "day_count": 0,
        "hour_id": "",
        "hour_count": 0,
        "stopped": False,
        "stop_reason": "",
    }


def _read_state(platform: str) -> dict:
    path = _state_path(platform)
    if not os.path.isfile(path):
        return _empty_state()
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        for k, v in _empty_state().items():
            st.setdefault(k, v)
        return st
    except Exception:
        return _empty_state()


def _write_state(platform: str, state: dict) -> None:
    path = _state_path(platform)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def quota_status(platform: str | None = None) -> dict:
    """查询配额；platform=None 返回全部已知平台。"""
    plats = [_canon(platform)] if platform else [
        "sohu", "tencent", "sina", "baostock", "tdx", "ths", "eastmoney",
    ]
    out = {}
    for p in plats:
        lim = limits_for(p)
        st = _read_state(p)
        nid, hid, did = night_id(), hour_id(), day_id()
        soft = float(lim.get("soft_cap_pct", 0.80) or 0.80)
        n_hard = int(lim.get("max_per_night", 0) or 0)
        h_hard = int(lim.get("max_per_hour", 0) or 0)
        d_hard = int(lim.get("max_per_day", 0) or 0)
        n_lim = soft_limit(n_hard, soft) if n_hard else 0
        h_lim = soft_limit(h_hard, soft) if h_hard else 0
        d_lim = soft_limit(d_hard, soft) if d_hard else 0
        n_used = int(st.get("night_count", 0) or 0) if st.get("night_id") == nid else 0
        h_used = int(st.get("hour_count", 0) or 0) if st.get("hour_id") == hid else 0
        d_used = int(st.get("day_count", 0) or 0) if st.get("day_id") == did else 0
        stopped = bool(st.get("stopped")) and (
            (n_hard and st.get("night_id") == nid) or (d_hard and st.get("day_id") == did)
        )
        out[p] = {
            "night_id": nid,
            "night_used": n_used,
            "night_limit_hard": n_hard,
            "night_limit": n_lim,
            "day_id": did,
            "day_used": d_used,
            "day_limit_hard": d_hard,
            "day_limit": d_lim,
            "hour_used": h_used,
            "hour_limit_hard": h_hard,
            "hour_limit": h_lim,
            "soft_cap_pct": soft,
            "min_interval_sec": float(lim.get("min_interval_sec", 2.5)),
            "stopped": stopped,
            "stop_reason": st.get("stop_reason") or "",
            "remaining_night": max(0, n_lim - n_used) if n_lim else None,
            "remaining_day": max(0, d_lim - d_used) if d_lim else None,
            "remaining_hour": max(0, h_lim - h_used) if h_lim else None,
        }
    return out


def is_stopped(platform: str) -> bool:
    st = quota_status(platform).get(_canon(platform), {})
    if bool(st.get("stopped")):
        return True
    rem_n = st.get("remaining_night")
    if rem_n is not None and int(rem_n) <= 0:
        return True
    rem_d = st.get("remaining_day")
    if rem_d is not None and int(rem_d) <= 0:
        return True
    rem_h = st.get("remaining_hour")
    if rem_h is not None and int(rem_h) <= 0:
        return True
    return False


def mark_rate_limited(platform: str, detail: str = "429/ban observed") -> None:
    """外部观测到限流/封禁时立刻关停本夜。"""
    p = _canon(platform)
    lock_path, _ = _paths(p)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        st = _read_state(p)
        st["night_id"] = night_id()
        st["stopped"] = True
        st["stop_reason"] = detail
        st["stopped_at"] = datetime.now().isoformat()
        _write_state(p, st)
        print(f"[gate] HARD_STOP {p}: {detail}", flush=True)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _paths(platform: str) -> tuple[str, str]:
    p = _canon(platform)
    return (
        os.path.join(_LOCK_DIR, f"{p}.lock"),
        os.path.join(_LOCK_DIR, f"{p}.last"),
    )


def _check_and_bump(platform: str) -> dict:
    """在已持锁前提下：校验配额（硬上限×soft_cap_pct），超限抛 PlatformQuotaExceeded，否则 +1。"""
    p = _canon(platform)
    reserve_block = _morning_reserve_block(p)
    if reserve_block:
        raise PlatformMorningReserve(p, reserve_block)
    lim = limits_for(p)
    st = _read_state(p)
    nid, hid, did = night_id(), hour_id(), day_id()
    soft = float(lim.get("soft_cap_pct", 0.80) or 0.80)

    # 换日：重置日计数与 stopped（日平台如 tdx）
    if st.get("day_id") != did:
        st["day_id"] = did
        st["day_count"] = 0
        if int(lim.get("max_per_day", 0) or 0) > 0:
            st["stopped"] = False
            st["stop_reason"] = ""
    if st.get("night_id") != nid:
        st["night_id"] = nid
        st["night_count"] = 0
        if int(lim.get("max_per_night", 0) or 0) > 0:
            st["stopped"] = False
            st["stop_reason"] = ""
    if st.get("hour_id") != hid:
        st["hour_id"] = hid
        st["hour_count"] = 0

    if st.get("stopped"):
        raise PlatformQuotaExceeded(
            p, "stopped",
            int(st.get("day_count", 0) or st.get("night_count", 0)),
            soft_limit(int(lim.get("max_per_day", 0) or lim.get("max_per_night", 0) or 0), soft),
        )

    n_hard = int(lim.get("max_per_night", 0) or 0)
    h_hard = int(lim.get("max_per_hour", 0) or 0)
    d_hard = int(lim.get("max_per_day", 0) or 0)
    n_lim = soft_limit(n_hard, soft) if n_hard else 0
    h_lim = soft_limit(h_hard, soft) if h_hard else 0
    d_lim = soft_limit(d_hard, soft) if d_hard else 0
    n_used = int(st.get("night_count", 0) or 0)
    h_used = int(st.get("hour_count", 0) or 0)
    d_used = int(st.get("day_count", 0) or 0)

    if d_lim and d_used >= d_lim:
        st["stopped"] = True
        st["stop_reason"] = f"day_soft_cap {d_used}>={d_lim} (hard={d_hard}, pct={soft})"
        st["stopped_at"] = datetime.now().isoformat()
        _write_state(p, st)
        raise PlatformQuotaExceeded(p, "day", d_used, d_lim)
    if n_lim and n_used >= n_lim:
        st["stopped"] = True
        st["stop_reason"] = f"night_soft_cap {n_used}>={n_lim} (hard={n_hard}, pct={soft})"
        st["stopped_at"] = datetime.now().isoformat()
        _write_state(p, st)
        raise PlatformQuotaExceeded(p, "night", n_used, n_lim)
    if h_lim and h_used >= h_lim:
        raise PlatformQuotaExceeded(p, "hour", h_used, h_lim)

    if n_hard:
        st["night_count"] = n_used + 1
    if d_hard:
        st["day_count"] = d_used + 1
    st["hour_count"] = h_used + 1
    st["last_request_at"] = datetime.now().isoformat()
    _write_state(p, st)
    return st


@contextmanager
def platform_slot(platform: str, min_interval: float | None = None):
    """占用平台槽位：拿锁 → 配额检查 → 间隔等待 → yield → 记时间戳。

    超配额抛 PlatformQuotaExceeded（调用方必须停该轨，禁止重试硬撞）。
    """
    p = _canon(platform)
    lim = limits_for(p)
    interval = float(
        min_interval if min_interval is not None else lim.get("min_interval_sec", 2.5)
    )
    lock_path, last_path = _paths(p)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        _check_and_bump(p)
        last = 0.0
        try:
            with open(last_path, "r", encoding="utf-8") as f:
                last = float((f.read() or "0").strip() or "0")
        except Exception:
            last = 0.0
        wait = interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        yield
    finally:
        try:
            with open(last_path, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception:
            pass
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == "__main__":
    print(json.dumps(quota_status(), ensure_ascii=False, indent=2))
    print("limits file:", _LIMIT_FILE)
