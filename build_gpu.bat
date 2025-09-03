@echo off
setlocal

echo =======================================
echo Building GPU Version...
echo =======================================

echo Step 1: Checking for Python virtual environment...
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo Failed to create virtual environment. Please ensure Python 3.10 is in your PATH.
        pause
        exit /b 1
    )
)

echo Step 2: Activating virtual environment and installing GPU dependencies...
call venv\Scripts\activate.bat

echo.
echo IMPORTANT: Please make sure you have installed the correct PyTorch version for your CUDA setup.
echo See requirements_gpu.txt for instructions.
echo.
pause

pip install -r requirements_gpu.txt

echo Step 3: Running PyInstaller...
pyinstaller build.spec

echo =======================================
echo GPU Version Build Finished.
echo Find the executable in the 'dist' folder.
echo =======================================
pause
