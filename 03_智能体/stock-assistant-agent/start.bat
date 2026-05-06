#!/bin/bash
# 智能选股助手 - Windows 启动脚本

@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ===================================================
echo                    智能选股助手 - 一键启动
echo ===================================================
echo.

echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 Python，请先安装 Python 3.7+
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo ✅ Python 环境: %PYTHON_VERSION%

echo.
echo [2/4] 检查依赖包...
python -c "import json; import sys; print('✅ 基础依赖正常')" 2>nul
if errorlevel 1 (
    echo ⚠️  部分依赖缺失，正在安装...
    pip install -q requests pandas numpy
    echo ✅ 依赖安装完成
)

echo.
echo [3/4] 检查配置文件...
if not exist "config.json" (
    echo ❌ 未找到配置文件 config.json
    pause
    exit /b 1
)
if not exist "agent.py" (
    echo ❌ 未找到智能体文件 agent.py
    pause
    exit /b 1
)
echo ✅ 配置文件正常

echo.
echo [4/4] 启动智能选股助手...
echo.
echo 🚀 正在启动智能体...
echo.

python agent.py

echo.
echo ===================================================
echo                    智能选股助手已退出
echo ===================================================
pause
