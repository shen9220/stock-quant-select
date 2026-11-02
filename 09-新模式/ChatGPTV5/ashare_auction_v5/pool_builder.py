"""Build yesterday's first-limit-up pool from AKShare Eastmoney limit-up pool."""
import argparse
from pathlib import Path
import pandas as pd
from free_auction import valid_code

def build_pool(frame, pool_date):
    required={'代码','名称','连板数'}
    if not required.issubset(frame.columns):
        raise ValueError(f'涨停池缺少列: {required-set(frame.columns)}')
    df=frame.copy()
    df['code']=df['代码'].astype(str).str.zfill(6)
    df['streak']=pd.to_numeric(df['连板数'],errors='coerce')
    df=df[df['code'].map(valid_code)&df['streak'].eq(1)&~df['名称'].astype(str).str.contains('ST|退',case=False,regex=True)]
    if df.empty: raise ValueError('有效首板池为空，拒绝输出')
    if df['code'].duplicated().any():raise ValueError('股票代码重复')
    out=pd.DataFrame({'code':df.code,'name':df['名称'],'streak':1,'pool_date':pool_date})
    for source,target in [('首次封板时间','first_seal'),('最后封板时间','last_seal'),('炸板次数','break_count'),('封板资金','seal_amount'),('所属行业','industry'),('换手率','turnover')]:
        out[target]=df[source].values if source in df else pd.NA
    return out.reset_index(drop=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--date',required=True,help='上一交易日 YYYYMMDD');p.add_argument('--out',required=True);p.add_argument('--input-csv',help='离线原始涨停池CSV')
    a=p.parse_args()
    if a.input_csv: raw=pd.read_csv(a.input_csv,dtype={'代码':str})
    else:
        import akshare as ak
        raw=ak.stock_zt_pool_em(date=a.date)
    pool=build_pool(raw,a.date)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    pool.to_csv(a.out,index=False,encoding='utf-8-sig')
    print(f'已生成 {len(pool)} 只首板候选，注意核验交易日历与原始源完整性：{a.out}')
if __name__=='__main__':main()
