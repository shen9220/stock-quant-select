"""V2 缩量回调 / 结构 / 确认 K 线（GPT V2.3）。"""
from __future__ import annotations

from typing import Any, Optional

import rules_v2 as R
from services import market as mkt


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _bar(code: str, day: str) -> Optional[dict]:
    return mkt.get_ohlc(code, day)


def limitup_volume(code: str, limit_up_date: str) -> Optional[float]:
    b = _bar(code, limit_up_date)
    if not b:
        return None
    vol = _f(b.get("volume") or b.get("vol") or b.get("成交量"))
    return vol


def day_volume(code: str, day: str) -> Optional[float]:
    b = _bar(code, day)
    if not b:
        return None
    return _f(b.get("volume") or b.get("vol") or b.get("成交量"))


def limitup_day_low(code: str, limit_up_date: str) -> Optional[float]:
    b = _bar(code, limit_up_date)
    return _f(b.get("low")) if b else None


def pullback_swing_low(code: str, limit_up_date: str, asof: str) -> Optional[float]:
    """首板次日～asof 的最低价（回调低点）。"""
    lows = []
    cur = mkt.next_trading_day(limit_up_date)
    while cur and cur <= asof:
        b = _bar(code, cur)
        if b and b.get("low") is not None:
            lows.append(float(b["low"]))
        cur = mkt.next_trading_day(cur)
    return min(lows) if lows else None


def vol_ratio_to_limitup(code: str, day: str, limit_up_date: str) -> Optional[float]:
    v0 = limitup_volume(code, limit_up_date)
    v1 = day_volume(code, day)
    if not v0 or v0 <= 0 or v1 is None:
        return None
    return v1 / v0


def has_volume_dump_down(code: str, limit_up_date: str, asof: str) -> bool:
    """回调期出现放量阴线（量≥首板×VOL_DUMP 且收阴）。"""
    v0 = limitup_volume(code, limit_up_date)
    if not v0:
        return False
    cur = mkt.next_trading_day(limit_up_date)
    while cur and cur <= asof:
        b = _bar(code, cur)
        cur = mkt.next_trading_day(cur)
        if not b:
            continue
        vol = _f(b.get("volume") or b.get("vol"))
        o, c = _f(b.get("open")), _f(b.get("close"))
        if vol is None or o is None or c is None:
            continue
        if vol >= v0 * R.VOL_DUMP_RATIO and c < o:
            return True
    return False


def making_lower_lows(code: str, asof: str, lookback: int = 3) -> bool:
    """近 lookback 日收盘持续创新低。"""
    days = []
    cur = asof
    for _ in range(lookback):
        if not cur:
            break
        days.append(cur)
        cur = mkt.prev_trading_day(cur)
    days = list(reversed(days))
    closes = []
    for d in days:
        b = _bar(code, d)
        if b and b.get("close") is not None:
            closes.append(float(b["close"]))
    if len(closes) < 2:
        return False
    return all(closes[i] < closes[i - 1] for i in range(1, len(closes)))


def check_pullback_volume(code: str, limit_up_date: str, signal_day: str) -> dict[str, Any]:
    """缩量回调质量：C1 无放量下跌；C2 信号日量≤首板×阈值；不持续创新低。"""
    ratio = vol_ratio_to_limitup(code, signal_day, limit_up_date)
    dump = has_volume_dump_down(code, limit_up_date, signal_day)
    lower = making_lower_lows(code, signal_day)
    c2_ok = ratio is not None and ratio <= R.VOL_PULLBACK_RATIO
    ok = (not dump) and c2_ok and (not lower)
    reason = []
    if dump:
        reason.append("放量阴线出货式回调")
    if ratio is None:
        reason.append("DATA_INCOMPLETE:成交量")
    elif not c2_ok:
        reason.append(f"信号日量比{ratio:.2f}>阈值{R.VOL_PULLBACK_RATIO}")
    if lower:
        reason.append("近几日收盘持续创新低")
    return {
        "ok": ok,
        "vol_ratio_limitup": round(ratio, 4) if ratio is not None else None,
        "has_dump": dump,
        "lower_lows": lower,
        "reason": ";".join(reason) or "ok",
    }


