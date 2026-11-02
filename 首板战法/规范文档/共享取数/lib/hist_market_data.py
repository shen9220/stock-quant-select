#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨模型共享取数门面 — 历史涨停池 / 换手补齐 / 开盘买价 / 卖价估价。

权威说明：../历史涨停池与买卖竞价取数规范.md
实现委托：首板战法/交易记录UI/lib/board_*_sources.py + platform_gate

新模型应：
  from hist_market_data import fetch_limit_up_pool, enrich_turnover, estimate_buy_open
禁止再单独只调 ak.stock_zt_pool_em。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.dirname(_HERE)  # 规范文档/共享取数
_NORMS = os.path.dirname(_SHARED)  # 规范文档
_SHOU = os.path.dirname(_NORMS)  # 首板战法
_UI = os.path.join(_SHOU, "交易记录UI")
_UI_LIB = os.path.join(_UI, "lib")
for p in (_UI_LIB, _UI, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import board_buy_sources as buy_src  # noqa: E402
import board_data_sources as zt_src  # noqa: E402

try:
    from platform_gate import platform_slot, PlatformQuotaExceeded  # noqa: E402
except Exception:  # pragma: no cover
    from contextlib import contextmanager

    class PlatformQuotaExceeded(RuntimeError):
        pass

    @contextmanager
    def platform_slot(_name: str):
        yield


def _date8(s: str) -> str:
    return str(s).replace("-", "")[:8]


def _is_sparse_zt(df: Optional[pd.DataFrame]) -> bool:
    """有代码但换手/成交额几乎全空 → Stage A 可用，需 Stage B。"""
    if df is None or getattr(df, "empty", True) or "代码" not in df.columns:
        return True
    if "换手率" not in df.columns and "成交额" not in df.columns:
        return True
    turn = pd.to_numeric(df.get("换手率"), errors="coerce").fillna(0)
    amt = pd.to_numeric(df.get("成交额"), errors="coerce").fillna(0)
    return bool((turn <= 0).all() and (amt <= 0).all())


def fetch_limit_up_pool(
    date_yyyymmdd: str,
    *,
    prefer: str = "em",
    force_refresh: bool = False,
    allow_sparse: bool = True,
) -> pd.DataFrame:
    """
    Stage A：涨停池名单。

    - 默认走 V1 多源（磁盘 → em → ths）。
    - 磁盘若仅为「代码壳」（换手全 0）：默认仍返回（allow_sparse=True），
      并尝试 ths/em 覆盖富字段；覆盖失败也不清空名单。
    - force_refresh=True：忽略磁盘，重新打网（仍写回缓存）。
    """
    date8 = _date8(date_yyyymmdd)
    if force_refresh:
        zt_src._ZT_MEM.pop(date8, None)
        path = zt_src._cache_path(date8)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    df = zt_src.fetch_zt_pool(date8, prefer=prefer, allow_fallback=True)

    if df is not None and not df.empty and "代码" in df.columns:
        if not _is_sparse_zt(df):
            return df
        # 稀疏：尝试其它源刷新富字段，失败则保留名单
        if allow_sparse:
            for src in ("ths", "em"):
                if src == prefer:
                    continue
                fn = zt_src.ZT_FETCHERS.get(src)
                if not fn:
                    continue
                try:
                    time.sleep(0.8)
                    rich = fn(date8)
                    if (
                        rich is not None
                        and not rich.empty
                        and "代码" in rich.columns
                        and not _is_sparse_zt(rich)
                    ):
                        zt_src.save_zt_disk(date8, rich)
                        zt_src._ZT_MEM[date8] = rich
                        return rich
                except Exception:
                    continue
            return df
        return df

    return zt_src._empty_zt()


def enrich_turnover(
    df: pd.DataFrame,
    date_yyyymmdd: str,
    *,
    max_codes: Optional[int] = None,
    sleep_sec: float = 0.25,
) -> pd.DataFrame:
    """
    Stage B：在已有涨停名单上补换手率 / 成交额（日线）。

    不因补字段失败删除代码行；补到的写入列，补不到保持原值。
    默认走腾讯日线（platform=tencent）。
    """
    if df is None or getattr(df, "empty", True) or "代码" not in df.columns:
        return zt_src._empty_zt() if df is None else df

    out = df.copy()
    if "换手率" not in out.columns:
        out["换手率"] = 0.0
    if "成交额" not in out.columns:
        out["成交额"] = 0.0
    if "涨跌幅" not in out.columns:
        out["涨跌幅"] = 0.0

    date8 = _date8(date_yyyymmdd)
    day = f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"
    codes = [str(c).zfill(6) for c in out["代码"].tolist()]
    if max_codes is not None:
        codes = codes[: max(0, int(max_codes))]

    import json as _json

    import akshare as ak
    import requests

    turn_map: dict[str, float] = {}
    amt_map: dict[str, float] = {}
    pct_map: dict[str, float] = {}

    def _clear_proxy() -> dict:
        saved = {}
        for k in list(os.environ):
            if "proxy" in k.lower():
                saved[k] = os.environ.pop(k)
        return saved

    def _em_kline(code: str) -> Optional[dict[str, float]]:
        """东财 kline 直连（trust_env=False），f61=换手率。"""
        secid = f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&ut=7eea3edcaed734bea9cbfc24409ed989&klt=101&fqt=0&beg={date8}&end={date8}"
        )
        with platform_slot("eastmoney"):
            sess = requests.Session()
            sess.trust_env = False
            r = sess.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
                timeout=12,
            )
        if r.status_code != 200 or not r.text:
            return None
        klines = (((_json.loads(r.text) or {}).get("data") or {}).get("klines")) or []
        if not klines:
            return None
        parts = klines[0].split(",")
        # date,open,close,high,low,vol,amt,amp,pct,chg,turnover
        out: dict[str, float] = {}
        try:
            if len(parts) > 6:
                out["成交额"] = float(parts[6] or 0)
            if len(parts) > 8:
                out["涨跌幅"] = float(parts[8] or 0)
            if len(parts) > 10:
                out["换手率"] = float(parts[10] or 0)
        except (TypeError, ValueError):
            return out or None
        return out

    saved_proxy = _clear_proxy()
    try:
        for code in codes:
            got_turn = False
            try:
                bar = _em_kline(code)
                if bar:
                    if bar.get("换手率", 0) > 0:
                        turn_map[code] = float(bar["换手率"])
                        got_turn = True
                    elif "换手率" in bar:
                        turn_map[code] = float(bar.get("换手率") or 0)
                    if bar.get("成交额", 0) > 0:
                        amt_map[code] = float(bar["成交额"])
                    if "涨跌幅" in bar:
                        pct_map[code] = float(bar.get("涨跌幅") or 0)
            except PlatformQuotaExceeded:
                raise  # 软上限必须上抛，禁止吞掉继续硬撞
            except Exception:
                pass

            # 腾讯日线兜底成交额
            if code not in amt_map or not got_turn:
                try:
                    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
                    with platform_slot("tencent"):
                        hist = ak.stock_zh_a_hist_tx(
                            symbol=f"{prefix}{code}",
                            start_date=date8,
                            end_date=date8,
                            adjust="",
                        )
                    if hist is not None and not hist.empty:
                        row = hist.iloc[0]
                        if code not in amt_map:
                            for k_amt in ("amount", "成交额"):
                                if k_amt in hist.columns:
                                    try:
                                        amt_map[code] = float(row[k_amt])
                                    except (TypeError, ValueError):
                                        pass
                                    break
                except PlatformQuotaExceeded:
                    raise
                except Exception:
                    pass
            time.sleep(sleep_sec)
    finally:
        os.environ.update(saved_proxy)

    def _fill(col: str, mp: dict[str, float]) -> None:
        if not mp:
            return
        vals = []
        for _, r in out.iterrows():
            c = str(r["代码"]).zfill(6)
            cur = float(r.get(col) or 0)
            vals.append(mp.get(c, cur) if (c in mp and (cur <= 0 or col == "涨跌幅")) else cur)
        out[col] = vals

    _fill("换手率", turn_map)
    _fill("成交额", amt_map)
    _fill("涨跌幅", pct_map)
    out.attrs["enrich_turnover_hit"] = len(turn_map) + len(amt_map)
    out.attrs["enrich_date"] = day
    return out


