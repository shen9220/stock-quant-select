#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock V2 · 历史集合竞价数据源验证（对照 需求/历史数据回溯.md）

铁律：
  - 禁止未实测标注可用
  - 禁止用 9:30 后续行情冒充 9:25
  - 多平台：同一时刻每平台仅 1 请求；平台间可串行切换（本脚本默认全局串行，更稳）
  - 缺失不得填 0 参与评分

用法：
  python3 auction_source_test.py
  python3 auction_source_test.py --dates 2026-10-27,2026-10-20 --codes 000001,600000,300750
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHOU = os.path.dirname(os.path.dirname(_HERE))  # 首板战法
_PUBLIC = os.path.join(_SHOU, "公共")
_UI = os.path.join(_SHOU, "交易记录UI")
_UI_LIB = os.path.join(_UI, "lib")
for p in (_HERE, _PUBLIC, _SHOU, _UI, _UI_LIB):
    if p not in sys.path:
        sys.path.insert(0, p)

for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("DISABLE_BAOSTOCK", "1")

OUT_DIR = os.path.join(_HERE, "auction_probe")
os.makedirs(OUT_DIR, exist_ok=True)

# 全局串行：任意源请求间隔
_GLOBAL_GAP = 0.8
_last_call = 0.0


def _throttle():
    global _last_call
    left = _GLOBAL_GAP - (time.time() - _last_call)
    if left > 0:
        time.sleep(left)
    _last_call = time.time()


def _http_get(url: str, params: Optional[dict] = None, timeout: float = 12) -> Optional[dict | str]:
    import requests
    _throttle()
    sess = requests.Session()
    sess.trust_env = False
    try:
        r = sess.get(url, params=params or {}, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
        })
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "")
        if "json" in ct or r.text[:1] in "{[":
            return r.json()
        return r.text
    except Exception as e:
        return {"_error": str(e)}


def probe_em_trends2(code: str, date: str) -> dict[str, Any]:
    """东财 trends2（ndays）— 实测是否含 09:25。"""
    code = code.zfill(6)
    secid = f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"
    out = {"source": "em_trends2", "code": code, "date": date, "ok": False}
    j = _http_get(
        "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
        dict(
            fields1="f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            fields2="f51,f52,f53,f54,f55,f56,f57,f58",
            ut="7eea3edcaed734bea9cbfc24409ed989",
            ndays="5", iscr="0", secid=secid,
        ),
    )
    if not isinstance(j, dict) or j.get("_error"):
        out["error"] = (j or {}).get("_error") if isinstance(j, dict) else "empty"
        return out
    trends = ((j.get("data") or {}).get("trends")) or []
    hits = [t for t in trends if date.replace("-", "")[:8] in str(t).replace("-", "") and "09:25" in str(t)]
    # date in trends is YYYY-MM-DD HH:MM
    hits = [t for t in trends if date in str(t) and "09:25" in str(t)]
    out["rows_total"] = len(trends)
    out["has_0925"] = bool(hits)
    out["sample"] = hits[:2] or ([trends[0]] if trends else [])
    out["ok"] = False  # 无 09:25 不能当竞价源
    out["note"] = "无09:25行（通常从09:30起）" if not hits else "含09:25需人工核验单位"
    if hits:
        out["ok"] = True
    return out


