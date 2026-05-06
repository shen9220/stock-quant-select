#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化选股模型脚本
策略：缩量回调至10日线买入，历史放量大阳线支撑
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd


class StockQuantSelector:
    """量化选股器"""

    def __init__(
        self,
        select_date: str,
        ma10_diff: float = 0.02,
        big_rise_threshold: float = 0.06,
        volume_multiple: float = 1.5,
        lookback_days: int = 20,
        limit_up_threshold: float = 0.095,
    ):
        """
        初始化选股器

        Args:
            select_date: 选股日期 (YYYY-MM-DD)
            ma10_diff: 10日线回调阈值 (±范围)
            big_rise_threshold: 大阳线涨幅阈值
            volume_multiple: 放量倍数
            lookback_days: 历史回看天数
            limit_up_threshold: 涨停板阈值
        """
        self.select_date = select_date
        self.ma10_diff = ma10_diff
        self.big_rise_threshold = big_rise_threshold
        self.volume_multiple = volume_multiple
        self.lookback_days = lookback_days
        self.limit_up_threshold = limit_up_threshold

    def get_stock_list(self) -> pd.DataFrame:
        """获取A股全市场股票列表"""
        try:
            # 获取A股实时行情
            df = ak.stock_zh_a_spot_em()

            # 过滤ST股票
            df = df[~df['名称'].str.contains('ST')]

            # 只保留主板股票
            df = df[df['代码'].str.match(r'^\d{6}$')]

            return df
        except Exception as e:
            print(f"获取股票列表失败: {e}", file=sys.stderr)
            return pd.DataFrame()

    def get_stock_data(self, stock_code: str) -> Optional[pd.DataFrame]:
        """
        获取单只股票的历史数据

        Args:
            stock_code: 股票代码

        Returns:
            包含历史数据的DataFrame，失败返回None
        """
        try:
            # 计算需要获取的数据天数
            start_date = (datetime.strptime(self.select_date, "%Y-%m-%d") - timedelta(days=self.lookback_days + 30)).strftime("%Y-%m-%d")

            # 获取历史数据
            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=start_date, adjust="qfq")

            if df.empty:
                return None

            # 重命名列以便处理
            df = df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '最高': 'high',
                '最低': 'low',
                '收盘': 'close',
                '成交量': 'volume',
                '换手率': 'turnover'
            })

            # 确保日期格式正确
            df['date'] = pd.to_datetime(df['date'])

            return df
        except Exception as e:
            print(f"获取股票 {stock_code} 数据失败: {e}", file=sys.stderr)
            return None

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算技术指标

        Args:
            df: 包含OHLCV数据的DataFrame

        Returns:
            添加了技术指标的DataFrame
        """
        # 计算移动平均线
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma10'] = df['close'].rolling(window=10).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()

        # 计算成交量均线
        df['ma_volume_5'] = df['volume'].rolling(window=5).mean()
        df['ma_volume_20'] = df['volume'].rolling(window=20).mean()

        # 计算涨跌幅
        df['pct_change'] = df['close'].pct_change()

        # 计算MA10斜率（上升趋势）
        df['ma10_slope'] = df['ma10'].diff()

        return df

    def check_conditions(self, df: pd.DataFrame, idx: int) -> Tuple[bool, List[str], float]:
        """
        检查股票是否满足所有买入条件

        Args:
            df: 包含技术指标的DataFrame
            idx: 选股日期在DataFrame中的索引

        Returns:
            (是否满足条件, 满足的条件列表, 评分)
        """
        conditions_met = []
        score = 0.0

        # 确保有足够的数据
        if idx < self.lookback_days:
            return False, conditions_met, score

        today = df.iloc[idx]
        yesterday = df.iloc[idx - 1]

        # 条件1: 当日收阴线 (close < open)
        if today['close'] < today['open']:
            conditions_met.append("收阴线")
            score += 10
        else:
            return False, conditions_met, score

        # 条件2: 历史有放量大阳线或涨停板（过去lookback_days天内）
        history_df = df.iloc[idx - self.lookback_days:idx]
        has_big_rise = False

        for _, row in history_df.iterrows():
            rise_pct = (row['close'] - row['close'].shift(1)) / row['close'].shift(1) if pd.notna(row['close'].shift(1)) else 0

            # 大阳线或涨停板
            if rise_pct >= max(self.big_rise_threshold, self.limit_up_threshold):
                # 检查是否放量
                if row['volume'] > row['ma_volume_20'] * self.volume_multiple:
                    has_big_rise = True
                    # 评分：涨幅越大，放量越明显，分数越高
                    rise_score = min(rise_pct * 100, 30)
                    volume_score = min((row['volume'] / row['ma_volume_20'] - 1) * 10, 20)
                    score += rise_score + volume_score
                    break

        if has_big_rise:
            conditions_met.append("历史放量大阳线或涨停板")
        else:
            return False, conditions_met, score

        # 条件3: 当日缩量 (volume < volume_prev)
        if today['volume'] < yesterday['volume']:
            conditions_met.append("缩量")
            score += 15
        else:
            return False, conditions_met, score

        # 条件4: 股价回调至10日线附近 (±ma10_diff)
        ma10_lower = today['ma10'] * (1 - self.ma10_diff)
        ma10_upper = today['ma10'] * (1 + self.ma10_diff)

        if ma10_lower <= today['close'] <= ma10_upper:
            conditions_met.append("回调至10日线")
            # 评分：越接近10日线中心，分数越高
            distance = abs(today['close'] - today['ma10']) / today['ma10']
            proximity_score = max(0, 20 - distance * 1000)
            score += proximity_score
        else:
            return False, conditions_met, score

        # 条件5: MA10方向向上
        if today['ma10'] > yesterday['ma10']:
            conditions_met.append("MA10向上")
            # 评分：上升趋势越强，分数越高
            slope = today['ma10_slope'] / today['ma10'] if today['ma10'] > 0 else 0
            slope_score = min(slope * 1000, 15)
            score += slope_score
        else:
            return False, conditions_met, score

        return True, conditions_met, score

    def select_stock(self) -> Optional[Dict]:
        """
        执行选股流程

        Returns:
            选股结果字典，无符合条件的股票时返回None
        """
        print(f"开始选股，日期: {self.select_date}")

        # 获取股票列表
        stock_list = self.get_stock_list()
        if stock_list.empty:
            print("未能获取股票列表", file=sys.stderr)
            return None

        print(f"获取到 {len(stock_list)} 只股票，开始筛选...")

        qualified_stocks = []

        # 遍历所有股票
        for idx, row in stock_list.iterrows():
            stock_code = row['代码']
            stock_name = row['名称']

            # 获取历史数据
            df = self.get_stock_data(stock_code)
            if df is None or df.empty:
                continue

            # 计算技术指标
            df = self.calculate_indicators(df)

            # 找到选股日期
            select_date_dt = pd.to_datetime(self.select_date)
            if select_date_dt not in df['date'].values:
                continue

            idx_date = df[df['date'] == select_date_dt].index[0]

            # 检查条件
            is_qualified, conditions, score = self.check_conditions(df, idx_date)

            if is_qualified:
                qualified_stocks.append({
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'select_date': self.select_date,
                    'buy_price': float(df.loc[idx_date, 'close']),
                    'buy_time': '14:50',
                    'ma10': float(df.loc[idx_date, 'ma10']),
                    'ma10_slope': float(df.loc[idx_date, 'ma10_slope']),
                    'conditions_met': conditions,
                    'score': score,
                    'signals': self._generate_signals(conditions, df.loc[idx_date])
                })

                print(f"  ✓ {stock_code} {stock_name} - 评分: {score:.1f}")

        # 如果没有符合条件的股票
        if not qualified_stocks:
            print("当日无符合所有条件的股票")
            return None

        # 按评分排序，选择最高分的一只
        qualified_stocks.sort(key=lambda x: x['score'], reverse=True)
        best_stock = qualified_stocks[0]

        print(f"\n✓ 选股完成！推荐股票: {best_stock['stock_code']} {best_stock['stock_name']}")
        print(f"  评分: {best_stock['score']:.1f}")
        print(f"  满足条件: {', '.join(best_stock['conditions_met'])}")

        return best_stock

    def _generate_signals(self, conditions: List[str], today_row: pd.Series) -> str:
        """生成信号描述"""
        signal_parts = []

        if "历史放量大阳线或涨停板" in conditions:
            signal_parts.append("历史放量突破")
        if "缩量" in conditions:
            signal_parts.append("缩量整理")
        if "回调至10日线" in conditions:
            signal_parts.append("回调10日线")
        if "MA10向上" in conditions:
            signal_parts.append("趋势向上")

        return "，".join(signal_parts) if signal_parts else "多信号共振"

    def save_result(self, result: Dict, output_path: str):
        """保存选股结果到文件"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存至: {output_path}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='量化选股模型')
    parser.add_argument('--date', type=str, default=None,
                        help='选股日期 (YYYY-MM-DD)，默认为最新交易日')
    parser.add_argument('--output', type=str, default='./stock_select_result.json',
                        help='输出文件路径')
    parser.add_argument('--ma10-diff', type=float, default=0.02,
                        help='10日线回调阈值 (默认: 0.02，即±2%%)')
    parser.add_argument('--big-rise-threshold', type=float, default=0.06,
                        help='大阳线涨幅阈值 (默认: 0.06，即6%%)')
    parser.add_argument('--volume-multiple', type=float, default=1.5,
                        help='放量倍数 (默认: 1.5)')
    parser.add_argument('--lookback-days', type=int, default=20,
                        help='历史回看天数 (默认: 20)')
    parser.add_argument('--limit-up-threshold', type=float, default=0.095,
                        help='涨停板阈值 (默认: 0.095，即9.5%%)')
    parser.add_argument('--top-n', type=int, default=None,
                        help='返回前N只股票（兼容参数）')

    args = parser.parse_args()

    # 处理默认日期
    if args.date is None:
        # 默认使用最新交易日（简化处理，使用昨天）
        args.date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # 创建选股器
    selector = StockQuantSelector(
        select_date=args.date,
        ma10_diff=args.ma10_diff,
        big_rise_threshold=args.big_rise_threshold,
        volume_multiple=args.volume_multiple,
        lookback_days=args.lookback_days,
        limit_up_threshold=args.limit_up_threshold,
    )

    # 执行选股
    result = selector.select_stock()

    # 保存结果
    if result is None:
        # 即使没有选中股票，也输出空结果
        empty_result = {
            "stock_code": "",
            "stock_name": "",
            "select_date": args.date,
            "buy_price": 0,
            "buy_time": "",
            "ma10": 0,
            "ma10_slope": 0,
            "conditions_met": [],
            "score": 0,
            "signals": "当日无符合条件的股票"
        }
        selector.save_result(empty_result, args.output)
    else:
        selector.save_result(result, args.output)


if __name__ == "__main__":
    main()
