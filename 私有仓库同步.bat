@echo off
chcp 65001 >nul
setlocal

echo ========================================
echo 私有仓库同步工具
echo ========================================
echo.
echo 分支说明:
echo   don - 流水线功能（四线流水线，简化版，原文上下文）
echo   my-custom-features - UI功能（漫画管理面板+CBZ工具等）
echo.
echo 使用说明:
echo   运行脚本后会自动提示选择分支和操作
echo ========================================
echo.

echo 启动PowerShell同步脚本...
powershell -ExecutionPolicy Bypass -File "%~dp0私有仓库同步.ps1"

pause
