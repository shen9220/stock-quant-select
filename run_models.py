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
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.models = {
            '1': {
                'name': '强度首板战法',
                'path': os.path.join(self.base_dir, '首板战法', 'up.py'),
                'description': '首次涨停股票分析，早盘集合竞价买入，次日收盘前卖出',
                'params': ['--date', 'today', '--top-n', '2']
            },
            '2': {
                'name': '涨停三阴法',
                'path': os.path.join(self.base_dir, '涨停三阴法', 'pop3.py'),
                'description': '首次涨停后阴跌战法，涨停后3天阴跌不破底',
                'params': ['--date', 'today', '--top-n', '5']
            },
            '3': {
                'name': '隔夜战法（深度）',
                'path': os.path.join(self.base_dir, '隔夜战法', 'deep.py'),
                'description': '深度选股模型，买阴不买阳策略',
                'params': ['--date', 'today']
            },
            '4': {
                'name': '隔夜战法（股票）',
                'path': os.path.join(self.base_dir, '隔夜战法', 'stock.py'),
                'description': '隔夜选股模型',
                'params': ['--date', 'today']
            },
            '5': {
                'name': '三阴战法',
                'path': os.path.join(self.base_dir, '三阴战法', 'pop3.py'),
                'description': '三阴战法选股模型',
                'params': ['--date', 'today', '--top-n', '5']
            },
            '6': {
                'name': '运行所有模型',
                'path': 'all',
                'description': '依次运行所有量化模型',
                'params': []
            },
            '0': {
                'name': '退出',
                'path': 'exit',
                'description': '退出程序',
                'params': []
            }
        }
    
    def show_menu(self):
        """显示主菜单"""
        print("=" * 70)
        print(" " * 15 + "股票量化模型一键运行工具")
        print("=" * 70)
        print("\n请选择要运行的模型：\n")
        
        for key, model in self.models.items():
            if key == '0':
                continue
            print(f"  [{key}] {model['name']}")
            print(f"      {model['description']}")
            print()
        
        print(f"  [0] 退出程序")
        print("=" * 70)
    
    def get_date_input(self):
        """获取日期输入"""
        print("\n请选择日期：")
        print("  [1] 今天（默认）")
        print("  [2] 昨天")
        print("  [3] 自定义日期（格式：YYYY-MM-DD）")
        
        choice = input("\n请输入选项（1-3，默认1）: ").strip() or '1'
        
        if choice == '1':
            return datetime.now().strftime('%Y-%m-%d')
        elif choice == '2':
            from datetime import timedelta
            return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        elif choice == '3':
            date_str = input("请输入日期（YYYY-MM-DD）: ").strip()
            # 简单验证日期格式
            try:
                datetime.strptime(date_str, '%Y-%m-%d')
                return date_str
            except ValueError:
                print("日期格式错误，使用今天")
                return datetime.now().strftime('%Y-%m-%d')
        else:
            return datetime.now().strftime('%Y-%m-%d')
    
    def run_model(self, model_key, date=None):
        """运行指定模型"""
        if model_key not in self.models:
            print(f"错误：无效的模型选项 {model_key}")
            return False
        
        model = self.models[model_key]
        
        # 退出
        if model['path'] == 'exit':
            print("\n再见！")
            return True
        
        # 运行所有模型
        if model['path'] == 'all':
            print("\n开始运行所有模型...\n")
            for key in ['1', '2', '3', '4', '5']:
                if key in self.models and self.models[key]['path'] not in ['all', 'exit']:
                    print(f"\n{'='*70}")
                    print(f"运行模型: {self.models[key]['name']}")
                    print(f"{'='*70}")
                    self.run_single_model(self.models[key], date)
                    print(f"\n{'='*70}\n")
            return False
        
        # 运行单个模型
        return self.run_single_model(model, date)
    
    def run_single_model(self, model, date=None):
        """运行单个模型"""
        model_path = model['path']
        
        # 检查文件是否存在
        if not os.path.exists(model_path):
            print(f"错误：找不到模型文件")
            print(f"  文件路径: {model_path}")
            return False
        
        # 准备命令参数
        params = model['params'].copy()
        
        # 替换日期参数
        if 'today' in params:
            params = [p if p != 'today' else (date or datetime.now().strftime('%Y-%m-%d')) 
                     for p in params]
        
        # 构建命令
        cmd = [sys.executable, model_path] + params
        
        print(f"\n运行命令: {' '.join(cmd)}\n")
        print(f"{'='*70}\n")
        
        try:
            # 运行模型
            result = subprocess.run(cmd, 
                                  capture_output=False, 
                                  text=True,
                                  encoding='utf-8')
            
            if result.returncode == 0:
                print(f"\n✅ {model['name']} 运行成功！")
            else:
                print(f"\n❌ {model['name']} 运行失败")
                print(f"错误代码: {result.returncode}")
            
            return result.returncode == 0
            
        except Exception as e:
            print(f"\n❌ 运行出错: {e}")
            return False
    
    def run(self):
        """主运行循环"""
        while True:
            self.show_menu()
            
            choice = input("\n请输入选项（0-6）: ").strip()
            
            if choice == '0':
                if self.run_model('0'):
                    break
            elif choice in self.models:
                # 获取日期
                date = self.get_date_input()
                
                # 运行模型
                should_exit = self.run_model(choice, date)
                
                if should_exit:
                    break
                
                # 询问是否继续
                continue_choice = input("\n是否继续运行其他模型？（y/n，默认y）: ").strip().lower()
                if continue_choice == 'n':
                    print("\n再见！")
                    break
            else:
                print("\n无效的选项，请重新选择")


def main():
    """主函数"""
    try:
        runner = ModelRunner()
        runner.run()
    except KeyboardInterrupt:
        print("\n\n程序已终止")
        sys.exit(0)
    except Exception as e:
        print(f"\n程序运行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
