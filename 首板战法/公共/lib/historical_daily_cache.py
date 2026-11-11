"""Read-only dated history fallback, shared by first-board checks and V3 ledger.

Never turns an absent day into a suspension. Previous close requires the exact
previous exchange session. Missing OHLC fields are not synthesized from close.
"""
from pathlib import Path
from bisect import bisect_left
import json
import threading

_CACHE = {}
_LOCK = threading.RLock()


def _load(base):
    base = Path(base) if base else Path(__file__).resolve().parents[2] / '交易记录UI'
    prices = base / 'data/v3_daily_history.json'
    calendar = base / 'data/trade_calendar.json'
    try:
        signature = (prices.stat().st_mtime_ns, prices.stat().st_size, calendar.stat().st_mtime_ns)
    except OSError:
        return {}, []
    with _LOCK:
        cached = _CACHE.get(str(base))
        if cached and cached[0] == signature:
            return cached[1], cached[2]
        try:
            series = json.loads(prices.read_text(encoding='utf-8'))['series']
            days = sorted(json.loads(calendar.read_text(encoding='utf-8'))['trade_dates'])
        except (OSError, ValueError, KeyError):
            return {}, []
        _CACHE[str(base)] = signature, series, days
        return series, days


def _positive(value):
    try:
        v = float(value)
        return v if v > 0 and v < float('inf') else None
    except (ValueError, TypeError):
        return None


def merge_history(code, day, primary=None, base=None, recorded_open=None):
    code = str(code).zfill(6)
    result = dict(primary or {})
    fields = {field: 'free_daily' for field in ('open', 'close', 'pre_close', 'high', 'low') if _positive(result.get(field)) is not None}
    if any(_positive(result.get(f)) is None for f in ('open', 'close', 'pre_close', 'high', 'low')):
        series, calendar = _load(base)
        values = series.get(code) or {}
        bar = values.get(day) or {}
        for field, short in [('close', 'c'), ('open', 'o'), ('high', 'h'), ('low', 'l')]:
            value = _positive(bar.get(short))
            if _positive(result.get(field)) is None and value is not None:
                result[field] = value; fields[field] = bar.get('source') or 'historical_daily_cache'
        if _positive(result.get('pre_close')) is None and day in calendar:
            i = bisect_left(calendar, day)
            previous = calendar[i - 1] if i else None
            value = _positive((values.get(previous) or {}).get('c'))
            if value is not None:
                result['pre_close'] = value; fields['pre_close'] = 'historical_daily_cache_exact_previous_session'
    value = _positive(recorded_open)
    if _positive(result.get('open')) is None and value is not None and _positive(result.get('close')) is not None:
        result['open'] = value; fields['open'] = 'recorded_dated_open'
    if fields:
        result['quote_sources'] = fields
    # A close-only archive cannot prove historical ST state for 10% boards.
    # Keep uncertainty rather than silently apply the wrong 5%/10% band.
    if _positive(result.get('close')) is not None and 'is_st' not in result and not code.startswith(('300','301','688','689','4','8','92')):
        result['limit_rule_unknown'] = True
    return result