def probe_ak_pre_min(code: str, date: str) -> dict[str, Any]:
    """AKShare stock_zh_a_hist_pre_min_em — 通常仅当日。"""
    out = {"source": "ak_pre_min_em", "code": code.zfill(6), "date": date, "ok": False}
    try:
        import akshare as ak
        _throttle()
        df = ak.stock_zh_a_hist_pre_min_em(symbol=code.zfill(6))
        if df is None or getattr(df, "empty", True):
            out["error"] = "empty"
            return out
        out["cols"] = list(df.columns)
        out["n"] = len(df)
        # 检查日期是否等于请求日
        times = df["时间"].astype(str) if "时间" in df.columns else None
        if times is not None:
            out["time_head"] = times.head(3).tolist()
            hit = df[times.str.contains("09:25|09:26", regex=True)]
            out["has_0925_or_26"] = len(hit) > 0
            out["sample"] = hit.head(2).astype(str).to_dict("records") if len(hit) else []
            # 历史日：时间戳日期是否匹配
            day_ok = any(date in str(t) or date.replace("-", "") in str(t).replace("-", "") for t in times.head(5))
            out["date_match_request"] = day_ok
            out["ok"] = bool(out["has_0925_or_26"] and day_ok)
            out["note"] = "仅当返回日=请求日才可用于该日；历史日通常不可用"
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def probe_mootdx_tick_0925(code: str, date: str) -> dict[str, Any]:
    """
    mootdx/pytdx 历史分笔：找 time=09:25 撮合行。
    vol 单位：手；amount ≈ price * vol * 100（元）。
    """
    out = {"source": "mootdx_history_tick", "code": code.zfill(6), "date": date, "ok": False}
    d8 = date.replace("-", "")
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std", multithread=False, heartbeat=True)
        hit_rows = []
        scanned = 0
        for start in range(0, 24000, 800):
            _throttle()
            df = client.transactions(symbol=code.zfill(6), start=start, offset=800, date=d8)
            if df is None or len(df) == 0:
                break
            scanned += len(df)
            hit = df[df["time"].astype(str).str.startswith("09:25")]
            if len(hit):
                hit_rows = hit.to_dict("records")
                break
            # 已翻到 09:15 一带仍无 09:25 → 可能停牌/无撮合
            if any(str(t).startswith("09:15") for t in df["time"].astype(str).tolist()):
                # 再看本页是否有 09:25
                break
        out["scanned_ticks"] = scanned
        if not hit_rows:
            out["error"] = "no_09:25_tick"
            out["note"] = "已翻历史分笔未见 09:25（可能停牌或源缺）"
            return out
        # 取 09:25 最后一笔（撮合）
        row = hit_rows[-1]
        price = float(row.get("price") or 0)
        vol_hand = float(row.get("vol") or row.get("volume") or 0)
        amount = price * vol_hand * 100.0
        out.update({
            "ok": price > 0 and vol_hand > 0,
            "auction_time": "09:25:00",
            "price": price,
            "volume_hands": vol_hand,
            "volume_shares": vol_hand * 100.0,
            "amount_yuan": round(amount, 2),
            "buyorsell": row.get("buyorsell"),
            "raw": row,
            "unit_note": "vol=手; amount=price*vol*100 推算",
        })
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def probe_tencent_realtime(code: str, date: str) -> dict[str, Any]:
    """腾讯 qt：仅实时今开，不能指定历史日。"""
    out = {"source": "tencent_qt", "code": code.zfill(6), "date": date, "ok": False,
           "note": "仅当日实时；不能查历史日"}
    today = datetime.now().strftime("%Y-%m-%d")
    if date != today:
        out["error"] = "not_today"
        return out
    c = code.zfill(6)
    sym = f"sh{c}" if c.startswith(("5", "6", "9")) else f"sz{c}"
    text = _http_get(f"http://qt.gtimg.cn/q={sym}")
    if not isinstance(text, str) or '="' not in text:
        out["error"] = "bad_response"
        return out
    try:
        payload = text.split('="', 1)[1].rstrip('";')
        parts = payload.split("~")
        pre, open_p = float(parts[4] or 0), float(parts[5] or 0)
        vol_hand = float(parts[6] or 0)
        amt_wan = float(parts[37] or 0) if len(parts) > 37 else 0
        out.update({
            "ok": open_p > 0 and pre > 0,
            "price": open_p,
            "pre_close": pre,
            "volume_hands": vol_hand,
            "amount_yuan": amt_wan * 10000.0 if amt_wan else open_p * vol_hand * 100,
            "note": "当日今开≈竞价价；量额为截至拉取时刻累计，9:25后短暂窗口近似竞价",
        })
    except Exception as e:
        out["error"] = str(e)
    return out


