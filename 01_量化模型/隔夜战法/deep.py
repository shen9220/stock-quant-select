#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隔夜战法 / 量化选股（实时数据版）

对齐 SKILL.md：
- 当日收阴线
- 过去 lookback 日内有放量大阳线或涨停（涨幅≥阈值且放量≥倍数）
- 当日缩量
- 回调至 MA10 ± 阈值
- MA10 向上
- 评分排序，默认推荐 1 只

数据对齐 stock.py：
- ak.stock_zh_a_spot_em 实时列表
- ak.stock_zh_a_hist 前复权日线
- 交易日历 + 请求退避重试

禁止本地硬编码股池、禁止随机造假 K 线。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as time_mod
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import akshare as ak
import numpy as np
import pandas as pd
from requests.exceptions import RequestException

# ---------- 缓存 ----------
_TRADE_CAL_CACHE = None
_HIST_CACHE: Dict[str, Optional[pd.DataFrame]] = {}


def _retry_fetch(description, func, *args, max_attempts=5, base_delay=1.5, **kwargs):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except (RequestException, OSError, ValueError) as e:
            last_err = e
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                print(
                    f"{description} 失败（{type(e).__name__}），"
                    f"{wait:.1f}s 后重试 ({attempt}/{max_attempts})…",
                    flush=True,
                )
                time_mod.sleep(wait)
    raise last_err


def clear_caches():
    global _TRADE_CAL_CACHE
    _TRADE_CAL_CACHE = None
    _HIST_CACHE.clear()


def _to_date_str(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, np.integer)):
        text = str(int(value))
        if len(text) == 8 and text.startswith(("19", "20")):
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        return None
    if isinstance(value, str):
        text = value.strip().replace("/", "-")
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        return None
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return None


def get_trade_calendar():
    global _TRADE_CAL_CACHE
    if _TRADE_CAL_CACHE is not None:
        return _TRADE_CAL_CACHE
    cal = _retry_fetch("交易日历", ak.tool_trade_date_hist_sina, max_attempts=3, base_delay=1.0)
    cal = cal.copy()
    cal["trade_date"] = pd.to_datetime(cal["trade_date"])
    _TRADE_CAL_CACHE = cal
    return cal


def resolve_select_date(date_str: Optional[str]) -> datetime:
    """解析选股日：默认取不晚于今天的最近交易日。"""
    cal = None
    try:
        cal = get_trade_calendar()
    except Exception:
        pass

    if date_str:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if cal is not None:
        d_ts = pd.Timestamp(d).normalize()
        past = cal[cal["trade_date"] <= d_ts]
        if not past.empty:
            return past.iloc[-1]["trade_date"].to_pydatetime()
    # fallback：跳过周末
    while d.weekday() >= 5:
        d -= timedelta(1)
    return d


def fetch_spot_universe_sina(min_amount: float = 5e7) -> pd.DataFrame:
    """新浪 hs_a 分页行情回退（东财 spot 不可用时）。"""
    import requests

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
    rows = []
    for page in range(1, 60):
        def _fetch(p=page):
            url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
            r = requests.get(
                url,
                params={
                    "page": p,
                    "num": 100,
                    "sort": "amount",
                    "asc": 0,
                    "node": "hs_a",
                    "symbol": "",
                    "_s_r_a": "page",
                },
                timeout=20,
                headers=headers,
            )
            r.raise_for_status()
            text = (r.text or "").strip()
            if not text or text == "null":
                return []
            return r.json()

        try:
            batch = _retry_fetch(f"新浪行情 p{page}", _fetch, max_attempts=2, base_delay=0.8)
        except Exception:
            break
        if not batch:
            break
        rows.extend(batch)
        time_mod.sleep(0.05)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # sina: code, name, trade, changepercent, amount, volume, turnoverratio
    out = pd.DataFrame({
        "代码": df["code"].astype(str).str.zfill(6),
        "名称": df["name"].astype(str),
        "最新价": pd.to_numeric(df.get("trade"), errors="coerce"),
        "涨跌幅": pd.to_numeric(df.get("changepercent"), errors="coerce"),
        "成交额": pd.to_numeric(df.get("amount"), errors="coerce"),
        "成交量": pd.to_numeric(df.get("volume"), errors="coerce"),
        "换手率": pd.to_numeric(df.get("turnoverratio"), errors="coerce"),
    })
    out = out[~out["代码"].str.startswith(("688", "8", "92"))]
    out = out[~out["名称"].str.contains(r"ST", case=False, na=False)]
    out = out[out["最新价"].notna() & (out["最新价"] > 0)]
    out = out[out["成交额"].fillna(0) >= min_amount]
    out = out[out["涨跌幅"] < 0]
    return out.sort_values("成交额", ascending=False).reset_index(drop=True)


