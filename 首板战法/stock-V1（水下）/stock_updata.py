"""
stock_updata — 基于 stock.py 的「集合竞价水下（绿开）」变体。

流程（仅交易日早盘）:
  1) 9:24:40 开始扫描（正式集合竞价 9:25 开盘价落地）
  2) 严格用「今开 vs 昨收」判绿开（pct < 0）；禁止用现价冒充竞价
  3) 【铁律】腾讯实时行情禁止批量：强度序逐只请求，凑齐绿开 Top2 即停
  4) PushPlus 目标 9:28，【铁律】最迟 9:29 必须发送成功（失败重试）

用法:
  python3 stock_updata.py
  python3 stock_updata.py --force --no-notify
  python3 stock_updata.py --backfill 2026-09-01 2026-09-07
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path as _FirstBoardPath
_first_board_public = next((p / '公共/lib' for p in _FirstBoardPath(__file__).resolve().parents if (p / '公共/lib/first_board_gate.py').is_file()), None)
if _first_board_public is not None and str(_first_board_public) not in sys.path:
    sys.path.insert(0, str(_first_board_public))
import time as time_mod
from datetime import datetime, timedelta, time
from typing import Optional

import pandas as pd
from requests.exceptions import RequestException

# 与 stock.py 一致：用户 site-packages
_user_site = os.path.expanduser("~/.local/lib/python3.13/site-packages")
if _user_site not in sys.path:
    sys.path.insert(0, _user_site)

import akshare as ak  # noqa: E402

PROGRAM = "stock_updata"
# 正式集合竞价 9:25；提前约 20s 开始等开盘价
SCAN_START = time(9, 24, 40)
AUCTION_OPEN = time(9, 25, 0)
SCAN_SOFT_DEADLINE = time(9, 28, 0)
NOTIFY_DEADLINE = time(9, 29, 0)

# ── 腾讯实时行情铁律（禁止批量）──────────────────────────
# 每请求 1 只；请求间隔；探测今开是否落地的间隔
TX_SINGLE_SLEEP = 0.45
TX_PROBE_SLEEP = 1.5
TX_OPEN_RETRY = 3  # 单票今开未落地时最多再试几次
PUSH_RETRY_SLEEP = 2.0

# 早盘必须快：仅用涨停池字段打分（不做 30 日涨停基因/均线/PE）
SCORE_WEIGHTS = {"涨跌幅": 0.5, "成交额": 0.25, "换手率": 0.25}


def _retry_fetch(description, func, *args, max_attempts=5, base_delay=1.0, **kwargs):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except (RequestException, OSError) as e:
            last_err = e
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                print(f"{description} 失败（{type(e).__name__}），{wait:.1f}s 后重试 ({attempt}/{max_attempts})…")
                time_mod.sleep(wait)
    raise last_err


_ZT_POOL_CACHE: dict = {}
_ZT_POOL_COLUMNS = (
    "序号", "代码", "名称", "涨跌幅", "最新价", "成交额",
    "流通市值", "总市值", "换手率", "封板资金", "首次封板时间",
    "最后封板时间", "炸板次数", "涨停统计", "连板数", "所属行业",
)


def clear_zt_pool_cache():
    _ZT_POOL_CACHE.clear()


def _normalize_zt_pool(df):
    if df is None or (getattr(df, "empty", True) and "代码" not in getattr(df, "columns", [])):
        return pd.DataFrame(columns=list(_ZT_POOL_COLUMNS))
    return df


def _zt_pool_codes(df):
    if df is None or df.empty or "代码" not in df.columns:
        return set()
    return set(df["代码"].astype(str))


def fetch_zt_pool_em(date_str, max_attempts=5):
    if date_str in _ZT_POOL_CACHE:
        return _ZT_POOL_CACHE[date_str]
    df = _retry_fetch(
        f"涨停池 {date_str}",
        ak.stock_zt_pool_em,
        date=date_str,
        max_attempts=max_attempts,
        base_delay=1.0,
    )
    df = _normalize_zt_pool(df)
    _ZT_POOL_CACHE[date_str] = df
    return df


def _local_calendar_mod():
    records_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "交易记录UI"
    )
    if records_dir not in sys.path:
        sys.path.insert(0, records_dir)
    import fetch_calendar as fc

    return fc


def get_previous_trading_day(current_date):
    # 本地日历优先：稳定、含法定休市，避免新浪 py_mini_racer 架构报错
    try:
        return _local_calendar_mod().prev_trading_day_dt(current_date)
    except Exception as e:
        print(f"⚠️ 本地交易日历失败，尝试新浪: {e}")
    try:
        trade_cal = _retry_fetch(
            "交易日历",
            ak.tool_trade_date_hist_sina,
            max_attempts=3,
            base_delay=0.8,
        )
        trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"])
        current_date_only = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        past = trade_cal[trade_cal["trade_date"] < current_date_only]
        if len(past) > 0:
            return past.iloc[-1]["trade_date"].to_pydatetime()
    except Exception:
        pass
    day = current_date - timedelta(1)
    while day.weekday() >= 5:
        day -= timedelta(1)
    return day


def get_next_trading_day(current_date):
    try:
        return _local_calendar_mod().next_trading_day_dt(current_date)
    except Exception as e:
        print(f"⚠️ 本地交易日历失败，尝试新浪: {e}")
    try:
        trade_cal = _retry_fetch(
            "交易日历",
            ak.tool_trade_date_hist_sina,
            max_attempts=3,
            base_delay=0.8,
        )
        trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"])
        current_date_only = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        future = trade_cal[trade_cal["trade_date"] > current_date_only]
        if len(future) > 0:
            return future.iloc[0]["trade_date"].to_pydatetime()
    except Exception:
        pass
    day = current_date + timedelta(1)
    while day.weekday() >= 5:
        day += timedelta(1)
    return day


def is_a_share_trading_day(dt: datetime | None = None) -> bool:
    dt = dt or datetime.now()
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return _local_calendar_mod().is_trading_day(day)
    except Exception:
        pass
    try:
        trade_cal = _retry_fetch(
            "交易日历",
            ak.tool_trade_date_hist_sina,
            max_attempts=2,
            base_delay=0.5,
        )
        trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"])
        return bool((trade_cal["trade_date"] == day).any())
    except Exception:
        return day.weekday() < 5


def _wait_until(target: time, label: str) -> None:
    """贴着目标时刻醒来（最后 1s 用 50ms 粒度）。"""
    last_print = 0.0
    while True:
        now = datetime.now()
        if now.time() >= target:
            print(f"▶ 到达 {label} @ {now.strftime('%H:%M:%S.%f')[:-3]}")
            return
        secs = (datetime.combine(now.date(), target) - now).total_seconds()
        if secs <= 1.0:
            sleep_for = 0.05
        elif secs <= 5.0:
            sleep_for = 0.2
        else:
            sleep_for = min(secs, 2.0)
            # 远离目标时少刷屏
            tmon = time_mod.time()
            if tmon - last_print >= 5.0:
                print(f"⏳ 等待 {label}（当前 {now.strftime('%H:%M:%S')}，剩余 {secs:.0f}s）…")
                last_print = tmon
        time_mod.sleep(sleep_for)


def enforce_scan_window(*, force: bool = False) -> None:
    """交易日【9:24:40 准时】起扫；超过 9:29 拒绝（除非 --force）。"""
    now = datetime.now()
    if force:
        print(f"⚠ --force：跳过时间窗（当前 {now.strftime('%H:%M:%S')}）")
        return
    # 先快速工作日判断，网络日历放到等待之后，避免拖过 9:24:40
    if now.weekday() >= 5:
        print(f"周末（{now.strftime('%Y-%m-%d')}），退出")
        sys.exit(0)
    if now.time() > NOTIFY_DEADLINE:
        print(
            f"已过通知截止 {NOTIFY_DEADLINE.strftime('%H:%M:%S')}"
            f"（当前 {now.strftime('%H:%M:%S')}），退出"
        )
        sys.exit(0)
    if now.time() < SCAN_START:
        _wait_until(SCAN_START, f"扫描起点 {SCAN_START.strftime('%H:%M:%S')}")
    # 开扫时刻再确认交易日（节假日）；失败则工作日 fail-open
    if not is_a_share_trading_day(datetime.now()):
        print(f"非 A 股交易日（{datetime.now().strftime('%Y-%m-%d')}），退出")
        sys.exit(0)
    print(
        f"▶ 进入扫描（竞价开盘 {AUCTION_OPEN.strftime('%H:%M')}，"
        f"推送目标 {SCAN_SOFT_DEADLINE.strftime('%H:%M')}，"
        f"截止 {NOTIFY_DEADLINE.strftime('%H:%M')}）"
    )


def _to_tencent_symbol(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("5", "6", "9")):
        return f"sh{c}"
    return f"sz{c}"


def fetch_one_open_pct(code: str) -> Optional[float]:
    """
    【铁律】腾讯实时行情：每次只请求 1 只，禁止 q=a,b,c 批量拼接。
    返回 (今开-昨收)/昨收*100；今开未落地返回 None。绝不用现价。
    """
    import requests

    code = str(code).zfill(6)
    sym = _to_tencent_symbol(code)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"}
    try:
        sess = requests.Session()
        sess.trust_env = False
        r = sess.get(f"http://qt.gtimg.cn/q={sym}", headers=headers, timeout=6)
        text = (r.text or "").strip()
    except Exception as e:
        print(f"  腾讯单票失败 {code}: {e}")
        return None
    for line in text.split(";"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        try:
            payload = line.split('="', 1)[1].rstrip('";')
            parts = payload.split("~")
            if len(parts) < 6:
                continue
            pre = float(parts[4] or 0)
            open_p = float(parts[5] or 0)
            if pre <= 0 or open_p <= 0:
                return None
            return round((open_p - pre) / pre * 100.0, 3)
        except (ValueError, IndexError, TypeError):
            continue
    return None


def wait_auction_open_ready(*, force: bool = False, probe_code: str = "600000") -> None:
    """等到 9:25，并用【单票】探测今开是否落地。"""
    if force:
        return
    if datetime.now().time() < AUCTION_OPEN:
        _wait_until(AUCTION_OPEN, f"集合竞价开盘 {AUCTION_OPEN.strftime('%H:%M')}")
    deadline = datetime.combine(datetime.now().date(), NOTIFY_DEADLINE)
    while datetime.now() < deadline:
        pct = fetch_one_open_pct(probe_code)
        time_mod.sleep(TX_SINGLE_SLEEP)
        if pct is not None:
            print(f"  今开已落地（探针 {probe_code}={pct:+.2f}%）")
            return
        print(f"  今开尚未落地，{TX_PROBE_SLEEP:.1f}s 后再探…")
        time_mod.sleep(TX_PROBE_SLEEP)
    print("  ⚠ 截止前探针仍无今开，继续按单票尽力拉取")


def fetch_open_pct_map(codes: list[str], chunk_size: int = 1) -> dict[str, float]:
    """兼容旧名：强制逐只请求（忽略 chunk_size），禁止批量。"""
    _ = chunk_size
    out: dict[str, float] = {}
    seen: set[str] = set()
    for c in codes:
        cc = str(c).zfill(6)
        if cc in seen:
            continue
        seen.add(cc)
        pct = fetch_one_open_pct(cc)
        time_mod.sleep(TX_SINGLE_SLEEP)
        if pct is not None:
            out[cc] = pct
    return out


def fetch_open_pct_map_ready(
    codes: list[str],
    *,
    force: bool = False,
    min_coverage: float = 0.85,
    poll_sec: float = 1.2,
) -> dict[str, float]:
    """兼容旧接口（回填）。早盘主路径用 select_green_open_top2_live。"""
    _ = min_coverage, poll_sec
    if not force and datetime.now().time() < AUCTION_OPEN:
        _wait_until(AUCTION_OPEN, f"集合竞价开盘 {AUCTION_OPEN.strftime('%H:%M')}")
    best = fetch_open_pct_map(codes)
    print(f"  今开覆盖 {len(best)}/{max(len(codes),1)}（逐只·禁批量）")
    return best


def calculate_fast_score(row) -> float:
    zf = float(row.get("涨跌幅") or 0) * SCORE_WEIGHTS["涨跌幅"]
    cj = (float(row.get("成交额") or 0) / 1e8) * SCORE_WEIGHTS["成交额"]
    hs = float(row.get("换手率") or 0) * SCORE_WEIGHTS["换手率"]
    return zf + cj + hs


def select_green_open_top2(sorted_df: pd.DataFrame, auction_pct: dict[str, float]) -> tuple[pd.DataFrame, list[dict]]:
    """按强度序取绿开 Top2（已有 pct 表，供回填）。"""
    picks = []
    skip_log = []
    for _, row in sorted_df.iterrows():
        code = str(row["代码"]).zfill(6)
        name = str(row["名称"])
        pct = auction_pct.get(code)
        score = float(row.get("综合得分") or 0)
        if pct is None:
            skip_log.append({"code": code, "name": name, "reason": "无竞价", "score": score})
            continue
        if pct >= 0:
            skip_log.append({
                "code": code, "name": name,
                "reason": f"红/平开 {pct:+.2f}%", "score": score, "pct": pct,
            })
            continue
        picks.append({**row.to_dict(), "竞价涨跌幅": pct})
        if len(picks) >= 2:
            break
    if not picks:
        return pd.DataFrame(), skip_log
    return pd.DataFrame(picks), skip_log


def select_green_open_top2_live(
    sorted_df: pd.DataFrame,
    *,
    force: bool = False,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    【铁律·早盘主路径】强度序逐只拉今开，凑齐 2 只绿开即停。
    禁止批量；预留时间给 PushPlus，最迟 9:29 发出。
    """
    wait_auction_open_ready(force=force)
    picks: list[dict] = []
    skip_log: list[dict] = []
    scan_stop = datetime.combine(datetime.now().date(), NOTIFY_DEADLINE) - timedelta(seconds=40)
    if force:
        scan_stop = datetime.now() + timedelta(hours=1)

    n_req = 0
    for _, row in sorted_df.iterrows():
        now = datetime.now()
        if now >= scan_stop:
            print(f"  ⏰ 临近推送截止，停止扫票（已有绿开 {len(picks)}）")
            break

        code = str(row["代码"]).zfill(6)
        name = str(row["名称"])
        score = float(row.get("综合得分") or 0)
        pct = None
        for attempt in range(1, TX_OPEN_RETRY + 1):
            pct = fetch_one_open_pct(code)
            n_req += 1
            time_mod.sleep(TX_SINGLE_SLEEP)
            if pct is not None:
                break
            if attempt < TX_OPEN_RETRY:
                time_mod.sleep(0.25)

        if pct is None:
            skip_log.append({"code": code, "name": name, "reason": "无竞价", "score": score})
            continue
        if pct >= 0:
            skip_log.append({
                "code": code, "name": name,
                "reason": f"红/平开 {pct:+.2f}%", "score": score, "pct": pct,
            })
            print(f"  跳过 {code} {name} 红/平开 {pct:+.2f}%")
            continue
        picks.append({**row.to_dict(), "竞价涨跌幅": pct})
        print(f"  ✓ 绿开 #{len(picks)} {code} {name} {pct:+.2f}%（累计请求 {n_req}）")
        if len(picks) >= 2:
            break

    print(f"  单票请求合计 {n_req} 次（禁批量）")
    if not picks:
        return pd.DataFrame(), skip_log
    return pd.DataFrame(picks), skip_log


