# -*- coding: utf-8 -*-
"""交易记录UI 包内统一路径。

目录约定：
- 包根只暴露主运行程序 stock_UI.py
- 支撑模块 → lib/
- 作业/CLI → jobs/
- 文档 → 文档/ 与 迭代记录/
"""
from __future__ import annotations

import os
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(LIB_DIR)  # 交易记录UI
ROOT_DIR = os.path.dirname(BASE_DIR)  # 首板战法
PUBLIC_DIR = os.path.join(ROOT_DIR, "公共")
NORMS_DIR = os.path.join(ROOT_DIR, "规范文档")
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
DATA_DIR = os.path.join(BASE_DIR, "data")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
DOCS_DIR = os.path.join(BASE_DIR, "文档")


def ensure_sys_path() -> str:
    """把包根 / lib / jobs / 首板战法根 / 公共 / 各模型目录 加入 sys.path。"""
    stock_v1_dir = os.path.join(ROOT_DIR, "stock-V1", "stock-V1（默认原）")
    extras = (
        BASE_DIR,
        LIB_DIR,
        JOBS_DIR,
        ROOT_DIR,
        PUBLIC_DIR,
        stock_v1_dir,  # import stock（stock.py）
        os.path.join(stock_v1_dir, "scripts"),
        os.path.join(ROOT_DIR, "Stock-V1（水下）"),
        os.path.join(ROOT_DIR, "Stock（低价）"),
        os.path.join(ROOT_DIR, "up"),
        os.path.join(ROOT_DIR, "up", "scripts"),
        os.path.join(ROOT_DIR, "stock-V2", "Stock-V2（一进二）"),
        os.path.join(NORMS_DIR, "共享取数", "lib"),
    )
    for p in extras:
        if p and p not in sys.path:
            sys.path.insert(0, p)
    return BASE_DIR