def fetch_spot_universe(min_amount: float = 5e7) -> pd.DataFrame:
    """
    实时全市场列表。
    优先东财 spot；失败则新浪 hs_a。
    预筛：非 ST/688/北交所；成交额达标；当日收跌；按成交额降序。
    """
    try:
        df = _retry_fetch("A股实时行情(东财)", ak.stock_zh_a_spot_em, max_attempts=3, base_delay=1.5)
        if df is not None and not df.empty:
            out = df.copy()
            out["代码"] = out["代码"].astype(str).str.zfill(6)
            out["名称"] = out["名称"].astype(str)
            for col in ("最新价", "涨跌幅", "成交额", "成交量", "换手率"):
                if col in out.columns:
                    out[col] = pd.to_numeric(out[col], errors="coerce")
            out = out[~out["代码"].str.startswith(("688", "8", "92"))]
            out = out[~out["名称"].str.contains(r"ST", case=False, na=False)]
            out = out[out["最新价"].notna() & (out["最新价"] > 0)]
            if "成交额" in out.columns:
                out = out[out["成交额"].fillna(0) >= min_amount]
            if "涨跌幅" in out.columns:
                out = out[out["涨跌幅"] < 0]
            out = out.sort_values("成交额", ascending=False).reset_index(drop=True)
            if not out.empty:
                print(f"行情源: 东财 spot，预筛 {len(out)} 只", flush=True)
                return out
    except Exception as e:
        print(f"东财行情不可用（{type(e).__name__}），切换新浪…", flush=True)

    out = fetch_spot_universe_sina(min_amount=min_amount)
    print(f"行情源: 新浪 hs_a，预筛 {len(out)} 只", flush=True)
    return out


