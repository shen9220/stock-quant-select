"""
价格获取与一字/涨停判定。
分钟价优先 baostock 5 分钟 K；日线用 akshare（东财/腾讯兜底）。
失败返回 None，由调用方留空或稍后补全。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None

try:
    import baostock as bs
except ImportError:  # pragma: no cover
    bs = None

from requests.exceptions import RequestException

_TRADE_CAL_CACHE: Optional[pd.DataFrame] = None
_DAILY_CACHE: dict[str, pd.DataFrame] = {}
_MIN_CACHE: dict[str, pd.DataFrame] = {}
_BS_LOCK = threading.Lock()
_BS_LOGGED_IN = False


def _retry(description, func, *args, max_attempts=4, base_delay=1.2, **kwargs):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except (RequestException, OSError, ValueError, KeyError, IndexError) as e:
            last_err = e
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                print(f"[price] {description} 失败 ({type(e).__name__})，{wait:.1f}s 后重试 ({attempt}/{max_attempts})")
                time.sleep(wait)
    if last_err:
        print(f"[price] {description} 最终失败: {last_err}")
    return None


def limit_up_ratio(code: str) -> float:
    code = str(code).zfill(6)
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.startswith(("8", "4")):
        return 0.30
    return 0.10


def limit_up_price(prev_close: float, code: str) -> float:
    return round(prev_close * (1 + limit_up_ratio(code)), 2)


def limit_down_price(prev_close: float, code: str) -> float:
    return round(prev_close * (1 - limit_up_ratio(code)), 2)


def is_at_limit_up(price: float, prev_close: float, code: str, tol: float = 0.003) -> bool:
    if not price or not prev_close or prev_close <= 0:
        return False
    target = limit_up_price(prev_close, code)
    return abs(price - target) / target <= tol or price >= target * (1 - tol)


def is_at_limit_down(price: float, prev_close: float, code: str, tol: float = 0.003) -> bool:
    """触及跌停：该时点卖不出。"""
    if not price or not prev_close or prev_close <= 0:
        return False
    target = limit_down_price(prev_close, code)
    return abs(price - target) / target <= tol or price <= target * (1 + tol)


def to_bs_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    return f"sz.{code}"


def to_sina_symbol(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _bs_ensure_login():
    global _BS_LOGGED_IN
    if bs is None:
        return False
    with _BS_LOCK:
        if _BS_LOGGED_IN:
            return True
        lg = bs.login()
        if lg.error_code == "0":
            _BS_LOGGED_IN = True
            return True
        print(f"[price] baostock 登录失败: {lg.error_msg}")
        return False


def get_trade_calendar() -> Optional[pd.DataFrame]:
    """交易日历：优先 baostock，避免 akshare/py_mini_racer 架构问题。"""
    global _TRADE_CAL_CACHE
    if _TRADE_CAL_CACHE is not None:
        return _TRADE_CAL_CACHE

    # baostock：取近年日历
    if bs is not None and _bs_ensure_login():
        try:
            with _BS_LOCK:
                rs = bs.query_trade_dates(start_date="2024-01-01", end_date="2027-12-31")
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
            if rows:
                df = pd.DataFrame(rows, columns=["calendar_date", "is_trading_day"])
                df = df[df["is_trading_day"] == "1"].copy()
                df["trade_date"] = pd.to_datetime(df["calendar_date"])
                _TRADE_CAL_CACHE = df[["trade_date"]].reset_index(drop=True)
                return _TRADE_CAL_CACHE
        except Exception as e:
            print(f"[price] baostock 交易日历失败: {e}")

    # akshare 兜底（可能因 py_mini_racer 失败）
    if ak is not None:
        df = _retry("交易日历", ak.tool_trade_date_hist_sina, max_attempts=2, base_delay=0.5)
        if df is not None and not df.empty:
            df = df.copy()
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            _TRADE_CAL_CACHE = df
            return _TRADE_CAL_CACHE
    return None


def next_trading_day(date_like) -> Optional[str]:
    dt = _to_dt(date_like)
    if dt is None:
        return None
    cal = get_trade_calendar()
    if cal is not None:
        future = cal[cal["trade_date"] > dt]
        if not future.empty:
            return future.iloc[0]["trade_date"].strftime("%Y-%m-%d")
    day = dt + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def prev_trading_day(date_like) -> Optional[str]:
    dt = _to_dt(date_like)
    if dt is None:
        return None
    cal = get_trade_calendar()
    if cal is not None:
        past = cal[cal["trade_date"] < dt]
        if not past.empty:
            return past.iloc[-1]["trade_date"].strftime("%Y-%m-%d")
    day = dt - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def _to_dt(date_like) -> Optional[datetime]:
    if date_like is None:
        return None
    if isinstance(date_like, datetime):
        return date_like.replace(hour=0, minute=0, second=0, microsecond=0)
    s = str(date_like).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s[:8], fmt)
        except ValueError:
            continue
    return None


def _fetch_daily_baostock(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    if bs is None or not _bs_ensure_login():
        return None
    bs_code = to_bs_code(code)
    with _BS_LOCK:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="3",
        )
        if rs.error_code != "0":
            return None
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["日期", "开盘", "最高", "最低", "收盘", "volume", "amount"])
    df["日期"] = pd.to_datetime(df["日期"])
    for col in ("开盘", "最高", "最低", "收盘"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _fetch_daily(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    key = f"{code}_{start}_{end}"
    if key in _DAILY_CACHE:
        return _DAILY_CACHE[key]
    code6 = str(code).zfill(6)

    # 1) baostock 日线（稳定）
    df = _fetch_daily_baostock(code6, start, end)

    # 2) 腾讯
    if (df is None or df.empty) and ak is not None:
        start8 = start.replace("-", "")
        end8 = end.replace("-", "")
        prefix = "sh" if code6.startswith(("6", "9")) else "sz"
        df_tx = _retry(
            f"日线TX {code6}",
            ak.stock_zh_a_hist_tx,
            symbol=f"{prefix}{code6}",
            start_date=start8,
            end_date=end8,
            adjust="",
            max_attempts=2,
            base_delay=0.6,
        )
        if df_tx is not None and not df_tx.empty:
            df = df_tx.rename(columns={
                "date": "日期", "open": "开盘", "close": "收盘",
                "high": "最高", "low": "最低",
            })
            df["日期"] = pd.to_datetime(df["日期"])

    # 3) 东财（最不稳定，放最后）
    if (df is None or df.empty) and ak is not None:
        start8 = start.replace("-", "")
        end8 = end.replace("-", "")
        df_em = _retry(
            f"日线EM {code6}",
            ak.stock_zh_a_hist,
            symbol=code6,
            period="daily",
            start_date=start8,
            end_date=end8,
            adjust="",
            max_attempts=2,
            base_delay=0.6,
        )
        if df_em is not None and not df_em.empty:
            df = df_em.copy()
            df["日期"] = pd.to_datetime(df["日期"])

    if df is None or df.empty:
        return None
    _DAILY_CACHE[key] = df
    return df


def get_open_price(code: str, date_str: str) -> Optional[float]:
    ohlc = get_daily_ohlc(code, date_str)
    return ohlc["open"] if ohlc else None


def get_prev_close(code: str, date_str: str) -> Optional[float]:
    prev = prev_trading_day(date_str)
    if not prev:
        return None
    ohlc = get_daily_ohlc(code, prev)
    return ohlc["close"] if ohlc else None


def _fetch_today_ohlc_sina(code: str) -> Optional[dict]:
    """
    当日日线常尚未入库：用新浪实时行情取今开/最高/最低/现价。
    字段：名称,今开,昨收,现价,最高,最低,...
    """
    try:
        import requests
    except ImportError:
        return None
    sym = to_sina_symbol(code)
    try:
        r = requests.get(
            f"https://hq.sinajs.cn/list={sym}",
            headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=8,
        )
        text = (r.text or "").strip()
        if '="' not in text:
            return None
        payload = text.split('="', 1)[1]
        if payload.startswith('";') or payload.startswith('"'):
            return None
        payload = payload.rstrip('";')
        parts = payload.split(",")
        if len(parts) < 6:
            return None
        open_p = float(parts[1])
        high = float(parts[4])
        low = float(parts[5])
        price = float(parts[3])
        if open_p <= 0:
            return None
        return {
            "open": round(open_p, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(price if price > 0 else open_p, 2),
        }
    except Exception as e:
        print(f"[price] 新浪实时 {code} 失败: {e}")
        return None


def get_daily_ohlc(code: str, date_str: str) -> Optional[dict]:
    dt = _to_dt(date_str)
    if dt is None:
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    df = _fetch_daily(code, (dt - timedelta(days=10)).strftime("%Y-%m-%d"), date_str)
    if df is not None and not df.empty:
        target = _to_dt(date_str)
        row = df[df["日期"] == target]
        if not row.empty:
            r = row.iloc[0]
            try:
                return {
                    "open": round(float(r["开盘"]), 2),
                    "high": round(float(r["最高"]), 2),
                    "low": round(float(r["最低"]), 2),
                    "close": round(float(r["收盘"]), 2),
                }
            except (TypeError, ValueError, KeyError):
                pass
    # 当日日线延迟：新浪实时兜底（收盘补全铁纪律依赖）
    if date_str == today:
        live = _fetch_today_ohlc_sina(code)
        if live:
            return live
    return None

def is_yizi_limit_up(code: str, date_str: str) -> Optional[bool]:
    """严格一字：开盘即涨停且全天几乎无振幅。"""
    ohlc = get_daily_ohlc(code, date_str)
    prev = get_prev_close(code, date_str)
    if ohlc is None or prev is None:
        return None
    open_p = ohlc["open"]
    if not is_at_limit_up(open_p, prev, code):
        return False
    if abs(ohlc["high"] - ohlc["low"]) / max(open_p, 1e-6) <= 0.005:
        return True
    return abs(open_p - ohlc["high"]) / max(open_p, 1e-6) <= 0.003 and abs(open_p - ohlc["low"]) / max(open_p, 1e-6) <= 0.003


def is_open_limit_up(code: str, date_str: str) -> Optional[bool]:
    """
    开盘涨停（含一字/开盘封死）：开盘价触及涨停。
    用于买入日判定——开盘涨停通常买不进，不记交易。
    """
    ohlc = get_daily_ohlc(code, date_str)
    prev = get_prev_close(code, date_str)
    if ohlc is None or prev is None:
        return None
    return is_at_limit_up(ohlc["open"], prev, code)


def _fetch_minute_baostock(code: str, date_str: str) -> Optional[pd.DataFrame]:
    if bs is None or not _bs_ensure_login():
        return None
    key = f"bs5_{code}_{date_str}"
    if key in _MIN_CACHE:
        return _MIN_CACHE[key]
    bs_code = to_bs_code(code)
    with _BS_LOCK:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,time,code,open,high,low,close,volume,amount",
            start_date=date_str,
            end_date=date_str,
            frequency="5",
            adjustflag="3",
        )
        if rs.error_code != "0":
            print(f"[price] baostock 分钟线失败 {code} {date_str}: {rs.error_msg}")
            return None
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "time", "code", "open", "high", "low", "close", "volume", "amount"])
    # time: 20260706093500000
    df["_hm"] = df["time"].astype(str).str.slice(8, 12)  # HHMM
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    _MIN_CACHE[key] = df
    return df


def _fetch_minute_sina(code: str, date_str: str) -> Optional[pd.DataFrame]:
    """新浪近约 5 个交易日 1 分钟线。"""
    if ak is None:
        return None
    key = f"sina_{code}"
    if key not in _MIN_CACHE:
        df = _retry(
            f"新浪分钟 {code}",
            ak.stock_zh_a_minute,
            symbol=to_sina_symbol(code),
            period="1",
            adjust="",
            max_attempts=2,
            base_delay=0.8,
        )
        if df is None or df.empty:
            return None
        df = df.copy()
        df["_ts"] = pd.to_datetime(df["day"])
        _MIN_CACHE[key] = df
    df = _MIN_CACHE[key]
    day = df[df["_ts"].dt.strftime("%Y-%m-%d") == date_str].copy()
    if day.empty:
        return None
    day["_hm"] = day["_ts"].dt.strftime("%H%M")
    day["close"] = pd.to_numeric(day["close"], errors="coerce")
    return day


def _pick_hm_price(df: pd.DataFrame, hhmm: str) -> Optional[float]:
    """hhmm='09:35' → 优先精确，否则向后最近，再向前最近。"""
    if df is None or df.empty or "_hm" not in df.columns:
        return None
    target = hhmm.replace(":", "")
    close_col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
    if close_col is None:
        return None
    exact = df[df["_hm"] == target]
    if not exact.empty:
        try:
            return round(float(exact.iloc[0][close_col]), 2)
        except (TypeError, ValueError):
            return None
    # 数值比较取最近
    try:
        target_n = int(target)
        df = df.copy()
        df["_n"] = pd.to_numeric(df["_hm"], errors="coerce")
        after = df[df["_n"] >= target_n].sort_values("_n")
        if not after.empty:
            return round(float(after.iloc[0][close_col]), 2)
        before = df[df["_n"] <= target_n].sort_values("_n")
        if not before.empty:
            return round(float(before.iloc[-1][close_col]), 2)
    except Exception:
        pass
    return None


def get_minute_price(code: str, date_str: str, hhmm: str) -> Optional[float]:
    """
    取 09:35 / 13:15 价格。
    优先 baostock 5 分钟 → 新浪 1 分钟 → 日线近似（开盘/收盘）。
    """
    df = _fetch_minute_baostock(code, date_str)
    p = _pick_hm_price(df, hhmm) if df is not None else None
    if p is not None:
        return p

    df = _fetch_minute_sina(code, date_str)
    p = _pick_hm_price(df, hhmm) if df is not None else None
    if p is not None:
        return p

    # 兜底：9:35≈开盘，13:15≈收盘（标记由上层 skip_reason 可选记录）
    ohlc = get_daily_ohlc(code, date_str)
    if not ohlc:
        return None
    if hhmm.startswith("09"):
        return ohlc["open"]
    return ohlc["close"]


def is_limit_up_at_time(code: str, date_str: str, hhmm: str) -> Optional[bool]:
    price = get_minute_price(code, date_str, hhmm)
    prev = get_prev_close(code, date_str)
    if price is None or prev is None:
        return None
    return is_at_limit_up(price, prev, code)


def check_second_board(code: str, buy_date: str) -> Optional[bool]:
    """买入日是否涨停（冲击二板）。优先日线判断，避免涨停池接口拖慢。"""
    ohlc = get_daily_ohlc(code, buy_date)
    prev = get_prev_close(code, buy_date)
    if ohlc is not None and prev is not None:
        return is_at_limit_up(ohlc["close"], prev, code) or is_at_limit_up(ohlc["high"], prev, code)
    if ak is None:
        return None
    date8 = buy_date.replace("-", "")
    df = _retry(f"涨停池 {date8}", ak.stock_zt_pool_em, date=date8, max_attempts=2, base_delay=0.6)
    if df is None or getattr(df, "empty", True) or "代码" not in getattr(df, "columns", []):
        return None
    codes = set(df["代码"].astype(str).str.zfill(6))
    return str(code).zfill(6) in codes


def resolve_sell_day_prices(code: str, sell_date: str, max_roll_days: int = 8) -> dict:
    """
    卖出日取价规则：
    - 一字/开盘涨停：跳过 9:35，等 13:15
    - 9:35 跌停：该时点卖不出，跳过 9:35
    - 13:15 仍涨停，或 13:15 跌停：当日卖不出，滚到下一交易日（D3+）早盘/午盘
    """
    result = {
        "sell_date": None,
        "sell_price_935": None,
        "sell_price_1315": None,
        "sell_price_half_935": None,
        "sell_price_half_1315": None,
        "skip_935": False,
        "skip_1315": False,
        "skip_reason": None,
        "rolled": False,
    }
    cur = sell_date
    for _ in range(max_roll_days):
        if not cur:
            break
        yizi = is_yizi_limit_up(code, cur)
        p935 = get_minute_price(code, cur, "09:35")
        p1315 = get_minute_price(code, cur, "13:15")
        prev = get_prev_close(code, cur)

        dt_935 = None
        if p935 is not None and prev is not None:
            dt_935 = is_at_limit_down(p935, prev, code)

        skip_935 = bool(yizi is True) or bool(dt_935 is True)
        skip_935_reason = None
        if dt_935 is True:
            skip_935_reason = f"{cur} 9:35跌停卖不出"
        elif yizi is True:
            skip_935_reason = f"{cur} 一字/涨停跳过9:35"

        if not skip_935 and yizi is None and p935 is not None and prev is not None:
            ohlc = get_daily_ohlc(code, cur)
            if (
                ohlc
                and is_at_limit_up(ohlc["open"], prev, code)
                and abs(ohlc["open"] - p935) / max(p935, 1e-6) <= 0.003
                and abs(ohlc["high"] - ohlc["low"]) / max(ohlc["open"], 1e-6) <= 0.005
            ):
                skip_935 = True
                skip_935_reason = f"{cur} 一字/涨停跳过9:35"

        zt_1315 = None
        dt_1315 = None
        if p1315 is not None and prev is not None:
            zt_1315 = is_at_limit_up(p1315, prev, code)
            dt_1315 = is_at_limit_down(p1315, prev, code)

        # 午盘涨停卖不出 / 午盘跌停卖不出 → 整日滚动到下一交易日
        if zt_1315 is True or dt_1315 is True:
            nxt = next_trading_day(cur)
            result["rolled"] = True
            why = "13:15仍涨停" if zt_1315 is True else "13:15跌停卖不出"
            result["skip_reason"] = f"{cur} {why}，滚到{nxt}"
            cur = nxt
            continue

        # 早盘跌停且午盘无有效价 → 也无法完成当日卖出，继续滚
        if skip_935 and dt_935 is True and p1315 is None:
            nxt = next_trading_day(cur)
            result["rolled"] = True
            result["skip_reason"] = f"{cur} 9:35跌停且无13:15价，滚到{nxt}"
            cur = nxt
            continue

        result["sell_date"] = cur
        if skip_935:
            result["skip_935"] = True
            extra = skip_935_reason or f"{cur} 跳过9:35"
            result["skip_reason"] = ((result.get("skip_reason") or "") + "; " + extra).strip("; ")
            result["sell_price_935"] = None
            result["sell_price_half_935"] = None
        else:
            result["sell_price_935"] = p935
            result["sell_price_half_935"] = p935

        result["sell_price_1315"] = p1315
        result["sell_price_half_1315"] = p1315
        result["skip_1315"] = False
        return result

    result["skip_reason"] = (result.get("skip_reason") or "") + "; 超过最大滚动天数仍未卖出"
    return result
