#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主板/创业板回填 · 免费多源取数（单次单请求，带磁盘缓存）。

涨停池：东财 / 同花顺 /（可选）efinance
日线趋势：腾讯 / 新浪 / 东财 / baostock /（可选）mootdx
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd
import requests

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_LIB_DIR)
ZT_CACHE_DIR = os.path.join(_BASE_DIR, "data", "zt_pool_cache")
os.makedirs(ZT_CACHE_DIR, exist_ok=True)

_ZT_MEM: dict[str, pd.DataFrame] = {}
_DEFAULT_SLEEP = 1.0

_ZT_COLS = (
    "序号", "代码", "名称", "涨跌幅", "最新价", "成交额",
    "流通市值", "总市值", "换手率", "封板资金", "首次封板时间",
    "最后封板时间", "炸板次数", "涨停统计", "连板数", "所属行业",
)


def _sleep(sec: float = _DEFAULT_SLEEP) -> None:
    time.sleep(max(0.2, sec))


def _empty_zt() -> pd.DataFrame:
    return pd.DataFrame(columns=list(_ZT_COLS))


def _normalize_zt(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None or getattr(df, "empty", True):
        return _empty_zt()
    out = df.copy()
    if "代码" not in out.columns:
        return _empty_zt()
    out["代码"] = out["代码"].astype(str).str.zfill(6)
    for col, default in (("涨跌幅", 0.0), ("成交额", 0.0), ("换手率", 0.0), ("名称", "")):
        if col not in out.columns:
            out[col] = default
    return out


def zt_metrics_usable(df: Optional[pd.DataFrame]) -> bool:
    """非空涨停池是否具备可打分字段。

    同花顺部分日期接口只回 code/name，涨跌幅/成交额/换手全 0。
    这种「空壳池」不可作权威缓存，否则会毒死 live 评分（2026-10-30 实锤）。
    空表视为可用（表示当日确认无涨停，允许缓存避免反复打穿）。
    """
    if df is None:
        return False
    if getattr(df, "empty", True):
        return True
    out = _normalize_zt(df)
    zf = pd.to_numeric(out.get("涨跌幅"), errors="coerce").fillna(0.0)
    amt = pd.to_numeric(out.get("成交额"), errors="coerce").fillna(0.0)
    hs = pd.to_numeric(out.get("换手率"), errors="coerce").fillna(0.0)
    return bool((zf > 0).any() or (amt > 0).any() or (hs > 0).any())


def _cache_path(date8: str) -> str:
    return os.path.join(ZT_CACHE_DIR, f"{date8}.json")


def load_zt_disk(date8: str) -> Optional[pd.DataFrame]:
    path = _cache_path(date8)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        if not rows:
            return _empty_zt()
        df = _normalize_zt(pd.DataFrame(rows))
        if not zt_metrics_usable(df):
            # 空壳缓存作废，改名保留取证，下次走实时源
            try:
                os.replace(path, path + ".sparse_bad")
            except OSError:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return None
        return df
    except Exception:
        return None


def save_zt_disk(date8: str, df: pd.DataFrame) -> None:
    """只落可打分池或确认空表；空壳（有代码但指标全 0）禁止写入。"""
    norm = _normalize_zt(df)
    if not norm.empty and not zt_metrics_usable(norm):
        return
    path = _cache_path(date8)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    records = norm.to_dict(orient="records")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    os.replace(tmp, path)


def fetch_zt_em(date8: str) -> pd.DataFrame:
    import akshare as ak
    try:
        from platform_gate import platform_slot
    except Exception:  # pragma: no cover
        from contextlib import contextmanager

        @contextmanager
        def platform_slot(_n: str):
            yield

    with platform_slot("eastmoney"):
        df = ak.stock_zt_pool_em(date=date8)
    return _normalize_zt(df)


def fetch_zt_ths(date8: str) -> pd.DataFrame:
    """同花顺涨停池 → 对齐东财列名（缺省成交额/换手用 0，粗排仍可用涨幅）。"""
    try:
        from platform_gate import platform_slot
    except Exception:  # pragma: no cover
        from contextlib import contextmanager

        @contextmanager
        def platform_slot(_n: str):
            yield

    url = (
        "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
        f"?page=1&limit=200&date={date8}"
    )
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.10jqka.com.cn/"}
    with platform_slot("ths"):
        r = requests.get(url, timeout=20, headers=headers)
        r.raise_for_status()
        info = ((r.json() or {}).get("data") or {}).get("info") or []
    rows = []
    for x in info:
        code = str(x.get("code") or "").zfill(6)
        name = str(x.get("name") or "").strip()
        if not code or code.startswith(("688", "8", "4")):
            continue
        if "ST" in name.upper():
            continue
        again = x.get("is_again_limit")
        tag = str(x.get("change_tag") or "")
        # 这里落的是全量涨停池，连板必须留下。旧逻辑把连板丢掉再写入磁盘，
        # 次日用这份残池做「不在池里=首板」，三连板及以上会重新混进首板。
        high = x.get("high_days")
        if high in (None, ""):
            high = x.get("continue_num") or x.get("limit_up_days")
        try:
            lianban = int(float(high)) if high not in (None, "") else None
        except (TypeError, ValueError):
            lianban = None
        if lianban is None:
            if tag == "FIRST_LIMIT" or again in (0, "0", False, None, ""):
                lianban = 1 if tag == "FIRST_LIMIT" or again in (0, "0", False) else None
            elif again in (1, "1", True) or tag == "LIMIT_BACK":
                lianban = 2
        try:
            zf = float(x.get("change_rate") or x.get("pct_chg") or 0)
        except (TypeError, ValueError):
            zf = 0.0
        try:
            amount = float(x.get("turnover") or x.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            hs = float(x.get("turnover_rate") or x.get("hs") or 0)
        except (TypeError, ValueError):
            hs = 0.0
        row = {
            "代码": code, "名称": name, "涨跌幅": zf,
            "成交额": amount, "换手率": hs,
        }
        if lianban is not None:
            row["连板数"] = lianban
        rows.append(row)
    return _normalize_zt(pd.DataFrame(rows) if rows else None)


def fetch_zt_efinance(date8: str) -> pd.DataFrame:
    import efinance as ef
    # efinance 涨停池：date 格式 YYYYMMDD
    df = ef.stock.get_daily_billboard(start_date=date8, end_date=date8)
    # 龙虎榜 ≠ 涨停池；若无专用接口则跳过
    if df is None or getattr(df, "empty", True):
        return _empty_zt()
    # 尽力映射
    colmap = {}
    for a, b in (("股票代码", "代码"), ("股票名称", "名称"), ("涨跌幅", "涨跌幅")):
        if a in df.columns:
            colmap[a] = b
    if "代码" not in colmap.values() and "股票代码" not in df.columns:
        return _empty_zt()
    out = df.rename(columns=colmap)
    return _normalize_zt(out)


ZT_FETCHERS: dict[str, Callable[[str], pd.DataFrame]] = {
    "em": fetch_zt_em,
    "ths": fetch_zt_ths,
}


def _register_optional_zt() -> None:
    try:
        import efinance  # noqa: F401
        # efinance 无稳定涨停池 API 时不注册；保留探测位
    except Exception:
        pass


_register_optional_zt()


def fetch_zt_pool(date8: str, prefer: str = "em", allow_fallback: bool = True) -> pd.DataFrame:
    """磁盘缓存优先；prefer 源失败时可回退其它源。缓存命中不 sleep。

    空壳池（有票但涨跌幅/成交额/换手全 0）不落盘、不进内存权威缓存；
    仍可作最后 fallback 返回给调用方自行 enrich。
    """
    if date8 in _ZT_MEM:
        mem = _ZT_MEM[date8]
        if mem.empty or zt_metrics_usable(mem):
            return mem
        _ZT_MEM.pop(date8, None)
    disk = load_zt_disk(date8)
    if disk is not None:
        # 含空表缓存：避免周末/无数据日反复打接口
        _ZT_MEM[date8] = disk
        return disk

    order = [prefer] + [k for k in ZT_FETCHERS if k != prefer]
    if not allow_fallback:
        order = [prefer]
    last_err = None
    sparse_fallback: Optional[pd.DataFrame] = None
    for src in order:
        fn = ZT_FETCHERS.get(src)
        if not fn:
            continue
        try:
            _sleep(1.0)
            df = fn(date8)
            if df is not None and not df.empty and "代码" in df.columns:
                if zt_metrics_usable(df):
                    save_zt_disk(date8, df)
                    _ZT_MEM[date8] = df
                    return df
                # 空壳：记下，继续试下一源（常见于同花顺历史日精简字段）
                sparse_fallback = _normalize_zt(df)
                continue
            # 空结果也尝试下一源
            if df is not None and df.empty:
                continue
        except Exception as e:
            last_err = e
            continue
    if sparse_fallback is not None and not sparse_fallback.empty:
        # 不落盘，避免毒缓存；调用方应 enrich 后再用
        return sparse_fallback
    # 空也缓存，避免同日反复打穿
    empty = _empty_zt()
    save_zt_disk(date8, empty)
    _ZT_MEM[date8] = empty
    if last_err:
        raise last_err
    return empty


def prewarm_zt_window(end_dt: datetime, days: int = 30, prefer: str = "em") -> None:
    """批量预热涨停池缓存（只对未缓存日期发请求）。"""
    for i in range(days):
        d = (end_dt - timedelta(days=i)).strftime("%Y%m%d")
        try:
            fetch_zt_pool(d, prefer=prefer)
        except Exception:
            continue


# ── 日线 / 趋势 ─────────────────────────────────────────────

def _ma_trend_from_closes(closes: list[float]) -> int:
    if len(closes) < 10:
        return 0
    ma5 = sum(closes[-5:]) / 5.0
    ma10 = sum(closes[-10:]) / 10.0
    return 1 if ma5 > ma10 else 0


def hist_closes_tx(code: str, start8: str, end8: str) -> list[float]:
    import akshare as ak
    df = ak.stock_zh_a_hist_tx(
        symbol=code, start_date=start8, end_date=end8, adjust="qfq")
    if df is None or df.empty:
        return []
    col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
    if not col:
        return []
    return [float(x) for x in df[col].tolist() if x is not None]


def hist_closes_em(code: str, start8: str, end8: str) -> list[float]:
    import akshare as ak
    df = ak.stock_zh_a_hist(
        symbol=code, period="daily", start_date=start8, end_date=end8, adjust="qfq")
    if df is None or df.empty or "收盘" not in df.columns:
        return []
    return [float(x) for x in df["收盘"].tolist()]


def hist_closes_sina(code: str, start8: str, end8: str) -> list[float]:
    import akshare as ak
    # 新浪日线：stock_zh_a_daily；symbol 需 sh/sz 前缀
    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    df = ak.stock_zh_a_daily(
        symbol=f"{prefix}{code}", start_date=start8, end_date=end8, adjust="qfq")
    if df is None or df.empty:
        return []
    col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
    if not col:
        return []
    return [float(x) for x in df[col].tolist()]


_BS_LOGIN = {"ok": False}


def hist_closes_bs(code: str, start8: str, end8: str) -> list[float]:
    if os.environ.get("DISABLE_BAOSTOCK") == "1":
        return []
    import baostock as bs
    if _BS_LOGIN.get("dead"):
        return []
    if not _BS_LOGIN["ok"]:
        try:
            lg = bs.login()
            _BS_LOGIN["ok"] = lg.error_code == "0"
        except Exception:
            _BS_LOGIN["ok"] = False
        if not _BS_LOGIN["ok"]:
            _BS_LOGIN["dead"] = True  # 本进程不再重试，避免卡死
            return []
    if code.startswith(("6", "9")):
        full = "sh." + code
    elif code.startswith(("4", "8")):
        full = "bj." + code
    else:
        full = "sz." + code
    start = f"{start8[:4]}-{start8[4:6]}-{start8[6:]}"
    end = f"{end8[:4]}-{end8[4:6]}-{end8[6:]}"
    try:
        rs = bs.query_history_k_data_plus(
            full, "date,close", start_date=start, end_date=end,
            frequency="d", adjustflag="2")
    except Exception:
        _BS_LOGIN["dead"] = True
        return []
    closes = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        try:
            closes.append(float(row[1]))
        except (TypeError, ValueError, IndexError):
            pass
    return closes


def hist_closes_mootdx(code: str, start8: str, end8: str) -> list[float]:
    from mootdx.quotes import Quotes
    client = Quotes.factory(market="std")
    market = 1 if code.startswith(("5", "6", "9")) else 0
    df = client.bars(symbol=code, frequency=9, offset=40)
    if df is None or getattr(df, "empty", True):
        return []
    col = "close" if "close" in df.columns else None
    if not col:
        return []
    return [float(x) for x in df[col].tolist()]


HIST_FETCHERS: dict[str, Callable[[str, str, str], list[float]]] = {
    "tx": hist_closes_tx,
    "em": hist_closes_em,
    "sina": hist_closes_sina,
    "bs": hist_closes_bs,
}


def _register_optional_hist() -> None:
    try:
        import mootdx  # noqa: F401
        HIST_FETCHERS["mootdx"] = hist_closes_mootdx
    except Exception:
        pass


_register_optional_hist()


def get_trend(code: str, prefer: str = "tx", end: Optional[datetime] = None,
              *, allow_fallback: bool = False) -> int:
    """趋势指标。分轨模式默认只用 prefer 源，避免串台打爆其它平台。"""
    end = end or datetime.now()
    start = end - timedelta(days=40)
    start8, end8 = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    order = [prefer]
    if allow_fallback:
        order += [k for k in HIST_FETCHERS if k != prefer]
    for src in order:
        fn = HIST_FETCHERS.get(src)
        if not fn:
            continue
        try:
            _sleep(0.85)
            closes = fn(code, start8, end8)
            if len(closes) >= 10:
                return _ma_trend_from_closes(closes)
        except Exception:
            continue
    return 0


def gene_from_cached_pools(code: str, end_dt: datetime, days: int = 30) -> int:
    """只读内存/磁盘涨停池算基因；调用前应 prewarm_zt_window。"""
    count = 0
    code = str(code).zfill(6)
    for i in range(days):
        d = (end_dt - timedelta(days=i)).strftime("%Y%m%d")
        df = _ZT_MEM.get(d)
        if df is None:
            df = load_zt_disk(d)
            if df is not None:
                _ZT_MEM[d] = df
        if df is not None and not df.empty and code in set(df["代码"].astype(str)):
            count += 1
    return count


def list_available_lanes() -> list[dict]:
    """返回可开的分轨定义：每轨绑定涨停池源 + 日线源。"""
    lanes = [
        {"id": "em_tx", "zt": "em", "hist": "tx", "label": "东财涨停+腾讯日线"},
        {"id": "ths_tx", "zt": "ths", "hist": "tx", "label": "同花顺涨停+腾讯日线"},
        {"id": "em_em", "zt": "em", "hist": "em", "label": "东财涨停+东财日线"},
        {"id": "ths_sina", "zt": "ths", "hist": "sina", "label": "同花顺涨停+新浪日线"},
        {"id": "em_sina", "zt": "em", "hist": "sina", "label": "东财涨停+新浪日线"},
        {"id": "ths_em", "zt": "ths", "hist": "em", "label": "同花顺涨停+东财日线"},
    ]
    # baostock 仅在未禁用且可登录时启用（避免 login failed 卡死整轮）
    if os.environ.get("DISABLE_BAOSTOCK") != "1":
        lanes.append({"id": "em_bs", "zt": "em", "hist": "bs", "label": "东财涨停+baostock日线"})
        lanes.append({"id": "ths_bs", "zt": "ths", "hist": "bs", "label": "同花顺涨停+baostock日线"})
    if "mootdx" in HIST_FETCHERS:
        lanes.append({"id": "em_mootdx", "zt": "em", "hist": "mootdx", "label": "东财涨停+通达信"})
        lanes.append({"id": "ths_mootdx", "zt": "ths", "hist": "mootdx", "label": "同花顺涨停+通达信"})
    return lanes