def _normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    if "日期" not in out.columns:
        raise ValueError("日线缺少日期列")
    out["日期"] = pd.to_datetime(out["日期"], errors="coerce")
    out = out.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    rename = {
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "换手率": "turnover",
        "涨跌幅": "pct_change",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    for col in ("open", "close", "high", "low", "volume", "turnover", "pct_change"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def fetch_hist(stock_code: str, select_date: datetime, lookback_days: int = 20) -> Optional[pd.DataFrame]:
    end_str = select_date.strftime("%Y%m%d")
    start_str = (select_date - timedelta(days=lookback_days + 40)).strftime("%Y%m%d")
    cache_key = f"{stock_code}:{end_str}:{lookback_days}"
    if cache_key in _HIST_CACHE:
        return _HIST_CACHE[cache_key]

    def _fetch():
        raw = ak.stock_zh_a_hist(
            symbol=str(stock_code).zfill(6),
            period="daily",
            start_date=start_str,
            end_date=end_str,
            adjust="qfq",
        )
        if raw is None or raw.empty:
            raise ValueError("空日线")
        return _normalize_hist(raw)

    try:
        df = _retry_fetch(f"日线 {stock_code}", _fetch, max_attempts=3, base_delay=1.0)
    except Exception:
        df = None
    _HIST_CACHE[cache_key] = df
    return df


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ma5"] = out["close"].rolling(5).mean()
    out["ma10"] = out["close"].rolling(10).mean()
    out["ma20"] = out["close"].rolling(20).mean()
    out["ma_volume_5"] = out["volume"].rolling(5).mean()
    out["ma_volume_20"] = out["volume"].rolling(20).mean()
    if "pct_change" not in out.columns or out["pct_change"].isna().all():
        out["pct_change"] = out["close"].pct_change() * 100.0
    out["ma10_slope"] = out["ma10"].diff()
    out["ma10_up"] = out["ma10"] > out["ma10"].shift(1)
    return out


def find_date_index(df: pd.DataFrame, select_date: datetime) -> int:
    want = select_date.strftime("%Y-%m-%d")
    for i in range(len(df) - 1, -1, -1):
        if _to_date_str(df.iloc[i]["日期"]) == want:
            return i
    # 若选股日无 bar（停牌），用不超过该日的最后一根
    for i in range(len(df) - 1, -1, -1):
        d = _to_date_str(df.iloc[i]["日期"])
        if d and d <= want:
            return i
    return -1


def check_buy_conditions(
    df: pd.DataFrame,
    idx: int,
    ma10_diff: float = 0.02,
    big_rise_threshold: float = 0.06,
    volume_multiple: float = 1.5,
    lookback_days: int = 20,
    limit_up_threshold: float = 0.095,
) -> Tuple[bool, List[str], Dict[str, Any], float]:
    """SKILL 五条件，全部满足才 True；并返回详情与评分。"""
    conditions: List[str] = []
    detail: Dict[str, Any] = {}
    score = 0.0

    if idx < max(lookback_days, 10) or idx >= len(df):
        return False, conditions, detail, score

    today = df.iloc[idx]
    yesterday = df.iloc[idx - 1]

    # 1 收阴线
    if float(today["close"]) < float(today["open"]):
        conditions.append("收阴线")
        yin_pct = (float(today["close"]) - float(today["open"])) / float(today["open"]) * 100
        detail["当日阴线跌幅"] = f"{yin_pct:.2f}%"
        score += 10
    else:
        return False, conditions, detail, score

    # 2 历史放量大阳 / 涨停（过去 lookback 日，不含当日）
    hist = df.iloc[max(0, idx - lookback_days): idx]
    rise_need_big = big_rise_threshold
    best = None
    base_i = max(0, idx - lookback_days)
    for j in range(1, len(hist)):
        row = hist.iloc[j]
        prev = hist.iloc[j - 1]
        prev_c = float(prev["close"])
        if prev_c <= 0:
            continue
        rise = (float(row["close"]) - prev_c) / prev_c
        vol20 = float(row["ma_volume_20"]) if pd.notna(row.get("ma_volume_20")) else np.nan
        if pd.isna(vol20) or vol20 <= 0:
            continue
        if rise >= rise_need_big and float(row["volume"]) >= vol20 * volume_multiple:
            cand = {
                "rise": rise,
                "vol_mult": float(row["volume"]) / vol20,
                "days_ago": idx - (base_i + j),
                "is_limit": rise >= limit_up_threshold,
            }
            if best is None or cand["rise"] > best["rise"]:
                best = cand
    if best is None:
        return False, conditions, detail, score

    conditions.append("历史放量大阳线或涨停板")
    tag = "涨停" if best["is_limit"] else "大阳"
    detail["放量大阳线"] = (
        f"{tag}涨幅{best['rise'] * 100:.2f}%, 放量{best['vol_mult']:.2f}倍, {best['days_ago']}天前"
    )
    score += min(best["rise"] * 100, 30) + min((best["vol_mult"] - 1) * 10, 20)

    # 3 缩量
    if float(today["volume"]) < float(yesterday["volume"]):
        conditions.append("缩量")
        ratio = float(today["volume"]) / float(yesterday["volume"]) if float(yesterday["volume"]) else 0
        detail["缩量比例"] = f"{ratio:.2f}"
        score += 15
    else:
        return False, conditions, detail, score

    # 4 回调至 MA10
    ma10 = float(today["ma10"]) if pd.notna(today.get("ma10")) else np.nan
    if pd.isna(ma10) or ma10 <= 0:
        return False, conditions, detail, score
    price = float(today["close"])
    lower, upper = ma10 * (1 - ma10_diff), ma10 * (1 + ma10_diff)
    if lower <= price <= upper:
        conditions.append("回调至10日线")
        dist = (price - ma10) / ma10 * 100
        detail["距离MA10"] = f"{dist:.2f}%"
        score += 25 - abs(dist)  # 越贴 MA10 分越高
    else:
        return False, conditions, detail, score

    # 5 MA10 向上
    if bool(today.get("ma10_up")):
        conditions.append("MA10向上")
        slope = float(today["ma10_slope"]) if pd.notna(today.get("ma10_slope")) else 0.0
        slope_pct = slope / ma10 * 100 if ma10 else 0
        detail["MA10斜率"] = f"{slope_pct:.3f}%"
        score += 20 + min(max(slope_pct, 0) * 5, 10)
    else:
        return False, conditions, detail, score

    return True, conditions, detail, float(score)


def build_trading_advice(df: pd.DataFrame, idx: int) -> Dict[str, Any]:
    today = df.iloc[idx]
    buy = float(today["close"])
    ma10 = float(today["ma10"])
    ma5 = float(today["ma5"]) if pd.notna(today.get("ma5")) else buy
    return {
        "buy_price": round(buy, 2),
        "buy_time": "尾盘/14:50 附近（隔夜）",
        "stop_loss": round(ma10 * 0.99, 2),
        "take_profit_2pct": round(buy * 1.02, 2),
        "take_profit_5pct": round(buy * 1.05, 2),
        "ma10": round(ma10, 2),
        "ma5": round(ma5, 2),
        "next_day_rules": [
            "次日开盘价 < MA10 → 立即止损",
            "盘中冲高 2% → 止盈；冲高回落破分时均线 → 卖出",
            "10:00 未触发 → 强制卖出",
            "早盘涨停或突破 MA5 → 可持有至尾盘",
        ],
    }


def select_stocks(
    select_date: Optional[str] = None,
    ma10_diff: float = 0.02,
    big_rise_threshold: float = 0.06,
    volume_multiple: float = 1.5,
    lookback_days: int = 20,
    limit_up_threshold: float = 0.095,
    max_stocks: int = 400,
    min_amount: float = 5e7,
    top_n: int = 1,
) -> Dict[str, Any]:
    clear_caches()
    d = resolve_select_date(select_date)
    date_disp = d.strftime("%Y-%m-%d")

    print("=" * 60, flush=True)
    print("隔夜战法 · 实时量化选股（SKILL.md）", flush=True)
    print("=" * 60, flush=True)
    print(f"选股日期: {date_disp}", flush=True)
    print(
        f"参数: ma10_diff={ma10_diff}, big_rise={big_rise_threshold}, "
        f"vol_mult={volume_multiple}, lookback={lookback_days}, "
        f"limit_up={limit_up_threshold}",
        flush=True,
    )
    print("买入条件: 阴线 + 历史放量大阳/涨停 + 缩量 + 贴MA10 + MA10向上", flush=True)
    print("※ 实时行情+日线；禁止硬编码股池与造假K线\n", flush=True)

    try:
        universe = fetch_spot_universe(min_amount=min_amount)
    except Exception as e:
        print(f"获取实时行情失败: {e}", flush=True)
        return {
            "select_date": date_disp,
            "model": "隔夜战法-缩量回踩MA10",
            "demo_data": False,
            "score": 0,
            "recommendation": None,
            "candidates": [],
            "error": str(e),
        }

    if universe.empty:
        print("实时行情为空或预筛后无票。", flush=True)
        return {
            "select_date": date_disp,
            "model": "隔夜战法-缩量回踩MA10",
            "demo_data": False,
            "score": 0,
            "recommendation": None,
            "candidates": [],
        }

    scan_n = min(len(universe), max_stocks)
    print(
        f"预筛后活跃收跌标的 {len(universe)} 只，按成交额扫描前 {scan_n} 只…\n",
        flush=True,
    )

    qualified: List[Dict[str, Any]] = []
    for i in range(scan_n):
        row = universe.iloc[i]
        code = str(row["代码"])
        name = str(row["名称"])
        if (i + 1) % 50 == 0:
            print(f"  进度 {i + 1}/{scan_n}，已命中 {len(qualified)}…", flush=True)

        hist = fetch_hist(code, d, lookback_days=lookback_days)
        if hist is None or len(hist) < lookback_days + 5:
            continue
        hist = calc_indicators(hist)
        idx = find_date_index(hist, d)
        if idx < 0:
            continue

        ok, conds, detail, score = check_buy_conditions(
            hist,
            idx,
            ma10_diff=ma10_diff,
            big_rise_threshold=big_rise_threshold,
            volume_multiple=volume_multiple,
            lookback_days=lookback_days,
            limit_up_threshold=limit_up_threshold,
        )
        if not ok:
            continue

        today = hist.iloc[idx]
        advice = build_trading_advice(hist, idx)
        item = {
            "stock_code": code,
            "stock_name": name,
            "score": round(score, 2),
            "buy_price": float(today["close"]),
            "ma10": float(today["ma10"]),
            "ma10_slope": float(today["ma10_slope"]) if pd.notna(today.get("ma10_slope")) else 0.0,
            "volume": float(today["volume"]),
            "prev_volume": float(hist.iloc[idx - 1]["volume"]),
            "pct_change": float(today["pct_change"]) if pd.notna(today.get("pct_change")) else None,
            "amount_spot": float(row["成交额"]) if pd.notna(row.get("成交额")) else None,
            "conditions": conds,
            "conditions_detail": detail,
            "trading_advice": advice,
        }
        qualified.append(item)
        print(f"  ✓ {code} {name} | 得分 {score:.1f} | 买价 {today['close']:.2f}", flush=True)

    qualified.sort(key=lambda x: x["score"], reverse=True)
    top = qualified[: max(top_n, 1)]
    best = top[0] if top else None

    print("\n" + "=" * 60, flush=True)
    print(f"扫描完成 | 严格命中 {len(qualified)} 只 | 输出 Top{top_n}", flush=True)
    print("=" * 60, flush=True)

    if best:
        print(f"\n【主推】{best['stock_code']} {best['stock_name']}", flush=True)
        print(f"  得分: {best['score']}", flush=True)
        print(f"  买入价: {best['buy_price']:.2f}（{best['trading_advice']['buy_time']}）", flush=True)
        print(f"  MA10: {best['ma10']:.2f}", flush=True)
        print("  条件:", " / ".join(best["conditions"]), flush=True)
        for k, v in best["conditions_detail"].items():
            print(f"    {k}: {v}", flush=True)
        print("  次日规则:", flush=True)
        for r in best["trading_advice"]["next_day_rules"]:
            print(f"    - {r}", flush=True)
    else:
        print("\n当日无完全符合五条件的真实标的（score=0，不造假）。", flush=True)

    return {
        "select_date": date_disp,
        "model": "隔夜战法-缩量回踩MA10",
        "data_source": "akshare realtime (stock_zh_a_spot_em + stock_zh_a_hist)",
        "demo_data": False,
        "params": {
            "ma10_diff": ma10_diff,
            "big_rise_threshold": big_rise_threshold,
            "volume_multiple": volume_multiple,
            "lookback_days": lookback_days,
            "limit_up_threshold": limit_up_threshold,
            "max_stocks": max_stocks,
            "min_amount": min_amount,
        },
        "scanned": scan_n,
        "total_found": len(qualified),
        "score": best["score"] if best else 0,
        "recommendation": best,
        "candidates": top,
    }


def main():
    parser = argparse.ArgumentParser(description="隔夜战法实时选股（SKILL.md）")
    parser.add_argument("--date", type=str, default=None, help="选股日期 YYYY-MM-DD")
    parser.add_argument("--output", type=str, default=None, help="输出 JSON 路径")
    parser.add_argument("--ma10-diff", type=float, default=0.02, help="MA10 回调带宽，默认 ±2%")
    parser.add_argument("--big-rise-threshold", type=float, default=0.06, help="大阳线涨幅阈值")
    parser.add_argument("--volume-multiple", type=float, default=1.5, help="放量相对20日均量倍数")
    parser.add_argument("--lookback-days", type=int, default=20, help="历史回看天数")
    parser.add_argument("--limit-up-threshold", type=float, default=0.095, help="涨停阈值")
    parser.add_argument("--max-stocks", type=int, default=400, help="按成交额扫描前 N 只")
    parser.add_argument("--min-amount", type=float, default=5e7, help="最低成交额（元）")
    parser.add_argument("--top-n", type=int, default=1, help="输出前 N 只，默认 1")
    args = parser.parse_args()

    result = select_stocks(
        select_date=args.date,
        ma10_diff=args.ma10_diff,
        big_rise_threshold=args.big_rise_threshold,
        volume_multiple=args.volume_multiple,
        lookback_days=args.lookback_days,
        limit_up_threshold=args.limit_up_threshold,
        max_stocks=args.max_stocks,
        min_amount=args.min_amount,
        top_n=args.top_n,
    )

    out_path = args.output
    if not out_path:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deep_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存: {out_path}", flush=True)
    if not result.get("recommendation"):
        print("⚠️ 无真实入选（已禁止演示/造假数据）", flush=True)
    print("🎉 完成", flush=True)


if __name__ == "__main__":
    main()