def enrich_turnover_baostock(
    df: pd.DataFrame,
    date_yyyymmdd: str,
    *,
    max_codes: Optional[int] = None,
    sleep_sec: float = 0.05,
) -> pd.DataFrame:
    """Stage B 兜底：东财/腾讯触限或空壳时，用 baostock 日线补 涨跌幅/成交额/换手率。

    2026-11-02 实锤：腾讯夜软上限满 → enrich_turnover 整段跳过 → V2 预筛全员
    turnover_missing_after_enrich → 假空仓。baostock 日额度充裕，作硬兜底。

    铁纪律：本函数临时解除 DISABLE_BAOSTOCK（早盘脚本默认=1），否则兜底形同虚设。
    """
    if df is None or getattr(df, "empty", True) or "代码" not in df.columns:
        return zt_src._empty_zt() if df is None else df

    out = df.copy()
    for col in ("换手率", "成交额", "涨跌幅"):
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    date8 = _date8(date_yyyymmdd)
    day = f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"
    codes = [str(c).zfill(6) for c in out["代码"].tolist()]
    if max_codes is not None:
        codes = codes[: max(0, int(max_codes))]

    try:
        import baostock as bs
    except Exception:
        return out

    # 早盘入口常 export DISABLE_BAOSTOCK=1；Stage B 假空仓兜底必须能打 baostock
    _prev_disable = os.environ.pop("DISABLE_BAOSTOCK", None)
    zf_map: dict[str, float] = {}
    amt_map: dict[str, float] = {}
    hs_map: dict[str, float] = {}
    try:
        with platform_slot("baostock"):
            lg = bs.login()
            if getattr(lg, "error_code", "0") not in ("0", 0, None):
                return out
            try:
                for code in codes:
                    # 已有换手+成交额则跳过，省配额
                    try:
                        idx = out.index[out["代码"].astype(str).str.zfill(6) == code][0]
                        if float(out.at[idx, "换手率"] or 0) > 0 and float(
                            out.at[idx, "成交额"] or 0
                        ) > 0:
                            continue
                    except Exception:
                        pass
                    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
                    rs = bs.query_history_k_data_plus(
                        f"{prefix}.{code}",
                        "date,code,amount,turn,pctChg",
                        start_date=day,
                        end_date=day,
                        frequency="d",
                        adjustflag="3",
                    )
                    row = None
                    while rs.error_code == "0" and rs.next():
                        row = rs.get_row_data()
                        break
                    if not row or len(row) < 5:
                        continue
                    try:
                        amt_map[code] = float(row[2] or 0)
                    except (TypeError, ValueError):
                        pass
                    try:
                        hs_map[code] = float(row[3] or 0)
                    except (TypeError, ValueError):
                        pass
                    try:
                        zf_map[code] = float(row[4] or 0)
                    except (TypeError, ValueError):
                        pass
                    time.sleep(sleep_sec)
            finally:
                try:
                    bs.logout()
                except Exception:
                    pass
    finally:
        if _prev_disable is not None:
            os.environ["DISABLE_BAOSTOCK"] = _prev_disable

    def _fill(col: str, mp: dict[str, float]) -> None:
        if not mp:
            return
        vals = []
        for _, r in out.iterrows():
            c = str(r["代码"]).zfill(6)
            cur = float(pd.to_numeric(r.get(col), errors="coerce") or 0)
            vals.append(mp[c] if (c in mp and cur <= 0) else cur)
        out[col] = vals

    _fill("涨跌幅", zf_map)
    _fill("成交额", amt_map)
    _fill("换手率", hs_map)
    out.attrs["enrich_turnover_baostock_hit"] = len(zf_map) + len(amt_map) + len(hs_map)
    out.attrs["enrich_date"] = day
    return out


