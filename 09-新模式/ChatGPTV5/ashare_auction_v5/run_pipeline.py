"""Offline reproducible full-pool ranking with strict provenance and calendar checks."""
import argparse
from datetime import date
from pathlib import Path
import pandas as pd
from scoring import rank
from audit import reconcile
from free_auction import valid_code

def previous_trading_day(day):
    import exchange_calendars as xcals
    cal=xcals.get_calendar('XSHG')
    session=pd.Timestamp(day)
    if not cal.is_session(session):
        raise ValueError(f'{day}: 非沪深交易日')
    return cal.previous_session(session).strftime('%Y%m%d')

def verify_pool(pool, trade_date):
    expected=previous_trading_day(pd.to_datetime(trade_date,format='%Y%m%d').strftime('%Y-%m-%d'))
    required={'code','name','streak','pool_date'}
    if not required.issubset(pool.columns): raise ValueError('股票池字段缺失')
    if pool.empty or pool.code.duplicated().any():raise ValueError('空股票池或重复代码')
    if not pool.code.map(valid_code).all() or not pool.streak.eq(1).all():raise ValueError('股票池包含非首板或不支持板块')
    if pool.name.astype(str).str.contains('ST|退',case=False,regex=True).any():raise ValueError('含ST或退市')
    if not pool.pool_date.astype(str).str.replace('-','').eq(expected).all():raise ValueError(f'股票池日期不符，应为{expected}')
    return expected

def pipeline(pool, auction, reference, prev_close, theme, trade_date):
    verify_pool(pool,trade_date)
    if set(pool.code)!=set(auction.code) or set(pool.code)!=set(prev_close.code):raise ValueError('竞价或历史数据覆盖不完整')
    if not auction.trade_date.astype(str).eq(trade_date).all():raise ValueError('竞价数据日期错误')
    checks=reconcile(pool,auction,reference)
    if not checks.passed.all():raise ValueError('独立源量价校验不通过')
    if 'source' not in auction or auction.source.isna().any():raise ValueError('缺少行情来源')
    ranked=rank(pool,auction,prev_close,theme)
    ranked.insert(0,'rank',range(1,len(ranked)+1))
    ranked['trade_date']=trade_date
    ranked['status']='OFFLINE_VERIFIED_INPUTS_NOT_LIVE_SIGNAL'
    return ranked,checks

def main():
    ap=argparse.ArgumentParser(description='离线核验+排序。不能替代9:26实时采集。')
    for k in ['pool','auction','reference','prev-close','theme','date','outdir']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();rd=lambda f:pd.read_csv(f,dtype={'code':str,'trade_date':str,'pool_date':str})
    ranked,checks=pipeline(rd(a.pool),rd(a.auction),rd(a.reference),rd(a.prev_close),rd(a.theme),a.date)
    dest=Path(a.outdir);dest.mkdir(parents=True,exist_ok=True)
    ranked.to_csv(dest/f'ranking_{a.date}.csv',index=False,encoding='utf-8-sig')
    checks.to_csv(dest/f'audit_{a.date}.csv',index=False,encoding='utf-8-sig')
    print('仅离线重算；非9:26实盘信号。全池',len(ranked),'只，独立校验通过。')
    print(ranked[['rank','code','name','total_95','volume_score','seal_score','theme_score','auction_strength_score']].head(2).to_string(index=False))
if __name__=='__main__':main()
