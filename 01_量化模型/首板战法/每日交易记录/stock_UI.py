"""
首板战法 — 每日交易记录 Web UI
启动: python stock_UI.py
浏览器打开后可查看/编辑/补全价格。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta

# ── 路径：包根只留本主运行；lib/jobs 经 runtime_paths 注入 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
_LIB_DIR = os.path.join(BASE_DIR, "lib")
_JOBS_DIR = os.path.join(BASE_DIR, "jobs")
for _p in (BASE_DIR, _LIB_DIR, _JOBS_DIR, ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import ensure_sys_path  # noqa: E402

ensure_sys_path()

from flask import Flask, Response, jsonify, render_template, request

import price_service as ps
import paths  # noqa: E402
import stock_meta  # noqa: E402  板块判定唯一事实来源（创业板/主板/…）

BACKUP_DIR = os.path.join(BASE_DIR, "data", "backups")
VALID_PROGRAMS = paths.PROGRAMS
DEFAULT_PROGRAM = "stock"
os.makedirs(BACKUP_DIR, exist_ok=True)
# 旧 trades/ 目录（迁移后仍保留只读）
_LEGACY_TRADES_BASE = os.path.join(BASE_DIR, "trades")


def _trades_dir(program: str = DEFAULT_PROGRAM) -> str:
    return paths.program_trades_dir(program)


def _migrate_legacy_trades():
    """将旧 每日交易记录/trades/{program} 或 trades/*.json 迁移到各程序 trades/。"""
    legacy_base = _LEGACY_TRADES_BASE
    if not os.path.isdir(legacy_base):
        return
    for prog in VALID_PROGRAMS:
        old_prog = os.path.join(legacy_base, prog)
        if os.path.isdir(old_prog):
            target = paths.program_trades_dir(prog)
            for fname in os.listdir(old_prog):
                if not fname.endswith(".json"):
                    continue
                src = os.path.join(old_prog, fname)
                dst = os.path.join(target, fname)
                if not os.path.exists(dst):
                    import shutil
                    shutil.copy2(src, dst)
    for fname in os.listdir(legacy_base):
        if not fname.endswith(".json") or not DATE_RE.match(fname[:-5]):
            continue
        src = os.path.join(legacy_base, fname)
        if os.path.isdir(src):
            continue
        stock_dir = _trades_dir("stock")
        dst = os.path.join(stock_dir, fname)
        if os.path.exists(dst):
            continue  # 已迁移
        try:
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for t in data:
                    t.setdefault("program", "stock")
            os.makedirs(stock_dir, exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            os.remove(src)
            print(f"[迁移] {fname} → stock/trades/{fname}")
        except Exception as e:
            print(f"[迁移] {fname} 失败: {e}")


# 显式指定模板目录，避免 runpy/兼容入口启动时找错路径
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    root_path=BASE_DIR,
)
# debug=False 时 Jinja 默认不重读模板；HTML 改完刷新仍旧页。强制热读，免重启。
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

_data_lock = threading.Lock()

# ── 磁盘读缓存（mtime 失效）：避免每次 API 请求全量重扫 400+ 个 JSON ──
# _DAY_CACHE: (program, date_str) -> (mtime, list)  单日文件缓存
# _DIR_CACHE:  program -> (dir_mtime, sorted_filelist)  目录列表缓存
_DAY_CACHE: dict[tuple[str, str], tuple[float, list]] = {}
_DIR_CACHE: dict[str, tuple[float, list[str]]] = {}


def _dir_signature(program: str) -> tuple[float, int]:
    """目录 mtime 作为文件列表变化签名。"""
    d = _trades_dir(program)
    try:
        st = os.stat(d)
        return st.st_mtime, st.st_mtime_ns if hasattr(st, "st_mtime_ns") else 0
    except OSError:
        return 0.0, 0


STOCK_CODE_RE = re.compile(r"^\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 启动时自动迁移旧数据（必须在 DATE_RE 定义之后）
_migrate_legacy_trades()

# 表头字段（英文 key）
REQUIRED_FIELDS = ["date", "stock_name"]
OPTIONAL_CODE = ["stock_code"]
PRICE_FIELDS = [
    "buy_price",
    "sell_price_935",
    "sell_price_1315",
    "sell_price_half_935",
    "sell_price_half_1315",
    "sell_price_batch_avg",
]
META_FIELDS = [
    "sell_date", "hold_days", "is_second_board", "rank",
    "skip_reason", "notes", "sell_date_935", "sell_date_1315",
    "board",  # 板块（创业板/主板…）—— 由代码判定，2026-10-26 用户要求
]
PNL_FIELDS = [
    "pnl_morning",
    "pnl_afternoon",
    "pnl_morning_half",
    "pnl_afternoon_half",
    "pnl_batch_morning",
    "pnl_batch_afternoon",
    "pnl_batch_total",
    "pnl_batch_total_half",
]
ALL_EDITABLE = REQUIRED_FIELDS + PRICE_FIELDS + META_FIELDS + PNL_FIELDS + ["id"]


def _ok(data, code=200):
    return jsonify({"success": True, "data": data}), code


def _err(msg: str, code: int = 400):
    return jsonify({"success": False, "error": msg}), code


@app.errorhandler(Exception)
def _handle_unexpected(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import traceback
    traceback.print_exc()
    return _err(f"服务器错误: {e}", 500)


def _safe_float(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val):
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


# ── 股票名称/代码解析 ─────────────────────────────────────
_NAME_CACHE: dict[str, str] | None = None  # name -> code
_CODE_CACHE: dict[str, str] | None = None  # code -> name


def _invalidate_stock_name_map():
    global _NAME_CACHE, _CODE_CACHE
    _NAME_CACHE = None
    _CODE_CACHE = None


def _load_stock_name_map() -> tuple[dict[str, str], dict[str, str]]:
    """
    权威码表：baostock 全市场（用最近有数据的交易日）。
    禁止用交易记录反写 name→code（错误手改会污染，如把安诺其指到张江高科代码）。
    """
    global _NAME_CACHE, _CODE_CACHE
    if _NAME_CACHE is not None and _CODE_CACHE is not None:
        return _NAME_CACHE, _CODE_CACHE
    name_to_code: dict[str, str] = {}
    code_to_name: dict[str, str] = {}
    try:
        if ps.baostock_enabled():
            import baostock as bs
            if ps._bs_ensure_login():
                today = datetime.now().strftime("%Y-%m-%d")
                # 今日可能尚无全市场快照（早盘/非交易日），回退到上一交易日
                days = [today]
                prev = ps.prev_trading_day(today)
                if prev:
                    days.append(prev)
                # 再兜底近几日
                for i in range(2, 8):
                    days.append((datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"))
                loaded = False
                for day in days:
                    with ps._BS_LOCK:
                        rs = bs.query_all_stock(day=day)
                        n = 0
                        while rs.error_code == "0" and rs.next():
                            row = rs.get_row_data()
                            raw = str(row[0])
                            code = raw.split(".")[-1].zfill(6)
                            name = str(row[2] or "").strip()
                            if code and name and str(row[1]) == "1":
                                name_to_code[name] = code
                                code_to_name[code] = name
                                n += 1
                    if n > 1000:
                        print(f"[stock-map] 使用 baostock 全市场 {day}，共 {n} 只")
                        loaded = True
                        break
                if not loaded:
                    print("[stock-map] baostock 全市场为空，尝试 query_stock_basic 兜底")
    except Exception as e:
        print(f"[stock-map] baostock 失败: {e}")

    _NAME_CACHE, _CODE_CACHE = name_to_code, code_to_name
    return name_to_code, code_to_name


def _bs_symbol(code: str) -> str:
    code = str(code).zfill(6)
    return ("sh." if code.startswith(("6", "9")) else "sz.") + code


def resolve_code_by_name(name: str) -> str | None:
    """优先单票查询，避免每次拉全市场卡死新增。"""
    name = str(name or "").strip()
    if not name:
        return None
    if _NAME_CACHE and name in _NAME_CACHE:
        return _NAME_CACHE[name]
    try:
        if ps.baostock_enabled():
            import baostock as bs
            if ps._bs_ensure_login():
                with ps._BS_LOCK:
                    rs = bs.query_stock_basic(code_name=name)
                    rows = []
                    while rs.error_code == "0" and rs.next():
                        rows.append(rs.get_row_data())
                live = [r for r in rows if str(r[5]) == "1" and name == str(r[1]).strip()]
                if len(live) == 1:
                    code = str(live[0][0]).split(".")[-1].zfill(6)
                    if _NAME_CACHE is not None:
                        _NAME_CACHE[name] = code
                    if _CODE_CACHE is not None:
                        _CODE_CACHE[code] = name
                    return code
                # 唯一包含
                contains = [r for r in rows if str(r[5]) == "1" and name in str(r[1])]
                if len(contains) == 1:
                    code = str(contains[0][0]).split(".")[-1].zfill(6)
                    nm = str(contains[0][1]).strip()
                    if _NAME_CACHE is not None:
                        _NAME_CACHE[nm] = code
                    if _CODE_CACHE is not None:
                        _CODE_CACHE[code] = nm
                    return code
    except Exception as e:
        print(f"[resolve-code] {e}")
    # 慢路径：全市场（后台可能已预热）
    name_map, _ = _load_stock_name_map()
    if name in name_map:
        return name_map[name]
    hits = [(n, c) for n, c in name_map.items() if name in n]
    if len(hits) == 1:
        return hits[0][1]
    return None


def resolve_name_by_code(code: str) -> str | None:
    """优先单票查询，避免每次拉全市场卡死新增。"""
    code = str(code or "").zfill(6)
    if _CODE_CACHE and code in _CODE_CACHE:
        return _CODE_CACHE[code]
    try:
        if ps.baostock_enabled():
            import baostock as bs
            if ps._bs_ensure_login():
                with ps._BS_LOCK:
                    rs = bs.query_stock_basic(code=_bs_symbol(code))
                    if rs.error_code == "0" and rs.next():
                        name = str(rs.get_row_data()[1] or "").strip()
                        if name:
                            if _NAME_CACHE is not None:
                                _NAME_CACHE[name] = code
                            if _CODE_CACHE is not None:
                                _CODE_CACHE[code] = name
                            return name
    except Exception as e:
        print(f"[resolve-name] {e}")
    _, code_map = _load_stock_name_map()
    return code_map.get(code)


def recalculate_all_trades(program: str = DEFAULT_PROGRAM) -> int:
    """按最新公式重算全部盈亏并落盘。"""
    with _data_lock:
        n = 0
        for fname in _list_day_files(program):
            day = fname[:-5]
            trades = _load_day(day, program)
            for i, t in enumerate(trades):
                trades[i] = auto_calculate(t)
                n += 1
            _save_day(day, trades, program)
        return n


def _pct(sell, buy):
    """(卖 - 买) / 买 * 100"""
    if sell is None or buy is None or buy == 0:
        return None
    try:
        return round((float(sell) - float(buy)) / float(buy) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def auto_calculate(trade: dict) -> dict:
    """根据买卖价自动计算分批均价、持仓天数、8 个盈亏字段。"""
    # 板块（2026-10-26 用户要求）：由代码判定，绝不信手工填的值。
    # 放在这里是因为 auto_calculate 是所有写盘路径的必经之路 —— 兜底最稳。
    code = str(trade.get("stock_code") or "").strip()
    if code:
        board = stock_meta.board_of_code(code)
        if board:
            trade["board"] = board
    buy = _safe_float(trade.get("buy_price"))
    p935 = _safe_float(trade.get("sell_price_935"))
    p1315 = _safe_float(trade.get("sell_price_1315"))
    h935 = _safe_float(trade.get("sell_price_half_935"))
    h1315 = _safe_float(trade.get("sell_price_half_1315"))

    # 半仓列未填时，回填为全仓同价
    if h935 is None and p935 is not None:
        h935 = p935
        trade["sell_price_half_935"] = p935
    if h1315 is None and p1315 is not None:
        h1315 = p1315
        trade["sell_price_half_1315"] = p1315

    # 分批卖出总价 = (早盘半仓价 + 午盘半仓价) / 2
    if h935 is not None and h1315 is not None:
        trade["sell_price_batch_avg"] = round((h935 + h1315) / 2, 2)
    elif h935 is not None:
        trade["sell_price_batch_avg"] = h935
    elif h1315 is not None:
        trade["sell_price_batch_avg"] = h1315
    else:
        trade["sell_price_batch_avg"] = None

    # 持仓天数
    buy_date = sell_date = None
    try:
        if trade.get("date"):
            buy_date = datetime.strptime(str(trade["date"]).strip(), "%Y-%m-%d")
    except ValueError:
        pass
    try:
        if trade.get("sell_date"):
            sell_date = datetime.strptime(str(trade["sell_date"]).strip(), "%Y-%m-%d")
    except ValueError:
        pass
    if buy_date and sell_date:
        trade["hold_days"] = max(0, (sell_date - buy_date).days)
    else:
        trade["hold_days"] = None

    # 8 个盈亏
    pnl_m = _pct(p935, buy)
    pnl_a = _pct(p1315, buy)
    pnl_bm = _pct(h935, buy)
    pnl_ba = _pct(h1315, buy)

    trade["pnl_morning"] = pnl_m
    trade["pnl_afternoon"] = pnl_a
    trade["pnl_morning_half"] = round(pnl_m / 2, 2) if pnl_m is not None else None
    trade["pnl_afternoon_half"] = round(pnl_a / 2, 2) if pnl_a is not None else None
    trade["pnl_batch_morning"] = pnl_bm
    trade["pnl_batch_afternoon"] = pnl_ba
    # 分批总盈亏 = 早盘分批/2 + 午盘分批/2
    if pnl_bm is not None and pnl_ba is not None:
        trade["pnl_batch_total"] = round(pnl_bm / 2 + pnl_ba / 2, 2)
    elif pnl_bm is not None:
        trade["pnl_batch_total"] = round(pnl_bm / 2, 2)
    elif pnl_ba is not None:
        trade["pnl_batch_total"] = round(pnl_ba / 2, 2)
    else:
        trade["pnl_batch_total"] = None
    if trade["pnl_batch_total"] is not None:
        trade["pnl_batch_total_half"] = round(trade["pnl_batch_total"] / 2, 2)
    else:
        trade["pnl_batch_total_half"] = None

    return trade


def empty_trade(**kwargs) -> dict:
    t = {
        "id": None,
        "date": None,
        "program": DEFAULT_PROGRAM,
        "stock_code": None,
        "stock_name": None,
        "board": None,
        "buy_price": None,
        "sell_price_935": None,
        "sell_price_1315": None,
        "sell_price_half_935": None,
        "sell_price_half_1315": None,
        "sell_price_batch_avg": None,
        "sell_date": None,
        "hold_days": None,
        "pnl_morning": None,
        "pnl_afternoon": None,
        "pnl_morning_half": None,
        "pnl_afternoon_half": None,
        "pnl_batch_morning": None,
        "pnl_batch_afternoon": None,
        "pnl_batch_total": None,
        "pnl_batch_total_half": None,
        "is_second_board": None,
        "rank": None,
        "skip_reason": None,
        "notes": None,
    }
    t.update(kwargs)
    return t


def validate_trade(data: dict, is_update: bool = False) -> list[str]:
    errors = []
    if not is_update:
        if not data.get("date"):
            errors.append("缺少必填字段: date")
        if not data.get("stock_name") and not data.get("stock_code"):
            errors.append("股票名称与代码至少填一项")
    if data.get("date") and not DATE_RE.match(str(data["date"])):
        errors.append("买入日期格式错误，应为 YYYY-MM-DD")
    if data.get("sell_date") and not DATE_RE.match(str(data["sell_date"])):
        errors.append("卖出日期格式错误，应为 YYYY-MM-DD")
    if data.get("stock_code") and str(data["stock_code"]).strip():
        code = str(data["stock_code"]).strip().zfill(6)
        if not STOCK_CODE_RE.match(code):
            errors.append("股票代码应为 6 位数字")
    for f in PRICE_FIELDS:
        if f in data and data[f] not in (None, ""):
            v = _safe_float(data[f])
            if v is None:
                errors.append(f"{f} 必须为数字")
            elif v <= 0:
                errors.append(f"{f} 必须大于 0")
    return errors


# ── 按日存储 ──────────────────────────────────────────────

def _day_path(date_str: str, program: str = DEFAULT_PROGRAM) -> str:
    return os.path.join(_trades_dir(program), f"{date_str}.json")


def _list_day_files(program: str = DEFAULT_PROGRAM) -> list[str]:
    sig = _dir_signature(program)
    cached = _DIR_CACHE.get(program)
    if cached is not None and cached[0] == sig[0]:
        return list(cached[1])
    files: set[str] = set()
    d = _trades_dir(program)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json") and DATE_RE.match(f[:-5]):
                files.add(f)
    legacy_prog = os.path.join(_LEGACY_TRADES_BASE, program)
    if os.path.isdir(legacy_prog):
        for f in os.listdir(legacy_prog):
            if f.endswith(".json") and DATE_RE.match(f[:-5]):
                files.add(f)
    result = sorted(files)
    _DIR_CACHE[program] = (sig[0], result)
    return result


_ALL_CACHE: dict[str, tuple[tuple, list]] = {}


def _all_cache_signature(program: str) -> tuple:
    """程序级聚合缓存签名：目录 mtime + 各文件 mtime 摘要。

    只做 stat（沙箱内约 0.005ms/次），不做 open（��箱内约 1.8ms/次）。
    """
    sig: list = [_dir_signature(program)]
    for fname in _list_day_files(program):
        try:
            st = os.stat(_day_path(fname[:-5], program))
            sig.append((fname, st.st_mtime_ns))
        except OSError:
            sig.append((fname, 0))
    return tuple(sig)


def _invalidate_cache(program: str | None = None, date_str: str | None = None) -> None:
    """写盘/删除后使相关缓存失效。"""
    if program is None:
        _DAY_CACHE.clear()
        _DIR_CACHE.clear()
        _ALL_CACHE.clear()
        return
    _ALL_CACHE.pop(program, None)
    if date_str is not None:
        _DAY_CACHE.pop((program, date_str), None)
    else:
        for k in [k for k in _DAY_CACHE if k[0] == program]:
            _DAY_CACHE.pop(k, None)
    _DIR_CACHE.pop(program, None)


def _resolve_day_path(date_str: str, program: str = DEFAULT_PROGRAM) -> str:
    """优先程序 trades/，兼容旧 每日交易记录/trades/{program}/。"""
    primary = _day_path(date_str, program)
    if os.path.exists(primary):
        return primary
    legacy = os.path.join(_LEGACY_TRADES_BASE, program, f"{date_str}.json")
    if os.path.exists(legacy):
        return legacy
    return primary


def _load_day(date_str: str, program: str = DEFAULT_PROGRAM) -> list:
    path = _resolve_day_path(date_str, program)
    if not os.path.exists(path):
        return []
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    key = (program, date_str)
    cached = _DAY_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        # 命中缓存：返回浅拷贝（列表新、每个 dict 新），避免 deepcopy 的高昂开销。
        # 字段值全是标量，浅拷贝足以隔离调用方的原地 mutate。
        return [dict(t) for t in cached[1]]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = data if isinstance(data, list) else []
        for t in result:
            t.setdefault("program", program)
        _DAY_CACHE[key] = (mtime, result)
        # 返回拷贝，防止调用方原地 mutate 污染缓存
        return [dict(t) for t in result]
    except (json.JSONDecodeError, ValueError):
        print(f"[数据层] {date_str}.json ({program}) 损坏，尝试备份恢复")
        return _recover_day(date_str, program)


def _recover_day(date_str: str, program: str = DEFAULT_PROGRAM) -> list:
    prefix = f"trades_{date_str}_"
    backups = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix) and f.endswith(".json")],
        reverse=True,
    )
    for bf in backups:
        try:
            with open(os.path.join(BACKUP_DIR, bf), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _atomic_write(_day_path(date_str, program), data)
                return data
        except Exception:
            continue
    return []


def _atomic_write(filepath: str, data):
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, filepath)


def _backup_day(date_str: str, data: list):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"trades_{date_str}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    cutoff = datetime.now() - timedelta(days=14)
    for fname in os.listdir(BACKUP_DIR):
        fpath = os.path.join(BACKUP_DIR, fname)
        try:
            if datetime.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                os.remove(fpath)
        except Exception:
            pass


def _is_trading_buy_date(date_str: str) -> bool:
    """买入日必须是 A 股交易日（周末/节假日禁止写入 trades）。"""
    if not date_str or not DATE_RE.match(date_str):
        return False
    cal = ps.get_trade_calendar()
    if cal is None:
        return True
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return not cal[cal["trade_date"] == dt].empty


def purge_non_trading_trade_days(programs: tuple[str, ...] | None = None) -> list[dict]:
    """删除 stock/slow/up 中非交易日的 trades 文件。"""
    programs = programs or VALID_PROGRAMS
    removed: list[dict] = []
    for program in programs:
        if program not in VALID_PROGRAMS:
            continue
        for fname in list(_list_day_files(program)):
            day = fname[:-5]
            if _is_trading_buy_date(day):
                continue
            path = _day_path(day, program)
            try:
                os.remove(path)
                removed.append({"program": program, "date": day, "path": path})
                print(f"[purge] 已删除非交易日记录 {program} {day}")
            except Exception as e:
                print(f"[purge] 删除失败 {program} {day}: {e}")
    return removed


def _save_day(date_str: str, data: list, program: str = DEFAULT_PROGRAM):
    if not _is_trading_buy_date(date_str):
        print(f"[trades] 拒绝写入非交易日买入文件: {program} {date_str}")
        return
    for t in data:
        t.setdefault("program", program)
        # 板块最终兜底（2026-10-26 用户要求）：这里是所有写盘路径的唯一出口，
        # 在此补齐可保证「历史补齐 + 以后新数据」都必带 board 字段。
        code = str(t.get("stock_code") || "").strip()
        if code:
            board = stock_meta.board_of_code(code)
            if board:
                t["board"] = board
    _backup_day(date_str, data)
    _atomic_write(_day_path(date_str, program), data)
    _invalidate_cache(program, date_str)
    # 本地 HTML 可见数据落盘后，防抖同步 GitHub Pages（与本机 UI 一致）
    try:
        from pages_sync import schedule_pages_sync
        schedule_pages_sync()
    except Exception as e:
        print(f"[pages-sync] 调度失败: {e}", flush=True)


def _load_all_trades(program: str = DEFAULT_PROGRAM) -> list:
    """全量交易。走程序级聚合缓存，避免每次 API 请求重扫 400+ 个 JSON。

    沙箱内单次 open() 约 1.8ms，429 个文件冷读约 1.3s——这是「点击后等几秒」
    的真正根因。这里用 stat 签名（0.005ms/次）判定缓存有效性，命中则直接返回。
    """
    sig = _all_cache_signature(program)
    cached = _ALL_CACHE.get(program)
    if cached is not None and cached[0] == sig:
        return [dict(t) for t in cached[1]]

    all_trades: list = []
    for fname in _list_day_files(program):
        day = fname[:-5]
        for t in _load_day(day, program):
            t.setdefault("date", day)
            t.setdefault("program", program)
            all_trades.append(t)
    all_trades.sort(key=lambda x: (str(x.get("date") or ""), int(x.get("id") or 0)))
    _ALL_CACHE[program] = (sig, all_trades)
    # 逐条 dict() 拷贝隔离调用方 mutate：0.13ms/846条，比 deepcopy 快 50 倍。
    # trades 字段全为标量、无嵌套结构，浅拷贝已足够。
    return [dict(t) for t in all_trades]


def warm_all_caches(programs: list[str] | None = None) -> dict[str, float]:
    """预热各程序聚合缓存，返回 {program: 耗时秒}。供启动后台线程调用。"""
    result: dict[str, float] = {}
    for p in (programs or VALID_PROGRAMS):
        t0 = time.perf_counter()
        try:
            n = len(_load_all_trades(p))
            result[p] = time.perf_counter() - t0
            print(f"[每日交易记录] 交易缓存预热 {p}: {n} 条 / {result[p] * 1000:.0f}ms")
        except Exception as e:
            print(f"[每日交易记录] 交易缓存预热 {p} 失败: {e}")
    return result


def _next_global_id(program: str = DEFAULT_PROGRAM, trades: list | None = None) -> int:
    if trades is None:
        trades = _load_all_trades(program)
    if not trades:
        return 1
    return max(int(t.get("id") or 0) for t in trades) + 1


def _find_trade(tid: int, program: str = DEFAULT_PROGRAM) -> tuple[str | None, int | None, dict | None]:
    """返回 (date_str, index_in_day, trade)。"""
    for fname in _list_day_files(program):
        day = fname[:-5]
        day_trades = _load_day(day, program)
        for i, t in enumerate(day_trades):
            if int(t.get("id") or 0) == tid:
                return day, i, t
    return None, None, None


# ── 自动补价 ──────────────────────────────────────────────

_SELL_PRICE_KEYS = (
    "sell_price_935", "sell_price_1315",
    "sell_price_half_935", "sell_price_half_1315",
)
_ROLL_TO_RE = re.compile(r"滚到(\d{4}-\d{2}-\d{2})")


def _effective_sell_start(trade: dict) -> str | None:
    """连板滚动后的实际卖出起点（skip_reason 里「滚到YYYY-MM-DD」优先于旧 sell_date）。"""
    sell_date = trade.get("sell_date")
    m = _ROLL_TO_RE.search(str(trade.get("skip_reason") or ""))
    if m:
        roll_to = m.group(1)
        if not sell_date or str(roll_to) > str(sell_date):
            return roll_to
    return sell_date


def pending_sell_buy_days(
    program: str = DEFAULT_PROGRAM,
    as_of: str | None = None,
    lookback_days: int = 60,
) -> set[str]:
    """
    仍有未补卖价、且计划卖出日不晚于 as_of 的买入日（含连板滚动中的历史持仓）。
    收盘补全必须扫描这些日，否则会漏掉 8/3、8/4 等早先买入仍持仓的票。
    """
    today = as_of or datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    days: set[str] = set()
    for fname in _list_day_files(program):
        buy_day = fname[:-5]
        if buy_day < cutoff:
            continue
        for t in _load_day(buy_day, program):
            if t.get("buy_price") in (None, ""):
                continue
            if "买不进" in str(t.get("skip_reason") or ""):
                continue
            if not any(t.get(k) in (None, "") for k in _SELL_PRICE_KEYS):
                continue
            sell_start = _effective_sell_start(t) or ps.next_trading_day(buy_day)
            if sell_start and str(sell_start) <= today:
                days.add(buy_day)
                break
    return days


def enrich_trade_prices(trade: dict, force: bool = False) -> dict:
    """
    自动补全：买入开盘价、卖出价、二板、盈亏。
    force=False：只填空字段，已有正确值不覆盖。
    force=True：强制重拉价格字段。
    开盘涨停(一字)：仍保留记录，但不写买卖价（买不进）。
    """
    code = str(trade.get("stock_code") or "").zfill(6)
    buy_date = trade.get("date")
    if not code or not buy_date or not DATE_RE.match(str(buy_date)):
        return auto_calculate(trade)

    trade.pop("skip_buy", None)

    # 开盘涨停：记一笔，买卖价全部留空
    open_zt = ps.is_open_limit_up(code, buy_date)
    if open_zt is True:
        for k in (
            "buy_price",
            "sell_price_935", "sell_price_1315",
            "sell_price_half_935", "sell_price_half_1315",
            "sell_price_batch_avg", "sell_date",
            "pnl_morning", "pnl_afternoon", "pnl_morning_half", "pnl_afternoon_half",
            "pnl_batch_morning", "pnl_batch_afternoon", "pnl_batch_total", "pnl_batch_total_half",
        ):
            trade[k] = None
        trade["skip_reason"] = f"{buy_date} 开盘涨停(一字/封板)，买不进，不记买卖价"
        # 二板可仍尝试标记（开盘涨停通常即冲击二板）
        if force or trade.get("is_second_board") is None:
            trade["is_second_board"] = True
        return auto_calculate(trade)

    # 买入价 = D1 开盘价（铁律：禁止用 D0 涨停日开盘价/收盘价）。
    # buy_date 即 trade["date"]，语义必须是「D1 买入日」；任何 D0 价格绝不写入 buy_price。
    # 参见 IRON_RULES.md「买入价口径」与 fix_buy_price.py。
    cur_buy = _safe_float(trade.get("buy_price"))
    need_buy = force or cur_buy is None
    if not need_buy and cur_buy is not None:
        # 自愈：已有值若等于 D0 开/收且与真实 D1 开盘不同 → 视为历史错写，强制纠正
        d0 = ps.prev_trading_day(buy_date)
        d0_ohlc = ps.get_daily_ohlc(code, d0) if d0 else None
        if d0_ohlc:
            d0_open = float(d0_ohlc["open"])
            d0_close = float(d0_ohlc["close"])
            if abs(cur_buy - d0_open) < 0.015 or abs(cur_buy - d0_close) < 0.015:
                need_buy = True
    if need_buy:
        open_p = ps.get_open_price(code, buy_date)
        if open_p is not None:
            # 再挡一层：若取到的「开盘」仍等于 D0 开盘且今天尚未开盘确认，宁可不写
            d0 = ps.prev_trading_day(buy_date)
            d0_ohlc = ps.get_daily_ohlc(code, d0) if d0 else None
            today = datetime.now().strftime("%Y-%m-%d")
            now = datetime.now()
            pre_auction = (
                str(buy_date) == today
                and (now.hour < 9 or (now.hour == 9 and now.minute < 25))
            )
            if (
                d0_ohlc
                and abs(open_p - float(d0_ohlc["open"])) < 0.015
                and pre_auction
            ):
                pass  # 竞价前疑似 D0 开盘残留，不写
            else:
                trade["buy_price"] = open_p

    # 默认卖出日起点 = D2；连板滚动时从 skip_reason「滚到」日或已更新的 sell_date 继续
    d2 = ps.next_trading_day(buy_date)
    sell_date = _effective_sell_start(trade) or trade.get("sell_date") or d2
    trade["sell_date"] = sell_date

    any_sell_empty = any(trade.get(k) in (None, "") for k in _SELL_PRICE_KEYS)

    # 已有卖出价疑似「开盘冒充9:35 / 收盘冒充13:15」→ 必须重解
    ohlc_fallback_sell = False
    if not force and sell_date and not any_sell_empty:
        ohlc_fallback_sell = ps.sell_prices_look_like_ohlc_fallback(
            code,
            sell_date,
            trade.get("sell_price_935"),
            trade.get("sell_price_1315"),
        )

    # 快速路径：买卖价已齐、非强制、且无开盘/收盘冒充 → 只重算盈亏
    if (
        not force
        and not ohlc_fallback_sell
        and trade.get("buy_price") not in (None, "")
        and not any_sell_empty
    ):
        return auto_calculate(trade)

    # 部分空值时：已有卖出价若落在跌停，视为无效成交，触发重解
    soft_force_sell = bool(ohlc_fallback_sell)
    if not force and not soft_force_sell and sell_date and any_sell_empty:
        prev_s = ps.get_prev_close(code, sell_date)
        if prev_s is not None:
            for k in _SELL_PRICE_KEYS:
                px = _safe_float(trade.get(k))
                if px is not None and ps.is_at_limit_down(px, prev_s, code):
                    soft_force_sell = True
                    break

    need_sell = force or soft_force_sell or any_sell_empty
    if need_sell and d2:
        resolve_from = d2 if (force or soft_force_sell) else sell_date
        resolved = ps.resolve_sell_day_prices(code, resolve_from)
        overwrite = force or soft_force_sell
        resolved_sd = resolved.get("sell_date")
        if resolved_sd:
            old_sd = trade.get("sell_date")
            if overwrite or not old_sd or resolved.get("rolled") or str(resolved_sd) >= str(old_sd):
                trade["sell_date"] = resolved_sd
        if resolved.get("skip_reason"):
            if overwrite or not trade.get("skip_reason") or resolved.get("rolled"):
                trade["skip_reason"] = resolved["skip_reason"].strip("; ")
            elif any(trade.get(k) in (None, "") for k in _SELL_PRICE_KEYS):
                trade["skip_reason"] = resolved["skip_reason"].strip("; ")

        mapping = [
            ("sell_price_935", "sell_price_935"),
            ("sell_price_1315", "sell_price_1315"),
            ("sell_price_half_935", "sell_price_half_935"),
            ("sell_price_half_1315", "sell_price_half_1315"),
        ]
        for tk, rk in mapping:
            if overwrite:
                # 含 None：滚动/跳过时清空旧价，避免留下跌停日假成交
                trade[tk] = resolved.get(rk)
            elif trade.get(tk) in (None, ""):
                if resolved.get(rk) is not None:
                    trade[tk] = resolved[rk]
        # 两批独立滚动：记录各批实际成交日，便于追溯（sell_date 取较晚者）
        for tk, rk in (("sell_date_935", "sell_date_935"), ("sell_date_1315", "sell_date_1315")):
            if overwrite or resolved.get(rk) is not None:
                trade[tk] = resolved.get(rk)
        # 价格来源留痕：baostock_5m（口径基准）/ sina_1m（当日 EOD 未发布时的兜底）
        if resolved.get("sell_price_source"):
            trade["sell_price_source"] = resolved["sell_price_source"]
            trade["sell_price_source_935"] = resolved.get("sell_price_source_935")
            trade["sell_price_source_1315"] = resolved.get("sell_price_source_1315")
        # 卖出价已齐 → 清除历史占位/缺价说明（"买不进"保留）
        if not any(trade.get(k) in (None, "") for k in _SELL_PRICE_KEYS):
            sr = str(trade.get("skip_reason") or "")
            if sr and "买不进" not in sr:
                trade["skip_reason"] = None

    if force or trade.get("is_second_board") is None:
        sb = ps.check_second_board(code, buy_date)
        if sb is not None:
            trade["is_second_board"] = sb

    return auto_calculate(trade)


def purge_open_limit_buys(program: str = DEFAULT_PROGRAM) -> list[dict]:
    """将买入日开盘涨停的记录改为「留档、清空买卖价」（不再删除）。"""
    touched = []
    with _data_lock:
        for fname in list(_list_day_files(program)):
            day = fname[:-5]
            trades = _load_day(day, program)
            changed = False
            for i, t in enumerate(trades):
                code = str(t.get("stock_code") or "").zfill(6)
                if not code:
                    continue
                flag = ps.is_open_limit_up(code, day)
                if flag is True:
                    nt = enrich_trade_prices(dict(t), force=True)
                    trades[i] = nt
                    touched.append({"id": t.get("id"), "date": day, "code": code, "name": t.get("stock_name")})
                    changed = True
            if changed:
                _save_day(day, trades, program)
    return touched


def upsert_from_stock_py(records: list[dict], auto_enrich: bool = True, replace_by_rank: bool = True, program: str = DEFAULT_PROGRAM) -> dict:
    """
    stock.py 调用：写入基础记录（代码/名称/买入日期/排名），并尝试补价。
    records: [{date, stock_code, stock_name, rank}, ...]
    replace_by_rank=True：同一买入日同一排名以 stock.py 为准覆盖（换码时清空旧价）。
    auto_enrich=False 时只写基础字段，留给后续批量补价。
    """
    created, updated = 0, 0
    price_fields = (
        "buy_price", "sell_price_935", "sell_price_1315",
        "sell_price_half_935", "sell_price_half_1315", "sell_price_batch_avg",
        "pnl_morning", "pnl_afternoon", "pnl_morning_half", "pnl_afternoon_half",
        "pnl_batch_morning", "pnl_batch_afternoon", "pnl_batch_total", "pnl_batch_total_half",
        "sell_date", "skip_reason", "is_second_board",
    )
    with _data_lock:
        all_trades = _load_all_trades(program)
        next_id = _next_global_id(program, all_trades)
        by_day: dict[str, list] = {}
        for r in records:
            date_str = str(r.get("date") or "").strip()
            code = str(r.get("stock_code") or "").strip().zfill(6)
            name = str(r.get("stock_name") or "").strip()
            rank = r.get("rank")
            if not date_str or not DATE_RE.match(date_str) or not STOCK_CODE_RE.match(code):
                continue
            if not _is_trading_buy_date(date_str):
                print(f"[upsert] 跳过非交易日买入 {program} {date_str} {code}")
                continue
            # 名称以代码权威名为准（若能解析）
            official = resolve_name_by_code(code)
            if official:
                name = official
            # 板块由代码判定（2026-10-26 用户要求：每条记录必带 board）
            board = stock_meta.board_of_code(code) or None
            # 开盘涨停也入库：不写买卖价（enrich 内处理）
            if date_str not in by_day:
                by_day[date_str] = _load_day(date_str, program)

            day_trades = by_day[date_str]
            existing = next(
                (t for t in day_trades if str(t.get("stock_code", "")).zfill(6) == code),
                None,
            )
            if existing is None and replace_by_rank and rank is not None:
                existing = next(
                    (t for t in day_trades if int(t.get("rank") or 0) == int(rank)),
                    None,
                )

            if existing:
                old_code = str(existing.get("stock_code") or "").zfill(6)
                if old_code != code:
                    for k in price_fields:
                        existing[k] = None
                existing["stock_code"] = code
                existing["stock_name"] = name or existing.get("stock_name")
                if board:
                    existing["board"] = board
                if rank is not None:
                    existing["rank"] = rank
                existing["program"] = program
                if auto_enrich:
                    enrich_trade_prices(existing, force=False)
                else:
                    auto_calculate(existing)
                updated += 1
            else:
                trade = empty_trade(
                    id=next_id,
                    date=date_str,
                    stock_code=code,
                    stock_name=name,
                    board=board,
                    rank=rank,
                    program=program,
                )
                next_id += 1
                if auto_enrich:
                    enrich_trade_prices(trade, force=False)
                else:
                    auto_calculate(trade)
                day_trades.append(trade)
                created += 1

        for date_str, day_trades in by_day.items():
            _save_day(date_str, day_trades, program)

    _invalidate_stock_name_map()
    return {"created": created, "updated": updated}


# ── API ───────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/trades", methods=["GET"])
def api_get_trades():
    keyword = request.args.get("keyword", "").strip()
    date_filter = request.args.get("date", "").strip()
    month = request.args.get("month", "").strip()  # YYYY-MM
    rank = request.args.get("rank", "").strip()
    program = request.args.get("program", DEFAULT_PROGRAM).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    with _data_lock:
        trades = _load_all_trades(program)
    # 最新日期在上
    trades.sort(key=lambda x: (str(x.get("date") or ""), int(x.get("id") or 0)), reverse=True)
    if month and re.match(r"^\d{4}-\d{2}$", month):
        trades = [t for t in trades if str(t.get("date") or "").startswith(month)]
    if date_filter:
        trades = [t for t in trades if str(t.get("date") or "") == date_filter]
    if rank:
        try:
            r = int(rank)
            trades = [t for t in trades if int(t.get("rank") or 0) == r]
        except ValueError:
            pass
    if keyword:
        kw = keyword.lower()
        trades = [
            t for t in trades
            if kw in str(t.get("stock_code", "")).lower()
            or kw in str(t.get("stock_name", "")).lower()
        ]
    # 板块过滤（主板 / 创业板 …）：字段缺失时回退到代码判定（唯一事实来源）
    board_filter = request.args.get("board", "").strip()
    board_rank_filter = request.args.get("board_rank", "").strip()
    if board_filter:
        def _eff_board(t):
            b = t.get("board")
            if not b:
                b = stock_meta.board_of_code(t.get("stock_code", ""))
            return b or ""
        # 板块 tab 只展示「该板块自己的 Top2」记录（带 board_rank 的回填记录）
        trades = [t for t in trades if _eff_board(t) == board_filter and t.get("board_rank") is not None]
        if board_rank_filter:
            try:
                br = int(board_rank_filter)
                trades = [t for t in trades if int(t.get("board_rank") or 0) == br]
            except ValueError:
                pass
    return _ok(trades)


def _enrich_trade_by_id(trade_id: int, program: str = DEFAULT_PROGRAM, force: bool = False) -> None:
    """后台补价：短锁读 → 锁外取价 → 短锁写。"""
    try:
        with _data_lock:
            day, idx, cur = _find_trade(trade_id, program)
            if cur is None:
                return
            snap = dict(cur)
        enriched = enrich_trade_prices(snap, force=force)
        enriched.pop("skip_buy", None)
        enriched.setdefault("program", program)
        with _data_lock:
            day, idx, cur = _find_trade(trade_id, program)
            if cur is None or day is None or idx is None:
                return
            day_trades = _load_day(day, program)
            day_trades[idx] = enriched
            _save_day(day, day_trades, program)
        print(f"[bg-enrich] #{trade_id} ({program}) buy={enriched.get('buy_price')} sell={enriched.get('sell_price_935')}/{enriched.get('sell_price_1315')}")
    except Exception as e:
        print(f"[bg-enrich] #{trade_id} ({program}) 失败: {e}")


def _resolve_code_name_for_write(name: str, code: str) -> tuple[str, str]:
    """解析代码/名称（锁外调用）。优先信任 6 位代码。"""
    name = str(name or "").strip()
    code = str(code or "").strip()
    if code and STOCK_CODE_RE.match(code.zfill(6)):
        code = code.zfill(6)
        official = resolve_name_by_code(code)
        if official:
            name = official
        elif not name:
            name = code
        return code, name
    if name:
        got = resolve_code_by_name(name) or ""
        if got:
            code = got
            official = resolve_name_by_code(code)
            if official:
                name = official
            return code, name
    return code.zfill(6) if code else "", name


@app.route("/api/trades", methods=["POST"])
def api_create_trade():
    data = request.get_json(silent=True) or {}
    errors = validate_trade(data, is_update=False)
    if errors:
        return _err("; ".join(errors))

    date_str = str(data["date"]).strip()
    name = str(data.get("stock_name") or "").strip()
    code = str(data.get("stock_code") or "").strip()
    program = str(data.get("program", DEFAULT_PROGRAM)).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    code, name = _resolve_code_name_for_write(name, code)
    if not code or not STOCK_CODE_RE.match(code):
        return _err("未能识别股票代码，请填写代码或使用准确名称")

    trade = empty_trade()
    for f in ALL_EDITABLE:
        if f in data and data[f] is not None:
            trade[f] = data[f]
    trade["date"] = date_str
    trade["stock_code"] = code
    trade["stock_name"] = name
    trade["program"] = program
    # 板块由代码判定（不取前端传值，auto_calculate 里还会再兜底一次）
    trade["board"] = stock_meta.board_of_code(code) or None
    if "is_second_board" in data:
        trade["is_second_board"] = bool(data["is_second_board"]) if data["is_second_board"] is not None else None
    for pf in PRICE_FIELDS:
        if pf in data:
            trade[pf] = _safe_float(data.get(pf))

    trade = auto_calculate(trade)
    trade.pop("skip_buy", None)
    auto_fill = bool(data.get("auto_fill", True))

    with _data_lock:
        day_trades = _load_day(date_str, program)
        for t in day_trades:
            if str(t.get("stock_code", "")).zfill(6) == code:
                return _err(f"{date_str} {code} ({program}) 已存在记录", 409)
        trade["id"] = _next_global_id(program)
        day_trades.append(trade)
        _save_day(date_str, day_trades, program)

    saved = dict(trade)
    if auto_fill:
        threading.Thread(
            target=_enrich_trade_by_id,
            args=(int(saved["id"]), program, False),
            daemon=True,
            name=f"enrich-{saved['id']}",
        ).start()
    return _ok(saved, 201)


@app.route("/api/trades/<int:tid>", methods=["PUT"])
def api_update_trade(tid):
    """先存字段，再按需后台补价；禁止在持锁时打行情。"""
    data = request.get_json(silent=True) or {}
    errors = validate_trade(data, is_update=True)
    if errors:
        return _err("; ".join(errors))

    auto_fill = bool(data.get("auto_fill", False))
    program = str(data.get("program", DEFAULT_PROGRAM)).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM

    with _data_lock:
        day, idx, trade = _find_trade(tid, program)
        if trade is None:
            # 降级尝试所有程序
            for p in VALID_PROGRAMS:
                if p == program:
                    continue
                day, idx, trade = _find_trade(tid, p)
                if trade is not None:
                    program = p
                    break
        if trade is None:
            return _err(f"记录 {tid} 不存在", 404)
        old_date = day
        old_code = str(trade.get("stock_code") or "").zfill(6)
        snap = dict(trade)

    new_date = str(data.get("date", snap.get("date") or old_date)).strip()
    name = str(data.get("stock_name", snap.get("stock_name")) or "").strip()
    new_code = str(data.get("stock_code", snap.get("stock_code")) or "").strip()
    # 锁外解析
    new_code, name = _resolve_code_name_for_write(name, new_code)
    if not new_code or not STOCK_CODE_RE.match(new_code):
        return _err("未能识别股票代码")

    data["stock_code"] = new_code
    data["stock_name"] = name

    clear_prices = old_code != new_code
    need_bg = auto_fill or clear_prices

    with _data_lock:
        day, idx, trade = _find_trade(tid, program)
        if trade is None:
            return _err(f"记录 {tid} 不存在", 404)
        old_date = day

        check_day = _load_day(new_date, program) if new_date != old_date else _load_day(old_date, program)
        for t in check_day:
            if int(t.get("id") or 0) == tid:
                continue
            if str(t.get("stock_code", "")).zfill(6) == new_code:
                return _err(f"{new_date} {new_code} ({program}) 已存在记录", 409)

        day_trades = _load_day(old_date, program)
        idx = next((i for i, t in enumerate(day_trades) if int(t.get("id") or 0) == tid), None)
        if idx is None:
            return _err(f"记录 {tid} 不存在", 404)

        if clear_prices:
            for k in PRICE_FIELDS + (
                "sell_date", "skip_reason", "is_second_board",
                "pnl_morning", "pnl_afternoon", "pnl_morning_half", "pnl_afternoon_half",
                "pnl_batch_morning", "pnl_batch_afternoon", "pnl_batch_total", "pnl_batch_total_half",
                "sell_price_batch_avg",
            ):
                day_trades[idx][k] = None

        for key in REQUIRED_FIELDS + PRICE_FIELDS + META_FIELDS:
            if key not in data:
                continue
            if key in PRICE_FIELDS:
                day_trades[idx][key] = _safe_float(data[key])
            elif key == "is_second_board":
                v = data[key]
                if v is None or v == "":
                    day_trades[idx][key] = None
                else:
                    day_trades[idx][key] = bool(v) if not isinstance(v, str) else v in ("1", "true", "True", "是", "yes")
            elif key == "stock_code":
                day_trades[idx][key] = new_code
            elif key == "stock_name":
                day_trades[idx][key] = name
            elif key == "board":
                # 板块由代码判定，不接受前端传值（防止手改污染）
                pass
            else:
                day_trades[idx][key] = data[key]

        day_trades[idx]["stock_code"] = new_code
        day_trades[idx]["stock_name"] = name
        day_trades[idx]["program"] = program
        day_trades[idx] = auto_calculate(day_trades[idx])

        if new_date != old_date:
            moved = day_trades.pop(idx)
            moved["date"] = new_date
            _save_day(old_date, day_trades, program)
            target = _load_day(new_date, program)
            target.append(moved)
            _save_day(new_date, target, program)
            saved = dict(moved)
        else:
            _save_day(old_date, day_trades, program)
            saved = dict(day_trades[idx])

    # 空价或换码 / 显式 auto_fill → 后台补
    empty_price = saved.get("buy_price") in (None, "") or any(
        saved.get(k) in (None, "")
        for k in ("sell_price_935", "sell_price_1315", "sell_price_half_935", "sell_price_half_1315")
    )
    if need_bg or empty_price:
        threading.Thread(
            target=_enrich_trade_by_id,
            args=(tid, program, clear_prices),
            daemon=True,
            name=f"enrich-put-{tid}",
        ).start()

    return _ok(saved)

@app.route("/api/trades/<int:tid>", methods=["DELETE"])
def api_delete_trade(tid):
    program = request.args.get("program", DEFAULT_PROGRAM).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    with _data_lock:
        day, idx, trade = _find_trade(tid, program)
        if trade is None:
            # 降级尝试所有程序
            for p in VALID_PROGRAMS:
                if p == program:
                    continue
                day, idx, trade = _find_trade(tid, p)
                if trade is not None:
                    program = p
                    break
        if trade is None:
            return _err(f"记录 {tid} 不存在", 404)
        day_trades = _load_day(day, program)
        idx = next(i for i, t in enumerate(day_trades) if int(t.get("id") or 0) == tid)
        removed = day_trades.pop(idx)
        _save_day(day, day_trades, program)
    return _ok({"deleted": removed})


@app.route("/api/trades/refresh-prices", methods=["POST"])
def api_refresh_prices():
    """
    补全价格。body: {ids?:[], force?:bool, date?:str, month?:str, program?:str}
    force=False（默认）：只填空，已有值不动。
    force=True：强制重拉。
    仅未买入且开盘涨停的记录会被删除（已有买入价的不删）。
    取价在锁外执行，避免长时间阻塞页面刷新。
    """
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))
    ids = data.get("ids")
    date_filter = (data.get("date") or "").strip() or None
    month_filter = (data.get("month") or "").strip() or None
    program = str(data.get("program", DEFAULT_PROGRAM)).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    id_set = None
    if ids:
        try:
            id_set = {int(x) for x in ids}
        except (TypeError, ValueError):
            return _err("ids 格式错误")

    ensure_result = None
    if program in ("slow", "up"):
        from daily_close_job import ensure_slow_up_ready, morning_recommendations_ready, _is_trading_day
        today_str = datetime.now().strftime("%Y-%m-%d")
        ensure_day = (date_filter or today_str).strip()
        need_ensure = False
        if ensure_day and _is_trading_day(ensure_day):
            if force:
                need_ensure = True
            elif ensure_day == today_str:
                ok, _ = morning_recommendations_ready(program, ensure_day)
                need_ensure = not ok
            elif date_filter:
                ok, _ = morning_recommendations_ready(program, ensure_day)
                need_ensure = not ok
        if need_ensure:
            ensure_result = ensure_slow_up_ready(
                ensure_day, (program,), force_produce=force, label="refresh_ensure",
            )

    # 1) 短锁：读出待处理快照
    with _data_lock:
        if date_filter:
            days = [date_filter]
        else:
            days = sorted({f[:-5] for f in _list_day_files(program)})
            days_set = set(days)
            days_set |= pending_sell_buy_days(program, datetime.now().strftime("%Y-%m-%d"))
            days = sorted(days_set)
            if month_filter and re.match(r"^\d{4}-\d{2}$", month_filter):
                days = [d for d in days if d.startswith(month_filter)]
        snapshot = {}
        for day in days:
            if not day:
                continue
            day_trades = _load_day(day, program)
            if id_set is not None:
                day_trades = [t for t in day_trades if int(t.get("id") or 0) in id_set]
            if day_trades:
                # 深拷贝，避免锁外修改与磁盘态交织
                snapshot[day] = [dict(t) for t in day_trades]

    # 2) 锁外取价（网络 IO）；一字板只清空价格，不删除记录
    updated = []
    unfilled_open_limit = []
    price_errors = []
    results = {}
    for day, subset in snapshot.items():
        enriched_by_id = {}
        for t in subset:
            before = json.dumps(t, sort_keys=True, default=str)
            try:
                enriched = enrich_trade_prices(t, force=force)
            except Exception as e:
                print(f"[refresh-prices] 补价失败 {day} {t.get('stock_code')}: {e}")
                enriched = auto_calculate(dict(t))
                price_errors.append({
                    "id": t.get("id"),
                    "date": day,
                    "code": t.get("stock_code"),
                    "error": str(e),
                })
            enriched.pop("skip_buy", None)
            enriched.setdefault("program", program)
            if enriched.get("skip_reason") and "买不进" in str(enriched.get("skip_reason")):
                unfilled_open_limit.append({
                    "id": enriched.get("id"),
                    "date": day,
                    "code": enriched.get("stock_code"),
                    "name": enriched.get("stock_name"),
                })
            after = json.dumps(enriched, sort_keys=True, default=str)
            enriched_by_id[int(enriched.get("id") or 0)] = enriched
            if before != after or id_set is not None:
                updated.append({"id": enriched.get("id"), "date": day, "code": enriched.get("stock_code")})
        results[day] = enriched_by_id

    # 3) 短锁：写回
    with _data_lock:
        for day, enriched_by_id in results.items():
            day_trades = _load_day(day, program)
            new_list = []
            changed = False
            for t in day_trades:
                tid = int(t.get("id") or 0)
                if tid in enriched_by_id:
                    nt = enriched_by_id[tid]
                    if json.dumps(nt, sort_keys=True, default=str) != json.dumps(t, sort_keys=True, default=str):
                        changed = True
                    new_list.append(nt)
                else:
                    new_list.append(t)
            if changed:
                _save_day(day, new_list, program)

    return _ok({
        "updated": len(updated),
        "removed": [],
        "unfilled_open_limit": unfilled_open_limit,
        "price_errors": price_errors,
        "ensure": ensure_result,
    })


@app.route("/api/trades/purge-open-limit", methods=["POST"])
def api_purge_open_limit():
    data = request.get_json(silent=True) or {}
    program = str(data.get("program", DEFAULT_PROGRAM)).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    removed = purge_open_limit_buys(program)
    return _ok({"removed": removed, "count": len(removed)})


@app.route("/api/trades/sync-from-recommend", methods=["POST"])
def api_sync_from_recommend():
    """手动写入推荐票基础数据。body: {records:[{date,stock_code,stock_name,rank}], program?:str}"""
    data = request.get_json(silent=True) or {}
    records = data.get("records") or []
    program = str(data.get("program", DEFAULT_PROGRAM)).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    if not records:
        return _err("records 不能为空")
    result = upsert_from_stock_py(records, program=program)
    return _ok(result)


@app.route("/api/stats", methods=["GET"])
def api_stats():
    month = request.args.get("month", "").strip()  # YYYY-MM
    rank = request.args.get("rank", "").strip()
    program = request.args.get("program", DEFAULT_PROGRAM).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    with _data_lock:
        trades = _load_all_trades(program)

    if month and re.match(r"^\d{4}-\d{2}$", month):
        trades = [t for t in trades if str(t.get("date") or "").startswith(month)]
    if rank:
        try:
            r = int(rank)
            trades = [t for t in trades if int(t.get("rank") or 0) == r]
        except ValueError:
            pass
    # 板块 tab（主板/创业板）：统计口径与 /api/trades 的 board 过滤完全一致，
    # 三张盈亏累计卡片只算该板块自己的记录
    board_filter = request.args.get("board", "").strip()
    board_rank_filter = request.args.get("board_rank", "").strip()
    if board_filter:
        def _eff_board_stat(t):
            b = t.get("board")
            if not b:
                b = stock_meta.board_of_code(t.get("stock_code", ""))
            return b or ""
        trades = [t for t in trades if _eff_board_stat(t) == board_filter and t.get("board_rank") is not None]
        if board_rank_filter:
            try:
                br = int(board_rank_filter)
                trades = [t for t in trades if int(t.get("board_rank") or 0) == br]
            except ValueError:
                pass

    total = len(trades)
    empty = {
        "total": 0, "completed": 0, "holding": 0,
        "win_rate_morning": 0, "win_rate_afternoon": 0, "win_rate_batch": 0,
        "sum_pnl_morning": 0, "sum_pnl_afternoon": 0, "sum_pnl_batch": 0,
        "sum_pnl_batch_half": 0, "avg_pnl_morning": 0, "avg_pnl_afternoon": 0,
        "avg_pnl_batch": 0, "second_board_count": 0, "second_board_rate": 0,
        "month_pnl_morning_half": 0, "month_pnl_afternoon_half": 0, "month_pnl_batch_half": 0,
        "max_win_streak": 0, "max_loss_streak": 0,
        "board_cyb_count": 0, "board_zb_count": 0,
        "month": month or None,
    }
    if total == 0:
        return _ok(empty)

    def _sum_avg(field):
        vals = [t.get(field) for t in trades if t.get(field) is not None]
        if not vals:
            return 0, 0, 0, 0
        s = round(sum(vals), 2)
        avg = round(s / len(vals), 2)
        wins = sum(1 for v in vals if v > 0)
        rate = round(wins / len(vals) * 100, 1)
        return s, avg, wins, rate

    sm, am, wm, rm = _sum_avg("pnl_morning")
    sa, aa, wa, ra = _sum_avg("pnl_afternoon")
    sb, ab, wb, rb = _sum_avg("pnl_batch_total")
    sbh = round(sum(t.get("pnl_batch_total_half") or 0 for t in trades if t.get("pnl_batch_total_half") is not None), 2)

    completed = sum(1 for t in trades if t.get("sell_price_935") is not None or t.get("sell_price_1315") is not None)
    holding = total - completed
    sb_count = sum(1 for t in trades if t.get("is_second_board") is True)
    sb_known = sum(1 for t in trades if t.get("is_second_board") is not None)

    # 本月三类盈亏：半仓口径
    # 早盘/午盘：取一半列之和；分批：取分批半仓盈亏之和
    month_pnl_morning_half = round(sum(t.get("pnl_morning_half") or 0 for t in trades if t.get("pnl_morning_half") is not None), 2)
    month_pnl_afternoon_half = round(sum(t.get("pnl_afternoon_half") or 0 for t in trades if t.get("pnl_afternoon_half") is not None), 2)
    month_pnl_batch_half = round(sum(t.get("pnl_batch_total_half") or 0 for t in trades if t.get("pnl_batch_total_half") is not None), 2)
    # 分批胜率按分批半仓盈亏
    rb = 0
    batch_half_vals = [t.get("pnl_batch_total_half") for t in trades if t.get("pnl_batch_total_half") is not None]
    if batch_half_vals:
        rb = round(sum(1 for v in batch_half_vals if v > 0) / len(batch_half_vals) * 100, 1)

    # 板块数量（2026-10-26 用户要求）：取当前月份（已按 month 过滤）的记录
    # board 由 stock_meta.board_of_code 判定并写入；缺失时按代码兜底重算
    board_cyb = board_zb = 0
    for t in trades:
        b = t.get("board")
        if not b:
            code = str(t.get("stock_code") or "").strip()
            b = stock_meta.board_of_code(code) if code else ""
        if b == stock_meta.BOARD_CHUANGYEBAN:
            board_cyb += 1
        elif b == stock_meta.BOARD_ZHUBAN:
            board_zb += 1

    max_win_streak = max_loss_streak = current_win = current_loss = 0
    for t in sorted(trades, key=lambda x: (str(x.get("date") or ""), int(x.get("id") or 0))):
        pnl = t.get("pnl_batch_total")
        if pnl is None:
            current_win = current_loss = 0
            continue
        if pnl > 0:
            current_win += 1
            current_loss = 0
            max_win_streak = max(max_win_streak, current_win)
        elif pnl < 0:
            current_loss += 1
            current_win = 0
            max_loss_streak = max(max_loss_streak, current_loss)
        else:
            current_win = current_loss = 0

    return _ok({
        "total": total,
        "completed": completed,
        "holding": holding,
        "win_rate_morning": rm,
        "win_rate_afternoon": ra,
        "win_rate_batch": rb,
        "sum_pnl_morning": sm,
        "sum_pnl_afternoon": sa,
        "sum_pnl_batch": sb,
        "sum_pnl_batch_half": sbh,
        "avg_pnl_morning": am,
        "avg_pnl_afternoon": aa,
        "avg_pnl_batch": ab,
        "second_board_count": sb_count,
        "second_board_rate": round(sb_count / sb_known * 100, 1) if sb_known else 0,
        "month_pnl_morning_half": month_pnl_morning_half,
        "month_pnl_afternoon_half": month_pnl_afternoon_half,
        "month_pnl_batch_half": month_pnl_batch_half,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "board_cyb_count": board_cyb,
        "board_zb_count": board_zb,
        "month": month or None,
    })


@app.route("/api/months", methods=["GET"])
def api_months():
    program = request.args.get("program", DEFAULT_PROGRAM).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    with _data_lock:
        trades = _load_all_trades(program)
    months = sorted({str(t.get("date") or "")[:7] for t in trades if t.get("date")}, reverse=True)
    # 北京时间当前月始终排第一（即使尚无当月成交）
    cur = datetime.now().strftime("%Y-%m")
    months = [cur] + [m for m in months if m != cur]
    return _ok(months)


def _empty_board_bucket() -> dict:
    return {
        "morning": 0.0,
        "morning_half": 0.0,
        "afternoon": 0.0,
        "afternoon_half": 0.0,
        "batch": 0.0,
        "batch_half": 0.0,
        "count": 0,
    }


def _accumulate_month_buckets(trades: list) -> dict[str, dict]:
    """按买入月汇总全仓 + 半仓盈亏。"""
    by_month: dict[str, dict] = {}
    for t in trades:
        d = str(t.get("date") or "")
        if len(d) < 7:
            continue
        month = d[:7]
        bucket = by_month.setdefault(month, {**_empty_board_bucket()})
        bucket["count"] += 1
        for field, key in (
            ("pnl_morning", "morning"),
            ("pnl_morning_half", "morning_half"),
            ("pnl_afternoon", "afternoon"),
            ("pnl_afternoon_half", "afternoon_half"),
            ("pnl_batch_total", "batch"),
            ("pnl_batch_total_half", "batch_half"),
        ):
            if t.get(field) is not None:
                bucket[key] += float(t[field])

    for bucket in by_month.values():
        for key in ("morning", "morning_half", "afternoon", "afternoon_half", "batch", "batch_half"):
            bucket[key] = round(bucket[key], 2)
    return by_month


def _aggregate_rank_by_month(program: str, rank: int) -> dict[str, dict]:
    """按月份汇总全仓 + 半仓口径盈亏累计。"""
    with _data_lock:
        trades = _load_all_trades(program)
    trades = [t for t in trades if int(t.get("rank") or 0) == rank]
    return _accumulate_month_buckets(trades)


def _eff_board_of_trade(t: dict) -> str:
    b = t.get("board")
    if not b:
        b = stock_meta.board_of_code(t.get("stock_code", ""))
    return b or ""


def _aggregate_board_rank_by_month(program: str, board: str, board_rank: int) -> dict[str, dict]:
    """板块历史看板：只统计该板块 Top2（board_rank）回填记录。"""
    with _data_lock:
        trades = _load_all_trades(program)
    trades = [
        t for t in trades
        if _eff_board_of_trade(t) == board
        and t.get("board_rank") is not None
        and int(t.get("board_rank") or 0) == board_rank
    ]
    return _accumulate_month_buckets(trades)


def _history_board_compare_rows(program: str, board: str = "") -> list[dict]:
    """第一名 / 第二名按月份对齐，供历史看板左右比对。board 非空时按板块 Top2。"""
    if board:
        r1 = _aggregate_board_rank_by_month(program, board, 1)
        r2 = _aggregate_board_rank_by_month(program, board, 2)
    else:
        r1 = _aggregate_rank_by_month(program, 1)
        r2 = _aggregate_rank_by_month(program, 2)
    months = sorted(set(r1.keys()) | set(r2.keys()), reverse=True)
    rows: list[dict] = []
    for month in months:
        rows.append({
            "month": month,
            "rank1": r1.get(month) or {**_empty_board_bucket()},
            "rank2": r2.get(month) or {**_empty_board_bucket()},
        })
    return rows


@app.route("/api/history-board", methods=["GET"])
def api_history_board():
    """
    历史看板：按月份并排对比第一名 / 第二名。
    query: program, board(可选 主板/创业板), page, page_size, year, month_from, month_to
    """
    program = request.args.get("program", DEFAULT_PROGRAM).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    board = request.args.get("board", "").strip()
    if board and board not in (stock_meta.BOARD_ZHUBAN, stock_meta.BOARD_CHUANGYEBAN):
        board = ""
    try:
        page = max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("page_size", "40") or 40)
    except ValueError:
        page_size = 40
    page_size = max(1, min(page_size, 200))

    year = request.args.get("year", "").strip()
    month_from = request.args.get("month_from", "").strip()
    month_to = request.args.get("month_to", "").strip()

    rows = _history_board_compare_rows(program, board=board)
    all_years = sorted({r["month"][:4] for r in rows if r.get("month")}, reverse=True)

    if year and re.match(r"^\d{4}$", year):
        rows = [r for r in rows if str(r.get("month") or "").startswith(year + "-")]
    if month_from and re.match(r"^\d{4}-\d{2}$", month_from):
        rows = [r for r in rows if str(r.get("month") or "") >= month_from]
    if month_to and re.match(r"^\d{4}-\d{2}$", month_to):
        rows = [r for r in rows if str(r.get("month") or "") <= month_to]

    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]

    return _ok({
        "program": program,
        "board": board or None,
        "rows": page_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "years": all_years,
    })


@app.route("/api/stock/lookup", methods=["GET"])
def api_stock_lookup():
    """按名称查代码（及模糊候选）。?name=黄河旋风"""
    name = request.args.get("name", "").strip()
    code_q = request.args.get("code", "").strip()
    if code_q:
        code = code_q.zfill(6)
        return _ok({"code": code, "name": resolve_name_by_code(code) or ""})
    if not name:
        return _err("请提供 name 或 code")
    code = resolve_code_by_name(name)
    name_map, _ = _load_stock_name_map()
    suggestions = [{"name": n, "code": c} for n, c in name_map.items() if name in n][:8]
    return _ok({"code": code, "name": name, "suggestions": suggestions})


@app.route("/api/trades/recalculate", methods=["POST"])
def api_recalculate():
    data = request.get_json(silent=True) or {}
    program = str(data.get("program", DEFAULT_PROGRAM)).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    n = recalculate_all_trades(program)
    return _ok({"recalculated": n})


@app.route("/api/export/csv", methods=["GET"])
def api_export_csv():
    month = request.args.get("month", "").strip()
    rank = request.args.get("rank", "").strip()
    program = request.args.get("program", DEFAULT_PROGRAM).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    with _data_lock:
        trades = _load_all_trades(program)
    trades.sort(key=lambda x: (str(x.get("date") or ""), int(x.get("id") or 0)), reverse=True)
    if month and re.match(r"^\d{4}-\d{2}$", month):
        trades = [t for t in trades if str(t.get("date") or "").startswith(month)]
    if rank:
        try:
            r = int(rank)
            trades = [t for t in trades if int(t.get("rank") or 0) == r]
        except ValueError:
            pass

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "程序", "买入日期", "代码", "股票名称", "板块", "买入价",
        "卖出价(早盘9:35)", "卖出价(午盘13:15)", "卖出价(早盘一半)", "卖出价(午盘一半)", "分批卖出总价",
        "卖出日期", "持仓天数",
        "早盘总盈亏%", "午盘总盈亏%", "早盘总盈亏一半%", "午盘总盈亏一半%",
        "早盘分批盈亏%", "午盘分批盈亏%", "分批总盈亏%", "分批总盈亏一半%",
        "二板", "排名", "跳过原因",
    ])
    for t in trades:
        writer.writerow([
            t.get("id", ""),
            t.get("program", ""),
            t.get("date", ""),
            t.get("stock_code", ""),
            t.get("stock_name", ""),
            t.get("board", ""),
            t.get("buy_price", ""),
            t.get("sell_price_935", ""),
            t.get("sell_price_1315", ""),
            t.get("sell_price_half_935", ""),
            t.get("sell_price_half_1315", ""),
            t.get("sell_price_batch_avg", ""),
            t.get("sell_date", ""),
            t.get("hold_days", ""),
            t.get("pnl_morning", ""),
            t.get("pnl_afternoon", ""),
            t.get("pnl_morning_half", ""),
            t.get("pnl_afternoon_half", ""),
            t.get("pnl_batch_morning", ""),
            t.get("pnl_batch_afternoon", ""),
            t.get("pnl_batch_total", ""),
            t.get("pnl_batch_total_half", ""),
            "是" if t.get("is_second_board") else ("否" if t.get("is_second_board") is False else ""),
            t.get("rank", ""),
            t.get("skip_reason", ""),
        ])
    output.seek(0)
    fname = f"trades_{program}_export.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.route("/api/dates", methods=["GET"])
def api_dates():
    program = request.args.get("program", DEFAULT_PROGRAM).strip()
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    with _data_lock:
        dates = [f[:-5] for f in _list_day_files(program)]
    return _ok(dates)


@app.route("/api/jobs/after-close", methods=["POST"])
def api_after_close_job():
    """手动触发收盘补全。body: {date?: YYYY-MM-DD, force?: bool, program?: str|all}"""
    data = request.get_json(silent=True) or {}
    as_of = (data.get("date") or "").strip() or None
    force = bool(data.get("force", False))
    program = str(data.get("program", DEFAULT_PROGRAM)).strip()
    if program == "all":
        from daily_close_job import run_after_close_job
        out = {}
        for p in VALID_PROGRAMS:
            try:
                out[p] = run_after_close_job(as_of, force=force, program=p)
            except Exception as e:
                out[p] = {"error": str(e)}
        return _ok(out)
    if program not in VALID_PROGRAMS:
        program = DEFAULT_PROGRAM
    try:
        from daily_close_job import run_after_close_job
        result = run_after_close_job(as_of, force=force, program=program)
        return _ok(result)
    except Exception as e:
        return _err(f"收盘补全失败: {e}", 500)


@app.route("/api/jobs/after-close/status", methods=["GET"])
def api_after_close_status():
    from daily_close_job import _load_state, get_morning_readiness_status
    state = _load_state()
    state["morning_readiness"] = get_morning_readiness_status()
    return _ok(state)


# ── 启动 ──────────────────────────────────────────────────

def find_free_port(start: int = 5050, max_attempts: int = 100) -> int:
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found")


def _lan_ips() -> list[str]:
    """本机局域网 IP，供手机访问。"""
    ips = []
    try:
        import subprocess
        for iface in ("en0", "en1", "eth0"):
            try:
                out = subprocess.check_output(
                    ["ipconfig", "getifaddr", iface], stderr=subprocess.DEVNULL, text=True
                ).strip()
                if out and out not in ips:
                    ips.append(out)
            except Exception:
                pass
    except Exception:
        pass
    if not ips:
        try:
            import socket as _s
            hostname = _s.gethostname()
            for info in _s.getaddrinfo(hostname, None, _s.AF_INET):
                ip = info[4][0]
                if ip and not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
    return ips


if __name__ == "__main__":
    # 确保三个程序的数据目录都存在
    for p in VALID_PROGRAMS:
        _trades_dir(p)
        print(f"[每日交易记录] 数据目录: {_trades_dir(p)}")

    try:
        from daily_close_job import start_close_scheduler, start_morning_scheduler
        start_close_scheduler()
        start_morning_scheduler()
    except Exception as e:
        print(f"[每日交易记录] 调度器启动失败: {e}")

    def _startup_purge():
        try:
            n = purge_non_trading_trade_days()
            if n:
                print(f"[每日交易记录] 已清理非交易日 trades: {len(n)} 个文件")
        except Exception as e:
            print(f"[每日交易记录] 非交易日清理失败: {e}")

    threading.Thread(target=_startup_purge, daemon=True, name="purge-non-trading").start()

    def _warmup_stock_map():
        try:
            _load_stock_name_map()
            print("[每日交易记录] 股票码表预热完成")
        except Exception as e:
            print(f"[每日交易记录] 股票码表预热失败: {e}")

    threading.Thread(target=_warmup_stock_map, daemon=True, name="stock-map-warmup").start()

    def _warmup_trade_cache():
        # 沙箱内单次 open() ≈1.8ms，843 个 trades 文件冷读 ≈2.5s。
        # 若不预热，服务重启后首次点击必然等数秒；预热后 API 只读内存（≈3ms）。
        try:
            total_t = time.perf_counter()
            warm_all_caches()
            print(f"[每日交易记录] 交易缓存预热完成，总耗时 {(time.perf_counter() - total_t) * 1000:.0f}ms")
        except Exception as e:
            print(f"[每日交易记录] 交易缓存预热失败: {e}")

    threading.Thread(target=_warmup_trade_cache, daemon=True, name="trade-cache-warmup").start()

    port = find_free_port(5050)
    # 0.0.0.0：同一 Wi-Fi 下手机可访问
    local_url = f"http://127.0.0.1:{port}"
    lan_list = _lan_ips()
    print(f"[每日交易记录] 本机打开: {local_url}")
    for ip in lan_list:
        print(f"[每日交易记录] 手机打开: http://{ip}:{port}  （需与电脑同一 Wi-Fi）")
    if not lan_list:
        print("[每日交易记录] 未检测到局域网 IP，可在「系统设置 → 网络」查看后再用 http://<你的IP>:端口 访问")
    print("[每日交易记录] 已挂载 A股交易日 7:50 up + 8:50 stock+slow+up + 9:30 六Tab落盘 + 15:00 收盘补全(含水下)")
    threading.Timer(1.0, lambda: webbrowser.open(local_url)).start()
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
