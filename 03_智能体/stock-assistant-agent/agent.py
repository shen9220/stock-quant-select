#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能选股助手 - 智能体
基于A股市场数据的智能选股工具
"""

import json
import sys
import os
import io
from pathlib import Path

# 设置标准输出为 UTF-8 编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加技能路径
SKILL_PATH = Path(__file__).parent / "downloaded_skills" / "stock-assistant"
sys.path.insert(0, str(SKILL_PATH))


class StockAssistantAgent:
    """智能选股助手智能体"""

    def __init__(self, config_path=None):
        """初始化智能体"""
        if config_path is None:
            config_path = Path(__file__).parent / "config.json"

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"❌ 配置文件读取失败: {e}")
            sys.exit(1)

        self.skill_path = self.config['skill_path']
        self.trigger_words = self.config['config']['trigger_words']
        self.scoring_config = self.config['config']['scoring']

        print(f"✅ 智能选股助手已加载")
        print(f"   版本: {self.config['version']}")
        print(f"   描述: {self.config['description']}")

    def is_triggered(self, user_input):
        """判断用户输入是否触发"""
        for trigger in self.trigger_words:
            if trigger.lower() in user_input.lower():
                return True
        return False

    def _detect_intent(self, user_input):
        """检测用户意图"""
        user_input = user_input.lower()

        # 高股息选股（优先匹配，因为包含"找"）
        if any(word in user_input for word in ['高股息', '分红', '股息率']):
            return 'high_dividend'

        # 热门板块
        if any(word in user_input for word in ['热门', '热点', '板块']):
            return 'hot_sectors'

        # 智能选股
        if any(word in user_input for word in ['选股', '推荐', '筛选', '低估值']):
            return 'select_stocks'

        # 个股分析
        if any(word in user_input for word in ['分析', '怎么样', '好不好', '能买']):
            return 'analyze_stock'

        # 找（通用匹配，如果没有更具体的意图）
        if '找' in user_input:
            return 'select_stocks'

        return 'general'

    def _extract_stock_info(self, user_input):
        """提取股票信息"""
        import re

        # 提取股票代码（6位数字）
        code_match = re.search(r'\b\d{6}\b', user_input)
        if code_match:
            return {"code": code_match.group()}

        # 简单的名称提取（示例）
        if "工商银行" in user_input:
            return {"name": "工商银行", "code": "601398"}
        elif "建设银行" in user_input:
            return {"name": "建设银行", "code": "601939"}
        elif "农业银行" in user_input:
            return {"name": "农业银行", "code": "601288"}
        elif "比亚迪" in user_input:
            return {"name": "比亚迪", "code": "002594"}
        elif "贵州茅台" in user_input or "茅台" in user_input:
            return {"name": "贵州茅台", "code": "600519"}

        return {"name": "未知股票"}

    def _general_response(self, user_input):
        """通用响应"""
        print("\n💡 智能选股助手")
        print("我可以帮您：")
        print("  • 智能选股 - 按行业、市值、估值筛选股票")
        print("  • 个股分析 - 分析具体股票的投资价值")
        print("  • 高股息选股 - 寻找高分红股票")
        print("  • 热门板块 - 了解当前市场热点")
        print("\n示例：")
        print("  • 帮我选几只银行股")
        print("  • 分析一下工商银行")
        print("  • 找几只股息率高的股票")
        print("  • 最近什么板块比较热门？")

    def _mock_select_stocks(self, criteria=None):
        """模拟智能选股"""
        return [
            {
                "code": "601398",
                "name": "工商银行",
                "price": 5.20,
                "pe": 5.2,
                "valuation_score": 88,
                "technical_score": 75,
                "overall_score": 81.5,
                "recommendation": "⭐⭐⭐⭐⭐"
            },
            {
                "code": "601939",
                "name": "建设银行",
                "price": 6.10,
                "pe": 4.8,
                "valuation_score": 90,
                "technical_score": 72,
                "overall_score": 81.0,
                "recommendation": "⭐⭐⭐⭐⭐"
            },
            {
                "code": "601288",
                "name": "农业银行",
                "price": 3.80,
                "pe": 4.5,
                "valuation_score": 92,
                "technical_score": 70,
                "overall_score": 81.0,
                "recommendation": "⭐⭐⭐⭐⭐"
            }
        ]

    def _mock_analyze_stock(self, stock_info):
        """模拟个股分析"""
        return {
            "code": stock_info.get("code", "未知"),
            "name": stock_info.get("name", "未知"),
            "price": 5.20,
            "pe": 5.2,
            "pb": 0.6,
            "market_cap": 1800000,  # 亿
            "valuation_score": 88,
            "technical_score": 75,
            "overall_score": 81.5,
            "recommendation": "推荐买入",
            "reasons": [
                "估值处于历史低位",
                "技术面显示企稳迹象",
                "基本面稳健，盈利能力强"
            ]
        }

    def _mock_high_dividend(self):
        """模拟高股息选股"""
        return [
            {
                "code": "601398",
                "name": "工商银行",
                "dividend_rate": 5.2,
                "valuation_score": 88,
                "score": 90
            },
            {
                "code": "601088",
                "name": "中国神华",
                "dividend_rate": 6.5,
                "valuation_score": 82,
                "score": 87
            }
        ]

    def _mock_hot_sectors(self):
        """模拟热门板块"""
        return [
            {
                "name": "新能源",
                "change": 2.5,
                "heat": "🔥🔥🔥",
                "stocks": ["宁德时代", "比亚迪", "隆基绿能"]
            },
            {
                "name": "人工智能",
                "change": 3.2,
                "heat": "🔥🔥🔥🔥",
                "stocks": ["科大讯飞", "海康威视", "浪潮信息"]
            },
            {
                "name": "半导体",
                "change": 4.1,
                "heat": "🔥🔥🔥🔥🔥",
                "stocks": ["中芯国际", "韦尔股份", "北方华创"]
            }
        ]

    def _print_stock_list(self, stocks, title="股票列表"):
        """打印股票列表"""
        print(f"\n🎯 {title}")
        print("-" * 60)
        print(f"找到 {len(stocks)} 只符合条件的股票：\n")

        for idx, stock in enumerate(stocks, 1):
            print(f"{idx}. {stock['name']} ({stock['code']})")
            print(f"   当前价格: ¥{stock['price']}")
            print(f"   PE: {stock['pe']}倍")
            print(f"   估值评分: {stock['valuation_score']}/100")
            print(f"   技术评分: {stock['technical_score']}/100")
            print(f"   综合评分: {stock['overall_score']}/100")
            print(f"   推荐: {stock['recommendation']}")
            print()

    def _print_risk_warning(self):
        """打印风险提示"""
        print("=" * 60)
        print("⚠️  风险提示：")
        for msg in self.config['config']['risk_warning']['messages']:
            print(f"  • {msg}")
        print("=" * 60)

    def process(self, user_input):
        """处理用户输入"""
        try:
            # 标准化输入
            user_input = str(user_input).strip()

            # 检查是否为空
            if not user_input:
                return

            # 检测意图
            intent = self._detect_intent(user_input)
            print(f"\n📊 正在处理: {user_input}")
            print("=" * 60)

            # 根据意图处理
            if intent == 'select_stocks':
                criteria = {}
                stocks = self._mock_select_stocks(criteria)
                self._print_stock_list(stocks, "智能选股分析")
                self._print_risk_warning()

            elif intent == 'analyze_stock':
                stock_info = self._extract_stock_info(user_input)
                analysis = self._mock_analyze_stock(stock_info)

                print(f"\n🎯 个股分析: {analysis['name']} ({analysis['code']})")
                print("-" * 60)
                print(f"当前价格: ¥{analysis['price']}")
                print(f"PE: {analysis['pe']}倍")
                print(f"PB: {analysis['pb']}倍")
                print(f"总市值: {analysis['market_cap']}亿")
                print(f"\n评分：")
                print(f"  估值评分: {analysis['valuation_score']}/100")
                print(f"  技术评分: {analysis['technical_score']}/100")
                print(f"  综合评分: {analysis['overall_score']}/100")
                print(f"\n投资建议: {analysis['recommendation']}")
                print(f"\n理由：")
                for reason in analysis['reasons']:
                    print(f"  • {reason}")

                self._print_risk_warning()

            elif intent == 'high_dividend':
                stocks = self._mock_high_dividend()
                print(f"\n🎯 高股息选股")
                print("-" * 60)
                print(f"找到 {len(stocks)} 只高股息股票：\n")

                for idx, stock in enumerate(stocks, 1):
                    print(f"{idx}. {stock['name']} ({stock['code']})")
                    print(f"   股息率: {stock['dividend_rate']}%")
                    print(f"   估值评分: {stock['valuation_score']}/100")
                    print(f"   综合评分: {stock['score']}/100")
                    print()

                self._print_risk_warning()

            elif intent == 'hot_sectors':
                sectors = self._mock_hot_sectors()
                print(f"\n🎯 热门板块分析")
                print("-" * 60)
                print(f"当前热门板块：\n")

                for sector in sectors:
                    print(f"📈 {sector['name']}")
                    print(f"   涨幅: {sector['change']}%")
                    print(f"   热度: {sector['heat']}")
                    print(f"   代表股票: {', '.join(sector['stocks'])}")
                    print()

            else:
                self._general_response(user_input)

        except Exception as e:
            print(f"\n❌ 处理错误: {e}")
            print("请尝试重新输入")

    def run_interactive(self):
        """运行交互模式"""
        print("\n🚀 智能选股助手已启动")
        print("=" * 60)
        print("输入您的问题，输入 'exit' 退出\n")

        while True:
            try:
                # 正确处理 UTF-8 编码的输入
                try:
                    user_input = input("\n📝 您的问题: ").strip()
                except (UnicodeDecodeError, UnicodeError):
                    # 如果遇到编码错误，使用安全的方式读取
                    user_input = sys.stdin.readline().strip()

                if user_input.lower() in ['exit', 'quit', '退出']:
                    print("\n👋 感谢使用智能选股助手！")
                    break

                if not user_input:
                    continue

                # 处理用户输入
                self.process(user_input)

            except KeyboardInterrupt:
                print("\n\n👋 感谢使用智能选股助手！")
                break
            except Exception as e:
                print(f"\n❌ 发生错误: {str(e)}")
                print("请尝试重新输入")


def main():
    """主函数"""
    try:
        # 创建智能体
        agent = StockAssistantAgent()

        # 运行交互模式
        agent.run_interactive()
    except KeyboardInterrupt:
        print("\n\n👋 感谢使用智能选股助手！")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
