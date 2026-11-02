#!/usr/bin/env python3
"""
涨停回头低吸 · 亏损归因诊断（9/1–10/29 可配）

产出：
  - data/diagnostics/loss_attribution_{start}_{end}.json
  - 文档/亏损归因诊断-{start}_{end}.md

口径：
  - A 股 T+1：MFE/MAE/可卖区间从买入下一交易日起算
  - 成本：佣金双边 + 印花税卖出 + 滑点双边（可调）
  - 等权单笔：每笔按净收益率计入权益曲线
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import market as mkt  # noqa: E402
import store  # noqa: E402

# ── 交易成本（A 股常见近似，可改）──────────────────────────
COMMISSION_RATE = 0.00025   # 佣金万 2.5，买卖各一次（忽略 5 元下限，按比例诊断）
STAMP_TAX_RATE = 0.0005     # 印花税 万 5，仅卖出
SLIPPAGE_RATE = 0.001       # 滑点 单边 0.1%


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_buy_adj(pos: dict) -> Optional[int]:
    notes = str(pos.get("notes") or "")
    m = re.search(r"adj\s*=\s*(\d+)", notes, re.I)
    if m:
        return int(m.group(1))
    # 回退：用涨停日相对买入日
    lup = pos.get("limit_up_date")
    buy = pos.get("buy_date")
    if lup and buy:
        return mkt.adj_day_of(lup, buy)
    return None


def parse_slot(pos: dict) -> str:
    notes = str(pos.get("notes") or "")
    m = re.search(r"slot\s*=\s*(\w+)", notes, re.I)
    return m.group(1) if m else "unknown"


def net_return_pct(buy: float, sell: float) -> float:
    """含佣金+印花税+滑点的净收益率%。"""
    buy_eff = buy * (1 + SLIPPAGE_RATE) * (1 + COMMISSION_RATE)
    sell_eff = sell * (1 - SLIPPAGE_RATE) * (1 - COMMISSION_RATE - STAMP_TAX_RATE)
    return (sell_eff - buy_eff) / buy_eff * 100.0


def gross_return_pct(buy: float, sell: float) -> float:
    return (sell - buy) / buy * 100.0


def trading_days_between(start: str, end: str) -> list[str]:
    """含 start、含 end 的交易日列表（若 start 非交易日则从下一交易日）。"""
    out = []
    cur = start
    # 若 start 当天无 K，从 next 起
    if mkt.get_ohlc("000001", start) is None and mkt.prev_trading_day(mkt.next_trading_day(start) or start):
        # 对个股再判断；这里只生成日历
        pass
    # 用 next 链：从 start 的「当日或下一交易日」走到 end
    probe = start
    # 确保 probe 是交易日：若 next(prev(probe))==probe
    for _ in range(5):
        prev = mkt.prev_trading_day(probe)
        if prev and mkt.next_trading_day(prev) == probe:
            break
        nxt = mkt.next_trading_day(probe)
        if not nxt:
            break
        probe = nxt
        if probe > end:
            return []
    cur = probe
    while cur and cur <= end:
        out.append(cur)
        cur = mkt.next_trading_day(cur)
    return out


def bars_for(code: str, start: str, end: str) -> list[dict]:
    df = mkt.get_daily_df(code, start, end)
    rows = []
    if df is not None and not df.empty:
        # 兼容列名
        cols = {c.lower(): c for c in df.columns}
        def col(*names):
            for n in names:
                if n in df.columns:
                    return n
                if n.lower() in cols:
                    return cols[n.lower()]
            return None
        dcol = col("date", "日期")
        ocol = col("open", "开盘")
        hcol = col("high", "最高")
        lcol = col("low", "最低")
        ccol = col("close", "收盘")
        for _, r in df.iterrows():
            ds = str(r[dcol])[:10] if dcol else None
            if not ds:
                continue
            if ds < start or ds > end:
                continue
            rows.append({
                "date": ds,
                "open": _f(r[ocol]) if ocol else None,
                "high": _f(r[hcol]) if hcol else None,
                "low": _f(r[lcol]) if lcol else None,
                "close": _f(r[ccol]) if ccol else None,
            })
        rows.sort(key=lambda x: x["date"])
        return rows
    # 逐日兜底
    days = trading_days_between(start, end)
    for d in days:
        o = mkt.get_ohlc(code, d)
        if o:
            rows.append({
                "date": d,
                "open": _f(o.get("open")),
                "high": _f(o.get("high")),
                "low": _f(o.get("low")),
                "close": _f(o.get("close")),
            })
    return rows


def index_ret(start: str, end: str) -> Optional[float]:
    """上证指数区间涨跌%（收盘对收盘）。"""
    try:
        a = mkt.get_ohlc("000001", start)
        b = mkt.get_ohlc("000001", end)
        if a and b and a.get("close") and b.get("close"):
            return (float(b["close"]) - float(a["close"])) / float(a["close"]) * 100.0
    except Exception:
        pass
    # 尝试用日线表
    rows = bars_for("000001", start, end)
    if len(rows) >= 2 and rows[0].get("close") and rows[-1].get("close"):
        return (rows[-1]["close"] - rows[0]["close"]) / rows[0]["close"] * 100.0
    return None


def regime_label(idx_pct: Optional[float]) -> str:
    if idx_pct is None:
        return "未知"
    if idx_pct <= -2:
        return "走弱"
    if idx_pct >= 2:
        return "走强"
    return "震荡"


def fwd_ret_after_buy(buy: float, bars: list[dict], n_td: int) -> Optional[float]:
    """买入后第 n 个可交易日收盘相对买入价（T+1 起算第 1 日）。"""
    if not bars or buy is None or buy <= 0:
        return None
    if len(bars) < n_td:
        return None
    c = bars[n_td - 1].get("close")
    if c is None:
        return None
    return (c - buy) / buy * 100.0


def analyze_one(pos: dict) -> dict:
    code = str(pos.get("stock_code") or "").zfill(6)
    buy_d = pos.get("buy_date")
    sell_d = pos.get("sell_date") or buy_d
    buy = _f(pos.get("buy_price"))
    sell = _f(pos.get("sell_price"))
    if not code or not buy_d or buy is None or sell is None:
        return {"id": pos.get("id"), "ok": False, "error": "缺字段"}

    # T+1：可交易区间从下一交易日开始；若卖出日=买入日（数据异常），仍用当日高低作参考并打标
    t1 = mkt.next_trading_day(buy_d)
    same_day_exit = bool(sell_d and buy_d and sell_d <= buy_d)
    mfe_start = buy_d if same_day_exit else (t1 or buy_d)

    # 取到卖出日；另多取 5 个交易日做 1/3/5 日表现（即使已卖）
    end_ext = sell_d
    for _ in range(8):
        nxt = mkt.next_trading_day(end_ext)
        if not nxt:
            break
        end_ext = nxt
        # 够用即可
        if end_ext > sell_d:
            # 至少延伸到 buy 后 8 个交易日
            pass
    # 明确：从 buy_d 起取足够 bars
    bars_all = bars_for(code, buy_d, end_ext or sell_d)
    # T+1 起至卖出日（含）用于 MFE/MAE
    bars_hold = [b for b in bars_all if mfe_start <= b["date"] <= sell_d]
    # 买入后可观察序列（T+1 起，不限于卖出）
    bars_fwd = [b for b in bars_all if (t1 or buy_d) <= b["date"]]

    highs = [b["high"] for b in bars_hold if b.get("high") is not None]
    lows = [b["low"] for b in bars_hold if b.get("low") is not None]
    mfe = max(((h - buy) / buy * 100.0) for h in highs) if highs else None
    mae = min(((l - buy) / buy * 100.0) for l in lows) if lows else None
    # 若无 K，用收盘近似
    if mfe is None and bars_hold:
        closes = [b["close"] for b in bars_hold if b.get("close") is not None]
        if closes:
            mfe = max((c - buy) / buy * 100.0 for c in closes)
            mae = min((c - buy) / buy * 100.0 for c in closes)

    gross = gross_return_pct(buy, sell)
    net = net_return_pct(buy, sell)
    # 退出效率：实际净收益 / MFE（MFE>0）；差异 = MFE - 实际毛收益（有利波动未兑现）
    exit_eff = None
    mfe_gap = None
    if mfe is not None and mfe > 1e-9:
        exit_eff = gross / mfe  # 用毛收益对 MFE，更直观「吃到多少有利波动」
        mfe_gap = mfe - gross
    elif mfe is not None and mfe <= 0:
        exit_eff = None
        mfe_gap = None

    # 归因标签
    tags = []
    if net < 0 and mfe is not None and mfe >= 2.0:
        tags.append("先涨后亏→优先看卖出")
    if net < 0 and (mfe is None or mfe < 1.0) and mae is not None and mae <= -2.0:
        tags.append("买入后持续偏弱→优先看选股/入场")
    if same_day_exit:
        tags.append("同日买卖(违反T+1或回填异常)")

    idx = index_ret(buy_d, sell_d)
    regime = regime_label(idx)
    if net < 0 and regime == "走弱":
        tags.append("区间指数走弱")

    adj = parse_buy_adj(pos)
    row = {
        "ok": True,
        "id": pos.get("id"),
        "stock_code": code,
        "stock_name": pos.get("stock_name"),
        "limit_up_date": pos.get("limit_up_date"),
        "buy_date": buy_d,
        "sell_date": sell_d,
        "buy_price": buy,
        "sell_price": sell,
        "hold_days": pos.get("hold_days"),
        "buy_adj_day": adj,
        "slot": parse_slot(pos),
        "industry": pos.get("industry_board") or "未知",
        "concept": pos.get("concept_board") or "未知",
        "sell_reason": pos.get("sell_reason"),
        "status": pos.get("status"),
        "gross_pnl_pct": round(gross, 2),
        "net_pnl_pct": round(net, 2),
        "recorded_pnl_pct": _f(pos.get("profit_rate")),
        "mfe_pct": round(mfe, 2) if mfe is not None else None,
        "mae_pct": round(mae, 2) if mae is not None else None,
        "exit_efficiency": round(exit_eff, 3) if exit_eff is not None else None,
        "mfe_minus_gross": round(mfe_gap, 2) if mfe_gap is not None else None,
        "ret_d1": (lambda v: round(v, 2) if v is not None else None)(fwd_ret_after_buy(buy, bars_fwd, 1)),
        "ret_d3": (lambda v: round(v, 2) if v is not None else None)(fwd_ret_after_buy(buy, bars_fwd, 3)),
        "ret_d5": (lambda v: round(v, 2) if v is not None else None)(fwd_ret_after_buy(buy, bars_fwd, 5)),
        "index_ret_hold_pct": round(idx, 2) if idx is not None else None,
        "market_regime": regime,
        "same_day_exit": same_day_exit,
        "bars_hold_n": len(bars_hold),
        "tags": tags,
        "notes": pos.get("notes"),
    }
    return row


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("ok")]
    nets = [r["net_pnl_pct"] for r in ok]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    n = len(nets)
    win_rate = len(wins) / n if n else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    pl_ratio = (avg_win / abs(avg_loss)) if losses and avg_loss != 0 else None
    expectancy = sum(nets) / n if n else 0

    # 少数大亏 vs 多数小亏
    loss_sorted = sorted(losses)  # 更负在前
    top3 = loss_sorted[:3]
    top3_share = (sum(top3) / sum(losses)) if losses and sum(losses) != 0 else None

    # 权益曲线（等权累加净收益）
    equity = [0.0]
    for r in sorted(ok, key=lambda x: (x["sell_date"] or x["buy_date"], x["id"])):
        equity.append(equity[-1] + r["net_pnl_pct"])
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        max_dd = min(max_dd, e - peak)

    # 连续亏损
    max_lose_streak = cur = 0
    for x in nets:  # 按原列表顺序？改为按卖出日
        pass
    chron = sorted(ok, key=lambda x: (x["sell_date"] or x["buy_date"], x["id"]))
    max_lose_streak = cur = 0
    for r in chron:
        if r["net_pnl_pct"] <= 0:
            cur += 1
            max_lose_streak = max(max_lose_streak, cur)
        else:
            cur = 0

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 2) if xs else None

    def group_stats(key_fn):
        buckets: dict[str, list] = defaultdict(list)
        for r in ok:
            buckets[str(key_fn(r))].append(r)
        out = {}
        for k, rs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            ns = [x["net_pnl_pct"] for x in rs]
            out[k] = {
                "n": len(rs),
                "win_rate": round(sum(1 for x in ns if x > 0) / len(ns), 3),
                "avg_net": round(sum(ns) / len(ns), 2),
                "sum_net": round(sum(ns), 2),
                "avg_mfe": avg([x.get("mfe_pct") for x in rs]),
                "avg_mae": avg([x.get("mae_pct") for x in rs]),
            }
        return out

    first_up_then_lose = [r for r in ok if "先涨后亏→优先看卖出" in (r.get("tags") or [])]
    weak_entry = [r for r in ok if "买入后持续偏弱→优先看选股/入场" in (r.get("tags") or [])]
    weak_mkt = [r for r in ok if r.get("market_regime") == "走弱" and r["net_pnl_pct"] <= 0]

    return {
        "n_trades": n,
        "n_win": len(wins),
        "n_loss": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_loss_ratio": round(pl_ratio, 3) if pl_ratio is not None else None,
        "expectancy_pct": round(expectancy, 2),
        "sum_net_pct": round(sum(nets), 2),
        "top3_loss_pct": [round(x, 2) for x in top3],
        "top3_loss_share_of_total_loss": round(top3_share, 3) if top3_share is not None else None,
        "max_drawdown_pct_points": round(max_dd, 2),
        "equity_end_pct_points": round(equity[-1], 2),
        "max_losing_streak": max_lose_streak,
        "avg_mfe": avg([r.get("mfe_pct") for r in ok]),
        "avg_mae": avg([r.get("mae_pct") for r in ok]),
        "avg_exit_efficiency": avg([r.get("exit_efficiency") for r in ok if r.get("exit_efficiency") is not None]),
        "avg_ret_d1": avg([r.get("ret_d1") for r in ok]),
        "avg_ret_d3": avg([r.get("ret_d3") for r in ok]),
        "avg_ret_d5": avg([r.get("ret_d5") for r in ok]),
        "by_adj_day": group_stats(lambda r: f"D{r.get('buy_adj_day')}" if r.get("buy_adj_day") else "未知"),
        "by_industry": group_stats(lambda r: r.get("industry") or "未知"),
        "by_concept": group_stats(lambda r: r.get("concept") or "未知"),
        "by_regime": group_stats(lambda r: r.get("market_regime") or "未知"),
        "by_sell_reason": group_stats(lambda r: r.get("sell_reason") or "未知"),
        "by_slot": group_stats(lambda r: r.get("slot") or "未知"),
        "n_first_up_then_lose": len(first_up_then_lose),
        "n_weak_entry": len(weak_entry),
        "n_loss_in_weak_market": len(weak_mkt),
        "cost_model": {
            "commission_rate": COMMISSION_RATE,
            "stamp_tax_rate": STAMP_TAX_RATE,
            "slippage_rate": SLIPPAGE_RATE,
            "note": "净收益=含双边佣金+卖出印花税+双边滑点；忽略佣金5元下限",
        },
    }


def render_md(summary: dict, rows: list[dict], start: str, end: str) -> str:
    s = summary
    lines = []
    lines.append(f"# 涨停回头低吸 · 亏损归因诊断（{start}～{end}）")
    lines.append("")
    lines.append("> 目的：先归因、再决定是否换打法。以下为完整成交复盘统计（含成本与 T+1 口径）。")
    lines.append("")
    lines.append("## 白话结论（先看这里）")
    lines.append("")
    # 自动结论
    n = s["n_trades"]
    if n == 0:
        lines.append("区间内无成交记录，无法诊断。")
        return "\n".join(lines) + "\n"

    exp = s["expectancy_pct"]
    wr = s["win_rate"]
    pl = s["profit_loss_ratio"]
    lines.append(
        f"- 样本 **{n}** 笔（买入日落在区间内）；等权净收益合计 **{s['sum_net_pct']}%**，"
        f"单笔期望 **{exp}%**，胜率 **{wr*100:.1f}%**，盈亏比 **{pl if pl is not None else '暂无'}**。"
    )
    if s.get("top3_loss_share_of_total_loss") is not None:
        share = s["top3_loss_share_of_total_loss"]
        if share >= 0.45:
            lines.append(f"- 最大 3 笔亏损约占全部亏损金额的 **{share*100:.0f}%** → 更像「少数大亏拉低」。")
        else:
            lines.append(f"- 最大 3 笔亏损约占全部亏损的 **{share*100:.0f}%** → 更像「亏损笔数偏多、普遍小亏」。")
    lines.append(
        f"- 最大回撤（等权收益点数）**{s['max_drawdown_pct_points']}**；最长连续亏损 **{s['max_losing_streak']}** 笔。"
    )
    lines.append(
        f"- 买点后延表现（相对买入价，T+1 起算）：D1 均 **{s['avg_ret_d1']}%** / "
        f"D3 **{s['avg_ret_d3']}%** / D5 **{s['avg_ret_d5']}%**。"
    )
    lines.append(
        f"- 波动结构：平均 MFE **{s['avg_mfe']}%**，平均 MAE **{s['avg_mae']}%**；"
        f"有 MFE 的交易平均退出效率 **{s['avg_exit_efficiency']}**（实际毛收益÷MFE）。"
    )
    lines.append(
        f"- 模式计数：先涨后亏 **{s['n_first_up_then_lose']}** 笔；"
        f"买入后持续偏弱 **{s['n_weak_entry']}** 笔；"
        f"指数走弱期亏损 **{s['n_loss_in_weak_market']}** 笔。"
    )
    # 优先建议
    lines.append("")
    lines.append("### 优化重心建议（按本批数据）")
    lines.append("")
    scores = {
        "卖出": s["n_first_up_then_lose"],
        "选股/入场": s["n_weak_entry"],
        "市场环境过滤": s["n_loss_in_weak_market"],
    }
    stop_n = s.get("by_sell_reason", {}).get("stop_loss", {}).get("n", 0)
    # D3 显著差于 D4 时加重入场
    d3 = (s.get("by_adj_day") or {}).get("D3") or {}
    d4 = (s.get("by_adj_day") or {}).get("D4") or {}
    if d3.get("n", 0) >= 3 and d4.get("n", 0) >= 3 and (d3.get("avg_net") or 0) + 2 < (d4.get("avg_net") or 0):
        scores["选股/入场"] += 2
    if stop_n >= max(1, n * 0.5):
        scores["选股/入场"] += 1
    primary = max(scores, key=scores.get)
    lines.append(
        f"- 本批优先建议盯：**{primary}**"
        f"（模式计数：先涨后亏 {s['n_first_up_then_lose']} / 持续偏弱 {s['n_weak_entry']} / "
        f"弱市亏损 {s['n_loss_in_weak_market']}；止损离场 {stop_n} 笔；"
        f"D3 均净 {d3.get('avg_net')}% vs D4 {d4.get('avg_net')}%）。"
    )
    lines.append("- 这不是换打法的充分条件；先按亏损来源改参数/过滤，再对比下一段样本。")
    lines.append("")

    lines.append("## 1. 交易表现")
    lines.append("")
    lines.append("| 指标 | 数值 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| 成交笔数 | {n} | 买入日∈[{start},{end}] |")
    lines.append(f"| 盈利 / 亏损笔数 | {s['n_win']} / {s['n_loss']} | 按净收益 |")
    lines.append(f"| 胜率 | {wr*100:.1f}% | 净收益>0 |")
    lines.append(f"| 平均盈利 | {s['avg_win_pct']}% | 仅盈利笔 |")
    lines.append(f"| 平均亏损 | {s['avg_loss_pct']}% | 仅亏损笔 |")
    lines.append(f"| 盈亏比 | {pl if pl is not None else '—'} | 平均盈利÷\|平均亏损\| |")
    lines.append(f"| 单笔期望 | {exp}% | 全部笔平均净收益 |")
    lines.append(f"| 等权合计 | {s['sum_net_pct']}% | 逐笔净收益相加 |")
    lines.append(f"| 最大3亏 | {s['top3_loss_pct']} | 占亏损合计 {None if s['top3_loss_share_of_total_loss'] is None else round(s['top3_loss_share_of_total_loss']*100,1)}% |")
    lines.append("")

    lines.append("## 2. 回撤与连续亏损")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 权益终点（等权收益点数） | {s['equity_end_pct_points']} |")
    lines.append(f"| 最大回撤（点数） | {s['max_drawdown_pct_points']} |")
    lines.append(f"| 最长连续亏损 | {s['max_losing_streak']} 笔 |")
    lines.append("")
    lines.append("说明：此处回撤是「每笔净收益率累加」的点数回撤，不是真实资金曲线；用于比较风险集中度。")
    lines.append("")

    lines.append("## 3. 买点质量（买入后 1/3/5 日）")
    lines.append("")
    lines.append("| 时点 | 平均表现 |")
    lines.append("|------|----------|")
    lines.append(f"| 买入后第 1 交易日收盘 | {s['avg_ret_d1']}% |")
    lines.append(f"| 买入后第 3 交易日收盘 | {s['avg_ret_d3']}% |")
    lines.append(f"| 买入后第 5 交易日收盘 | {s['avg_ret_d5']}% |")
    lines.append("")
    lines.append("解读：D1 就明显为负 → 偏「买错/买早」；D1 尚可但持有期末为负 → 偏「持有或卖点」问题。")
    lines.append("")

    lines.append("## 4. 亏损来源分组")
    lines.append("")
    for title, key in [
        ("按建仓调整日 D3/D4/D5", "by_adj_day"),
        ("按行业", "by_industry"),
        ("按题材/概念", "by_concept"),
        ("按持仓期指数环境", "by_regime"),
        ("按卖出原因", "by_sell_reason"),
        ("按买入时段", "by_slot"),
    ]:
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| 分组 | 笔数 | 胜率 | 平均净收益% | 合计% | 平均MFE | 平均MAE |")
        lines.append("|------|------|------|-------------|-------|---------|---------|")
        for name, g in (s.get(key) or {}).items():
            lines.append(
                f"| {name} | {g['n']} | {g['win_rate']*100:.0f}% | {g['avg_net']} | {g['sum_net']} | "
                f"{g['avg_mfe']} | {g['avg_mae']} |"
            )
        lines.append("")

    lines.append("## 5. 逐笔明细（含 MFE / MAE / 退出效率）")
    lines.append("")
    lines.append(
        "| ID | 代码 | 名称 | 买→卖 | 调整日 | 毛%/净% | MFE | MAE | 退出效率 | "
        "D1/D3/D5 | 指数环境 | 卖出原因 | 标签 |"
    )
    lines.append("|----|------|------|-------|--------|---------|-----|-----|----------|---------|----------|----------|------|")
    for r in sorted([x for x in rows if x.get("ok")], key=lambda z: z["buy_date"]):
        tags = "；".join(r.get("tags") or []) or "—"
        lines.append(
            f"| {r['id']} | {r['stock_code']} | {r['stock_name']} | {r['buy_date']}→{r['sell_date']} | "
            f"D{r.get('buy_adj_day')} | {r['gross_pnl_pct']}/{r['net_pnl_pct']} | "
            f"{r.get('mfe_pct')} | {r.get('mae_pct')} | {r.get('exit_efficiency')} | "
            f"{r.get('ret_d1')}/{r.get('ret_d3')}/{r.get('ret_d5')} | "
            f"{r.get('market_regime')}({r.get('index_ret_hold_pct')}) | {r.get('sell_reason')} | {tags} |"
        )
    lines.append("")

    lines.append("## 6. 回测/复盘约束（已按此执行）")
    lines.append("")
    cm = s["cost_model"]
    lines.append(f"- **T+1**：MFE/MAE 从买入下一交易日起算；同日买卖会打标「同日买卖」。")
    lines.append(f"- **成本**：佣金双边各 {cm['commission_rate']*10000:.1f}‱；印花税卖出 {cm['stamp_tax_rate']*10000:.0f}‱；滑点单边 {cm['slippage_rate']*100:.2f}%。")
    lines.append("- **涨跌停**：本诊断用真实日线高低收；未再做「涨停买不进」模拟（样本来自实盘/系统成交记录）。")
    lines.append("- **未入模**：佣金 5 元下限、过户费、冲击成本随成交额变化——小资金比例影响有限，大单需另估。")
    lines.append("")
    lines.append("## 7. 如何读「先涨后亏 / 持续下跌 / 弱市」")
    lines.append("")
    lines.append("| 模式 | 判定（本批） | 优化方向 |")
    lines.append("|------|--------------|----------|")
    lines.append("| 买入后先上涨最终亏 | MFE≥2% 且净收益<0 | 优先卖出：止盈/涨停次日/持有天数 |")
    lines.append("| 买入后持续偏弱 | MFE<1% 且 MAE≤-2% 且净亏 | 优先选股与入场：过滤、D3/D4、低吸深度 |")
    lines.append("| 仅弱市亏得多 | 持仓期指数≤-2% 的亏损笔 | 优先市场环境过滤 |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}；数据源：`data/position.json`。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--end", default="2026-10-29")
    args = ap.parse_args()
    start, end = args.start, args.end

    items = store.load_positions()
    picked = [
        p for p in items
        if p.get("buy_date") and start <= str(p["buy_date"]) <= end
        and p.get("sell_price") not in (None, "")
    ]
    print(f"[diag] positions closed in range: {len(picked)}")
    rows = []
    for i, p in enumerate(sorted(picked, key=lambda x: x.get("buy_date") or ""), 1):
        print(f"[diag] ({i}/{len(picked)}) {p.get('stock_code')} {p.get('buy_date')}…")
        rows.append(analyze_one(p))

    summary = summarize(rows)
    out_dir = os.path.join(ROOT, "data", "diagnostics")
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{start}_{end}".replace("-", "")
    jp = os.path.join(out_dir, f"loss_attribution_{tag}.json")
    payload = {
        "range": {"start": start, "end": end},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "trades": rows,
    }
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[diag] json → {jp}")

    doc_dir = os.path.join(ROOT, "文档")
    os.makedirs(doc_dir, exist_ok=True)
    mp = os.path.join(doc_dir, f"亏损归因诊断-{start}_{end}.md")
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_md(summary, rows, start, end))
    print(f"[diag] md → {mp}")
    print(json.dumps({k: summary[k] for k in (
        "n_trades", "win_rate", "expectancy_pct", "sum_net_pct",
        "max_drawdown_pct_points", "max_losing_streak",
        "n_first_up_then_lose", "n_weak_entry", "n_loss_in_weak_market",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
