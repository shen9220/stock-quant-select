#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主板 / 创业板 tab 历史数据补全脚本（首板战法）。

背景
----
交易记录原本每天只存「全市场综合得分 Top2」（rank 1/2）。主板 / 创业板 tab
需要的是「该板块自己的 Top2」。

权威口径（用户要求，2026-10-30）
----
Stock-V1（默认原）的主板 / 创业板 **禁止另拉涨停池重算**。
最底层数据源 = `stock-V1/stock-V1（默认原）/results/*_recommendations.md`
（含「排名前二」+「其他未入选」全量综合得分）：

1. 若 V1 原全局 Top1/Top2 本身就是主板（或创业板），则该板 board_rank
   必须与全局完全一致（例：10-08 昭衍新药 / 南华生物）。
2. 若全局 Top 全是另一板，则在同一份 results 里按综合得分从高到低，
   筛出该板最高分的两只（例：10-09 全局贝特利/领湃均为创业板 →
   主板取同文件评分最高的两只主板）。
3. results 缺失的日期：不臆造、不另源重跑；标记跳过，保留待有元数据再补。

水下(stock_updata) 仍走独立绿开逻辑（与本口径无关）。

铁律
----
1. stock：只读本地 results，零外部接口。
2. 水下：单线程串行 + sleep（避免限流）。
3. 幂等：已齐可跳过；`--force` / `--dates` 可强制按 results 重写。
4. 不破坏已有 rank 1/2 买卖盈亏；board_rank 按代码合并，并清掉错误旧板记录。

用法
----
  python3 backfill_board_records.py --program stock --limit 50
  python3 backfill_board_records.py --program stock --force --dates 2026-10-08,2026-10-09
  python3 backfill_board_records.py --program stock --from-results --force
  python3 backfill_board_records.py --program stock_updata --limit 15

