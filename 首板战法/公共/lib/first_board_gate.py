"""Uniform first-board gate: limit day=D0-1; previous check day=D0-2.

Local free only. Unknown is not first. DataFrame callers gate before ranking
and writing results; raw market caches may retain rejected observations.
"""
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
import sys
import time


def _number(value):
    try:
        v = Decimal(str(value))
        return v if v.is_finite() else None
    except Exception:
        return None


@lru_cache(maxsize=65536)
def _bar(code, day, generation):
    primary = {}
    try:
        sdk = Path(__file__).resolve().parents[2] / '数据缓存/free-stockdb/stockdb/pybao'
        if str(sdk) not in sys.path:
            sys.path.insert(0, str(sdk))
        from stock_sdk import init, rd
        init('127.0.0.1', 7899, warm=False)
        got = rd.get('日k', code, day.replace('-', '')).do()
        primary = dict(got) if isinstance(got, dict) else {}
    except Exception:
        pass
    from historical_daily_cache import merge_history
    return merge_history(code, day, primary)


def is_limit_up(code, bar):
    if bar.get('limit_rule_unknown'):
        return None
    close, prev = _number(bar.get('close')), _number(bar.get('pre_close'))
    if close is None or prev is None or prev <= 0:
        return None
    ratio = Decimal('.3') if code.startswith(('4', '8', '92')) else Decimal('.2') if code.startswith(('300', '301', '688', '689')) else Decimal('.05') if bar.get('is_st') else Decimal('.1')
    price = (prev * (1 + ratio)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return close >= price


def classify(code, limit_day, check_day, reader=None):
    code = str(code).zfill(6)
    if not limit_day or not check_day:
        return 'unknown'
    get = reader or (lambda c, d: _bar(c, d, int(time.time() // 45)))
    previous = is_limit_up(code, get(code, check_day))
    if previous is True:
        return 'lianban'
    current = is_limit_up(code, get(code, limit_day))
    if current is False:
        return 'not_limit'
    return 'first' if current is True and previous is False else 'unknown'


def keep_first_board(frame, limit_day, check_day):
    if frame is None or frame.empty:
        return frame
    def iso(day):
        text = day.strftime('%Y-%m-%d') if hasattr(day, 'strftime') else str(day)
        return text if '-' in text else text[:4] + '-' + text[4:6] + '-' + text[6:8]
    limit_day, check_day = iso(limit_day), iso(check_day)
    return frame.loc[[classify(str(row.get('代码') or row.get('code') or '').zfill(6), limit_day, check_day) == 'first' for _, row in frame.iterrows()]].copy()
