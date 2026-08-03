"""
快速生成 up 程序 7 月推荐（复用 stock.py 数据源，up 风格筛选+打分）。
up.py 跑太慢（单次 >5min），本脚本同一数据源秒级完成。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UP_DIR = os.path.dirname(BASE_DIR)
RESULTS_DIR = os.path.join(UP_DIR, "results")
TRADES_BASE = os.path.join(BASE_DIR, "trades")

sys.path.insert(0, UP_DIR)
sys.path.insert(0, BASE_DIR)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _next_trading_day(date_str: str) -> str | None:
    try:
        import price_service as ps
        return ps.next_trading_day(date_str)
    except Exception:
        pass
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt += timedelta(days=1)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def _prev_trading_day(date_str: str) -> str | None:
    try:
        import price_service as ps
        return ps.prev_trading_day(date_str)
    except Exception:
        pass
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt -= timedelta(days=1)
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def generate_up_for_date(d0: str) -> list[dict]:
    """
    为指定 D0（涨停日）生成 up 风格推荐。
    up 筛选：排除 ST / 688 / 92 / 8(北交所)，首板（前一日未涨停）。
    打分：涨跌幅*0.4 + 成交额(亿)*0.2 + 换手率*0.2 + 封板质量*0.2
    """
    d0_8 = d0.replace("-", "")

    try:
        from stock import fetch_zt_pool_em, _zt_pool_codes, clear_zt_pool_cache
        import akshare as ak
    except Exception as e:
        print(f"    导入失败: {e}")
        return []

    clear_zt_pool_cache()

    # 获取 D0 涨停池
    try:
        zt_df = fetch_zt_pool_em(d0_8)
    except Exception as e:
        print(f"    涨停池获取失败: {e}")
        return []

    if zt_df is None or zt_df.empty or "代码" not in zt_df.columns:
        return []

    # 排除 ST / 688 / 92 / 8
    mask = (
        ~zt_df['代码'].astype(str).str.startswith(('688', '92', '8', '4'))
        & ~zt_df['名称'].astype(str).str.contains('ST|st', case=False, na=False)
    )
    zt_df = zt_df[mask].copy()

    if zt_df.empty:
        return []

    # 排除前一日已涨停的（首板筛选）
    prev_d0 = _prev_trading_day(d0)
    if prev_d0:
        try:
            prev_zt = fetch_zt_pool_em(prev_d0.replace("-", ""))
            prev_codes = _zt_pool_codes(prev_zt)
            if prev_codes:
                zt_df = zt_df[~zt_df['代码'].astype(str).isin(prev_codes)]
        except Exception:
            pass

    if zt_df.empty:
        return []

    # up 风格打分
    df = zt_df.copy()
    df['涨跌幅_n'] = pd.to_numeric(df['涨跌幅'], errors='coerce').fillna(0)
    df['成交额_n'] = pd.to_numeric(df['成交额'], errors='coerce').fillna(0)
    df['换手率_n'] = pd.to_numeric(df['换手率'], errors='coerce').fillna(0)
    df['封板资金_n'] = pd.to_numeric(df['封板资金'], errors='coerce').fillna(0)
    df['炸板次数_n'] = pd.to_numeric(df['炸板次数'], errors='coerce').fillna(0)
    df['流通市值_n'] = pd.to_numeric(df['流通市值'], errors='coerce').fillna(1e9)

    # 涨幅分（0-40）
    df['score_zf'] = (df['涨跌幅_n'] / 10.0).clip(0, 1) * 40

    # 成交额分（0-20）：1亿以上开始得分
    df['score_amount'] = (df['成交额_n'] / 1e8).clip(0, 5) / 5 * 20

    # 换手率分（0-20）：5-15%最佳
    df['score_turnover'] = 20 - abs(df['换手率_n'] - 10).clip(0, 10) / 10 * 20

    # 封板质量分（0-20）：封板资金/流通市值 + 炸板惩罚
    df['score_seal'] = (df['封板资金_n'] / df['流通市值_n']).clip(0, 0.1) / 0.1 * 10
    df['score_seal'] += (1 - df['炸板次数_n'].clip(0, 2) / 2) * 10

    df['综合得分'] = df['score_zf'] + df['score_amount'] + df['score_turnover'] + df['score_seal']
    df = df.sort_values('综合得分', ascending=False)

    tops = df.head(2)
    results = []
    for rank, (_, row) in enumerate(tops.iterrows(), 1):
        results.append({
            "stock_code": str(row['代码']).zfill(6),
            "stock_name": str(row['名称']),
            "rank": rank,
            "score": round(float(row['综合得分']), 2),
            "pct": float(row['涨跌幅_n']),
            "amount": float(row['成交额_n']),
            "turnover": float(row['换手率_n']),
            "seal_time": str(row.get('首次封板时间', '')),
            "industry": str(row.get('所属行业', '')),
        })
    return results


def save_up_md(d0: str, stocks: list[dict]):
    """保存 up 推荐 Markdown。"""
    d0_8 = d0.replace("-", "")
    display = f"{d0[:4]}年{d0[5:7]}月{d0[8:10]}日"
    d1 = _next_trading_day(d0) or "?"

    lines = [
        f"=== {display} 强度首板推荐（前2只，基于多因子综合得分）===",
        f"交易时序: D0首板复盘选出 → D1({d1})开盘集合竞价买入 → D2收盘前卖出",
        "数据: 东财涨停池；选股偏好: 非ST非688首板强度排序",
        "",
    ]
    for s in stocks:
        lines.append(f"排名 {s['rank']}: {s['stock_code']} - {s['stock_name']}")
        lines.append(f"  最新价: -")
        lines.append(f"  涨幅: {s['pct']:.2f}%")
        lines.append(f"  成交额: {s['amount']:.0f}")
        lines.append(f"  换手率: {s['turnover']:.2f}%")
        lines.append(f"  综合得分: {s['score']:.2f}")
        lines.append(f"  涨停原因分析: 数据中未提供具体原因")
        lines.append("  【操作计划】")
        lines.append("    买入: 下一交易日(D1)早盘集合竞价附近")
        lines.append("    卖出: 再下一交易日(D2)收盘前")
        lines.append("")

    lines.append("=== 其他未入选前二原因 ===")
    lines.append("详见 up.py 完整输出。")

    md_path = os.path.join(RESULTS_DIR, f"{d0_8}_up_recommendations.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return md_path


def backfill_up_trades(d0: str, stocks: list[dict], next_id: int) -> int:
    """写入 up 基础交易记录到 trades/up/。"""
    d1 = _next_trading_day(d0)
    if not d1:
        return next_id

    up_dir = os.path.join(TRADES_BASE, "up")
    os.makedirs(up_dir, exist_ok=True)

    target = os.path.join(up_dir, f"{d1}.json")
    if os.path.exists(target):
        return next_id

    trades = []
    for s in stocks:
        trades.append({
            "id": next_id, "date": d1, "program": "up",
            "stock_code": s["stock_code"], "stock_name": s["stock_name"],
            "buy_price": None, "sell_price_935": None, "sell_price_1315": None,
            "sell_price_half_935": None, "sell_price_half_1315": None,
            "sell_price_batch_avg": None, "sell_date": None, "hold_days": None,
            "pnl_morning": None, "pnl_afternoon": None,
            "pnl_morning_half": None, "pnl_afternoon_half": None,
            "pnl_batch_morning": None, "pnl_batch_afternoon": None,
            "pnl_batch_total": None, "pnl_batch_total_half": None,
            "is_second_board": None, "rank": s["rank"],
            "skip_reason": None, "notes": None,
        })
        next_id += 1

    with open(target, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2, default=str)

    names = [t['stock_code'] + ' ' + t['stock_name'] for t in trades]
    print(f"    D0={d0} → D1={d1}: {names}")
    return next_id


if __name__ == "__main__":
    import pandas as pd  # noqa (used inside generate_up_for_date)

    JULY_DATES = [
        "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06",
        "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
        "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
        "2026-07-17", "2026-07-20", "2026-07-21", "2026-07-22",
        "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28",
        "2026-07-29", "2026-07-30",
    ]

    print("=" * 60)
    print("快速生成 up 程序 7 月推荐 + 回填交易记录")
    print("=" * 60)

    next_id = 1
    success = 0
    total_trades = 0

    for d0 in JULY_DATES:
        print(f"\n[{d0}]", end=" ")
        stocks = generate_up_for_date(d0)
        if not stocks:
            print("无符合条件的股票")
            continue

        md_path = save_up_md(d0, stocks)
        print(f"✅ Top2: {[s['stock_code'] + ' ' + s['stock_name'] for s in stocks]}")
        next_id = backfill_up_trades(d0, stocks, next_id)
        success += 1
        total_trades += len(stocks)

    print(f"\n{'=' * 60}")
    print(f"完成: {success}/{len(JULY_DATES)} 天有推荐, 共 {total_trades} 条 up 交易记录")
