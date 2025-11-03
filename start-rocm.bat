@echo off
chcp 65001 >nul
REM ========================================
REM Manga Translator UI - ROCm 7 啟動腳本
REM 適用於 AMD Radeon RX 9000 系列 (gfx1201)
REM ========================================

REM ========================================
REM 注意：請根據您的實際路徑修改以下變量
REM ========================================
set "CONDA_ROOT=C:\Users\longxin\miniconda3"
set "PROJECT_DIR=D:\漫画\1\manga-translator-ui"
set "CONDA_ENV=manga"

cd /d "%PROJECT_DIR%"

echo ========================================
echo Manga Translator UI - ROCm 7 版本
echo ========================================
echo.

REM 激活 Conda 環境
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%"
call conda activate %CONDA_ENV%

if %errorlevel% neq 0 (
    echo [錯誤] 無法激活環境 %CONDA_ENV%
    echo.
    echo 請先創建環境：
    echo   conda create -n manga python=3.13 -y
    echo   conda activate manga
    echo   python -m pip install --index-url https://d2awnip2yjpvqn.cloudfront.net/v2/gfx120X-all/ torch torchvision torchaudio
    echo   pip install -r requirements_rocm.txt
    echo.
    pause
    exit /b 1
)

echo [成功] 環境已激活
echo.

REM 設置環境變量，確保使用 ROCm
set HSA_OVERRIDE_GFX_VERSION=12.0.1
set PYTORCH_ROCM_ARCH=gfx1201

echo 正在啟動 Manga Translator UI (Qt 界面)...
echo.

REM 啟動 Qt 界面
python -m desktop_qt_ui.main

echo.
echo 已退出 Manga Translator UI
echo.
pause



