"""买入 / 卖出信号与持仓写入。"""
from __future__ import annotations

from datetime import datetime

import store
from rules import (
    ADD_ADJ_DAYS,
    ADD_BUY_PCT,
    AUCTION_SINGLE_SLEEP,
    BUILD_ADJ_DAYS,
    BUY_DROP_PCT,
    FIRST_BUY_PCT,
    HOLD_MAX_DAYS,
    MAX_CONCURRENT_HOLDINGS,
    MAX_OBS_DAYS,
    STOP_LOSS_MA_PCT,
    WATCH_BOUGHT,
    WATCH_EXPIRED,
    WATCH_WATCHING,
    POS_HOLDING,
    POS_SOLD,
    POS_STOPPED,
)
from services import market as mkt


def _f(v):
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
    stop = _f(pos.get("stop_loss_price"))
    if buy is None:
        return pos
    ref = sell if sell is not None else last
    if ref is not None:
        rate = round((ref - buy) / buy * 100, 2)
        pos["profit_rate"] = rate
        pos["is_profit"] = rate > 0
    if stop is not None:
        pos["stop_loss_pnl"] = round((stop - buy) / buy * 100, 2)
    return pos



def create_position_from_buy(
    *,
    stock_name: str,
    buy_date: str,
    buy_price: float,
    limit_up_date: str | None = None,
    position_pct: float = FIRST_BUY_PCT,
    obs_id: int | None = None,
    watch_id: int | None = None,
    notes: str = "",
    stock_code: str = "",
    enforce_adj_day: bool = False,
    require_watch: bool = True,
) -> dict:
    """持有池买入：默认必须来自观察池（二道筛选）。"""
    if store.holding_count() >= MAX_CONCURRENT_HOLDINGS:
        raise ValueError(f"最多同时持有 {MAX_CONCURRENT_HOLDINGS} 只，请先卖出后再买")

    watch = store.get_watch(watch_id) if watch_id else None
    if not watch and obs_id:
        watch = store.get_watch(obs_id)

    # 优先用观察池/入参代码，避免仅靠名称解析（空格名、码表不可用会失败）
    code_hint = (stock_code or (watch or {}).get("stock_code") or "").strip()
    name_hint = (stock_name or (watch or {}).get("stock_name") or "").strip()
    code, name = mkt.resolve_code_name(name=name_hint, code=code_hint)
    if not code and code_hint and code_hint.isdigit():
        code = code_hint.zfill(6)
        name = name_hint or code
    if not code:
        raise ValueError(f"无法解析股票：{name_hint or stock_code}")

    if not watch and code:
        cands = [
            x for x in store.load_watches()
            if str(x.get("stock_code")).zfill(6) == code and x.get("status") == WATCH_WATCHING
        ]
        cands.sort(key=lambda x: x.get("limit_up_date") or "", reverse=True)
        if cands:
            watch = cands[0]
    if require_watch and not watch:
        raise ValueError("只能从观察池买入：请先将涨停池标的加入观察池")

    lup = limit_up_date or (watch or {}).get("limit_up_date")
    if not lup:
        raise ValueError("缺少涨停日期")

    adj = mkt.adj_day_of(lup, buy_date)
    if enforce_adj_day and adj < min(BUILD_ADJ_DAYS):
        raise ValueError(f"最早 D{min(BUILD_ADJ_DAYS)} 才可买入，当前为 D{adj}")

    series = (watch or {}).get("ma_series") or mkt.build_ma_series(code, lup, MAX_OBS_DAYS)
    snap = series.get(str(min(max(adj, 1), MAX_OBS_DAYS))) or series.get("1") or {}
    basic = mkt.fetch_basic_filters(code)

    stop = mkt.compute_stop_loss_price(
        float(buy_price),
        snap.get("ma20"),
        snap.get("ma25"),
        snap.get("ma30"),
    )

    t1_open = (watch or {}).get("t1_open")
    if t1_open in (None, ""):
        t1 = mkt.trading_days_after(lup, 1)
        if t1:
            ohlc = mkt.get_ohlc(code, t1)
            if ohlc:
                t1_open = ohlc.get("open")

    pos = {
        "stock_code": code,
        "stock_name": name or stock_name,
        "limit_up_date": lup,
        "concept_board": (watch or {}).get("concept_board") or basic.get("concept_board"),
        "industry_board": (watch or {}).get("industry_board") or basic.get("industry_board"),
        "t1_open": t1_open,
        "ma_series": series,
        "view_adj_day": adj or 1,
        "ma10": snap.get("ma10"),
        "ma20": snap.get("ma20"),
        "ma25": snap.get("ma25"),
        "ma30": snap.get("ma30"),
        "last_price": snap.get("close") or float(buy_price),
        "buy_date": buy_date,
        "buy_price": round(float(buy_price), 3),
        "position_pct": float(position_pct),
        "add_buys": [],
        "sell_date": None,
        "sell_price": None,
        "sell_reason": None,
        "hold_days": 1,
        "status": POS_HOLDING,
        "notes": notes,
        "buy_price_locked": False,
        "sell_price_locked": False,
        "watch_id": (watch or {}).get("id"),
        **stop,
    }
    recompute_pnl(pos)
    saved = store.upsert_position(pos)
    if watch and watch.get("id"):
        store.update_watch(int(watch["id"]), {"status": WATCH_BOUGHT})
    return saved


