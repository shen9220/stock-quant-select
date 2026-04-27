#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强度首板战法模型
策略：连续涨停1天，早盘集合竞价买入，次日收盘前全部卖出
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import sys
import time


def get_previous_trading_day(current_date):
    """获取前一个交易日（周一到周五）"""
    day = current_date - timedelta(1)
    while day.weekday() >= 5:  # 5=Saturday, 6=Sunday
        day -= timedelta(1)
    return day


def get_zt_pool_with_retry(date_str, max_retries=3):
    """获取涨停池（带重试机制）"""
    for attempt in range(max_retries):
        try:
            zt_df = ak.stock_zt_pool_em(date=date_str)
            return zt_df
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                return pd.DataFrame()


def get_stock_history_with_retry(stock_code, days=60, max_retries=2):
    """
    获取股票历史数据（优化版）
    
    优化处理：
    1. 尝试多个数据源
    2. 减少请求数据量（只获取必要的数据）
    3. 减少重试次数，提高速度
    4. 使用更稳定的数据源
    """
    normalized_code = stock_code
    if len(stock_code) > 6:
        normalized_code = stock_code[-6:]
    
    # 尝试不同的数据源
    data_sources = [
        # 方案1：标准接口
        lambda: ak.stock_zh_a_hist(
            symbol=normalized_code,
            period="daily",
            start_date=(datetime.now() - timedelta(days=days)).strftime('%Y%m%d'),
            end_date=datetime.now().strftime('%Y%m%d'),
            adjust="qfq"
        ),
        # 方案2：新浪财经接口（备用）
        lambda: ak.stock_zh_a_hist(
            symbol=normalized_code,
            period="daily",
            adjust=""
        )
    ]
    
    for source_idx, get_data in enumerate(data_sources):
        for attempt in range(max_retries):
            try:
                df = get_data()
                if df is not None and not df.empty and len(df) >= 10:
                    return df
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(0.2)
                    continue
                else:
                    # 尝试下一个数据源
                    break
    
    return None


def get_stock_fundamentals_with_retry(stock_code, max_retries=3):
    """
    获取股票基本面数据（带重试机制）
    
    优化处理：
    1. 从涨停池数据中获取部分信息
    2. 标准化股票代码
    3. 捕获所有异常
    """
    for attempt in range(max_retries):
        try:
            # 标准化股票代码
            normalized_code = stock_code
            if len(stock_code) > 6:
                normalized_code = stock_code[-6:]
            
            # 获取实时行情
            spot_df = ak.stock_zh_a_spot_em()
            stock_info = spot_df[spot_df['代码'] == normalized_code]
            
            if not stock_info.empty:
                return {
                    'price': float(stock_info['最新价'].values[0]),
                    'market_cap': float(stock_info['总市值'].values[0]),
                    'pe_ratio': float(stock_info['市盈率-动态'].values[0]) if '市盈率-动态' in stock_info.columns and pd.notna(stock_info['市盈率-动态'].values[0]) else 0,
                    'turnover_rate': float(stock_info['换手率'].values[0]) if '换手率' in stock_info.columns else 0,
                }
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            else:
                # 返回None表示获取失败
                return None


def check_single_day_limit_up(stock_code, zt_date_str):
    """
    检查是否为连续涨停1天（非连板）
    
    Returns:
        (bool, str): 是否符合，原因说明
    """
    # 获取前一交易日
    zt_date = datetime.strptime(zt_date_str, '%Y%m%d')
    prev_date = get_previous_trading_day(zt_date)
    prev_date_str = prev_date.strftime('%Y%m%d')
    
    # 获取前一交易日的涨停池
    prev_zt_df = get_zt_pool_with_retry(prev_date_str)
    
    if prev_zt_df is None or prev_zt_df.empty:
        # 无法获取前一交易日数据，假设是首次涨停
        return True, "无法获取前一日数据，假设首次涨停"
    
    # 检查该股票是否在前一交易日也涨停
    if stock_code in prev_zt_df['代码'].values:
        return False, "前一日已涨停，属于连板"
    
    return True, "首次涨停"


