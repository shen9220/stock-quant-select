'''
cd /workspace/projects/stock-quant-select/01_量化模型/首板战法
python3 slow.py
python3 slow.py 20260716

低价一进二选股：D0 收盘后筛首板 → D1 开盘半仓冲击二板 → D2 收盘前清仓。
东方财富涨停池通常不含 ST，本脚本会用新浪ST列表+腾讯K线补充 ST 涨停。
仅本地打印与落盘，不发送通知。
'''

import akshare as ak  # 免费A股数据
import pandas as pd
from datetime import datetime, timedelta, time
import os
import re
import sys
import time as time_mod
import requests
from requests.exceptions import RequestException

# ---------- 一进二硬筛阈值（对齐人工偏好：低价ST优先 + 创业板早封）----------
MAX_PRICE_ST = 6.0          # 低价 ST
MAX_PRICE_NORMAL = 12.0     # 非 ST（覆盖创业板如格林精密约10元）
MAX_FIRST_SEAL_TIME = 103000  # 10:30
MAX_ZHABAN = 1
TURNOVER_MIN = 3.0          # 覆盖创业板缩量早板（如换手约3.9%）
TURNOVER_MAX = 25.0
TURNOVER_MAX_ST = 45.0      # ST 涨停换手常偏高（如*ST萃华）
FLOAT_MV_MIN, FLOAT_MV_MAX = 3e8, 80e8  # 3亿 ~ 80亿
ONE_WORD_SEAL_TIME = 92500  # 09:25
ONE_WORD_TURNOVER = 3.0
MIN_INDUSTRY_ZT = 2  # 同行业涨停家数下限（硬筛）
ST_INDUSTRY = "ST风险警示"  # ST 补充标的统一行业，便于板块计数
ST_LIMIT_PCT = 4.8  # 兼容 ST 5%/10% 涨停

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def _retry_fetch(description, func, *args, max_attempts=5, base_delay=1.5, **kwargs):
    """东方财富等接口易出现 TLS 中断（SSLEOFError），退避重试可提高成功率。"""
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except (RequestException, OSError) as e:
            last_err = e
            if attempt < max_attempts:
                wait = base_delay * (2 ** (attempt - 1))
                print(f"{description} 请求失败（{type(e).__name__}），{wait:.1f} 秒后重试 ({attempt}/{max_attempts})…")
                time_mod.sleep(wait)
    raise last_err


_ZT_POOL_CACHE = {}
_ST_META_CACHE = None
_TX_KLINE_CACHE = {}

# akshare 无数据时返回 (0,0) 且无列名，后续访问「代码」会 KeyError
_ZT_POOL_COLUMNS = (
    "序号", "代码", "名称", "涨跌幅", "最新价", "成交额",
    "流通市值", "总市值", "换手率", "封板资金", "首次封板时间",
    "最后封板时间", "炸板次数", "涨停统计", "连板数", "所属行业",
)


def clear_zt_pool_cache():
    _ZT_POOL_CACHE.clear()


def _normalize_zt_pool(df):
    """空涨停池统一为带标准列名的空表，避免 KeyError。"""
    if df is None or (getattr(df, "empty", True) and "代码" not in getattr(df, "columns", [])):
        return pd.DataFrame(columns=list(_ZT_POOL_COLUMNS))
    return df


def _zt_pool_codes(df):
    if df is None or df.empty or "代码" not in df.columns:
        return set()
    return set(df["代码"].astype(str))


def is_st_name(name):
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return False
    return bool(re.search(r"ST", str(name), re.IGNORECASE))


def _market_prefix(code):
    code = str(code)
    if code.startswith(("4", "8")) or code.startswith("92"):
        return "bj"
    return "sh" if code.startswith(("5", "6")) else "sz"


def load_st_meta():
    """从新浪 hs_a 拉取名称含 ST 的标的及流通市值口径（排除688/北交所）。"""
    global _ST_META_CACHE
    if _ST_META_CACHE is not None:
        return _ST_META_CACHE

    rows = []
    print("正在拉取 ST 标的列表（新浪，用于补充东财涨停池缺失的 ST）...")
    for page in range(1, 80):
        def _fetch(p=page):
            url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
            r = requests.get(
                url,
                params={
                    "page": p, "num": 100, "sort": "symbol", "asc": 1,
                    "node": "hs_a", "symbol": "", "_s_r_a": "page",
                },
                timeout=20,
                headers=_HTTP_HEADERS,
            )
            r.raise_for_status()
            text = (r.text or "").strip()
            if not text or text == "null":
                return []
            return r.json()

        try:
            batch = _retry_fetch(f"新浪ST列表 p{page}", _fetch, max_attempts=3, base_delay=1.0)
        except Exception as e:
            print(f"  新浪ST列表第{page}页失败: {e}")
            break
        if not batch:
            break
        for x in batch:
            name = str(x.get("name", ""))
            if not is_st_name(name):
                continue
            code = str(x.get("code", "")).zfill(6)
            # 选股本就不做 688/北交所，扫描时直接跳过以提速
            if code.startswith(("688", "8", "4")) or code.startswith("92"):
                continue
            try:
                price = float(x.get("trade") or 0)
                nmc = float(x.get("nmc") or 0)  # 万元
                mktcap = float(x.get("mktcap") or 0)
            except (TypeError, ValueError):
                price, nmc, mktcap = 0.0, 0.0, 0.0
            # 现价明显高于低价阈值的 ST，历史首板价也难进池，跳过加速
            if price > MAX_PRICE_ST * 2.5 and price > 0:
                continue
            float_mv_live = nmc * 1e4 if nmc else 0.0
            total_mv_live = mktcap * 1e4 if mktcap else 0.0
            rows.append({
                "代码": code,
                "名称": name,
                "最新价_live": price,
                "流通市值_live": float_mv_live,
                "总市值_live": total_mv_live,
            })
        time_mod.sleep(0.05)

    _ST_META_CACHE = rows
    print(f"  ST 标的共 {len(rows)} 只（已排除688/北交所/高价）")
    return _ST_META_CACHE