def sync_trades_to_ui(top2_df: pd.DataFrame, buy_date_str: str) -> dict:
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    records_dir = os.path.join(_root, "交易记录UI")
    if records_dir not in sys.path:
        sys.path.insert(0, records_dir)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    try:
        from stock_UI import upsert_from_stock_py
    except Exception as e:
        print(f"⚠️ 无法加载交易记录UI模块: {e}")
        return {"error": str(e)}

    records = []
    for rank, (_, row) in enumerate(top2_df.iterrows(), start=1):
        records.append({
            "date": buy_date_str,
            "stock_code": str(row["代码"]).zfill(6),
            "stock_name": str(row["名称"]),
            "rank": rank,
        })
    try:
        # 早盘只写基础字段，15:05 收盘作业补价
        result = upsert_from_stock_py(records, auto_enrich=False, program=PROGRAM)
        print(
            f"✅ 已同步 UI[{PROGRAM}]: 新建 {result.get('created', 0)} / "
            f"更新 {result.get('updated', 0)}"
        )
        return result
    except Exception as e:
        print(f"⚠️ 同步交易记录UI失败: {e}")
        return {"error": str(e)}


def send_push(top2_df: pd.DataFrame, buy_date_str: str, d0_display: str, *, dry_run: bool = False) -> bool:
    """
    【铁律】PushPlus 最迟 9:29 必须发出成功；失败则短间隔重试直到截止。
    """
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    records_dir = os.path.join(_root, "交易记录UI")
    if records_dir not in sys.path:
        sys.path.insert(0, records_dir)

    now = datetime.now()
    lines = [
        f"📉 Stock水下(绿开) — {buy_date_str}",
        f"D0涨停日: {d0_display}",
        "规则: 强度序 → 跳过红开 → 取竞价水下 Top2（腾讯今开·禁批量）",
        "",
    ]
    if top2_df is None or top2_df.empty:
        lines.append("今日无符合绿开条件的标的")
    else:
        for rank, (_, row) in enumerate(top2_df.iterrows(), start=1):
            medal = "🥇" if rank == 1 else "🥈"
            code = str(row["代码"]).zfill(6)
            pct = row.get("竞价涨跌幅")
            pct_s = f"{pct:+.2f}%" if pct is not None else "N/A"
            lines.append(
                f"{medal} 排名 {rank}: {code} {row['名称']} | 竞价 {pct_s} | 得分 {float(row.get('综合得分') or 0):.2f}"
            )
            lines.append(f"   链接: https://finance.baidu.com/stock/ab-{code}")
    lines.append("")
    lines.append(f"🔗 UI → stock_updata Tab | 推送时刻 {now.strftime('%H:%M:%S')}")
    message = "\n".join(lines)
    title = f"STOCK水下 {now.strftime('%m/%d %H:%M')}"
    print(message)

    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    topic = os.environ.get("PUSHPLUS_TOPIC", "").strip()
    to = os.environ.get("PUSHPLUS_TO", "").strip()
    if dry_run:
        print("[DRY-RUN] 未实际推送")
        return True
    if not token:
        token_path = os.path.expanduser("~/new_stock/github/pushplus/token.md")
        if os.path.isfile(token_path):
            import re
            with open(token_path, "r", encoding="utf-8") as f:
                m = re.search(r"[a-f0-9]{32}", f.read())
                if m:
                    token = m.group(0)
    if not token:
        print("⚠️ 无 PUSHPLUS_TOKEN，跳过推送")
        return False

    try:
        # 交易记录UI目录规范：send_push_notify 在 jobs/；pushplus_notify 在首板战法根
        _here = os.path.dirname(os.path.abspath(__file__))
        _root = os.path.dirname(_here)  # 首板战法
        _jobs = os.path.join(_root, "交易记录UI", "jobs")
        _pub = os.path.join(_root, "公共")
        for _p in (_pub, _root, _jobs):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from pushplus_notify import send_pushplus, KIND_STOCK_UPDATA
    except Exception as e:
        print(f"⚠️ 无法加载 pushplus_notify: {e}")
        return False

    # 硬截止 + 短暂宽限（网络抖动）；人工补发时放宽到开盘后 30 分钟
    hard = datetime.combine(datetime.now().date(), NOTIFY_DEADLINE)
    grace_end = hard + timedelta(minutes=30)
    attempt = 0
    while True:
        attempt += 1
        ok = False
        try:
            # 与 pushplus_notify 契约一致：本人+白名单好友由 kind 闸门处理
            ok = bool(
                send_pushplus(
                    title,
                    message,
                    token=token,
                    topic=topic or "",
                    to=to or "",
                    kind=KIND_STOCK_UPDATA,
                )
            )
        except Exception as e:
            print(f"⚠️ 推送异常 attempt={attempt}: {e}")
        if ok:
            print(f"✅ PushPlus 发送成功（第 {attempt} 次，{datetime.now().strftime('%H:%M:%S')}）")
            return True
        now = datetime.now()
        if now >= grace_end:
            print(f"❌ PushPlus 在截止后仍失败（attempt={attempt}），放弃")
            return False
        left = (grace_end - now).total_seconds()
        print(f"  推送失败，{PUSH_RETRY_SLEEP:.0f}s 后重试（剩余宽限 {left:.0f}s）…")
        time_mod.sleep(min(PUSH_RETRY_SLEEP, max(left, 0.5)))


