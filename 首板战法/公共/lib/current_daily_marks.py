"""Dated current-day valuation quotes. Never fabricate 09:35/13:35 fills.

Fetches run outside API replay locks, single symbol at a time. Only a quote
whose embedded date matches the requested current session is persisted.
"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import threading
import time

_LOCK = threading.RLock()
_QUEUE = {}
_ACTIVE = set()
_LAST = {}
_WORKER = None
_CACHE = {}


def parse_quote(text, code, day):
    if '=\"' not in text:
        return None
    parts = text.split('=\"', 1)[1].split('\"', 1)[0].split(',')
    if len(parts) < 32 or parts[30] != day:
        return None
    try:
        values = {k: float(parts[i]) for k, i in [('open',1),('pre_close',2),('last',3),('high',4),('low',5),('volume',8)]}
    except (TypeError, ValueError):
        return None
    if values['pre_close'] <= 0 or not parts[31] or values['last'] < 0:
        return None
    values.update(code=str(code).zfill(6), date=day, quote_time=day+'T'+parts[31], name=parts[0],
                  is_st='ST' in parts[0].upper(), source='sina_dated_daily')
    if parts[31] >= '15:00:00' and values['last'] > 0:
        values['close'] = values['last']
    if values['open'] == 0 and values['volume'] == 0:
        values['suspended'] = True
    return values


def read_mark(base, code, day):
    path = Path(base) / 'data/v3_daily_marks.json'
    try:
        signature = path.stat().st_mtime_ns, path.stat().st_size
    except OSError:
        return {}
    with _LOCK:
        old = _CACHE.get(str(path))
        if not old or old[0] != signature:
            try:
                data = json.loads(path.read_text(encoding='utf-8')).get('quotes', {})
            except (ValueError, OSError):
                return {}
            _CACHE[str(path)] = signature, data
        else:
            data = old[1]
        row = (data.get(str(code).zfill(6)) or {}).get(day) or {}
        return dict(row) if row.get('date') == day and str(row.get('quote_time','')).startswith(day+'T') else {}


def _worker():
    global _WORKER
    import requests
    while True:
        with _LOCK:
            if not _QUEUE:
                _WORKER = None
                return
            key = next(iter(_QUEUE)); base, code, day = _QUEUE.pop(key); _ACTIVE.add(key)
        try:
            prefix = 'sh' if code.startswith(('6','9')) else 'bj' if code.startswith(('4','8')) else 'sz'
            response = requests.get('https://hq.sinajs.cn/list='+prefix+code,
                headers={'Referer':'https://finance.sina.com.cn','User-Agent':'Mozilla/5.0'}, timeout=8)
            response.raise_for_status()
            row = parse_quote(response.text, code, day)
            if row:
                path = Path(base) / 'data/v3_daily_marks.json'
                with _LOCK:
                    try:
                        data = json.loads(path.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        data = {'schema':1,'quotes':{}}
                    data['quotes'].setdefault(code,{})[day] = row
                    path.parent.mkdir(parents=True,exist_ok=True)
                    temporary = path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                    temporary.replace(path)
        except Exception:
            pass
        finally:
            with _LOCK:
                _ACTIVE.discard(key); _LAST[key] = time.monotonic()
        time.sleep(.5)


def request_refresh(base, codes, day):
    global _WORKER
    if day != datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'):
        return False
    base = str(base)
    with _LOCK:
        for raw in codes:
            code = str(raw).strip().zfill(6)
            if not code.isdigit() or len(code) != 6 or code == '000000':
                continue
            key = base, code, day
            if key in _ACTIVE or key in _QUEUE or time.monotonic() - _LAST.get(key, -1e9) < 60:
                continue
            # A final session mark is immutable; don't repeatedly request it.
            known = read_mark(base, code, day)
            if known.get('close') and str(known.get('quote_time',''))[11:] >= '15:00:00':
                continue
            _QUEUE[key] = base, code, day
        if _QUEUE and _WORKER is None:
            _WORKER = threading.Thread(target=_worker,daemon=True,name='v3-current-daily-marks')
            _WORKER.start()
        return any((base,str(code).zfill(6),day) in _ACTIVE or (base,str(code).zfill(6),day) in _QUEUE for code in codes)