def fetch_sina_kline(code, datalen=80):
    """新浪日K备用。"""
    m = _market_prefix(code)
    if m == "bj":
        return pd.DataFrame()
    symbol = f"{m}{code}"
    cache_key = f"sina:{symbol}:{datalen}"
    if cache_key in _TX_KLINE_CACHE:
        return _TX_KLINE_CACHE[cache_key]

    def _fetch():
        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        r = requests.get(
            url,
            params={"symbol": symbol, "scale": "240", "ma": "5", "datalen": str(datalen)},
            timeout=15,
            headers=_HTTP_HEADERS,
        )
        r.raise_for_status()
        arr = r.json()
        rows = []
        for x in arr or []:
            rows.append({
                "日期": str(x.get("day", ""))[:10],
                "开盘": float(x["open"]),
                "收盘": float(x["close"]),
                "最高": float(x["high"]),
                "最低": float(x["low"]),
                # 新浪 volume 为股
                "成交量_手": float(x.get("volume") or 0) / 100.0,
            })
        return pd.DataFrame(rows)

    try:
        df = _retry_fetch(f"新浪K线 {code}", _fetch, max_attempts=2, base_delay=0.6)
    except Exception:
        df = pd.DataFrame()
    _TX_KLINE_CACHE[cache_key] = df
    return df


def fetch_daily_bars(code, start="20260601", end=None, max_attempts=1):
    """日K：腾讯失败则新浪（ST 全量扫描时避免长时间卡死）。"""
    # 腾讯偶发限流时直接走新浪更稳
    df = fetch_sina_kline(code)
    if df is not None and not df.empty:
        if end:
            end_dash = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
            df = df[df["日期"] <= end_dash]
        if start:
            start_dash = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
            df = df[df["日期"] >= start_dash]
        return df.reset_index(drop=True)
    return fetch_tencent_kline(code, start=start, end=end, max_attempts=max_attempts)


def fetch_tencent_kline(code, start="20260601", end=None, max_attempts=2):
    """腾讯日K（东财 push2his 易断连时的备用源）。"""
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    cache_key = f"tx:{code}:{start}:{end}"
    if cache_key in _TX_KLINE_CACHE:
        return _TX_KLINE_CACHE[cache_key]

    m = _market_prefix(code)
    if m == "bj":
        _TX_KLINE_CACHE[cache_key] = pd.DataFrame()
        return _TX_KLINE_CACHE[cache_key]

    start_dash = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_dash = f"{end[:4]}-{end[4:6]}-{end[6:8]}"

    def _fetch():
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        r = requests.get(
            url,
            params={"param": f"{m}{code},day,{start_dash},{end_dash},120,qfq"},
            timeout=15,
            headers=_HTTP_HEADERS,
        )
        r.raise_for_status()
        j = r.json()
        data = (j.get("data") or {}).get(f"{m}{code}") or {}
        bars = data.get("qfqday") or data.get("day") or []
        rows = []
        for b in bars:
            rows.append({
                "日期": b[0],
                "开盘": float(b[1]),
                "收盘": float(b[2]),
                "最高": float(b[3]),
                "最低": float(b[4]),
                "成交量_手": float(b[5]),
            })
        return pd.DataFrame(rows)

    try:
        df = _retry_fetch(f"腾讯K线 {code}", _fetch, max_attempts=max_attempts, base_delay=0.8)
    except Exception:
        df = pd.DataFrame()
    _TX_KLINE_CACHE[cache_key] = df
    return df


def _is_limit_up_bar(close, high, prev_close):
    if prev_close is None or prev_close <= 0 or close is None or high is None:
        return False
    pct = (close - prev_close) / prev_close * 100
    return pct >= ST_LIMIT_PCT and close >= high * 0.995


