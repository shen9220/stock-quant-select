"""V2 扫描：涨停池 → 观察池（写入 data_v2，不碰 V1）。"""
from __future__ import annotations

from datetime import datetime

import rules_v2 as R
import store as store_mod
from services import market as mkt
from services import scanner as v1_scanner
from v2 import quality as Q

PROFILE = "v2"


def st():
    return store_mod.for_profile(PROFILE)


def scan_recent_days(
    asof: str | None = None,
    n: int | None = None,
    skip_soft_filters: bool = False,
) -> dict:
    """复用 V1 单日扫描逻辑，结果写入 V2 库。"""
    today = asof or datetime.now().strftime("%Y-%m-%d")
    n = n if n is not None else R.MORNING_SCAN_DAYS
    days = v1_scanner.recent_limit_up_dates(asof=today, n=n)
    added = 0
    details = []
    s = st()
    for d in days:
        # 用 V1 扫描函数扫到内存逻辑：直接调 scan_one_day 会写入 V1。
        # 改为本地复制单日写入 V2。
        res = _scan_one_day_v2(d, skip_soft_filters=skip_soft_filters)
        added += int(res.get("added") or 0)
        details.append(res)
    s.meta_set({"last_v2_scan": today, "v2_scan_added": added, "profile": "v2"})
    return {"ok": True, "asof": today, "days": days, "added": added, "details": details, "profile": "v2"}


