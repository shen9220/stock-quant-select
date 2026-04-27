@echo off
REM Windows批处理启动脚本

echo ======================================================================
echo                      股票量化模型一键运行工具
echo ======================================================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到Python，请先安装Python
    pause
    exit /b 1
)

REM 运行主程序
python run_models.py

REM 如果程序异常退出，暂停以便查看错误信息
if errorlevel 1 (
    echo.
    echo 程序运行出错，请查看上面的错误信息
    pause
)
