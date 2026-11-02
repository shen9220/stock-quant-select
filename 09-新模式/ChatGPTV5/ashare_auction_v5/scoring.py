"""Conservative, auditable pre-open ranking; missing required inputs fail closed."""
import argparse
import pandas as pd
from pathlib import Path

def rank(pool,auction,prev_close,theme):
    for name,df,cols in [('pool',pool,['code','first_seal','break_count','seal_amount','industry']),('auction',auction,['code','price','volume_hands','amount_yuan']),('prev_close',prev_close,['code','prev_close','avg_daily_amount_5d']),('theme',theme,['industry','theme_heat'])]:
        missing=set(cols)-set(df.columns)
        if missing:raise ValueError(f'{name}缺少字段: {missing}')
    for df in (pool,auction,prev_close):
        if df.code.astype(str).duplicated().any():raise ValueError('重复代码')
    a=pool.merge(auction,on='code',how='left',validate='one_to_one').merge(prev_close,on='code',how='left',validate='one_to_one').merge(theme,on='industry',how='left',validate='many_to_one')
    if len(a)!=len(pool) or a[['price','volume_hands','amount_yuan','prev_close','avg_daily_amount_5d','first_seal','break_count','seal_amount','theme_heat']].isna().any().any():
        raise ValueError('数据不完整，不能输出全池前二')
    for c in ['price','volume_hands','amount_yuan','prev_close','avg_daily_amount_5d','break_count','seal_amount','theme_heat']:
        a[c]=pd.to_numeric(a[c],errors='coerce')
    if a[['price','volume_hands','amount_yuan','prev_close','avg_daily_amount_5d','break_count','seal_amount','theme_heat']].isna().any().any() or (a[['price','volume_hands','amount_yuan','prev_close','avg_daily_amount_5d']]<=0).any().any():
        raise ValueError('无效量价或评分数据')
    # Auction amount as fraction of prior 5-day average daily amount, avoiding pure large-cap bias.
    ratio=a.amount_yuan/a.avg_daily_amount_5d
    a['volume_score']=(ratio/0.12*35).clip(0,35).round(2)
    a['first_seal']=a.first_seal.astype(str).str.replace(':','',regex=False).str.zfill(6)
    if ~a.first_seal.str.match(r'^\d{6}$').all():raise ValueError('首次封板时间格式无效')
    minute=a.first_seal.str[:2].astype(int)*60+a.first_seal.str[2:4].astype(int)
    early=((660-minute)/90).clip(0,1)*12
    stable=(10-a.break_count*2).clip(0,10)
    seal=(a.seal_amount/a.avg_daily_amount_5d/0.3*3).clip(0,3)
    a['seal_score']=(early+stable+seal).clip(0,25).round(2)
    if ((a.theme_heat<0)|(a.theme_heat>1)).any():raise ValueError('theme_heat必须是0到1之间的已验证值')
    a['theme_score']=(a.theme_heat*20).round(2)
    limit=a.code.astype(str).str.startswith(('300','301')).map({True:.2,False:.1})
    gain=a.price/a.prev_close-1
    if (gain>limit+.005).any() or (gain<-.22).any():raise ValueError('竞价涨幅异常')
    a['auction_strength_score']=(gain/limit*15).clip(0,15).round(2)
    a['total_95']=a[['volume_score','seal_score','theme_score','auction_strength_score']].sum(axis=1).round(2)
    return a.sort_values(['total_95','amount_yuan','code'],ascending=[False,False,True]).reset_index(drop=True)

def main():
    p=argparse.ArgumentParser()
    for x in ('pool','auction','prev-close','theme','out'):p.add_argument('--'+x,required=True)
    a=p.parse_args()
    read=lambda f:pd.read_csv(f,dtype={'code':str})
    result=rank(read(a.pool),read(a.auction),read(a.prev_close),read(a.theme))
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    result.to_csv(a.out,index=False,encoding='utf-8-sig')
    print(result[['code','name','total_95','volume_score','seal_score','theme_score','auction_strength_score']].head(2).to_string(index=False))
    print('注意：只有全部输入源真实且经过独立核验，排名才有实盘参考意义。')
if __name__=='__main__':main()
