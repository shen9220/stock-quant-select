# -*- coding: utf-8 -*-
"""每日交易记录包内统一路径。

目录约定（铁律）：
- 包根只暴露主运行程序 stock_UI.py
- 支撑模块 → lib/
- 作业/CLI → jobs/
- 文档 → 文档/ 与 迭代记录/
"""
from __future__ import annotations

import os
import sys

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(LIB_DIR)  # 每日交易记录
ROOT_DIR = os.path.dirname(BASE_DIR)  # 首板战法
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
DATA_DIR = os.path.join(BASE_DIR, "data")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
DOCS_DIR = os.path.join(BASE_DIR, "文档")


def ensure_sys_path() -> str:
    """把包根 / lib / jobs / 首板战法根 加入 sys.path，返回 BASE_DIR。"""
    extras = (
        BASE_DIR,
        LIB_DIR,
        JOBS_DIR,
        ROOT_DIR,
        os.path.join(ROOT_DIR, "up", "scripts"),
        os.path.join(ROOT_DIR, "stock", "scripts"),
    )
    for p in extras:
        if p and p not in sys.path:
            sys.path.insert(0, p)
    return BASE_DIR
