#!/bin/bash
# Python 环境初始化脚本
# 永久解决 akshare 模块导入问题

echo "🔧 检查 Python 环境..."

# 检查 Python 版本
python_version=$(python3 --version 2>&1)
echo "  Python 版本: $python_version"

# 检查 akshare 是否已安装
if python3 -c "import akshare" 2>/dev/null; then
    echo "✅ akshare 已安装"
    akshare_version=$(python3 -c "import akshare; print(akshare.__version__)")
    echo "  版本: $akshare_version"
else
    echo "❌ akshare 未安装，开始安装..."
    python3 -m pip install --user akshare pandas numpy
    echo "✅ akshare 安装完成"
fi

echo ""
echo "🚀 运行程序..."
echo ""

# 运行传入的命令
if [ -n "$1" ]; then
    cd "$(dirname "$1")"
    python3 "$(basename "$1")" "${@:2}"
else
    echo "请提供要运行的 Python 文件"
    echo "用法: ./init.sh <python_file.py> [参数...]"
fi