def analyze_k_line_trend(df):
    """
    分析K线趋势
    
    Returns:
        dict: 趋势分析结果
    """
    if len(df) < 10:
        return {'score': 0, 'trend': '数据不足'}
    
    score = 0
    details = {}
    
    # 1. 均线排列（30分）
    ma5 = df['收盘'].tail(5).mean()
    ma10 = df['收盘'].tail(10).mean()
    ma20 = df['收盘'].tail(20).mean()
    
    if ma5 > ma10 > ma20:
        ma_score = 30
        details['均线'] = "多头排列"
    elif ma5 > ma10:
        ma_score = 20
        details['均线'] = "短期向上"
    elif ma10 > ma20:
        ma_score = 15
        details['均线'] = "中期向上"
    else:
        ma_score = 5
        details['均线'] = "趋势不明"
    
    score += ma_score
    
    # 2. 近期涨幅（25分）
    recent_closes = df['收盘'].tail(5).values
    recent_pct = sum([(recent_closes[i] - recent_closes[i-1]) / recent_closes[i-1] 
                     for i in range(1, len(recent_closes))])
    
    if recent_pct > 15:
        pct_score = 25
        details['近期涨幅'] = f"强势上涨{recent_pct:.2f}%"
    elif recent_pct > 5:
        pct_score = 20
        details['近期涨幅'] = f"稳步上涨{recent_pct:.2f}%"
    elif recent_pct > 0:
        pct_score = 15
        details['近期涨幅'] = f"小幅上涨{recent_pct:.2f}%"
    elif recent_pct > -5:
        pct_score = 10
        details['近期涨幅'] = f"震荡整理{recent_pct:.2f}%"
    else:
        pct_score = 5
        details['近期涨幅'] = f"下跌{recent_pct:.2f}%"
    
    score += pct_score
    
    # 3. 成交量趋势（25分）
    recent_vol = df['成交量'].tail(5).mean()
    earlier_vol = df['成交量'].tail(20).head(15).mean()
    
    if recent_vol > earlier_vol * 2:
        vol_score = 25
        details['成交量'] = "大幅放量"
    elif recent_vol > earlier_vol * 1.5:
        vol_score = 20
        details['成交量'] = "明显放量"
    elif recent_vol > earlier_vol:
        vol_score = 15
        details['成交量'] = "温和放量"
    else:
        vol_score = 10
        details['成交量'] = "缩量"
    
    score += vol_score
    
    # 4. 突破形态（20分）
    # 检查是否突破前期高点
    high_20 = df['最高'].tail(20).max()
    current_close = df.iloc[-1]['收盘']
    
    if current_close > high_20 * 0.98:  # 接近或突破20日高点
        breakthrough_score = 20
        details['突破'] = "突破前期高点"
    elif current_close > df['收盘'].tail(20).mean():
        breakthrough_score = 15
        details['突破'] = "站上均线"
    else:
        breakthrough_score = 10
        details['突破'] = "未突破"
    
    score += breakthrough_score
    
    return {'score': score, 'trend': details, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20}


def analyze_future_outlook(df, trend_score):
    """
    分析未来走法
    
    Returns:
        dict: 未来展望分析
    """
    if len(df) < 10:
        return {'score': 0, 'outlook': '数据不足'}
    
    score = 0
    details = {}
    
    # 1. 上涨空间（30分）
    # 计算距20日均线的距离
    ma20 = df['收盘'].tail(20).mean()
    current_price = df.iloc[-1]['收盘']
    space_pct = (current_price - ma20) / ma20
    
    if space_pct < 0.05:  # 距离5%以内，上涨空间大
        space_score = 30
        details['上涨空间'] = "充足"
    elif space_pct < 0.10:
        space_score = 20
        details['上涨空间'] = "适中"
    elif space_pct < 0.20:
        space_score = 10
        details['上涨空间'] = "一般"
    else:
        space_score = 5
        details['上涨空间'] = "受限"
    
    score += space_score
    
    # 2. 支撑位强度（35分）
    # 检查是否有强支撑
    recent_low = df['最低'].tail(10).min()
    support_strength = (current_price - recent_low) / current_price
    
    if support_strength < 0.03:  # 距离支撑位近
        support_score = 35
        details['支撑位'] = "强支撑"
    elif support_strength < 0.08:
        support_score = 25
        details['支撑位'] = "有效支撑"
    elif support_strength < 0.15:
        support_score = 15
        details['支撑位'] = "一般支撑"
    else:
        support_score = 5
        details['支撑位'] = "支撑薄弱"
    
    score += support_score
    
    # 3. 趋势延续性（35分）
    # 结合趋势评分
    if trend_score >= 80:
        continuation_score = 35
        details['延续性'] = "强势延续"
    elif trend_score >= 60:
        continuation_score = 25
        details['延续性'] = "良好延续"
    elif trend_score >= 40:
        continuation_score = 15
        details['延续性'] = "一般延续"
    else:
        continuation_score = 5
        details['延续性'] = "延续性弱"
    
    score += continuation_score
    
    return {'score': score, 'outlook': details}