def add_to_position(pid: int, add_price: float, add_date: str, add_pct: float = ADD_BUY_PCT) -> dict:
    pos = store.get_position(pid)
    if not pos or pos.get("status") != POS_HOLDING:
        raise ValueError("持仓不存在或已结束")
    old_pct = float(pos.get("position_pct") or FIRST_BUY_PCT)
    if old_pct >= 99:
        raise ValueError("已是全仓，无需再补")
    old_price = float(pos["buy_price"])
    new_pct = min(100.0, old_pct + float(add_pct))
    # 加权成本
    w_old = old_pct
    w_new = new_pct - old_pct
    avg = (old_price * w_old + float(add_price) * w_new) / (w_old + w_new)
    adds = list(pos.get("add_buys") or [])
    adds.append({"date": add_date, "price": float(add_price), "pct": w_new})
    # 补仓后重算止损（按新成本）
    stop = mkt.compute_stop_loss_price(avg, pos.get("ma20"), pos.get("ma25"), pos.get("ma30"))
    patch = {
        "buy_price": round(avg, 3),
        "position_pct": new_pct,
        "add_buys": adds,
        **stop,
    }
    if not pos.get("buy_price_locked"):
        pass
    updated = store.update_position(pid, patch)
    return recompute_pnl(updated or {})


def mark_sell(pid: int, sell_price: float, sell_date: str, reason: str, status: str = POS_SOLD) -> dict:
    pos = store.get_position(pid)
    if not pos:
        raise ValueError("记录不存在")
    if pos.get("sell_price_locked") and pos.get("sell_price") not in (None, ""):
        # 仍允许显式手动卖出覆盖？手动调用本函数视为确认
        pass
    hold = mkt.hold_day_of(pos.get("buy_date"), sell_date) if pos.get("buy_date") else None
    patch = {
        "sell_price": round(float(sell_price), 3),
        "sell_date": sell_date,
        "sell_reason": reason,
        "status": status,
        "hold_days": hold,
        "await_limit_up_exit": False,
    }
    updated = store.update_position(pid, patch)
    pnl = recompute_pnl(dict(updated or {}))
    # 必须回写收益率，否则编辑卖出价后列表不刷新
    return store.update_position(pid, {
        "profit_rate": pnl.get("profit_rate"),
        "is_profit": pnl.get("is_profit"),
        "stop_loss_pnl": pnl.get("stop_loss_pnl"),
    }) or pnl


def _is_limit_up_price(code: str, price: float, prev: float | None) -> bool:
    if prev is None or prev <= 0 or price is None:
        return False
    try:
        import price_service as ps
        return bool(ps.is_at_limit_up(float(price), float(prev), code))
    except Exception:
        return (float(price) - float(prev)) / float(prev) * 100 >= 9.9


def _day_change_pct(code: str, date_str: str) -> float | None:
    ohlc = mkt.get_ohlc(code, date_str)
    if not ohlc:
        return None
    prev = None
    try:
        import price_service as ps
        prev = ps.get_prev_close(code, date_str)
    except Exception:
        prev = None
    if not prev:
        return None
    return (float(ohlc["close"]) - float(prev)) / float(prev) * 100


def _is_add_candle_ok(snap: dict) -> bool:
    """补仓形态：阴线或十字星；持续阳线（红盘）不加仓。"""
    if not snap:
        return False
    if snap.get("is_doji") or snap.get("is_yin"):
        return True
    # 无标记时：收 < 开 视为可补
    o, c = snap.get("open"), snap.get("close")
    if o is not None and c is not None:
        return c <= o
    return False



