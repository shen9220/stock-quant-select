"""Local-only market adapter and isolated V3 ledger API service."""
from pathlib import Path
from bisect import bisect_left
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import re
import sys
import threading
import time

from v3_ledger import positive, at_limit
from v3_template_ledger import Replay, VERSION
from v3_period_stats import monthly_stats
from v3_scores import ResultScores, attach_scores

MODELS = [
    dict(id='original', program='stock'), dict(id='main', program='stock', board='主板'),
    dict(id='cyb', program='stock', board='创业板'), dict(id='under', program='stock_updata'),
    dict(id='low', program='slow'), dict(id='stable', program='stock_v2', path='B'),
    dict(id='advance', program='stock_v2', path='A'), dict(id='low2', program='slow_v2'),
    dict(id='up', program='up')]
_CACHE = {}
_LOCK = threading.RLock()


def belongs(row, model, rank=None):
    if model.get('board'):
        return row.get('board') == model['board'] and (rank is None or int(row.get('board_rank') or 0) == rank)
    if model.get('path'):
        return row.get('path') == model['path'] and (rank is None or int(row.get('path_rank') or 0) == rank)
    return rank is None or int(row.get('rank') or 0) == rank


class LocalMarket:
    def __init__(self, base, model, all_rows, calendar):
        self.base, self.model = Path(base), model
        self.calendar, self.all_rows = calendar, all_rows
        self.daily_cache, self.minute_cache = {}, {}
        self.paths = None
        self.rd = None
        self.error = None
        try:
            sdk = self.base.parent / '数据缓存/free-stockdb/stockdb/pybao'
            if str(sdk) not in sys.path:
                sys.path.insert(0, str(sdk))
            from stock_sdk import init, rd
            init('127.0.0.1', 7899, warm=False)
            self.rd = rd
        except Exception as exc:
            self.error = type(exc).__name__
        from v3_sell_1335 import _load
        self.exact1335, self.quotes1335 = _load(base)
        self.result_cache = {}

    def previous(self, day):
        i = bisect_left(self.calendar, day) - 1
        return self.calendar[i] if i >= 0 else None

    def daily(self, code, day):
        key = str(code).zfill(6), day
        if key not in self.daily_cache:
            bar = {}
            if self.rd and day:
                try:
                    got = self.rd.get('日k', key[0], day.replace('-', '')).do()
                    if isinstance(got, dict):
                        bar = dict(got)
                except Exception:
                    pass
            from historical_daily_cache import merge_history
            if not hasattr(self, '_recorded_opens'):
                candidates = {}
                for record in self.all_rows:
                    value = positive(record.get('buy_price'))
                    identity = (str(record.get('stock_code') or '').zfill(6), record.get('date'))
                    if value is not None:
                        candidates.setdefault(identity, set()).add(value)
                self._recorded_opens = {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}
            bar = merge_history(key[0], day, bar, base=getattr(self, 'base', None), recorded_open=self._recorded_opens.get(key))
            from current_daily_marks import read_mark
            mark = read_mark(getattr(self, 'base', Path(__file__).resolve().parents[1]), key[0], day)
            if mark:
                sources = dict(bar.get('quote_sources') or {})
                for field in ('open','pre_close','close','high','low','volume','is_st','suspended'):
                    if field in mark and (field not in bar or bar.get(field) is None):
                        bar[field] = mark[field]
                        if field in ('open','close','pre_close'):
                            sources[field] = 'sina_dated_daily'
                bar['quote_sources'] = sources
                bar['daily_mark_time'] = mark['quote_time']
                if 'is_st' in bar:
                    bar.pop('limit_rule_unknown', None)
            self.daily_cache[key] = bar
        return self.daily_cache[key]

    def suspended(self, code, day):
        bar = self.daily(code, day)
        # A missing bar is a gap, NOT proof of suspension.
        if bar and (bar.get('suspended') or bar.get('is_suspended') or bar.get('volume') == 0):
            return True
        return any(str(r.get('stock_code')) == str(code) and r.get('date') == day and '停牌' in str(r.get('skip_reason') or '') and (not positive(r.get('buy_price')) or r.get('buy_suspended')) for r in self.all_rows)

    def minute(self, code, day, slot):
        key = str(code).zfill(6), day, slot
        if key not in self.minute_cache:
            value = None
            if self.rd:
                try:
                    stamp = day.replace('-', '') + ('093500' if slot == 'am' else '133500')
                    bar = self.rd.get('分钟k', key[0], stamp).do()
                    if hasattr(bar, 'get'):
                        value = positive(bar.get('close'))
                except Exception:
                    pass
            self.minute_cache[key] = value
        return self.minute_cache[key]

    def quote(self, row, day, slot):
        code = str(row['stock_code']).zfill(6)
        if slot == 'pm':
            # Exact sidecar nulls remain authoritative, preserving explicit gaps.
            source = self.exact1335.get((self.model['program'], str(row.get('id')), row.get('signal_date') or row.get('date'), code))
            if source and source.get('sell_date') == day:
                return source.get('sell_price_1335')
            if (code, day) in self.quotes1335:
                return self.quotes1335[code, day]
        quote = self.minute(code, day, slot)
        if quote is not None:
            return quote
        # Existing AM fields are dated evidence; never carry a D1 buy/sell price
        # into a different D2 day. Never use legacy 13:15 quotes.
        if slot == 'am':
            for source in self.all_rows:
                if str(source.get('stock_code')).zfill(6) != code:
                    continue
                for prefix in ('', 'd2_'):
                    sold = source.get(prefix + 'sell_date_935') or source.get(prefix + 'sell_date')
                    if sold == day and not source.get(prefix + 'skip_935'):
                        value = positive(source.get(prefix + 'sell_price_half_935')) or positive(source.get(prefix + 'sell_price_935'))
                        if value:
                            return value
        return None

    def valuation(self, code, day, clock):
        if not hasattr(self, 'valuation_info'):
            self.valuation_info = {}
        bar = self.daily(code, day)
        price = None
        mark_day, source, stale = day, 'dated_quote', False
        if not self.suspended(code, day):
            if clock in ('open', 'close'):
                price = positive(bar.get(clock))
                source = (bar.get('quote_sources') or {}).get(clock, 'free_daily')
            else:
                price = positive(self.minute(code, day, 'am' if clock == '09:35' else 'pm'))
                source = 'free_minute'
        if price is None:
            stale = True
            # Today's documented previous close is also dated market evidence.
            price = positive(bar.get('pre_close'))
            mark_day = self.previous(day)
            source = (bar.get('quote_sources') or {}).get('pre_close', 'dated_previous_close')
            if price is None:
                previous = self.previous(day)
                while previous:
                    historical = self.daily(code, previous)
                    price = positive(historical.get('close'))
                    if price is not None:
                        mark_day = previous
                        source = (historical.get('quote_sources') or {}).get('close', 'free_daily')
                        break
                    previous = self.previous(previous)
        self.valuation_info[str(code), day, clock] = dict(stock_code=str(code), date=mark_day if price is not None else None,
            price=float(price) if price is not None else None, source=source if price is not None else None,
            stale=stale, requested_date=day, requested_time=clock)
        return price

    def first_board(self, code, source_day):
        limit_day = self.previous(source_day)
        check_day = self.previous(limit_day) if limit_day else None
        if not hasattr(self, 'first_board_checks'):
            self.first_board_checks = {}
        info = dict(signal_date=source_day, limit_date=limit_day, check_date=check_day,
                    check_previous_date=self.previous(check_day) if check_day else None,
                    status='unknown', quotes={}, reason='首板交易日历缺失')
        self.first_board_checks[str(code).zfill(6), source_day] = info
        if not check_day:
            return None
        missing = []
        for label, day in [('D0-2', check_day), ('D0-1', limit_day)]:
            bar = self.daily(code, day)
            info['quotes'][label] = dict(date=day, close=bar.get('close'), pre_close=bar.get('pre_close'),
                                         sources=bar.get('quote_sources') or {})
            fields = [name for field, name in [('close', '收盘价'), ('pre_close', '前收盘价')] if positive(bar.get(field)) is None]
            if fields:
                missing.append(f'{label}（{day}）缺少' + '/'.join(fields))
            elif bar.get('limit_rule_unknown'):
                missing.append(f'{label}（{day}）涨停规则待核实')
        from first_board_gate import classify
        status = classify(code, limit_day, check_day, self.daily)
        info.update(status=status, reason='；'.join(missing) if status == 'unknown' else '')
        return True if status == 'first' else False if status in ('lianban', 'not_limit') else None

    def fallback(self, source):
        """Load ordered, same-signal/model candidates; never recompute scores.

        Raw rank rows work for every model. Original/board MD also contains
        the complete ranked remainder; unknown path formats are not guessed.
        """
        day = source['signal_date']
        rows = [dict(r) for r in self.all_rows if r.get('date') == day and belongs(r, self.model)]
        rank_key = 'board_rank' if self.model.get('board') else 'path_rank' if self.model.get('path') else 'rank'
        rows.sort(key=lambda r: int(r.get(rank_key) or 99999))
        if self.model['program'] == 'stock':
            key = self.previous(day)
            if key not in self.result_cache:
                import paths
                directory = Path(paths.program_dir('stock')) / 'results'
                parsed = []
                if key:
                    path = directory / (key.replace('-', '') + '_recommendations.md')
                    if path.exists():
                        text = path.read_text(encoding='utf-8')
                        seen = set()
                        for match in re.finditer(r'(?:排名\s*\d+:|^\s*)(\s*\d{6})\s*-\s*([^\n:：]+)(?:[:：]\s*综合得分\s*([\d.]+))?', text, re.M):
                            code, name, score = match.group(1).strip(), match.group(2).strip(), match.group(3)
                            if code not in seen:
                                seen.add(code)
                                board = '创业板' if code.startswith(('300', '301')) else '科创板' if code.startswith(('688', '689')) else '主板'
                                parsed.append(dict(stock_code=code, stock_name=name, score=score, board=board))
                self.result_cache[key] = parsed
            known = {r['stock_code'] for r in rows}
            rows.extend(r for r in self.result_cache[key] if r['stock_code'] not in known and (not self.model.get('board') or r['board'] == self.model['board']))
        try:
            i = next(i for i, r in enumerate(rows) if r['stock_code'] == source['stock_code'])
            return rows[i:]
        except StopIteration:
            return [source]

    def choose(self, source, mode, day):
        if not str(source.get('stock_code') or '').strip() or source.get('is_board_placeholder') or source.get('is_rank_placeholder'):
            return None, '当日无候选'
        candidates = self.fallback(source)
        for candidate in candidates:
            code = str(candidate['stock_code']).zfill(6)
            # Suspensions substitute the next SAME-signal rank; a data gap does
            # not trigger speculative substitution.
            if self.suspended(code, day):
                continue
            eligible = self.first_board(code, source['signal_date'])
            if eligible is False:
                return None, '未通过首板校验（连板或涨停日不符）'
            if eligible is None:
                info = getattr(self, 'first_board_checks', {}).get((code, source['signal_date']), {})
                return None, info.get('reason') or '首板校验行情缺失'
            bar = self.daily(code, day)
            buy = positive(bar.get('open'))
            if buy is None:
                return None, '买入日开盘行情缺失（未判停牌）'
            # Existing original-day explicit no-buy reason remains evidence.
            if mode == 'D1' and candidate['stock_code'] == source['stock_code'] and re.search('买不进|没买|不可买', str(source.get('skip_reason') or '')):
                return None, '原始记录不可买入'
            # One-price limit-up has no executable entry in this simulation.
            if positive(bar.get('high')) is not None and positive(bar.get('low')) is not None and positive(bar.get('high')) == positive(bar.get('low')) and at_limit(buy, bar, code) is True:
                return None, '一字涨跌停未确认买入'
            return candidate, ''
        return None, '候选停牌，后续名次不足'


