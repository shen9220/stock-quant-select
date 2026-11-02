"""
价格获取与一字/涨停判定。
分钟价优先 baostock 5 分钟 K；baostock 不可达时自动容灾降级到新浪 1 分钟（避免卖价长期全空）。
日线用 akshare（东财/腾讯兜底）。失败返回 None，由调用方留空或稍后补全。
"""
from __future__ import annotations

import os
import sys

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_LIB_DIR)
_JOBS_DIR = os.path.join(_BASE_DIR, "jobs")
for _p in (_BASE_DIR, _LIB_DIR, _JOBS_DIR, os.path.dirname(_BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import BASE_DIR, ROOT_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import json
import os
import threading
import time
import urllib.request
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

# ── py_mini_racer 架构兼容性检测 ────────────────────────────
# akshare 的新浪交易日历依赖 py_mini_racer 执行 JS；若其 dylib 架构不匹配
# （如 x86_64 装到 arm64），每次调用都会报 dlopen 错误并重试，产生噪音。
# 启动时检测一次，不兼容则跳过新浪，直接走 baostock/本地日历。
_SINA_CAL_OK: Optional[bool] = None


def _sina_calendar_available() -> bool:
    """检测新浪交易日历（py_mini_racer）是否在当前架构可用。"""
    global _SINA_CAL_OK
    if _SINA_CAL_OK is not None:
        return _SINA_CAL_OK
    try:
        import py_mini_racer  # noqa: F401
        # 尝试实例化（触发 dylib 加载）。失败即架构不兼容。
        ctx = py_mini_racer.MiniRacer()
        _ = ctx.eval("1+1")
        _SINA_CAL_OK = True
    except Exception:
        _SINA_CAL_OK = False
    return _SINA_CAL_OK
_BS_LOCK = threading.Lock()
_BS_LOGGED_IN = False
_BS_DISABLED = False
_BS_DISABLE_MSG = ""
# ── 容灾降级（baostock 不可达时自动切新浪，避免卖价长期全空）──
_BS_FAIL_COUNT = 0
_BS_FAIL_THRESHOLD = 2          # 连续登录失败达到该次数即判定为不可达，启用新浪容灾
_BS_DISABLE_KIND = ""           # "blacklist"=永久禁用；"unreachable"=临时禁用，定时重试自愈
_BS_LAST_RETRY = 0.0
_BS_RETRY_INTERVAL = 600       # 不可达禁用后，每 10 分钟尝试重连一次


def baostock_enabled() -> bool:
    """baostock 是否可用（登录成功且未被禁用）。"""
    return bs is not None and not _BS_DISABLED


def _disable_baostock(reason: str, kind: str = "blacklist") -> None:
    global _BS_DISABLED, _BS_LOGGED_IN, _BS_DISABLE_MSG, _BS_DISABLE_KIND
    if _BS_DISABLED:
        return
    _BS_DISABLED = True
    _BS_LOGGED_IN = False
    _BS_DISABLE_KIND = kind
    _BS_DISABLE_MSG = reason
    tag = "永久（黑名单/限流）" if kind == "blacklist" else "临时（不可达，启用新浪容灾，将定时重试）"
    print(f"[price] baostock 已禁用（{tag}）: {reason}")


def _bs_ensure_login():
    global _BS_LOGGED_IN, _BS_FAIL_COUNT, _BS_LAST_RETRY, _BS_DISABLED, _BS_DISABLE_KIND
    if bs is None:
        return False
    if _BS_DISABLED:
        # 不可达导致的临时禁用：到点尝试重连自愈（避免长期依赖新浪）
        if _BS_DISABLE_KIND == "unreachable":
            now = time.time()
            if now - _BS_LAST_RETRY >= _BS_RETRY_INTERVAL:
                _BS_LAST_RETRY = now
                try:
                    lg = bs.login()
                except Exception as e:  # noqa: BLE001
                    print(f"[price] baostock 容灾重试登录异常: {e}")
                    return False
                if lg.error_code == "0":
                    _BS_DISABLED = False
                    _BS_DISABLE_KIND = ""
                    _BS_LOGGED_IN = True
                    _BS_FAIL_COUNT = 0
                    print("[price] baostock 容灾恢复：重连成功，重新启用")
                    return True
                # 仍不可达：保持禁用，等下次重试
        return False
    with _BS_LOCK:
        if _BS_LOGGED_IN:
            return True
        try:
            lg = bs.login()
        except Exception as e:  # noqa: BLE001
            print(f"[price] baostock 登录异常: {e}")
            lg = None
        if lg is None or lg.error_code != "0":
            msg = (lg.error_msg or "未知错误") if lg is not None else "登录异常"
            print(f"[price] baostock 登录失败: {msg}")
            # 黑名单/账号类错误：永久跳过，避免反复 login 触发限流
            if any(k in msg for k in ("黑名单", "black", "封禁", "禁止", "频繁")):
                _disable_baostock(msg, kind="blacklist")
                return False
            # 网络/域名不可达等：累计失败，达阈值后启用新浪容灾降级（而非逐笔留空）
            _BS_FAIL_COUNT += 1
            if _BS_FAIL_COUNT >= _BS_FAIL_THRESHOLD:
                _disable_baostock(
                    f"连续 {_BS_FAIL_COUNT} 次登录失败（疑似网络不可达），启用新浪容灾降级",
                    kind="unreachable",
                )
            return False
        _BS_LOGGED_IN = True
        _BS_FAIL_COUNT = 0
        return True


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


def get_trade_calendar() -> Optional[pd.DataFrame]:
    """交易日历：本地法定节假日 → 新浪 → baostock。"""
    global _TRADE_CAL_CACHE
    if _TRADE_CAL_CACHE is not None:
        return _TRADE_CAL_CACHE

    # 本地日历优先：稳定、无外部依赖、含中秋/国庆等法定休市
    try:
        import fetch_calendar as fc

        days = fc.load_calendar("2023-01-01", "2026-12-31")
        if days:
            _TRADE_CAL_CACHE = pd.DataFrame({"trade_date": pd.to_datetime(days)})
            print(f"[price] 交易日历使用本地法定节假日表（{len(days)} 天）")
            return _TRADE_CAL_CACHE
    except Exception as e:
        print(f"[price] 本地交易日历失败: {e}")

    # 新浪：依赖 py_mini_racer，架构不兼容时直接跳过（避免 dlopen 报错噪音）
    if ak is not None and _sina_calendar_available():
        df = _retry("交易日历", ak.tool_trade_date_hist_sina, max_attempts=2, base_delay=0.5)
        if df is not None and not df.empty:
            df = df.copy()
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            _TRADE_CAL_CACHE = df
            return _TRADE_CAL_CACHE

    if bs is not None and not _BS_DISABLED and _bs_ensure_login():
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

    df = None
    # 1) 腾讯日线（baostock 禁用时优先）
    if ak is not None:
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

    # 2) baostock 日线（仅未禁用时）
    if (df is None or df.empty) and baostock_enabled():
        df = _fetch_daily_baostock(code6, start, end)

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
    # 铁律：竞价结束前（9:25）今开不可靠，常残留 D0 开盘/昨收，禁止写入买入价。
    if date_str == today:
        now = datetime.now()
        if now.hour < 9 or (now.hour == 9 and now.minute < 25):
            return None
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

    def _pull_once() -> Optional[pd.DataFrame]:
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
                msg = rs.error_msg or ""
                print(f"[price] baostock 分钟线失败 {code} {date_str}: {msg}")
                if "网络" in msg or "Broken pipe" in msg or "接收" in msg:
                    global _BS_LOGGED_IN
                    _BS_LOGGED_IN = False
                return None
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
        if not rows:
            # 当日 EOD 尚未发布（baostock 收盘后要等一段时间才出数据）：
            # 返回空表而不是 None，便于上层区分「该日无数据」与「接口失败」。
            # 不写缓存，后续重试还能重新拉。
            return pd.DataFrame(
                columns=["date", "time", "code", "open", "high", "low", "close", "volume", "amount", "_hm"]
            )
        df = pd.DataFrame(rows, columns=["date", "time", "code", "open", "high", "low", "close", "volume", "amount"])
        df["_hm"] = df["time"].astype(str).str.slice(8, 12)
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    df = _pull_once()
    if df is None:
        if _bs_ensure_login():
            df = _pull_once()
    if df is None:
        return None
    _MIN_CACHE[key] = df
    return df


_SINA_MIN_URL = (
    "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    "?symbol={symbol}&scale={scale}&ma=no&datalen={n}"
)


def _fetch_minute_sina_http(code: str, date_str: str) -> Optional[pd.DataFrame]:
    """新浪分钟线直连（单标的单请求；platform_gate 互斥）。"""
    key = f"sina_http_{code}_{date_str}"
    if key in _MIN_CACHE:
        return _MIN_CACHE[key]
    url = _SINA_MIN_URL.format(symbol=to_sina_symbol(code), scale=1, n=1970)
    try:
        import requests as _req
        from platform_gate import platform_slot

        with platform_slot("sina"):
            r = _req.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 429:
                from platform_gate import PlatformQuotaExceeded, mark_rate_limited
                mark_rate_limited("sina", f"HTTP 429 minute {code}")
                raise PlatformQuotaExceeded("sina", "http429", 0, 0)
            r.raise_for_status()
            rows = r.json()
        if not isinstance(rows, list) or not rows:
            return None
        df = pd.DataFrame(rows)
        df = df[df["day"].astype(str).str[:10] == date_str].copy()
        if df.empty:
            return None
        df["_hm"] = df["day"].astype(str).str.slice(11, 16).str.replace(":", "", regex=False)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        _MIN_CACHE[key] = df
        return df
    except Exception as e:
        from platform_gate import PlatformQuotaExceeded
        if isinstance(e, PlatformQuotaExceeded):
            raise
        print(f"[price] 新浪分钟直连失败 {code} {date_str}: {type(e).__name__}: {e}", flush=True)
        return None


def _fetch_minute_sina(code: str, date_str: str) -> Optional[pd.DataFrame]:
    """新浪 1 分钟线：优先 HTTP 直连（带门禁）；akshare 路径也进同一门禁。"""
    # 先走直连，避免 mini_racer / 双通道并发
    hit = _fetch_minute_sina_http(code, date_str)
    if hit is not None:
        return hit
    if ak is None:
        return None
    key = f"sina_{code}"
    if key not in _MIN_CACHE:
        try:
            from platform_gate import platform_slot

            with platform_slot("sina"):
                df = _retry(
                    f"新浪分钟 {code}",
                    ak.stock_zh_a_minute,
                    symbol=to_sina_symbol(code),
                    period="1",
                    adjust="",
                    max_attempts=1,
                    base_delay=1.0,
                )
        except Exception:
            df = None
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


def _same_day_sina_fallback_enabled() -> bool:
    """当日 EOD 未发布时是否允许新浪 1 分钟兜底（默认开，可用环境变量关掉）。"""
    return os.environ.get("SINA_SAME_DAY_FALLBACK", "1").lower() in ("1", "true", "yes")


def _to_tx_symbol(code: str) -> str:
    c = str(code).zfill(6)
    return ("sh" if c.startswith(("5", "6", "9")) else "sz") + c


def _fetch_minute_tencent(code: str, date_str: str, period: str = "m5") -> Optional[pd.DataFrame]:
    """腾讯 mkline 分钟 K（m1≈近 3～4 日，m5≈近 15～17 日；不可翻页更深）。

    行格式：[YYYYMMDDHHMM, open, close, high, low, vol, ...]
    与腾讯日线共用 platform_gate('tencent')，禁止同平台并发。
    """
    key = f"tx_{period}_{code}_{date_str}"
    if key in _MIN_CACHE:
        return _MIN_CACHE[key]
    # 同 symbol+period 整段缓存，避免同日 9:35/13:15 打两次网
    bulk_key = f"tx_{period}_{code}_bulk"
    rows = None
    if bulk_key in _MIN_CACHE:
        rows = _MIN_CACHE[bulk_key]
    else:
        sym = _to_tx_symbol(code)
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={sym},{period},0,800"
        try:
            import requests as _req
            from platform_gate import platform_slot

            with platform_slot("tencent"):
                r = _req.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 429:
                    from platform_gate import mark_rate_limited
                    mark_rate_limited("tencent", f"HTTP 429 mkline {code}")
                    from platform_gate import PlatformQuotaExceeded
                    raise PlatformQuotaExceeded("tencent", "http429", 0, 0)
                r.raise_for_status()
                node = (r.json().get("data") or {}).get(sym) or {}
                rows = node.get(period) or []
            _MIN_CACHE[bulk_key] = rows
        except Exception as e:
            from platform_gate import PlatformQuotaExceeded
            if isinstance(e, PlatformQuotaExceeded):
                raise
            print(f"[price] 腾讯分钟失败 {code} {date_str} {period}: {type(e).__name__}: {e}", flush=True)
            return None
    if not rows:
        return None
    d8 = date_str.replace("-", "")
    out = []
    for row in rows:
        ts = str(row[0])
        if not ts.startswith(d8):
            continue
        out.append({"_hm": ts[8:12], "close": float(row[2]), "open": float(row[1])})
    if not out:
        return None
    df = pd.DataFrame(out)
    _MIN_CACHE[key] = df
    return df


def _try_alt_minute(code: str, date_str: str, hhmm: str) -> tuple[Optional[float], Optional[str]]:
    """baostock 不可用时的分钟链。默认：新浪1m → 腾讯m5。

    环境变量 BOARD_MINUTE_PREFER=sina|tx 时只打该平台（分轨专用，禁止跨平台连打）。
    AlphaFeed / mootdx 已排除。
    """
    prefer = os.environ.get("BOARD_MINUTE_PREFER", "").strip().lower()
    if prefer == "sina":
        chain = (("sina_1m", lambda: _fetch_minute_sina(code, date_str)),)
    elif prefer in ("tx", "tencent", "qq"):
        chain = (("tx_m5", lambda: _fetch_minute_tencent(code, date_str, "m5")),)
    else:
        chain = (
            ("sina_1m", lambda: _fetch_minute_sina(code, date_str)),
            ("tx_m5", lambda: _fetch_minute_tencent(code, date_str, "m5")),
        )
    for src, fetcher in chain:
        try:
            df = fetcher()
        except Exception as e:
            from platform_gate import PlatformQuotaExceeded
            if isinstance(e, PlatformQuotaExceeded):
                raise
            df = None
        p = _pick_hm_price(df, hhmm) if df is not None else None
        if p is not None:
            return p, src
    return None, None


def get_minute_price_ex(code: str, date_str: str, hhmm: str) -> tuple[Optional[float], Optional[str]]:
    """取分钟价，并返回数据源。

    优先级：
      1) baostock 5m（账号可用时）
      2) 新浪 1m → 腾讯 m5（分轨时可 BOARD_MINUTE_PREFER 限定单平台）

    本机窗口：sina1m≈9日 / tx_m5≈15～17日；更深历史暂无免费源。
    禁止用日线开盘/收盘冒充 9:35/13:15。同平台禁止并发（platform_gate）。
    """
    if baostock_enabled():
        df = _fetch_minute_baostock(code, date_str)
        p = _pick_hm_price(df, hhmm) if df is not None else None
        if p is not None:
            return p, "baostock_5m"
        if baostock_enabled():
            eod_missing = df is not None and getattr(df, "empty", False)
            if eod_missing and _same_day_sina_fallback_enabled():
                sp, src = _try_alt_minute(code, date_str, hhmm)
                if sp is not None:
                    print(
                        f"[price] baostock {date_str} 分钟柱未发布，"
                        f"临时用 {src} {code} {hhmm}={sp}",
                        flush=True,
                    )
                    return sp, src
            # 历史缺失也试一下多源（不静默：有结果才返回并带 source）
            sp, src = _try_alt_minute(code, date_str, hhmm)
            if sp is not None:
                print(
                    f"[price] baostock 无 {date_str} {hhmm}，改用 {src} {code}={sp}",
                    flush=True,
                )
                return sp, src
            print(
                f"[price] baostock 分钟价缺失 {code} {date_str} {hhmm}"
                f"{'（当日EOD未发布）' if eod_missing else ''}，留空待补",
                flush=True,
            )
            return None, None
        # 本次取数失败已触发容灾降级（不可达），下一个调用将走多源兜底
        return None, None

    sp, src = _try_alt_minute(code, date_str, hhmm)
    if sp is not None:
        return sp, src

    print(f"[price] 分钟价缺失 {code} {date_str} {hhmm}（禁止开盘/收盘兜底）")
    return None, None


def get_minute_price(code: str, date_str: str, hhmm: str) -> Optional[float]:
    """
    取 09:35 / 13:15 **真实分钟价**（只要价格，来源见 get_minute_price_ex）。

    数据源优先级：baostock 5m → 新浪1m → 腾讯m5。
    用户看盘口径优先 baostock 5 分钟柱；其它源必须写 sell_price_source。

    铁律（不可再犯）：
      早盘卖出价 = 09:35 分钟价，午盘卖出价 = 13:15 分钟价。
      **禁止**用日线开盘价冒充 9:35、用日线收盘价冒充 13:15。
      分钟线取不到时返回 None，由上层留空待补，绝不写错价。
    """
    return get_minute_price_ex(code, date_str, hhmm)[0]



def sell_prices_look_like_ohlc_fallback(
    code: str,
    sell_date: str,
    sell_935,
    sell_1315,
    tol: float = 0.015,
) -> bool:
    """
    检测历史错写：9:35 栏位=当日开盘 且 13:15 栏位=当日收盘
    （旧版 get_minute_price 日线兜底的典型指纹）。
    """
    ohlc = get_daily_ohlc(code, sell_date)
    if not ohlc:
        return False
    try:
        s935 = float(sell_935) if sell_935 not in (None, "") else None
        s1315 = float(sell_1315) if sell_1315 not in (None, "") else None
    except (TypeError, ValueError):
        return False
    if s935 is None and s1315 is None:
        return False
    open_p, close_p = float(ohlc["open"]), float(ohlc["close"])
    eq_open = s935 is not None and abs(s935 - open_p) < tol
    eq_close = s1315 is not None and abs(s1315 - close_p) < tol
    # 双字段都对齐开/收 → 高度疑似旧兜底
    if eq_open and eq_close:
        return True
    # 仅 9:35=开盘（用户报告的主症状）也视为污染
    if eq_open:
        return True
    return False


def is_limit_up_at_time(code: str, date_str: str, hhmm: str) -> Optional[bool]:
    price = get_minute_price(code, date_str, hhmm)
    prev = get_prev_close(code, date_str)
    if price is None or prev is None:
        return None
    return is_at_limit_up(price, prev, code)


def check_second_board(code: str, buy_date: str) -> Optional[bool]:
    """买入日是否封住二板（更精准）。

    口径（一进二）：
    1. D0（买入日前一交易日）收盘涨停 = 首板成立
    2. D1（买入日）收盘涨停 = 二板封住 → True
    仅盘中摸板（最高触及涨停）但收盘未封，不算二板。
    D0 无法判定时：退化为「买入日收盘是否涨停」。
    """
    code6 = str(code).zfill(6)
    ohlc = get_daily_ohlc(code6, buy_date)
    prev = get_prev_close(code6, buy_date)
    if ohlc is None or prev is None:
        # 日线缺失时用涨停池兜底（仍要求在池内 = 收盘口径）
        if ak is None:
            return None
        date8 = buy_date.replace("-", "")
        df = _retry(f"涨停池 {date8}", ak.stock_zt_pool_em, date=date8, max_attempts=2, base_delay=0.6)
        if df is None or getattr(df, "empty", True) or "代码" not in getattr(df, "columns", []):
            return None
        return code6 in set(df["代码"].astype(str).str.zfill(6))

    d1_sealed = is_at_limit_up(float(ohlc["close"]), float(prev), code6)
    if not d1_sealed:
        return False

    d0 = prev_trading_day(buy_date)
    if not d0:
        return True
    d0_ohlc = get_daily_ohlc(code6, d0)
    d0_prev = get_prev_close(code6, d0)
    if d0_ohlc is None or d0_prev is None:
        return True  # D1 已封板；D0 缺数时不强行否决
    d0_sealed = is_at_limit_up(float(d0_ohlc["close"]), float(d0_prev), code6)
    return bool(d0_sealed and d1_sealed)


def _slot_sellable(code: str, date_str: str, hhmm: str) -> tuple[bool, Optional[float], Optional[str], Optional[str]]:
    """判断某一时点(09:35/13:15)能否卖出，并返回该时点分钟价与来源。

    返回 (sellable, price, blocked_reason, source)
      - 涨停 → 卖不掉，顺延到下一交易日的**同一时点**
      - 跌停 → 同样顺延
      - 分钟价缺失（含 baostock 当日 EOD 未发布）→ 视作不可判定，不写价（宁可留空也不写错）
    """
    # 未来交易日尚未产生 9:35/13:15，不是「卖不出」，而是「还没到」。
    # 必须提前返回，否则会把未来日期当成卖不出，白跑接口并污染 skip_reason。
    today = datetime.now().strftime("%Y-%m-%d")
    if date_str > today:
        return False, None, f"{date_str} 尚未开盘", None

    price, source = get_minute_price_ex(code, date_str, hhmm)
    prev = get_prev_close(code, date_str)
    if price is None or prev is None:
        return False, None, f"{date_str} {hhmm} 分钟价缺失", None

    if is_at_limit_up(price, prev, code):
        return False, None, f"{date_str} {hhmm} 涨停卖不出", source
    if is_at_limit_down(price, prev, code):
        return False, None, f"{date_str} {hhmm} 跌停卖不出", source
    return True, price, None, source


def resolve_sell_day_prices(code: str, sell_date: str, max_roll_days: int = 8) -> dict:
    """
    卖出取价规则（两批完全独立滚动，用户 2026-10-16 确认）：

      D1 买入 → D2 起分两批卖：早盘批在 9:35，午盘批在 13:15。
      **每一批独立判断、独立顺延，且顺延后仍落在原本的时点**：
        - 早盘批：9:35 涨停/跌停 → 顺延到下一交易日的 9:35 再判断；
        - 午盘批：13:15 涨停/跌停 → 顺延到下一交易日的 13:15 再判断；
      两批互不影响：午盘批涨停不得牵连早盘批，反之亦然。

      例（605058 澳弘电子，D1=10-12 买入）：
        早盘批 10-13 9:35 可卖但用户口径为顺延至 10-14 → 10-14 9:35 = 65.54 成交；
        午盘批 10-13 13:15 涨停 → 10-14 13:15 涨停 → 10-15 13:15 涨停 → 10-16 13:15 = 66.69。

      sell_date 返回两批中**较晚**的成交日；另给 sell_date_935 / sell_date_1315 供追溯。
    """
    result = {
        "sell_date": None,
        "sell_date_935": None,
        "sell_date_1315": None,
        "sell_price_935": None,
        "sell_price_1315": None,
        "sell_price_half_935": None,
        "sell_price_half_1315": None,
        "skip_935": False,
        "skip_1315": False,
        "skip_reason": None,
        "rolled": False,
        # 顺延到的未来交易日（尚未开盘，非「卖不出」）
        "pending_935": None,
        "pending_1315": None,
        # 价格来源：baostock_5m（口径基准） / sina_1m（当日 EOD 未发布时的兜底）
        "sell_price_source": None,
        "sell_price_source_935": None,
        "sell_price_source_1315": None,
    }
    reasons: list[str] = []

    # ── 早盘批：独立顺延，始终在 9:35 ──
    cur = sell_date
    for _ in range(max_roll_days):
        if not cur:
            break
        ok, price, why, src = _slot_sellable(code, cur, "09:35")
        if ok:
            result["sell_price_935"] = price
            result["sell_price_half_935"] = price
            result["sell_date_935"] = cur
            result["sell_price_source_935"] = src
            break
        reasons.append(why or f"{cur} 9:35 不可卖")
        # 未来交易日尚未开盘 / 分钟价未落库：都不是「卖不出」，不得滚动卖出日，
        # 原地留 pending 等数据落库后补齐（否则 sell_date 会漂到下周，卖出价与周报全错）
        if why and ("尚未开盘" in why or "分钟价缺失" in why):
            result["pending_935"] = cur
            break
        result["rolled"] = True
        nxt = next_trading_day(cur)
        if nxt:
            result["skip_reason"] = f"{why}，滚到{nxt}"
        cur = nxt
    else:
        reasons.append("早盘批超过最大滚动天数仍未卖出")

    # ── 午盘批：独立顺延，始终在 13:15 ──
    cur = sell_date
    for _ in range(max_roll_days):
        if not cur:
            break
        ok, price, why, src = _slot_sellable(code, cur, "13:15")
        if ok:
            result["sell_price_1315"] = price
            result["sell_price_half_1315"] = price
            result["sell_date_1315"] = cur
            result["sell_price_source_1315"] = src
            break
        reasons.append(why or f"{cur} 13:15 不可卖")
        # 同早盘批：缺价 / 未开盘不滚动，原地等补
        if why and ("尚未开盘" in why or "分钟价缺失" in why):
            result["pending_1315"] = cur
            break
        result["rolled"] = True
        nxt = next_trading_day(cur)
        if nxt:
            result["skip_reason"] = f"{why}，滚到{nxt}"
        cur = nxt
    else:
        reasons.append("午盘批超过最大滚动天数仍未卖出")

    # 整体卖出日 = 两批中较晚者；只成交一批则取该批日期
    d935, d1315 = result["sell_date_935"], result["sell_date_1315"]
    cands = [d for d in (d935, d1315) if d]
    result["sell_date"] = max(cands) if cands else None
    # 一批都没成交 → 若已顺延到未来日，取该未来日为预期卖出日（供调度跟踪）
    if not cands:
        pend = [d for d in (result.get("pending_935"), result.get("pending_1315")) if d]
        result["sell_date"] = max(pend) if pend else sell_date

    result["skip_935"] = d935 is None
    result["skip_1315"] = d1315 is None
    # 来源汇总：两批同源则取该源，混源则标 mixed
    s935 = result.get("sell_price_source_935")
    s1315 = result.get("sell_price_source_1315")
    if s935 and s1315 and s935 != s1315:
        result["sell_price_source"] = f"mixed:{s935}+{s1315}"
    elif s935 or s1315:
        result["sell_price_source"] = s935 or s1315
    if reasons:
        result["skip_reason"] = "; ".join(dict.fromkeys(reasons))
    return result


# 进程启动时可显式禁用 baostock，避免历史回填/补价反复 login
if os.environ.get("DISABLE_BAOSTOCK", "").lower() in ("1", "true", "yes"):
    _disable_baostock("环境变量 DISABLE_BAOSTOCK=1")
