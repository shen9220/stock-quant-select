"""Fail-closed cross-source reconciliation of 09:25 auction snapshots."""
import argparse
from pathlib import Path
import pandas as pd

FIELDS=('price','volume_hands','amount_yuan')

def reconcile(pool, primary, reference, price_tol=0.001, volume_tol=0.02, amount_tol=0.02):
    for name,frame,cols in [('pool',pool,['code','streak']),('primary',primary,['code','trade_date','auction_time',*FIELDS]),('reference',reference,['code','trade_date','auction_time',*FIELDS])]:
        missing=set(cols)-set(frame.columns)
        if missing: raise ValueError(f'{name}: missing columns {sorted(missing)}')
        if frame.code.astype(str).duplicated().any(): raise ValueError(f'{name}: duplicate code')
    p=pool.copy(); a=primary.copy(); b=reference.copy()
    for frame in (p,a,b): frame['code']=frame.code.astype(str).str.zfill(6)
    if p.empty or not p.streak.eq(1).all(): raise ValueError('invalid first-board pool')
    codes=set(p.code)
    if set(a.code)!=codes or set(b.code)!=codes: raise ValueError('incomplete or extra stock coverage')
    if a.trade_date.nunique()!=1 or b.trade_date.nunique()!=1 or a.trade_date.iloc[0]!=b.trade_date.iloc[0]: raise ValueError('date mismatch')
    if not a.auction_time.eq('09:25:00').all() or not b.auction_time.eq('09:25:00').all():raise ValueError('wrong auction timestamp')
    joined=a.merge(b,on='code',suffixes=('_main','_ref'),validate='one_to_one')
    limits={'price':price_tol,'volume_hands':volume_tol,'amount_yuan':amount_tol}
    for f in FIELDS:
        left=pd.to_numeric(joined[f+'_main'],errors='coerce');right=pd.to_numeric(joined[f+'_ref'],errors='coerce')
        if left.isna().any() or right.isna().any() or (left<=0).any() or (right<=0).any():raise ValueError(f'{f}: invalid value')
        joined[f+'_relative_error']=((left-right).abs()/right).round(8)
        joined[f+'_pass']=joined[f+'_relative_error']<=limits[f]
    joined['passed']=joined[[f+'_pass' for f in FIELDS]].all(axis=1)
    return joined

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pool',required=True);ap.add_argument('--primary',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args();read=lambda path:pd.read_csv(path,dtype={'code':str,'trade_date':str})
    result=reconcile(read(a.pool),read(a.primary),read(a.reference))
    Path(a.out).parent.mkdir(parents=True,exist_ok=True);result.to_csv(a.out,index=False,encoding='utf-8-sig')
    print(f'交叉核验: {int(result.passed.sum())}/{len(result)}; 报告 {a.out}')
    if not result.passed.all():raise SystemExit('核验不通过，禁止正式排名')
if __name__=='__main__':main()
