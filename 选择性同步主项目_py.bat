@echo off
chcp 65001 >nul
title 选择性同步主项目 (Python版)

echo.
echo ========================================
echo 选择性同步主项目工具 (Python版)
echo ========================================
echo.
echo 正在启动 Python 脚本...
echo.

python "%~dp0selective_sync.py"

if %errorlevel% neq 0 (
    echo.
    echo [错误] Python脚本执行失败
    echo 请确保已安装Python并添加到环境变量
    pause
)
