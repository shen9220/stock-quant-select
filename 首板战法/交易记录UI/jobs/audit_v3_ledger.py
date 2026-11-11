"""Read-only audit of all model/rank/mode accounts on the local preview server.
Writes an aggregate review artifact, never trades or Pages. Can be rerun after
10:00 / 14:00 market data updates; identical inputs do not duplicate events.
"""
import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import urllib.request


def main():
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:5051');p.add_argument('--out',required=True)
    args=p.parse_args();summary=[]
    for mode in ('D1','D2'):
        for model in ('original','main','cyb','under','low','stable','advance','low2','up'):
            for rank in (1,2):
                url=f'{args.url}/api/v3/ledger?model={model}&rank={rank}&mode={mode}'
                payload=json.load(urllib.request.urlopen(url,timeout=120));assert payload['success'],payload
                d=payload['data'];cash={};shares=defaultdict(int);seen=set()
                for e in d['events']:
                    period=e['period'];cash.setdefault(period,Decimal(100000 if period=='2026-first' else 40000))
                    if e['type']=='buy':
                        cash[period]-=Decimal(str(e['cost']));shares[e['trade_key']]+=e['shares']
                    elif e['type']=='sell':
                        identity=(e['trade_key'],e['slot']);assert identity not in seen,identity;seen.add(identity)
                        cash[period]+=Decimal(str(e['proceeds']));shares[e['trade_key']]-=e['shares'];assert shares[e['trade_key']]>=0
                last={s['period']:s for s in d['snapshots']}
                for period,balance in cash.items():assert abs(balance-Decimal(str(last[period]['cash'])))<Decimal('.000001'),(model,rank,mode,period,balance,last[period])
                assert d['version']=='doc-delta-v1'
                bought=[r for r in d['rows'] if r['executed']]
                for r in bought:
                    assert r['invested']<=r['capital']/2+1e-7
                    assert r['invested']<=r['buyAmount']+1e-7
                    assert r['positionAmount']==r['initial_buy_cost']==r['invested']
                    assert abs(r['budget_remainder']-(r['buyBudget']-r['invested']))<1e-6
                    assert r['shares']%100==0 and r['remainingShares']==shares[r['trade_key']]
                    realized=sum(e['profit'] for e in r['events'])
                    assert abs(r['realized_profit_total']-realized)<1e-6
                    if r['unrealized_profit'] is not None:
                        assert abs(r['display_profit']-realized-r['unrealized_profit'])<1e-6
                    for e in r['events']:
                        assert abs(e['profit']-(e['proceeds']-e['allocated_initial_cost']))<1e-6
                        assert abs(e['proceeds']-e['allocated_carry_value']-e['batch_change'])<1e-6
                for s in d['monthly_stats'].values():
                    if s['ending'] is not None:
                        assert abs(s['opening']+(s['profit'] or 0)+s['floatingChange']-s['ending'])<1e-5
                        assert abs(s['cash']+s['position_value']-s['ending'])<1e-5
                summary.append(dict(model=model,rank=rank,mode=mode,as_of=d['as_of'],version=d['version'],bought=len(bought),statuses=d['statuses'],sells=len(seen),cash_conserved=True,half_asset_cap=True,shares_conserved=True))
                print(f'PASS {model} rank{rank} {mode}: {len(bought)} buys / {len(seen)} sells',flush=True)
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);temp=out.with_suffix('.tmp')
    temp.write_text(json.dumps(summary,ensure_ascii=False,indent=2));temp.replace(out)
    print(f'{len(summary)} independent accounts passed; report: {out}')

if __name__=='__main__':main()