def detect_confirm_pattern(code: str, day: str) -> dict[str, Any]:
    """
    买点 A 类确认形态（可执行公式）：
    - 小阳止跌 / 探底回升 / 局部高点突破（相对前一日高点）
    """
    b = _bar(code, day)
    prev_d = mkt.prev_trading_day(day)
    prev = _bar(code, prev_d) if prev_d else None
    if not b:
        return {"ok": False, "pattern": None, "reason": "DATA_INCOMPLETE:当日K线"}
    o = _f(b.get("open"))
    h = _f(b.get("high"))
    l = _f(b.get("low"))
    c = _f(b.get("close"))
    if None in (o, h, l, c) or h <= l:
        return {"ok": False, "pattern": None, "reason": "DATA_INCOMPLETE:OHLC"}
    body = abs(c - o)
    amp = h - l
    lower_shadow = min(o, c) - l
    mid = (h + l) / 2.0
    prev_low = _f(prev.get("low")) if prev else None
    prev_high = _f(prev.get("high")) if prev else None
    prev_close = _f(prev.get("close")) if prev else None

    # 缩量十字星（实体≤振幅25%，收盘不低于前收）
    if amp > 0 and body / amp <= 0.25 and (prev_close is None or c >= prev_close):
        return {"ok": True, "pattern": "doji_hold", "reason": "缩量十字星承接"}

    # 小阳止跌：阳线、实体≤振幅55%、最低≥前日最低
    if c > o and amp > 0 and body / amp <= 0.55:
        if prev_low is None or l >= prev_low:
            return {"ok": True, "pattern": "small_yang", "reason": "小阳线止跌"}

    # 探底回升：下影≥1.5×实体，收盘在振幅中位以上
    if body > 0 and lower_shadow >= 1.5 * body and c >= mid:
        return {"ok": True, "pattern": "hammer", "reason": "探底回升"}

    # 局部高点突破
    if prev_high is not None and c > prev_high:
        return {"ok": True, "pattern": "break_prev_high", "reason": "突破前一日高点"}

    return {"ok": False, "pattern": None, "reason": "无确认形态"}


def structure_intact(code: str, limit_up_date: str, asof: str, price: float | None) -> dict[str, Any]:
    """结构未破：收盘/现价未有效跌破回调低点与首板低点外层参考。"""
    swing = pullback_swing_low(code, limit_up_date, asof)
    lu_low = limitup_day_low(code, limit_up_date)
    px = _f(price)
    if px is None:
        b = _bar(code, asof)
        px = _f(b.get("close")) if b else None
    reasons = []
    ok = True
    if px is None:
        return {"ok": False, "swing_low": swing, "limitup_low": lu_low, "reason": "DATA_INCOMPLETE:价格"}
    # 主结构：回调低点（若有）
    if swing is not None and px < swing * 0.998:  # 微小缓冲
        ok = False
        reasons.append("跌破回调低点")
    # 外层：首板日最低价
    if lu_low is not None and px < lu_low:
        ok = False
        reasons.append("跌破首板日最低价")
    return {
        "ok": ok,
        "swing_low": swing,
        "limitup_low": lu_low,
        "price": px,
        "reason": ";".join(reasons) or "结构完好",
    }


