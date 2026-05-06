import akshare as ak  # 免费A股数据
import pandas as pd
from datetime import datetime, timedelta, time
import os

def get_previous_trading_day(current_date):
    """获取前一个中国股市交易日（使用真实交易日历）"""
    # 使用 akshare 获取中国A股真实交易日历
    try:
        trade_cal = ak.tool_trade_date_hist_sina()
        trade_cal['trade_date'] = pd.to_datetime(trade_cal['trade_date'])
        
        # 将当前日期转换为日期格式（去掉时间部分）
        current_date_only = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 找出所有在当前日期之前的交易日
        past_trades = trade_cal[trade_cal['trade_date'] < current_date_only]
        
        if len(past_trades) > 0:
            # 返回最后一个交易日
            return past_trades.iloc[-1]['trade_date'].to_pydatetime()
    except:
        pass
    
    # 如果获取失败， fallback 到简单的周末排除逻辑
    day = current_date - timedelta(1)
    while day.weekday() >= 5:  # 5=Saturday, 6=Sunday
        day -= timedelta(1)
    return day

def get_historical_zt_count(stock_code, days=30):
    """获取过去days天内的涨停次数"""
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        zt_history = ak.stock_zt_pool_em(date=end_date.strftime('%Y%m%d'))
        count = 0
        for i in range(days):
            date_str = (end_date - timedelta(i)).strftime('%Y%m%d')
            daily_zt = ak.stock_zt_pool_em(date=date_str)
            if stock_code in daily_zt['代码'].values:
                count += 1
        return count
    except:
        return 0

def get_trend_indicator(stock_code):
    """简单趋势指标：5日均线 > 10日均线则为1，否则0"""
    try:
        hist = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=(datetime.now() - timedelta(30)).strftime('%Y%m%d'), end_date=datetime.now().strftime('%Y%m%d'))
        if len(hist) < 10:
            return 0
        ma5 = hist['收盘'].tail(5).mean()
        ma10 = hist['收盘'].tail(10).mean()
        return 1 if ma5 > ma10 else 0
    except:
        return 0

def get_basic_fundamentals(stock_code):
    """获取基本面数据，如市盈率"""
    try:
        fundamentals = ak.stock_a_lg_indicator_cninfo(symbol=stock_code)
        if not fundamentals.empty:
            pe = fundamentals.get('市盈率', 0)
            return pe if pe else 0
        return 0
    except:
        return 0

def calculate_composite_score(row):
    """计算综合得分"""
    # 权重：涨幅40%, 成交额20%, 换手率20%, 历史基因10%, 趋势10%
    zf_score = row['涨跌幅'] * 0.4
    cj_score = (row['成交额'] / 1e8) * 0.2  # 标准化成交额（亿元）
    hs_score = row['换手率'] * 0.2
    gene_score = row.get('历史涨停基因', 0) * 0.1
    trend_score = row.get('趋势指标', 0) * 0.1
    return zf_score + cj_score + hs_score + gene_score + trend_score

