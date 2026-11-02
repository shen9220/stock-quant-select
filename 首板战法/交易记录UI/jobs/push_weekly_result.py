"""
V1 原周结果通知（PushPlus · 仅本人）。

权威：首板战法/规范文档/规范要求/PushPlus通知规范.md（2026-10-30 最终）

- 触发：当周最后交易日 ≥15:00 收盘补全之后
- **一条**通知：排名1 一节、排名2 一节，然后汇总
- 只含全局 rank=1/2；禁止 board_rank「排名 0」
- V2 / 主板 / 创业板周结果：暂停，本模块不发
- 发送必须单次等回执（由 pushplus_notify 闸门保证）
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
from runtime_paths import BASE_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import datetime
import time
from typing import Optional

BASE = BASE_DIR
ROOT = os.path.dirname(BASE)
for _p in (ROOT, BASE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import price_service as ps  # noqa: E402

SEP = "━"
_CACHE: dict[tuple[str, str], Optional[tuple]] = {}


def _pct(cur: Optional[float], base: Optional[float]) -> Optional[float]:
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / base * 100.0


def _pct_str(v: Optional[float]) -> str:
    return "--" if v is None else f"{v:+.2f}%"


def _week_monday(day: str) -> str:
    dt = datetime.datetime.strptime(day, "%Y-%m-%d")
    return (dt - datetime.timedelta(days=dt.weekday())).strftime("%Y-%m-%d")


def _week_friday(monday: str) -> str:
    dt = datetime.datetime.strptime(monday, "%Y-%m-%d")
    return (dt + datetime.timedelta(days=4)).strftime("%Y-%m-%d")


def _close_quote(code: str, sell_date: str) -> tuple[Optional[float], Optional[float]]:
    key = (code, sell_date)
    if key in _CACHE:
        return _CACHE[key]
    close_p = prev = None
    for attempt in range(2):
        ohlc = ps.get_daily_ohlc(code, sell_date)
        prev = ps.get_prev_close(code, sell_date)
        close_p = ohlc["close"] if ohlc else None
        if close_p is not None and prev is not None:
            break
        if attempt == 0:
            time.sleep(1.5)
    res = (close_p, prev)
    if close_p is not None and prev is not None:
        _CACHE[key] = res
    return res


def _planned_sell_day(buy_day: str) -> str:
    try:
        return ps.next_trading_day(buy_day) or ""
    except Exception:
        return ""


def _collect_week(monday: str, program: str = "stock") -> list[dict]:
    from stock_UI import _list_day_files, _load_day

    friday = _week_friday(monday)
    rows: list[dict] = []
    for fname in _list_day_files(program):
        buy_day = fname[:-5]
        for t in _load_day(buy_day, program):
            if t.get("buy_price") in (None, ""):
                continue
            if "买不进" in str(t.get("skip_reason") or ""):
                continue
            # 只要全局 rank 1/2；排除仅 board_rank 的主板/创业板记录
            if t.get("rank") in (None, ""):
                continue
            r = int(t.get("rank"))
            if r not in (1, 2):
                continue
            sd = str(t.get("sell_date") or "")
            planned = _planned_sell_day(buy_day)
            if sd and monday <= sd <= friday:
                week_day = sd
            elif planned and monday <= planned <= friday:
                week_day = planned
            else:
                continue
            t["_week_sell_day"] = week_day
            rows.append(t)
    rows.sort(key=lambda x: (int(x.get("rank") or 99), str(x.get("date") or "")))
    return rows


def _sum_block(items: list[dict], monday: str, friday: str) -> tuple[str, str, str, int]:
    """返回 (当天利润串, 早盘串, 午盘串, 缺分钟价数)。"""
    sum_close = sum_935 = sum_1315 = 0.0
    n_close = n_935 = n_1315 = 0
    missing = 0
    lines: list[str] = []
    for i, t in enumerate(items, 1):
        code = str(t.get("stock_code") or "")
        name = str(t.get("stock_name") or "")
        buy_day = str(t.get("date") or "")
        buy_price = float(t["buy_price"])
        sd = str(t.get("_week_sell_day") or t.get("sell_date") or "")
        close_p, prev_close = _close_quote(code, sd)
        p_close = _pct(close_p, prev_close)
        p_935 = _pct(t.get("sell_price_935"), buy_price)
        p_1315 = _pct(t.get("sell_price_1315"), buy_price)
        if p_935 is None or p_1315 is None:
            missing += 1
        in_week_buy = monday <= buy_day <= friday
        if p_close is not None and in_week_buy:
            sum_close += p_close
            n_close += 1
        if p_935 is not None:
            sum_935 += p_935
            n_935 += 1
        if p_1315 is not None:
            sum_1315 += p_1315
            n_1315 += 1
        tag = "" if in_week_buy else "（上周买入）"
        lines.append(f"{i}、{name} {code}{tag}")
        lines.append(f"   买入日期：{buy_day}")
        lines.append(f"   当天收盘利润：{_pct_str(p_close)}")
        lines.append(f"   早盘全卖出利润：{_pct_str(p_935)}")
        lines.append(f"   午盘全卖出利润：{_pct_str(p_1315)}")
    detail = "\n".join(lines)
    s_close = _pct_str(sum_close if n_close else None)
    s_935 = _pct_str(sum_935 if n_935 else None)
    s_1315 = _pct_str(sum_1315 if n_1315 else None)
    return detail, s_close, s_935, s_1315, n_close, n_935, n_1315, missing


def build_weekly_report(monday: Optional[str] = None, program: str = "stock") -> Optional[str]:
    """生成 V1 原周报：排名1 / 排名2 分开，然后汇总。一条正文。"""
    if program != "stock":
        print(f"[push-weekly] program={program} 已暂停周结果（规范 §3），不生成", flush=True)
        return None
    monday = monday or _week_monday(datetime.datetime.now().strftime("%Y-%m-%d"))
    friday = _week_friday(monday)
    rows = _collect_week(monday, program)
    if not rows:
        return None

    by_rank: dict[int, list[dict]] = {1: [], 2: []}
    for t in rows:
        r = int(t.get("rank"))
        if r in by_rank:
            by_rank[r].append(t)

    lines: list[str] = []
    lines.append(f"【stockV1 周结果】{monday} ~ {friday}")
    lines.append("（当天收盘利润＝相对前收盘涨跌幅；早/午盘利润＝相对买入价）")

    summary_bits: list[str] = []
    missing_all = 0
    for r in (1, 2):
        items = by_rank[r]
        lines.append("")
        lines.append(f"{SEP * 2} 排名 {r} {SEP * 2}")
        if not items:
            lines.append("（本周无卖出）")
            summary_bits.append(f"排名{r}：当天-- / 早盘-- / 午盘--（0 只）")
            continue
        detail, s_close, s_935, s_1315, n_close, n_935, n_1315, missing = _sum_block(
            items, monday, friday
        )
        missing_all += missing
        lines.append(detail)
        lines.append(f"6、本周当天利润：{s_close}（{n_close} 只本周买入）")
        lines.append(f"7、本周早盘利润：{s_935}（{n_935} 只）")
        lines.append(f"8、本周午盘利润：{s_1315}（{n_1315} 只）")
        summary_bits.append(
            f"排名{r}：当天{s_close} / 早盘{s_935} / 午盘{s_1315}（卖出{len(items)}只）"
        )

    lines.append("")
    lines.append(f"{SEP * 2} 汇总 {SEP * 2}")
    for bit in summary_bits:
        lines.append(bit)

    if missing_all:
        lines.append("")
        lines.append(f"注：{missing_all} 条 09:35/13:15 分钟价未落库，早/午盘利润显示 --")

    return "\n".join(lines)


def is_week_end(day: str) -> bool:
    dt = datetime.datetime.strptime(day, "%Y-%m-%d")
    if dt.weekday() == 4:
        return True
    nxt = ps.next_trading_day(day)
    if not nxt:
        return False
    return _week_monday(nxt) != _week_monday(day)


def push_weekly(day: Optional[str] = None, program: str = "stock", dry_run: bool = False) -> bool:
    if program != "stock":
        print(f"[push-weekly] program={program} 暂停，跳过", flush=True)
        return False
    day = day or datetime.datetime.now().strftime("%Y-%m-%d")
    monday = _week_monday(day)
    try:
        text = build_weekly_report(monday, program)
    except Exception as e:
        print(f"[push-weekly] 生成周报失败 {monday}: {e}", flush=True)
        return False
    if not text:
        print(f"[push-weekly] {monday} 那周无卖出记录，跳过", flush=True)
        return False

    title = f"stockV1-周结果 {monday}~{_week_friday(monday)}"
    print(f"[push-weekly] ===== {title} =====", flush=True)
    print(text, flush=True)
    if dry_run:
        print("[push-weekly] dry-run，未发送", flush=True)
        return True

    from pushplus_notify import send_pushplus, KIND_STOCK_WEEKLY

    ok = send_pushplus(title, text, kind=KIND_STOCK_WEEKLY)
    print(f"[push-weekly] 推送{'成功' if ok else '失败'}", flush=True)
    return ok


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="V1 原周结果推送（单条）")
    ap.add_argument("--date", default="")
    ap.add_argument("--monday", default="")
    ap.add_argument("--program", default="stock")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    target = a.monday or (a.date or None)
    raise SystemExit(0 if push_weekly(target, a.program, a.dry_run) else 1)