进度状态写在 backfill_board_state.json（按 program 记录已处理到的日期游标）。
"""
from __future__ import annotations

import os
import sys

_JOBS_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_JOBS_DIR)
_LIB_DIR = os.path.join(_BASE_DIR, "lib")
for _p in (_BASE_DIR, _LIB_DIR, _JOBS_DIR, os.path.dirname(_BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import BASE_DIR, ROOT_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import argparse
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

import paths as pkg_paths  # noqa: E402  首板战法/公共/paths.py
import stock_meta  # noqa: E402  (板块判定唯一事实来源)
from stock_meta import board_of_code, BOARD_ZHUBAN, BOARD_CHUANGYEBAN  # noqa: E402

# 权威包布局：stock-V1/stock-V1（默认原）/stock.py（由 runtime_paths 加入 sys.path）
import stock as stock_mod  # noqa: E402  选股逻辑：涨停池 / 打分 / 交易日历
import stock_UI  # noqa: E402  (upsert_from_stock_py 写盘)
import board_data_sources as bds  # noqa: E402  多源涨停池/日线（仅水下历史用）
import price_service as ps  # noqa: E402

STATE_FILE = os.path.join(DATA_DIR, "backfill_board_state.json")
VALID_BOARDS = (BOARD_ZHUBAN, BOARD_CHUANGYEBAN)
PLACEHOLDER_NAME = "（空占位）"
PLACEHOLDER_SKIP = "当日该板候选不足，空占位（规范：第一名/第二名必占位）"

# results md 解析
_BUY_DAY_RE = re.compile(r"买入日\(D1\):\s*(\d{4}-\d{2}-\d{2})")
_RANK_BLOCK_RE = re.compile(
    r"排名\s+(\d+):\s+(\d{6})\s+-\s+(.+?)(?=\n排名\s+\d+:|\n===|\Z)",
    re.S,
)
_RANK_SCORE_RE = re.compile(r"综合得分:\s*([0-9.]+)")
_OTHER_SCORE_RE = re.compile(
    r"^\s*(\d{6})\s+-\s+(.+?):\s*综合得分\s+([0-9.]+)",
    re.M,
)


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ── 交易日历（复用 stock_UI 的判定，保持口径一致）──────────────────────
def is_trading_buy_date(d: str) -> bool:
    return bool(stock_UI._is_trading_buy_date(d))


def list_existing_dates(program: str) -> list[str]:
    """返回该 program 已存在交易记录的买入日（YYYY-MM-DD）倒序。"""
    files = stock_UI._list_day_files(program)
    dates = [f[:-5] for f in files if f.endswith(".json")]
    return sorted(dates, reverse=True)


def generate_trading_days(start_date: str, end_date: str) -> list[str]:
    """按本地交易日历生成 [start, end] 内的交易日（YYYY-MM-DD 倒序）。
    用于水下等「历史文件缺失」的程序：缺的月份没有文件，必须按日历补。"""
    try:
        d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
        d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return []
    out = []
    cur = d1
    while cur >= d0:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_buy_date(ds):
            out.append(ds)
        cur -= timedelta(days=1)
    return sorted(out, reverse=True)


def already_done(program: str, date_str: str) -> bool:
    """主板 & 创业板 各自必须同时有 board_rank 1 与 2（空占位也算齐）。"""
    trades = stock_UI._load_day(date_str, program)
    for board in VALID_BOARDS:
        ranks = {
            int(t.get("board_rank"))
            for t in trades
            if t.get("board") == board and t.get("board_rank") is not None
        }
        if 1 not in ranks or 2 not in ranks:
            return False
    return True


def _placeholder_rec(buy_date_str: str, board: str, board_rank: int) -> dict:
    return {
        "date": buy_date_str,
        "stock_code": "",
        "stock_name": PLACEHOLDER_NAME,
        "board": board,
        "board_rank": int(board_rank),
        "score": None,
        "is_board_placeholder": True,
        "skip_reason": PLACEHOLDER_SKIP,
    }


def pad_board_top2_slots(
    buy_date_str: str, board_top2: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    """每板补齐 board_rank 1、2；不足则空占位（禁止缺日缺名次）。"""
    out: dict[str, list[dict]] = {}
    for board in VALID_BOARDS:
        rows = list(board_top2.get(board) or [])
        # 按 board_rank 去重保序
        by_r: dict[int, dict] = {}
        for r in rows:
            br = int(r.get("board_rank") or 0)
            if br in (1, 2) and br not in by_r:
                by_r[br] = r
        filled = []
        for br in (1, 2):
            if br in by_r:
                filled.append(by_r[br])
            else:
                filled.append(_placeholder_rec(buy_date_str, board, br))
        out[board] = filled
    return out


_STATE_LOCK = os.path.join(BASE_DIR, "data", "backfill_board_state.lock")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(_STATE_LOCK), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def mark_attempted(program: str, dates: list[str]) -> None:
    """带文件锁合并 attempted，避免多轨并行丢更新。"""
    os.makedirs(os.path.dirname(_STATE_LOCK), exist_ok=True)
    fd = os.open(_STATE_LOCK, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = load_state()
        prog = state.get(program, {})
        attempted = set(prog.get("attempted") or [])
        attempted.update(dates)
        state[program] = {
            "attempted": sorted(attempted),
            "updated_at": datetime.now().isoformat(),
        }
        save_state(state)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def parse_v1_results_scored(path: str) -> tuple[str | None, list[dict]]:
    """解析 V1 原 recommendations.md 全量评分列表（排名前二 + 未入选）。

    返回 (买入日D1 或 None, [{stock_code, stock_name, score, global_rank?}, ...])，
    已按 score 降序；同码去重保留首次（更高分段）。
    """
    if not path or not os.path.isfile(path):
        return None, []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    buy_m = _BUY_DAY_RE.search(text)
    buy_day = buy_m.group(1) if buy_m else None

    scored: list[dict] = []
    seen: set[str] = set()

    head, _, tail = text.partition("=== 其他未入选")
    for m in _RANK_BLOCK_RE.finditer(head):
        code = m.group(2).zfill(6)
        name = m.group(3).split("\n", 1)[0].strip()
        sm = _RANK_SCORE_RE.search(m.group(0))
        if not sm:
            continue
        if code in seen:
            continue
        seen.add(code)
        scored.append({
            "stock_code": code,
            "stock_name": name,
            "score": float(sm.group(1)),
            "global_rank": int(m.group(1)),
        })

    for m in _OTHER_SCORE_RE.finditer(tail):
        code = m.group(1).zfill(6)
        if code in seen:
            continue
        seen.add(code)
        scored.append({
            "stock_code": code,
            "stock_name": m.group(2).strip(),
            "score": float(m.group(3)),
            "global_rank": None,
        })

    scored.sort(key=lambda x: (-x["score"], x["stock_code"]))
    return buy_day, scored


def find_v1_results_md_for_buy_date(buy_date_str: str) -> str | None:
    """买入日 D1 → 对应 D0 的 results/{D0}_recommendations.md。"""
    d0 = ps.prev_trading_day(buy_date_str)
    if not d0:
        try:
            d1 = datetime.strptime(buy_date_str, "%Y-%m-%d")
            d0_dt = stock_mod.get_previous_trading_day(d1)
            d0 = d0_dt.strftime("%Y-%m-%d")
        except Exception:
            return None
    path = pkg_paths.recommendation_md("stock", d0)
    if os.path.isfile(path):
        return path
    for d in pkg_paths.legacy_results_dirs("stock"):
        cand = os.path.join(d, f"{d0.replace('-', '')}_recommendations.md")
        if os.path.isfile(cand):
            return cand
    return None


def list_buy_dates_with_v1_results() -> list[str]:
    """扫描 V1 原 results，返回有元数据的买入日（倒序）。"""
    results_dir = pkg_paths.program_results_dir("stock")
    out: list[str] = []
    if not os.path.isdir(results_dir):
        return out
    for name in os.listdir(results_dir):
        if not name.endswith("_recommendations.md"):
            continue
        path = os.path.join(results_dir, name)
        buy_day, scored = parse_v1_results_scored(path)
        if buy_day and scored:
            out.append(buy_day)
            continue
        # 无买入日头：用文件名 D0 推下一交易日
        d0_8 = name.split("_", 1)[0]
        if len(d0_8) == 8 and d0_8.isdigit():
            d0 = f"{d0_8[:4]}-{d0_8[4:6]}-{d0_8[6:]}"
            d1 = ps.next_trading_day(d0)
            if d1:
                out.append(d1)
    return sorted(set(out), reverse=True)


# 买入日停牌，买点错过。已核对，选股必须跳过，下一名顺延。
CONFIRMED_BUY_DAY_HALT = {
    ("300986", "2026-01-13"),  # 志特新材，1月16日才开盘
    ("300333", "2026-07-30"),  # 兆日科技
    ("603221", "2026-08-11"),  # 爱丽家居
}
HALT_SKIP_REASON = "买入日停牌，错过买点，忽略并顺延下一名"


def _is_lianban_on_d0(code: str, buy_date: str) -> bool:
    """D0-1 涨停则是连板，选股必须跳过。日线不够时不在这里删已有记录。"""
    d0 = ps.prev_trading_day(buy_date)
    d0_prev = ps.prev_trading_day(d0) if d0 else None
    if not d0 or not d0_prev:
        return False
    try:
        from first_board_rule import classify_first_board, load_bars
    except Exception:
        return False
    return classify_first_board(code, d0, d0_prev, load_bars()) == "lianban"


def is_buy_day_halt(code: str, buy_date: str, *, probe: bool = False) -> bool:
    """买入日停牌才返回 True。接口失败不算停牌。"""
    key = (str(code).zfill(6), str(buy_date)[:10])
    if key in CONFIRMED_BUY_DAY_HALT:
        return True
    if not probe:
        return False
    return _probe_buy_day_halt(key[0], key[1])


def _probe_buy_day_halt(code: str, day: str) -> bool:
    """搜狐当日 K 线：有行且成交量为 0，或明确空行情，才算停牌。"""
    import requests

    d8 = day.replace("-", "")
    url = (
        f"https://q.stock.sohu.com/hisHq?code=cn_{code}"
        f"&start={d8}&end={d8}&stat=1&order=D&period=d"
    )
    try:
        from platform_gate import platform_slot
        with platform_slot("sohu"):
            r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            payload = r.json()
    except Exception:
        return False
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return False
    hq = payload[0].get("hq") or []
    if not hq:
        return True
    row = hq[0]
    try:
        volume = float(row[7]) if len(row) > 7 else None
    except (TypeError, ValueError):
        return False
    return volume == 0


def slot_needs_halt_promote(program: str, buy_date: str, *, probe: bool = False) -> bool:
    """板块名次上仍坐着买入日停牌票时，要改写并顺延。"""
    trades = stock_UI._load_day(buy_date, program)
    for t in trades:
        if t.get("board_rank") is None or t.get("is_board_placeholder"):
            continue
        code = str(t.get("stock_code") or "").strip()
        if code and is_buy_day_halt(code, buy_date, probe=probe):
            return True
    return False


def board_top2_from_scored(
    buy_date_str: str,
    scored: list[dict],
    *,
    probe_suspend: bool = False,
) -> dict[str, list[dict]]:
    """按综合得分从高到低，筛主板/创业板各 Top2；买入日停牌顺延；不足 2 只则空占位补齐。"""
    result: dict[str, list[dict]] = {b: [] for b in VALID_BOARDS}
    skipped: list[str] = []
    for row in scored:
        code = str(row["stock_code"]).zfill(6)
        board = board_of_code(code)
        if board not in result:
            continue
        if len(result[board]) >= 2:
            continue
        if is_buy_day_halt(code, buy_date_str, probe=probe_suspend):
            skipped.append(f"{code} {row.get('stock_name')} 买入日停牌")
            continue
        if _is_lianban_on_d0(code, buy_date_str):
            skipped.append(f"{code} {row.get('stock_name')} 连板")
            continue
        result[board].append({
            "date": buy_date_str,
            "stock_code": code,
            "stock_name": str(row.get("stock_name") or ""),
            "board": board,
            "board_rank": len(result[board]) + 1,
            "score": float(row.get("score") or 0),
            "is_board_placeholder": False,
        })
        if all(len(result[b]) >= 2 for b in VALID_BOARDS):
            break
    if skipped:
        log(f"  [{buy_date_str}] 买入日停牌忽略: {', '.join(skipped)}")
    return pad_board_top2_slots(buy_date_str, result)


# ── 核心：对某个买入日 D1，从 V1 原 results 按板块取 Top2 ───────────────
def build_board_top2(
    program: str,
    buy_date_str: str,
    *,
    zt_prefer: str = "em",
    hist_prefer: str = "tx",
) -> dict[str, list[dict]]:
    """
    返回 { "主板": [rec1, rec2], "创业板": [rec1, rec2] }。

    Stock-V1：只读默认原 results md（全量评分），禁止另拉涨停池。
    zt_prefer / hist_prefer 保留兼容旧调用，stock 路径忽略。
    """
    _ = (program, zt_prefer, hist_prefer)
    path = find_v1_results_md_for_buy_date(buy_date_str)
    if not path:
        log(f"  [{buy_date_str}] 无 V1 原 results（D0 md 缺失），不臆造")
        return {}
    buy_in_md, scored = parse_v1_results_scored(path)
    if not scored:
        log(f"  [{buy_date_str}] results 无评分行: {os.path.basename(path)}")
        return {}
    if buy_in_md and buy_in_md != buy_date_str:
        log(f"  [{buy_date_str}] 警告: md 买入日={buy_in_md} 与请求不一致 "
            f"({os.path.basename(path)})，仍按请求日写入")
    # 历史日只跳过已确认停牌，避免全量重跑打接口。当日收盘后才探停牌。
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    probe = (
        buy_date_str == today
        and (now.hour > 15 or (now.hour == 15 and now.minute >= 30))
    )
    top2 = board_top2_from_scored(buy_date_str, scored, probe_suspend=probe)
    zb = top2.get(BOARD_ZHUBAN) or []
    cy = top2.get(BOARD_CHUANGYEBAN) or []

    def _fmt(rows: list[dict]) -> str:
        bits = []
        for x in rows:
            if x.get("is_board_placeholder"):
                bits.append(f"空占位# {x.get('board_rank')}")
            else:
                bits.append(f"{x['stock_name']}({x.get('score')})")
        return ", ".join(bits) or "-"

    log(f"  [{buy_date_str}] 源={os.path.basename(path)} 主板=[{_fmt(zb)}] 创业板=[{_fmt(cy)}]")
    return top2


# ── 水下(stock_updata) 历史补全 ─────────────────────────────────────────
def _fast_score(row) -> float:
    """与 stock_updata.calculate_fast_score 完全一致（沿用现有口径，不改逻辑）。"""
    zf = float(row.get("涨跌幅") or 0) * 0.5
    cj = (float(row.get("成交额") or 0) / 1e8) * 0.25
    hs = float(row.get("换手率") or 0) * 0.25
    return zf + cj + hs


_BS_STATE = {"login": False}


def _bs_login() -> bool:
    """baostock 登录（复用连接，失败抛异常由上层停批——防止黑名单期间把日期空跑标记掉）。"""
    if _BS_STATE["login"]:
        return True
    try:
        import baostock as bs
        lg = bs.login()
        ok = (lg.error_code == "0")
        if not ok:
            _BS_STATE["login"] = False
            raise RuntimeError(f"baostock 登录失败: {getattr(lg, 'errormsg', '') or lg.error_code}（疑似限流/黑名单，本批停止，不标记日期）")
        _BS_STATE["login"] = True
        return True
    except RuntimeError:
        raise
    except Exception as e:
        _BS_STATE["login"] = False
        raise RuntimeError(f"baostock 不可用: {e}（本批停止，不标记日期）")


def _fetch_hist_open_pct(code: str, d0_str: str, d1_str: str):
    """历史「今开涨跌幅」= (D1开盘 - D0收盘) / D0收盘 * 100。
    用 baostock 日线一次取 D0+D1 两根（数据源与主流程一致，东财接口被代理拦截不可用）。"""
    if not _bs_login():
        return None
    try:
        import baostock as bs
        if code.startswith(("6", "9")):
            full = "sh." + code
        elif code.startswith(("4", "8")):
            full = "bj." + code
        else:
            full = "sz." + code
        rs = bs.query_history_k_data_plus(
            full, "date,open,close",
            start_date=d0_str[:4] + "-" + d0_str[4:6] + "-" + d0_str[6:],
            end_date=d1_str[:4] + "-" + d1_str[4:6] + "-" + d1_str[6:],
            frequency="d", adjustflag="3")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if len(rows) < 2:
            return None
        prev_close = float(rows[0][2])
        open_p = float(rows[-1][1])
        if not prev_close:
            return None
        return (open_p - prev_close) / prev_close * 100
    except Exception as e:
        log(f"  [水下] {code} baostock 日线失败: {e}")
        return None


def build_board_top2_updata(buy_date_str: str) -> dict[str, list[dict]]:
    """水下历史补全：首板池按 fast_score 排序 → 逐只取历史今开（禁批量、单只串行
    + sleep 间隔）→ 绿开(pct<0)按板块各自取 Top2，某板块凑齐 2 只即不再为其请求。"""
    d1 = datetime.strptime(buy_date_str, "%Y-%m-%d")
    d0 = stock_mod.get_previous_trading_day(d1)
    prev = stock_mod.get_previous_trading_day(d0)
    d0_str, d1_str = d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")

    # 前置检查：baostock 不可用（黑名单/限流）直接抛异常停批，避免整批日期被空跑标记
    _bs_login()

    stock_mod.clear_zt_pool_cache()
    zt_df = stock_mod.fetch_zt_pool_em(d0_str)
    if zt_df is None or zt_df.empty or "代码" not in zt_df.columns:
        return {}
    prev_zt = stock_mod.fetch_zt_pool_em(prev.strftime("%Y%m%d"))
    prev_codes = stock_mod._zt_pool_codes(prev_zt) if prev_zt is not None else set()
    pool = zt_df[~zt_df["代码"].isin(prev_codes)] if prev_codes else zt_df
    pool = pool[
        ~pool["代码"].astype(str).str.startswith(("688", "92"))
        & ~pool["名称"].astype(str).str.contains("ST|st", case=False, na=False)
    ]
    if pool.empty:
        return {}
    pool = pool.copy()
    pool["综合得分"] = pool.apply(_fast_score, axis=1)
    pool = pool.sort_values("综合得分", ascending=False)

    result: dict[str, list[dict]] = {b: [] for b in VALID_BOARDS}
    need = {b: 2 for b in VALID_BOARDS}
    scanned = 0
    for _, row in pool.iterrows():
        if all(v <= 0 for v in need.values()) or scanned >= 40:
            break
        scanned += 1
        code = str(row["代码"]).zfill(6)
        board = board_of_code(code)
        if board not in need or need[board] <= 0:
            continue  # 该板块已凑齐，不再为它发请求（省调用）
        try:
            pct = _fetch_hist_open_pct(code, d0_str, d1_str)
        except Exception as e:
            log(f"  [水下] {code} 历史今开失败: {e}")
            pct = None
        time.sleep(0.8)  # 铁律：单只串行 + 间隔
        if pct is None or pct >= 0:
            continue
        need[board] -= 1
        result[board].append({
            "date": buy_date_str,
            "stock_code": code,
            "stock_name": str(row.get("名称", "")),
            "board": board,
            "board_rank": 2 - need[board],
        })
    return result


def upsert_board_records(program: str, board_top2: dict[str, list[dict]]) -> dict:
    """写入板块 Top2（含空占位），并纠正旧错误 board_rank。

    - 每板必须同时有 board_rank 1 与 2（不足则空占位）
    - 真票走 upsert_from_stock_py；占位票直接写盘（无六位代码）
    - 同板其它旧 board_rank：清除；纯板孤儿删除
    """
    # 取买入日
    day = ""
    for board in VALID_BOARDS:
        for rec in board_top2.get(board) or []:
            day = str(rec.get("date") or "").strip()
            if day:
                break
        if day:
            break
    if not day:
        return {"created": 0, "updated": 0, "board_rank_set": 0,
                "board_rank_cleared": 0, "orphans_removed": 0,
                "placeholders": 0}

    board_top2 = pad_board_top2_slots(day, board_top2)

    real_records = []
    by_code: dict[str, dict] = {}
    desired: dict[tuple[str, int], dict] = {}
    for board in VALID_BOARDS:
        for rec in board_top2.get(board) or []:
            br = int(rec["board_rank"])
            desired[(board, br)] = rec
            code = str(rec.get("stock_code") or "").strip()
            if rec.get("is_board_placeholder") or not code or not re.match(r"^\d{6}$", code.zfill(6)):
                continue
            code = code.zfill(6)
            by_code[code] = rec
            real_records.append({
                "date": day,
                "stock_code": code,
                "stock_name": rec.get("stock_name") or "",
                "board": board,
            })

    if real_records:
        res = stock_UI.upsert_from_stock_py(
            real_records, auto_enrich=False, program=program)
    else:
        res = {"created": 0, "updated": 0}

    trades = stock_UI._load_day(day, program)
    cleared = 0
    orphans = 0
    placeholders = 0
    kept: list[dict] = []
    used_slots: set[tuple[str, int]] = set()

    for t in trades:
        code = str(t.get("stock_code") or "").strip()
        code6 = code.zfill(6) if code else ""
        is_ph = bool(t.get("is_board_placeholder")) or (
            t.get("board_rank") is not None and (not code or not re.match(r"^\d{6}$", code6))
        )

        if code6 in by_code:
            want = by_code[code6]
            br = int(want["board_rank"])
            board = want.get("board") or board_of_code(code6)
            t["board_rank"] = br
            if board:
                t["board"] = board
            t["is_board_placeholder"] = False
            if PLACEHOLDER_SKIP in str(t.get("skip_reason") or ""):
                t["skip_reason"] = None
            used_slots.add((board, br))
            kept.append(t)
            continue

        if t.get("board_rank") is not None:
            board = t.get("board") or board_of_code(code6)
            br = int(t.get("board_rank"))
            slot = (board, br) if board else None
            # 旧占位：若该槽已有真票目标，丢掉旧占位
            if is_ph:
                if slot and slot in desired and not desired[slot].get("is_board_placeholder"):
                    orphans += 1
                    continue
                if slot and slot in desired and desired[slot].get("is_board_placeholder"):
                    # 保留并规范化占位
                    t["stock_code"] = ""
                    t["stock_name"] = PLACEHOLDER_NAME
                    t["board"] = board
                    t["board_rank"] = br
                    t["is_board_placeholder"] = True
                    t["skip_reason"] = PLACEHOLDER_SKIP
                    for k in (
                        "buy_price", "sell_price_935", "sell_price_1315",
                        "pnl_morning", "pnl_afternoon", "rank",
                    ):
                        t[k] = None
                    used_slots.add(slot)
                    placeholders += 1
                    kept.append(t)
                    continue
                orphans += 1
                continue
            has_global = t.get("rank") is not None
            if not has_global:
                orphans += 1
                continue
            t["board_rank"] = None
            cleared += 1
        kept.append(t)

    # 补尚未占用的槽位（真票应已在 kept；缺的是占位）
    next_id = stock_UI._next_global_id(program, stock_UI._load_all_trades(program))
    # 用 kept 内最大 id 续号更稳
    for t in kept:
        try:
            next_id = max(next_id, int(t.get("id") or 0) + 1)
        except Exception:
            pass
    for slot, rec in desired.items():
        if slot in used_slots:
            continue
        board, br = slot
        if rec.get("is_board_placeholder") or not str(rec.get("stock_code") or "").strip():
            kept.append(stock_UI.empty_trade(
                id=next_id,
                date=day,
                stock_code="",
                stock_name=PLACEHOLDER_NAME,
                program=program,
                board=board,
                board_rank=br,
                is_board_placeholder=True,
                skip_reason=PLACEHOLDER_SKIP,
            ))
            next_id += 1
            placeholders += 1
            used_slots.add(slot)
            res["created"] = int(res.get("created") or 0) + 1
        else:
            # 真票未匹配到（异常）：再走一遍按码查找失败时仍写占位兜底防缺日
            kept.append(stock_UI.empty_trade(
                id=next_id,
                date=day,
                stock_code="",
                stock_name=PLACEHOLDER_NAME,
                program=program,
                board=board,
                board_rank=br,
                is_board_placeholder=True,
                skip_reason=PLACEHOLDER_SKIP + "（真票落盘未命中）",
            ))
            next_id += 1
            placeholders += 1

    stock_UI._save_day(day, kept, program)
    res["board_rank_set"] = len(used_slots)
    res["board_rank_cleared"] = cleared
    res["orphans_removed"] = orphans
    res["placeholders"] = placeholders
    return res


def list_todo_dates(
    program: str = "stock",
    start_date: str = "2026-01-01",
    *,
    force: bool = False,
    from_results: bool = False,
) -> list[str]:
    """倒序待补买入日列表。

    stock + from_results：只处理有 V1 原 results 的买入日。
    force：忽略 already_done / attempted，强制按 results 重写。
    """
    state = load_state()
    prog_state = state.get(program, {})
    attempted = set(prog_state.get("attempted") or [])
    today_str = datetime.now().strftime("%Y-%m-%d")

    if program == "stock" and from_results:
        dates = [d for d in list_buy_dates_with_v1_results() if d >= start_date]
    else:
        existing = list_existing_dates(program)
        latest = existing[0] if existing else today_str
        generated = generate_trading_days(start_date, latest)
        dates = sorted(set(existing) | set(generated), reverse=True)
        dates = [d for d in dates if d >= start_date]
        legacy_cursor = prog_state.get("cursor")
        if legacy_cursor and not attempted and not force:
            attempted = {d for d in dates
                         if d >= legacy_cursor
                         and not (d == today_str and not already_done(program, d))}

    if force:
        return dates
    return [d for d in dates if d not in attempted and not already_done(program, d)]


def process_one_day(
    program: str,
    d: str,
    *,
    zt_prefer: str = "em",
    hist_prefer: str = "tx",
) -> tuple[bool, str]:
    """
    处理单日。返回 (should_mark_attempted, message)。
    stock：无 V1 results 不标记（等有元数据再补）；水下：无产出历史日可标记。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    if program == "stock":
        board_top2 = build_board_top2(
            program, d, zt_prefer=zt_prefer, hist_prefer=hist_prefer)
        if not board_top2:
            msg = f"[skip] {d} 无 V1 原 results 元数据"
            log(msg)
            return False, msg  # 不标记 attempted，有 md 后再补
    else:
        board_top2 = build_board_top2_updata(d)
        if not board_top2:
            msg = f"[skip] {d} 无首板候选/无绿开"
            log(msg)
            if d == today_str:
                return False, msg
            return True, msg
    res = upsert_board_records(program, board_top2)
    msg = (f"[ok] {d} 主板 {len(board_top2.get(BOARD_ZHUBAN, []))} / "
           f"创业板 {len(board_top2.get(BOARD_CHUANGYEBAN, []))} "
           f"→ 新建 {res.get('created', 0)} 更新 {res.get('updated', 0)} "
           f"清旧rank {res.get('board_rank_cleared', 0)} "
           f"删孤儿 {res.get('orphans_removed', 0)}")
    log(msg)
    return True, msg


