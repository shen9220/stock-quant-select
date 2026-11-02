"""扫描：近 N 日涨停 → 涨停池；D3–D5 低吸区间 → 观察池。"""
from __future__ import annotations

from datetime import datetime

import store
from rules import (
    AUCTION_SINGLE_SLEEP,
    MAX_OBS_DAYS,
    MAX_PRICE,
    SCAN_RECENT_DAYS,
    WATCH_ADJ_DAYS,
    WATCH_EXPIRED,
    WATCH_MAX_ACTIVE,
    WATCH_WATCHING,
    ZT_ACTIVE,
    ZT_EXPIRED,
    ZT_WATCHED,
)
from services import market as mkt


def _ymd(d: str) -> str:
    return d.replace("-", "")


def _recent_trading_days(asof: str, n: int = SCAN_RECENT_DAYS) -> list[str]:
    """含 asof 往前共 n 个交易日（若 asof 非交易日则从上一交易日起）。"""
    days = []
    cur = asof
    # 若当天不是交易日或无涨停，仍从最近交易日回溯
    # 先取 asof 当天或往前找到交易日
    probe = cur
    for _ in range(10):
        # 用 prev 链：取 prev(next(probe)) 不稳定；直接用日历回退
        prev = mkt.prev_trading_day(probe)
        # 检查 probe 是否交易日：若 next(prev)==probe
        if prev:
            nxt = mkt.next_trading_day(prev)
            if nxt == probe:
                break
        probe = prev or probe
        if not prev:
            break
    # probe 为最近交易日或 asof
    # 简化：从 asof 连续 prev n 次，收集
    cur = asof
    # 若 asof 无数据，先退一天
    for _ in range(n + 5):
        if len(days) >= n:
            break
        # 尝试把 cur 当作交易日
        p = mkt.prev_trading_day(mkt.next_trading_day(cur) or cur)
        # 更稳：只用 prev 链
        if not days:
            # 起始：asof 的上一交易日作为「最近完整日」，同时尝试 asof
            d0 = cur
            d_prev = mkt.prev_trading_day(cur)
            # 优先放入 d_prev（通常昨日），再往前
            cursor = d_prev or d0
        else:
            cursor = mkt.prev_trading_day(days[-1])
        if not cursor:
            break
        if cursor not in days:
            days.append(cursor)
    return days[:n]


def recent_limit_up_dates(asof: str | None = None, n: int = SCAN_RECENT_DAYS) -> list[str]:
    today = asof or datetime.now().strftime("%Y-%m-%d")
    days: list[str] = []
    cur = mkt.prev_trading_day(today)  # 从上一交易日开始（通常更完整）
    # 若今天也是交易日且已收盘，可含今天；默认近 n 个完整日从昨日起
    while cur and len(days) < n:
        days.append(cur)
        cur = mkt.prev_trading_day(cur)
    return days  # 已是从近到远


