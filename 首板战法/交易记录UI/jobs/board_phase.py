#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主板/创业板回补顺序。

用户 2026-11-02：从近到远，每个月先补完创业板，再补完主板，然后上一个月。
下界 2026-01。同一阶段里日期仍倒序。
"""
from __future__ import annotations

BOARD_PHASES = ("创业板", "主板")
FLOOR = "2026-01-01"


def trade_board(trade: dict) -> str:
    board = str(trade.get("board") or "")
    if "创业" in board:
        return "创业板"
    code = str(trade.get("stock_code") or "").strip()
    if code.startswith(("300", "301")):
        return "创业板"
    return "主板"


def sort_work(items: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """(month, board, day) → 月份倒序，月内创业板优先，日内倒序。"""

    def key(item: tuple[str, str, str]):
        month, board, day = item
        phase = 0 if board == "创业板" else 1
        return (month, -phase, day)

    return sorted(items, key=key, reverse=True)


def phase_days(items: list[tuple[str, str, str]]) -> tuple[str, str, list[str]]:
    """当前只放行第一阶段（一个月 + 一个板）的日期。"""
    ordered = sort_work(items)
    if not ordered:
        return "", "", []
    month, board, _day = ordered[0]
    days: list[str] = []
    for m, b, day in ordered:
        if m != month or b != board:
            break
        if day not in days:
            days.append(day)
    return month, board, days
