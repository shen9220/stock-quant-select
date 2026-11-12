"""
首板战法路径（权威包：工作区根 /Users/admin/stock_quant_select/首板战法）

根目录只保留：README.md + 规范文档/ + 交易记录UI/ + 公共/ + 各模型目录。
程序 id（stock / stock_updata / …）保持稳定，物理目录按下表映射。
"""
from __future__ import annotations

import os

# 本文件在 公共/ → ROOT = 首板战法包根
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_UI_DIR = os.path.join(ROOT, "交易记录UI")
NORMS_DIR = os.path.join(ROOT, "规范文档")
SHARED_FETCH_DIR = os.path.join(NORMS_DIR, "共享取数")
SHARED_FETCH_LIB = os.path.join(SHARED_FETCH_DIR, "lib")

# program id → 相对 ROOT 的物理目录
PROGRAM_REL_DIRS: dict[str, str] = {
    "stock": os.path.join("stock-V1", "stock-V1（默认原）"),
    "stock_updata": "Stock-V1（水下）",
    "slow": "Stock（低价）",
    "up": "up",
    "stock_v2": os.path.join("stock-V2", "Stock-V2（一进二）"),
}

STOCK_V2_ROOT = os.path.join(ROOT, PROGRAM_REL_DIRS["stock_v2"])
PROGRAMS = tuple(PROGRAM_REL_DIRS.keys())

# 只给看板展示。不放进 PROGRAMS，收盘补全和通知不会去跑它。
DISPLAY_ONLY_DIRS: dict[str, str] = {
    "llm_input": "大模型输入",
}

# Pages / git 仓库根（docs/trade-ui 仍在代码仓内）
CODE_REPO_ROOT = os.path.abspath(os.path.join(ROOT, "..", "stock-quant-select"))

_DIR_READY: set[str] = set()


def _ensure_dir(path: str) -> str:
    if path not in _DIR_READY:
        os.makedirs(path, exist_ok=True)
        _DIR_READY.add(path)
    return path


def program_dir(program: str) -> str:
    rel = PROGRAM_REL_DIRS.get(program) or DISPLAY_ONLY_DIRS.get(program)
    if not rel:
        raise ValueError(f"unknown program: {program}")
    return os.path.join(ROOT, rel)


def program_script(program: str) -> str:
    if program == "stock_updata":
        return os.path.join(program_dir("stock_updata"), "stock_updata.py")
    if program == "stock_v2":
        return os.path.join(STOCK_V2_ROOT, "stock_v2.py")
    return os.path.join(program_dir(program), f"{program}.py")


def program_results_dir(program: str) -> str:
    return _ensure_dir(os.path.join(program_dir(program), "results"))


def program_trades_dir(program: str) -> str:
    return _ensure_dir(os.path.join(program_dir(program), "trades"))


def recommendation_md(program: str, d0_yyyy_mm_dd: str) -> str:
    d8 = d0_yyyy_mm_dd.replace("-", "")
    if program == "stock":
        fname = f"{d8}_recommendations.md"
    elif program == "up":
        fname = f"{d8}_up_recommendations.md"
    elif program == "stock_updata":
        fname = f"{d8}_stock_updata_recommendations.md"
    elif program == "stock_v2":
        return os.path.join(
            program_results_dir(program),
            f"{d0_yyyy_mm_dd}_recommendations.md",
        )
    else:
        fname = f"{d8}_slow_recommendations.md"
    return os.path.join(program_results_dir(program), fname)


def up_result_json_candidates() -> list[str]:
    return [
        os.path.join(program_dir("up"), "strength_limit_up_result.json"),
        os.path.join(ROOT, "strength_limit_up_result.json"),
    ]


def _extra_roots() -> list[str]:
    """仅扫描本包之外的历史残留（若仍存在）；禁止再新建代码仓内「首板战法」。"""
    roots: list[str] = []
    for p in (
        os.path.expanduser("~/stock_quant_select/首板战法"),
    ):
        if os.path.isdir(p) and os.path.normpath(p) != os.path.normpath(ROOT):
            roots.append(p)
    return roots


def legacy_results_dirs(program: str) -> list[str]:
    seen: set[str] = set()
    dirs: list[str] = []

    def _add(d: str) -> None:
        nd = os.path.normpath(d)
        if nd not in seen and os.path.isdir(nd):
            seen.add(nd)
            dirs.append(d)

    _add(program_results_dir(program))
    _add(os.path.join(ROOT, "results"))
    for root in _extra_roots():
        rel = PROGRAM_REL_DIRS.get(program, program)
        _add(os.path.join(root, rel, "results"))
        _add(os.path.join(root, program, "results"))
        _add(os.path.join(root, "results"))
    return dirs