def save_results_md(
    output_lines: list[str],
    query_date_str: str,
) -> str:
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import paths

    results_dir = paths.program_results_dir(PROGRAM)
    file_path = os.path.join(results_dir, f"{query_date_str}_stock_updata_recommendations.md")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
    print(f"✅ 结果已保存: {file_path}")
    return file_path


def analyze_green_open_stocks(*, force: bool = False, dry_run: bool = False, do_notify: bool = True) -> None:
    t0 = time_mod.time()
    enforce_scan_window(force=force)
    clear_zt_pool_cache()
    print("开始分析「强度 + 竞价绿开」…", flush=True)

    today = datetime.now()
    # D0 = 前一交易日涨停日；买入日 = 今天（扫描日）
    previous_trading_day = get_previous_trading_day(today)
    query_date_str = previous_trading_day.strftime("%Y%m%d")
    query_date_display = previous_trading_day.strftime("%Y年%m月%d日")
    buy_day = today.replace(hour=0, minute=0, second=0, microsecond=0)
    # 若今天不是交易日（force 调试），买入日用 D0 下一交易日
    if not is_a_share_trading_day(today):
        buy_day = get_next_trading_day(previous_trading_day)
    buy_date_str = buy_day.strftime("%Y-%m-%d")

    print(f"D0 涨停日: {query_date_display} | 买入日 D1: {buy_date_str}")

    try:
        zt_df = fetch_zt_pool_em(query_date_str)
    except (RequestException, OSError) as e:
        print(f"获取涨停数据失败: {e}")
        return

    if zt_df.empty or "代码" not in zt_df.columns:
        print(f"{query_date_display} 暂无涨停数据")
        return

    two_days_ago = get_previous_trading_day(previous_trading_day)
    two_days_ago_str = two_days_ago.strftime("%Y%m%d")
    print(f"筛选首板（排除 {two_days_ago_str} 已涨停）…")
    try:
        two_days_ago_zt = fetch_zt_pool_em(two_days_ago_str)
    except (RequestException, OSError) as e:
        print(f"获取前两日涨停池失败: {e}")
        return

    prev_zt_codes = _zt_pool_codes(two_days_ago_zt)
    today_zt_only = zt_df[~zt_df["代码"].isin(prev_zt_codes)]
    today_zt_only = today_zt_only[
        ~today_zt_only["代码"].astype(str).str.startswith(("688", "92"))
        & ~today_zt_only["名称"].astype(str).str.contains("ST|st", case=False, na=False)
    ]
    if today_zt_only.empty:
        print("无首板候选")
        return

    from first_board_gate import keep_first_board
    today_zt_only = keep_first_board(today_zt_only, query_date_str, two_days_ago_str)
    today_zt_only = today_zt_only.copy()
    today_zt_only["综合得分"] = today_zt_only.apply(calculate_fast_score, axis=1)
    sorted_stocks = today_zt_only.sort_values("综合得分", ascending=False)
    print(f"强度候选 {len(sorted_stocks)} 只，【逐只】拉今开（禁批量，凑齐绿开Top2即停）…")

    top2, skip_log = select_green_open_top2_live(sorted_stocks, force=force)
    elapsed = time_mod.time() - t0
    print(
        f"扫描耗时 {elapsed:.1f}s"
        f"（目标推送 {SCAN_SOFT_DEADLINE.strftime('%H:%M')} / 截止 {NOTIFY_DEADLINE.strftime('%H:%M')}）"
    )

    if top2.empty:
        print("未找到今开绿开标的（全部红开/无行情）")
    else:
        print("绿开 Top2（今开 vs 昨收）:")
        for rank, (_, row) in enumerate(top2.iterrows(), start=1):
            print(
                f"  #{rank} {row['代码']} {row['名称']} "
                f"今开 {row.get('竞价涨跌幅'):+.2f}% 得分 {row['综合得分']:.2f}"
            )

    if not dry_run and not top2.empty:
        sync_trades_to_ui(top2, buy_date_str)

    # md
    lines = [
        f"=== {query_date_display} Stock水下（今开绿开 Top2）===",
        f"买入日(D0): {buy_date_str} | 规则: 强度序跳过红开，取 (今开-昨收)/昨收 < 0",
        f"扫描时刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if top2.empty:
        lines.append("无符合条件标的")
    else:
        for rank, (_, row) in enumerate(top2.iterrows(), start=1):
            code = str(row["代码"]).zfill(6)
            lines.append(f"排名 {rank}: {code} - {row['名称']}")
            lines.append(f"  今开涨跌幅: {row.get('竞价涨跌幅'):+.2f}%")
            lines.append(f"  涨幅(D0): {row['涨跌幅']}%")
            lines.append(f"  成交额: {row['成交额']}")
            lines.append(f"  换手率: {row['换手率']}%")
            lines.append(f"  综合得分: {row['综合得分']:.2f}")
            lines.append(f"  百度行情: https://finance.baidu.com/stock/ab-{code}")
            lines.append("")
    lines.append("=== 强度序已跳过（红开/无今开）===")
    for s in skip_log[:30]:
        lines.append(f"  {s['code']} {s['name']}: {s['reason']} (得分 {s['score']:.2f})")
    if len(skip_log) > 30:
        lines.append(f"  …共跳过 {len(skip_log)} 只")
    save_results_md(lines, query_date_str)

    if do_notify:
        send_push(top2, buy_date_str, query_date_display, dry_run=dry_run)

    now = datetime.now()
    if now.time() > NOTIFY_DEADLINE and not force:
        print(f"⚠ 结束时刻 {now.strftime('%H:%M:%S')} 已超过通知截止 {NOTIFY_DEADLINE.strftime('%H:%M')}")
    else:
        print(f"🎉 完成于 {now.strftime('%H:%M:%S')}（总耗时 {time_mod.time() - t0:.1f}s）")


def _build_first_board_sorted(d0: datetime) -> Optional[pd.DataFrame]:
    """给定 D0 涨停日，返回强度排序后的首板候选。"""
    query_date_str = d0.strftime("%Y%m%d")
    try:
        zt_df = fetch_zt_pool_em(query_date_str)
    except (RequestException, OSError) as e:
        print(f"  涨停池失败 {query_date_str}: {e}")
        return None
    if zt_df.empty or "代码" not in zt_df.columns:
        print(f"  {query_date_str} 无涨停数据")
        return None
    two = get_previous_trading_day(d0)
    try:
        two_zt = fetch_zt_pool_em(two.strftime("%Y%m%d"))
    except (RequestException, OSError) as e:
        print(f"  前两日涨停池失败: {e}")
        return None
    prev_codes = _zt_pool_codes(two_zt)
    cand = zt_df[~zt_df["代码"].isin(prev_codes)].copy()
    from first_board_gate import keep_first_board
    cand = keep_first_board(cand, d0, two)
    cand = cand[
        ~cand["代码"].astype(str).str.startswith(("688", "92"))
        & ~cand["名称"].astype(str).str.contains("ST|st", case=False, na=False)
    ]
    if cand.empty:
        return cand
    cand["综合得分"] = cand.apply(calculate_fast_score, axis=1)
    return cand.sort_values("综合得分", ascending=False)


def _hist_open_pct(code: str, buy_date: str) -> Optional[float]:
    """历史日：(今开-昨收)/昨收*100，腾讯日线，慢速。"""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui = os.path.join(_root, "交易记录UI")
    if ui not in sys.path:
        sys.path.insert(0, ui)
    import price_service as ps

    ohlc = ps.get_daily_ohlc(code, buy_date)
    pre = ps.get_prev_close(code, buy_date)
    if not ohlc or not pre or float(pre) <= 0:
        return None
    open_p = float(ohlc.get("open") or 0)
    if open_p <= 0:
        return None
    return round((open_p - float(pre)) / float(pre) * 100.0, 3)


def _buy_days_inclusive(start: str, end: str) -> list[str]:
    """优先本地日历；2026 等未入库时用工作日 + 已有 stock 交易文件兜底。"""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui = os.path.join(_root, "交易记录UI")
    if ui not in sys.path:
        sys.path.insert(0, ui)
    try:
        import fetch_calendar as fc
        days = [d for d in fc.load_calendar(start, end)]
        if days:
            return days
    except Exception as e:
        print(f"  日历加载失败: {e}")

    # 兜底1：stock/trades 已有买入日
    trades = os.path.join(_root, "stock", "trades")
    from_files = []
    if os.path.isdir(trades):
        for fname in sorted(os.listdir(trades)):
            if not fname.endswith(".json"):
                continue
            day = fname[:-5]
            if start <= day <= end:
                from_files.append(day)

    # 兜底2：区间内工作日（周一到五），再与 is_a_share_trading_day 交叉
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    weekdays = []
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            weekdays.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    merged = sorted(set(from_files) | set(weekdays))
    # 去掉明显非交易：若 ak 日历可用则过滤
    out = []
    for day in merged:
        dt = datetime.strptime(day, "%Y-%m-%d")
        try:
            if is_a_share_trading_day(dt):
                out.append(day)
            elif day in from_files:
                out.append(day)
        except Exception:
            out.append(day)
    return out


def backfill_range(start: str, end: str, *, enrich: bool = True, sleep_stock: float = 0.7, sleep_day: float = 2.5) -> None:
    """
    慢速回补买入日 [start, end] 的绿开 Top2。
    用历史日线今开判定，避免盘中现价误判；接口间隔拉大防封。
    """
    os.environ.setdefault("DISABLE_BAOSTOCK", "1")
    clear_zt_pool_cache()
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui = os.path.join(_root, "交易记录UI")
    if ui not in sys.path:
        sys.path.insert(0, ui)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from stock_UI import enrich_trade_prices, _load_day, _save_day

    days = _buy_days_inclusive(start, end)
    print(f"=== 水下回补 {start}→{end} 共 {len(days)} 个买入日（慢速）: {days} ===")
    for buy_date in days:
        print(f"\n--- 买入日 {buy_date} ---")
        d0_dt = get_previous_trading_day(datetime.strptime(buy_date, "%Y-%m-%d"))
        sorted_df = _build_first_board_sorted(d0_dt)
        time_mod.sleep(1.2)
        if sorted_df is None or sorted_df.empty:
            print("  无首板候选，跳过")
            continue

        open_pct: dict[str, float] = {}
        for _, row in sorted_df.iterrows():
            code = str(row["代码"]).zfill(6)
            pct = _hist_open_pct(code, buy_date)
            time_mod.sleep(sleep_stock)
            if pct is None:
                continue
            open_pct[code] = pct
            if len(open_pct) >= 25:
                break

        if sum(1 for p in open_pct.values() if p < 0) < 2:
            for _, row in sorted_df.iloc[len(open_pct) :].iterrows():
                code = str(row["代码"]).zfill(6)
                if code in open_pct:
                    continue
                pct = _hist_open_pct(code, buy_date)
                time_mod.sleep(sleep_stock)
                if pct is not None:
                    open_pct[code] = pct
                if sum(1 for p in open_pct.values() if p < 0) >= 2 and len(open_pct) >= 40:
                    break

        top2, skip_log = select_green_open_top2(sorted_df, open_pct)
        if top2.empty:
            print(
                f"  无绿开 Top2（已查今开 {len(open_pct)} 只，"
                f"红开跳过 {sum(1 for s in skip_log if '红' in s['reason'])}）"
            )
            time_mod.sleep(sleep_day)
            continue

        for rank, (_, row) in enumerate(top2.iterrows(), start=1):
            print(
                f"  #{rank} {row['代码']} {row['名称']} 今开 {row.get('竞价涨跌幅'):+.2f}% "
                f"得分 {float(row['综合得分']):.2f}"
            )
        sync_trades_to_ui(top2, buy_date)

        d0_display = d0_dt.strftime("%Y年%m月%d日")
        lines = [
            f"=== {d0_display} Stock水下（今开绿开 Top2，回补）===",
            f"买入日(D0): {buy_date} | 规则: (今开-昨收)/昨收 < 0",
            "",
        ]
        for rank, (_, row) in enumerate(top2.iterrows(), start=1):
            code = str(row["代码"]).zfill(6)
            lines.append(f"排名 {rank}: {code} - {row['名称']}")
            lines.append(f"  今开涨跌幅: {row.get('竞价涨跌幅'):+.2f}%")
            lines.append(f"  综合得分: {float(row['综合得分']):.2f}")
            lines.append("")
        save_results_md(lines, d0_dt.strftime("%Y%m%d"))

        if enrich:
            trades = _load_day(buy_date, PROGRAM)
            changed = False
            for i, t in enumerate(trades or []):
                try:
                    e = enrich_trade_prices(dict(t), force=False)
                    e.pop("skip_buy", None)
                    e.setdefault("program", PROGRAM)
                    trades[i] = e
                    changed = True
                    print(
                        f"    补价 {e.get('stock_code')} buy={e.get('buy_price')} "
                        f"935={e.get('sell_price_935')} pnl={e.get('pnl_batch_total')}"
                    )
                except Exception as ex:
                    print(f"    补价失败: {ex}")
                time_mod.sleep(0.55)
            if changed:
                _save_day(buy_date, trades, PROGRAM)
        time_mod.sleep(sleep_day)
    print("\n=== 水下回补结束 ===")


def main():
    parser = argparse.ArgumentParser(description="Stock 今开绿开（水下）Top2")
    parser.add_argument("--force", action="store_true", help="忽略 9:24:40–9:29 时间窗")
    parser.add_argument("--dry-run", action="store_true", help="不写 UI、不推送")
    parser.add_argument("--no-notify", action="store_true", help="不推送")
    parser.add_argument(
        "--backfill",
        nargs=2,
        metavar=("START", "END"),
        help="慢速回补买入日区间，例: --backfill 2026-09-01 2026-09-07",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="单日买入日 D1=YYYY-MM-DD（历史回补；内部走 backfill_range 同日）",
    )
    parser.add_argument("--no-enrich", action="store_true", help="回补时不补价")
    args = parser.parse_args()
    if args.date:
        d = args.date.strip()[:10]
        backfill_range(d, d, enrich=not args.no_enrich)
        return
    if args.backfill:
        backfill_range(args.backfill[0], args.backfill[1], enrich=not args.no_enrich)
        return
    analyze_green_open_stocks(
        force=args.force,
        dry_run=args.dry_run,
        do_notify=not args.no_notify,
    )


if __name__ == "__main__":
    main()