def _scan_one_day_v2(limit_day: str, skip_soft_filters: bool = False) -> dict:
    s = st()
    mkt.preload_zt_pools_for_window(limit_day)
    df = mkt.fetch_zt_pool(limit_day.replace("-", ""))
    added, skipped, rejected = [], [], []
    if df is None or df.empty:
        return {"ok": True, "limit_up_date": limit_day, "added": 0, "message": "空池"}

    code_col = "代码" if "代码" in df.columns else df.columns[1]
    name_col = "名称" if "名称" in df.columns else df.columns[2]
    price_col = "最新价" if "最新价" in df.columns else None
    industry_col = "所属行业" if "所属行业" in df.columns else None
    board_col = "连板数" if "连板数" in df.columns else None

    for _, row in df.iterrows():
        code = str(row[code_col]).zfill(6)
        name = str(row[name_col])
        reason = v1_scanner._reject_reason(
            code, name, row, price_col, limit_day, skip_soft_filters, board_col=board_col
        )
        if reason:
            rejected.append({"code": code, "reason": reason})
            continue
        exists = any(
            str(x.get("stock_code")).zfill(6) == code and x.get("limit_up_date") == limit_day
            for x in s.load_limitups()
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
        try:
            lb = int(float(row[board_col])) if board_col else None
        except (TypeError, ValueError):
            lb = None
        score = Q.simple_leader_score(
            {"last_price": price, "industry_board": industry, "炸板次数": row["炸板次数"] if "炸板次数" in row.index else None},
            code,
            limit_day,
        )
        rec = {
            "stock_code": code,
            "stock_name": name,
            "limit_up_date": limit_day,
            "industry_board": industry,
            "concept_board": None,
            "last_price": price,
            "limit_board": lb,
            "status": R.ZT_ACTIVE,
            "strategy_version": R.STRATEGY_VERSION,
            "experiment_id": R.EXPERIMENT_ID,
            "leader_score": score["leader_score"],
            "score_confidence": score["score_confidence"],
            "score_parts": score["score_parts"],
            "data_incomplete": score["data_incomplete"],
            "profile": "v2",
        }
        s.upsert_limitup(rec)
        added.append(code)
    return {
        "ok": True,
        "limit_up_date": limit_day,
        "added": len(added),
        "skipped": len(skipped),
        "rejected": len(rejected),
    }


def promote_ready_to_watch(asof: str | None = None, use_live: bool = False) -> dict:
    """
    D3–D5 + 低吸区间进观察；附加缩量/结构标签。
    不因到日强制买；买入另见 signals。
    """
    today = asof or datetime.now().strftime("%Y-%m-%d")
    s = st()
    promoted, skipped, expired, refreshed = [], [], [], []
    # 刷新已有
    for w in s.load_watches():
        if w.get("status") != R.WATCH_WATCHING:
            continue
        code = str(w.get("stock_code") or "").zfill(6)
        lup = w.get("limit_up_date")
        adj = mkt.adj_day_of(lup, today) if lup else None
        if adj is None or adj > max(R.WATCH_ADJ_DAYS):
            s.update_watch(int(w["id"]), {"status": R.WATCH_EXPIRED, "expire_reason": "timeout_watch_window", "adj_day": adj})
            expired.append(code)
            continue
        series = w.get("ma_series") or mkt.build_ma_series(code, lup, R.MAX_OBS_DAYS)
        snap = series.get(str(adj)) or {}
        px = snap.get("close")
        in_zone = mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), px)
        s.update_watch(int(w["id"]), {
            "ma_series": series, "adj_day": adj, "view_adj_day": adj,
            "ma10": snap.get("ma10"), "ma20": snap.get("ma20"),
            "ma25": snap.get("ma25"), "ma30": snap.get("ma30"),
            "last_price": px, "in_dip_zone": in_zone,
        })
        refreshed.append(code)

    active = sum(1 for x in s.load_watches() if x.get("status") == R.WATCH_WATCHING)
    room = max(0, R.WATCH_MAX_ACTIVE - active)
    cands = []
    for zt in s.load_limitups():
        if zt.get("status") == R.ZT_WATCHED and s.find_watch(str(zt.get("stock_code")), zt.get("limit_up_date")):
            continue
        lup = zt.get("limit_up_date")
        code = str(zt.get("stock_code") or "").zfill(6)
        adj = mkt.adj_day_of(lup, today) if lup else None
        if adj is None or adj < min(R.WATCH_ADJ_DAYS):
            skipped.append({"code": code, "reason": f"需D3–D5现D{adj}"})
            continue
        if adj > max(R.WATCH_ADJ_DAYS):
            if zt.get("status") == R.ZT_ACTIVE:
                s.update_limitup(int(zt["id"]), {"status": R.ZT_EXPIRED, "expire_reason": "missed_watch_window"})
            continue
        series = mkt.build_ma_series(code, lup, R.MAX_OBS_DAYS)
        snap = series.get(str(adj)) or {}
        ma10, ma20, px = snap.get("ma10"), snap.get("ma20"), snap.get("close")
        if not mkt.in_dip_zone(ma10, ma20, px):
            skipped.append({"code": code, "reason": "未进低吸观察区"})
            continue
        # 位置条件满足即可入观察；缩量/确认在买入阶段再卡
        volq = Q.check_pullback_volume(code, lup, today)
        struct = Q.structure_intact(code, lup, today, px)
        depth = 0.5
        try:
            if ma10 and ma20 and float(ma10) > float(ma20):
                depth = max(0.0, min(1.0, (float(ma10) - float(px)) / (float(ma10) - float(ma20))))
        except (TypeError, ValueError):
            pass
        # D4 优先于 D5（GPT：D4 主窗）；D3 可观察但排序靠后
        adj_pen = {3: 2.0, 4: 0.0, 5: 1.0}.get(int(adj), 9.0)
        rank = adj_pen * 10 - depth
        cands.append({
            "zt": zt, "code": code, "name": zt.get("stock_name"), "adj": adj,
            "rank": rank, "depth": depth, "px": px, "ma10": ma10, "ma20": ma20,
            "series": series, "volq": volq, "struct": struct,
            "leader_score": zt.get("leader_score"),
        })
    cands.sort(key=lambda x: (x["rank"], -(x.get("leader_score") or 0), x["code"]))
    for c in cands:
        if len(promoted) >= room:
            break
        zt = c["zt"]
        watch = {k: v for k, v in zt.items() if k != "id"}
        watch.update({
            "status": R.WATCH_WATCHING,
            "watched_at": today,
            "watch_source": "auto",
            "is_auto_added": True,
            "limitup_id": zt.get("id"),
            "adj_day": c["adj"],
            "view_adj_day": c["adj"],
            "ma_series": c["series"],
            "ma10": c["ma10"], "ma20": c["ma20"],
            "last_price": c["px"],
            "in_dip_zone": True,
            "dip_depth": round(c["depth"], 3),
            "vol_quality": c["volq"],
            "structure": c["struct"],
            "strategy_version": R.STRATEGY_VERSION,
            "experiment_id": R.EXPERIMENT_ID,
            "profile": "v2",
        })
        saved = s.upsert_watch(watch)
        s.update_limitup(int(zt["id"]), {"status": R.ZT_WATCHED})
        promoted.append({"code": c["code"], "name": c["name"], "adj": c["adj"], "id": saved.get("id")})

    return {
        "ok": True, "asof": today, "promoted": len(promoted), "items": promoted,
        "refreshed": len(refreshed), "expired": len(expired),
        "candidates": len(cands), "skipped": skipped[:40], "profile": "v2",
    }


