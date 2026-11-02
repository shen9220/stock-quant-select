"""行情与基础资料：复用首板 price_service + akshare 扩展。"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

# 复用首板战法价格服务（price_service 已迁到 每日交易记录/lib/）
_PS_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",  # 涨停回头低吸
        "..",  # 09-新模式
        "..",  # stock-quant-select
        "01_量化模型",
        "首板战法",
        "每日交易记录",
        "lib",
    )
)
if _PS_DIR not in sys.path:
    sys.path.insert(0, _PS_DIR)

import price_service as ps  # noqa: E402

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None

from rules import (  # noqa: E402
    LOOKBACK_LIMIT_UP_DAYS,
    MAIN_BOARD_PREFIXES,
    MAX_OBS_DAYS,
    MAX_PE_TTM,
    MAX_PRICE,
    MIN_DIVIDEND_PAYOUT,
    MIN_LIST_YEARS,
)

_NAME_CACHE: dict[str, str] = {}
_BASIC_CACHE: dict[str, dict] = {}


def is_main_board(code: str) -> bool:
    code = str(code).zfill(6)
    return code.startswith(MAIN_BOARD_PREFIXES)


def is_st_name(name: str) -> bool:
    n = (name or "").upper().replace(" ", "")
    return "ST" in n


def next_trading_day(d) -> Optional[str]:
    return ps.next_trading_day(d)


def prev_trading_day(d) -> Optional[str]:
    return ps.prev_trading_day(d)


def trading_days_after(start: str, n: int) -> Optional[str]:
    """涨停日后第 n 个交易日（n>=1 → 调整第 n 天）。"""
    cur = start
    for _ in range(n):
        cur = next_trading_day(cur)
        if not cur:
            return None
    return cur


def adj_day_of(limit_up_date: str, asof: str) -> int:
    """asof 相对涨停日的调整天数；涨停当日及之前返回 0。"""
    if not limit_up_date or not asof or asof <= limit_up_date:
        return 0
    d = 0
    cur = limit_up_date
    while True:
        cur = next_trading_day(cur)
        if not cur:
            break
        d += 1
        if cur >= asof:
            return d
        if d > MAX_OBS_DAYS + 5:
            break
    return d


def hold_day_of(buy_date: str, asof: str) -> int:
    """买入日=第1天。"""
    if not buy_date or not asof or asof < buy_date:
        return 0
    if asof == buy_date:
        return 1
    d = 1
    cur = buy_date
    while True:
        cur = next_trading_day(cur)
        if not cur:
            break
        d += 1
        if cur >= asof:
            return d
        if d > HOLD_MAX_SAFE():
            break
    return d


def HOLD_MAX_SAFE() -> int:
    return 40


def get_daily_df(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    return ps._fetch_daily(str(code).zfill(6), start, end)


def get_ohlc(code: str, date_str: str) -> Optional[dict]:
    return ps.get_daily_ohlc(str(code).zfill(6), date_str)


def is_limit_up_close(code: str, date_str: str) -> Optional[bool]:
    ohlc = get_ohlc(code, date_str)
    if not ohlc:
        return None
    prev = ps.get_prev_close(code, date_str)
    if prev is None:
        return None
    return ps.is_at_limit_up(ohlc["close"], prev, code)


def is_yizi_limit_up(code: str, date_str: str) -> Optional[bool]:
    return ps.is_yizi_limit_up(str(code).zfill(6), date_str)


def calc_ma_at(df: pd.DataFrame, asof_date: str) -> dict:
    """截至 asof_date（含）的 MA10/20/25/30 与收盘。"""
    if df is None or df.empty:
        return {}
    work = df.copy()
    work["日期"] = pd.to_datetime(work["日期"])
    asof = pd.to_datetime(asof_date)
    sub = work[work["日期"] <= asof].sort_values("日期")
    if sub.empty:
        return {}
    closes = sub["收盘"].astype(float)
    row = sub.iloc[-1]
    out = {
        "date": row["日期"].strftime("%Y-%m-%d"),
        "close": float(row["收盘"]),
        "open": float(row["开盘"]) if pd.notna(row.get("开盘")) else None,
        "high": float(row["最高"]) if "最高" in row and pd.notna(row["最高"]) else None,
        "low": float(row["最低"]) if "最低" in row and pd.notna(row["最低"]) else None,
    }
    for n, key in ((10, "ma10"), (20, "ma20"), (25, "ma25"), (30, "ma30")):
        out[key] = round(float(closes.tail(n).mean()), 3) if len(closes) >= n else None
    return out


def build_ma_series(code: str, limit_up_date: str, max_days: int = MAX_OBS_DAYS) -> dict:
    """
    涨停后第 1～max_days 个交易日的均线快照。
    key 为字符串天数 '1'..'14'。
    """
    code = str(code).zfill(6)
    start = (pd.to_datetime(limit_up_date) - timedelta(days=120)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    # 拉到足够远，便于算 MA30
    last_obs = trading_days_after(limit_up_date, max_days)
    if last_obs:
        end = max(end, last_obs)
    df = get_daily_df(code, start, end)
    series: dict[str, dict] = {}
    for d in range(1, max_days + 1):
        day = trading_days_after(limit_up_date, d)
        if not day:
            break
        if pd.to_datetime(day) > pd.to_datetime(datetime.now().strftime("%Y-%m-%d")):
            break
        snap = calc_ma_at(df, day)
        if snap:
            snap["adj_day"] = d
            # 十字星 / 阴线标记（补仓参考）
            o, c, h, l = snap.get("open"), snap.get("close"), snap.get("high"), snap.get("low")
            snap["is_yin"] = bool(o is not None and c is not None and c < o)
            snap["is_doji"] = False
            if o and c and h and l and h > l:
                body = abs(c - o)
                snap["is_doji"] = body / (h - l) <= 0.25
            series[str(d)] = snap
    return series


def in_dip_zone(ma10, ma20, price) -> bool:
    if ma10 is None or ma20 is None or price is None:
        return False
    return ma20 < float(price) < float(ma10) and float(ma10) > float(ma20)


def compute_stop_loss_price(buy_price: float, ma20, ma25, ma30) -> dict:
    """
    止损参考（放宽）：
    - 主档展示价 = buy * (1 - 6%) 
    - 触发：现价跌破 MA30，或 浮亏 ≤ -6%（两条件任一）
    - MA25 仅参考展示
    """
    from rules import STOP_LOSS_MA_PCT

    buy = float(buy_price)
    pct_stop = round(buy * (1 + STOP_LOSS_MA_PCT / 100.0), 3)

    def _ma_pnl(level) -> float | None:
        if level is None:
            return None
        return round((float(level) - buy) / buy * 100, 2)

    ma25_lv = round(float(ma25), 3) if ma25 is not None else None
    ma30_lv = round(float(ma30), 3) if ma30 is not None else None
    return {
        "stop_loss_price": pct_stop,
        "stop_loss_pnl": round(STOP_LOSS_MA_PCT, 2),
        "stop_loss_pct_level": pct_stop,
        "stop_loss_pct_max_level": pct_stop,
        "stop_loss_ma_level": ma30_lv,  # 主辅均线：30 日
        "stop_loss_ma25_level": ma25_lv,
        "stop_loss_ma30_level": ma30_lv,
        "stop_loss_ma25_pnl": _ma_pnl(ma25_lv),
        "stop_loss_ma30_pnl": _ma_pnl(ma30_lv),
        "stop_loss_ma20_at_buy": round(float(ma20), 3) if ma20 is not None else None,
        "stop_loss_ma25_at_buy": ma25_lv,
        "stop_loss_ma30_at_buy": ma30_lv,
    }


def resolve_code_name(name: str = "", code: str = "") -> tuple[str, str]:
    name = (name or "").strip()
    # 东财等源偶发「生 意 宝」类空格名，归一后再查
    name_compact = "".join(name.split())
    code = (code or "").strip()
    if code and code.isdigit():
        code = code.zfill(6)
        official = name_by_code(code)
        return code, official or name_compact or name
    if name_compact:
        c = code_by_name(name_compact) or (code_by_name(name) if name != name_compact else None)
        if c:
            return c, name_by_code(c) or name_compact
    return code, name_compact or name


def name_by_code(code: str) -> Optional[str]:
    code = str(code).zfill(6)
    if code in _NAME_CACHE:
        return _NAME_CACHE[code]
    # baostock 单票
    try:
        if ps.baostock_enabled() and ps._bs_ensure_login():
            import baostock as bs

            with ps._BS_LOCK:
                rs = bs.query_stock_basic(code=ps.to_bs_code(code))
                if rs.error_code == "0" and rs.next():
                    row = rs.get_row_data()
                    # code, code_name, ipoDate, outDate, type, status
                    if len(row) >= 2 and row[1]:
                        _NAME_CACHE[code] = row[1]
                        return row[1]
    except Exception:
        pass
    if ak is not None:
        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                m = dict(zip(df["item"].astype(str), df["value"].astype(str)))
                nm = m.get("股票简称") or m.get("名称")
                if nm:
                    _NAME_CACHE[code] = nm
                    return nm
        except Exception:
            pass
    return None


def code_by_name(name: str) -> Optional[str]:
    name = (name or "").strip()
    name_compact = "".join(name.split())
    if not name_compact:
        return None
    for c, n in _NAME_CACHE.items():
        nn = "".join(str(n).split())
        if n == name or nn == name_compact or name in str(n) or name_compact in nn:
            return c
    if ak is None:
        return None
    # 1) 东财实时行情（相对稳）
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty and "名称" in df.columns and "代码" in df.columns:
            names = df["名称"].astype(str)
            hit = df[names == name]
            if hit.empty and name_compact != name:
                hit = df[names.map(lambda s: "".join(str(s).split())) == name_compact]
            if hit.empty:
                hit = df[names.str.contains(name_compact, na=False)]
            if not hit.empty:
                c = str(hit.iloc[0]["代码"]).zfill(6)
                _NAME_CACHE[c] = str(hit.iloc[0]["名称"])
                return c
    except Exception as e:
        print(f"[market] spot name lookup fail: {e}")
    # 2) 码表（可能因北交所代理失败）
    try:
        df = ak.stock_info_a_code_name()
        if df is not None and not df.empty:
            names = df["name"].astype(str)
            hit = df[names == name]
            if hit.empty and name_compact != name:
                hit = df[names.map(lambda s: "".join(str(s).split())) == name_compact]
            if hit.empty:
                hit = df[names.str.contains(name_compact, na=False)]
            if not hit.empty:
                c = str(hit.iloc[0]["code"]).zfill(6)
                _NAME_CACHE[c] = str(hit.iloc[0]["name"])
                return c
    except Exception as e:
        print(f"[market] code_name table fail: {e}")
    return None


def fetch_boards(code: str) -> dict:
    """
    行业 / 概念分离：
    - 行业：优先 baostock 证监会行业；其次东财 F10 板块前几位行业层级
    - 概念：东财 F10 所属板块中过滤掉行业/地域/涨停榜噪音后，取排名最前的概念
    """
    code = str(code).zfill(6)
    industry = None
    concept = None
    # baostock 行业
    try:
        if ps.baostock_enabled() and ps._bs_ensure_login():
            import baostock as bs
            with ps._BS_LOCK:
                rs = bs.query_stock_industry(code=ps.to_bs_code(code))
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
            if rows:
                # calendarDate, code, code_name, industry, industryClassification
                industry = rows[0][3] if len(rows[0]) > 3 else None
    except Exception as e:
        print(f"[market] bs industry fail {code}: {e}")

    # 东财 F10 核心题材 / 所属板块
    try:
        import requests
        sess = requests.Session()
        sess.trust_env = False
        prefix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code={prefix}{code}"
        r = sess.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok:
            data = r.json() or {}
            boards = data.get("ssbk") or []
            noise_kw = (
                "昨日", "最近", "热股", "连板", "涨停", "首板", "高振幅",
                "中报", "年报", "题材股", "沪股通", "深股通", "融资融券",
            )
            industry_ranks = []
            concept_cands = []
            for b in boards:
                name = str(b.get("BOARD_NAME") or "").strip()
                rank = int(b.get("BOARD_RANK") or 999)
                if not name:
                    continue
                if name.endswith("板块") and len(name) <= 6:
                    continue  # 上海板块/广东板块
                if any(k in name for k in noise_kw):
                    continue
                if rank <= 3:
                    industry_ranks.append((rank, name))
                    continue
                concept_cands.append((rank, name))
            if not industry and industry_ranks:
                # 取第 2 档细分行业，否则第 1 档
                industry_ranks.sort()
                industry = industry_ranks[1][1] if len(industry_ranks) >= 2 else industry_ranks[0][1]
            if concept_cands:
                concept_cands.sort()
                # 优先带「概念」字样，否则取排名最前
                with_word = [x for x in concept_cands if "概念" in x[1]]
                concept = (with_word[0] if with_word else concept_cands[0])[1]
    except Exception as e:
        print(f"[market] em boards fail {code}: {e}")

    return {"industry_board": industry, "concept_board": concept}


def fetch_basic_filters(code: str) -> dict:
    """上市年限 / PE / 分红 / 行业概念；失败字段标 data_gap。"""
    code = str(code).zfill(6)
    if code in _BASIC_CACHE:
        return dict(_BASIC_CACHE[code])
    out = {
        "list_date": None,
        "list_years_ok": None,
        "pe_ttm": None,
        "pe_ok": None,
        "dividend_ok": None,
        "industry_board": None,
        "concept_board": None,
        "data_gap": [],
    }

    boards = fetch_boards(code)
    out["industry_board"] = boards.get("industry_board")
    out["concept_board"] = boards.get("concept_board")
    if not out["industry_board"]:
        out["data_gap"].append("industry")
    if not out["concept_board"]:
        out["data_gap"].append("concept")

    if ak is None:
        out["data_gap"].append("akshare_missing")
        _BASIC_CACHE[code] = out
        return out

    # PE / 上市：东财个股资料（可能因代理失败）
    try:
        import requests
        sess = requests.Session()
        sess.trust_env = False
        # 用 baostock 上市日兜底
        if ps.baostock_enabled() and ps._bs_ensure_login():
            import baostock as bs
            with ps._BS_LOCK:
                rs = bs.query_stock_basic(code=ps.to_bs_code(code))
                if rs.error_code == "0" and rs.next():
                    row = rs.get_row_data()
                    # code, code_name, ipoDate, outDate, type, status
                    if len(row) >= 3 and row[2]:
                        ld = str(row[2])[:10]
                        out["list_date"] = ld
                        try:
                            years = (datetime.now() - datetime.strptime(ld, "%Y-%m-%d")).days / 365.25
                            out["list_years_ok"] = years >= MIN_LIST_YEARS
                        except ValueError:
                            out["data_gap"].append("list_date")
    except Exception as e:
        out["data_gap"].append(f"list:{e}")

    try:
        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and not df.empty:
            # 兼容多列
            cols = list(df.columns)
            if "item" in cols and "value" in cols:
                m = dict(zip(df["item"].astype(str), df["value"].astype(str)))
            else:
                m = {}
            if not out["industry_board"]:
                out["industry_board"] = m.get("行业") or None
            if not out["list_date"] and m.get("上市时间"):
                ld = str(m.get("上市时间")).replace("/", "-")
                if len(ld) == 8 and ld.isdigit():
                    ld = f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}"
                out["list_date"] = ld[:10]
                try:
                    years = (datetime.now() - datetime.strptime(out["list_date"], "%Y-%m-%d")).days / 365.25
                    out["list_years_ok"] = years >= MIN_LIST_YEARS
                except ValueError:
                    pass
            pe = m.get("市盈率-动态") or m.get("市盈率")
            if pe not in (None, "-", ""):
                try:
                    pe_f = float(pe)
                    out["pe_ttm"] = pe_f
                    out["pe_ok"] = 0 < pe_f < MAX_PE_TTM
                except ValueError:
                    out["data_gap"].append("pe")
            else:
                out["data_gap"].append("pe")
    except Exception as e:
        out["data_gap"].append(f"info:{type(e).__name__}")

    try:
        div = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
        if div is not None and not div.empty:
            out["dividend_ok"] = True
            out["data_gap"].append("dividend_payout_approx")
        else:
            out["dividend_ok"] = None
            out["data_gap"].append("dividend")
    except Exception:
        out["dividend_ok"] = None
        out["data_gap"].append("dividend")

    _BASIC_CACHE[code] = out
    return dict(out)


def price_ok(price) -> bool:
    try:
        return price is not None and float(price) <= MAX_PRICE
    except (TypeError, ValueError):
        return False


_ZT_POOL_CACHE: dict[str, pd.DataFrame] = {}
_ZT_CODES_CACHE: dict[str, set[str]] = {}
_FAST_CAL_READY = False


def _ensure_fast_calendar() -> None:
    """ak/baostock 日历不可用时，注入工作日日历，避免每次 prev/next 都重试新浪。"""
    global _FAST_CAL_READY
    if _FAST_CAL_READY:
        return
    try:
        if getattr(ps, "_TRADE_CAL_CACHE", None) is not None:
            _FAST_CAL_READY = True
            return
        # 不调用 get_trade_calendar()（会反复打挂掉的新浪接口）；直接注入工作日近似日历
        rows = []
        cur = datetime(2024, 1, 1)
        end = datetime(2027, 12, 31)
        while cur <= end:
            if cur.weekday() < 5:
                rows.append({"trade_date": cur})
            cur += timedelta(days=1)
        ps._TRADE_CAL_CACHE = pd.DataFrame(rows)
        print("[market] 使用工作日近似交易日历（跳过不可用的 ak/baostock 日历）")
        _FAST_CAL_READY = True
    except Exception as e:
        print(f"[market] fast calendar fail: {e}")


def fetch_zt_pool(date_yyyymmdd: str) -> pd.DataFrame:
    """东财涨停池；date 格式 YYYYMMDD。带进程内缓存。"""
    _ensure_fast_calendar()
    key = str(date_yyyymmdd).replace("-", "")
    if key in _ZT_POOL_CACHE:
        return _ZT_POOL_CACHE[key].copy()
    if ak is None:
        return pd.DataFrame()
    try:
        df = ak.stock_zt_pool_em(date=key)
        if df is None:
            df = pd.DataFrame()
        _ZT_POOL_CACHE[key] = df
        return df.copy()
    except Exception as e:
        print(f"[market] zt_pool {key} fail: {e}")
        _ZT_POOL_CACHE[key] = pd.DataFrame()
        return pd.DataFrame()


def zt_pool_codes(date_str: str) -> set[str] | None:
    """
    某日东财涨停代码集合。
    空表 → 空 set（当日无涨停/非交易日）；仅列异常时返回 None。
    """
    _ensure_fast_calendar()
    key = str(date_str).replace("-", "")[:8]
    if not (len(key) == 8 and key.isdigit()):
        key = str(date_str)[:10].replace("-", "")
    if key in _ZT_CODES_CACHE:
        return set(_ZT_CODES_CACHE[key])
    if key not in _ZT_POOL_CACHE:
        fetch_zt_pool(key)
    df = _ZT_POOL_CACHE.get(key)
    if df is None:
        return None
    if df.empty:
        _ZT_CODES_CACHE[key] = set()
        return set()
    col = "代码" if "代码" in df.columns else None
    if not col:
        for c in df.columns:
            if "代码" in str(c) or str(c).lower() == "code":
                col = c
                break
    if not col:
        return None
    codes = {str(x).zfill(6) for x in df[col].tolist()}
    _ZT_CODES_CACHE[key] = codes
    return set(codes)


def clear_zt_pool_cache() -> None:
    _ZT_POOL_CACHE.clear()
    _ZT_CODES_CACHE.clear()


def _was_limit_up_day(code: str, date_str: str) -> Optional[bool]:
    """优先东财涨停池；不可用时回退收盘价。None=无法确认，不得放行。"""
    code = str(code).zfill(6)
    pool = zt_pool_codes(date_str)
    if pool is not None:
        return code in pool
    return is_limit_up_close(code, date_str)


def first_limit_up_in_window(
    code: str,
    asof: str,
    window: int = LOOKBACK_LIMIT_UP_DAYS,
    *,
    strict: bool = True,
) -> bool:
    """asof 涨停且此前 window-1 日无涨停。strict 下未知日直接不通过。"""
    _ensure_fast_calendar()
    code = str(code).zfill(6)
    if _was_limit_up_day(code, asof) is not True:
        return False
    cur = asof
    for _ in range(window - 1):
        cur = prev_trading_day(cur)
        if not cur:
            break
        flag = _was_limit_up_day(code, cur)
        if flag is True:
            return False
        if flag is None and strict:
            return False
    return True


def preload_zt_pools_for_window(asof: str, window: int = LOOKBACK_LIMIT_UP_DAYS) -> list[str]:
    """预拉 asof 及此前 window-1 日涨停池。"""
    _ensure_fast_calendar()
    loaded = []
    cur = asof
    for _ in range(window):
        if not cur:
            break
        zt_pool_codes(cur)
        loaded.append(cur)
        cur = prev_trading_day(cur)
    return loaded


def get_minute_price(code: str, date_str: str, hhmm: str) -> Optional[float]:
    return ps.get_minute_price(str(code).zfill(6), date_str, hhmm)
