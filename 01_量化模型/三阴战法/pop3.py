#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
首次涨停后阴跌战法模型（实时数据版）

策略（与 README 一致）：
1. 首次涨停（非连板）
2. 涨停后连续 3 天阴跌，不破涨停底部（允许 1% 误差）
3. 第 3 或第 4 天收十字星（振幅 < 2%）
4. 排除 ST / 科创板 / 北交所
5. 强度评分排序，取前 N 只

数据源对齐 stock.py：
- 涨停池：ak.stock_zt_pool_em（带重试+缓存）
- 交易日历：ak.tool_trade_date_hist_sina
- 日线：ak.stock_zh_a_hist（规范化「日期」列）

禁止回退演示/内置假数据；无候选则如实返回空结果。
"""

import argparse
import json
import os
import time as time_mod
from datetime import datetime, timedelta

import akshare as ak
import numpy as np
import pandas as pd
from requests.exceptions import RequestException

# ---------- 常量 ----------
_ZT_POOL_CACHE = {}
_TRADE_CAL_CACHE = None
_HIST_CACHE = {}

_ZT_POOL_COLUMNS = (
    "序号", "代码", "名称", "涨跌幅", "最新价", "成交额",
    "流通市值", "总市值", "换手率", "封板资金", "首次封板时间",
    "最后封板时间", "炸板次数", "涨停统计", "连板数", "所属行业",
)

LIMIT_UP_PCT = 0.095          # 主板/创业板涨停近似阈值（用于日线兜底）
BOTTOM_TOLERANCE = 0.99       # 不破涨停底：最低价 >= 涨停最低 * 0.99
CROSS_STAR_AMP = 0.02         # 十字星振幅 < 2%
LOOKBACK_ZT_DAYS = 12         # 向前扫描涨停池的交易日数（需覆盖「涨停+3~4阴跌」）


def _retry_fetch(description, func, *args, max_attempts=5, base_delay=1.5, **kwargs):
    """东方财富等接口易 TLS 中断，退避重试。"""
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except (RequestException, OSError, ValueError) as e:
            last_err = e
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                print(
                    f"{description} 请求失败（{type(e).__name__}），"
                    f"{wait:.1f} 秒后重试 ({attempt}/{max_attempts})…",
                    flush=True,
                )
                time_mod.sleep(wait)
    raise last_err


def clear_caches():
    _ZT_POOL_CACHE.clear()
    _HIST_CACHE.clear()
    global _TRADE_CAL_CACHE
    _TRADE_CAL_CACHE = None


def _to_date_str(value):
    """任意日期值 → YYYY-MM-DD。"""
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


def _normalize_zt_pool(df):
    if df is None or (getattr(df, "empty", True) and "代码" not in getattr(df, "columns", [])):
        return pd.DataFrame(columns=list(_ZT_POOL_COLUMNS))
    out = df.copy()
    out["代码"] = out["代码"].astype(str).str.zfill(6)
    return out


def _zt_pool_codes(df):
    if df is None or df.empty or "代码" not in df.columns:
        return set()
    return set(df["代码"].astype(str))


def fetch_zt_pool_em(date_str, max_attempts=5):
    """涨停池：同日只请求一次并缓存（对齐 stock.py）。"""
    if date_str in _ZT_POOL_CACHE:
        return _ZT_POOL_CACHE[date_str]
    df = _retry_fetch(
        f"涨停池 {date_str}",
        ak.stock_zt_pool_em,
        date=date_str,
        max_attempts=max_attempts,
        base_delay=1.5,
    )
    df = _normalize_zt_pool(df)
    _ZT_POOL_CACHE[date_str] = df
    return df


def get_trade_calendar():
    global _TRADE_CAL_CACHE
    if _TRADE_CAL_CACHE is not None:
        return _TRADE_CAL_CACHE
    trade_cal = _retry_fetch(
        "交易日历",
        ak.tool_trade_date_hist_sina,
        max_attempts=3,
        base_delay=1.0,
    )
    trade_cal = trade_cal.copy()
    trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"])
    _TRADE_CAL_CACHE = trade_cal
    return trade_cal


def get_previous_trading_day(current_date):
    """真实交易日历取前一交易日；失败则跳过周末。"""
    try:
        trade_cal = get_trade_calendar()
        current_date_only = pd.Timestamp(current_date).normalize()
        past = trade_cal[trade_cal["trade_date"] < current_date_only]
        if len(past) > 0:
            return past.iloc[-1]["trade_date"].to_pydatetime()
    except Exception:
        pass
    day = pd.Timestamp(current_date).to_pydatetime() - timedelta(1)
    while day.weekday() >= 5:
        day -= timedelta(1)
    return day


def list_trading_days_before(end_date, n):
    """取 end_date 之前（不含当日）最近 n 个交易日，从旧到新。"""
    try:
        trade_cal = get_trade_calendar()
        end_only = pd.Timestamp(end_date).normalize()
        past = trade_cal[trade_cal["trade_date"] < end_only].tail(n)
        return [d.to_pydatetime() for d in past["trade_date"].tolist()]
    except Exception:
        days = []
        cur = pd.Timestamp(end_date).to_pydatetime()
        while len(days) < n:
            cur = get_previous_trading_day(cur)
            days.append(cur)
        return list(reversed(days))


def _normalize_hist_df(df):
    """保证存在可解析的「日期」列（禁止对 RangeIndex 调 strftime）。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "日期" not in out.columns:
        if not isinstance(out.index, pd.RangeIndex):
            out = out.reset_index()
            for col in list(out.columns):
                if str(col) in ("日期", "date", "Date", "index") or "日期" in str(col):
                    if str(col) != "日期":
                        out = out.rename(columns={col: "日期"})
                    break
    if "日期" not in out.columns:
        raise ValueError("历史行情缺少「日期」列")
    out["日期"] = pd.to_datetime(out["日期"], errors="coerce")
    out = out.dropna(subset=["日期"]).sort_values("日期").reset_index(drop=True)
    for col in ("开盘", "收盘", "最高", "最低", "成交量"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "换手率" in out.columns:
        out["换手率"] = pd.to_numeric(out["换手率"], errors="coerce")
    if "涨跌幅" in out.columns:
        out["涨跌幅"] = pd.to_numeric(out["涨跌幅"], errors="coerce")
    return out


def _row_date_str(df, i):
    return _to_date_str(df.iloc[i]["日期"])


def get_stock_history(stock_code, end_date, days=60, max_attempts=4):
    """获取截止 end_date 的前复权日线，带缓存。"""
    end_str = pd.Timestamp(end_date).strftime("%Y%m%d")
    cache_key = f"{stock_code}:{end_str}:{days}"
    if cache_key in _HIST_CACHE:
        return _HIST_CACHE[cache_key]

    start_str = (pd.Timestamp(end_date) - timedelta(days=days + 20)).strftime("%Y%m%d")

    def _fetch():
        df = ak.stock_zh_a_hist(
            symbol=str(stock_code).zfill(6),
            period="daily",
            start_date=start_str,
            end_date=end_str,
            adjust="qfq",
        )
        if df is None or df.empty:
            raise ValueError("空日线")
        return _normalize_hist_df(df)

    try:
        df = _retry_fetch(f"日线 {stock_code}", _fetch, max_attempts=max_attempts, base_delay=1.0)
    except Exception as e:
        print(f"    日线获取失败 {stock_code}: {type(e).__name__}", flush=True)
        df = None
    _HIST_CACHE[cache_key] = df
    return df


def filter_board(zt_df):
    """排除 ST / 科创板 688 / 北交所 8、92。"""
    if zt_df is None or zt_df.empty:
        return zt_df
    out = zt_df.copy()
    out["代码"] = out["代码"].astype(str)
    out["名称"] = out["名称"].astype(str)
    out = out[~out["代码"].str.startswith(("688", "8", "92"))]
    out = out[~out["名称"].str.contains(r"ST", case=False, na=False)]
    return out


def is_first_limit_up(code, limit_date, prev_zt_codes=None, df=None, limit_up_index=None):
    """
    首次涨停：
    1) 优先：再前一交易日不在涨停池
    2) 若有连板数字段且 >1，否
    3) 兜底：日线前日涨幅未达涨停
    """
    code = str(code)
    if prev_zt_codes is not None and code in prev_zt_codes:
        return False
    if df is not None and limit_up_index is not None and limit_up_index > 0:
        prev_close = float(df.iloc[limit_up_index - 1]["收盘"])
        cur_close = float(df.iloc[limit_up_index]["收盘"])
        if prev_close > 0 and (cur_close - prev_close) / prev_close >= LIMIT_UP_PCT:
            # 若前日也是涨停形态，则非首板（在无法核对涨停池时）
            # 注意：仅作兜底；有 prev_zt_codes 时以上面为准
            if prev_zt_codes is None:
                return False
    return True


def _is_yin_bar(row):
    return float(row["收盘"]) < float(row["开盘"])


def _is_cross_star(row):
    low = float(row["最低"])
    if low <= 0:
        return False
    amp = (float(row["最高"]) - low) / low
    return amp < CROSS_STAR_AMP


def check_yin_die_pattern(df, limit_up_index):
    """
    涨停后：
    - 第 1~3 个交易日必须均为阴线
    - 这 3 日（及若存在的第 4 日）不破涨停最低价 * 0.99
    - 第 3 日或第 4 日为十字星（振幅 < 2%）
    """
    if limit_up_index < 0:
        return False, {"reason": "无效涨停索引"}
    if limit_up_index + 3 >= len(df):
        return False, {"reason": "涨停后不足3个交易日"}

    limit_up_low = float(df.iloc[limit_up_index]["最低"])
    limit_up_close = float(df.iloc[limit_up_index]["收盘"])

    # 连续 3 日阴跌 + 不破底
    for offset in (1, 2, 3):
        row = df.iloc[limit_up_index + offset]
        if float(row["最低"]) < limit_up_low * BOTTOM_TOLERANCE:
            return False, {"reason": f"第{offset}日破涨停底部"}
        if not _is_yin_bar(row):
            return False, {"reason": f"第{offset}日非阴线"}

    cross_star_day = -1
    # 第 3 日十字星
    if _is_cross_star(df.iloc[limit_up_index + 3]):
        cross_star_day = 3

    # 第 4 日：若有，检查不破底；可为十字星
    if limit_up_index + 4 < len(df):
        row4 = df.iloc[limit_up_index + 4]
        if float(row4["最低"]) < limit_up_low * BOTTOM_TOLERANCE:
            return False, {"reason": "第4日破涨停底部"}
        if cross_star_day < 0 and _is_cross_star(row4):
            cross_star_day = 4

    if cross_star_day not in (3, 4):
        return False, {"reason": "第3或4日未收十字星"}

    last_i = limit_up_index + max(cross_star_day, 3)
    return True, {
        "limit_up_date": pd.Timestamp(df.iloc[limit_up_index]["日期"]),
        "limit_up_close": limit_up_close,
        "limit_up_low": limit_up_low,
        "yin_days": 3,
        "cross_star_day": cross_star_day,
        "current_close": float(df.iloc[last_i]["收盘"]),
        "pattern_end_index": last_i,
        "days_after_limit_up": last_i - limit_up_index,
    }


def calculate_strength_score(df, pattern_info):
    """README 100 分制强度评分。"""
    score = 0
    details = {}
    if df is None or len(df) < 5:
        return 0, details

    end_i = int(pattern_info.get("pattern_end_index", len(df) - 1))
    view = df.iloc[: end_i + 1]

    # 1. 趋势 30
    closes = view["收盘"]
    ma5 = closes.tail(5).mean() if len(view) >= 5 else closes.mean()
    ma10 = closes.tail(10).mean() if len(view) >= 10 else closes.mean()
    ma20 = closes.tail(20).mean() if len(view) >= 20 else closes.mean()
    if ma5 > ma10 > ma20:
        trend_score, details["趋势"] = 30, "多头排列"
    elif ma5 > ma10:
        trend_score, details["趋势"] = 20, "短期向上"
    else:
        trend_score, details["趋势"] = 10, "趋势一般"
    score += trend_score

    # 2. 成交量 20
    if "成交量" in view.columns and len(view) >= 10:
        recent_vol = view["成交量"].tail(3).mean()
        earlier_vol = view["成交量"].tail(10).head(7).mean()
        if earlier_vol > 0 and recent_vol > earlier_vol * 1.2:
            vol_score, details["成交量"] = 20, "放量"
        elif earlier_vol > 0 and recent_vol > earlier_vol:
            vol_score, details["成交量"] = 15, "温和放量"
        else:
            vol_score, details["成交量"] = 10, "缩量"
    else:
        vol_score, details["成交量"] = 10, "数据不足"
    score += vol_score

    # 3. 回调幅度 20（相对涨停收盘）
    limit_up_close = float(pattern_info["limit_up_close"])
    current_close = float(pattern_info["current_close"])
    callback_pct = (limit_up_close - current_close) / limit_up_close if limit_up_close else 0
    if 0.02 <= callback_pct <= 0.05:
        callback_score = 20
        details["回调幅度"] = f"理想回调{callback_pct * 100:.2f}%"
    elif callback_pct < 0.02:
        callback_score = 15
        details["回调幅度"] = f"回调不足{callback_pct * 100:.2f}%"
    elif callback_pct <= 0.10:
        callback_score = 10
        details["回调幅度"] = f"回调较多{callback_pct * 100:.2f}%"
    else:
        callback_score = 5
        details["回调幅度"] = f"回调过深{callback_pct * 100:.2f}%"
    score += callback_score

    # 4. 近期涨幅 20（近 5 日累计涨跌幅，百分比）
    if len(view) >= 6:
        c0 = float(view["收盘"].iloc[-6])
        c1 = float(view["收盘"].iloc[-1])
        recent_pct = (c1 - c0) / c0 * 100 if c0 else 0
    else:
        recent_pct = 0
    if recent_pct > 10:
        recent_score, details["近期涨幅"] = 20, f"强势{recent_pct:.2f}%"
    elif recent_pct > 0:
        recent_score, details["近期涨幅"] = 15, f"上涨{recent_pct:.2f}%"
    elif recent_pct > -5:
        recent_score, details["近期涨幅"] = 10, f"震荡{recent_pct:.2f}%"
    else:
        recent_score, details["近期涨幅"] = 5, f"下跌{recent_pct:.2f}%"
    score += recent_score

    # 5. 换手率 10
    if "换手率" in view.columns and view["换手率"].notna().any():
        recent_turnover = float(view["换手率"].tail(3).mean())
    else:
        recent_turnover = 0.0
    if 5 <= recent_turnover <= 15:
        turnover_score, details["换手率"] = 10, f"活跃{recent_turnover:.2f}%"
    elif recent_turnover > 15:
        turnover_score, details["换手率"] = 8, f"过高{recent_turnover:.2f}%"
    elif recent_turnover >= 3:
        turnover_score, details["换手率"] = 7, f"适中{recent_turnover:.2f}%"
    else:
        turnover_score, details["换手率"] = 5, f"低迷{recent_turnover:.2f}%"
    score += turnover_score

    return score, details


def analyze_yin_die_stocks(target_date=None, top_n=5):
    """
    实时扫描：涨停池 + 日线验证阴跌模式，返回真实候选（无演示数据）。
    """
    clear_caches()
    if target_date is None:
        target_date = datetime.now()
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d")

    print("=" * 60, flush=True)
    print("首次涨停后阴跌战法模型（实时数据）", flush=True)
    print("=" * 60, flush=True)
    print(f"分析日期: {target_date.strftime('%Y-%m-%d')}", flush=True)
    print("筛选条件:", flush=True)
    print("  1. 首次涨停（非连板）", flush=True)
    print("  2. 涨停后连续3天阴跌不破涨停底部", flush=True)
    print("  3. 第3或4天收十字星", flush=True)
    print("  4. 排除ST/科创板/北交所", flush=True)
    print(f"  5. 强度排名前{top_n}只", flush=True)
    print("  ※ 无候选时不使用演示/内置假数据\n", flush=True)

    # 涨停日需发生在「分析日」之前至少 3 个交易日，才可能走完阴跌
    trading_days = list_trading_days_before(target_date, LOOKBACK_ZT_DAYS)
    if len(trading_days) < 4:
        print("交易日不足，无法分析。", flush=True)
        return []

    # 候选涨停日：倒数第 4 个交易日及更早（保证之后至少还有 3 日）
    # trading_days 旧→新；最后 3 个交易日之后才到 target，故涨停日最多取到倒数第 4
    candidate_limit_days = trading_days[:-3] if len(trading_days) > 3 else trading_days[:1]
    print(
        f"扫描涨停日区间: {_to_date_str(candidate_limit_days[0])} ~ {_to_date_str(candidate_limit_days[-1])}"
        f"（共 {len(candidate_limit_days)} 个交易日）\n",
        flush=True,
    )

    qualified = []
    seen_codes = set()

    for i, limit_day in enumerate(candidate_limit_days):
        date_str = limit_day.strftime("%Y%m%d")
        date_disp = limit_day.strftime("%Y-%m-%d")
        print(f"正在获取 {date_disp} 的涨停池...", flush=True)
        try:
            zt_df = fetch_zt_pool_em(date_str)
        except Exception as e:
            print(f"  涨停池失败: {e}", flush=True)
            continue

        if zt_df.empty:
            print("  该日无涨停数据", flush=True)
            continue

        zt_df = filter_board(zt_df)
        # 优先用连板数=1
        if "连板数" in zt_df.columns:
            lianban = pd.to_numeric(zt_df["连板数"], errors="coerce")
            zt_first = zt_df[lianban.fillna(1) <= 1]
            if not zt_first.empty:
                zt_df = zt_first

        # 再前一交易日涨停池，排除连板
        prev_day = get_previous_trading_day(limit_day)
        try:
            prev_zt = fetch_zt_pool_em(prev_day.strftime("%Y%m%d"))
            prev_codes = _zt_pool_codes(prev_zt)
        except Exception:
            prev_codes = set()

        zt_df = zt_df[~zt_df["代码"].astype(str).isin(prev_codes)]
        print(f"  首板候选 {len(zt_df)} 只（已排除ST/688/北交所/连板）", flush=True)

        for _, row in zt_df.iterrows():
            code = str(row["代码"])
            name = str(row["名称"])
            if code in seen_codes:
                continue

            print(f"  分析 {code} {name}...", flush=True)
            df = get_stock_history(code, end_date=target_date, days=60)
            if df is None or len(df) < 10:
                continue

            # 涨停日在日线中的位置
            limit_up_index = -1
            want = _to_date_str(limit_day)
            for j in range(len(df) - 1, -1, -1):
                if _row_date_str(df, j) == want:
                    limit_up_index = j
                    break
            if limit_up_index < 0:
                continue

            if not is_first_limit_up(code, limit_day, prev_zt_codes=prev_codes, df=df, limit_up_index=limit_up_index):
                continue

            ok, pattern_info = check_yin_die_pattern(df, limit_up_index)
            if not ok:
                continue

            # 模式结束日不得晚于分析日（日线已截到 target_date）
            score, strength_details = calculate_strength_score(df, pattern_info)
            end_i = int(pattern_info["pattern_end_index"])
            cur = df.iloc[end_i]
            change_pct = float(cur["涨跌幅"]) if "涨跌幅" in df.columns and pd.notna(cur.get("涨跌幅")) else 0.0
            turnover = float(cur["换手率"]) if "换手率" in df.columns and pd.notna(cur.get("换手率")) else 0.0

            qualified.append({
                "stock_code": code,
                "stock_name": name,
                "score": score,
                "limit_up_date": pattern_info["limit_up_date"],
                "limit_up_price": pattern_info["limit_up_close"],
                "current_price": float(cur["收盘"]),
                "change_pct": change_pct,
                "volume": float(cur["成交量"]) if "成交量" in df.columns else 0.0,
                "turnover": turnover,
                "strength_details": strength_details,
                "pattern_info": pattern_info,
                "industry": str(row.get("所属行业", "") or ""),
            })
            seen_codes.add(code)
            print(
                f"    ✓ 符合条件 | 得分 {score} | 十字星第{pattern_info['cross_star_day']}日",
                flush=True,
            )

    print(f"\n扫描完成！真实符合条件: {len(qualified)} 只\n", flush=True)
    qualified.sort(key=lambda x: x["score"], reverse=True)
    top_stocks = qualified[:top_n]

    print("=" * 60, flush=True)
    print(f"强度排名前{len(top_stocks)}只（实时筛选）", flush=True)
    print("=" * 60, flush=True)

    if not top_stocks:
        print("当日无符合「首板+三阴+十字星」的真实标的，不输出演示数据。", flush=True)
        return []

    for idx, stock in enumerate(top_stocks, start=1):
        print(f"\n【排名 {idx}】{stock['stock_code']} {stock['stock_name']}", flush=True)
        print(f"  强度得分: {stock['score']}", flush=True)
        print(f"  涨停日期: {_to_date_str(stock['limit_up_date'])}", flush=True)
        print(f"  涨停价: {stock['limit_up_price']:.2f}", flush=True)
        print(f"  当前价: {stock['current_price']:.2f}", flush=True)
        print(f"  涨跌幅: {stock['change_pct']:.2f}%", flush=True)
        print(f"  换手率: {stock['turnover']:.2f}%", flush=True)
        print(f"  所属行业: {stock.get('industry') or '-'}", flush=True)
        print("  强度分析:", flush=True)
        for k, v in stock["strength_details"].items():
            print(f"    {k}: {v}", flush=True)

    print("\n" + "=" * 60, flush=True)
    print("交易建议", flush=True)
    print("=" * 60, flush=True)
    first = top_stocks[0]
    second = top_stocks[1] if len(top_stocks) > 1 else None
    print(f"\n【今日操作（{target_date.strftime('%Y-%m-%d')}）】", flush=True)
    print(f"  第一名: {first['stock_code']} {first['stock_name']}", flush=True)
    print("  买入策略: 集合竞价附近买入半仓", flush=True)
    print(f"  参考价: {first['current_price']:.2f}", flush=True)
    print(f"  止损价: {first['pattern_info']['limit_up_low']:.2f} (涨停底部)", flush=True)
    print("  注意: 若开盘涨幅>5%，改买第二名", flush=True)
    if second:
        print(f"\n  备选第二名: {second['stock_code']} {second['stock_name']}", flush=True)
        print(f"  当前价: {second['current_price']:.2f} | 得分: {second['score']}", flush=True)
    print("\n【明日操作】", flush=True)
    print("  1. 继续用模型选新的强度票", flush=True)
    print("  2. 剩余半仓开盘附近买入", flush=True)
    print("  3. 持有 1 天，收盘前全部清仓", flush=True)

    return top_stocks


def main():
    parser = argparse.ArgumentParser(description="首次涨停后阴跌战法模型（实时数据）")
    parser.add_argument("--date", type=str, default=None, help="分析日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--top-n", type=int, default=5, help="返回前 N 只，默认 5")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出 JSON 路径，默认脚本目录下 yin_die_result.json",
    )
    args = parser.parse_args()

    out_path = args.output
    if not out_path:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yin_die_result.json")

    result_stocks = analyze_yin_die_stocks(target_date=args.date, top_n=args.top_n)

    result = {
        "analysis_date": args.date or datetime.now().strftime("%Y-%m-%d"),
        "model": "首次涨停后阴跌战法",
        "data_source": "akshare realtime (stock_zt_pool_em + stock_zh_a_hist)",
        "demo_data": False,
        "total_found": len(result_stocks),
        "top_stocks": [],
    }
    for stock in result_stocks:
        result["top_stocks"].append({
            "rank": len(result["top_stocks"]) + 1,
            "stock_code": stock["stock_code"],
            "stock_name": stock["stock_name"],
            "score": stock["score"],
            "limit_up_date": _to_date_str(stock["limit_up_date"]),
            "limit_up_price": float(stock["limit_up_price"]),
            "current_price": float(stock["current_price"]),
            "change_pct": float(stock["change_pct"]),
            "turnover": float(stock["turnover"]),
            "industry": stock.get("industry", ""),
            "strength_details": stock["strength_details"],
            "pattern": {
                "yin_days": stock["pattern_info"].get("yin_days"),
                "cross_star_day": stock["pattern_info"].get("cross_star_day"),
                "limit_up_low": float(stock["pattern_info"]["limit_up_low"]),
            },
            "trading_advice": {
                "buy_price": float(stock["current_price"]),
                "stop_loss": float(stock["pattern_info"]["limit_up_low"]),
                "buy_time": "集合竞价附近",
            },
        })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 结果已保存到: {out_path}", flush=True)
    if not result_stocks:
        print("⚠️ 本次无真实入选标的（已禁止演示数据）。", flush=True)
    print("🎉 分析完成！", flush=True)


if __name__ == "__main__":
    main()