def analyze_sentiment(df, stock_code, zt_df_row):
    """
    分析市场情绪
    
    Returns:
        dict: 情绪分析
    """
    score = 0
    details = {}
    
    # 1. 涨停原因（30分）
    # 从涨停池数据中获取涨停原因
    reason = zt_df_row.get('涨停原因', '')
    hot_keywords = ['AI', '新能源', '芯片', '半导体', '汽车', '医药', '消费', '科技', '数字经济']
    
    reason_lower = reason.lower()
    has_hot = any(keyword in reason_lower for keyword in hot_keywords)
    
    if has_hot and reason:
        sentiment_score = 30
        details['热点'] = reason
    elif reason:
        sentiment_score = 20
        details['热点'] = reason
    else:
        sentiment_score = 10
        details['热点'] = "无明显热点"
    
    score += sentiment_score
    
    # 2. 换手率（30分）
    turnover = float(zt_df_row.get('换手率', 0))
    
    if 5 <= turnover <= 15:
        turnover_score = 30
        details['换手率'] = f"活跃{turnover:.2f}%"
    elif turnover > 15:
        turnover_score = 25
        details['换手率'] = f"高换手{turnover:.2f}%"
    elif turnover >= 3:
        turnover_score = 20
        details['换手率'] = f"适中{turnover:.2f}%"
    else:
        turnover_score = 10
        details['换手率'] = f"低迷{turnover:.2f}%"
    
    score += turnover_score
    
    # 3. 封单强度（40分）
    # 根据涨停时间和成交额判断
    amount = float(zt_df_row.get('成交额', 0))
    
    if amount > 1000000000:  # 10亿以上
        seal_score = 40
        details['封单'] = "强势封单"
    elif amount > 500000000:  # 5-10亿
        seal_score = 30
        details['封单'] = "较强封单"
    elif amount > 200000000:  # 2-5亿
        seal_score = 20
        details['封单'] = "一般封单"
    else:
        seal_score = 10
        details['封单'] = "封单较弱"
    
    score += seal_score
    
    return {'score': score, 'sentiment': details}


def analyze_fundamentals(fundamentals):
    """
    分析基本面
    
    Returns:
        dict: 基本面分析
    """
    if fundamentals is None:
        return {'score': 0, 'fundamentals': '数据缺失'}
    
    score = 0
    details = {}
    
    # 1. 市值（40分）
    market_cap = fundamentals.get('market_cap', 0)
    
    if market_cap < 5000000000:  # 50亿以下
        cap_score = 40
        details['市值'] = f"{market_cap/100000000:.2f}亿（小盘股）"
    elif market_cap < 10000000000:  # 50-100亿
        cap_score = 30
        details['市值'] = f"{market_cap/100000000:.2f}亿（中小盘）"
    elif market_cap < 20000000000:  # 100-200亿
        cap_score = 20
        details['市值'] = f"{market_cap/100000000:.2f}亿（中盘）"
    else:
        cap_score = 5
        details['市值'] = f"{market_cap/100000000:.2f}亿（大盘）"
    
    score += cap_score
    
    # 2. 股价（30分）
    price = fundamentals.get('price', 0)
    
    if price < 10:
        price_score = 30
        details['股价'] = f"{price:.2f}元（低价股）"
    elif price < 20:
        price_score = 25
        details['股价'] = f"{price:.2f}元（中低价）"
    elif price < 50:
        price_score = 20
        details['股价'] = f"{price:.2f}元（中价）"
    elif price < 100:
        price_score = 15
        details['股价'] = f"{price:.2f}元（中高价）"
    else:
        price_score = 5
        details['股价'] = f"{price:.2f}元（高价）"
    
    score += price_score
    
    # 3. 市盈率（30分）
    pe_ratio = fundamentals.get('pe_ratio', 0)
    
    if pe_ratio > 0:
        if pe_ratio < 20:
            pe_score = 30
            details['市盈率'] = f"{pe_ratio:.2f}（低估值）"
        elif pe_ratio < 40:
            pe_score = 25
            details['市盈率'] = f"{pe_ratio:.2f}（合理）"
        elif pe_ratio < 80:
            pe_score = 15
            details['市盈率'] = f"{pe_ratio:.2f}（偏高）"
        else:
            pe_score = 5
            details['市盈率'] = f"{pe_ratio:.2f}（高估值）"
    else:
        pe_score = 15
        details['市盈率'] = "亏损或无数据"
    
    score += pe_score
    
    return {'score': score, 'fundamentals': details}


