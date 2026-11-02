"""
上证指数（sh.000001）月度涨跌幅。

口径：当月首个交易日收盘 → 当月最后一个可用交易日收盘
  pct = (last_close / first_close - 1) * 100
当前未结束月份：取已有最新交易日收盘。

缓存：data/sh000001_month_cache.json（日线收盘 + 月度结果）
取数优先：baostock → 新浪 K 线；失败则该月字段留空。
"""
from __future__ import annotations

import calendar
import json
import os
import threading
from datetime import date, datetime
from typing import Any, Optional

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_LIB_DIR)
_CACHE_PATH = os.path.join(_BASE_DIR, "data", "sh000001_month_cache.json")
_LOCK = threading.Lock()

_INDEX_CODE_BS = "sh.000001"
_SINA_SYMBOL = "sh000001"


def _today() -> date:
    return datetime.now().date()


def _month_bounds(ym: str) -> tuple[str, str]:
    """返回 YYYY-MM 的日历起止（月末截到今天）。"""
    y, m = int(ym[:4]), int(ym[5:7])
    start = date(y, m, 1)
    last_day = calendar.monthrange(y, m)[1]
    end = date(y, m, last_day)
    today = _today()
    if end > today:
        end = today
    return start.isoformat(), end.isoformat()


def _load_cache() -> dict:
    if not os.path.isfile(_CACHE_PATH):
        return {"daily": {}, "months": {}, "updated_at": None}
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"daily": {}, "months": {}, "updated_at": None}
        data.setdefault("daily", {})
        data.setdefault("months", {})
        return data
    except Exception:
        return {"daily": {}, "months": {}, "updated_at": None}


def _save_cache(data: dict) -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = _CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _CACHE_PATH)


def _fetch_baostock(start: str, end: str) -> dict[str, float]:
    try:
        import baostock as bs
    except ImportError:
        return {}
    out: dict[str, float] = {}
    try:
        lg = bs.login()
        if getattr(lg, "error_code", "1") != "0":
            return {}
        rs = bs.query_history_k_data_plus(
            _INDEX_CODE_BS,
            "date,close",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="3",
        )
        if rs.error_code != "0":
            return {}
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if not row or len(row) < 2:
                continue
            d, c = str(row[0]), row[1]
            try:
                out[d] = float(c)
            except (TypeError, ValueError):
                continue
    except Exception:
        return {}
    finally:
        try:
            bs.logout()
        except Exception:
            pass
    return out


def _fetch_sina(datalen: int = 800) -> dict[str, float]:
    """新浪日 K（无需东财代理）。"""
    try:
        import requests
    except ImportError:
        return {}
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={_SINA_SYMBOL}&scale=240&ma=5&datalen={datalen}"
    )
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            },
            timeout=15,
        )
        rows = r.json()
    except Exception:
        return {}
    out: dict[str, float] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = str(row.get("day") or "")[:10]
        try:
            out[d] = float(row.get("close"))
        except (TypeError, ValueError):
            continue
    return out


def _ensure_daily_range(cache: dict, start: str, end: str) -> dict[str, float]:
    daily: dict[str, float] = dict(cache.get("daily") or {})
    # 缺日则补拉整个区间（baostock 一次；失败再新浪）
    need = False
    # 粗检：起止附近是否有点
    in_range = [d for d in daily if start <= d <= end]
    if len(in_range) < 2:
        need = True
    # 当月未结束：末交易日可能落后于缓存 → 若 end==today 且最新点早于 end 超过 3 天则刷新
    today = _today().isoformat()
    if end >= today and in_range:
        latest = max(in_range)
        if latest < today:
            # 工作日可能缺最新一根
            need = True

    if not need:
        return daily

    fetched = _fetch_baostock(start, end)
    if not fetched:
        # 新浪一次拉较长历史，合并
        fetched = _fetch_sina(800)
    if fetched:
        daily.update(fetched)
        cache["daily"] = daily
    return daily


def _calc_month(daily: dict[str, float], ym: str) -> Optional[dict[str, Any]]:
    start, end = _month_bounds(ym)
    closes = sorted((d, c) for d, c in daily.items() if start <= d <= end)
    if len(closes) < 1:
        return None
    first_d, first_c = closes[0]
    last_d, last_c = closes[-1]
    if not first_c:
        return None
    pct = round((last_c / first_c - 1.0) * 100.0, 2)
    return {
        "pct": pct,
        "profit": pct >= 0,
        "first_date": first_d,
        "last_date": last_d,
        "first_close": round(first_c, 4),
        "last_close": round(last_c, 4),
        "source": "sh000001",
    }


def get_month_index_stats(ym: str, *, force: bool = False) -> dict[str, Any]:
    """
    返回单月大盘字段：
      index_pct: float | None
      index_profit: bool | None   # True=盈(红/是) False=亏(绿/否)
    """
    empty = {"index_pct": None, "index_profit": None}
    if not ym or len(ym) < 7 or ym[4] != "-":
        return empty
    ym = ym[:7]
    with _LOCK:
        cache = _load_cache()
        months = cache.setdefault("months", {})
        today = _today().isoformat()
        cached = months.get(ym) if not force else None
        # 历史完整月可直接用缓存；当月或缓存末日偏旧则重算
        if cached and isinstance(cached, dict) and cached.get("pct") is not None:
            start, end = _month_bounds(ym)
            is_open_month = end >= today
            if not is_open_month:
                return {
                    "index_pct": cached.get("pct"),
                    "index_profit": bool(cached.get("profit")),
                }
            # 当月：若 last_date 已是 end（或当天）可复用
            if str(cached.get("last_date") or "") >= end:
                return {
                    "index_pct": cached.get("pct"),
                    "index_profit": bool(cached.get("profit")),
                }

        start, end = _month_bounds(ym)
        # 多取几天缓冲，便于首日对齐
        daily = _ensure_daily_range(cache, start, end)
        info = _calc_month(daily, ym)
        if info:
            months[ym] = info
            cache["months"] = months
            _save_cache(cache)
            return {"index_pct": info["pct"], "index_profit": info["profit"]}
        return empty


def attach_index_to_rows(rows: list[dict]) -> list[dict]:
    """给历史看板行挂上证月度字段（同一月份 rank1/rank2 共用）。

    先按本页月份并集一次补全日线，再逐月计算，避免每行 login/logout。
    """
    months = sorted({str(r.get("month") or "")[:7] for r in rows if r.get("month")})
    if months:
        with _LOCK:
            cache = _load_cache()
            start = min(_month_bounds(m)[0] for m in months)
            end = max(_month_bounds(m)[1] for m in months)
            daily = _ensure_daily_range(cache, start, end)
            changed = False
            for ym in months:
                info = _calc_month(daily, ym)
                if info:
                    cache.setdefault("months", {})[ym] = info
                    changed = True
            if changed:
                _save_cache(cache)

    for r in rows:
        ym = str(r.get("month") or "")[:7]
        stats = get_month_index_stats(ym)
        r["index_pct"] = stats.get("index_pct")
        r["index_profit"] = stats.get("index_profit")
    return rows
