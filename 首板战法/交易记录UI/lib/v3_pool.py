"""First-board-only read-only pool view. Raw market observations stay archived."""
from pathlib import Path
import ast
import json
import threading
import requests

_TAGS = {}
_RUNNING = False
_LOCK = threading.Lock()


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _repo(base):
    for candidate in (base.parents[1] / 'stock-quant-select', base.parents[1]):
        if (candidate / '09-新模式').exists():
            return candidate
    return base.parents[1]


def _catalog(base):
    directory = _repo(base) / '09-新模式/涨停回头低吸/data'
    result = dict(_TAGS)
    for name in ('limitup', 'watch', 'observation', 'position'):
        data = _read(directory / (name + '.json')) or {}
        for row in data.get('items', []) if isinstance(data, dict) else []:
            code = str(row.get('stock_code', '')).zfill(6)
            result.setdefault(code, {}).update({k: row[k] for k in ('concept_board', 'industry_board') if row.get(k)})
    return result


def _enrich(base, codes, ps):
    global _RUNNING
    try:
        # Load the existing function verbatim, avoiding imports of the scanner/rules
        # and their side effects. No alternate taxonomy is introduced.
        path = _repo(base) / '09-新模式/涨停回头低吸/services/market.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fetch_boards')
        scope = {'ps': ps}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), scope)
        for code in codes:
            values = scope['fetch_boards'](code)
            with _LOCK:
                _TAGS[code] = values
            target = base / 'data/v3_pool_tags.json'
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(_TAGS, ensure_ascii=False), encoding='utf-8')
            temporary.replace(target)
    finally:
        _RUNNING = False


def pool_view(namespace, date=''):
    global _RUNNING
    base = Path(namespace['BASE_DIR'])
    directory = base / 'data/zt_pool_cache'
    calendar = namespace['ps'].get_trade_calendar()
    from zoneinfo import ZoneInfo
    today = namespace['datetime'].now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
    days = sorted(p.stem for p in directory.glob('????????.json'))
    if not date:
        # Require completed daily data; default prior available session.
        eligible = [d for d in days if d < today.replace('-', '')]
        date = eligible[-1] if eligible else today.replace('-', '')
        date = date[:4] + '-' + date[4:6] + '-' + date[6:]
    day8 = date.replace('-', '')
    cached = _read(directory / (day8 + '.json'))
    if calendar is not None and date not in set(calendar['trade_date'].dt.strftime('%Y-%m-%d')):
        return {'date': date, 'items': [], 'note': '该日非交易日。'}
    # Preserve all rows, codes and ST names returned by provider, unlike strategy helpers.
    full_dir = base / 'data/v3_full_pool'
    full_dir.mkdir(exist_ok=True)
    raw = _read(full_dir / (day8 + '.json'))
    source = '东财完整缓存'
    note = ''
    if raw is None:
        raw = list(cached or [])
        try:
            info, page = [], 1
            while page <= 20:
                response = requests.get('https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool', params={'page': page, 'limit': 200, 'date': day8}, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.10jqka.com.cn/'}, timeout=8)
                response.raise_for_status()
                payload = response.json()
                if payload.get('status_code') != 0:
                    raise ValueError(payload.get('status_msg') or '涨停池响应异常')
                rows = (payload.get('data') or {}).get('info') or []
                info.extend(rows)
                if len(rows) < 200:
                    break
                page += 1
            if info:
                known = {str(r.get('代码', '')).zfill(6): r for r in raw}
                for r in info:
                    code = str(r.get('code', '')).zfill(6)
                    row = known.setdefault(code, {'代码': code, '名称': r.get('name')})
                    row['_provider_all'] = True
                    row.setdefault('连板数', r.get('high_days_value'))
                    # Accept only explicitly dated source metrics, never live quote fields.
                    for key in ('auction_volume', 'close_volume_ratio'):
                        if r.get(key) is not None:
                            row[key] = r[key]
                raw = list(known.values())
                (full_dir / (day8 + '.json')).write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
            elif not cached:
                note = '数据源该日未提供涨停记录。'
        except Exception as error:
            note = '全市场补充源不可用；当前仅显示已有缓存，不承诺完整覆盖。'
    persisted = _read(base / 'data/v3_pool_tags.json') or {}
    _TAGS.update(persisted)
    tags = _catalog(base)
    next_day = None
    if calendar is not None:
        future = calendar[calendar['trade_date'].dt.strftime('%Y-%m-%d') > date]
        if not future.empty:
            next_day = future.iloc[0]['trade_date'].strftime('%Y-%m-%d')
    from first_board_gate import classify
    dates = sorted(calendar['trade_date'].dt.strftime('%Y-%m-%d')) if calendar is not None else []
    previous = [d for d in dates if d < date]
    check_day = previous[-1] if previous else None
    items = []
    rejected = unknown = 0
    for index, row in enumerate(raw):
        code = str(row.get('代码') or row.get('stock_code') or '').zfill(6)
        status = classify(code, date, check_day)
        if status != 'first':
            unknown += status == 'unknown'
            rejected += status != 'unknown'
            continue
        metadata = tags.get(code, {})
        items.append({'id': index, 'date': date, 'd1': next_day, 'stock_code': code, 'stock_name': row.get('名称') or row.get('stock_name'), 'board': namespace['stock_meta'].board_of_code(code), 'd0_pct': row.get('涨跌幅'), 'turnover': row.get('换手率'), 'lianban': 1, 'raw_lianban': row.get('连板数'), 'industry_board': metadata.get('industry_board') or row.get('所属行业'), 'concept_board': metadata.get('concept_board'), 'auction_volume': row.get('auction_volume'), 'close_volume_ratio': row.get('close_volume_ratio'), 'src': '涨停全池缓存' if row.get('_provider_all') else source})
    missing = [r['stock_code'] for r in items if r['stock_code'] not in _TAGS and not tags.get(r['stock_code'], {}).get('concept_board')]
    if missing and not _RUNNING:
        _RUNNING = True
        threading.Thread(target=_enrich, args=(base, missing, namespace['ps']), daemon=True).start()
    note = (note + f' 首板校验：剔除连板/非涨停{rejected}只；待核实{unknown}只未放行。').strip()
    return {'date': date, 'items': items, 'enriching': _RUNNING, 'note': note, 'rejected': rejected, 'unknown': unknown}