def run(
    program: str,
    limit: int,
    sleep_s: float,
    dry_run: bool,
    start_date: str = "2026-01-01",
    *,
    zt_prefer: str = "em",
    hist_prefer: str = "tx",
    only_dates: list[str] | None = None,
    force: bool = False,
    from_results: bool = False,
):
    if only_dates is not None:
        todo = only_dates
    else:
        # stock 默认只扫有 results 的买入日，避免无元数据日空跑
        use_results = from_results or program == "stock"
        todo = list_todo_dates(
            program, start_date, force=force, from_results=use_results)

    log(f"[{program}] 待处理 {len(todo)} 天，本批上限 {limit} "
        f"force={force} from_results={from_results or program == 'stock'}")

    if dry_run:
        log("[dry-run] 仅统计，不写盘。")
        for d in todo[:limit]:
            path = find_v1_results_md_for_buy_date(d) if program == "stock" else None
            log(f"  would process {d} src={os.path.basename(path) if path else '-'}")
        return

    done_this_run = 0
    consec_err = 0
    marked: list[str] = []
    for d in todo:
        if done_this_run >= limit:
            log(f"已达本批上限 {limit}，停止；下次续跑。")
            break
        try:
            should_mark, _ = process_one_day(
                program, d, zt_prefer=zt_prefer, hist_prefer=hist_prefer)
            consec_err = 0
            if should_mark:
                marked.append(d)
            done_this_run += 1
        except Exception as e:
            consec_err += 1
            log(f"[err] {d} 处理失败：{e}（不标记，下次重试）")
            if consec_err >= 3:
                log("连续 3 次失败，疑似接口异常，停止本批，下次续跑。")
                break
            continue
        # stock 纯本地 md，无需长 sleep；水下仍要间隔
        if program != "stock" and sleep_s > 0:
            time.sleep(sleep_s)
        elif program == "stock" and sleep_s > 0:
            time.sleep(min(sleep_s, 0.05))

    if marked:
        mark_attempted(program, marked)
    remaining = len(list_todo_dates(
        program, start_date,
        force=False,
        from_results=(from_results or program == "stock"),
    ))
    log(f"本批完成 {done_this_run} 天，剩余待补约 {remaining} 天。")


