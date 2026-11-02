"""Free AKShare auction snapshot collector. No invented data; fail closed."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pandas as pd

TZ=ZoneInfo('Asia/Shanghai')

def valid_code(code):
    c=str(code).split('.')[0].zfill(6)
    return len(c)==6 and c.startswith(('600','601','603','605','000','001','002','003','300','301'))

def parse_snapshot(frame, code, trade_date):
    """AKShare pre-min data: 09:26 row represents 09:25 auction; source-dependent mapping."""
    required={'时间','收盘','成交量','成交额'}
    if frame is None or not required.issubset(frame.columns):
        raise ValueError(f'{code}: 缺少列 {required-set([] if frame is None else frame.columns)}')
    f=frame.copy()
    f['时间']=f['时间'].astype(str)
    match=f[f['时间'].str.contains(r'(?:^|\s)09:26(?::00)?$',regex=True)]
    if match.empty: raise ValueError(f'{code}: 缺少09:26源行，不可用09:25/09:30代替')
    if len(match)!=1: raise ValueError(f'{code}: 09:26源行重复')
    row=match.iloc[0]
    stamp=str(row['时间'])
    if len(stamp)>=10 and stamp[:10].replace('-','')!=trade_date:
        raise ValueError(f'{code}: 数据日期不匹配')
    price=float(row['收盘']); vol=float(row['成交量']); amount=float(row['成交额'])
    if not all(pd.notna(v) for v in (price,vol,amount)) or price<=0 or vol<0 or amount<0:
        raise ValueError(f'{code}: 竞价量价无效')
    if vol==0 or amount==0: raise ValueError(f'{code}: 无成交，不能计入有效排名')
    # AKShare volume unit = hands, 100 shares per hand for ordinary A shares.
    implied=amount/(vol*100)
    if not (price*0.90 <= implied <= price*1.10):
        raise ValueError(f'{code}: 量价金额不一致; 单位或源数据可能变化')
    return {'code':str(code).zfill(6),'trade_date':trade_date,'auction_time':'09:25:00',
            'source_row_time':stamp,'price':price,'volume_hands':vol,'amount_yuan':amount,
            'source':'AKShare/Eastmoney pre-min 09:26 row','verified_cross_source':False}

def collect(codes,trade_date,workers=4):
    import akshare as ak
    results=[]; errors=[]
    def one(code):
        return parse_snapshot(ak.stock_zh_a_hist_pre_min_em(symbol=code),code,trade_date)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(one,code):code for code in codes}
        for future in as_completed(futures):
            try: results.append(future.result())
            except Exception as e: errors.append({'code':futures[future],'error':str(e)})
    return pd.DataFrame(results),pd.DataFrame(errors)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--pool',required=True,help='CSV含code,name,streak等字段，必须为前一交易日首板池')
    p.add_argument('--date',required=True,help='YYYYMMDD')
    p.add_argument('--outdir',default='reports')
    p.add_argument('--offline',action='store_true',help='仅处理本地已保存的AKShare分钟CSV目录，不访问行情接口')
    p.add_argument('--minute-dir',default='')
    args=p.parse_args()
    pool=pd.read_csv(args.pool,dtype={'code':str})
    required={'code','name','streak'}
    if not required.issubset(pool.columns): raise SystemExit(f'首板池缺少列: {required-set(pool.columns)}')
    pool['code']=pool.code.str.zfill(6)
    if pool.empty or pool.code.duplicated().any(): raise SystemExit('首板池为空或重复')
    if not pool.code.map(valid_code).all() or pool.name.str.contains('ST|退',case=False,na=False).any() or not pool.streak.eq(1).all():
        raise SystemExit('首板池包含非主板/创业板、ST/退市或非首板')
    if 'pool_date' not in pool.columns or pool.pool_date.astype(str).str.replace('-','').nunique()!=1:
        raise SystemExit('必须提供唯一pool_date以核验前一交易日，不能以当日池替代')
    if args.offline:
        rows=[]; errs=[]
        for code in pool.code:
            try: rows.append(parse_snapshot(pd.read_csv(Path(args.minute_dir)/f'{code}.csv'),code,args.date))
            except Exception as e: errs.append({'code':code,'error':str(e)})
        data=pd.DataFrame(rows); errors=pd.DataFrame(errs)
    else:
        now=datetime.now(TZ)
        if now.strftime('%Y%m%d')!=args.date or now.hour<9 or (now.hour==9 and now.minute<26):
            raise SystemExit('拒绝获取：不是指定交易日9:26之后，避免未来函数或历史伪实时')
        data,errors=collect(pool.code.tolist(),args.date)
    out=Path(args.outdir);out.mkdir(parents=True,exist_ok=True)
    data.to_csv(out/f'auction_{args.date}.csv',index=False,encoding='utf-8-sig')
    errors.to_csv(out/f'errors_{args.date}.csv',index=False,encoding='utf-8-sig')
    n=len(pool); good=len(data)
    print(f'竞价覆盖 {good}/{n}; 失败 {n-good}; 源数据未经独立行情交叉验证')
    if good!=n: raise SystemExit('FAIL CLOSED: 数据缺失，不允许宣布全池正式前二')
    print('采集成功；下一步需独立数据源校验与评分，不等于可实盘下单')

if __name__=='__main__':main()
