@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%私人仓库更新.ps1"

echo.
echo ========================================
echo 私人仓库更新工具
echo Manga Translator - Private Repo Update
echo ========================================
echo.
echo 私人仓库: loqwe/manga-translator-ui
echo 主项目: hgmzhn/manga-translator-ui
echo.
echo ========================================
echo.

if not exist "%PS_SCRIPT%" (
    echo [错误] 找不到PowerShell脚本
    pause
    exit /b 1
)

if not exist "%SCRIPT_DIR%.git" (
    echo [警告] 当前目录似乎不是Git仓库
    echo.
    choice /C YN /M "是否继续运行"
    if errorlevel 2 exit /b 0
    echo.
)

:menu
echo 请选择操作:
echo [1] 从私人仓库拉取 (仅更新本地代码)
echo [2] 同步主项目到私人仓库 (拉取主项目更新并推送)
echo [3] 完整更新 (从私人仓库拉取 + 同步主项目)
echo [4] 退出
echo.
set /p choice="请选择 (1/2/3/4): "

if "%choice%"=="1" goto update_only
if "%choice%"=="2" goto sync_upstream
if "%choice%"=="3" goto full_sync
if "%choice%"=="4" goto end

echo [错误] 无效的选择
echo.
goto menu

:update_only
echo.
echo ========================================
echo 从私人仓库拉取代码
echo ========================================
echo.

echo 请选择要更新的分支:
echo   1. main  - 主分支
echo   2. dev   - 开发分支
echo.
set /p branch_choice="请输入选择 (1/2): "

if "%branch_choice%"=="1" (
    set "branch=main"
) else if "%branch_choice%"=="2" (
    set "branch=dev"
) else (
    echo [错误] 无效的选择
    pause
    goto menu
)

echo.
echo [信息] 选择的分支: %branch%
echo [警告] 本地未提交的修改将被自动暂存
echo.

set /p confirm="是否继续? (y/n): "
if /i not "%confirm%"=="y" (
    echo 取消操作
    goto menu
)

echo.
echo 开始从私人仓库拉取...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -Branch %branch%

set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [错误] 更新失败 (错误码: %EXIT_CODE%)
    pause
    goto menu
)

echo.
echo [成功] 从私人仓库拉取完成
pause
goto menu

:sync_upstream
echo.
echo ========================================
echo 同步主项目到私人仓库
echo ========================================
echo.

echo 请选择要同步的分支:
echo   1. main  - 主分支
echo   2. dev   - 开发分支
echo.
set /p branch_choice="请输入选择 (1/2): "

if "%branch_choice%"=="1" (
    set "branch=main"
) else if "%branch_choice%"=="2" (
    set "branch=dev"
) else (
    echo [错误] 无效的选择
    pause
    goto menu
)

echo.
echo [信息] 选择的分支: %branch%
echo [警告] 这将从主项目拉取最新更新并推送到私人仓库
echo.

set /p confirm="是否继续? (y/n): "
if /i not "%confirm%"=="y" (
    echo 取消操作
    goto menu
)

echo.
echo 开始同步主项目...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -Branch %branch% -SyncUpstream

set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [错误] 同步失败 (错误码: %EXIT_CODE%)
    pause
    goto menu
)

echo.
echo [成功] 主项目同步完成
pause
goto menu

:full_sync
echo.
echo ========================================
echo 完整更新
echo ========================================
echo.

echo 请选择要更新的分支:
echo   1. main  - 主分支
echo   2. dev   - 开发分支
echo.
set /p branch_choice="请输入选择 (1/2): "

if "%branch_choice%"=="1" (
    set "branch=main"
) else if "%branch_choice%"=="2" (
    set "branch=dev"
) else (
    echo [错误] 无效的选择
    pause
    goto menu
)

echo.
echo [信息] 选择的分支: %branch%
echo.
echo 完整更新将执行:
echo 1. 从私人仓库拉取最新代码
echo 2. 从主项目同步更新
echo 3. 推送更新到私人仓库
echo.

set /p confirm="是否继续? (y/n): "
if /i not "%confirm%"=="y" (
    echo 取消操作
    goto menu
)

echo.
echo 开始完整更新...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -Branch %branch% -SyncUpstream

set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo [错误] 完整更新失败 (错误码: %EXIT_CODE%)
    pause
    goto menu
)

echo.
echo [成功] 完整更新完成
pause
goto menu

:end
echo.
echo 退出更新工具
pause