def calculate_simple_score(stock_code, stock_name, zt_df_row):
    """
    当无法获取历史数据时，基于涨停数据进行简单评分
    
    Returns:
        dict: 简单评分结果
    """
    score = 0
    components = {}
    
    # 1. 基于涨停数据评分（50分）
    amount = float(zt_df_row.get('成交额', 0))
    turnover = float(zt_df_row.get('换手率', 0))
    pct_change = float(zt_df_row.get('涨跌幅', 0))
    
    # 成交额评分（20分）
    if amount > 1000000000:
        amount_score = 20
    elif amount > 500000000:
        amount_score = 15
    elif amount > 200000000:
        amount_score = 10
    else:
        amount_score = 5
    score += amount_score
    
    # 换手率评分（15分）
    if 5 <= turnover <= 15:
        turnover_score = 15
    elif turnover > 15:
        turnover_score = 12
    elif turnover >= 3:
        turnover_score = 10
    else:
        turnover_score = 5
    score += turnover_score
    
    # 涨停幅度评分（15分）
    if pct_change >= 9.9:
        pct_score = 15
    elif pct_change >= 9.5:
        pct_score = 12
    else:
        pct_score = 8
    score += pct_score
    
    components['涨停数据'] = {
        'score': score,
        'details': {
            '成交额': f"{amount/100000000:.2f}亿",
            '换手率': f"{turnover:.2f}%",
            '涨幅': f"{pct_change:.2f}%"
        }
    }
    
    # 2. 涨停原因分析（25分）
    reason = zt_df_row.get('涨停原因', '未知')
    hot_keywords = ['AI', '新能源', '芯片', '半导体', '汽车', '医药', '消费', '科技', '数字经济']
    reason_lower = reason.lower()
    has_hot = any(keyword in reason_lower for keyword in hot_keywords)
    
    if has_hot and reason:
        reason_score = 25
        reason_desc = reason
    elif reason:
        reason_score = 15
        reason_desc = reason
    else:
        reason_score = 10
        reason_desc = "无明显原因"
    
    score += reason_score
    components['热点题材'] = {
        'score': reason_score,
        'reason': reason_desc
    }
    
    # 3. 股票基本信息（25分）
    # 尝试获取基本面数据
    fundamentals = get_stock_fundamentals_with_retry(stock_code)
    if fundamentals:
        price = fundamentals.get('price', 0)
        market_cap = fundamentals.get('market_cap', 0)
        
        # 股价评分（10分）
        if price < 10:
            price_score = 10
        elif price < 20:
            price_score = 8
        elif price < 50:
            price_score = 6
        else:
            price_score = 4
        score += price_score
        
        # 市值评分（15分）
        if market_cap < 5000000000:
            cap_score = 15
        elif market_cap < 10000000000:
            cap_score = 12
        elif market_cap < 20000000000:
            cap_score = 10
        else:
            cap_score = 8
        score += cap_score
        
        components['基本信息'] = {
            'score': price_score + cap_score,
            'details': {
                '股价': f"{price:.2f}元",
                '市值': f"{market_cap/100000000:.2f}亿"
            }
        }
    else:
        # 无法获取基本面数据，给基础分
        score += 10
        components['基本信息'] = {
            'score': 10,
            'details': {
                '股价': '未知',
                '市值': '未知'
            }
        }
    
    return {
        'score': score,
        'components': components,
        'fundamentals': fundamentals,
        'is_simple_score': True  # 标记为简单评分
    }