def fetch_st_zt_supplement(date_str):
    """
    补充东财涨停池缺失的 ST 涨停。
    date_str: YYYYMMDD
    """
    meta = load_st_meta()
    if not meta:
        return pd.DataFrame(columns=list(_ZT_POOL_COLUMNS) + ["ST补充"])

    date_dash = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    # 多取几天用于昨收与连板判断
    start = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d")
    rows = []
    print(f"正在扫描 ST 涨停补充（{date_str}，约 {len(meta)} 只）...")
    for i, item in enumerate(meta, start=1):
        code = item["代码"]
        name = item["名称"]
        hist = fetch_daily_bars(code, start=start, end=date_str, max_attempts=1)
        if hist.empty or "日期" not in hist.columns:
            continue
        by_date = {str(d): idx for idx, d in enumerate(hist["日期"].astype(str))}
        if date_dash not in by_date:
            continue
        idx = by_date[date_dash]
        if idx <= 0:
            continue
        cur = hist.iloc[idx]
        prev = hist.iloc[idx - 1]
        close, high = float(cur["收盘"]), float(cur["最高"])
        prev_close = float(prev["收盘"])
        if not _is_limit_up_bar(close, high, prev_close):
            continue
        # 补充池只要可能进硬筛的低价 ST
        if close > MAX_PRICE_ST:
            continue

        # 连板数：向前连续涨停天数
        lianban = 1
        j = idx - 1
        while j > 0:
            c0, h0 = float(hist.iloc[j]["收盘"]), float(hist.iloc[j]["最高"])
            p0 = float(hist.iloc[j - 1]["收盘"])
            if _is_limit_up_bar(c0, h0, p0):
                lianban += 1
                j -= 1
            else:
                break

        live_price = item.get("最新价_live") or close
        float_mv_live = item.get("流通市值_live") or 0.0
        total_mv_live = item.get("总市值_live") or 0.0
        float_mv = float_mv_live / live_price * close if live_price else float_mv_live
        total_mv = total_mv_live / live_price * close if live_price else total_mv_live

        vol_shares = float(cur["成交量_手"]) * 100.0
        amount = vol_shares * close
        turnover = (vol_shares / float_mv * 100.0) if float_mv > 0 else 0.0
        pct = (close - prev_close) / prev_close * 100.0

        rows.append({
            "序号": 0,
            "代码": code,
            "名称": name,
            "涨跌幅": round(pct, 4),
            "最新价": close,
            "成交额": amount,
            "流通市值": float_mv,
            "总市值": total_mv,
            "换手率": round(turnover, 4),
            "封板资金": 0.0,
            "首次封板时间": "",  # 无分时，硬筛对 ST 补充放宽
            "最后封板时间": "",
            "炸板次数": 0,
            "涨停统计": f"{lianban}/{lianban}",
            "连板数": lianban,
            "所属行业": ST_INDUSTRY,
            "ST补充": True,
        })
        if i % 40 == 0:
            print(f"  ST扫描进度 {i}/{len(meta)}，已发现涨停 {len(rows)} 只")
        time_mod.sleep(0.02)

    print(f"  ST 涨停补充完成: {len(rows)} 只")
    if not rows:
        return pd.DataFrame(columns=list(_ZT_POOL_COLUMNS) + ["ST补充"])
    return pd.DataFrame(rows)


def fetch_zt_pool_em(date_str, max_attempts=5, include_st=True):
    """
    东财涨停池 +（可选）ST 涨停补充；同一日期缓存。
    include_st=False 时仅东财池（用于加速历史基因等可走腾讯单票路径的场景）。
    """
    cache_key = f"{date_str}:st{int(include_st)}"
    if cache_key in _ZT_POOL_CACHE:
        return _ZT_POOL_CACHE[cache_key]

    df = _retry_fetch(
        f"涨停池 {date_str}",
        ak.stock_zt_pool_em,
        date=date_str,
        max_attempts=max_attempts,
        base_delay=1.5,
    )
    df = _normalize_zt_pool(df)
    if "ST补充" not in df.columns:
        df["ST补充"] = False

    if include_st:
        st_df = fetch_st_zt_supplement(date_str)
        if st_df is not None and not st_df.empty:
            exist = set(df["代码"].astype(str)) if not df.empty else set()
            st_df = st_df[~st_df["代码"].astype(str).isin(exist)]
            df = pd.concat([df, st_df], ignore_index=True)
            st_cnt = int(df["名称"].astype(str).str.contains("ST", case=False, na=False).sum())
            print(f"  合并后涨停池 {len(df)} 只（其中名称含ST约 {st_cnt} 只）")

    _ZT_POOL_CACHE[cache_key] = df
    return df


def get_previous_trading_day(current_date):
    """获取前一个中国股市交易日（使用真实交易日历）"""
    try:
        trade_cal = _retry_fetch(
            "交易日历",
            ak.tool_trade_date_hist_sina,
            max_attempts=3,
            base_delay=1.0,
        )
        trade_cal['trade_date'] = pd.to_datetime(trade_cal['trade_date'])
        current_date_only = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        past_trades = trade_cal[trade_cal['trade_date'] < current_date_only]
        if len(past_trades) > 0:
            return past_trades.iloc[-1]['trade_date'].to_pydatetime()
    except Exception:
        pass

    day = current_date - timedelta(1)
    while day.weekday() >= 5:
        day -= timedelta(1)
    return day


