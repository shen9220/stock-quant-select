#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票量化模型一键运行工具
支持多个量化模型的选择和运行
"""

import os
import sys
import subprocess
from datetime import datetime


class ModelRunner:
    """量化模型运行器"""

    def __init__(self):
        # 获取项目根目录
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        # 获取项目根目录的父目录
        self.project_root = os.path.dirname(self.base_dir)

        self.models = {
            '1': {
                'name': '强度首板战法',
                'path': os.path.join(self.project_root, '01_量化模型', '首板战法', 'up.py'),
                'description': '首次涨停股票分析，早盘集合竞价买入，次日收盘前卖出',
                'params': ['--date', 'today', '--top-n', '2']
            },
            '2': {
                'name': '涨停三阴法',
                'path': os.path.join(self.project_root, '01_量化模型', '三阴战法', 'pop3.py'),
                'description': '首次涨停后阴跌战法，涨停后3天阴跌不破底',
                'params': ['--date', 'today', '--top-n', '5']
            },
            '3': {
                'name': '隔夜战法（深度）',
                'path': os.path.join(self.project_root, '01_量化模型', '隔夜战法', 'deep.py'),
                'description': '买阴不买阳，深度选股模型',
                'params': ['--date', 'today', '--mode', 'buy_signals']
            },
            '4': {
                'name': '隔夜战法（股票）',
                'path': os.path.join(self.project_root, '01_量化模型', '隔夜战法', 'stock_quant_select.py'),
                'description': '隔夜选股模型',
                'params': ['--date', 'today', '--top-n', '2']
            }
        }

    def print_menu(self):
        """打印主菜单"""
        print("=" * 70)
        print("                    股票量化模型一键运行工具".center(70))
        print("=" * 70)
        print()
        print("请选择要运行的模型：")
        print()

        for key, model in self.models.items():
            print(f"  [{key}] {model['name']}")
            print(f"      {model['description']}")
            print()

        print("  [6] 运行所有模型")
        print("      依次运行所有量化模型")
        print()
        print("  [0] 退出程序")
        print("=" * 70)

    def select_date(self):
        """选择日期"""
        print()
        print("请选择日期：")
        print("  [1] 今天")
        print("  [2] 昨天")
        print("  [3] 自定义日期 (YYYY-MM-DD)")

        choice = input("请输入选项（1-3）: ").strip()

        if choice == '1':
            return datetime.now().strftime('%Y-%m-%d')
        elif choice == '2':
            from datetime import timedelta
            return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        elif choice == '3':
            date_str = input("请输入日期 (YYYY-MM-DD): ").strip()
            return date_str
        else:
            print("无效选项，使用今天")
            return datetime.now().strftime('%Y-%m-%d')

    def run_model(self, model_key, date):
        """运行单个模型"""
        if model_key not in self.models:
            print(f"❌ 无效的模型选择: {model_key}")
            return False

        model = self.models[model_key]

        print()
        print("=" * 70)
        print(f"运行: {model['name']}")
        print("=" * 70)
        print(f"  日期: {date}")
        print(f"  路径: {model['path']}")
        print()

        # 检查文件是否存在
        if not os.path.exists(model['path']):
            print(f"❌ 文件不存在: {model['path']}")
            return False

        # 构建命令
        cmd = ['python3', model['path']] + model['params']

        # 替换日期参数
        cmd = [arg if arg != 'today' else date for arg in cmd]

        try:
            # 运行模型（添加超时控制，最多3分钟）
            result = subprocess.run(cmd, cwd=os.path.dirname(model['path']),
                                  capture_output=True, text=True, timeout=180)
            print(result.stdout)
            if result.stderr:
                print("错误信息:", result.stderr)

            if result.returncode == 0:
                print(f"✅ {model['name']} 运行完成")
                return True
            else:
                print(f"❌ {model['name']} 运行失败")
                return False
        except subprocess.TimeoutExpired:
            print(f"❌ {model['name']} 运行超时（超过3分钟）")
            return False
        except Exception as e:
            print(f"❌ 运行出错: {str(e)}")
            return False

    def run_all_models(self, date):
        """运行所有模型"""
        print()
        print("=" * 70)
        print("开始运行所有模型")
        print("=" * 70)
        print()

        success_count = 0
        for key in sorted(self.models.keys()):
            if self.run_model(key, date):
                success_count += 1
            print()

        print("=" * 70)
        print(f"所有模型运行完成: {success_count}/{len(self.models)} 成功")
        print("=" * 70)

    def run(self):
        """主运行逻辑"""
        while True:
            self.print_menu()
            choice = input("\n请输入选项（0-6）: ").strip()

            if choice == '0':
                print("\n再见！")
                break

            elif choice == '6':
                # 运行所有模型
                date = self.select_date()
                self.run_all_models(date)

                # 询问是否继续
                continue_choice = input("\n是否继续运行其他模型？(y/n): ").strip().lower()
                if continue_choice != 'y':
                    print("\n再见！")
                    break

            elif choice in self.models:
                # 运行单个模型
                date = self.select_date()
                self.run_model(choice, date)

                # 询问是否继续
                continue_choice = input("\n是否继续运行其他模型？(y/n): ").strip().lower()
                if continue_choice != 'y':
                    print("\n再见！")
                    break

            else:
                print("\n❌ 无效选项，请重新选择")


def main():
    """主函数"""
    print("\n" + "=" * 70)
    print("股票量化模型一键运行工具".center(70))
    print("版本: 2.0.0".center(70))
    print("=" * 70)
    print()

    # 创建运行器
    runner = ModelRunner()

    # 运行
    runner.run()


if __name__ == "__main__":
    main()
