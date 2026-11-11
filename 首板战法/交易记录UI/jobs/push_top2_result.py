"""
收盘推送：当天买入的 stock Top2 结果汇总（PushPlus）。

触发时机：daily_close_job 收盘补全（stock 程序）成功之后。

内容格式（用户 2026-10-19 指定）：
    【stock Top2】
    数据日期: 2026年10月16日
    买入日: 2026-10-19
    执行时间: 2026-10-19 09:05
    ━━ 排名 1 ━━
    300045 华力创通
    开盘价：13.59
    开盘涨跌幅：+7.01%
    收盘价：13.02
    收盘涨跌幅：+2.52%
    买入后是否盈利：否
    买入后利润：-4.19%
    ━━ 排名 2 ━━
    ...

口径说明：
    - 「数据日期」= D0 涨停选出日（买入日的上一个交易日）
    - 「买入日」  = trade["date"]（D1，实际买入日）
    - 「执行时间」= D1 当天早盘选股/买入的执行时刻（取 stock 推荐文件的执行时间，
                    缺省时退化为 D1 的选股时刻）
    - 开盘涨跌幅 = (D1 开盘价 - D0 收盘价) / D0 收盘价
    - 收盘涨跌幅 = (D1 收盘价 - D0 收盘价) / D0 收盘价
    - 买入后利润 = (D1 收盘价 - 买入价) / 买入价
    - 买入价铁律 = D1 开盘价（IRoN_RULES「买入价口径」）
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

import os
import re
import sys
import time
from datetime import datetime
from typing import Optional

BASE = BASE_DIR
ROOT = os.path.dirname(BASE)
for _p in (ROOT, BASE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import price_service as ps  # noqa: E402

SEP = "━"


def _pct_str(val: float | None) -> str:
    """百分比格式化：+1.23% / -2.05% / --"""
    if val is None:
        return "--"
    return f"{val:+.2f}%"


def _money(val: float | None) -> str:
    return "--" if val is None else f"{val:.2f}"


def _pct(cur: float | None, base: float | None) -> float | None:
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / base * 100.0


def _resolve_execution_time(buy_day: str, program: str = "stock") -> str:
    """取 D1 的选股执行时间；优先读推荐 md，其次退化为 09:05。"""
    try:
        from daily_close_job import _find_recommendations_md

        d0 = ps.prev_trading_day(buy_day)
        path, _recs = _find_recommendations_md(d0 or buy_day, program)
        if path and os.path.exists(path):
            with open(path, encoding="utf-8", errors="ignore") as f:
                m = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", f.read())
            if m and m.group(1) == buy_day:
                return m.group(2)
    except Exception:
        pass
    return "09:27" if program == "stock_v2" else "09:05"


def _quote_with_retry(code: str, date_str: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """取 (昨收, 当日开盘, 当日收盘)。行情源偶发限流时重试一次，避免推送出现空洞。"""
    prev = open_p = close_p = None
    for attempt in range(2):
        prev = ps.get_prev_close(code, date_str)
        ohlc = ps.get_daily_ohlc(code, date_str)
        open_p = ohlc["open"] if ohlc else None
        close_p = ohlc["close"] if ohlc else None
        if prev is not None and open_p is not None and close_p is not None:
            break
        if attempt == 0:
            time.sleep(1.5)
    return prev, open_p, close_p


def build_stock_top2_report(
    buy_day: str,
    program: str = "stock",
    *,
    board: str | None = None,
) -> str | None:
    """生成当天买入涨跌幅文本。

    board=None：V1 原全局 Top2（rank 1/2）
    board=创业板：该板 board_rank Top2（规范 e2）
    """
    from stock_UI import _load_day
    import stock_meta

    trades = _load_day(buy_day, program)
    trades = [t for t in trades if t.get("buy_price") not in (None, "")]

    if board:
        def _eff_board(t):
            b = t.get("board")
            if not b:
                b = stock_meta.board_of_code(t.get("stock_code", ""))
            return b or ""

        trades = [
            t for t in trades
            if _eff_board(t) == board and t.get("board_rank") is not None
        ]
        trades.sort(key=lambda x: int(x.get("board_rank") or 99))
        rank_key = "board_rank"
        header = f"【stockV1-{board} 当日买入涨跌幅】"
    else:
        trades = [t for t in trades if t.get("rank") not in (None, "") and int(t.get("rank") or 0) in (1, 2)]
        trades.sort(key=lambda x: int(x.get("rank") or 99))
        rank_key = "rank"
        header = "【stock_v2 Top2】" if program == "stock_v2" else "【stockV1 原当日买入涨跌幅】"

    if not trades:
        return None

    top2 = trades[:2]
    d0 = ps.prev_trading_day(buy_day)

    lines: list[str] = []
    lines.append(header)
    d0_txt = "未知"
    if d0:
        try:
            d0_txt = datetime.strptime(d0, "%Y-%m-%d").strftime("%Y年%m月%d日")
        except ValueError:
            d0_txt = d0
    lines.append(f"数据日期: {d0_txt}")
    lines.append(f"买入日: {buy_day}")
    lines.append(f"执行时间: {buy_day} {_resolve_execution_time(buy_day, program)}")

    for t in top2:
        code = str(t.get("stock_code") or "")
        name = str(t.get("stock_name") or "")
        buy_price = float(t["buy_price"])
        prev_close, open_p, close_p = _quote_with_retry(code, buy_day)
        rank = int(t.get(rank_key) or 0)
        lines.append("")
        lines.append(f"{SEP * 2} 排名 {rank} {SEP * 2}")
        lines.append(f"{code} {name}")
        lines.append(f"开盘价：{_money(open_p)}")
        lines.append(f"开盘涨跌幅：{_pct_str(_pct(open_p, prev_close))}")
        lines.append(f"收盘价：{_money(close_p)}")
        lines.append(f"收盘涨跌幅：{_pct_str(_pct(close_p, prev_close))}")
        pnl_pct = _pct(close_p, buy_price)
        if pnl_pct is None:
            lines.append("买入后是否盈利：--")
            lines.append("买入后利润：--")
        else:
            lines.append(f"买入后是否盈利：{'是' if pnl_pct > 0 else ('否' if pnl_pct < 0 else '持平')}")
            lines.append(f"买入后利润：{_pct_str(pnl_pct)}")

    return "\n".join(lines)


def push_stock_top2(
    buy_day: str | None = None,
    program: str = "stock",
    dry_run: bool = False,
    *,
    board: str | None = None,
) -> bool:
    """收盘后推送当日买入涨跌幅。V2 只发日涨跌幅，周结果仍暂停。"""
    buy_day = buy_day or datetime.now().strftime("%Y-%m-%d")
    if program not in ("stock", "stock_v2"):
        print(f"[push-top2] program={program} 不在尾盘通知清单，跳过", flush=True)
        return False

    try:
        text = build_stock_top2_report(buy_day, program, board=board)
    except Exception as e:
        print(f"[push-top2] 生成报告失败 board={board} {buy_day}: {e}", flush=True)
        return False

    if not text:
        print(f"[push-top2] board={board or '原'} {buy_day} 无买入记录，跳过推送", flush=True)
        return False

    if program == "stock_v2":
        from pushplus_notify import KIND_STOCK_V2_TOP2 as _KIND
        title = f"stock-V2 买入涨跌幅 {buy_day}"
        kind = _KIND
    elif board == "创业板":
        from pushplus_notify import KIND_STOCK_CHINEXT_TOP2 as _KIND
        title = f"stockV1-创业板 当日买入涨跌幅 {buy_day}"
        kind = _KIND
    else:
        from pushplus_notify import KIND_STOCK_TOP2 as _KIND
        title = f"stockV1-原 当日买入涨跌幅 {buy_day}"
        kind = _KIND

    print(f"[push-top2] ===== {title} =====", flush=True)
    print(text, flush=True)

    if dry_run:
        print("[push-top2] dry-run，未发送", flush=True)
        return True

    try:
        from pushplus_notify import send_pushplus
        ok = send_pushplus(title, text, kind=kind)
    except Exception as e:
        print(f"[push-top2] 推送异常: {e}", flush=True)
        return False
    print(f"[push-top2] 推送{'成功' if ok else '失败'}", flush=True)
    return ok


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="收盘后推送当天买入涨跌幅（单条）")
    ap.add_argument("--date", default="", help="买入日 YYYY-MM-DD，默认今天")
    ap.add_argument("--program", default="stock")
    ap.add_argument("--board", default="", help="空=V1原；创业板=创业板当日")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    raise SystemExit(
        0
        if push_stock_top2(a.date or None, a.program, a.dry_run, board=(a.board or None))
        else 1
    )