def check_buy_signals(
    asof: str | None = None,
    use_close_as_ref: bool = True,
    *,
    use_live: bool = False,
    slot: str = "morning",
) -> dict:
    """仅扫描【观察池】：D3–D4 建仓；D4–D5 阴线/十字星补仓。use_live=现价（单票）。"""
    import time as time_mod
    from services.auction import fetch_one_live_quote

    today = asof or datetime.now().strftime("%Y-%m-%d")
    actions = []
    holdings = store.holding_count()

    # 按适宜度：adj 升序，再 dip_depth 降序
    watches = [w for w in store.load_watches() if w.get("status") == WATCH_WATCHING]
    watches.sort(key=lambda w: (
        int(w.get("adj_day") or 99),
        -float(w.get("dip_depth") or 0),
        str(w.get("stock_code") or ""),
    ))

    for watch in watches:
        code = str(watch.get("stock_code") or "").zfill(6)
        lup = watch.get("limit_up_date")
        if not code or not lup:
            continue
        adj = mkt.adj_day_of(lup, today)
        store.update_watch(int(watch["id"]), {"adj_day": adj})

        if adj > 5:
            store.update_watch(int(watch["id"]), {
                "status": WATCH_EXPIRED,
                "expire_reason": "timeout_no_buy",
            })
            actions.append({"type": "expire", "code": code, "adj_day": adj})
            continue

        series = watch.get("ma_series") or {}
        snap = series.get(str(adj)) or {}
        price = _f(snap.get("close"))
        chg = _day_change_pct(code, today)
        high = None
        if use_live:
            live = fetch_one_live_quote(code)
            time_mod.sleep(AUCTION_SINGLE_SLEEP)
            if live:
                price = _f(live.get("price")) or price
                chg = live.get("pct") if live.get("pct") is not None else chg
                high = _f(live.get("high"))
                store.update_watch(int(watch["id"]), {
                    "last_price": price,
                    "in_dip_zone": mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), price),
                })
        elif price is None and use_close_as_ref:
            ohlc = mkt.get_ohlc(code, today)
            price = _f(ohlc.get("close")) if ohlc else None

        time_ok = True  # 盘中各档位均可评估；尾盘档更贴近 14:30 语义
        if slot == "tail":
            time_ok = True
        drop_ok = chg is not None and chg <= BUY_DROP_PCT
        in_zone = mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), price)

        if adj in BUILD_ADJ_DAYS and holdings < MAX_CONCURRENT_HOLDINGS:
            if (time_ok or drop_ok) and in_zone and price is not None:
                try:
                    pos = create_position_from_buy(
                        stock_name=watch.get("stock_name") or code,
                        stock_code=code,
                        buy_date=today,
                        buy_price=price,
                        limit_up_date=lup,
                        position_pct=FIRST_BUY_PCT,
                        watch_id=int(watch["id"]),
                        notes=f"auto_build adj={adj} chg={chg} slot={slot}",
                        enforce_adj_day=True,
                        require_watch=True,
                    )
                    holdings += 1
                    actions.append({"type": "buy", "code": code, "price": price, "pos_id": pos.get("id"), "slot": slot})
                except Exception as e:
                    actions.append({"type": "buy_skip", "code": code, "error": str(e)})

        if adj in ADD_ADJ_DAYS:
            for pos in store.load_positions():
                if pos.get("status") != POS_HOLDING:
                    continue
                if str(pos.get("stock_code")).zfill(6) != code:
                    continue
                if float(pos.get("position_pct") or 0) >= 99:
                    continue
                if not _is_add_candle_ok(snap):
                    actions.append({"type": "add_skip_red", "code": code, "msg": "持续红盘不加仓"})
                    continue
                if price is None:
                    continue
                try:
                    updated = add_to_position(int(pos["id"]), price, today, ADD_BUY_PCT)
                    actions.append({"type": "add", "code": code, "price": price, "pos_id": updated.get("id"), "slot": slot})
                except Exception as e:
                    actions.append({"type": "add_skip", "code": code, "error": str(e)})

    store.meta_set({"last_buy_check": today, "buy_actions": len(actions), "buy_slot": slot})
    return {"ok": True, "asof": today, "slot": slot, "actions": actions}