def refresh_all_ma(asof: str | None = None) -> dict:
    today = asof or datetime.now().strftime("%Y-%m-%d")
    s = st()
    n = 0
    for pool_name, loader, updater in (
        ("zt", s.load_limitups, s.update_limitup),
        ("watch", s.load_watches, s.update_watch),
        ("pos", s.load_positions, s.update_position),
    ):
        for x in loader():
            code = str(x.get("stock_code") or "").zfill(6)
            lup = x.get("limit_up_date")
            if not code or not lup or x.get("ma_locked"):
                continue
            series = mkt.build_ma_series(code, lup, R.MAX_OBS_DAYS)
            adj = mkt.adj_day_of(lup, today)
            vd = min(max(adj or 1, 1), R.MAX_OBS_DAYS)
            snap = series.get(str(vd)) or {}
            patch = {
                "ma_series": series, "adj_day": adj, "view_adj_day": vd,
                "ma10": snap.get("ma10"), "ma20": snap.get("ma20"),
                "ma25": snap.get("ma25"), "ma30": snap.get("ma30"),
            }
            if snap.get("close") is not None:
                patch["last_price"] = snap["close"]
                patch["in_dip_zone"] = mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), snap["close"])
            updater(int(x["id"]), patch)
            n += 1
    s.meta_set({"last_v2_ma_refresh": today, "ma_touched": n})
    return {"ok": True, "touched": n, "asof": today, "profile": "v2"}


def apply_view_day(oid: int, view_day: int, pool: str = "limitup") -> dict | None:
    s = st()
    view_day = min(max(int(view_day), 1), R.MAX_OBS_DAYS)
    if pool == "watch":
        rec, updater = s.get_watch(oid), s.update_watch
    else:
        rec, updater = s.get_limitup(oid), s.update_limitup
    if not rec:
        return None
    series = rec.get("ma_series") or {}
    snap = series.get(str(view_day)) or {}
    patch = {
        "view_adj_day": view_day,
        "ma10": snap.get("ma10"), "ma20": snap.get("ma20"),
        "ma25": snap.get("ma25"), "ma30": snap.get("ma30"),
    }
    if snap.get("close") is not None:
        patch["last_price"] = snap["close"]
        patch["in_dip_zone"] = mkt.in_dip_zone(snap.get("ma10"), snap.get("ma20"), snap["close"])
    return updater(oid, patch)


def promote_to_watch(limitup_id: int, force: bool = False, source: str = "manual") -> dict:
    s = st()
    zt = s.get_limitup(limitup_id)
    if not zt:
        raise ValueError("涨停池记录不存在")
    code = str(zt.get("stock_code") or "").zfill(6)
    lup = zt.get("limit_up_date")
    existing = s.find_watch(code, lup)
    if existing:
        s.update_limitup(limitup_id, {"status": R.ZT_WATCHED})
        return existing
    today = datetime.now().strftime("%Y-%m-%d")
    watch = {k: v for k, v in zt.items() if k != "id"}
    watch.update({
        "status": R.WATCH_WATCHING,
        "watched_at": today,
        "watch_source": source,
        "is_auto_added": source == "auto",
        "limitup_id": limitup_id,
        "strategy_version": R.STRATEGY_VERSION,
        "profile": "v2",
        "manual_force": bool(force),
    })
    saved = s.upsert_watch(watch)
    s.update_limitup(limitup_id, {"status": R.ZT_WATCHED})
    return saved


def refresh_all_boards() -> dict:
    """板块回填：首月轻量占位，避免 UI 报错；后续可接 V1 同源逻辑写入 data_v2。"""
    return {"ok": True, "updated": 0, "profile": "v2", "note": "V2 板块回填待增强"}


def enrich_limitup_manual(stock_name: str, limit_up_date: str, stock_code: str = "") -> dict:
    """手工入库到 V2 涨停池。"""
    s = st()
    code, name = mkt.resolve_code_name(name=stock_name, code=stock_code)
    if not code:
        raise ValueError(f"无法解析股票：{stock_name or stock_code}")
    score = Q.simple_leader_score({"last_price": None, "industry_board": None}, code, limit_up_date)
    rec = {
        "stock_code": code,
        "stock_name": name or stock_name,
        "limit_up_date": limit_up_date,
        "status": R.ZT_ACTIVE,
        "strategy_version": R.STRATEGY_VERSION,
        "experiment_id": R.EXPERIMENT_ID,
        "leader_score": score["leader_score"],
        "score_confidence": score["score_confidence"],
        "profile": "v2",
    }
    return s.upsert_limitup(rec)
