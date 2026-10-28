# -*- coding: utf-8 -*-
"""导出交易 UI 静态快照到仓库 docs/trade-ui（GitHub Pages）。"""
from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from datetime import datetime
from typing import Any

from runtime_paths import ensure_sys_path

ensure_sys_path()

import paths  # noqa: E402
from pages_crypto import encrypt_payload  # noqa: E402

# 仓库根 = stock-quant-select/
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
PAGES_DIR = os.path.join(_REPO_ROOT, "docs", "trade-ui")
PAGES_DATA_DIR = os.path.join(PAGES_DIR, "data")
TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "index.html"
)
PAGES_URL = "https://shen9220.github.io/stock-quant-select/trade-ui/"

# 脱敏：数值/代码/名称等业务字段；保留 date/rank/program 等结构字段便于翻页
_MASK_EXACT = {
    "stock_code",
    "stock_name",
    "buy_price",
    "sell_price_935",
    "sell_price_1315",
    "sell_price_half_935",
    "sell_price_half_1315",
    "sell_price_batch_avg",
    "sell_date",
    "hold_days",
    "pnl_morning",
    "pnl_afternoon",
    "pnl_morning_half",
    "pnl_afternoon_half",
    "pnl_batch_morning",
    "pnl_batch_afternoon",
    "pnl_batch_total",
    "pnl_batch_total_half",
    "is_second_board",
    "skip_reason",
    "notes",
    "board",
}
_MASK_PREFIXES = ("pnl_", "sell_price", "buy_price")


def _pages_password() -> str:
    return (
        os.environ.get("TRADE_UI_PAGES_PASSWORD")
        or os.environ.get("DASHBOARD_PASSWORD")
        or "stock123"
    ).strip()


def _load_program_trades(program: str) -> list[dict]:
    trades_dir = paths.program_trades_dir(program)
    out: list[dict] = []
    if not os.path.isdir(trades_dir):
        return out
    for fname in sorted(os.listdir(trades_dir)):
        if not fname.endswith(".json"):
            continue
        day = fname[:-5]
        path = os.path.join(trades_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        for t in data:
            if not isinstance(t, dict):
                continue
            row = dict(t)
            row.setdefault("date", day)
            row.setdefault("program", program)
            out.append(row)
    out.sort(key=lambda x: (str(x.get("date") or ""), int(x.get("id") or 0)))
    return out


def build_full_snapshot() -> dict[str, Any]:
    programs: dict[str, list] = {}
    for prog in paths.PROGRAMS:
        programs[prog] = _load_program_trades(prog)
    return {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "programs": programs,
    }


def _mask_value(key: str, value: Any) -> Any:
    if value is None or value == "":
        return value
    if key == "stock_code":
        return "******"
    if key == "stock_name":
        return "***"
    if key in ("skip_reason", "notes", "board", "sell_date"):
        return "***"
    if key == "is_second_board":
        return None
    if isinstance(value, (int, float)):
        return None
    return "***"


def _should_mask_key(key: str) -> bool:
    if key in _MASK_EXACT:
        return True
    return any(key.startswith(p) for p in _MASK_PREFIXES)


def mask_snapshot(full: dict[str, Any]) -> dict[str, Any]:
    masked = deepcopy(full)
    for prog, trades in (masked.get("programs") or {}).items():
        for t in trades:
            for k in list(t.keys()):
                if _should_mask_key(k):
                    t[k] = _mask_value(k, t.get(k))
    masked["masked"] = True
    return masked


def _atomic_write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), default=str)
    os.replace(tmp, path)


def _write_index_html() -> None:
    src = os.path.abspath(TEMPLATE_PATH)
    dst = os.path.join(PAGES_DIR, "index.html")
    os.makedirs(PAGES_DIR, exist_ok=True)
    shutil.copy2(src, dst)


def _write_docs_root_helpers() -> None:
    docs = os.path.join(_REPO_ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    nojekyll = os.path.join(docs, ".nojekyll")
    if not os.path.exists(nojekyll):
        open(nojekyll, "a", encoding="utf-8").close()
    index = os.path.join(docs, "index.html")
    content = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head>"
        "<meta charset=\"UTF-8\"/>"
        "<meta http-equiv=\"refresh\" content=\"0;url=trade-ui/\"/>"
        "<title>交易数据记录</title></head>"
        "<body><a href=\"trade-ui/\">打开交易数据记录</a></body></html>\n"
    )
    with open(index, "w", encoding="utf-8") as f:
        f.write(content)


def export_pages_bundle(*, password: str | None = None) -> dict[str, Any]:
    """写出 masked.json + encrypted.json + index.html。返回摘要。"""
    pwd = (password or _pages_password()).strip()
    if not pwd:
        raise ValueError("缺少 Pages 加密口令（TRADE_UI_PAGES_PASSWORD）")

    full = build_full_snapshot()
    masked = mask_snapshot(full)
    enc = encrypt_payload(full, pwd)

    os.makedirs(PAGES_DATA_DIR, exist_ok=True)
    _atomic_write_json(os.path.join(PAGES_DATA_DIR, "masked.json"), masked)
    _atomic_write_json(os.path.join(PAGES_DATA_DIR, "encrypted.json"), enc)
    _write_index_html()
    _write_docs_root_helpers()

    counts = {k: len(v) for k, v in full["programs"].items()}
    return {
        "ok": True,
        "pages_dir": PAGES_DIR,
        "pages_url": PAGES_URL,
        "exported_at": full["exported_at"],
        "counts": counts,
        "total": sum(counts.values()),
    }