def check_sell_signals(
    asof: str | None = None,
    *,
    use_live: bool = False,
    slot: str = "morning",
) -> dict:
    import time as time_mod
    from services.auction import fetch_one_live_quote

    today = asof or datetime.now().strftime("%Y-%m-%d")
    actions = []
    for pos in store.load_positions():
        if pos.get("status") != POS_HOLDING:
            continue
        code = str(pos.get("stock_code") or "").zfill(6)
        buy = _f(pos.get("buy_price"))
        if not code or buy is None:
            continue

        last = high = open_px = None
        prev = None
        live = None
        ohlc = None
        if use_live:
            live = fetch_one_live_quote(code)
            time_mod.sleep(AUCTION_SINGLE_SLEEP)
            if live:
                last = _f(live.get("price"))
                high = _f(live.get("high")) or last
                open_px = _f(live.get("open"))
                prev = _f(live.get("pre_close"))
        else:
            ohlc = mkt.get_ohlc(code, today)
            if not ohlc:
                continue
            last = float(ohlc["close"])
            high = float(ohlc.get("high") or last)
            open_px = _f(ohlc.get("open"))
            try:
                import price_service as ps
                prev = ps.get_prev_close(code, today)
            except Exception:
                prev = None

        if last is None:
            continue

        series = pos.get("ma_series") or mkt.build_ma_series(code, pos.get("limit_up_date"), MAX_OBS_DAYS)
        adj = mkt.adj_day_of(pos.get("limit_up_date"), today)
        snap = series.get(str(min(max(adj, 1), MAX_OBS_DAYS))) or {}

        hold = mkt.hold_day_of(pos["buy_date"], today) if pos.get("buy_date") else 0
        store.update_position(int(pos["id"]), {
            "last_price": last,
            "hold_days": hold,
            "ma_series": series,
            "ma20": snap.get("ma20"),
            "ma10": snap.get("ma10"),
            "ma25": snap.get("ma25"),
            "ma30": snap.get("ma30"),
        })

        stop = _f(pos.get("stop_loss_price"))
        pnl = (last - buy) / buy * 100
        ma30 = _f(snap.get("ma30")) or _f(pos.get("ma30"))

        # ── 次日溢价退出（涨停收盘次日）：有溢价高开卖 / 无溢价也卖 ──
        if pos.get("await_limit_up_exit") and pos.get("limit_up_hold_date") and str(pos.get("limit_up_hold_date")) < today:
            exit_px = open_px if open_px is not None else last
            premium = None
            if prev and exit_px is not None:
                premium = (float(exit_px) - float(prev)) / float(prev) * 100
            reason = "limit_up_next_gap" if (premium is not None and premium > 0) else "limit_up_next_flat"
            mark_sell(int(pos["id"]), float(exit_px), today, reason, POS_SOLD)
            actions.append({
                "type": "limit_up_next_exit",
                "code": code,
                "price": exit_px,
                "premium": round(premium, 2) if premium is not None else None,
                "reason": reason,
                "slot": slot,
            })
            continue

        # ── 止损：跌破 30 日线 或 浮亏 ≤ -6%，两条件任一 ──
        below_ma30 = ma30 is not None and last < ma30
        hit_pct = pnl <= STOP_LOSS_MA_PCT
        hit_stop_price = stop is not None and last <= stop
        if below_ma30 or hit_pct or hit_stop_price:
            mark_sell(int(pos["id"]), last, today, "stop_loss", POS_STOPPED)
            actions.append({
                "type": "stop",
                "code": code,
                "price": last,
                "slot": slot,
                "pnl": round(pnl, 2),
                "ma30": ma30,
                "below_ma30": bool(below_ma30),
                "hit_pct": bool(hit_pct),
            })
            continue

        # ── 涨停收盘：当日不卖，挂次日溢价退出（盘中触及不卖，尾盘/收盘确认）──
        confirm_close = (not use_live) or slot == "tail"
        if confirm_close and prev is not None and _is_limit_up_price(code, last, prev):
            store.update_position(int(pos["id"]), {
                "await_limit_up_exit": True,
                "limit_up_hold_date": today,
                "last_price": last,
            })
            actions.append({
                "type": "limit_up_hold",
                "code": code,
                "price": last,
                "slot": slot,
                "msg": "涨停收盘不卖，次日看溢价",
            })
            continue

        # 第 7 日到期卖
        if hold >= HOLD_MAX_DAYS and (slot in ("tail", "morning", "midday") or not use_live):
            mark_sell(int(pos["id"]), last, today, "day7_close", POS_SOLD)
            actions.append({"type": "day7", "code": code, "price": last, "slot": slot})
            continue

    store.meta_set({"last_sell_check": today, "sell_actions": len(actions), "sell_slot": slot})
    return {"ok": True, "asof": today, "slot": slot, "actions": actions}
