"""
涨停回头低吸 — 本地 Web UI
三层：涨停池 → 观察池 → 持有池
大 Tab：V1 | V2（V2 数据在 data_v2/，规则见 rules_v2 + 文档 GPT V2.3）
启动: python zt_pullback_UI.py
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import store  # noqa: E402
from rules import MAX_OBS_DAYS  # noqa: E402
from services import scanner as scanner_v1  # noqa: E402
from services import signals as signals_v1  # noqa: E402
from services import market as mkt  # noqa: E402

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    root_path=BASE_DIR,
)


def _ok(data, code=200):
    return jsonify({"success": True, "data": data}), code


def _err(msg: str, code: int = 400):
    return jsonify({"success": False, "error": msg}), code


def _profile() -> str:
    p = (request.args.get("profile") or "").strip().lower()
    if not p:
        body = request.get_json(force=True, silent=True) or {}
        p = str(body.get("profile") or "").strip().lower()
    return "v2" if p == "v2" else "v1"


def _st():
    return store.for_profile(_profile())


def _sc():
    if _profile() == "v2":
        from v2 import scanner as scanner_v2
        return scanner_v2
    return scanner_v1


def _sg():
    if _profile() == "v2":
        from v2 import signals as signals_v2
        return signals_v2
    return signals_v1


# 兼容旧名（部分内部若仍引用）
scanner = scanner_v1
signals = signals_v1


@app.errorhandler(Exception)
def _handle_unexpected(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import traceback
    traceback.print_exc()
    return _err(f"服务器错误: {e}", 500)


@app.route("/")
def index():
    return render_template("index.html", max_obs_days=MAX_OBS_DAYS)


def _norm(s: str) -> str:
    return "".join(str(s or "").split()).lower()


def _match_filters(
    items,
    *,
    date_exact,
    date_field,
    keyword,
    industry,
    concept,
    status,
    is_profit="",
    is_auto_added="",
):
    out = []
    kw = _norm(keyword)
    for x in items:
        d = str(x.get(date_field) or "")
        if date_exact and d != date_exact:
            continue
        if kw:
            if kw not in _norm(x.get("stock_code", "")) and kw not in _norm(x.get("stock_name", "")):
                continue
        if industry and str(x.get("industry_board") or "") != industry:
            continue
        if concept and str(x.get("concept_board") or "") != concept:
            continue
        if status and str(x.get("status") or "") != status:
            continue
        if is_profit == "1" and not x.get("is_profit"):
            continue
        if is_profit == "0" and x.get("is_profit"):
            continue
        if is_auto_added in ("1", "0", "true", "false"):
            want = is_auto_added in ("1", "true")
            # 兼容旧数据：watch_source==auto 视为自动
            got = x.get("is_auto_added")
            if got is None:
                got = str(x.get("watch_source") or "") == "auto"
            if bool(got) != want:
                continue
        out.append(x)
    return out


@app.route("/api/meta")
def api_meta():
    return _ok(_st().meta_get())


# ── 涨停池 ────────────────────────────────────────────────

@app.route("/api/limitup", methods=["GET"])
def api_limitup_list():
    items = _st().load_limitups()
    date = request.args.get("date", "").strip()
    # 涨停池按日展示：无 date 时默认最近一天
    dates = _st().limitup_dates()
    if not date and dates:
        date = dates[0]
    items = _match_filters(
        items,
        date_exact=date,
        date_field="limit_up_date",
        keyword=request.args.get("keyword", "").strip(),
        industry=request.args.get("industry", "").strip(),
        concept=request.args.get("concept", "").strip(),
        status=request.args.get("status", "").strip(),
    )
    items.sort(key=lambda x: int(x.get("id") or 0), reverse=True)
    return _ok({"items": items, "date": date, "dates": dates})


@app.route("/api/limitup", methods=["POST"])
def api_limitup_create():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("stock_name") or "").strip()
    code = (data.get("stock_code") or "").strip()
    lup = (data.get("limit_up_date") or "").strip()
    if (not name and not code) or not lup:
        return _err("需要股票名称（或代码）和涨停日期")
    try:
        return _ok(_sc().enrich_limitup_manual(name, lup, stock_code=code), 201)
    except Exception as e:
        return _err(str(e))


@app.route("/api/limitup/<int:oid>", methods=["PUT"])
def api_limitup_update(oid: int):
    data = request.get_json(force=True, silent=True) or {}
    data.pop("id", None)
    if "view_adj_day" in data and len(data) == 1:
        rec = _sc().apply_view_day(oid, int(data["view_adj_day"]), pool="limitup")
        return _ok(rec) if rec else _err("记录不存在", 404)
    rec = _st().update_limitup(oid, data)
    if not rec:
        return _err("记录不存在", 404)
    if "view_adj_day" in data:
        rec = _sc().apply_view_day(oid, int(data["view_adj_day"]), pool="limitup") or rec
    return _ok(rec)


@app.route("/api/limitup/<int:oid>/view-day", methods=["POST"])
def api_limitup_view_day(oid: int):
    data = request.get_json(force=True, silent=True) or {}
    rec = _sc().apply_view_day(oid, int(data.get("view_adj_day") or data.get("day") or 1), pool="limitup")
    return _ok(rec) if rec else _err("记录不存在", 404)


@app.route("/api/limitup/<int:oid>", methods=["DELETE"])
def api_limitup_delete(oid: int):
    if not _st().delete_limitup(oid):
        return _err("记录不存在", 404)
    return _ok({"id": oid})


@app.route("/api/limitup/<int:oid>/to-watch", methods=["POST"])
def api_limitup_to_watch(oid: int):
    data = request.get_json(force=True, silent=True) or {}
    force = bool(data.get("force", True))  # 手工加入默认允许强制
    try:
        return _ok(_sc().promote_to_watch(oid, force=force, source="manual"))
    except Exception as e:
        return _err(str(e))


# ── 观察池 ────────────────────────────────────────────────

@app.route("/api/watch", methods=["GET"])
def api_watch_list():
    items = _st().load_watches()
    items = _match_filters(
        items,
        date_exact=request.args.get("date", "").strip(),
        date_field="limit_up_date",
        keyword=request.args.get("keyword", "").strip(),
        industry=request.args.get("industry", "").strip(),
        concept=request.args.get("concept", "").strip(),
        status=request.args.get("status", "").strip(),
        is_auto_added=request.args.get("is_auto_added", "").strip(),
    )
    items.sort(key=lambda x: (str(x.get("limit_up_date") or ""), int(x.get("id") or 0)), reverse=True)
    return _ok(items)


@app.route("/api/watch", methods=["POST"])
def api_watch_create():
    """手工新增观察：先入涨停池再晋升（或直接从已有涨停池 id）。"""
    data = request.get_json(force=True, silent=True) or {}
    if data.get("limitup_id"):
        try:
            return _ok(_sc().promote_to_watch(int(data["limitup_id"]), force=True, source="manual"), 201)
        except Exception as e:
            return _err(str(e))
    name = (data.get("stock_name") or "").strip()
    code = (data.get("stock_code") or "").strip()
    lup = (data.get("limit_up_date") or "").strip()
    if (not name and not code) or not lup:
        return _err("需要股票名称（或代码）和涨停日期，或 limitup_id")
    try:
        zt = _sc().enrich_limitup_manual(name, lup, stock_code=code)
        w = _sc().promote_to_watch(int(zt["id"]), force=True, source="manual_create")
        return _ok(w, 201)
    except Exception as e:
        return _err(str(e))


@app.route("/api/watch/<int:oid>", methods=["PUT"])
def api_watch_update(oid: int):
    data = request.get_json(force=True, silent=True) or {}
    data.pop("id", None)
    if "view_adj_day" in data and len(data) == 1:
        rec = _sc().apply_view_day(oid, int(data["view_adj_day"]), pool="watch")
        return _ok(rec) if rec else _err("记录不存在", 404)
    rec = _st().update_watch(oid, data)
    if not rec:
        return _err("记录不存在", 404)
    if "view_adj_day" in data:
        rec = _sc().apply_view_day(oid, int(data["view_adj_day"]), pool="watch") or rec
    return _ok(rec)


@app.route("/api/watch/<int:oid>/view-day", methods=["POST"])
def api_watch_view_day(oid: int):
    data = request.get_json(force=True, silent=True) or {}
    rec = _sc().apply_view_day(oid, int(data.get("view_adj_day") or data.get("day") or 1), pool="watch")
    return _ok(rec) if rec else _err("记录不存在", 404)


@app.route("/api/watch/<int:oid>", methods=["DELETE"])
def api_watch_delete(oid: int):
    if not _st().delete_watch(oid):
        return _err("记录不存在", 404)
    return _ok({"id": oid})


# ── 持有池 ────────────────────────────────────────────────

@app.route("/api/position", methods=["GET"])
def api_pos_list():
    items = _st().load_positions()
    # 持仓中按最新价刷新浮动收益率
    refreshed = []
    for x in items:
        if x.get("status") == "holding":
            _sg().recompute_pnl(x)
        refreshed.append(x)
    items = refreshed
    date_field = request.args.get("date_field", "buy_date").strip() or "buy_date"
    items = _match_filters(
        items,
        date_exact=request.args.get("date", "").strip(),
        date_field=date_field,
        keyword=request.args.get("keyword", "").strip(),
        industry=request.args.get("industry", "").strip(),
        concept=request.args.get("concept", "").strip(),
        status=request.args.get("status", "").strip(),
        is_profit=request.args.get("is_profit", "").strip(),
    )
    items.sort(key=lambda x: (str(x.get("buy_date") or ""), int(x.get("id") or 0)), reverse=True)
    return _ok(items)


@app.route("/api/position", methods=["POST"])
def api_pos_create():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("stock_name") or "").strip()
    buy_date = (data.get("buy_date") or "").strip()
    buy_price = data.get("buy_price")
    watch_id = data.get("watch_id")
    if buy_price in (None, "") or not buy_date:
        return _err("需要买入日期、买入价")
    if not watch_id and not name:
        return _err("需要观察池 watch_id，或股票名称（须已在观察池）")
    try:
        rec = _sg().create_position_from_buy(
            stock_name=name or "",
            buy_date=buy_date,
            buy_price=float(buy_price),
            limit_up_date=(data.get("limit_up_date") or "").strip() or None,
            position_pct=float(data.get("position_pct") or 50),
            notes=data.get("notes") or "manual",
            stock_code=(data.get("stock_code") or "").strip(),
            watch_id=int(watch_id) if watch_id else None,
            require_watch=True,
        )
        return _ok(rec, 201)
    except Exception as e:
        return _err(str(e))


@app.route("/api/position/<int:pid>", methods=["PUT"])
def api_pos_update(pid: int):
    data = request.get_json(force=True, silent=True) or {}
    data.pop("id", None)
    rec = _st().update_position(pid, data)
    if not rec:
        return _err("记录不存在", 404)
    # 任意价格字段变更后立即重算并落库收益率
    if any(k in data for k in ("buy_price", "sell_price", "last_price", "stop_loss_price", "status")):
        rec = _sg().recompute_pnl(dict(rec))
        patch = {
            "profit_rate": rec.get("profit_rate"),
            "is_profit": rec.get("is_profit"),
            "stop_loss_pnl": rec.get("stop_loss_pnl"),
        }
        if "buy_price" in data and not rec.get("buy_price_locked") and rec.get("buy_price") not in (None, ""):
            stop = mkt.compute_stop_loss_price(
                float(rec["buy_price"]), rec.get("ma20"), rec.get("ma25"), rec.get("ma30"),
            )
            patch.update(stop)
        # 填了卖出价且未标明状态时，自动视为已卖出并算收益
        if "sell_price" in data and data.get("sell_price") not in (None, "") and "status" not in data:
            if rec.get("status") == "holding":
                patch["status"] = "sold"
                if not rec.get("sell_date"):
                    patch["sell_date"] = datetime.now().strftime("%Y-%m-%d")
                if not rec.get("sell_reason"):
                    patch["sell_reason"] = "manual"
                rec = {**rec, **patch}
                _sg().recompute_pnl(rec)
                patch["profit_rate"] = rec.get("profit_rate")
                patch["is_profit"] = rec.get("is_profit")
        rec = _st().update_position(pid, patch) or rec
    return _ok(rec)


@app.route("/api/position/<int:pid>", methods=["DELETE"])
def api_pos_delete(pid: int):
    if not _st().delete_position(pid):
        return _err("记录不存在", 404)
    return _ok({"id": pid})


@app.route("/api/position/<int:pid>/add", methods=["POST"])
def api_pos_add(pid: int):
    data = request.get_json(force=True, silent=True) or {}
    try:
        return _ok(_sg().add_to_position(
            pid, float(data["add_price"]),
            (data.get("add_date") or datetime.now().strftime("%Y-%m-%d")).strip(),
            float(data.get("add_pct") or 50),
        ))
    except Exception as e:
        return _err(str(e))


@app.route("/api/position/<int:pid>/sell", methods=["POST"])
def api_pos_sell(pid: int):
    data = request.get_json(force=True, silent=True) or {}
    try:
        return _ok(_sg().mark_sell(
            pid, float(data["sell_price"]),
            (data.get("sell_date") or datetime.now().strftime("%Y-%m-%d")).strip(),
            data.get("sell_reason") or "manual",
            data.get("status") or "sold",
        ))
    except Exception as e:
        return _err(str(e))


# ── 任务 ──────────────────────────────────────────────────

def _ui_job(program: str, fn):
    """UI 触发任务：执行并写入 runs 台账。"""
    from services.run_recorder import track_run, summarize_pipeline_result
    import rules_v2 as R2
    prof = _profile()
    with track_run(
        program,
        profile=prof,
        triggered_by="ui",
        strategy_version=(R2.STRATEGY_VERSION if prof == "v2" else "V1"),
        experiment_id=(R2.EXPERIMENT_ID if prof == "v2" else "V1_BASELINE"),
    ) as ctx:
        result = fn()
        ctx["result"] = result
        ctx["summary"] = summarize_pipeline_result(result if isinstance(result, dict) else {"raw": result})
        ctx["ok"] = True
        return result


@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(force=True, silent=True) or {}
    n = int(data.get("days") or 5)
    skip_soft = bool(data.get("skip_soft_filters"))
    asof = (data.get("asof") or "").strip() or None
    return _ok(_ui_job("ui_scan", lambda: _sc().scan_recent_days(asof=asof, n=n, skip_soft_filters=skip_soft)))


@app.route("/api/promote-watch", methods=["POST"])
def api_promote_watch():
    data = request.get_json(force=True, silent=True) or {}
    asof = (data.get("asof") or "").strip() or None
    return _ok(_ui_job("ui_promote_watch", lambda: _sc().promote_ready_to_watch(asof=asof)))


@app.route("/api/update-ma", methods=["POST"])
def api_update_ma():
    data = request.get_json(force=True, silent=True) or {}
    asof = (data.get("asof") or "").strip() or None
    return _ok(_ui_job("ui_update_ma", lambda: _sc().refresh_all_ma(asof=asof)))


@app.route("/api/refresh-boards", methods=["POST"])
def api_refresh_boards():
    return _ok(_ui_job("ui_refresh_boards", lambda: _sc().refresh_all_boards()))


@app.route("/api/jobs/buy-check", methods=["POST"])
def api_buy_check():
    data = request.get_json(force=True, silent=True) or {}
    asof = (data.get("asof") or "").strip() or None
    return _ok(_ui_job("ui_buy_check", lambda: _sg().check_buy_signals(asof=asof)))


@app.route("/api/jobs/sell-check", methods=["POST"])
def api_sell_check():
    data = request.get_json(force=True, silent=True) or {}
    asof = (data.get("asof") or "").strip() or None
    return _ok(_ui_job("ui_sell_check", lambda: _sg().check_sell_signals(asof=asof)))


@app.route("/api/filters/options")
def api_filter_options():
    zt = _st().load_limitups()
    wt = _st().load_watches()
    pos = _st().load_positions()
    all_rows = zt + wt + pos
    return _ok({
        "industries": sorted({str(x.get("industry_board")) for x in all_rows if x.get("industry_board")}),
        "concepts": sorted({str(x.get("concept_board")) for x in all_rows if x.get("concept_board")}),
        "zt_dates": _st().limitup_dates(),
        "watch_dates": sorted({str(x.get("limit_up_date")) for x in wt if x.get("limit_up_date")}, reverse=True),
        "pos_dates": sorted({str(x.get("buy_date") or x.get("limit_up_date")) for x in pos if x.get("buy_date") or x.get("limit_up_date")}, reverse=True),
    })


# 兼容旧 observation API → 涨停池
@app.route("/api/observation", methods=["GET", "POST"])
def api_obs_compat():
    if request.method == "GET":
        return api_limitup_list()
    return api_limitup_create()


def _free_port(start=5055) -> int:
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def main():
    port = int(os.environ.get("ZT_PULLBACK_PORT") or _free_port())
    url = f"http://127.0.0.1:{port}/"
    print(f"[涨停回头低吸] UI → {url}")

    def _open():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Timer(0.8, _open).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
