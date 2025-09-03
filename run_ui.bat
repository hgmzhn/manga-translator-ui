@echo off
setlocal

echo Checking for Python virtual environment...
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo Failed to create virtual environment. Please ensure Python is installed and in your PATH.
        pause
        exit /b 1
    )
)

echo Activating virtual environment and installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo Starting Manga Image Translator UI...
python -m desktop-ui.main

echo Application closed.
pause