def default_codes() -> list[str]:
    """从 V1 近几日推荐抽样本，不足则补主板/创业板对照码。"""
    codes: list[str] = []
    res = os.path.join(_SHOU, "stock", "results")
    for fname in ("20261027_recommendations.md", "20261026_recommendations.md", "20261023_recommendations.md"):
        path = os.path.join(res, fname)
        if not os.path.isfile(path):
            continue
        import re
        text = open(path, encoding="utf-8").read()
        for m in re.finditer(r"\b(\d{6})\b", text):
            c = m.group(1)
            if c not in codes and (c.startswith(("00", "60", "30"))):
                codes.append(c)
            if len(codes) >= 8:
                break
        if len(codes) >= 8:
            break
    for c in ("000001", "600000", "600519", "000858", "300750", "301236", "002594", "300308"):
        if c not in codes:
            codes.append(c)
    return codes[:12]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="2026-10-27,2026-10-20,2026-09-28,2026-08-24")
    ap.add_argument("--codes", default="")
    args = ap.parse_args()
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()] or default_codes()

    probes = [
        ("em_trends2", probe_em_trends2),
        ("ak_pre_min_em", probe_ak_pre_min),
        ("mootdx_history_tick", probe_mootdx_tick_0925),
        ("tencent_qt", probe_tencent_realtime),
    ]

    rows: list[dict] = []
    print(f"dates={dates} codes={len(codes)} serial_gap={_GLOBAL_GAP}s", flush=True)
    for date in dates:
        for code in codes:
            for name, fn in probes:
                print(f"→ {name} {date} {code}", flush=True)
                try:
                    r = fn(code, date)
                except Exception as e:
                    r = {"source": name, "code": code, "date": date, "ok": False, "error": str(e)}
                rows.append(r)
                print(f"  ok={r.get('ok')} err={r.get('error')} note={r.get('note')}", flush=True)

    sample_path = os.path.join(OUT_DIR, "auction_sample.csv")
    val_path = os.path.join(OUT_DIR, "auction_validation.csv")
    keys = sorted({k for r in rows for k in r.keys()} - {"raw", "sample", "cols"})
    with open(sample_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {k: r.get(k) for k in keys}
            w.writerow(flat)

    # 跨源：同一 code+date 上 mootdx vs 其它有价源
    with open(val_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "code", "src_a", "price_a", "vol_a", "src_b", "price_b", "vol_b", "price_diff_pct"])
        by = {}
        for r in rows:
            if r.get("ok") and r.get("price"):
                by.setdefault((r["date"], r["code"]), []).append(r)
        for (date, code), lst in by.items():
            if len(lst) < 2:
                continue
            a, b = lst[0], lst[1]
            pa, pb = float(a["price"]), float(b["price"])
            diff = abs(pa - pb) / pa * 100 if pa else None
            w.writerow([date, code, a["source"], pa, a.get("volume_hands"), b["source"], pb, b.get("volume_hands"), diff])

    # 报告
    report = os.path.join(_HERE, "auction_source_report.md")
    by_src: dict[str, list] = {}
    for r in rows:
        by_src.setdefault(r.get("source") or "?", []).append(r)

    lines = [
        "# Stock V2 历史集合竞价数据源报告",
        "",
        f"> 生成: {datetime.now().isoformat(timespec='seconds')}  · 实测脚本 `auction_source_test.py`",
        "",
        "## 结论（先看）",
        "",
    ]
    # summarize
    mootdx_ok = sum(1 for r in by_src.get("mootdx_history_tick", []) if r.get("ok"))
    mootdx_n = len(by_src.get("mootdx_history_tick", []))
    em_ok = sum(1 for r in by_src.get("em_trends2", []) if r.get("ok"))
    ak_ok = sum(1 for r in by_src.get("ak_pre_min_em", []) if r.get("ok"))
    lines += [
        f"- **mootdx/通达信历史分笔 09:25**：实测可用 {mootdx_ok}/{mootdx_n}（价+量手；额=价×量×100 推算）→ **推荐作历史回测主源**",
        f"- **东财 trends2**：含 09:25 = {em_ok}/{len(by_src.get('em_trends2', []))}（通常从 09:30 起，**不能**当竞价源）",
        f"- **AKShare pre_min_em**：请求日匹配且有 09:25/26 = {ak_ok}/{len(by_src.get('ak_pre_min_em', []))}（多为**仅当日**）",
        "- **腾讯 qt**：仅当日实时，不能查历史日",
        "- **Tushare stk_auction**：文档称可历史但需单独付费权限，本机未测（禁止未实测标可用）",
        "- **eltdx get_auction_0925**：本机未安装，未测",
        "",
        "## 平台并发铁律",
        "",
        "- 同一平台同时仅 1 请求；本探针全局串行（间隔 ≥0.8s）",
        "- 与 `交易记录UI/lib/platform_gate.py` 一致：到配额硬停，宁停勿撞",
        "",
        "## 实时选股（D1 9:25）",
        "",
        "- 主源：腾讯 qt 今开+量额（水下同法）；辅：东财/通达信当日分笔",
        "",
        "## 原始结果",
        "",
        f"- 样本 CSV: `auction_probe/auction_sample.csv`",
        f"- 跨源 CSV: `auction_probe/auction_validation.csv`",
        "",
        "## 分源明细摘要",
        "",
    ]
    for src, lst in by_src.items():
        ok = sum(1 for r in lst if r.get("ok"))
        lines.append(f"### {src}: {ok}/{len(lst)} ok")
        for r in lst[:6]:
            lines.append(
                f"- {r.get('date')} {r.get('code')} ok={r.get('ok')} "
                f"price={r.get('price')} vol_hand={r.get('volume_hands')} err={r.get('error')}"
            )
        lines.append("")

    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {report}\n{sample_path}\n{val_path}", flush=True)


if __name__ == "__main__":
    main()