def attach_opening_prices(replay, sources, market):
    """Display D0 market information independently of whether a buy executed.

    D2 still displays D0's open; its actual D1 entry price stays in `buy` for
    quantities and P/L. A substituted symbol cannot inherit the original open.
    """
    original = {(str(r.get('id')), str(r.get('date') or '')[:10], str(r.get('stock_code') or '').zfill(6)): r for r in sources}
    for row in replay['rows']:
        day = row.get('signal_date') or row['date']
        code = str(row.get('stock_code') or '').strip()
        row.update(open_price=None, open_price_date=day, open_price_source=None)
        if not code or row.get('is_board_placeholder') or row.get('is_rank_placeholder'):
            continue
        bar = market.daily(code, day)
        value = positive(bar.get('open'))
        source = (bar.get('quote_sources') or {}).get('open', 'free_daily_open') if value is not None else None
        if value is None:
            record = original.get((str(row.get('id')), day, code.zfill(6))) or {}
            value = positive(record.get('buy_price'))
            if value is not None:
                source = 'original_d0_open'
        row['open_price'] = float(value) if value is not None else None
        row['open_price_source'] = source
    return replay


def refresh_current_marks(replay, base, marks_signature=None):
    from current_daily_marks import request_refresh
    from v3_ledger import capital_period
    today = replay['as_of'][:10]
    current_period = capital_period(today)[0]
    latest = [r for r in replay['snapshots'] if r['period'] == current_period]
    missing = bool(latest and (latest[-1]['equity'] is None or any(m.get('stale') for m in latest[-1].get('valuation_marks', [])))) or any(r['date'] == today and (r.get('capital') is None or any(m.get('stale') for m in r.get('capital_marks', []))) for r in replay['rows'])
    codes = sorted({r['stock_code'] for r in replay['rows'] if r.get('period') == current_period and r.get('stock_code') and (r.get('remainingShares') or r['date'] == today)}) if missing else []
    pending = request_refresh(base, codes, today) if codes else False
    path = Path(base) / 'data/v3_daily_marks.json'
    latest_signature = (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
    replay['marks_refreshing'] = bool(missing and (pending or latest_signature != marks_signature))
    return replay


def ledger_for(namespace, model_id, rank, mode, as_of=None, force=False):
    model = next((m for m in MODELS if m['id'] == model_id), None)
    if model is None or rank not in (1, 2) or mode not in ('D1', 'D2'):
        raise ValueError('模型、名次或模式错误')
    base = namespace['BASE_DIR']
    import paths
    calendar = json.loads((Path(base) / 'data/trade_calendar.json').read_text())['trade_dates']
    scores = ResultScores(model['program'], Path(paths.program_dir(model['program'])) / 'results', calendar)
    now = as_of or datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='minutes')[:16]
    # Source mtimes + short quote TTL invalidate updates without mutation.
    signature = namespace['_all_cache_signature'](model['program'])
    sidecars = tuple((p.name, p.stat().st_mtime_ns) for p in (Path(base) / 'data/sell_1335').glob('*.json'))
    cache_key = (str(base), model_id, rank, mode)
    history = Path(base) / 'data/v3_daily_history.json'
    history_signature = (history.stat().st_mtime_ns, history.stat().st_size) if history.exists() else None
    marks = Path(base) / 'data/v3_daily_marks.json'
    marks_signature = (marks.stat().st_mtime_ns, marks.stat().st_size) if marks.exists() else None
    clock = now[11:16]
    phase = 'preopen' if clock < '09:30' else 'open' if clock < '09:35' else 'morning' if clock < '13:35' else 'afternoon' if clock < '15:00' else 'closed'
    ttl = 300 if phase == 'closed' else 60
    fingerprint = repr((VERSION,signature, scores.signature, sidecars, history_signature, marks_signature, now[:10], phase, time.time() // ttl))
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if not force and cached and cached[0] == fingerprint:
            return refresh_current_marks(cached[1], base, cached[2])
        with namespace['_data_lock']:
            all_rows = scores.enrich(namespace['_load_all_trades'](model['program']))
        selected = [r for r in all_rows if belongs(r, model, rank)]
        market = LocalMarket(base, model, all_rows, calendar)
        replay = Replay(calendar, market, now).run(selected, mode)
        attach_opening_prices(replay, selected, market)
        attach_scores(replay, all_rows, scores)
        for row in replay['rows']:
            info = getattr(market, 'first_board_checks', {}).get((str(row.get('stock_code') or '').zfill(6), row.get('signal_date')))
            if info:
                row['first_board_check'] = info
        replay.update(model=model_id, rank=rank, market_error=market.error)
        replay['monthly_stats']=monthly_stats(replay)
        _CACHE[cache_key] = fingerprint, replay, marks_signature
        return refresh_current_marks(replay, base, marks_signature)


def summary_for(namespace, model_id, rank, mode, force=False):
    """Compact comparison payload, derived from the identical event ledger."""
    replay = ledger_for(namespace, model_id, rank, mode, force=force)
    return dict(version=replay['version'],calc_version=replay['calc_version'],source_mode=replay['source_mode'],
                mode=mode,model=model_id,rank=rank,as_of=replay['as_of'],rows=[],
                monthly_stats=replay['monthly_stats'],marks_refreshing=replay.get('marks_refreshing',False))