def calculate_composite_score(stock_code, stock_name, zt_date_str, zt_df_row):
    """
    计算综合强度得分
    
    优先尝试获取历史数据进行完整评分，
    如果失败则使用简单评分方案
    """
    total_score = 0
    components = {}
    
    # 1. 获取历史数据
    df = get_stock_history_with_retry(stock_code, days=60)
    if df is None or df.empty:
        print(f"    ⚠ 无法获取历史数据，使用简单评分方案")
        # 使用简单评分方案
        return calculate_simple_score(stock_code, stock_name, zt_df_row)
    
    # 检查数据量是否足够
    if len(df) < 10:
        print(f"    ⚠ 历史数据不足（仅{len(df)}条记录），使用简单评分方案")
        return calculate_simple_score(stock_code, stock_name, zt_df_row)
    
    # 2. K线趋势分析（25%权重）
    trend_analysis = analyze_k_line_trend(df)
    trend_score = trend_analysis['score']
    components['趋势'] = trend_analysis
    total_score += trend_score * 0.25
    
    # 3. 未来走法分析（25%权重）
    outlook_analysis = analyze_future_outlook(df, trend_score)
    outlook_score = outlook_analysis['score']
    components['未来'] = outlook_analysis
    total_score += outlook_score * 0.25
    
    # 4. 情绪分析（25%权重）
    sentiment_analysis = analyze_sentiment(df, stock_code, zt_df_row)
    sentiment_score = sentiment_analysis['score']
    components['情绪'] = sentiment_analysis
    total_score += sentiment_score * 0.25
    
    # 5. 基本面分析（25%权重）
    fundamentals = get_stock_fundamentals_with_retry(stock_code)
    
    fundamental_analysis = analyze_fundamentals(fundamentals)
    fundamental_score = fundamental_analysis['score']
    components['基本面'] = fundamental_analysis
    total_score += fundamental_score * 0.25
    
    # 最终得分（100分制）
    final_score = min(100, total_score)
    
    return {
        'score': final_score,
        'components': components,
        'fundamentals': fundamentals
    }


