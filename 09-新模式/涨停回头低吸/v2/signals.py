"""V2 买入/卖出（GPT V2.3 主组 V2_MAIN）。确认次日执行；关补仓；T+1 硬校验。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import rules_v2 as R
import store as store_mod
from services import market as mkt
from v2 import quality as Q

PROFILE = "v2"


def st():
    return store_mod.for_profile(PROFILE)


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def recompute_pnl(pos: dict) -> dict:
    buy = _f(pos.get("buy_price"))
    sell = _f(pos.get("sell_price"))
    last = _f(pos.get("last_price"))
    if buy is None:
        return pos
    ref = sell if sell is not None else last
    if ref is not None:
        rate = round((ref - buy) / buy * 100, 2)
        pos["profit_rate"] = rate
        pos["is_profit"] = rate > 0
    return pos


def mark_sell(pid: int, sell_price: float, sell_date: str, reason: str, status: str = R.POS_SOLD) -> dict:
    s = st()
    pos = s.get_position(pid)
    if not pos:
        raise ValueError("记录不存在")
    buy_date = pos.get("buy_date")
    # T+1 硬规则
    if buy_date and sell_date <= str(buy_date):
        s.update_position(pid, {
            "exit_pending_t1": True,
            "pending_sell_price": float(sell_price),
            "pending_sell_reason": reason,
            "pending_sell_status": status,
            "t1_tag": "T1_VIOLATION_OR_DATA_ERROR",
        })
        raise ValueError(f"T+1：买入日 {buy_date} 不可同日/当日卖出，已记 EXIT_PENDING_T1")
    hold = mkt.hold_day_of(buy_date, sell_date) if buy_date else None
    patch = {
        "sell_price": round(float(sell_price), 3),
        "sell_date": sell_date,
        "sell_reason": reason,
        "status": status,
        "hold_days": hold,
        "await_limit_up_exit": False,
        "exit_pending_t1": False,
        "paper": bool(R.PAPER_MODE),
    }
    updated = s.update_position(pid, patch)
    pnl = recompute_pnl(dict(updated or {}))
    return s.update_position(pid, {
        "profit_rate": pnl.get("profit_rate"),
        "is_profit": pnl.get("is_profit"),
    }) or pnl


def create_position_from_buy(
    *,
    stock_name: str,
    buy_date: str,
    buy_price: float,
    limit_up_date: str | None = None,
    position_pct: float = R.FIRST_BUY_PCT,
    watch_id: int | None = None,
    notes: str = "",
    stock_code: str = "",
    signal_day: str | None = None,
    buy_pattern: str | None = None,
    manual_d3: bool = False,
    require_watch: bool = True,
) -> dict:
    s = st()
    if s.holding_count() >= R.MAX_CONCURRENT_HOLDINGS:
        raise ValueError(f"最多同时持有 {R.MAX_CONCURRENT_HOLDINGS} 只")
    watch = s.get_watch(watch_id) if watch_id else None
    code_hint = (stock_code or (watch or {}).get("stock_code") or "").strip()
    name_hint = (stock_name or (watch or {}).get("stock_name") or "").strip()
    code, name = mkt.resolve_code_name(name=name_hint, code=code_hint)
    if not code and code_hint.isdigit():
        code = code_hint.zfill(6)
        name = name_hint or code
    if not code:
        raise ValueError(f"无法解析股票：{name_hint or stock_code}")
    if require_watch and not watch:
        raise ValueError("只能从观察池买入")
    lup = limit_up_date or (watch or {}).get("limit_up_date")
    if not lup:
        raise ValueError("缺少涨停日期")
    adj = mkt.adj_day_of(lup, buy_date)
    swing = Q.pullback_swing_low(code, lup, buy_date)
    lu_low = Q.limitup_day_low(code, lup)
    stop = Q.initial_stop_price(swing, lu_low, float(buy_price))
    series = (watch or {}).get("ma_series") or mkt.build_ma_series(code, lup, R.MAX_OBS_DAYS)
    snap = series.get(str(min(max(adj or 1, 1), R.MAX_OBS_DAYS))) or {}
    pos = {
        "stock_code": code,
        "stock_name": name or stock_name,
        "limit_up_date": lup,
        "concept_board": (watch or {}).get("concept_board"),
        "industry_board": (watch or {}).get("industry_board"),
        "ma_series": series,
        "view_adj_day": adj or 1,
        "ma10": snap.get("ma10"), "ma20": snap.get("ma20"),
        "ma25": snap.get("ma25"), "ma30": snap.get("ma30"),
        "last_price": float(buy_price),
        "buy_date": buy_date,
        "buy_price": round(float(buy_price), 3),
        "position_pct": float(position_pct),
        "add_buys": [],
        "sell_date": None, "sell_price": None, "sell_reason": None,
        "hold_days": 1,
        "status": R.POS_HOLDING,
        "notes": notes,
        "watch_id": (watch or {}).get("id"),
        "strategy_version": R.STRATEGY_VERSION,
        "experiment_id": R.EXPERIMENT_ID,
        "signal_day": signal_day,
        "buy_pattern": buy_pattern,
        "manual_d3": bool(manual_d3),
        "paper": bool(R.PAPER_MODE),
        "peak_price": float(buy_price),
        "breakeven_armed": False,
        "pullback_swing_low": swing,
        "limitup_day_low": lu_low,
        "initial_stop_price": stop["initial_stop_price"],
        "risk_cap_price": stop["risk_cap_price"],
        "stop_loss_price": stop["initial_stop_price"],
        "stop_loss_pnl": stop["risk_pct"],
        "profile": "v2",
    }
    recompute_pnl(pos)
    saved = s.upsert_position(pos)
    if watch and watch.get("id"):
        s.update_watch(int(watch["id"]), {"status": R.WATCH_BOUGHT})
    return saved


def _entry_gates(watch: dict, today: str, slot: str) -> tuple[bool, str, dict]:
    """自动买入全部闸门。"""
    code = str(watch.get("stock_code") or "").zfill(6)
    lup = watch.get("limit_up_date")
    if not code or not lup:
        return False, "缺代码/涨停日", {}
    if slot not in R.ALLOWED_SLOTS:
        return False, "时段不允许自动买", {}
    adj = mkt.adj_day_of(lup, today)
    if adj is None:
        return False, "无法计算调整日", {}
    if adj not in R.BUILD_ADJ_DAYS:
        if adj == 3 and not R.D3_AUTO_BUY:
            return False, "D3不自动买", {"adj": adj}
        return False, f"非建仓窗D{adj}", {"adj": adj}
    series = watch.get("ma_series") or mkt.build_ma_series(code, lup, R.MAX_OBS_DAYS)
    snap = series.get(str(adj)) or {}
    px = _f(snap.get("close")) or _f(watch.get("last_price"))
    if not mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), px):
        return False, "不在低吸观察区", {"adj": adj}
    if R.LEADER_SCORE_ENABLED:
        sc = _f(watch.get("leader_score"))
        conf = _f(watch.get("score_confidence")) or 1.0
        if sc is not None and conf >= 0.7 and sc < R.LEADER_SCORE_MIN:
            return False, f"龙头评分{sc}<{R.LEADER_SCORE_MIN}", {"adj": adj}
    volq = Q.check_pullback_volume(code, lup, today)
    if not volq["ok"]:
        return False, f"缩量未过:{volq['reason']}", {"adj": adj, "volq": volq}
    struct = Q.structure_intact(code, lup, today, px)
    if not struct["ok"]:
        return False, f"结构破:{struct['reason']}", {"adj": adj, "struct": struct}
    conf = Q.detect_confirm_pattern(code, today)
    if not conf["ok"]:
        return False, f"无确认K:{conf['reason']}", {"adj": adj}
    stop = Q.initial_stop_price(struct.get("swing_low"), struct.get("limitup_low"), float(px))
    if stop.get("reject_over_risk"):
        return False, "止损距离超风险上限", {"adj": adj, "stop": stop}
    meta = {
        "adj": adj, "price": px, "volq": volq, "struct": struct,
        "confirm": conf, "stop": stop, "series": series, "snap": snap,
    }
    return True, "ok", meta


def add_to_position(pid: int, add_price: float, add_date: str, add_pct: float = 50.0) -> dict:
    raise ValueError("V2 首月关闭自动/手动补仓（对照期只做首仓）")


def check_buy_signals(asof: str | None = None, *, slot: str = "morning", use_live: bool = False) -> dict:
    """
    两段式：
    1) 当日确认 → 写入 pending_buy（execute_date=下一交易日）
    2) 到执行日 → 检查跳空后模拟开盘买入
    """
    today = asof or datetime.now().strftime("%Y-%m-%d")
    s = st()
    actions = []
    holdings = s.holding_count()

    # ① 执行到期挂单
    for watch in s.load_watches():
        if watch.get("status") != R.WATCH_WATCHING:
            continue
        pb = watch.get("pending_buy") or {}
        if not pb.get("execute_date"):
            continue
        if str(pb["execute_date"]) > today:
            continue
        if str(pb["execute_date"]) < today:
            # 过期未执行
            s.update_watch(int(watch["id"]), {"pending_buy": None, "pending_buy_expired": pb})
            actions.append({"type": "pending_expire", "code": watch.get("stock_code")})
            continue
        if holdings >= R.MAX_CONCURRENT_HOLDINGS:
            actions.append({"type": "buy_skip", "code": watch.get("stock_code"), "error": "持仓已满"})
            continue
        code = str(watch.get("stock_code") or "").zfill(6)
        signal_day = pb.get("signal_day")
        sig_close = _f(pb.get("signal_close"))
        ohlc = mkt.get_ohlc(code, today)
        open_px = _f(ohlc.get("open")) if ohlc else None
        if open_px is None or sig_close is None or sig_close <= 0:
            actions.append({"type": "buy_skip", "code": code, "error": "无开盘价/信号收盘"})
            continue
        gap = (open_px - sig_close) / sig_close * 100.0
        if gap > R.SKIP_GAP_UP_PCT:
            s.update_watch(int(watch["id"]), {"pending_buy": None, "gap_skip": {"gap": gap, "day": today}})
            actions.append({"type": "gap_skip", "code": code, "gap": round(gap, 2)})
            continue
        # 执行前再验结构与区间
        ok, reason, meta = _entry_gates({**watch, "ma_series": watch.get("ma_series")}, today, slot)
        # 执行日允许不在「确认形态」当日；放宽：只验缩量/结构/区间/窗
        lup = watch.get("limit_up_date")
        adj = mkt.adj_day_of(lup, today)
        series = watch.get("ma_series") or mkt.build_ma_series(code, lup, R.MAX_OBS_DAYS)
        snap = series.get(str(adj or 4)) or {}
        px = open_px
        if adj not in R.BUILD_ADJ_DAYS:
            s.update_watch(int(watch["id"]), {"pending_buy": None})
            actions.append({"type": "buy_skip", "code": code, "error": f"执行日D{adj}脱窗"})
            continue
        if not mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), px):
            # 若突破买点仍成立可走平台逻辑；首月主组放弃均线低吸冒用
            s.update_watch(int(watch["id"]), {"pending_buy": None, "exec_skip_zone": True})
            actions.append({"type": "buy_skip", "code": code, "error": "执行日脱离观察区"})
            continue
        struct = Q.structure_intact(code, lup, today, px)
        if not struct["ok"]:
            s.update_watch(int(watch["id"]), {"pending_buy": None})
            actions.append({"type": "buy_skip", "code": code, "error": struct["reason"]})
            continue
        try:
            pos = create_position_from_buy(
                stock_name=watch.get("stock_name") or code,
                stock_code=code,
                buy_date=today,
                buy_price=px,
                limit_up_date=lup,
                watch_id=int(watch["id"]),
                notes=f"v2_exec adj={adj} gap={gap:.2f} pattern={pb.get('pattern')} paper={R.PAPER_MODE}",
                signal_day=signal_day,
                buy_pattern=pb.get("pattern"),
            )
            holdings += 1
            s.update_watch(int(watch["id"]), {"pending_buy": None})
            actions.append({"type": "buy", "code": code, "price": px, "pos_id": pos.get("id"), "paper": R.PAPER_MODE})
        except Exception as e:
            actions.append({"type": "buy_skip", "code": code, "error": str(e)})

    # ② 扫描新确认 → 挂次日执行
    for watch in s.load_watches():
        if watch.get("status") != R.WATCH_WATCHING:
            continue
        if watch.get("pending_buy"):
            continue
        if holdings >= R.MAX_CONCURRENT_HOLDINGS:
            break
        ok, reason, meta = _entry_gates(watch, today, slot)
        code = watch.get("stock_code")
        if not ok:
            if reason and "无确认" not in reason and "D3" not in reason:
                actions.append({"type": "reject", "code": code, "reason": reason})
            continue
        if not R.CONFIRM_NEXT_DAY_EXEC:
            # 极少：同日执行（A股通常不可买后卖，但仍可开仓）
            pass
        exec_d = mkt.next_trading_day(today)
        if not exec_d:
            continue
        pb = {
            "signal_day": today,
            "execute_date": exec_d,
            "signal_close": meta.get("price"),
            "pattern": (meta.get("confirm") or {}).get("pattern"),
            "vol_ratio": (meta.get("volq") or {}).get("vol_ratio_limitup"),
            "armed_at": datetime.now().isoformat(timespec="seconds"),
        }
        s.update_watch(int(watch["id"]), {"pending_buy": pb, "adj_day": meta.get("adj")})
        actions.append({"type": "pending_buy", "code": code, "execute_date": exec_d, "pattern": pb["pattern"]})

    s.meta_set({"last_v2_buy_check": today, "buy_actions": len(actions), "buy_slot": slot, "paper": R.PAPER_MODE})
    return {"ok": True, "asof": today, "slot": slot, "actions": actions, "paper": R.PAPER_MODE, "profile": "v2"}


def check_sell_signals(asof: str | None = None, *, slot: str = "morning", use_live: bool = False) -> dict:
    today = asof or datetime.now().strftime("%Y-%m-%d")
    s = st()
    actions = []
    for pos in s.load_positions():
        if pos.get("status") != R.POS_HOLDING and pos.get("status") != R.POS_EXIT_PENDING_T1:
            continue
        # 消化 T+1 挂起
        if pos.get("exit_pending_t1") and pos.get("buy_date") and str(pos["buy_date"]) < today:
            try:
                mark_sell(
                    int(pos["id"]),
                    float(pos.get("pending_sell_price") or pos.get("last_price")),
                    today,
                    pos.get("pending_sell_reason") or "t1_delayed",
                    pos.get("pending_sell_status") or R.POS_SOLD,
                )
                actions.append({"type": "t1_flush", "code": pos.get("stock_code")})
                continue
            except Exception as e:
                actions.append({"type": "t1_flush_fail", "code": pos.get("stock_code"), "error": str(e)})

        if pos.get("status") != R.POS_HOLDING:
            continue
        code = str(pos.get("stock_code") or "").zfill(6)
        buy = _f(pos.get("buy_price"))
        if not code or buy is None:
            continue
        ohlc = mkt.get_ohlc(code, today)
        if not ohlc:
            continue
        last = float(ohlc["close"])
        high = float(ohlc.get("high") or last)
        open_px = _f(ohlc.get("open"))
        peak = max(_f(pos.get("peak_price")) or buy, high)
        s.update_position(int(pos["id"]), {"last_price": last, "peak_price": peak})

        hold = mkt.hold_day_of(pos["buy_date"], today) if pos.get("buy_date") else 0
        pnl = (last - buy) / buy * 100.0
        peak_pnl = (peak - buy) / buy * 100.0
        dd_from_peak = (peak - last) / peak * 100.0 if peak else 0.0

        # 优先级（GPT）：结构/风险上限 → 保本/移动止盈 → 时间
        stop_px = _f(pos.get("initial_stop_price")) or _f(pos.get("stop_loss_price"))
        cap_px = _f(pos.get("risk_cap_price"))
        lu_low = _f(pos.get("limitup_day_low"))

        # 结构/风险：收盘确认
        if stop_px is not None and last < stop_px:
            try:
                mark_sell(int(pos["id"]), last, today, "struct_stop", R.POS_STOPPED)
                actions.append({"type": "struct_stop", "code": code, "price": last})
            except ValueError as e:
                actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
            continue
        if cap_px is not None and last <= cap_px:
            try:
                mark_sell(int(pos["id"]), last, today, "risk_cap_stop", R.POS_STOPPED)
                actions.append({"type": "risk_cap", "code": code, "price": last})
            except ValueError as e:
                actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
            continue
        if lu_low is not None and last < lu_low:
            try:
                mark_sell(int(pos["id"]), last, today, "limitup_low_break", R.POS_STOPPED)
                actions.append({"type": "limitup_low_break", "code": code, "price": last})
            except ValueError as e:
                actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
            continue

        # 保本武装
        if peak_pnl >= R.TRAIL_ARM_BREAKEVEN_PCT:
            s.update_position(int(pos["id"]), {"breakeven_armed": True})
            pos["breakeven_armed"] = True

        if R.TRAIL_MODEL == "TRAIL_TIGHT":
            if peak_pnl >= R.TRAIL_TIGHT_PEAK and dd_from_peak >= R.TRAIL_TIGHT_DD:
                try:
                    mark_sell(int(pos["id"]), last, today, "trailing_tp_tight", R.POS_SOLD)
                    actions.append({"type": "trail_tight", "code": code, "price": last})
                except ValueError as e:
                    actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
                continue
            if R.TRAIL_LOOSE_PEAK <= peak_pnl < R.TRAIL_TIGHT_PEAK and dd_from_peak >= R.TRAIL_LOOSE_DD:
                try:
                    mark_sell(int(pos["id"]), last, today, "trailing_tp_loose", R.POS_SOLD)
                    actions.append({"type": "trail_loose", "code": code, "price": last})
                except ValueError as e:
                    actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
                continue
            if pos.get("breakeven_armed") and last <= buy:
                try:
                    mark_sell(int(pos["id"]), last, today, "breakeven_stop", R.POS_SOLD)
                    actions.append({"type": "breakeven", "code": code, "price": last})
                except ValueError as e:
                    actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
                continue

        # 涨停收盘 → 次日处理（主策略：不强行 70/30，低开/转弱再清）
        try:
            import price_service as ps
            prev = ps.get_prev_close(code, today)
            if prev and ps.is_at_limit_up(last, prev, code):
                s.update_position(int(pos["id"]), {"await_limit_up_exit": True, "limit_up_hold_date": today})
                actions.append({"type": "limit_up_hold", "code": code})
                continue
        except Exception:
            pass

        if pos.get("await_limit_up_exit") and pos.get("limit_up_hold_date") and str(pos["limit_up_hold_date"]) < today:
            exit_px = open_px if open_px is not None else last
            try:
                mark_sell(int(pos["id"]), float(exit_px), today, "limit_up_next_manage", R.POS_SOLD)
                actions.append({"type": "limit_up_next", "code": code, "price": exit_px})
            except ValueError as e:
                actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
            continue

        if hold >= R.HOLD_MAX_DAYS:
            try:
                mark_sell(int(pos["id"]), last, today, "day7_close", R.POS_SOLD)
                actions.append({"type": "day7", "code": code, "price": last})
            except ValueError as e:
                actions.append({"type": "sell_pending_t1", "code": code, "error": str(e)})
            continue

        s.update_position(int(pos["id"]), {"hold_days": hold, "profit_rate": round(pnl, 2), "is_profit": pnl > 0})

    s.meta_set({"last_v2_sell_check": today, "sell_actions": len(actions), "sell_slot": slot})
    return {"ok": True, "asof": today, "slot": slot, "actions": actions, "profile": "v2"}