def scan_one_day(limit_day: str, skip_soft_filters: bool = False) -> dict:
    """扫描单日涨停 → 写入涨停池（仅近10日首次涨停/一板）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    # 预拉近10日涨停池，用于严格「仅此一板」交叉校验
    mkt.preload_zt_pools_for_window(limit_day)
    df = mkt.fetch_zt_pool(_ymd(limit_day))
    added, skipped, rejected = [], [], []

    if df is None or df.empty:
        return {
            "ok": True,
            "limit_up_date": limit_day,
            "added": 0,
            "skipped": 0,
            "rejected": [],
            "message": f"{limit_day} 涨停池为空或拉取失败",
        }

    code_col = "代码" if "代码" in df.columns else df.columns[1]
    name_col = "名称" if "名称" in df.columns else df.columns[2]
    price_col = "最新价" if "最新价" in df.columns else None
    industry_col = "所属行业" if "所属行业" in df.columns else None
    board_col = "连板数" if "连板数" in df.columns else None

    for _, row in df.iterrows():
        code = str(row[code_col]).zfill(6)
        name = str(row[name_col])
        reason = _reject_reason(
            code, name, row, price_col, limit_day, skip_soft_filters, board_col=board_col
        )
        if reason:
            rejected.append({"code": code, "name": name, "reason": reason})
            continue

        exists = any(
            str(x.get("stock_code")).zfill(6) == code and x.get("limit_up_date") == limit_day
            for x in store.load_limitups()
        )
        if exists:
            skipped.append(code)
            continue

        price = None
        if price_col is not None:
            try:
                price = float(row[price_col])
            except (TypeError, ValueError):
                price = None

        industry = None
        if industry_col:
            try:
                industry = row[industry_col]
            except Exception:
                industry = None

        t1 = mkt.trading_days_after(limit_day, 1)
        t1_open = None
        # 轻量入库：均线系列稍后「刷新均线」补全，避免扫描卡死
        try:
            lb = int(float(row[board_col])) if board_col else None
        except (TypeError, ValueError):
            lb = None
        rec = {
            "stock_code": code,
            "stock_name": name,
            "limit_up_date": limit_day,
            "concept_board": None,
            "industry_board": industry,
            "t1_open": t1_open,
            "ma_series": {},
            "view_adj_day": 1,
            "ma10": None,
            "ma20": None,
            "ma25": None,
            "ma30": None,
            "last_price": price,
            "adj_day": mkt.adj_day_of(limit_day, today),
            "in_dip_zone": False,
            "status": ZT_ACTIVE,
            "entered_at": today,
            "pe_ttm": None,
            "list_date": None,
            "filter_flags": ["ma_pending"],
            "notes": "",
            "pool": "limitup",
            "board_count": lb,
        }
        # 板块 / 基础资料（失败不阻断入池）
        try:
            basic = mkt.fetch_basic_filters(code)
            rec["pe_ttm"] = basic.get("pe_ttm")
            rec["list_date"] = basic.get("list_date")
            rec["concept_board"] = basic.get("concept_board")
            rec["industry_board"] = industry or basic.get("industry_board")
            gaps = list(basic.get("data_gap") or [])
            gaps.append("ma_pending")
            rec["filter_flags"] = gaps
        except Exception:
            pass
        if t1:
            try:
                ohlc = mkt.get_ohlc(code, t1)
                if ohlc:
                    rec["t1_open"] = ohlc.get("open")
            except Exception:
                pass
        store.upsert_limitup(rec)
        added.append({"code": code, "name": name, "board_count": lb})

    return {
        "ok": True,
        "limit_up_date": limit_day,
        "added": len(added),
        "skipped": len(skipped),
        "rejected": rejected[:30],
        "items": added,
    }


def scan_for_date(asof: str | None = None, skip_soft_filters: bool = False) -> dict:
    """兼容旧接口：扫描上一交易日。"""
    today = asof or datetime.now().strftime("%Y-%m-%d")
    yesterday = mkt.prev_trading_day(today)
    if not yesterday:
        return {"ok": False, "error": "无法解析上一交易日", "added": 0}
    r = scan_one_day(yesterday, skip_soft_filters=skip_soft_filters)
    store.meta_set({
        "last_scan": today,
        "last_scan_limit_day": yesterday,
        "scan_added": r.get("added", 0),
    })
    return r


def scan_recent_days(
    asof: str | None = None,
    n: int = SCAN_RECENT_DAYS,
    skip_soft_filters: bool = False,
    *,
    auto_promote: bool = False,
) -> dict:
    """拉取近 n 个交易日涨停 → 涨停池。观察池晋升默认交给后续 watch 阶段（避免重复/抢跑）。"""
    today = asof or datetime.now().strftime("%Y-%m-%d")
    days = recent_limit_up_dates(today, n)
    results = []
    total_added = 0
    for d in days:
        print(f"[scan] limit-up day {d} …")
        r = scan_one_day(d, skip_soft_filters=skip_soft_filters)
        results.append(r)
        total_added += int(r.get("added") or 0)
        print(f"[scan] {d} added={r.get('added')} rejected={len(r.get('rejected') or [])}")
    promo = None
    if auto_promote:
        print("[scan] promote watch …")
        promo = promote_ready_to_watch(asof=today, use_live=False)
        print(f"[scan] promoted={promo.get('promoted')}")
    store.meta_set({
        "last_scan": today,
        "last_scan_days": days,
        "scan_added": total_added,
        "last_promote": (promo or {}).get("promoted", 0),
    })
    return {
        "ok": True,
        "asof": today,
        "days": days,
        "added": total_added,
        "day_results": results,
        "promote": promo,
    }


def _reject_reason(
    code, name, row, price_col, limit_day, skip_soft, board_col=None
) -> str | None:
    if not mkt.is_main_board(code):
        return "非主板"
    if mkt.is_st_name(name):
        return "ST"
    price = None
    if price_col is not None:
        try:
            price = float(row[price_col])
        except (TypeError, ValueError):
            price = None
    if price is not None and price > MAX_PRICE:
        return f"股价>{MAX_PRICE}"

    # 东财连板数：必须为首板（=1）；二板及以上直接剔除
    if board_col is not None:
        try:
            lb = int(float(row[board_col]))
            if lb != 1:
                return f"连板数={lb}（非首板）"
        except (TypeError, ValueError):
            return "连板数缺失"

    # 近10个交易日仅此一板（涨停池交叉；无法确认则拒）
    if not mkt.first_limit_up_in_window(code, limit_day, strict=True):
        return "非近10日首次涨停"

    # 一字板：炸板>0 必非一字；否则再查 OHLC（失败则不误杀）
    try:
        zha = int(float(row["炸板次数"])) if "炸板次数" in row.index else None
    except (TypeError, ValueError):
        zha = None
    if zha == 0:
        yizi = mkt.is_yizi_limit_up(code, limit_day)
        if yizi is True:
            return "一字涨停"
    # zha>0 → 非一字；zha 缺失则保守再查
    if zha is None:
        yizi = mkt.is_yizi_limit_up(code, limit_day)
        if yizi is True:
            return "一字涨停"

    if skip_soft:
        return None
    basic = mkt.fetch_basic_filters(code)
    if basic.get("list_years_ok") is False:
        return "上市未满5年"
    if basic.get("pe_ok") is False:
        return f"PE不合规:{basic.get('pe_ttm')}"
    if basic.get("dividend_ok") is False:
        return "分红条件不满足"
    return None


def _refresh_limitup_ma(code: str, limit_up_date: str) -> None:
    for x in store.load_limitups():
        if str(x.get("stock_code")).zfill(6) == code and x.get("limit_up_date") == limit_up_date:
            _apply_ma_patch_limitup(x)
            break


def _apply_ma_patch_limitup(x: dict) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    code = str(x.get("stock_code") or "").zfill(6)
    lup = x.get("limit_up_date")
    if not code or not lup or x.get("ma_locked"):
        return
    series = mkt.build_ma_series(code, lup, MAX_OBS_DAYS)
    adj = mkt.adj_day_of(lup, today)
    vd = int(x.get("view_adj_day") or min(max(adj, 1), MAX_OBS_DAYS) or 1)
    vd = min(max(vd, 1), MAX_OBS_DAYS)
    snap = series.get(str(vd)) or {}
    patch = {
        "ma_series": series,
        "adj_day": adj,
        "view_adj_day": vd,
        "ma10": snap.get("ma10"),
        "ma20": snap.get("ma20"),
        "ma25": snap.get("ma25"),
        "ma30": snap.get("ma30"),
    }
    if snap.get("close") is not None:
        patch["last_price"] = snap["close"]
        patch["in_dip_zone"] = mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), snap["close"])
    if x.get("t1_open") in (None, ""):
        t1 = mkt.trading_days_after(lup, 1)
        if t1:
            ohlc = mkt.get_ohlc(code, t1)
            if ohlc:
                patch["t1_open"] = ohlc.get("open")
    store.update_limitup(int(x["id"]), patch)


def refresh_all_ma(asof: str | None = None) -> dict:
    today = asof or datetime.now().strftime("%Y-%m-%d")
    n_zt = n_w = n_pos = 0
    for x in store.load_limitups():
        if x.get("ma_locked"):
            continue
        _apply_ma_patch_limitup(x)
        n_zt += 1
    n_w, n_pos = _refresh_watch_pos_ma(today)
    store.meta_set({"last_ma_refresh": today, "ma_zt": n_zt, "ma_watch": n_w, "ma_pos": n_pos})
    return {"ok": True, "limitup": n_zt, "watch": n_w, "position": n_pos, "asof": today}


def _refresh_watch_pos_ma(today: str) -> tuple[int, int]:
    n_w = n_pos = 0
    for w in store.load_watches():
        code = str(w.get("stock_code") or "").zfill(6)
        lup = w.get("limit_up_date")
        if not code or not lup or w.get("ma_locked"):
            continue
        series = mkt.build_ma_series(code, lup, MAX_OBS_DAYS)
        adj = mkt.adj_day_of(lup, today)
        vd = min(max(adj, 1), MAX_OBS_DAYS) if adj else int(w.get("view_adj_day") or 1)
        snap = series.get(str(vd)) or {}
        patch = {
            "ma_series": series,
            "adj_day": adj,
            "view_adj_day": vd,
            "ma10": snap.get("ma10"),
            "ma20": snap.get("ma20"),
            "ma25": snap.get("ma25"),
            "ma30": snap.get("ma30"),
        }
        if snap.get("close") is not None:
            patch["last_price"] = snap["close"]
            patch["in_dip_zone"] = mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), snap["close"])
        store.update_watch(int(w["id"]), patch)
        n_w += 1
    for p in store.load_positions():
        code = str(p.get("stock_code") or "").zfill(6)
        lup = p.get("limit_up_date")
        if not code or not lup or p.get("ma_locked"):
            continue
        series = mkt.build_ma_series(code, lup, MAX_OBS_DAYS)
        adj = mkt.adj_day_of(lup, today)
        vd = min(max(adj, 1), MAX_OBS_DAYS) if adj else 1
        snap = series.get(str(vd)) or {}
        patch = {
            "ma_series": series,
            "adj_day": adj,
            "view_adj_day": vd,
            "ma10": snap.get("ma10"),
            "ma20": snap.get("ma20"),
            "ma25": snap.get("ma25"),
            "ma30": snap.get("ma30"),
        }
        if snap.get("close") is not None:
            patch["last_price"] = snap["close"]
            buy = p.get("buy_price")
            if buy not in (None, "") and p.get("status") == "holding":
                pr = (float(snap["close"]) - float(buy)) / float(buy) * 100
                patch["profit_rate"] = round(pr, 2)
                patch["is_profit"] = pr > 0
            if p.get("buy_date") and p.get("status") == "holding":
                patch["hold_days"] = mkt.hold_day_of(p["buy_date"], today)
        store.update_position(int(p["id"]), patch)
        n_pos += 1
    return n_w, n_pos


def refresh_morning_ma(asof: str | None = None) -> dict:
    """
    9:40 日更：观察池+持有池全量刷均线；
    涨停池只刷仍在池且调整日在观察窗内的（晋升前已对 D3–D5 补过）。
    """
    today = asof or datetime.now().strftime("%Y-%m-%d")
    n_zt = 0
    for x in store.load_limitups():
        if x.get("ma_locked"):
            continue
        if x.get("status") not in (ZT_ACTIVE, None, ""):
            continue
        lup = x.get("limit_up_date")
        adj = mkt.adj_day_of(lup, today) if lup else None
        if adj is None or adj < 1 or adj > MAX_OBS_DAYS:
            continue
        # D3–D5 在 promote 时已刷；此处补 D1–D2 / D6–D14 展示用，但限制数量防卡死
        if adj not in WATCH_ADJ_DAYS and adj > 5:
            continue
        _apply_ma_patch_limitup(x)
        n_zt += 1
    n_w, n_pos = _refresh_watch_pos_ma(today)
    store.meta_set({"last_ma_refresh": today, "ma_zt": n_zt, "ma_watch": n_w, "ma_pos": n_pos})
    return {"ok": True, "limitup": n_zt, "watch": n_w, "position": n_pos, "asof": today}


def eligible_for_watch(rec: dict, asof: str | None = None) -> tuple[bool, str]:
    """D3–D5 且处于 10–20 日线之间 → 可入观察池。"""
    today = asof or datetime.now().strftime("%Y-%m-%d")
    lup = rec.get("limit_up_date")
    if not lup:
        return False, "无涨停日"
    adj = mkt.adj_day_of(lup, today)
    if adj not in WATCH_ADJ_DAYS:
        return False, f"调整第{adj}天，需D3–D5"
    series = rec.get("ma_series") or {}
    snap = series.get(str(adj)) or {}
    price = snap.get("close") or rec.get("last_price")
    ma10 = snap.get("ma10") if snap else rec.get("ma10")
    ma20 = snap.get("ma20") if snap else rec.get("ma20")
    if not mkt.in_dip_zone(ma10, ma20, price):
        return False, "未进入10–20日线低吸区间"
    return True, "ok"


def promote_to_watch(limitup_id: int, force: bool = False, source: str = "manual") -> dict:
    """从涨停池加入观察池。force=True 跳过 D3–D5/区间校验（手工）。"""
    zt = store.get_limitup(limitup_id)
    if not zt:
        raise ValueError("涨停池记录不存在")
    code = str(zt.get("stock_code") or "").zfill(6)
    lup = zt.get("limit_up_date")
    existing = store.find_watch(code, lup)
    if existing:
        store.update_limitup(limitup_id, {"status": ZT_WATCHED})
        return existing

    if not force:
        ok, reason = eligible_for_watch(zt)
        if not ok:
            raise ValueError(f"尚未达到观察池条件：{reason}")

    today = datetime.now().strftime("%Y-%m-%d")
    is_auto = source == "auto"
    watch = {k: v for k, v in zt.items() if k != "id"}
    watch.update({
        "status": WATCH_WATCHING,
        "watched_at": today,
        "watch_source": source,
        "is_auto_added": is_auto,  # True=扫描自动入观察池；False=手工加入
        "limitup_id": limitup_id,
        "pool": "watch",
    })
    saved = store.upsert_watch(watch)
    store.update_limitup(limitup_id, {"status": ZT_WATCHED})
    return saved


def _watch_suitability(adj: int, ma10, ma20, price) -> tuple[float, float]:
    """
    返回 (rank_key越小越好, depth)。
    depth: 0=贴MA10，1=贴MA20；低吸更深（靠近MA20）更优。
    """
    depth = 0.5
    try:
        lo, hi = float(ma20), float(ma10)
        px = float(price)
        if hi > lo:
            depth = max(0.0, min(1.0, (hi - px) / (hi - lo)))
    except (TypeError, ValueError):
        pass
    # D3 优先，其次更深低吸
    adj_pen = {3: 0.0, 4: 1.0, 5: 2.0}.get(int(adj or 99), 9.0)
    rank = adj_pen * 10 - depth  # depth 大 → rank 小
    return rank, depth


def promote_ready_to_watch(
    asof: str | None = None,
    *,
    use_live: bool = True,
    max_promote: int | None = None,
) -> dict:
    """
    每日观察池更新（涨停池跑完后执行）：
    - 扫描涨停池 D3–D5 + 低吸区间，按适宜度排序，可同时入选多只；
    - 刷新已在观察中的均线/现价；超窗 expire。
    """
    import time as time_mod
    from services.auction import fetch_one_live_quote

    today = asof or datetime.now().strftime("%Y-%m-%d")
    cap = max_promote if max_promote is not None else WATCH_MAX_ACTIVE
    promoted, refreshed, skipped, expired = [], [], [], []

    # ① 刷新/过期已有观察中
    for w in store.load_watches():
        if w.get("status") != WATCH_WATCHING:
            continue
        code = str(w.get("stock_code") or "").zfill(6)
        lup = w.get("limit_up_date")
        adj = mkt.adj_day_of(lup, today) if lup else None
        if adj is None or adj > max(WATCH_ADJ_DAYS):
            store.update_watch(int(w["id"]), {
                "status": WATCH_EXPIRED,
                "expire_reason": "timeout_watch_window",
                "adj_day": adj,
            })
            expired.append({"code": code, "adj": adj})
            continue
        series = w.get("ma_series") or mkt.build_ma_series(code, lup, MAX_OBS_DAYS)
        snap = series.get(str(adj)) or {}
        px = snap.get("close")
        if use_live:
            live = fetch_one_live_quote(code)
            time_mod.sleep(AUCTION_SINGLE_SLEEP)
            if live and live.get("price"):
                px = live["price"]
        in_zone = mkt.in_dip_zone(snap.get("ma10") or w.get("ma10"), snap.get("ma20") or w.get("ma20"), px)
        store.update_watch(int(w["id"]), {
            "ma_series": series,
            "adj_day": adj,
            "view_adj_day": adj,
            "ma10": snap.get("ma10"),
            "ma20": snap.get("ma20"),
            "ma25": snap.get("ma25"),
            "ma30": snap.get("ma30"),
            "last_price": px,
            "in_dip_zone": in_zone,
            "last_watch_refresh": today,
        })
        refreshed.append({"code": code, "adj": adj, "in_dip_zone": in_zone})

    # ② 从涨停池收集候选并按适宜度排序
    active_watch = sum(1 for x in store.load_watches() if x.get("status") == WATCH_WATCHING)
    room = max(0, cap - active_watch)
    cands = []
    for zt in store.load_limitups():
        if zt.get("status") in (ZT_WATCHED, ZT_EXPIRED):
            # 已转入观察过的跳过；若观察已 expire 且仍在 D3–D5 可再入 —— 仅当无同 code+lup watching/bought
            if zt.get("status") == ZT_WATCHED:
                code0 = str(zt.get("stock_code") or "").zfill(6)
                lup0 = zt.get("limit_up_date")
                if store.find_watch(code0, lup0):
                    continue
        lup = zt.get("limit_up_date")
        adj = mkt.adj_day_of(lup, today) if lup else None
        if adj is None or adj < min(WATCH_ADJ_DAYS):
            skipped.append({"code": zt.get("stock_code"), "reason": f"调整第{adj}天，需D3–D5"})
            continue
        if adj > max(WATCH_ADJ_DAYS):
            skipped.append({"code": zt.get("stock_code"), "reason": f"调整第{adj}天，需D3–D5"})
            if zt.get("status") == ZT_ACTIVE:
                store.update_limitup(int(zt["id"]), {"status": ZT_EXPIRED, "expire_reason": "missed_watch_window"})
            continue
        _apply_ma_patch_limitup(zt)
        zt = store.get_limitup(int(zt["id"])) or zt
        series = zt.get("ma_series") or {}
        snap = series.get(str(adj)) or {}
        ma10 = snap.get("ma10") or zt.get("ma10")
        ma20 = snap.get("ma20") or zt.get("ma20")
        px = snap.get("close") or zt.get("last_price")
        if use_live:
            live = fetch_one_live_quote(str(zt.get("stock_code")).zfill(6))
            time_mod.sleep(AUCTION_SINGLE_SLEEP)
            if live and live.get("price"):
                px = live["price"]
        if not mkt.in_dip_zone(ma10, ma20, px):
            skipped.append({"code": zt.get("stock_code"), "reason": "未进入10–20日线低吸区间"})
            continue
        rank, depth = _watch_suitability(adj, ma10, ma20, px)
        cands.append({
            "zt": zt,
            "code": str(zt.get("stock_code")).zfill(6),
            "name": zt.get("stock_name"),
            "adj": adj,
            "price": px,
            "rank": rank,
            "depth": depth,
            "ma10": ma10,
            "ma20": ma20,
        })

    cands.sort(key=lambda x: (x["rank"], x["code"]))
    print(f"[watch] 适宜候选 {len(cands)} 只，剩余名额 {room}/{cap}")
    for c in cands:
        if len(promoted) >= room:
            skipped.append({"code": c["code"], "reason": f"观察池已满{cap}"})
            continue
        try:
            w = promote_to_watch(int(c["zt"]["id"]), force=True, source="auto")
            store.update_watch(int(w["id"]), {
                "last_price": c["price"],
                "in_dip_zone": True,
                "adj_day": c["adj"],
                "view_adj_day": c["adj"],
                "ma10": c["ma10"],
                "ma20": c["ma20"],
                "watch_rank": round(c["rank"], 3),
                "dip_depth": round(c["depth"], 3),
                "is_auto_added": True,
                "notes": f"auto rank={c['rank']:.2f} depth={c['depth']:.2f} adj={c['adj']}",
            })
            promoted.append({
                "code": c["code"], "name": c["name"], "id": w.get("id"),
                "adj": c["adj"], "depth": round(c["depth"], 3), "price": c["price"],
            })
            print(f"  +观察 {c['code']} {c['name']} adj{c['adj']} px={c['price']} depth={c['depth']:.2f}")
        except Exception as e:
            skipped.append({"code": c["code"], "reason": str(e)})

    return {
        "ok": True,
        "asof": today,
        "promoted": len(promoted),
        "items": promoted,
        "refreshed": len(refreshed),
        "expired": len(expired),
        "candidates": len(cands),
        "skipped": skipped[:50],
    }


def apply_view_day(oid: int, view_day: int, pool: str = "limitup") -> dict | None:
    view_day = min(max(int(view_day), 1), MAX_OBS_DAYS)
    if pool == "watch":
        rec = store.get_watch(oid)
        updater = store.update_watch
    else:
        rec = store.get_limitup(oid)
        updater = store.update_limitup
    if not rec:
        return None
    series = rec.get("ma_series") or {}
    snap = series.get(str(view_day)) or {}
    patch = {
        "view_adj_day": view_day,
        "ma10": snap.get("ma10"),
        "ma20": snap.get("ma20"),
        "ma25": snap.get("ma25"),
        "ma30": snap.get("ma30"),
    }
    if snap.get("close") is not None:
        patch["last_price"] = snap["close"]
        patch["in_dip_zone"] = mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), snap["close"])
    return updater(oid, patch)


def enrich_limitup_manual(stock_name: str, limit_up_date: str, stock_code: str = "") -> dict:
    code, name = mkt.resolve_code_name(name=stock_name, code=stock_code)
    if not code:
        raise ValueError(f"无法解析股票：{stock_name or stock_code}")
    if not mkt.is_main_board(code):
        raise ValueError("仅支持主板")
    basic = mkt.fetch_basic_filters(code)
    boards = mkt.fetch_boards(code)
    ma_series = mkt.build_ma_series(code, limit_up_date, MAX_OBS_DAYS)
    today = datetime.now().strftime("%Y-%m-%d")
    adj = mkt.adj_day_of(limit_up_date, today)
    vd = 1
    snap = ma_series.get("1") or {}
    t1 = mkt.trading_days_after(limit_up_date, 1)
    t1_open = None
    if t1:
        ohlc = mkt.get_ohlc(code, t1)
        if ohlc:
            t1_open = ohlc.get("open")
    rec = {
        "stock_code": code,
        "stock_name": name or stock_name,
        "limit_up_date": limit_up_date,
        "concept_board": basic.get("concept_board") or boards.get("concept_board"),
        "industry_board": basic.get("industry_board") or boards.get("industry_board"),
        "t1_open": t1_open,
        "ma_series": ma_series,
        "view_adj_day": vd,
        "ma10": snap.get("ma10"),
        "ma20": snap.get("ma20"),
        "ma25": snap.get("ma25"),
        "ma30": snap.get("ma30"),
        "last_price": snap.get("close"),
        "adj_day": adj,
        "in_dip_zone": mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), snap.get("close")),
        "status": ZT_ACTIVE,
        "entered_at": today,
        "notes": "",
        "pool": "limitup",
        "filter_flags": basic.get("data_gap") or [],
    }
    return store.upsert_limitup(rec)


# 兼容旧名
enrich_observation_manual = enrich_limitup_manual


def refresh_all_boards() -> dict:
    n = 0
    mkt._BASIC_CACHE.clear()
    for x in store.load_limitups():
        code = str(x.get("stock_code") or "").zfill(6)
        if not code:
            continue
        boards = mkt.fetch_boards(code)
        patch = {}
        if boards.get("industry_board"):
            patch["industry_board"] = boards["industry_board"]
        if boards.get("concept_board"):
            patch["concept_board"] = boards["concept_board"]
        if patch:
            store.update_limitup(int(x["id"]), patch)
            n += 1
    for w in store.load_watches():
        code = str(w.get("stock_code") or "").zfill(6)
        if not code:
            continue
        boards = mkt.fetch_boards(code)
        patch = {}
        if boards.get("industry_board"):
            patch["industry_board"] = boards["industry_board"]
        if boards.get("concept_board"):
            patch["concept_board"] = boards["concept_board"]
        if patch:
            store.update_watch(int(w["id"]), patch)
    for p in store.load_positions():
        code = str(p.get("stock_code") or "").zfill(6)
        if not code:
            continue
        boards = mkt.fetch_boards(code)
        patch = {}
        if boards.get("industry_board"):
            patch["industry_board"] = boards["industry_board"]
        if boards.get("concept_board"):
            patch["concept_board"] = boards["concept_board"]
        if patch:
            store.update_position(int(p["id"]), patch)
    return {"ok": True, "updated": n}
