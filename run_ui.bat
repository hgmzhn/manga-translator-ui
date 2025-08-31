@echo off
setlocal

echo ====================================================================
echo This script requires Python 3.10 to be installed on your system.
echo ====================================================================
echo.

echo Checking for a compatible Python 3.10 installation...
py -3.10 -c "import sys; print(f'Found Python {sys.version}')" >nul 2>nul
if %errorlevel% neq 0 (
    echo Error: Python 3.10 not found.
    echo Please install Python 3.10 from the official website (python.org)
    echo and ensure it's available via the 'py' launcher.
    pause
    exit /b 1
)

set VENV_DIR=venv_py310
set LOCK_FILE=%VENV_DIR%\.install_lock

echo Checking for Python virtual environment at '%VENV_DIR%'...
if not exist "%VENV_DIR%" (
    echo Creating Python 3.10 virtual environment (this may take a moment)...
    py -3.10 -m venv %VENV_DIR%
    if %errorlevel% neq 0 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call %VENV_DIR%\Scripts\activate.bat

if exist "%LOCK_FILE%" (
    echo Dependencies already installed. Skipping installation.
) else (
    echo Installing dependencies (this will only run once)...
    pip install --upgrade pip
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo Failed to install dependencies. Please check the error messages above.
        pause
        exit /b 1
    )
    echo.
    echo Dependencies installed successfully.
    echo Creating installation lock file to skip this step in the future.
    echo installed > "%LOCK_FILE%"
)

echo.
echo Starting Manga Image Translator UI...
pythonw -m desktop-ui.main

echo Application closed.
pause