def repair_missing_prices(program: str, limit: int, sleep_s: float) -> int:
    """对已带 board_rank 但缺买入价的板块记录补价（baostock 黑名单恢复后数据自愈用）。
    单只串行 + sleep；每天只补 limit 个日期，宁小勿大。"""
    fixed = 0
    for d in list_existing_dates(program):   # 倒序：新的先补
        if fixed >= limit:
            break
        trades = stock_UI._load_day(d, program)
        targets = [t for t in trades
                   if t.get("board_rank") is not None and t.get("buy_price") is None]
        if not targets:
            continue
        for t in targets:
            try:
                stock_UI.enrich_trade_prices(t, force=False)
            except Exception as e:
                log(f"  [repair] {d} {t.get('stock_code')} 补价失败: {e}")
            stock_UI.auto_calculate(t)
            time.sleep(0.8)   # 铁律：单只串行 + 间隔
        stock_UI._save_day(d, trades, program)
        log(f"[repair] {program} {d} 补价 {len(targets)} 条")
        fixed += 1
        time.sleep(sleep_s)
    return fixed


def stock_backfill_done(start_date: str = "2026-01-01") -> bool:
    """stock 是否已补完：目标窗口内每个交易日都已尝试或已补齐（当天允许晚一天）。"""
    st = load_state().get("stock", {})
    attempted = set(st.get("attempted") or [])
    today_str = datetime.now().strftime("%Y-%m-%d")
    existing = [d for d in list_existing_dates("stock") if d >= start_date]
    latest = existing[0] if existing else today_str
    dates = [d for d in (set(existing) | set(generate_trading_days(start_date, latest)))
             if d >= start_date]
    for d in sorted(dates, reverse=True):
        if d in attempted or already_done("stock", d) or d == today_str:
            continue
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="主板/创业板 tab 历史数据补全")
    ap.add_argument("--program", default="stock", choices=["stock", "stock_updata"])
    ap.add_argument("--limit", type=int, default=5, help="每批处理天数上限（默认5；stock 可加大）")
    ap.add_argument("--sleep", type=float, default=1.2, help="每个日期之间的间隔(秒)")
    ap.add_argument("--start", default="2026-01-01", help="日历生成起始日（水下缺文件月份靠它补齐）")
    ap.add_argument("--chain", action="store_true",
                    help="先补 stock，补完自动转 stock_updata（定时任务用）")
    ap.add_argument("--repair-prices", type=int, default=0, metavar="N",
                    help="补价自愈：给缺买入价的板块记录补价，每天最多 N 个日期")
    ap.add_argument("--dry-run", action="store_true", help="只统计待补数量，不写盘")
    ap.add_argument("--force", action="store_true",
                    help="强制重写（忽略 already_done / attempted）")
    ap.add_argument("--from-results", action="store_true",
                    help="只处理有 V1 原 results 的买入日（stock 默认即如此）")
    ap.add_argument("--dates", default="",
                    help="逗号分隔买入日，如 2026-10-08,2026-10-09")
    args = ap.parse_args()

    only_dates = None
    if args.dates.strip():
        only_dates = [x.strip() for x in args.dates.split(",") if x.strip()]

    if args.repair_prices:
        repair_missing_prices(args.program, args.repair_prices, args.sleep)
        return

    if args.chain:
        run("stock", args.limit, args.sleep, args.dry_run, args.start,
            force=args.force, from_results=True, only_dates=only_dates)
        if args.dry_run:
            return
        repair_missing_prices("stock", 2, args.sleep)
        if stock_backfill_done():
            log("stock 已补完 → 开始补水下(stock_updata)")
            run("stock_updata", args.limit, args.sleep, False, args.start)
            repair_missing_prices("stock_updata", 2, args.sleep)
        else:
            log("stock 尚未补完，水下按约定等待（先 stock 后水下）。")
        return

    run(args.program, args.limit, args.sleep, args.dry_run, args.start,
        force=args.force, from_results=args.from_results or args.program == "stock",
        only_dates=only_dates)


if __name__ == "__main__":
    main()
