import sys
import os

# 添加用户 site-packages 路径（确保 akshare 能被找到）
user_site_packages = os.path.expanduser('~/.local/lib/python3.13/site-packages')
if user_site_packages not in sys.path:
    sys.path.insert(0, user_site_packages)


import akshare as ak  # 免费A股数据
import pandas as pd
from datetime import datetime, timedelta, time
import os
import sys
from pathlib import Path as _FirstBoardPath
_first_board_public = next((p / '公共/lib' for p in _FirstBoardPath(__file__).resolve().parents if (p / '公共/lib/first_board_gate.py').is_file()), None)
if _first_board_public is not None and str(_first_board_public) not in sys.path:
    sys.path.insert(0, str(_first_board_public))
import time as time_mod
from requests.exceptions import RequestException

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


def _platform_slot(platform: str):
    """限流门禁；不可用时退化为空上下文（仍靠 sleep/重试）。"""
    try:
        _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        lib = os.path.join(_pkg, "交易记录UI", "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        from platform_gate import platform_slot  # noqa: WPS433
        return platform_slot(platform)
    except Exception:
        from contextlib import nullcontext
        return nullcontext()


def fetch_zt_pool_em(date_str, max_attempts=5):
    """涨停池：优先交易记录UI 多源+磁盘缓存（限流合规）；失败再直连东财。

    历史回补严禁裸打无缓存接口——否则易空窗且超限。
    东财小时软上限已满时 prefer=ths，禁止再直连东财（否则整批 QUOTA_STOP）。
    """
    date_str = str(date_str).replace("-", "")[:8]
    if date_str in _ZT_POOL_CACHE:
        return _ZT_POOL_CACHE[date_str]

    em_rem = None
    try:
        _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        lib = os.path.join(_pkg, "交易记录UI", "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        from platform_gate import quota_status as _qs  # noqa: WPS433

        em_rem = (_qs().get("eastmoney") or {}).get("remaining_hour")
    except Exception:
        pass

    prefer = "ths" if (em_rem is not None and em_rem <= 0) else "em"

    # 1) 权威多源（含磁盘缓存 / ths 回退 / platform_gate）
    try:
        _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        lib = os.path.join(_pkg, "交易记录UI", "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import board_data_sources as bds  # noqa: WPS433
        df = bds.fetch_zt_pool(date_str, prefer=prefer, allow_fallback=True)
        df = _normalize_zt_pool(df)
        if df is not None and not df.empty:
            _ZT_POOL_CACHE[date_str] = df
            return df
    except Exception as e:
        # 东财配额类错误：禁止再直连东财
        if "QUOTA_STOP" in str(e) or e.__class__.__name__ == "PlatformQuotaExceeded":
            if prefer != "ths":
                try:
                    import board_data_sources as bds  # noqa: WPS433
                    df = bds.fetch_zt_pool(date_str, prefer="ths", allow_fallback=True)
                    df = _normalize_zt_pool(df)
                    if df is not None and not df.empty:
                        _ZT_POOL_CACHE[date_str] = df
                        return df
                except Exception as e2:
                    raise e2
            raise
        print(f"多源涨停池失败 {date_str}: {e}，回退直连东财")

    # 2) 直连东财（仅东财小时仍有余量时）
    if em_rem is not None and em_rem <= 0:
        raise RuntimeError(f"涨停池无缓存且东财小时已满，拒绝直连超限 date={date_str}")
    with _platform_slot("eastmoney"):
        df = _retry_fetch(
            f"涨停池 {date_str}",
            ak.stock_zt_pool_em,
            date=date_str,
            max_attempts=max_attempts,
            base_delay=1.5,
        )
    df = _normalize_zt_pool(df)
    _ZT_POOL_CACHE[date_str] = df
    return df


def _local_calendar_mod():
    """本地交易日历（含法定节假日），不依赖 py_mini_racer。"""
    _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    records_dir = os.path.join(_pkg, "交易记录UI")
    lib_dir = os.path.join(records_dir, "lib")
    for _p in (lib_dir, records_dir):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import fetch_calendar as fc  # noqa: WPS433

    return fc


def _keep_proven_first_board(df, d0_dt, d0_prev_dt):
    """只留已证明的首板。连板跳过。日线或连板数都证明不了的，也跳过。"""
    if df is None or df.empty:
        return df
    try:
        lib = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "交易记录UI", "lib",
        )
        if lib not in sys.path:
            sys.path.insert(0, lib)
        from first_board_rule import classify_first_board, load_bars
    except Exception as e:
        print(f"首板日线复核模块不可用，拒绝放行未证明的票: {e}")
        return df.iloc[0:0]
    bars = load_bars()
    d0 = d0_dt.strftime("%Y-%m-%d")
    d0_prev = d0_prev_dt.strftime("%Y-%m-%d")
    keep = []
    skipped = []
    for idx, row in df.iterrows():
        code = str(row.get("代码") or "").zfill(6)
        raw = row.get("连板数")
        try:
            n = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            n = None
        if n is not None and n > 1:
            skipped.append(f"{code}连板数{n:g}")
            continue
        if n == 1:
            keep.append(idx)
            continue
        status = classify_first_board(code, d0, d0_prev, bars)
        if status == "first":
            keep.append(idx)
        else:
            skipped.append(f"{code}{status}")
    if skipped:
        print(f"连板或未证明首板，跳过 {len(skipped)} 只: {', '.join(skipped[:12])}")
    return df.loc[keep].copy() if keep else df.iloc[0:0]


def get_previous_trading_day(current_date):
    """获取前一个中国股市交易日（本地法定节假日日历优先 → 新浪 → 仅跳周末）。"""
    # 本地日历优先：稳定、无外部依赖、含中秋/国庆等法定休市（避免新浪 py_mini_racer 架构报错）
    try:
        return _local_calendar_mod().prev_trading_day_dt(current_date)
    except Exception as e:
        print(f"⚠️ 本地交易日历失败，尝试新浪: {e}")

    try:
        trade_cal = _retry_fetch(
            "交易日历",
            ak.tool_trade_date_hist_sina,
            max_attempts=3,
            base_delay=1.0,
        )
        trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"])
        current_date_only = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        past_trades = trade_cal[trade_cal["trade_date"] < current_date_only]
        if len(past_trades) > 0:
            return past_trades.iloc[-1]["trade_date"].to_pydatetime()
    except Exception:
        pass

    day = current_date - timedelta(1)
    while day.weekday() >= 5:  # 5=Saturday, 6=Sunday
        day -= timedelta(1)
    return day


def get_next_trading_day(current_date):
    """获取严格晚于 current_date 的下一个交易日（本地日历优先）。"""
    try:
        return _local_calendar_mod().next_trading_day_dt(current_date)
    except Exception as e:
        print(f"⚠️ 本地交易日历失败，尝试新浪: {e}")

    try:
        trade_cal = _retry_fetch(
            "交易日历",
            ak.tool_trade_date_hist_sina,
            max_attempts=3,
            base_delay=1.0,
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


def sync_trades_to_ui(top2_df, buy_date_str):
    """
    将 Top2 基础信息写入「交易记录UI」。
    D0=涨停日，D1=买入日(buy_date_str)，卖出价等由 UI 自动补全。
    """
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    records_dir = os.path.join(_root, "交易记录UI")
    if records_dir not in sys.path:
        sys.path.insert(0, records_dir)
    try:
        from stock_UI import upsert_from_stock_py
    except Exception as e:
        print(f"⚠️ 无法加载交易记录UI模块: {e}")
        return

    records = []
    # 按综合得分已排序；排名用 1..n
    for rank, (_, row) in enumerate(top2_df.iterrows(), start=1):
        records.append({
            "date": buy_date_str,
            "stock_code": str(row["代码"]).zfill(6),
            "stock_name": str(row["名称"]),
            "rank": rank,
        })
    try:
        # 铁律：只写 code/name/rank/date，不在此处取买入价。
        # 买入价 = D1 开盘价，必须由 15:00 收盘补全 / 强制重拉 写入（禁止早盘/D0 夜盘误写 D0 价）。
        result = upsert_from_stock_py(records, auto_enrich=False)
        print(
            f"✅ 已同步到交易记录UI: 新建 {result.get('created', 0)} / "
            f"更新 {result.get('updated', 0)} → {os.path.join(records_dir, 'trades', buy_date_str + '.json')}"
        )
    except Exception as e:
        print(f"⚠️ 同步交易记录UI失败: {e}")

def _as_datetime(as_of) -> datetime:
    if as_of is None:
        return datetime.now()
    if isinstance(as_of, datetime):
        return as_of
    s = str(as_of).strip()
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d")
    return datetime.strptime(s[:10], "%Y-%m-%d")


def get_historical_zt_count(stock_code, days=30, as_of=None):
    """获取 as_of 往前约 days 个交易日内的涨停次数（历史回补必须传 as_of=D0，禁止用今天）。

    非交易日不打接口，节省 eastmoney 配额。
    """
    try:
        end_date = _as_datetime(as_of)
        count = 0
        checked = 0
        # 最多向前扫 60 个自然日，凑够 days 个交易日窗口
        for i in range(0, 60):
            if checked >= days:
                break
            d = end_date - timedelta(i)
            # 周末直接跳过
            if d.weekday() >= 5:
                continue
            date_str = d.strftime("%Y%m%d")
            checked += 1
            daily_zt = fetch_zt_pool_em(date_str, max_attempts=3)
            if (
                not daily_zt.empty
                and "代码" in daily_zt.columns
                and stock_code in daily_zt["代码"].values
            ):
                count += 1
        return count
    except Exception:
        return 0


def get_trend_indicator(stock_code, as_of=None):
    """简单趋势指标：5日均线 > 10日均线则为1，否则0。历史回补 as_of=D0。"""
    try:
        end_date = _as_datetime(as_of)
        start_date = end_date - timedelta(30)
        with _platform_slot("eastmoney"):
            hist = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )
        if hist is None or len(hist) < 10:
            return 0
        ma5 = hist["收盘"].tail(5).mean()
        ma10 = hist["收盘"].tail(10).mean()
        return 1 if ma5 > ma10 else 0
    except Exception:
        return 0


def get_basic_fundamentals(stock_code, as_of=None):
    """获取基本面数据，如市盈率（接口无历史截面时返回当前可得值；as_of 仅占位保持签名一致）。"""
    _ = as_of
    try:
        with _platform_slot("eastmoney"):
            fundamentals = ak.stock_a_lg_indicator_cninfo(symbol=stock_code)
        if not fundamentals.empty:
            pe = fundamentals.get("市盈率", 0)
            return pe if pe else 0
        return 0
    except Exception:
        return 0

def _daily_limit_pct(code):
    """返回股票所属板块的每日涨跌停幅度（百分比）。
    用于把『涨幅』按板块归一，消除创业板(±20%)相对主板(±10%)在涨幅因子上
    结构性多拿 4 分的问题（首板战法默认两者涨停时涨幅项均应为 4 分）。"""
    c = str(code or "")
    if c[:3] in ("300", "301", "688"):        # 创业板 / 科创板：±20%
        return 20.0
    if c[:3] in ("600", "601", "603", "605",  # 沪主板
                 "000", "001", "002", "003"):  # 深主板
        return 10.0
    if c[:1] in ("8", "4"):                    # 北交所：±30%
        return 30.0
    return 10.0


def calculate_composite_score(row):
    """计算综合得分（涨幅已按板块涨跌停幅度归一，板块中性）。
    权重：涨幅40%（归一后涨停统一 4 分）、成交额20%、换手率20%、历史基因10%、趋势10%。
    原实现 涨幅×0.4 直接用原始涨跌幅：创业板 20% 涨停得 8 分、主板 10% 涨停仅 4 分，
    造成创业板结构性 +4 分优势。现改为 涨幅/各自涨跌停上限 归一后再计 4 分，
    创业板与主板涨停时『涨幅』项均为 4 分，谁拿榜首只看成交额/换手/基因/趋势。"""
    code = row.get("代码", "")
    limit = _daily_limit_pct(code)
    # 涨停(贴近各自上限)→归一值 1.0，板块无关；封顶 1.0 防止数据异常超涨
    zf_norm = min(row["涨跌幅"] / limit, 1.0) if limit else 0.0
    zf_score = zf_norm * 4.0
    cj_score = (row["成交额"] / 1e8) * 0.2  # 标准化成交额（亿元）
    hs_score = row["换手率"] * 0.2
    gene_score = row.get("历史涨停基因", 0) * 0.1
    trend_score = row.get("趋势指标", 0) * 0.1
    return zf_score + cj_score + hs_score + gene_score + trend_score


def _zt_metrics_sparse(df: pd.DataFrame) -> bool:
    """涨停池是否缺打分字段（空壳：涨跌幅/成交额/换手几乎全 0）。"""
    if df is None or getattr(df, "empty", True):
        return False
    zf = pd.to_numeric(df.get("涨跌幅"), errors="coerce").fillna(0.0)
    amt = pd.to_numeric(df.get("成交额"), errors="coerce").fillna(0.0)
    hs = pd.to_numeric(df.get("换手率"), errors="coerce").fillna(0.0)
    n = max(1, len(df))
    # 超过 70% 票三项都 ≤0 → 视为空壳，需补字段
    dead = ((zf <= 0) & (amt <= 0) & (hs <= 0)).sum()
    return float(dead) / n >= 0.7


def _enrich_turnover_baostock(df: pd.DataFrame, date_yyyymmdd: str) -> pd.DataFrame:
    """东财/腾讯不可用时，用 baostock 日线补 涨跌幅/成交额/换手率。"""
    if df is None or getattr(df, "empty", True) or "代码" not in df.columns:
        return df
    date8 = str(date_yyyymmdd).replace("-", "")[:8]
    day = f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"
    out = df.copy()
    try:
        import baostock as bs
    except Exception as e:
        print(f"baostock 不可用，跳过补字段: {e}")
        return out
    with _platform_slot("baostock"):
        lg = bs.login()
        if getattr(lg, "error_code", "0") not in ("0", 0, None):
            print(f"baostock login 失败: {getattr(lg, 'error_msg', lg)}")
            return out
        try:
            zf_map, amt_map, hs_map = {}, {}, {}
            for code in out["代码"].astype(str).str.zfill(6).tolist():
                prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
                rs = bs.query_history_k_data_plus(
                    f"{prefix}.{code}",
                    "date,code,amount,turn,pctChg",
                    start_date=day,
                    end_date=day,
                    frequency="d",
                    adjustflag="3",
                )
                row = None
                while rs.error_code == "0" and rs.next():
                    row = rs.get_row_data()
                    break
                if not row or len(row) < 5:
                    continue
                try:
                    amt_map[code] = float(row[2] or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    hs_map[code] = float(row[3] or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    zf_map[code] = float(row[4] or 0)
                except (TypeError, ValueError):
                    pass
                time_mod.sleep(0.05)
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    def _fill(col: str, mp: dict) -> None:
        if not mp:
            return
        vals = []
        for _, r in out.iterrows():
            c = str(r["代码"]).zfill(6)
            cur = float(pd.to_numeric(r.get(col), errors="coerce") or 0)
            vals.append(mp[c] if (c in mp and cur <= 0) else cur)
        out[col] = vals

    _fill("涨跌幅", zf_map)
    _fill("成交额", amt_map)
    _fill("换手率", hs_map)
    print(
        f"baostock 补字段：涨跌幅 {len(zf_map)} / 成交额 {len(amt_map)} / 换手 {len(hs_map)}"
    )
    return out


def _repair_sparse_zt_metrics(
    today_zt_only: pd.DataFrame,
    query_date_str: str,
    *,
    results_only: bool = False,
) -> pd.DataFrame:
    """空壳涨停池补打分字段：先填涨停幅度，再 enrich_turnover，失败则 baostock。

    live / 历史共用。池内即涨停，涨跌幅≤0 时按板块涨停幅度回填。
    """
    out = today_zt_only.copy()
    for col in ("涨跌幅", "成交额", "换手率"):
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)

    zf = out["涨跌幅"]
    need = zf <= 0
    if need.any():
        out.loc[need, "涨跌幅"] = out.loc[need, "代码"].map(lambda c: _daily_limit_pct(c))
        print(f"已为 {int(need.sum())} 只补全涨停涨跌幅")

    amt_pos = float(out["成交额"].gt(0).sum())
    hs_pos = float(out["换手率"].gt(0).sum())
    n_need = int(len(out))
    if amt_pos >= max(1, n_need * 0.3) and hs_pos >= max(1, n_need * 0.3):
        return out

    if results_only:
        print(
            f"results_only → 跳过 enrich_turnover"
            f"（成交额>0仅 {int(amt_pos)}/{n_need}，防超限）"
        )
        return out

    enriched = False
    try:
        _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        shared = os.path.join(_pkg, "规范文档", "共享取数", "lib")
        lib = os.path.join(_pkg, "交易记录UI", "lib")
        for _p in (shared, lib):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from hist_market_data import enrich_turnover  # noqa: WPS433
        try:
            from platform_gate import PlatformQuotaExceeded as _PQE, quota_status as _qs
        except Exception:
            _PQE = RuntimeError
            _qs = None
        em_rem = None
        if _qs:
            em_rem = (_qs().get("eastmoney") or {}).get("remaining_hour")
        if em_rem is not None and em_rem <= 0:
            print("eastmoney 小时软上限已满 → 跳过 enrich 东财轨，改 baostock")
        elif em_rem is not None and em_rem < max(3, n_need):
            print(
                f"eastmoney 小时余量不足({em_rem}<{n_need}) → 跳过 enrich，改 baostock"
            )
        else:
            print(f"字段稀疏（成交额>0仅 {int(amt_pos)}），enrich_turnover…")
            try:
                out = enrich_turnover(out, query_date_str, sleep_sec=0.45)
                enriched = True
            except _PQE:
                print("enrich_turnover 触软上限 → 改 baostock 补字段")
    except Exception as e:
        if "QUOTA_STOP" in str(e) or e.__class__.__name__ == "PlatformQuotaExceeded":
            print(f"enrich_turnover 配额停：{e} → 改 baostock")
        else:
            print(f"enrich_turnover 跳过: {e}")

    amt2 = float(pd.to_numeric(out.get("成交额"), errors="coerce").fillna(0).gt(0).sum())
    if (not enriched) or amt2 < max(1, n_need * 0.3):
        out = _enrich_turnover_baostock(out, query_date_str)
    return out

def analyze_zhangting_stocks(
    date: str | None = None,
    *,
    buy_date: str | None = None,
    results_only: bool = False,
    skip_fundamentals: bool = False,
):
    """首板强度分析并落 results。

    日期锚定（历史回补铁律）：
    - `--date` / date：涨停日 D0（与 results 文件名一致）
    - `--buy-date` / buy_date：买入日 D1，内部推 D0=前一交易日
    - 都不传：D0=今天的前一交易日（早盘默认）
    基因/趋势一律按 D0 截断，同一 date 重跑结果稳定，不会漂到「今天」。
    results_only=True：只写 results，不 sync trades（保护 5–8 月 xlsx 手工 Top2）。
    """
    clear_zt_pool_cache()
    print("开始分析涨停股票...", flush=True)

    if buy_date and not date:
        d1 = datetime.strptime(str(buy_date)[:10], "%Y-%m-%d")
        previous_trading_day = get_previous_trading_day(d1)
    elif date:
        s = str(date).strip()
        if len(s) == 8 and s.isdigit():
            previous_trading_day = datetime.strptime(s, "%Y%m%d")
        else:
            previous_trading_day = datetime.strptime(s[:10], "%Y-%m-%d")
    else:
        previous_trading_day = get_previous_trading_day(datetime.now())

    query_date_str = previous_trading_day.strftime("%Y%m%d")
    query_date_display = previous_trading_day.strftime("%Y年%m月%d日")
    print(f"查询日期(D0): {query_date_display} 锚定回补")

    print("正在获取涨停股票数据...")
    # 获取前一个交易日涨停板股票
    try:
        zt_df = fetch_zt_pool_em(query_date_str)
    except (RequestException, OSError) as e:
        print(f"获取涨停数据失败（已多次重试）: {e}")
        print("请检查网络、代理或稍后重试；若持续失败可尝试: pip install -U certifi akshare requests urllib3")
        return {"ok": False, "message": f"获取涨停数据失败: {e}", "query_date_display": query_date_display}
    
    if zt_df.empty or "代码" not in zt_df.columns:
        print(f"{query_date_display} 暂无涨停股票数据（接口返回空或格式异常）。")
        return {"ok": False, "message": f"{query_date_display} 暂无涨停股票数据", "query_date_display": query_date_display}
    
    print(f"获取到 {len(zt_df)} 只涨停股票")
    
    # 首板：D0 涨停，D0-1 不是涨停。连板跳过。D1 买入，D2 卖出。
    two_days_ago = get_previous_trading_day(previous_trading_day)
    two_days_ago_str = two_days_ago.strftime('%Y%m%d')
    print(f"筛选首次涨停股票（排除{two_days_ago_str}已涨停的股票）...")
    try:
        two_days_ago_zt = fetch_zt_pool_em(two_days_ago_str)
    except (RequestException, OSError) as e:
        print(f"获取前两日涨停池失败: {e}")
        return {"ok": False, "message": f"获取前两日涨停池失败: {e}", "query_date_display": query_date_display}
    
    prev_zt_codes = _zt_pool_codes(two_days_ago_zt)
    # 涨停池本身包含连板。首板只能由「连板数=1」或「前一日完整涨停池里没有这只」证明。
    # 前一日池为空时，禁止把当日涨停全部当成首板（2026-05-28 华塑控股就是这样混进来的）。
    lianban_num = pd.to_numeric(zt_df["连板数"], errors="coerce") if "连板数" in zt_df.columns else None
    has_lianban = lianban_num is not None and bool(lianban_num.notna().any())
    if has_lianban:
        before = len(zt_df)
        zt_df = zt_df[lianban_num.isna() | (lianban_num <= 1)].copy()
        print(f"按连板数剔除连板 {before - len(zt_df)} 只")
    if not prev_zt_codes and not has_lianban:
        print(f"{two_days_ago_str} 无涨停数据，不能把当日涨停全部当成首板")
        return {
            "ok": False,
            "message": f"{two_days_ago_str} 涨停池缺失，拒绝把连板当首板",
            "query_date_display": query_date_display,
        }

    today_zt_only = zt_df[~zt_df["代码"].isin(prev_zt_codes)] if prev_zt_codes else zt_df
    
    # 增加筛选条件：排除科创板（688开头）、北交所（92开头）和ST票（名称包含ST或st），主要查询主板和创业板
    today_zt_only = today_zt_only[~today_zt_only['代码'].str.startswith(('688', '92')) & ~today_zt_only['名称'].str.contains('ST|st', case=False)]
    today_zt_only = _keep_proven_first_board(today_zt_only, previous_trading_day, two_days_ago)
    from first_board_gate import keep_first_board
    today_zt_only = keep_first_board(today_zt_only, previous_trading_day, two_days_ago)

    if today_zt_only.empty:
        print(f"{query_date_display} 无符合连续1天涨停条件的股票。")
        return {"ok": False, "message": f"{query_date_display} 无首板候选", "query_date_display": query_date_display}
    
    print(f"筛选后剩余 {len(today_zt_only)} 只股票")

    hist_mode = bool(date or buy_date)
    print("正在计算各项指标（基因/趋势 as_of=D0）...")
    today_zt_only = today_zt_only.copy()

    # live / 历史：空壳池（指标全 0）一律补字段，否则综合得分会被压成≈基因×0.1
    if _zt_metrics_sparse(today_zt_only):
        print("检测到空壳涨停池（涨跌幅/成交额/换手近乎全 0），开始补字段…")
        today_zt_only = _repair_sparse_zt_metrics(
            today_zt_only, query_date_str, results_only=results_only
        )
        # 补完后若已有真实指标，写回磁盘缓存，避免次日再中毒
        try:
            _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            lib = os.path.join(_pkg, "交易记录UI", "lib")
            if lib not in sys.path:
                sys.path.insert(0, lib)
            import board_data_sources as bds  # noqa: WPS433
            if bds.zt_metrics_usable(today_zt_only):
                bds.save_zt_disk(query_date_str, today_zt_only)
                bds._ZT_MEM[query_date_str] = today_zt_only
                print(f"已用补全后的涨停池刷新磁盘缓存 {query_date_str}")
        except Exception as e:
            print(f"刷新涨停池缓存跳过: {e}")

    if hist_mode:
        # 基因：只读已缓存涨停池；趋势：腾讯日线（platform_gate）
        try:
            _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            lib = os.path.join(_pkg, "交易记录UI", "lib")
            if lib not in sys.path:
                sys.path.insert(0, lib)
            import board_data_sources as bds  # noqa: WPS433
            # 不预热 30 日涨停池（易打满小时软上限并卡死）；只读已有磁盘/内存缓存算基因
            today_zt_only["历史涨停基因"] = today_zt_only["代码"].apply(
                lambda x: bds.gene_from_cached_pools(str(x).zfill(6), previous_trading_day, 30)
            )
            # 历史回补：全池逐票拉日线趋势会打爆腾讯小时软上限且极慢。
            # 固定置 0，同日可复现；区分度主要靠涨幅/成交额/基因（与 live 权重兼容）。
            today_zt_only["趋势指标"] = 0
            print("历史回补：趋势指标固定为 0（省配额/保速度）")
        except Exception as e:
            print(f"历史基因/趋势回退旧逻辑: {e}")
            today_zt_only["历史涨停基因"] = today_zt_only["代码"].apply(
                lambda x: get_historical_zt_count(x, 30, as_of=previous_trading_day)
            )
            today_zt_only["趋势指标"] = today_zt_only["代码"].apply(
                lambda x: get_trend_indicator(x, as_of=previous_trading_day)
            )
    else:
        # live：基因优先读本地涨停池缓存（省东财小时配额）；缓存不足再打接口
        try:
            _pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            lib = os.path.join(_pkg, "交易记录UI", "lib")
            if lib not in sys.path:
                sys.path.insert(0, lib)
            import board_data_sources as bds  # noqa: WPS433

            today_zt_only["历史涨停基因"] = today_zt_only["代码"].apply(
                lambda x: bds.gene_from_cached_pools(str(x).zfill(6), previous_trading_day, 30)
            )
        except Exception as e:
            print(f"缓存基因失败，回退接口: {e}")
            today_zt_only["历史涨停基因"] = today_zt_only["代码"].apply(
                lambda x: get_historical_zt_count(x, 30, as_of=previous_trading_day)
            )
        today_zt_only["趋势指标"] = today_zt_only["代码"].apply(
            lambda x: get_trend_indicator(x, as_of=previous_trading_day)
        )

    if skip_fundamentals:
        today_zt_only["市盈率"] = 0
    else:
        today_zt_only["市盈率"] = today_zt_only["代码"].apply(
            lambda x: get_basic_fundamentals(x, as_of=previous_trading_day)
        )

    # 计算综合得分
    today_zt_only["综合得分"] = today_zt_only.apply(calculate_composite_score, axis=1)

    # 强度排名：按综合得分排序，取前2只
    sorted_stocks = today_zt_only.sort_values("综合得分", ascending=False)
    top2 = sorted_stocks.head(2)

    # D0=涨停日，D1=买入日=D0下一交易日
    buy_day = get_next_trading_day(previous_trading_day)
    buy_date_str = buy_day.strftime("%Y-%m-%d")
    print(f"买入日(D0): {buy_date_str}（涨停日 D0-1={query_date_display}）")
    if not results_only:
        sync_trades_to_ui(top2, buy_date_str)
    else:
        print("results_only：跳过 trades 同步（保留既有/xlsx Top2）")
    
    print("分析完成，正在生成结果...")
    # 准备输出内容
    output_lines = []
    output_lines.append(f"=== {query_date_display} 强度推荐排名（前2只，基于多因子综合得分）===")
    output_lines.append(f"买入日(D0): {buy_date_str} | 卖出日(D1): 下一交易日 9:35/13:35")
    for rank, (idx, row) in enumerate(top2.iterrows(), start=1):
        output_lines.append(f"排名 {rank}: {row['代码']} - {row['名称']}")
        output_lines.append(f"  涨停天数: 1天")
        output_lines.append(f"  涨幅: {row['涨跌幅']}%")
        output_lines.append(f"  成交额: {row['成交额']}")
        output_lines.append(f"  换手率: {row['换手率']}%")
        output_lines.append(f"  历史涨停基因（30天内涨停次数）: {row['历史涨停基因']}")
        output_lines.append(f"  趋势指标（1=上升，0=其他）: {row['趋势指标']}")
        output_lines.append(f"  基本面（市盈率）: {row['市盈率']}")
        output_lines.append(f"  综合得分: {row['综合得分']:.2f}")
        # 涨停原因分析（假设数据中有此字段）
        reason = row.get('涨停原因', '数据中未提供具体原因')
        output_lines.append(f"  涨停原因分析: {reason}")
        # 连板属性和胜率（简化）
        lianban = "有连板潜力" if row['历史涨停基因'] > 5 else "连板属性一般"
        win_rate = "首板胜率较高" if row['趋势指标'] == 1 and row['历史涨停基因'] > 3 else "首板胜率中等"
        output_lines.append(f"  连板属性: {lianban}")
        output_lines.append(f"  首板胜率评估: {win_rate}（基于历史数据和趋势）")
        output_lines.append(f"  买入后第二天卖出建议: 若趋势持续，建议持有观察；否则考虑T+1卖出")
        output_lines.append("")
    
    # 其他未入选强度前二原因
    others = sorted_stocks.iloc[2:]
    output_lines.append("=== 其他未入选强度前二原因 ===")
    if not others.empty:
        output_lines.append("以下股票综合得分较低，未入选前二：")
        for idx, row in others.iterrows():
            output_lines.append(f"  {row['代码']} - {row['名称']}: 综合得分 {row['综合得分']:.2f} - 得分低于前二")
    else:
        output_lines.append("无其他候选股票。")
    
    # 打印到控制台
    for line in output_lines:
        print(line)
    
    # 保存到文件（与脚本同目录下的 results，避免硬编码路径）
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(results_dir, exist_ok=True)
    file_name = f"{query_date_str}_recommendations.md"
    file_path = os.path.join(results_dir, file_name)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))
    print(f"\n✅ 结果已保存到: {file_path}")
    print("🎉 分析完成！")
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print(f"📊 交易记录 UI: cd {_root}/交易记录UI && python3 stock_UI.py")

    top2_records = []
    for _, row in top2.iterrows():
        top2_records.append({
            "代码": str(row["代码"]).zfill(6),
            "名称": row["名称"],
            "涨跌幅": row["涨跌幅"],
            "成交额": row["成交额"],
            "换手率": row["换手率"],
            "历史涨停基因": int(row["历史涨停基因"]),
            "趋势指标": int(row["趋势指标"]),
            "市盈率": row["市盈率"],
            "综合得分": float(row["综合得分"]),
            "涨停原因": row.get("涨停原因", row.get("所属行业", "")),
        })
    others_summary = [
        f"{row['代码']} {row['名称']}: 得分 {row['综合得分']:.2f}（低于前二）"
        for _, row in others.iterrows()
    ]
    result = {
        "ok": True,
        "query_date": query_date_str,
        "query_date_display": query_date_display,
        "buy_date": buy_date_str,
        "top2": top2_records,
        "others_summary": others_summary,
        "file_path": file_path,
    }
    _save_stock_result_json(result)
    return result


_STOCK_RESULT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strength_stock_result.json")


def _save_stock_result_json(result: dict, path: str = None) -> None:
    import json as _json

    path = path or _STOCK_RESULT_JSON
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(result, f, ensure_ascii=False, indent=2, default=str)


def _load_pushplus_token() -> str:
    # 与 pushplus_notify 同源，避免 token 解析分叉
    _stock_dir = os.path.dirname(os.path.abspath(__file__))
    _model_dir = os.path.dirname(_stock_dir)
    if _model_dir not in sys.path:
        sys.path.insert(0, _model_dir)
    from pushplus_notify import load_pushplus_token

    return load_pushplus_token()


def _format_amount(amount) -> str:
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    if val >= 1e8:
        return f"{val / 1e8:.2f}亿"
    if val >= 1e4:
        return f"{val / 1e4:.2f}万"
    return f"{val:.0f}"


def build_stock_notify_content(result: dict | None) -> tuple:
    """PushPlus 标题与正文。分析失败也要发出去，避免静默漏通知。"""
    title = "stock模型通知"
    if not result or not result.get("top2"):
        message = (result or {}).get("message") or "今日无符合条件的首板标的"
        return title, f"【stock Top2】\n\n{message}"

    lines = [
        "【stock Top2】",
        f"数据日期: {result.get('query_date_display', '')}",
        f"买入日: {result.get('buy_date', '')}",
        f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for rank, row in enumerate(result["top2"], start=1):
        gene = row.get("历史涨停基因", 0)
        trend = row.get("趋势指标", 0)
        lianban = "有连板潜力" if gene > 5 else "连板属性一般"
        win_rate = "首板胜率较高" if trend == 1 and gene > 3 else "首板胜率中等"
        reason = row.get("涨停原因") or "数据中未提供具体原因"
        lines.extend([
            f"━━ 排名 {rank} ━━",
            f"{row['代码']} {row['名称']}",
            f"综合得分: {float(row['综合得分']):.2f}",
            f"涨幅 {row['涨跌幅']}% | 换手 {row['换手率']}%",
            f"成交额 {_format_amount(row['成交额'])}",
            f"30日涨停基因: {gene} | 趋势: {'上升' if trend == 1 else '震荡/偏弱'}",
            f"涨停原因: {reason}",
            f"连板属性: {lianban}",
            f"胜率评估: {win_rate}",
            "",
        ])
    others = result.get("others_summary") or []
    if others:
        lines.append("── 未入选原因（节选）──")
        for item in others[:5]:
            lines.append(f"· {item}")
        if len(others) > 5:
            lines.append(f"… 另有 {len(others) - 5} 只未列出")
    return title, "\n".join(lines)


def _send_stock_push(title: str, content: str) -> bool:
    """HTTPS+certifi 优先；规定时间内传输失败则降级 HTTP。"""
    _stock_dir = os.path.dirname(os.path.abspath(__file__))
    _model_dir = os.path.dirname(_stock_dir)
    if _model_dir not in sys.path:
        sys.path.insert(0, _model_dir)
    from pushplus_notify import send_pushplus
    from pushplus_notify import KIND_STOCK_NOTIFY

    # stock 模型通知（每交易日 09:05）转发给好友
    # （用户 2026-10-26 规则：好友只收「模型通知」+「周结果」两类）
    ok = send_pushplus(title, content, kind=KIND_STOCK_NOTIFY)
    if ok:
        # 发送成功留痕：不管这条通知是 crontab 还是 UI 调度发的，
        # 都写同一个标记，另一路看到当天已发就不会重复发（2026-10-27 加）。
        # 2026-10-29 补充：标记里同时记录所发数据的买入日(buy_date)，
        # 供兜底判断「是否用当日真数据发过」，避免用过期数据发一次就误判完成。
        try:
            import json as _json

            buy_date = ""
            try:
                with open(_STOCK_RESULT_JSON, encoding="utf-8") as _bf:
                    buy_date = _json.load(_bf).get("buy_date", "")
            except Exception:
                pass
            marker = os.path.join(_stock_dir, "last_notify.json")
            with open(marker, "w", encoding="utf-8") as f:
                _json.dump(
                    {
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "title": title,
                        "buy_date": buy_date,
                    },
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:
            print(f"[通知] 写入发送标记失败（不影响发送）: {e}", flush=True)
    return ok


def _wait_until_beijing(hour: int, minute: int) -> None:
    target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    now = datetime.now()
    if now >= target:
        print(f"[通知] 已过 {hour:02d}:{minute:02d}，立即发送", flush=True)
        return
    wait_seconds = (target - now).total_seconds()
    print(f"[通知] 等待至 {target.strftime('%H:%M')} 发送（还需 {wait_seconds:.0f} 秒）...", flush=True)
    time_mod.sleep(wait_seconds)


def _already_notified_today() -> bool:
    """按 last_notify.json 的 date + buy_date 判重，避免多入口重复推送同一天真数据。

    仅当「标记日期==今天 且 标记买入日==当前结果 JSON 买入日」才视为已发，
    覆盖旧格式（无 buy_date）时保守返回 False 以允许重发。
    """
    try:
        import json as _json

        here = os.path.dirname(os.path.abspath(__file__))
        marker = os.path.join(here, "last_notify.json")
        with open(marker, encoding="utf-8") as f:
            m = _json.load(f)
        if m.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return False
        buy = m.get("buy_date", "")
        if not buy:
            return False
        try:
            with open(_STOCK_RESULT_JSON, encoding="utf-8") as f:
                cur = _json.load(f).get("buy_date", "")
        except Exception:
            cur = ""
        return buy == cur
    except Exception:
        return False


def _today_is_trading_day() -> bool:
    """A 股交易日闸门（通知入口共用）。日历异常时：工作日 fail-open，周末 fail-closed。"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        fc = _local_calendar_mod()
        if hasattr(fc, "is_trading_day"):
            return bool(fc.is_trading_day(today))
        # 等价：today == next(prev(today+1天的日历锚))
        nxt = fc.next_trading_day_dt(datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1))
        return nxt.strftime("%Y-%m-%d") == today
    except Exception:
        return datetime.now().weekday() < 5


def send_notify_from_saved_json(path: str = _STOCK_RESULT_JSON) -> bool:
    if not _today_is_trading_day():
        print("[通知] 今日非 A 股交易日，跳过推送", flush=True)
        return True
    if _already_notified_today():
        print("[通知] 今日该买入日数据已发过，跳过（last_notify.json）", flush=True)
        return True
    import json as _json

    with open(path, encoding="utf-8") as f:
        result = _json.load(f)
    title, content = build_stock_notify_content(result)
    return _send_stock_push(title, content)


def run_with_notify(notify_at=None) -> dict:
    if not _today_is_trading_day():
        print("[通知] 今日非 A 股交易日，仅落盘不推送", flush=True)
        result = analyze_zhangting_stocks()
        if not result:
            result = {"ok": False, "message": "分析未返回结果"}
        _save_stock_result_json(result)
        return result
    result = analyze_zhangting_stocks()
    if not result:
        result = {"ok": False, "message": "分析未返回结果"}
    _save_stock_result_json(result)
    if notify_at:
        _wait_until_beijing(notify_at[0], notify_at[1])
    title, content = build_stock_notify_content(result)
    if not _send_stock_push(title, content):
        raise SystemExit("[stock] PushPlus 通知发送失败")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="首板强度推荐分析")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="涨停日 D0=YYYY-MM-DD 或 YYYYMMDD（与 results 文件名一致；历史回补必传）",
    )
    parser.add_argument(
        "--buy-date",
        type=str,
        default=None,
        help="买入日 D1=YYYY-MM-DD；内部推 D0=前一交易日",
    )
    parser.add_argument(
        "--results-only",
        action="store_true",
        help="只写 results，不覆盖 trades（历史回补默认建议开启）",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="跳过市盈率接口（省配额；得分里市盈率项为 0）",
    )
    parser.add_argument("--notify", action="store_true", help="执行后发送 PushPlus 通知")
    parser.add_argument("--notify-only", action="store_true", help="仅从已保存 JSON 重发通知")
    parser.add_argument("--notify-at", type=str, default=None, help="北京时间定时发送，格式 HH:MM")
    args = parser.parse_args()

    if args.notify_only:
        ok = send_notify_from_saved_json()
        raise SystemExit(0 if ok else 1)

    if args.notify or args.notify_at:
        if args.date or args.buy_date:
            print("历史 --date/--buy-date 与 --notify 同开时：先落盘再按通知逻辑（不改日期锚定）")
            analyze_zhangting_stocks(
                date=args.date,
                buy_date=args.buy_date,
                results_only=args.results_only,
                skip_fundamentals=args.skip_fundamentals,
            )
        notify_at = None
        if args.notify_at:
            hh, mm = args.notify_at.split(":")
            notify_at = (int(hh), int(mm))
        run_with_notify(notify_at=notify_at)
    else:
        analyze_zhangting_stocks(
            date=args.date,
            buy_date=args.buy_date,
            results_only=args.results_only,
            skip_fundamentals=args.skip_fundamentals,
        )