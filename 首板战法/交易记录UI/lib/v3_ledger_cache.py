"""Keep the last valid financial ledger when the local quote database is offline.

Today's recommendations can still be published. No fill or new account value is
inferred from yesterday's quote, nor is historical money recomputed without data.
"""
from pathlib import Path
from bisect import bisect_right
from copy import deepcopy
import json
import os
import tempfile

from v3_template_ledger import VERSION, Replay
from v3_ledger import capital_period
from v3_ledger import positive,as_float


def cache_path(base,model,rank,mode):
    return Path(base)/'data/v3_ledger_cache'/f'{model}_{rank}_{mode}.json'


def read_ledger(base,model,rank,mode):
    try:
        data=json.loads(cache_path(base,model,rank,mode).read_text())
        return data if data.get('version')==VERSION and data.get('market_status')!='offline' else None
    except (OSError,ValueError):
        return None


def save_ledger(base,model,rank,mode,data):
    target=cache_path(base,model,rank,mode);target.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix=target.stem+'-',suffix='.tmp',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as file:json.dump(data,file,ensure_ascii=False,separators=(',',':'))
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def offline_projection(cached,selected,calendar,market,now,model,rank,mode):
    result=deepcopy(cached) if cached else dict(version=VERSION,calc_version=VERSION,source_mode='market',
        rows=[],events=[],snapshots=[],monthly_stats={})
    today=now[:10];days=sorted(calendar)
    replay=Replay(calendar,market,now)
    def shift(day):
        i=bisect_right(days,day)
        return days[i] if i<len(days) else None
    def identity(row):return str(row.get('id')),row.get('signal_date') or row.get('date'),row.get('date')
    existing={identity(r):r for r in result['rows']}
    for source in selected:
        signal=str(source['date'])[:10];day=shift(signal) if mode=='D2' else signal
        if not day or day>today:continue
        key=str(source.get('id')),signal,day
        old=existing.get(key)
        if old and old.get('executed'):continue
        incoming=dict(source,date=day,signal_date=signal)
        row=replay.empty(incoming,mode,'今日选股已同步，行情库离线，成交待确认')
        row.update(period=capital_period(day)[0],planned_sell_date=shift(day),
                   open_price=as_float(positive(source.get('buy_price'))),open_price_date=signal,
                   open_price_source='recorded_dated_open',market_status='offline')
        if old:old.update(row)
        else:result['rows'].append(row);existing[key]=row
    previous=result.get('as_of')
    snapshots=[s for s in result.get('snapshots',[]) if s.get('period')==capital_period(today)[0]]
    last=snapshots[-1] if snapshots else {}
    valuation=max((m['date'] for m in last.get('valuation_marks',[]) if m.get('date')),default=last.get('date'))
    # Frozen historical amounts remain unchanged; their dates stay visible.
    result.update(as_of=now,cached_as_of=previous,valuation_as_of=valuation,market_status='offline',
        market_error=market.error or 'quote_database_unavailable',marks_refreshing=False,
        model=model,rank=rank,mode=mode,
        data_note='行情库离线：今日选股已同步，金额保留最近有效账本，未确认新成交。')
    result['rows'].sort(key=lambda r:(r['date'],int(r.get('id') or 0)),reverse=True)
    return result