def parse_seal_time(value):
    """将首次封板时间统一为 HHMMSS 整数，解析失败返回 None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "-"):
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if len(digits) <= 4:
        digits = digits.zfill(4) + "00"
    elif len(digits) == 5:
        digits = "0" + digits
    elif len(digits) > 6:
        digits = digits[:6]
    try:
        return int(digits)
    except ValueError:
        return None


def seal_time_to_minutes(seal_time):
    """HHMMSS → 当日分钟数，便于线性打分。"""
    if seal_time is None:
        return None
    h = seal_time // 10000
    m = (seal_time % 10000) // 100
    return h * 60 + m


def get_historical_zt_count(stock_code, days=30, stock_name=""):
    """获取过去days天内的涨停次数；ST 优先用腾讯K线，避免全市场ST日扫。"""
    try:
        if is_st_name(stock_name) or str(stock_code) in {m["代码"] for m in (_ST_META_CACHE or [])}:
            start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
            end = datetime.now().strftime("%Y%m%d")
            hist = fetch_daily_bars(stock_code, start=start, end=end, max_attempts=1)
            if hist.empty or len(hist) < 2:
                return 0
            count = 0
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            for i in range(1, len(hist)):
                d = str(hist.iloc[i]["日期"])
                if d < cutoff:
                    continue
                close, high = float(hist.iloc[i]["收盘"]), float(hist.iloc[i]["最高"])
                prev_close = float(hist.iloc[i - 1]["收盘"])
                if _is_limit_up_bar(close, high, prev_close):
                    count += 1
            return count

        end_date = datetime.now()
        count = 0
        for i in range(days):
            date_str = (end_date - timedelta(i)).strftime('%Y%m%d')
            # 基因统计只用东财池，避免 30 日反复全量 ST 扫描
            daily_zt = fetch_zt_pool_em(date_str, max_attempts=3, include_st=False)
            if not daily_zt.empty and "代码" in daily_zt.columns and stock_code in daily_zt["代码"].astype(str).values:
                count += 1
        return count
    except Exception:
        return 0


def get_trend_indicator(stock_code):
    """简单趋势指标：5日均线 > 10日均线则为1，否则0"""
    try:
        hist = fetch_daily_bars(
            stock_code,
            start=(datetime.now() - timedelta(40)).strftime("%Y%m%d"),
            end=datetime.now().strftime("%Y%m%d"),
            max_attempts=1,
        )
        if hist is None or len(hist) < 10:
            # 回退 akshare
            hist2 = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=(datetime.now() - timedelta(30)).strftime('%Y%m%d'),
                end_date=datetime.now().strftime('%Y%m%d'),
            )
            if len(hist2) < 10:
                return 0
            ma5 = hist2['收盘'].tail(5).mean()
            ma10 = hist2['收盘'].tail(10).mean()
            return 1 if ma5 > ma10 else 0
        ma5 = hist['收盘'].tail(5).mean()
        ma10 = hist['收盘'].tail(10).mean()
        return 1 if ma5 > ma10 else 0
    except Exception:
        return 0


def get_basic_fundamentals(stock_code):
    """获取基本面数据，如市盈率（仅展示，不进得分）"""
    try:
        fundamentals = ak.stock_a_lg_indicator_cninfo(symbol=stock_code)
        if not fundamentals.empty:
            pe = fundamentals.get('市盈率', 0)
            return pe if pe else 0
        return 0
    except Exception:
        return 0


def is_chinext(code):
    """创业板：300/301。"""
    c = str(code)
    return c.startswith(("300", "301"))


def _score_seal_time(seal_time):
    """09:30 → 1.0，10:30 → 0.0，按分钟线性衰减；缺失给中性分。"""
    mins = seal_time_to_minutes(seal_time)
    if mins is None:
        return 0.5
    start_m, end_m = 9 * 60 + 30, 10 * 60 + 30
    if mins <= start_m:
        return 1.0
    if mins >= end_m:
        return 0.0
    return max(0.0, 1.0 - (mins - start_m) / (end_m - start_m))


def _score_seal_strength(fengban_zijin, float_mv):
    """封板资金 / 流通市值，分段打分。"""
    if float_mv is None or float_mv <= 0 or fengban_zijin is None or pd.isna(fengban_zijin):
        return 0.0
    ratio = float(fengban_zijin) / float(float_mv)
    if ratio >= 0.05:
        return 1.0
    if ratio >= 0.03:
        return 0.8
    if ratio >= 0.015:
        return 0.6
    if ratio >= 0.008:
        return 0.4
    if ratio > 0:
        return 0.2
    return 0.0


def _score_zhaban(zhaban):
    if pd.isna(zhaban):
        return 0.5
    z = int(zhaban)
    if z <= 0:
        return 1.0
    if z == 1:
        return 0.5
    return 0.0


def _score_turnover_st(turnover):
    """ST：偏好活跃换手（约 15%~40%），突出*ST萃华类放量首板。"""
    if turnover is None or pd.isna(turnover):
        return 0.0
    t = float(turnover)
    if 20.0 <= t <= 40.0:
        return 1.0
    if 15.0 <= t < 20.0:
        return 0.75 + 0.25 * (t - 15.0) / 5.0
    if 40.0 < t <= TURNOVER_MAX_ST:
        return 0.85
    if 8.0 <= t < 15.0:
        return 0.35 + 0.4 * (t - 8.0) / 7.0
    if TURNOVER_MIN <= t < 8.0:
        return 0.2
    return 0.0


def _score_turnover_chinext(turnover):
    """创业板：偏好缩量早板（约 3%~10%），覆盖格林精密类。"""
    if turnover is None or pd.isna(turnover):
        return 0.0
    t = float(turnover)
    if 3.0 <= t <= 8.0:
        return 1.0
    if 8.0 < t <= 12.0:
        return 0.75
    if 12.0 < t <= 18.0:
        return 0.45
    if TURNOVER_MIN <= t < 3.0:
        return 0.3
    return 0.15


def _score_turnover_normal(turnover):
    """主板非ST：适中换手。"""
    if turnover is None or pd.isna(turnover):
        return 0.0
    t = float(turnover)
    if 8.0 <= t <= 18.0:
        return 1.0
    if 5.0 <= t < 8.0 or 18.0 < t <= 25.0:
        return 0.55
    if TURNOVER_MIN <= t < 5.0:
        return 0.35
    return 0.0


def _score_industry_heat(count):
    if count is None or pd.isna(count):
        return 0.0
    c = int(count)
    if c >= 5:
        return 1.0
    if c >= 3:
        return 0.8
    if c >= 2:
        return 0.5
    return 0.0


def _score_amount(amount):
    """成交额（元）分段，偏好有承接的活跃票。"""
    if amount is None or pd.isna(amount):
        return 0.0
    yi = float(amount) / 1e8
    if yi >= 3.0:
        return 1.0
    if yi >= 1.5:
        return 0.8
    if yi >= 0.8:
        return 0.6
    if yi >= 0.3:
        return 0.4
    return 0.2


def calculate_composite_score(row):
    """
    双轨打分（对齐人工偏好）：
    - 低价 ST：放量活跃 + 成交额 + *ST 加成（压低「趋势/基因」权重，避免西王类挤掉萃华）
    - 创业板：早封 + 缩量 + 20cm 弹性
    - 其他主板：早封 + 适中换手（权重低于前两类）
    """
    seal_time = row.get('封板时间数值', parse_seal_time(row.get('首次封板时间')))
    float_mv = pd.to_numeric(row.get('流通市值'), errors='coerce')
    fengban = pd.to_numeric(row.get('封板资金'), errors='coerce')
    zhaban = pd.to_numeric(row.get('炸板次数'), errors='coerce')
    turnover = pd.to_numeric(row.get('换手率'), errors='coerce')
    industry_cnt = pd.to_numeric(row.get('同行业涨停家数'), errors='coerce')
    amount = pd.to_numeric(row.get('成交额'), errors='coerce')
    pct = pd.to_numeric(row.get('涨跌幅'), errors='coerce')
    gene = float(row.get('历史涨停基因', 0) or 0)
    trend = float(row.get('趋势指标', 0) or 0)
    name = str(row.get('名称', ''))
    code = str(row.get('代码', ''))
    price = pd.to_numeric(row.get('最新价'), errors='coerce')
    st = is_st_name(name)
    chi = is_chinext(code)
    gene_trend = min(1.0, gene / 10.0) * 0.5 + trend * 0.5

    if st:
        # 低价 ST 轨：活跃换手为主，成交额次之；*ST 略优于普通 ST
        price_score = 1.0
        if pd.notna(price):
            if price <= 2.5:
                price_score = 1.0
            elif price <= 4.0:
                price_score = 0.85
            elif price <= MAX_PRICE_ST:
                price_score = 0.65
            else:
                price_score = 0.3
        score = (
            _score_turnover_st(turnover) * 35.0
            + _score_amount(amount) * 25.0
            + price_score * 15.0
            + _score_industry_heat(industry_cnt) * 10.0
            + _score_zhaban(zhaban) * 5.0
            + gene_trend * 5.0
            + _score_seal_time(seal_time) * 5.0
        )
        if re.search(r"\*ST|\*st", name):
            score *= 1.12
        else:
            score *= 1.05
        # ST 轨基准抬升，保证最终 Top 结构里 ST 能排第 1
        score += 25.0
        return score

    if chi:
        # 创业板轨：早封 + 缩量 + 涨幅弹性（20cm）
        pct_score = 0.0
        if pd.notna(pct):
            if pct >= 19.0:
                pct_score = 1.0
            elif pct >= 15.0:
                pct_score = 0.7
            elif pct >= 10.0:
                pct_score = 0.4
        score = (
            _score_seal_time(seal_time) * 30.0
            + _score_turnover_chinext(turnover) * 25.0
            + pct_score * 20.0
            + _score_seal_strength(fengban, float_mv) * 10.0
            + _score_zhaban(zhaban) * 5.0
            + _score_industry_heat(industry_cnt) * 5.0
            + gene_trend * 5.0
        )
        # 创业板轨基准抬升，保证能压过普通主板（如欢瑞）
        score += 15.0
        return score

    # 普通主板：保留一进二质量，但整体低于 ST/创业板偏好
    score = (
        _score_seal_time(seal_time) * 25.0
        + _score_turnover_normal(turnover) * 20.0
        + _score_seal_strength(fengban, float_mv) * 20.0
        + _score_zhaban(zhaban) * 15.0
        + _score_industry_heat(industry_cnt) * 10.0
        + gene_trend * 10.0
    )
    return score


def apply_yijiner_hard_filters(df, full_zt_df):
    """一进二硬筛；ST≤6 / 非ST≤12；ST补充放宽封板时间；ST换手上限更高。"""
    out = df.copy()
    out['代码'] = out['代码'].astype(str)
    out['最新价'] = pd.to_numeric(out['最新价'], errors='coerce')
    out['换手率'] = pd.to_numeric(out['换手率'], errors='coerce')
    out['流通市值'] = pd.to_numeric(out['流通市值'], errors='coerce')
    out['封板资金'] = pd.to_numeric(out['封板资金'], errors='coerce')
    out['炸板次数'] = pd.to_numeric(out['炸板次数'], errors='coerce').fillna(0).astype(int)
    out['封板时间数值'] = out['首次封板时间'].apply(parse_seal_time)
    if 'ST补充' not in out.columns:
        out['ST补充'] = False
    out['ST补充'] = out['ST补充'].fillna(False).astype(bool)
    out['是否ST'] = out['名称'].apply(is_st_name)
    out['是否创业板'] = out['代码'].apply(is_chinext)

    if full_zt_df is not None and not full_zt_df.empty and '所属行业' in full_zt_df.columns:
        industry_counts = full_zt_df['所属行业'].astype(str).value_counts()
        out['同行业涨停家数'] = out['所属行业'].astype(str).map(industry_counts).fillna(0).astype(int)
    else:
        out['同行业涨停家数'] = 0

    before = len(out)
    price_ok = (
        (out['是否ST'] & out['最新价'].notna() & (out['最新价'] <= MAX_PRICE_ST))
        | ((~out['是否ST']) & out['最新价'].notna() & (out['最新价'] <= MAX_PRICE_NORMAL))
    )
    out = out[price_ok]
    print(f"  股价ST≤{MAX_PRICE_ST}/非ST≤{MAX_PRICE_NORMAL}: {before} → {len(out)}")

    before = len(out)
    seal_ok = (
        (out['封板时间数值'].notna() & (out['封板时间数值'] <= MAX_FIRST_SEAL_TIME))
        | (out['ST补充'] & out['封板时间数值'].isna())
    )
    out = out[seal_ok]
    print(f"  首次封板≤10:30（ST补充无时间则放行）: {before} → {len(out)}")

    before = len(out)
    out = out[out['炸板次数'] <= MAX_ZHABAN]
    print(f"  炸板次数≤{MAX_ZHABAN}: {before} → {len(out)}")

    before = len(out)
    tmax = out['是否ST'].map(lambda x: TURNOVER_MAX_ST if x else TURNOVER_MAX)
    out = out[out['换手率'].notna() & (out['换手率'] >= TURNOVER_MIN) & (out['换手率'] <= tmax)]
    print(f"  换手率≥{TURNOVER_MIN}% 且 ≤{TURNOVER_MAX}%（ST≤{TURNOVER_MAX_ST}%）: {before} → {len(out)}")

    before = len(out)
    out = out[out['流通市值'].notna() & (out['流通市值'] >= FLOAT_MV_MIN) & (out['流通市值'] <= FLOAT_MV_MAX)]
    print(f"  流通市值3亿-80亿: {before} → {len(out)}")

    before = len(out)
    one_word_mask = (
        out['封板时间数值'].notna()
        & (out['封板时间数值'] <= ONE_WORD_SEAL_TIME)
        & (out['换手率'] < ONE_WORD_TURNOVER)
    )
    out = out[~one_word_mask]
    print(f"  排除近似一字板: {before} → {len(out)}")

    before = len(out)
    out = out[out['同行业涨停家数'] >= MIN_INDUSTRY_ZT]
    print(f"  同行业涨停≥{MIN_INDUSTRY_ZT}家: {before} → {len(out)}")

    return out


def select_top2_dual_track(sorted_stocks):
    """
    Top2 结构：① 最佳低价ST  ② 最佳创业板早封；
    若某一侧缺失，则从另一侧/主板按得分补足。
    """
    if sorted_stocks is None or sorted_stocks.empty:
        return sorted_stocks

    st_pool = sorted_stocks[sorted_stocks['是否ST'] == True]
    chi_pool = sorted_stocks[(sorted_stocks['是否ST'] == False) & (sorted_stocks['是否创业板'] == True)]
    other_pool = sorted_stocks[(sorted_stocks['是否ST'] == False) & (sorted_stocks['是否创业板'] == False)]

    picks = []
    used = set()

    if not st_pool.empty:
        row = st_pool.iloc[0]
        picks.append(row)
        used.add(str(row['代码']))
        print(f"  Top结构①低价ST: {row['代码']} {row['名称']} 得分{row['综合得分']:.2f}")

    if not chi_pool.empty:
        for _, row in chi_pool.iterrows():
            if str(row['代码']) not in used:
                picks.append(row)
                used.add(str(row['代码']))
                print(f"  Top结构②创业板: {row['代码']} {row['名称']} 得分{row['综合得分']:.2f}")
                break

    # 补足到 2
    for pool in (sorted_stocks,):
        if len(picks) >= 2:
            break
        for _, row in pool.iterrows():
            if str(row['代码']) in used:
                continue
            picks.append(row)
            used.add(str(row['代码']))
            side = "ST" if row.get('是否ST') else ("创业板" if row.get('是否创业板') else "主板")
            print(f"  Top补足({side}): {row['代码']} {row['名称']} 得分{row['综合得分']:.2f}")
            if len(picks) >= 2:
                break

    return pd.DataFrame(picks)


def analyze_zhangting_stocks(query_date_str=None):
    clear_zt_pool_cache()
    print("开始分析低价一进二候选...", flush=True)

    today = datetime.now()
    if query_date_str:
        previous_trading_day = datetime.strptime(query_date_str, "%Y%m%d")
    else:
        previous_trading_day = get_previous_trading_day(today)
        query_date_str = previous_trading_day.strftime('%Y%m%d')
    query_date_display = previous_trading_day.strftime('%Y年%m月%d日')

    print(f"查询日期(D0首板日): {query_date_display}")
    print("操作纪律: D1开盘半仓冲击二板 → 优先封死至收盘 → D2收盘前清仓（无通知）")
    print("数据说明: 东财涨停池 + ST涨停补充（新浪列表/腾讯K线）")

    print("正在获取涨停股票数据...")
    try:
        zt_df = fetch_zt_pool_em(query_date_str, include_st=True)
    except (RequestException, OSError) as e:
        print(f"获取涨停数据失败（已多次重试）: {e}")
        print("请检查网络、代理或稍后重试；若持续失败可尝试: pip install -U certifi akshare requests urllib3")
        return

    if zt_df.empty or "代码" not in zt_df.columns:
        print(f"{query_date_display} 暂无涨停股票数据（接口返回空或格式异常）。")
        return

    st_in_pool = zt_df[zt_df["名称"].astype(str).str.contains("ST", case=False, na=False)]
    print(f"获取到 {len(zt_df)} 只涨停股票（含ST {len(st_in_pool)} 只）")
    if not st_in_pool.empty:
        print("  ST涨停明细: " + ", ".join(f"{r['代码']}{r['名称']}" for _, r in st_in_pool.iterrows()))

    two_days_ago = get_previous_trading_day(previous_trading_day)
    two_days_ago_str = two_days_ago.strftime('%Y%m%d')
    print(f"筛选首次涨停股票（排除{two_days_ago_str}已涨停的股票）...")
    try:
        two_days_ago_zt = fetch_zt_pool_em(two_days_ago_str, include_st=True)
    except (RequestException, OSError) as e:
        print(f"获取前两日涨停池失败: {e}")
        return

    prev_zt_codes = _zt_pool_codes(two_days_ago_zt)
    if not prev_zt_codes:
        print(f"{two_days_ago_str} 无涨停数据，视为全部为首板候选")

    today_zt_only = zt_df[~zt_df["代码"].astype(str).isin(prev_zt_codes)].copy()

    today_zt_only = today_zt_only[~today_zt_only['代码'].astype(str).str.startswith(('688', '92'))]
    print(f"排除688/92后剩余 {len(today_zt_only)} 只（含ST）")

    print("应用一进二硬筛...")
    today_zt_only = apply_yijiner_hard_filters(today_zt_only, zt_df)

    if today_zt_only.empty:
        print(f"{query_date_display} 无符合一进二硬筛条件的低价首板股票。")
        return

    print(f"硬筛后剩余 {len(today_zt_only)} 只股票")
    st_pass = today_zt_only[today_zt_only["名称"].astype(str).str.contains("ST", case=False, na=False)]
    print(f"  其中 ST {len(st_pass)} 只: " + (
        ", ".join(f"{r['代码']}{r['名称']}" for _, r in st_pass.iterrows()) if len(st_pass) else "无"
    ))

    print("正在计算各项指标...")
    today_zt_only['历史涨停基因'] = today_zt_only.apply(
        lambda r: get_historical_zt_count(str(r['代码']), 30, stock_name=str(r.get('名称', ''))),
        axis=1,
    )
    today_zt_only['趋势指标'] = today_zt_only['代码'].apply(get_trend_indicator)
    today_zt_only['市盈率'] = today_zt_only['代码'].apply(get_basic_fundamentals)
    today_zt_only['是否ST'] = today_zt_only['名称'].apply(is_st_name)
    today_zt_only['是否创业板'] = today_zt_only['代码'].apply(is_chinext)

    today_zt_only['综合得分'] = today_zt_only.apply(calculate_composite_score, axis=1)

    sorted_stocks = today_zt_only.sort_values('综合得分', ascending=False)
    print("按双轨结构选取 Top2（①低价ST ②创业板早封）...")
    top2 = select_top2_dual_track(sorted_stocks)

    print("分析完成，正在生成结果...")
    output_lines = []
    output_lines.append(f"=== {query_date_display} 低价一进二推荐（前2只：①ST ②创业板）===")
    output_lines.append("交易时序: D0首板复盘选出 → D1开盘半仓买入冲击二板 → D2收盘前清仓")
    output_lines.append("数据: 东财涨停池 + ST补充；选股偏好: 低价活跃ST优先 + 创业板早封缩量；无通知")
    output_lines.append("")
    for rank, (_, row) in enumerate(top2.iterrows(), start=1):
        seal_time = row.get('首次封板时间', '') or ("N/A(ST补充无分时)" if row.get('ST补充') else "")
        float_mv = pd.to_numeric(row.get('流通市值'), errors='coerce')
        fengban = pd.to_numeric(row.get('封板资金'), errors='coerce')
        float_mv_yi = float_mv / 1e8 if pd.notna(float_mv) else float('nan')
        fengban_yi = fengban / 1e8 if pd.notna(fengban) else float('nan')
        track = "低价ST轨" if row.get('是否ST') else ("创业板轨" if row.get('是否创业板') else "主板轨")

        output_lines.append(f"排名 {rank}: {row['代码']} - {row['名称']}")
        output_lines.append(f"  赛道: {track}")
        output_lines.append(f"  是否ST: {'是' if row.get('是否ST') else '否'}")
        output_lines.append(f"  数据来源: {'ST补充' if row.get('ST补充') else '东财涨停池'}")
        output_lines.append(f"  涨停天数: 1天（首板）")
        output_lines.append(f"  最新价: {row['最新价']}")
        output_lines.append(f"  涨幅: {row['涨跌幅']}%")
        output_lines.append(f"  首次封板时间: {seal_time}")
        output_lines.append(f"  炸板次数: {row.get('炸板次数', 0)}")
        output_lines.append(f"  流通市值: {float_mv_yi:.2f}亿" if pd.notna(float_mv_yi) else "  流通市值: N/A")
        output_lines.append(f"  封板资金: {fengban_yi:.2f}亿" if pd.notna(fengban_yi) else "  封板资金: N/A")
        output_lines.append(f"  换手率: {row['换手率']}%")
        output_lines.append(f"  所属行业: {row.get('所属行业', '')}")
        output_lines.append(f"  同行业涨停家数: {row.get('同行业涨停家数', 0)}")
        output_lines.append(f"  成交额: {row['成交额']}")
        output_lines.append(f"  历史涨停基因（30天内涨停次数）: {row['历史涨停基因']}")
        output_lines.append(f"  趋势指标（1=上升，0=其他）: {row['趋势指标']}")
        output_lines.append(f"  基本面（市盈率）: {row['市盈率']}")
        output_lines.append(f"  综合得分: {row['综合得分']:.2f}")
        reason = row.get('涨停原因', '数据中未提供具体原因')
        output_lines.append(f"  涨停原因分析: {reason}")
        lianban = "有连板潜力" if row['历史涨停基因'] > 5 else "连板属性一般"
        win_rate = "一进二预期较高" if row['趋势指标'] == 1 and row['历史涨停基因'] > 3 else "一进二预期中等"
        output_lines.append(f"  连板属性: {lianban}")
        output_lines.append(f"  一进二评估: {win_rate}（基于封板质量/板块/趋势，非回测保证）")
        output_lines.append("  【操作计划】")
        output_lines.append("    买入: 下一交易日(D1)开盘/集合竞价附近半仓冲击二板（Top2合计不超过半仓~全仓轮动，单票建议≤半仓）")
        output_lines.append("    持有: 优先博 D1 封死二板并持有至收盘")
        output_lines.append("    卖出: 再下一交易日(D2)收盘前清仓")
        output_lines.append("    风控: D1 大幅低开或开盘后破位则当日止损，不隔夜赌反转")
        output_lines.append("")

    top_codes = set(top2['代码'].astype(str)) if not top2.empty else set()
    others = sorted_stocks[~sorted_stocks['代码'].astype(str).isin(top_codes)]
    output_lines.append("=== 其他未入选前二原因 ===")
    if not others.empty:
        output_lines.append("以下股票未进入双轨 Top2（①ST ②创业板）：")
        for _, row in others.iterrows():
            track = "ST" if row.get('是否ST') else ("创业板" if row.get('是否创业板') else "主板")
            output_lines.append(
                f"  {row['代码']} - {row['名称']}: 综合得分 {row['综合得分']:.2f} "
                f"(轨={track}/封板{row.get('首次封板时间', '') or '-'}/"
                f"炸板{row.get('炸板次数', 0)}/同业{row.get('同行业涨停家数', 0)}家)"
            )
    else:
        output_lines.append("无其他候选股票。")

    for line in output_lines:
        print(line)

    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(results_dir, exist_ok=True)
    file_name = f"{query_date_str}_slow_recommendations.md"
    file_path = os.path.join(results_dir, file_name)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))
    print(f"\n✅ 结果已保存到: {file_path}")
    print("🎉 分析完成！（未发送通知）")


if __name__ == "__main__":
    arg_date = None
    if len(sys.argv) >= 2 and re.fullmatch(r"\d{8}", sys.argv[1]):
        arg_date = sys.argv[1]
    analyze_zhangting_stocks(query_date_str=arg_date)