def estimate_buy_open(
    code: str,
    day: str,
    *,
    prefer: str = "sohu",
) -> Optional[float]:
    """Stage C：D1 开盘价估价（看板买价）。不占 tdx。"""
    day = day if "-" in day else f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    order = [prefer] + [x for x in ("sohu", "qq", "tx") if x != prefer]
    fn_map = {
        "sohu": buy_src.open_sohu,
        "qq": buy_src.open_qq,
        "tx": buy_src.open_tx,
    }
    for src in order:
        fn = fn_map.get(src)
        if not fn:
            continue
        try:
            px = fn(str(code).zfill(6), day)
            if px is not None and float(px) > 0:
                return float(px)
        except Exception:
            continue
    return None


def estimate_sell_prices(code: str, sell_day: str) -> dict[str, Any]:
    """
    Stage C：卖出价估价占位。

    完整多源卖价仍走 board_sell 夜班；此处仅提供轻量探测入口，
    避免新模型误以为「无卖价 API」。优先请用 UI/夜班产物。
    """
    # 轻量：用开盘作占位提示；真实 9:35/13:15 由 sell orchestrator 写入 trades
    day = sell_day if "-" in sell_day else f"{sell_day[:4]}-{sell_day[4:6]}-{sell_day[6:8]}"
    open_px = estimate_buy_open(code, day, prefer="qq")
    return {
        "code": str(code).zfill(6),
        "sell_day": day,
        "note": "精确 09:35/13:15 请用 board_sell 夜班；此处仅开盘占位",
        "open_placeholder": open_px,
        "sell_price_935": None,
        "sell_price_1315": None,
    }


def pool_meta(df: pd.DataFrame) -> dict[str, Any]:
    """诊断：区分 pool_empty / fields_sparse / rich。"""
    if df is None or getattr(df, "empty", True) or "代码" not in getattr(df, "columns", []):
        return {"status": "pool_empty", "n": 0, "sparse": True}
    n = len(df)
    sparse = _is_sparse_zt(df)
    turn_ok = 0
    if "换手率" in df.columns:
        turn_ok = int((pd.to_numeric(df["换手率"], errors="coerce").fillna(0) > 0).sum())
    return {
        "status": "fields_sparse" if sparse else "rich",
        "n": n,
        "sparse": sparse,
        "turnover_positive": turn_ok,
    }