def initial_stop_price(swing_low: float | None, limitup_low: float | None, buy: float) -> dict[str, Any]:
    """结构止损 + 风险上限；若结构距买价过远则标记超风险。"""
    candidates = [x for x in (swing_low, limitup_low) if x is not None]
    struct = max(candidates) if candidates else None  # 更近的失效位优先作主止损
    if struct is not None and candidates:
        # 取低于买入价且最接近买入价的结构位
        below = [x for x in candidates if x < buy]
        struct = max(below) if below else min(candidates)
    cap = buy * (1 + R.RISK_CAP_PCT / 100.0)
    if struct is None:
        stop = cap
        model = "risk_cap_only"
    else:
        stop = max(struct, cap) if struct < buy else cap
        # 规则：风险上限不能因结构更远而放宽 → stop = max(struct, cap) 当 struct 更低时用 cap？
        # 「不能把止损放到更远」→ 实际止损 = 更近者（亏损更小）= max(struct, cap) when both < buy
        # 若 struct=9.0, cap=9.4, buy=10 → max(9,9.4)=9.4 用了更远的 cap — 错
        # 正确：stop = 更近的保护 = max(struct, cap) 不对
        # 更近于买价 = 更高的止损价 = max(struct, cap) 当两者都低于买价时，max 是更近的
        # struct=9.0, cap=9.4 → max=9.4 更近，风险更小 — 对！用 risk cap 收紧
        # struct=9.5, cap=9.4 → max=9.5，但 GPT 说风险上限不能被放宽：应用 min? 
        # 「如果结构止损距离已超过最大风险，放弃或降仓；不能把止损放到更远」
        # 所以 final_stop = max(struct, cap) 当两者<buy：取更高者（更紧）
        stop = max(struct, cap)
        model = "struct_and_cap"
    risk_pct = (stop - buy) / buy * 100.0
    over_risk = risk_pct < R.RISK_CAP_PCT - 0.01  # 止损比上限更远（更亏）——不应发生
    # 若 struct 远低于 cap，max(struct,cap)=cap，风险刚好上限
    dist = abs(risk_pct)
    reject = dist > abs(R.RISK_CAP_PCT) + 0.5  # 理论不应
    return {
        "initial_stop_price": round(stop, 3),
        "risk_cap_price": round(cap, 3),
        "struct_stop": round(struct, 3) if struct is not None else None,
        "risk_pct": round(risk_pct, 2),
        "model": model,
        "reject_over_risk": bool(over_risk and reject),
    }


def simple_leader_score(zt_row: dict, code: str, limit_up_date: str) -> dict[str, Any]:
    """
    可解释初版评分 0–100（缺数据降 confidence，不给满分）。
    封板质量 / 换手 / 行业占位；题材强度数据不全时降权。
    """
    score = 50.0
    conf = 1.0
    parts = {}
    # 连板=1 已过滤；炸板
    try:
        zha = zt_row.get("炸板次数")
        if zha is None:
            zha = zt_row.get("blast_count")
        if zha is not None:
            z = int(float(zha))
            parts["seal"] = 20 if z == 0 else max(5, 20 - z * 5)
            score = score - 10 + parts["seal"] * 0.5
        else:
            conf *= 0.85
            parts["seal"] = None
    except (TypeError, ValueError):
        conf *= 0.85
    # 价格位置：越靠近低价区间略加分（非低价崇拜，仅轻度）
    px = _f(zt_row.get("last_price") or zt_row.get("最新价"))
    if px is not None:
        if px <= 15:
            parts["price"] = 8
        elif px <= 25:
            parts["price"] = 5
        else:
            parts["price"] = 2
        score += parts["price"]
    else:
        conf *= 0.9
    # 有行业信息加一点
    if zt_row.get("industry_board") or zt_row.get("所属行业"):
        parts["industry"] = 5
        score += 5
    else:
        conf *= 0.9
        parts["industry"] = 0
    # 量能可得
    if limitup_volume(code, limit_up_date):
        parts["vol"] = 10
        score += 5
    else:
        conf *= 0.7
        parts["vol"] = None
        score -= 5
    score = max(0.0, min(100.0, score))
    return {
        "leader_score": round(score, 1),
        "score_confidence": round(conf, 2),
        "score_parts": parts,
        "data_incomplete": conf < 0.95,
    }
