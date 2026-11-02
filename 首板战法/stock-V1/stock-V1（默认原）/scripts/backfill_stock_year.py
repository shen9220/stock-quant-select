"""
回填指定年份 stock 首板 Top2 明细 + 补价。
数据源：同花顺涨停池（首板，约保留近 8 个月）→ 成交额 Top2；
        更早日期自动回退 baostock 全市场扫描首板。

用法:
  python3 backfill_stock_year.py --year 2025 --records-only
  python3 backfill_stock_year.py --year 2025 --enrich-only
  python3 backfill_stock_year.py --year 2025 --months 2025-01,2025-02 --source baostock
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

_SCRIPT = os.path.dirname(os.path.abspath(__file__))
_PROG = os.path.dirname(_SCRIPT)
_FAMILY = os.path.dirname(_PROG)  # stock-V1
_ROOT = os.path.dirname(_FAMILY)  # 首板战法
for _p in (_ROOT, os.path.join(_ROOT, "交易记录UI"), _PROG):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import price_service as ps  # noqa: E402
import requests  # noqa: E402

from stock_UI import (  # noqa: E402
    _load_day,
    _save_day,
    enrich_trade_prices,
    purge_open_limit_buys,
    recalculate_all_trades,
    upsert_from_stock_py,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://quote.eastmoney.com/",
}
_UNIVERSE_CACHE: list[tuple[str, str, str]] | None = None
_UNIVERSE_CACHE_FILE = os.path.join(_SCRIPT, "universe_cache.json")


def _load_universe_sina() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    page = 1
    while page <= 120:
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1&node=hs_a"
        )
        try:
            r = requests.get(
                url,
                timeout=20,
                headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"},
            )
            r.raise_for_status()
            rows = r.json() or []
            if not rows:
                break
            for row in rows:
                code6 = str(row.get("code") or "").zfill(6)
                name = str(row.get("name") or code6)
                if code6 in seen:
                    continue
                if code6.startswith(("688", "8", "4", "92")):
                    continue
                if "ST" in name.upper():
                    continue
                if not code6.startswith(("600", "601", "603", "605", "000", "001", "002", "003", "300")):
                    continue
                secid = f"{'1' if code6.startswith(('600','601','603','605')) else '0'}.{code6}"
                seen.add(code6)
                out.append((code6, name, secid))
            page += 1
            time.sleep(0.08)
        except Exception as e:
            print(f"  [warn] 新浪列表 page={page} 失败: {e}")
            break
    return out


def _save_universe_cache(items: list[tuple[str, str, str]]) -> None:
    try:
        with open(_UNIVERSE_CACHE_FILE, "w", encoding="utf-8") as f:
            import json
            json.dump([{"code": c, "name": n, "secid": s} for c, n, s in items], f, ensure_ascii=False)
    except Exception:
        pass


def _load_universe_cache() -> list[tuple[str, str, str]]:
    if not os.path.isfile(_UNIVERSE_CACHE_FILE):
        return []
    try:
        import json
        with open(_UNIVERSE_CACHE_FILE, "r", encoding="utf-8") as f:
            rows = json.load(f) or []
        return [(r["code"], r["name"], r["secid"]) for r in rows if r.get("code")]
    except Exception:
        return []


def _load_universe() -> list[tuple[str, str, str]]:
    global _UNIVERSE_CACHE
    if _UNIVERSE_CACHE is not None:
        return _UNIVERSE_CACHE
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    pn = 1
    while True:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
            "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14"
        )
        try:
            r = requests.get(url, timeout=20, headers=HEADERS)
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
            diff = data.get("diff") or []
            if not diff:
                break
            for row in diff:
                code6 = str(row.get("f12") or "").zfill(6)
                if code6 in seen:
                    continue
                name = str(row.get("f14") or code6)
                if code6.startswith(("688", "8", "4", "92")):
                    continue
                if "ST" in name.upper():
                    continue
                if not code6.startswith(("600", "601", "603", "605", "000", "001", "002", "003", "300")):
                    continue
                secid = f"{'1' if code6.startswith(('600','601','603','605')) else '0'}.{code6}"
                seen.add(code6)
                out.append((code6, name, secid))
            total = int(data.get("total") or 0)
            if pn * 100 >= total:
                break
            pn += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"  [warn] 加载股票列表失败 pn={pn}: {e}")
            break
    if not out:
        cached = _load_universe_cache()
        if cached:
            print(f"  [universe] 使用本地缓存 {len(cached)} 只")
            _UNIVERSE_CACHE = cached
            return cached
        print("  [universe] 东财不可用，改用新浪列表…")
        out = _load_universe_sina()
        if out:
            _save_universe_cache(out)
    else:
        # 东财中途失败常只返回几十~几百只；不足则强制回退缓存，避免「无首板」假阴性
        cached = _load_universe_cache()
        if cached and len(out) < max(1500, int(len(cached) * 0.5)):
            print(
                f"  [universe] 在线仅 {len(out)} 只（疑似残缺），改用本地缓存 {len(cached)} 只"
            )
            out = cached
        elif len(out) >= 1500:
            _save_universe_cache(out)
    _UNIVERSE_CACHE = out
    print(f"  [universe] 加载 {len(out)} 只股票")
    return out


def _next_trading_day(d0: str) -> str | None:
    nxt = ps.next_trading_day(d0)
    if nxt:
        return nxt
    cur = datetime.strptime(d0, "%Y-%m-%d")
    for _ in range(12):
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            return cur.strftime("%Y-%m-%d")
    return None


def _prev_trading_day(d0: str) -> str | None:
    prev = ps.prev_trading_day(d0)
    if prev:
        return prev
    cur = datetime.strptime(d0, "%Y-%m-%d")
    for _ in range(12):
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            return cur.strftime("%Y-%m-%d")
    return None


def _em_day_bar(secid: str, d0: str, prev_d0: str) -> dict | None:
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=0&beg={prev_d0.replace('-', '')}&end={d0.replace('-', '')}"
    )
    try:
        r = requests.get(url, timeout=12, headers=HEADERS)
        r.raise_for_status()
        klines = ((r.json() or {}).get("data") or {}).get("klines") or []
    except Exception:
        return None
    by_date = {}
    for line in klines:
        parts = line.split(",")
        if len(parts) < 10:
            continue
        by_date[parts[0]] = {"pct": float(parts[8] or 0), "amount": float(parts[6] or 0)}
    cur = by_date.get(d0)
    if not cur or cur["pct"] < 9.8:
        return None
    prev = by_date.get(prev_d0) or {"pct": 0.0}
    if float(prev.get("pct") or 0) >= 9.8:
        return None
    return cur


def _tx_day_bar(code6: str, d0: str, prev_d0: str) -> dict | None:
    start = ps.prev_trading_day(prev_d0) or prev_d0
    df = ps._fetch_daily(code6, start, d0)
    if df is None or df.empty:
        return None
    d0_dt = ps._to_dt(d0)
    prev_dt = ps._to_dt(prev_d0)
    if d0_dt is None or prev_dt is None:
        return None
    cur_row = df[df["日期"] == d0_dt]
    prev_row = df[df["日期"] == prev_dt]
    if cur_row.empty or prev_row.empty:
        return None
    close = float(cur_row.iloc[0]["收盘"] or 0)
    prev_close = float(prev_row.iloc[0]["收盘"] or 0)
    if prev_close <= 0 or close <= 0:
        return None
    pct = (close - prev_close) / prev_close * 100
    if pct < 9.8:
        return None
    prev_pct = 0.0
    if len(prev_row):
        pc = float(prev_row.iloc[0]["收盘"] or 0)
        # prev day's pct needs day before prev - approximate: if prev was limit, skip
        pp_row = df[df["日期"] < prev_dt].tail(1)
        if not pp_row.empty:
            ppc = float(pp_row.iloc[0]["收盘"] or 0)
            if ppc > 0:
                prev_pct = (pc - ppc) / ppc * 100
    if prev_pct >= 9.8:
        return None
    amount = float(cur_row.iloc[0]["amount"] or 0) if "amount" in cur_row.columns else 0.0
    return {"pct": pct, "amount": amount}


def _tx_scan_one(payload: tuple[str, str, str, str, str]) -> dict | None:
    code6, name, _secid, d0, prev_d0 = payload
    bar = _tx_day_bar(code6, d0, prev_d0)
    if not bar:
        return None
    return {"code": code6, "name": name, "amount": bar["amount"]}


def fetch_first_board_tx(d0: str, workers: int = 1, interval_sec: float = 0.45) -> list[dict]:
    """腾讯日线扫首板。universe 残缺时强制回退缓存；默认间隔 0.45s 以在单晚跑完 1–3 天。"""
    prev = _prev_trading_day(d0)
    if not prev:
        return []
    universe = _load_universe()
    if not universe:
        return []
    if len(universe) < 1500:
        cached = _load_universe_cache()
        if cached and len(cached) >= 1500:
            print(f"  [tx-kline] universe 仅 {len(universe)}，强制改用缓存 {len(cached)}")
            universe = cached
            global _UNIVERSE_CACHE
            _UNIVERSE_CACHE = cached
        else:
            print(f"  [tx-kline] universe 过小 {len(universe)} 且无可用缓存，放弃本轮扫市")
            return []
    hits: list[dict] = []
    tasks = [(c, n, s, d0, prev) for c, n, s in universe]
    if workers <= 1:
        for i, t in enumerate(tasks):
            row = _tx_scan_one(t)
            if row:
                hits.append(row)
            if interval_sec > 0:
                time.sleep(interval_sec)
            if (i + 1) % 200 == 0:
                print(f"    [tx-kline] 已扫描 {i + 1}/{len(tasks)} 命中={len(hits)} …")
    else:
        print(f"  [warn] workers={workers} 并行扫描，有封号风险，建议 workers=1")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_tx_scan_one, t) for t in tasks]
            for fut in as_completed(futs):
                row = fut.result()
                if row:
                    hits.append(row)
    hits.sort(key=lambda x: x["amount"], reverse=True)
    print(f"  [tx-kline] {d0} 完成 universe={len(universe)} 首板={len(hits)}")
    return hits


def _em_scan_one(payload: tuple[str, str, str, str, str]) -> dict | None:
    code6, name, secid, d0, prev_d0 = payload
    bar = _em_day_bar(secid, d0, prev_d0)
    if not bar:
        return None
    return {"code": code6, "name": name, "amount": bar["amount"]}


def fetch_first_board_em(d0: str, workers: int = 16) -> list[dict]:
    prev = _prev_trading_day(d0)
    if not prev:
        return []
    universe = _load_universe()
    if not universe:
        return []
    hits: list[dict] = []
    tasks = [(c, n, s, d0, prev) for c, n, s in universe]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_em_scan_one, t) for t in tasks]
        for fut in as_completed(futs):
            row = fut.result()
            if row:
                hits.append(row)
    hits.sort(key=lambda x: x["amount"], reverse=True)
    return hits


def fetch_first_board_ths(d0_8: str) -> list[dict]:
    url = f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&date={d0_8}"
    ths_headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.10jqka.com.cn/"}
    try:
        r = requests.get(url, timeout=20, headers=ths_headers)
        r.raise_for_status()
        info = ((r.json() or {}).get("data") or {}).get("info") or []
    except Exception as e:
        print(f"  [warn] 涨停池 {d0_8}: {e}")
        return []
    out, seen = [], set()
    for x in info:
        code = str(x.get("code") or "").zfill(6)
        name = str(x.get("name") or "").strip()
        tag = str(x.get("change_tag") or "")
        again = x.get("is_again_limit")
        if not code or code.startswith(("688", "8", "4")):
            continue
        if "ST" in name.upper():
            continue
        if again in (1, "1", True) and tag != "FIRST_LIMIT":
            continue
        if tag == "LIMIT_BACK" and again in (1, "1", True):
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name})
    return out


def fetch_first_board(d0: str, source: str = "auto", workers: int = 1) -> list[dict]:
    d0_8 = d0.replace("-", "")
    if source == "baostock":
        print("  [拒绝] source=baostock 请走 fetch_first_board_resilient 兜底")
        return []
    if source in ("auto", "ths"):
        first = fetch_first_board_ths(d0_8)
        if first or source == "ths":
            return first
    if source in ("auto", "tx"):
        print(f"  [tx-kline] 扫描 {d0} 首板（workers={workers}）…")
        return fetch_first_board_tx(d0, workers=max(1, workers))
    if source == "em":
        print(f"  [em-kline] 扫描 {d0} 首板（workers={workers}，不稳定）…")
        return fetch_first_board_em(d0, workers=workers)
    return []


def fetch_first_board_resilient(d0: str, workers: int = 1) -> tuple[list[dict], str]:
    """
    稳健拉取首板（优先速度，保证日产出）:
      THS → Baostock 单日 → 腾讯全量扫。
    返回 (hits, source_used)。
    empty_confirmed = 多源均无；empty_unconfirmed = 中途失败勿推进游标。
    """
    d0_8 = d0.replace("-", "")
    ths = fetch_first_board_ths(d0_8)
    if ths:
        print(f"  [resilient] {d0} THS 命中 {len(ths)}")
        return ths, "ths"

    allow_bs = os.environ.get("TX_ALLOW_BAOSTOCK_FALLBACK", "1").lower() in ("1", "true", "yes")
    if allow_bs:
        # 与 03/16 Baostock 专线错峰：对方持锁或已封禁则跳过，改走腾讯
        _bs_busy = False
        try:
            import fcntl

            _here = os.path.dirname(os.path.abspath(__file__))
            _bs_lock = os.path.normpath(
                os.path.join(_here, "..", "..", "..", "交易记录UI", "data", "baostock_backfill.lock")
            )
            _bs_paused = os.path.normpath(
                os.path.join(_here, "..", "..", "..", "交易记录UI", "data", "BAOSTOCK_PAUSED")
            )
            if os.path.isfile(_bs_paused):
                print(f"  [resilient] {d0} 跳过 Baostock（存在 BAOSTOCK_PAUSED）")
                _bs_busy = True
            elif os.path.exists(_bs_lock):
                fd = os.open(_bs_lock, os.O_RDWR)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    print(f"  [resilient] {d0} 跳过 Baostock（03/16 专线占用中）")
                    _bs_busy = True
                finally:
                    os.close(fd)
        except Exception as e:
            print(f"  [resilient] Baostock 锁检测异常（继续尝试）: {e}")

        if not _bs_busy:
            print(f"  [resilient] {d0} THS 空 → Baostock 单日（优先保证日产出）…")
            try:
                import price_service as ps
                from baostock_safe_scan import scan_first_board_day

                prev_disabled = bool(getattr(ps, "_BS_DISABLED", False))
                prev_env = os.environ.get("DISABLE_BAOSTOCK")
                ps._BS_DISABLED = False
                if "DISABLE_BAOSTOCK" in os.environ:
                    del os.environ["DISABLE_BAOSTOCK"]
                try:
                    uni = _load_universe()
                    if uni and len(uni) >= 1500 and ps._bs_ensure_login():
                        hits = scan_first_board_day(d0, uni, interval_sec=0.12)
                        if hits:
                            print(f"  [resilient] {d0} Baostock 命中 {len(hits)}")
                            return hits, "baostock_fallback"
                        print(f"  [resilient] {d0} Baostock 无首板")
                    else:
                        print(
                            f"  [resilient] Baostock 跳过 universe="
                            f"{len(uni) if uni else 0} login={ps.baostock_enabled()}"
                        )
                finally:
                    ps._BS_DISABLED = prev_disabled
                    if prev_env is not None:
                        os.environ["DISABLE_BAOSTOCK"] = prev_env
                    try:
                        import baostock as bs

                        bs.logout()
                        setattr(ps, "_BS_LOGGED_IN", False)
                    except Exception:
                        pass
            except Exception as e:
                print(f"  [resilient] Baostock 异常: {e}")

    print(f"  [resilient] {d0} → 腾讯全量扫（兜底）…")
    tx = fetch_first_board_tx(d0, workers=max(1, workers), interval_sec=0.45)
    if tx:
        return tx, "tx"

    # Baostock 已试过仍空，或未启用：视为确认空（避免假阴性反复卡游标）
    if allow_bs:
        return [], "empty_confirmed"
    return [], "empty_unconfirmed"


def rank_by_amount(stocks: list[dict], d0: str, top_n: int = 12) -> list[dict]:
    if stocks and "amount" in stocks[0]:
        ranked = sorted(stocks, key=lambda x: float(x.get("amount") or 0), reverse=True)
        return ranked[:top_n]
    ranked = []
    for s in stocks[:top_n]:
        amt = 0.0
        bar = _tx_day_bar(s["code"], d0, _prev_trading_day(d0) or d0)
        if bar:
            amt = float(bar.get("amount") or 0)
        ranked.append({**s, "amount": amt})
    ranked.sort(key=lambda x: x["amount"], reverse=True)
    return ranked


def trading_days_in_month(ym: str) -> list[str]:
    y, m = map(int, ym.split("-"))
    if m == 12:
        end = f"{y + 1}-01-01"
    else:
        end = f"{y}-{m + 1:02d}-01"
    start = f"{y}-{m:02d}-01"
    end_dt = datetime.strptime(end, "%Y-%m-%d") - timedelta(days=1)
    end_s = end_dt.strftime("%Y-%m-%d")
    cal = ps.get_trade_calendar()
    if cal is not None:
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end_s, "%Y-%m-%d")
        mask = (cal["trade_date"] >= s) & (cal["trade_date"] <= e)
        return [d.strftime("%Y-%m-%d") for d in cal.loc[mask, "trade_date"]]
    out = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_d = datetime.strptime(end_s, "%Y-%m-%d")
    while cur <= end_d:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def backfill_month_records(ym: str, source: str = "auto", workers: int = 6) -> int:
    days = trading_days_in_month(ym)
    print(f"\n######## {ym} 共 {len(days)} 个交易日（写明细 source={source}）########")
    records = []
    for d0 in days:
        buy = _next_trading_day(d0)
        if not buy:
            continue
        first = fetch_first_board(d0, source=source, workers=workers)
        if not first:
            print(f"  [D0 {d0}] 无首板")
            time.sleep(0.1)
            continue
        top2 = rank_by_amount(first, d0)[:2]
        print(f"  [D0 {d0}→D1 {buy}] " + ", ".join(f"{h['code']} {h['name']}" for h in top2))
        for i, h in enumerate(top2, 1):
            records.append({
                "date": buy,
                "stock_code": h["code"],
                "stock_name": h["name"],
                "rank": i,
            })
        time.sleep(0.15 if source != "baostock" else 0.05)

    if not records:
        return 0
    seen, uniq = set(), []
    for r in records:
        k = (r["date"], r["stock_code"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    result = upsert_from_stock_py(uniq, auto_enrich=False, program="stock")
    print(f"  写入 created={result['created']} updated={result['updated']}")
    return len(uniq)


def enrich_month(ym: str, force: bool = False) -> int:
    days = trading_days_in_month(ym)
    buy_days = set()
    for d0 in days:
        buy = _next_trading_day(d0)
        if buy:
            buy_days.add(buy)
    updated = 0
    print(f"\n######## {ym} 补价 {len(buy_days)} 个买入日 ########")
    for day in sorted(buy_days):
        trades = _load_day(day, "stock")
        if not trades:
            continue
        keep = []
        changed = False
        for t in trades:
            enriched = enrich_trade_prices(t, force=force)
            enriched.pop("skip_buy", None)
            if enriched.get("skip_reason") and "买不进" in str(enriched.get("skip_reason")):
                print(f"  留档一字 {day} {enriched.get('stock_code')}")
            before = t.get("buy_price"), t.get("sell_price_935"), t.get("pnl_batch_total_half")
            after = enriched.get("buy_price"), enriched.get("sell_price_935"), enriched.get("pnl_batch_total_half")
            if before != after:
                changed = True
                updated += 1
            keep.append(enriched)
            time.sleep(0.06)
        if changed or force:
            _save_day(day, keep, "stock")
    return updated


def enrich_year(year: int, months: list[str], force: bool = False) -> int:
    total = 0
    for ym in months:
        total += enrich_month(ym, force=force)
    return total


def main():
    import backfill_guard  # noqa: E402

    backfill_guard.assert_backfill_allowed()
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--months", default="", help="逗号分隔 YYYY-MM")
    parser.add_argument("--records-only", action="store_true", help="只写明细不补价")
    parser.add_argument("--enrich-only", action="store_true", help="只补价")
    parser.add_argument("--force-enrich", action="store_true", help="补价时强制重拉")
    parser.add_argument("--source", choices=("auto", "ths", "tx", "em"), default="ths",
                        help="ths=同花顺(默认); tx=腾讯全扫(慢); auto=ths后tx; baostock已禁用")
    parser.add_argument("--workers", type=int, default=1, help="扫描并行度，铁纪律：仅允许 1")
    args = parser.parse_args()

    if args.workers > 1:
        print("[拒绝] workers>1 有封号风险，已强制降为 1")
        args.workers = 1
    if getattr(args, "source", None) == "baostock":
        print("[拒绝] baostock 已永久禁用")
        sys.exit(2)

    if args.months:
        months = [m.strip() for m in args.months.split(",") if m.strip()]
    else:
        months = [f"{args.year}-{m:02d}" for m in range(12, 0, -1)]

    if args.enrich_only:
        n = enrich_year(args.year, sorted(months), force=args.force_enrich)
        print(f"\n补价更新约 {n} 条")
        print("清理开盘涨停…", len(purge_open_limit_buys("stock")))
        print("重算盈亏…", recalculate_all_trades("stock"))
        return

    total = 0
    for ym in months:
        total += backfill_month_records(ym, source=args.source, workers=args.workers)
        if not args.records_only:
            enrich_month(ym, force=args.force_enrich)

    if args.records_only:
        print(f"\n明细写入完成，共约 {total} 条候选。请再运行 --enrich-only 补价。")
        return

    print("\n清理开盘涨停…", len(purge_open_limit_buys("stock")))
    print("重算盈亏…", recalculate_all_trades("stock"))
    print(f"完成，本轮写入约 {total} 条候选")


if __name__ == "__main__":
    main()
