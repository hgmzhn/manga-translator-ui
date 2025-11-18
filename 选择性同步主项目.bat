@echo off
chcp 65001 >nul
title 选择性同步主项目 - Cherry-pick Tool

echo.
echo ========================================
echo 选择性同步主项目工具
echo Selective Upstream Sync Tool
echo ========================================
echo.
echo 功能说明:
echo   从主项目 (hgmzhn/manga-translator-ui) 选择性地获取更新
echo   使用 Cherry-pick 精确控制要应用的提交
echo.
echo 优势:
echo   [√] 保留所有自定义功能
echo   [√] 只获取需要的更新
echo   [√] 避免大规模冲突
echo   [√] 完全控制更新内容
echo.
echo ========================================
echo.
echo 请选择操作:
echo   [1] 查看并选择提交 (推荐)
echo   [2] 查看最近50个提交
echo   [3] 退出
echo.
set /p choice="请选择 (1/2/3): "

if "%choice%"=="1" (
    echo.
    echo 启动选择性同步工具...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0选择性同步主项目.ps1"
    goto end
)

if "%choice%"=="2" (
    echo.
    echo 启动选择性同步工具 ^(显示50个提交^)...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0选择性同步主项目.ps1" -Count 50
    goto end
)

if "%choice%"=="3" (
    echo.
    echo 退出
    goto end
)

echo.
echo [错误] 无效选择
pause
goto end

:end
echo.
pause