def analyze_zhangting_stocks():
    print("开始分析涨停股票...")
    
    # 1. 获取前一个交易日
    today = datetime.now()
    previous_trading_day = get_previous_trading_day(today)
    query_date_str = previous_trading_day.strftime('%Y%m%d')
    query_date_display = previous_trading_day.strftime('%Y年%m月%d日')
    
    print(f"查询日期: {query_date_display} (前一个交易日)")
    
    # 检查时间窗口：早盘开盘附近（9:30-10:00）
    # 临时注释掉时间检查以演示输出
    # current_time = today.time()
    # if not (time(9, 30) <= current_time <= time(10, 0)):
    #     print("当前时间不在早盘开盘时间窗口（9:30-10:00）内，无法查询涨停情况。")
    #     return
    
    print("正在获取涨停股票数据...")
    # 获取前一个交易日涨停板股票
    zt_df = ak.stock_zt_pool_em(date=query_date_str)
    
    if zt_df.empty:
        print(f"{query_date_display} 暂无涨停股票数据。")
        return
    
    print(f"获取到 {len(zt_df)} 只涨停股票")
    
    # 筛选连续1天涨停（前一个交易日涨停，前两天没涨停）
    two_days_ago = get_previous_trading_day(previous_trading_day)
    two_days_ago_str = two_days_ago.strftime('%Y%m%d')
    print(f"筛选首次涨停股票（排除{two_days_ago_str}已涨停的股票）...")
    two_days_ago_zt = ak.stock_zt_pool_em(date=two_days_ago_str)
    
    today_zt_only = zt_df[~zt_df['代码'].isin(two_days_ago_zt['代码'])]
    
    # 增加筛选条件：排除科创板（688开头）、北交所（92开头）和ST票（名称包含ST或st），主要查询主板和创业板
    today_zt_only = today_zt_only[~today_zt_only['代码'].str.startswith(('688', '92')) & ~today_zt_only['名称'].str.contains('ST|st', case=False)]
    
    if today_zt_only.empty:
        print(f"{query_date_display} 无符合连续1天涨停条件的股票。")
        return
    
    print(f"筛选后剩余 {len(today_zt_only)} 只股票")
    
    print("正在计算各项指标...")
    # 添加额外指标
    today_zt_only['历史涨停基因'] = today_zt_only['代码'].apply(lambda x: get_historical_zt_count(x, 30))
    today_zt_only['趋势指标'] = today_zt_only['代码'].apply(get_trend_indicator)
    today_zt_only['市盈率'] = today_zt_only['代码'].apply(get_basic_fundamentals)
    
    # 计算综合得分
    today_zt_only['综合得分'] = today_zt_only.apply(calculate_composite_score, axis=1)
    
    # 强度排名：按综合得分排序，取前2只
    sorted_stocks = today_zt_only.sort_values('综合得分', ascending=False)
    top2 = sorted_stocks.head(2)
    
    print("分析完成，正在生成结果...")
    # 准备输出内容
    output_lines = []
    output_lines.append(f"=== {query_date_display} 强度推荐排名（前2只，基于多因子综合得分）===")
    for idx, row in top2.iterrows():
        output_lines.append(f"排名 {idx+1}: {row['代码']} - {row['名称']}")
        output_lines.append(f"  涨停天数: 1天")
        output_lines.append(f"  涨幅: {row['涨跌幅']}%")
        output_lines.append(f"  成交额: {row['成交额']}")
        output_lines.append(f"  换手率: {row['换手率']}%")
        output_lines.append(f"  历史涨停基因（30天内涨停次数）: {row['历史涨停基因']}")
        output_lines.append(f"  趋势指标（1=上升，0=其他）: {row['趋势指标']}")
        output_lines.append(f"  基本面（市盈率）: {row['市盈率']}")
        output_lines.append(f"  综合得分: {row['综合得分']:.2f}")
        # 涨停原因分析（假设数据中有此字段）
        reason = row.get('涨停原因', '数据中未提供具体原因')
        output_lines.append(f"  涨停原因分析: {reason}")
        # 连板属性和胜率（简化）
        lianban = "有连板潜力" if row['历史涨停基因'] > 5 else "连板属性一般"
        win_rate = "首板胜率较高" if row['趋势指标'] == 1 and row['历史涨停基因'] > 3 else "首板胜率中等"
        output_lines.append(f"  连板属性: {lianban}")
        output_lines.append(f"  首板胜率评估: {win_rate}（基于历史数据和趋势）")
        output_lines.append(f"  买入后第二天卖出建议: 若趋势持续，建议持有观察；否则考虑T+1卖出")
        output_lines.append("")
    
    # 其他未入选强度前二原因
    others = sorted_stocks.iloc[2:]
    output_lines.append("=== 其他未入选强度前二原因 ===")
    if not others.empty:
        output_lines.append("以下股票综合得分较低，未入选前二：")
        for idx, row in others.iterrows():
            output_lines.append(f"  {row['代码']} - {row['名称']}: 综合得分 {row['综合得分']:.2f} - 得分低于前二")
    else:
        output_lines.append("无其他候选股票。")
    
    # 打印到控制台
    for line in output_lines:
        print(line)
    
    # 保存到文件
    results_dir = "/Users/admin/Downloads/projects_test/filter/results"
    os.makedirs(results_dir, exist_ok=True)
    file_name = f"{query_date_str}_recommendations.md"
    file_path = os.path.join(results_dir, file_name)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))
    print(f"\n✅ 结果已保存到: {file_path}")
    print("🎉 分析完成！")
    print("🎉 分析完成！")

# 运行分析
if __name__ == "__main__":
    analyze_zhangting_stocks()