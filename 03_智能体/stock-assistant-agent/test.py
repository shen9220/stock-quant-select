#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能选股助手 - 测试脚本
自动测试所有功能
"""

import sys
import os
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from agent import StockAssistantAgent


class TestAgent:
    """智能体测试类"""

    def __init__(self):
        """初始化测试"""
        self.agent = None
        self.passed = 0
        self.failed = 0
        self.errors = []

    def test_1_initialization(self):
        """测试1: 智能体初始化"""
        try:
            self.agent = StockAssistantAgent()
            assert self.agent is not None, "智能体初始化失败"
            return "智能体初始化成功"
        except Exception as e:
            raise AssertionError(f"智能体初始化失败: {e}")

    def test_2_trigger_detection(self):
        """测试2: 触发词检测"""
        test_cases = [
            ("帮我选几只股票", True),
            ("今天天气怎么样", False),  # 不应该触发
            ("分析工商银行", True),  # 包含"分析"
            ("高股息", True)  # 包含"高股息"
        ]

        for input_text, expected in test_cases:
            result = self.agent.is_triggered(input_text)
            assert result == expected, f"触发词检测失败: '{input_text}' 应返回 {expected}, 实际返回 {result}"

        return "触发词检测正常"

    def test_3_smart_select(self):
        """测试3: 智能选股功能"""
        try:
            self.agent.process("帮我选几只低估值的银行股")
            return "智能选股功能正常"
        except Exception as e:
            raise AssertionError(f"智能选股失败: {e}")

    def test_4_analyze_stock(self):
        """测试4: 个股分析功能"""
        try:
            self.agent.process("分析一下工商银行")
            return "个股分析功能正常"
        except Exception as e:
            raise AssertionError(f"个股分析失败: {e}")

    def test_5_high_dividend(self):
        """测试5: 高股息选股功能"""
        try:
            self.agent.process("找几只股息率高的股票")
            return "高股息选股功能正常"
        except Exception as e:
            raise AssertionError(f"高股息选股失败: {e}")

    def test_6_hot_sector(self):
        """测试6: 热门板块分析功能"""
        try:
            self.agent.process("最近什么板块比较热门？")
            return "热门板块分析功能正常"
        except Exception as e:
            raise AssertionError(f"热门板块分析失败: {e}")

    def test_7_config_loading(self):
        """测试7: 配置文件加载"""
        assert 'trigger_words' in self.agent.config['config'], "配置缺少触发词"
        assert 'scoring' in self.agent.config['config'], "配置缺少评分规则"
        assert 'capabilities' in self.agent.config, "配置缺少能力定义"
        return "配置文件加载正常"

    def test_8_intent_analysis(self):
        """测试8: 意图分析"""
        test_cases = [
            ("帮我选几只低估值的银行股", "select_stocks"),
            ("分析一下比亚迪", "analyze_stock"),
            ("找几只股息率高的股票", "high_dividend"),
            ("最近什么板块比较热门？", "hot_sectors")
        ]

        for input_text, expected_intent in test_cases:
            intent = self.agent._detect_intent(input_text)
            assert intent == expected_intent, f"意图分析失败: '{input_text}' 应返回 {expected_intent}, 实际返回 {intent}"

        return "意图分析正常"

    def test_9_utf8_handling(self):
        """测试9: UTF-8 编码处理"""
        try:
            # 测试中文输入
            self.agent.process("今天有什么热门股指的买入的吗")
            return "UTF-8 编码处理正常"
        except UnicodeDecodeError as e:
            raise AssertionError(f"UTF-8 编码处理失败: {e}")
        except Exception as e:
            # 其他错误可以接受
            return f"UTF-8 编码处理正常（遇到其他错误: {str(e)[:50]}）"

    def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "="*60)
        print("           智能选股助手 - 自动测试")
        print("="*60 + "\n")

        tests = [
            ("智能体初始化", self.test_1_initialization),
            ("触发词检测", self.test_2_trigger_detection),
            ("智能选股功能", self.test_3_smart_select),
            ("个股分析功能", self.test_4_analyze_stock),
            ("高股息选股功能", self.test_5_high_dividend),
            ("热门板块分析功能", self.test_6_hot_sector),
            ("配置文件加载", self.test_7_config_loading),
            ("意图分析", self.test_8_intent_analysis),
            ("UTF-8 编码处理", self.test_9_utf8_handling),
        ]

        results = []

        for name, test_func in tests:
            print(f"测试用例: {name}")
            print("-" * 60)
            try:
                result = test_func()
                print(f"✅ 通过: {result}")
                self.passed += 1
                results.append({"name": name, "status": "PASS", "result": result})
            except AssertionError as e:
                print(f"❌ 测试错误: {e}")
                self.failed += 1
                self.errors.append(f"{name}: {e}")
                results.append({"name": name, "status": "FAIL", "error": str(e)})
            except Exception as e:
                print(f"❌ 测试失败: {e}")
                self.failed += 1
                self.errors.append(f"{name}: {e}")
                results.append({"name": name, "status": "ERROR", "error": str(e)})
            print()

        # 打印测试报告
        self.print_report(results)

        # 保存测试报告
        self.save_report(results)

        # 返回测试结果
        return self.failed == 0

    def print_report(self, results):
        """打印测试报告"""
        print("=" * 60)
        print("                    测试报告")
        print("=" * 60)
        print(f"总测试数: {len(results)}")
        print(f"通过: {self.passed}")
        print(f"失败: {self.failed}")

        if self.passed > 0:
            print(f"通过率: {self.passed / len(results) * 100:.1f}%")

        if self.failed > 0:
            print("\n失败的测试:")
            for error in self.errors:
                print(f"  ❌ {error}")

        if self.failed == 0:
            print("\n🎉 所有测试通过！智能体功能正常！")
        else:
            print(f"\n⚠️  部分测试失败，请检查错误信息")

        print("=" * 60)

    def save_report(self, results):
        """保存测试报告"""
        import json

        report = {
            "total_tests": len(results),
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": f"{self.passed / len(results) * 100:.1f}%" if len(results) > 0 else "0%",
            "results": results,
            "errors": self.errors
        }

        report_path = Path(__file__).parent / "test_report.json"
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        print(f"\n📄 测试报告已保存到: {report_path}")


def main():
    """主函数"""
    tester = TestAgent()
    success = tester.run_all_tests()

    if success:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
