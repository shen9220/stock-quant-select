# akshare 模块导入问题 - 永久解决方案

## 问题描述

运行 stock.py 或 up.py 时报错：
```
ModuleNotFoundError: No module named 'akshare'
```

## 根本原因

Python 找不到 akshare 模块，因为：
1. akshare 安装在用户目录 (`~/.local/lib/python3.13/site-packages`)
2. 但 Python 默认搜索路径不包含这个目录

## 永久解决方案

### 方案1：代码级别修复（已实施）

在每个 Python 文件的开头添加路径设置代码：

```python
import sys
import os

# 添加用户 site-packages 路径（确保 akshare 能被找到）
user_site_packages = os.path.expanduser('~/.local/lib/python3.13/site-packages')
if user_site_packages not in sys.path:
    sys.path.insert(0, user_site_packages)
```

**已修复的文件**：
- ✅ `01_量化模型/首板战法/stock.py`
- ✅ `01_量化模型/首板战法/up.py`

### 方案2：安装依赖（已执行）

```bash
pip3 install --user akshare pandas numpy requests
```

### 方案3：使用自动安装脚本

```bash
cd /workspace/projects/stock-quant-select
./install_deps.sh
```

### 方案4：使用 requirements.txt

```bash
cd /workspace/projects/stock-quant-select
pip3 install --user -r requirements.txt
```

## 测试验证

```bash
cd /workspace/projects/stock-quant-select/01_量化模型/首板战法
python3 stock.py
```

**预期输出**：
```
开始分析涨停股票...
查询日期: 2026年07月27日 (前一个交易日)
正在获取涨停股票数据...
获取到 111 只涨停股票
筛选首次涨停股票（排除20260724已涨停的股票）...
筛选后剩余 102 只股票
正在计算各项指标...
```

## 注意事项

1. **不要删除路径设置代码**：这是永久解决方案的关键
2. **定期更新依赖**：`pip3 install --user --upgrade akshare`
3. **使用虚拟环境**（推荐）：避免系统级依赖问题

## 相关文件

- `requirements.txt` - 依赖列表
- `install_deps.sh` - 自动安装脚本
- `.env` - 环境变量配置

## 更新日期

2026-07-27
