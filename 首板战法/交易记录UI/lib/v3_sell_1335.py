"""Overlay dated 13:35 sidecar quotes without rewriting legacy 13:15 prices."""
from pathlib import Path
import json
import threading

_CACHE = {}
_LOCK = threading.RLock()


def _key(row, program):
    return (program, str(row.get('id')), str(row.get('buy_date') or row.get('date') or ''), str(row.get('stock_code') or '').zfill(6))


def _load(base):
    directory = Path(base) / 'data/sell_1335'
    files = sorted(p for p in directory.glob('*.json') if not p.name.startswith('_'))
    signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    with _LOCK:
        old = _CACHE.get(str(directory))
        if old and old[0] == signature:
            return old[1:]
        exact, quotes, conflicts = {}, {}, set()
        for path in files:
            rows = json.loads(path.read_text(encoding='utf-8'))
            for row in rows:
                program = row.get('program') or path.stem
                key = _key(row, program)
                if key in exact:
                    raise ValueError(f'Duplicate 13:35 identity in {path.name}: {row.get("id")}')
                exact[key] = row
                quote_key = (key[3], str(row.get('sell_date') or ''))
                price = row.get('sell_price_1335')
                if price is not None:
                    if quote_key in quotes and quotes[quote_key] != price:
                        conflicts.add(quote_key)
                    quotes[quote_key] = price
        for key in conflicts:
            quotes.pop(key, None)
        _CACHE[str(directory)] = (signature, exact, quotes)
        return exact, quotes


def attach_1335(rows, program, base):
    exact, quotes = _load(base)
    result = []
    for original in rows:
        row = dict(original)
        source = exact.get(_key(row, program))
        if source is not None:
            # A source null is authoritative: never fall back to the old 13:15 quote.
            row['sell_price_1335'] = source.get('sell_price_1335')
            row['sell_date_1335'] = source.get('sell_date')
            row['sell_price_source_1335'] = source.get('source') or 'sell_1335_missing'
        else:
            row.setdefault('sell_price_1335', None)
        # D2 must use its own actual afternoon execution day, not D1's day.
        actual_d2_day = row.get('d2_sell_date_1335') or row.get('d2_sell_date_1315')
        if row.get('d2_sell_price_1335') is None:
            row['d2_sell_price_1335'] = quotes.get((str(row.get('stock_code') or '').zfill(6), str(actual_d2_day or '')))
            row['d2_sell_date_1335'] = actual_d2_day
            row['d2_sell_price_source_1335'] = 'sell_1335_exact_day' if row['d2_sell_price_1335'] is not None else 'sell_1335_missing'
        result.append(row)
    return result


def save_manual_1335(base, program, trade, submitted):
    """Only an explicit successful user edit may update the authoritative sidecar."""
    if 'sell_price_1335' not in submitted:
        return
    path = Path(base) / 'data/sell_1335' / (program + '.json')
    with _LOCK:
        rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        key = _key(trade, program)
        row = next((r for r in rows if _key(r, program) == key), None)
        if row is None:
            row = {'program': program, 'id': trade['id'], 'buy_date': trade['date'], 'stock_code': trade['stock_code'], 'stock_name': trade.get('stock_name'), 'sell_date': trade.get('sell_date_1335') or trade.get('sell_date')}
            rows.append(row)
        value = submitted['sell_price_1335']
        row['sell_price_1335'] = None if value in (None, '') else float(value)
        row['source'] = 'manual_1335'
        rows.sort(key=lambda r: (str(r.get('sell_date') or ''), str(r.get('buy_date') or '')), reverse=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
