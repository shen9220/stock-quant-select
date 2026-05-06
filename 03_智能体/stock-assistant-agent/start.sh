#!/bin/bash
# 智能选股助手 - 一键启动脚本

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 项目路径
PROJECT_DIR="/workspace/projects/stock-quant-select"
AGENT_DIR="$PROJECT_DIR/stock-assistant-agent"
PYTHON_CMD="python3"

echo -e "${BLUE}==================================================================="
echo "                   智能选股助手 - 一键启动"
echo -e "===================================================================${NC}"
echo ""

# 检查 Python
echo -e "${YELLOW}[1/4] 检查 Python 环境...${NC}"
if ! command -v $PYTHON_CMD &> /dev/null; then
    echo -e "${RED}❌ 未找到 Python，请先安装 Python 3.7+${NC}"
    exit 1
fi
PYTHON_VERSION=$($PYTHON_CMD --version)
echo -e "${GREEN}✅ Python 环境: $PYTHON_VERSION${NC}"

# 检查依赖
echo -e "${YELLOW}[2/4] 检查依赖包...${NC}"
cd "$AGENT_DIR"
$PYTHON_CMD -c "import json; import sys; print('✅ 基础依赖正常')" 2>/dev/null || {
    echo -e "${YELLOW}⚠️  部分依赖缺失，正在安装...${NC}"
    pip3 install -q requests pandas numpy
    echo -e "${GREEN}✅ 依赖安装完成${NC}"
}

# 检查配置文件
echo -e "${YELLOW}[3/4] 检查配置文件...${NC}"
if [ ! -f "config.json" ]; then
    echo -e "${RED}❌ 未找到配置文件 config.json${NC}"
    exit 1
fi
if [ ! -f "agent.py" ]; then
    echo -e "${RED}❌ 未找到智能体文件 agent.py${NC}"
    exit 1
fi
echo -e "${GREEN}✅ 配置文件正常${NC}"

# 启动智能体
echo -e "${YELLOW}[4/4] 启动智能选股助手...${NC}"
echo ""
echo -e "${GREEN}🚀 正在启动智能体...${NC}"
echo ""

# 运行智能体
$PYTHON_CMD agent.py

echo ""
echo -e "${BLUE}==================================================================="
echo -e "                   智能选股助手已退出"
echo -e "===================================================================${NC}"
