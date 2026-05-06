#!/bin/bash
# 智能选股助手 - 自动测试脚本

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

AGENT_DIR="/workspace/projects/stock-quant-select/stock-assistant-agent"

echo -e "${BLUE}==================================================================="
echo "                   智能选股助手 - 自动测试"
echo -e "===================================================================${NC}"
echo ""

cd "$AGENT_DIR"

# 运行测试
echo -e "${YELLOW}正在运行测试...${NC}"
echo ""

python3 test.py

TEST_EXIT_CODE=$?

echo ""
echo -e "${BLUE}==================================================================="
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ 所有测试通过！${NC}"
else
    echo -e "${RED}❌ 部分测试失败！${NC}"
fi
echo -e "===================================================================${NC}"

exit $TEST_EXIT_CODE