def analyze_strength_limit_up_stocks(target_date=None, top_n=2):
    """
    分析强度首板战法股票
    
    Args:
        target_date: 目标日期，None表示今天
        top_n: 返回前N只股票
    
    Returns:
        dict: 包含所有股票分析结果和推荐股票
    """
    if target_date is None:
        target_date = datetime.now()
    else:
        target_date = datetime.strptime(target_date, "%Y-%m-%d")
    
    print("=" * 70)
    print("强度首板战法模型")
    print("=" * 70)
    print(f"分析日期: {target_date.strftime('%Y-%m-%d')}")
    print(f"筛选条件:")
    print(f"  1. 连续涨停天数为1天（非连板）")
    print(f"  2. 早盘集合竞价附近买入")
    print(f"  3. 次日收盘前全部卖出")
    print(f"  4. 根据K线趋势、未来走法、情绪、基本面综合分析\n")
    
    # 获取前一交易日
    previous_day = get_previous_trading_day(target_date)
    date_str = previous_day.strftime('%Y%m%d')
    
    print(f"查询 {previous_day.strftime('%Y-%m-%d')} 的涨停股票...")
    
    # 获取涨停池
    zt_df = get_zt_pool_with_retry(date_str)
    
    if zt_df is None or zt_df.empty:
        print("获取涨停池失败，无法分析")
        return {
            'all_stocks': [],
            'top_stocks': [],
            'total_count': 0
        }
    
    print(f"找到 {len(zt_df)} 只涨停股票\n")
    
    # 排除ST、科创板、北交所
    zt_df = zt_df[~zt_df['代码'].str.startswith(('688', '8'))]
    zt_df = zt_df[~zt_df['名称'].str.contains('ST', case=False)]
    
    if zt_df.empty:
        print("筛选后无数据")
        return {
            'all_stocks': [],
            'top_stocks': [],
            'total_count': 0
        }
    
    print(f"筛选后剩余 {len(zt_df)} 只股票\n")
    print("开始分析所有涨停股票...\n")
    
    all_stocks = []
    scanned_count = 0
    qualified_count = 0
    
    # 分析每只股票
    for idx, row in zt_df.iterrows():
        stock_code = row['代码']
        stock_name = row['名称']
        
        scanned_count += 1
        
        # 1. 检查是否为连续涨停1天
        is_single, reason = check_single_day_limit_up(stock_code, date_str)
        
        if not is_single:
            all_stocks.append({
                'stock_code': stock_code,
                'stock_name': stock_name,
                'score': 0,
                'not_qualified': True,
                'reason': reason,
                'details': {
                    'turnover': row.get('换手率', 0),
                    'amount': row.get('成交额', 0),
                    'pct_change': row.get('涨跌幅', 0),
                    'zt_reason': row.get('涨停原因', '未知')
                }
            })
            continue
        
        qualified_count += 1
        print(f"[{qualified_count}/{len(zt_df)}] 分析 {stock_code} {stock_name}...")
        
        # 2. 计算综合强度得分
        score_result = calculate_composite_score(stock_code, stock_name, date_str, row)
        
        # 获取当前价格（优先级：基本面 > 涨停数据 > 默认值）
        fundamentals = score_result.get('fundamentals')
        current_price = fundamentals.get('price', 0) if fundamentals else 0
        
        # 如果基本面获取失败，尝试从涨停数据中获取
        if current_price <= 0:
            current_price = row.get('最新价', 0) if '最新价' in row else row.get('收盘', 0)
        
        # 如果仍然无法获取，使用涨幅和基准价计算
        if current_price <= 0:
            pct_change = row.get('涨跌幅', 9.95)  # 默认涨停涨幅
            # 假设基准价为10元
            current_price = 10.0 * (1 + pct_change / 100)
        
        # 计算买入、卖出建议价格
        buy_price = current_price if current_price > 0 else row.get('最新价', 0) if '最新价' in row else row.get('收盘', 0)
        
        # 如果仍然无法获取价格，使用默认值
        if buy_price <= 0:
            buy_price = 10.0  # 默认10元
        
        stop_loss = buy_price * 0.95  # 止损5%
        target_profit_up = buy_price * 1.08  # 止盈8%
        target_profit_down = buy_price * 0.97  # 风险提示3%
        
        # 计算预测涨跌幅
        forecast_up_pct = ((target_profit_up - buy_price) / buy_price) * 100
        forecast_down_pct = ((target_profit_down - buy_price) / buy_price) * 100
        
        # 添加到结果列表
        stock_info = {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'score': score_result['score'],
            'components': score_result['components'],
            'current_price': current_price,
            'market_cap': fundamentals.get('market_cap', 0) if fundamentals else 0,
            'pe_ratio': fundamentals.get('pe_ratio', 0) if fundamentals else 0,
            'turnover': row.get('换手率', 0),
            'amount': row.get('成交额', 0),
            'zt_reason': row.get('涨停原因', '未知'),
            'is_single_day': True,
            'reason': reason,
            'is_simple_score': score_result.get('is_simple_score', False),
            'not_qualified': False,
            'trading_advice': {
                'buy_price': round(buy_price, 2),
                'stop_loss': round(stop_loss, 2),
                'target_profit_up': round(target_profit_up, 2),
                'target_profit_down': round(target_profit_down, 2),
                'forecast_up_pct': round(forecast_up_pct, 2),
                'forecast_down_pct': round(forecast_down_pct, 2),
                'position': '50%',
                'buy_time': '早盘集合竞价附近（9:25）',
                'sell_time': '次日收盘前'
            },
            'details': {
                'turnover': row.get('换手率', 0),
                'amount': row.get('成交额', 0),
                'pct_change': row.get('涨跌幅', 0),
                'zt_reason': row.get('涨停原因', '未知')
            }
        }
        
        all_stocks.append(stock_info)
        
        score_type = "简单评分" if score_result.get('is_simple_score', False) else "完整评分"
        print(f"    ✓ {score_type} 得分: {score_result['score']:.2f}")
    
    print(f"\n扫描完成！")
    print(f"  - 总扫描股票: {scanned_count}")
    print(f"  - 符合首次涨停: {qualified_count}")
    print(f"  - 完整评分: {len([s for s in all_stocks if not s.get('is_simple_score', False) and not s.get('not_qualified')])}")
    print(f"  - 简单评分: {len([s for s in all_stocks if s.get('is_simple_score', False) and not s.get('not_qualified')])}")
    print(f"  - 不符合条件: {len([s for s in all_stocks if s.get('not_qualified', False)])}\n")
    
    # 按综合得分排序
    qualified_stocks = [s for s in all_stocks if not s.get('not_qualified', False)]
    qualified_stocks.sort(key=lambda x: x['score'], reverse=True)
    
    # 取前N只
    top_stocks = qualified_stocks[:top_n]
    
    return {
        'all_stocks': all_stocks,
        'top_stocks': top_stocks,
        'total_count': len(zt_df)
    }


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='强度首板战法模型')
    parser.add_argument('--date', type=str, default=None,
                        help='分析日期 (YYYY-MM-DD)，默认为今天')
    parser.add_argument('--top-n', type=int, default=2,
                        help='返回前N只股票 (默认: 2)')
    parser.add_argument('--output', type=str, default='./strength_limit_up_result.json',
                        help='输出文件路径')
    
    args = parser.parse_args()
    
    # 执行分析
    result_data = analyze_strength_limit_up_stocks(target_date=args.date, top_n=args.top_n)
    
    all_stocks = result_data.get('all_stocks', [])
    top_stocks = result_data.get('top_stocks', [])
    
    # 输出所有股票信息
    print("\n" + "=" * 70)
    print("所有涨停股票分析结果")
    print("=" * 70)
    
    for idx, stock in enumerate(all_stocks):
        if stock.get('not_qualified', False):
            # 不符合条件的股票
            print(f"\n{idx+1}. {stock['stock_code']} {stock['stock_name']} - ❌ 未入选")
            print(f"   原因: {stock['reason']}")
            print(f"   涨幅: {stock['details']['pct_change']:.2f}% | 换手率: {stock['details']['turnover']:.2f}%")
        else:
            # 符合条件的股票
            score_type = "简单评分" if stock.get('is_simple_score', False) else "完整评分"
            print(f"\n{idx+1}. {stock['stock_code']} {stock['stock_name']} - ✓ 得分: {stock['score']:.2f} ({score_type})")
            print(f"   涨幅: {stock['details']['pct_change']:.2f}% | 换手率: {stock['details']['turnover']:.2f}% | 成交额: {stock['details']['amount']/100000000:.2f}亿")
            print(f"   涨停原因: {stock['details']['zt_reason']}")
    
    # 输出前N名的详细信息
    if top_stocks:
        print("\n" + "=" * 70)
        print(f"强度排名前{len(top_stocks)}只 - 详细分析")
        print("=" * 70)
        
        for idx, stock in enumerate(top_stocks):
            score_type = "简单评分" if stock.get('is_simple_score', False) else "完整评分"
            advice = stock['trading_advice']
            
            print(f"\n{'='*70}")
            print(f"【排名 {idx+1}】{stock['stock_code']} {stock['stock_name']} ({score_type})")
            print(f"{'='*70}")
            print(f"\n  💡 综合得分: {stock['score']:.2f}分")
            print(f"  📊 当前价格: {stock['current_price']:.2f}元")
            print(f"  💰 市值: {stock['market_cap']/100000000:.2f}亿")
            print(f"  📈 市盈率: {stock['pe_ratio']:.2f}")
            print(f"  🔄 换手率: {stock['turnover']:.2f}%")
            print(f"  🎯 涨停原因: {stock['zt_reason']}")
            
            print(f"\n  📌 交易建议:")
            print(f"     买入价格: {advice['buy_price']}元（早盘集合竞价附近）")
            print(f"     止损价格: {advice['stop_loss']}元（-5%）")
            print(f"     目标盈利: {advice['target_profit_up']}元（+{advice['forecast_up_pct']}%）")
            print(f"     风险提示: {advice['target_profit_down']}元（{advice['forecast_down_pct']}%）")
            print(f"     买入仓位: {advice['position']}")
            print(f"     买入时机: {advice['buy_time']}")
            print(f"     卖出时机: {advice['sell_time']}")
            
            print(f"\n  📈 预测:")
            print(f"     预期涨幅: +{advice['forecast_up_pct']}%")
            print(f"     风险提示: {advice['forecast_down_pct']}%")
            print(f"     风险收益比: 1:{abs(advice['forecast_up_pct']/advice['forecast_down_pct']):.1f}")
            
            # 显示详细分析
            components = stock['components']
            if stock.get('is_simple_score', False):
                # 简单评分
                if '涨停数据' in components:
                    print(f"\n  🔥 涨停数据分析: {components['涨停数据'].get('score', 0)}/50")
                    details = components['涨停数据'].get('details', {})
                    for key, value in details.items():
                        print(f"     {key}: {value}")
                
                if '热点题材' in components:
                    print(f"\n  🌟 热点题材分析: {components['热点题材'].get('score', 0)}/25")
                    print(f"     原因: {components['热点题材'].get('reason', '未知')}")
                
                if '基本信息' in components:
                    print(f"\n  📋 基本信息分析: {components['基本信息'].get('score', 0)}/25")
                    details = components['基本信息'].get('details', {})
                    for key, value in details.items():
                        print(f"     {key}: {value}")
            else:
                # 完整评分
                if '趋势' in components:
                    print(f"\n  📊 趋势分析: {components['趋势'].get('score', 0)}/100")
                    trend_details = components['趋势'].get('trend', {})
                    for key, value in trend_details.items():
                        print(f"     {key}: {value}")
                
                if '未来' in components:
                    print(f"\n  🔮 未来展望: {components['未来'].get('score', 0)}/100")
                    outlook_details = components['未来'].get('outlook', {})
                    for key, value in outlook_details.items():
                        print(f"     {key}: {value}")
                
                if '情绪' in components:
                    print(f"\n  💫 情绪分析: {components['情绪'].get('score', 0)}/100")
                    sentiment_details = components['情绪'].get('sentiment', {})
                    for key, value in sentiment_details.items():
                        print(f"     {key}: {value}")
                
                if '基本面' in components:
                    print(f"\n  💎 基本面: {components['基本面'].get('score', 0)}/100")
                    fundamental_details = components['基本面'].get('fundamentals', {})
                    for key, value in fundamental_details.items():
                        print(f"     {key}: {value}")
    
    # 保存结果到JSON
    result = {
        'analysis_date': args.date or datetime.now().strftime('%Y-%m-%d'),
        'model': '强度首板战法',
        'strategy': '连续涨停1天，早盘集合竞价买入，次日收盘前全部卖出',
        'position': '每次买入50%（五层仓）',
        'total_stocks': result_data.get('total_count', 0),
        'qualified_count': len([s for s in all_stocks if not s.get('not_qualified', False)]),
        'all_stocks': [],
        'top_stocks': []
    }
    
    # 添加所有股票信息
    for stock in all_stocks:
        if stock.get('not_qualified', False):
            result['all_stocks'].append({
                'stock_code': stock['stock_code'],
                'stock_name': stock['stock_name'],
                'score': 0,
                'not_qualified': True,
                'reason': stock['reason'],
                'details': stock['details']
            })
        else:
            result['all_stocks'].append({
                'stock_code': stock['stock_code'],
                'stock_name': stock['stock_name'],
                'score': float(stock['score']),
                'score_type': '简单评分' if stock.get('is_simple_score', False) else '完整评分',
                'not_qualified': False,
                'details': stock['details']
            })
    
    # 添加前N名股票信息
    for stock in top_stocks:
        advice = stock['trading_advice']
        result['top_stocks'].append({
            'rank': len(result['top_stocks']) + 1,
            'stock_code': stock['stock_code'],
            'stock_name': stock['stock_name'],
            'score': float(stock['score']),
            'score_type': '简单评分' if stock.get('is_simple_score', False) else '完整评分',
            'current_price': float(stock['current_price']),
            'market_cap': float(stock['market_cap']),
            'pe_ratio': float(stock['pe_ratio']),
            'turnover': float(stock['turnover']),
            'amount': float(stock['amount']),
            'zt_reason': stock['zt_reason'],
            'trading_advice': advice,
            'details': stock['components']
        })
    
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*70}")
    print(f"✅ 结果已保存到: {args.output}")
    print(f"🎉 分析完成！")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
