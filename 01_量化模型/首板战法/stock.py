"""
股票量化选股模型 - 隔夜战法
使用 akshare 获取实时数据
"""
import sys
import os

# 添加用户 site-packages 路径（确保 akshare 能被找到）- 必须放在最前面！
user_site_packages = os.path.expanduser('~/.local/lib/python3.13/site-packages')
if user_site_packages not in sys.path:
    sys.path.insert(0, user_site_packages)

import akshare as ak  # 免费A股数据
import pandas as pd
from datetime import datetime, timedelta, time
from requests.exceptions import RequestException

def get_previous_trading_day(current_date):
    """获取前一个中国股市交易日"""
    try:
        # 使用 akshare 获取真实交易日历
        trade_cal = ak.tool_trade_date_hist_sina()
        trade_cal['trade_date'] = pd.to_datetime(trade_cal['trade_date'])
        current_date_only = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        past_trades = trade_cal[trade_cal['trade_date'] < current_date_only]
        if len(past_trades) > 0:
            return past_trades.iloc[-1]['trade_date'].to_pydatetime()
    except:
        pass
    # fallback 到简单的周末排除逻辑
    day = current_date - timedelta(1)
    while day.weekday() >= 5:
        day -= timedelta(1)
    return day

def get_limit_up_stocks(target_date):
    """获取涨停股票"""
    print(f"正在获取涨停股票数据...")
    try:
        # 获取涨停股票池
        zt_df = ak.stock_zt_pool_em(date=target_date.strftime('%Y%m%d'))
        if zt_df is None or zt_df.empty:
            print(f"{target_date.strftime('%Y年%m月%d日')} 暂无涨停股票数据。")
            return pd.DataFrame()
        
        print(f"获取到 {len(zt_df)} 只涨停股票")
        return zt_df
    except Exception as e:
        print(f"获取涨停数据失败: {e}")
        return pd.DataFrame()

def filter_first_limit_up(zt_df, current_date):
    """筛选首次涨停股票（排除已连续涨停的）"""
    print("正在筛选首次涨停股票...")
    
    if zt_df.empty:
        return pd.DataFrame()
    
    # 获取前一个交易日
    prev_date = get_previous_trading_day(current_date)
    prev_date_str = prev_date.strftime('%Y%m%d')
    print(f"查询前一日数据: {prev_date_str}")
    
    try:
        # 获取前一日的涨停股票
        prev_zt_df = ak.stock_zt_pool_em(date=prev_date_str)
        if prev_zt_df is None or prev_zt_df.empty:
            print(f"{prev_date.strftime('%Y年%m月%d日')} 无涨停股票")
            return zt_df
        
        # 获取前一日涨停的股票代码
        prev_zt_codes = set(prev_zt_df['代码'].astype(str).tolist())
        print(f"前一日涨停股票数: {len(prev_zt_codes)}")
        
        # 排除前一日已涨停的股票（保留首次涨停）
        current_zt_codes = zt_df['代码'].astype(str).tolist()
        first_zt_df = zt_df[~zt_df['代码'].astype(str).isin(prev_zt_codes)]
        
        print(f"筛选后首次涨停股票数: {len(first_zt_df)}")
        return first_zt_df
        
    except Exception as e:
        print(f"筛选首次涨停失败: {e}")
        return zt_df

def analyze_stocks(zt_df):
    """分析涨停股票"""
    print("正在计算各项指标...")
    
    if zt_df.empty:
        print("没有涨停股票可供分析")
        return
    
    results = []
    
    for idx, row in zt_df.iterrows():
        try:
            stock_code = str(row['代码']).zfill(6)
            stock_name = row.get('名称', '未知')
            change_pct = row.get('涨跌幅', 0)
            turnover = row.get('成交额', 0)
            exchange_ratio = row.get('换手率', 0)
            
            # 基本评分
            score = 50  # 基础分
            
            # 涨停幅度评分（涨停越好）
            if change_pct >= 9.9:
                score += 15
            elif change_pct >= 9.5:
                score += 10
            
            # 成交额评分（成交活跃度）
            if turnover > 5:
                score += 15
            elif turnover > 2:
                score += 10
            elif turnover > 1:
                score += 5
            
            # 换手率评分
            if exchange_ratio > 10:
                score += 10
            elif exchange_ratio > 5:
                score += 5
            
            # 封单强度评分
            volume = row.get('成交量', 0)
            if volume > 100000:
                score += 10
            
            results.append({
                'code': stock_code,
                'name': stock_name,
                'change_pct': change_pct,
                'turnover': turnover,
                'exchange_ratio': exchange_ratio,
                'score': min(score, 100)
            })
            
        except Exception as e:
            continue
    
    # 按评分排序
    results.sort(key=lambda x: x['score'], reverse=True)
    
    print("\n" + "="*60)
    print("涨停股票分析结果")
    print("="*60)
    
    for idx, stock in enumerate(results[:10], 1):
        print(f"\n【第{idx}名】{stock['code']} {stock['name']}")
        print(f"  涨停幅度: {stock['change_pct']:.2f}%")
        print(f"  成交额: {stock['turnover']:.2f}亿")
        print(f"  换手率: {stock['exchange_ratio']:.2f}%")
        print(f"  综合评分: {stock['score']}分")
        
        # 简单交易建议
        if stock['score'] >= 80:
            print(f"  建议: ⭐⭐⭐⭐⭐ 强烈推荐")
            print(f"  买入: 明日开盘可考虑买入半仓")
            print(f"  止损: 跌破涨停底部价格立即止损")
        elif stock['score'] >= 60:
            print(f"  建议: ⭐⭐⭐⭐ 推荐关注")
            print(f"  买入: 可轻仓试探")
        else:
            print(f"  建议: ⭐⭐⭐ 谨慎关注")
    
    print("\n" + "="*60)
    print("分析完成")
    print("="*60)

def main():
    """主函数"""
    print("="*60)
    print("股票量化选股模型 - 隔夜战法")
    print("="*60)
    print()
    
    # 获取当前日期
    current_date = datetime.now()
    
    # 如果是周末，自动使用上一个交易日
    if current_date.weekday() >= 5:  # 5=Saturday, 6=Sunday
        current_date = get_previous_trading_day(current_date)
    
    prev_date = get_previous_trading_day(current_date)
    print(f"分析日期: {current_date.strftime('%Y年%m月%d日 %H:%M')}")
    print(f"查询日期: {prev_date.strftime('%Y年%m月%d日')} (前一个交易日)")
    print()
    
    # 获取涨停股票
    zt_df = get_limit_up_stocks(prev_date)
    
    if zt_df.empty:
        print("\n没有找到涨停股票")
        return
    
    # 筛选首次涨停
    first_zt_df = filter_first_limit_up(zt_df, current_date)
    
    # 分析股票
    analyze_stocks(first_zt_df)

if __name__ == "__main__":
    main()
