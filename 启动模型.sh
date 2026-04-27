#!/bin/bash

echo "======================================================================"
echo "                    股票量化模型一键运行工具"
echo "======================================================================"
echo ""

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "错误：未找到Python3，请先安装Python3"
    exit 1
fi

# 运行主程序
python3 run_models.py

# 捕获退出码
if [ $? -ne 0 ]; then
    echo ""
    echo "程序运行出错，请查看上面的错误信息"
    read -p "按Enter键退出..."
fi
