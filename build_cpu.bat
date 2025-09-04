@echo off
echo Building CPU version...

REM Create virtual environment for CPU build
echo Creating virtual environment for CPU build...
python -m venv .venv_cpu

REM Activate virtual environment
call .venv_cpu\Scripts\activate.bat

REM Upgrade pip
python -m pip install --upgrade pip

REM Install dependencies for CPU version
echo Installing CPU dependencies...
pip install -r requirements_cpu.txt

REM Install PyInstaller
pip install pyinstaller

REM Clean previous build
if exist dist\manga-translator-cpu (
    echo Cleaning previous CPU build...
    rmdir /s /q dist\manga-translator-cpu
)
if exist dist\manga-translator-cpu-final (
    echo Cleaning previous CPU final build...
    rmdir /s /q dist\manga-translator-cpu-final
)

REM Check dependencies
echo Checking dependencies...
python -m pip check

REM Build the application
echo Building application...
pyinstaller manga-translator-cpu.spec

REM Rename output folder
if exist dist\manga-translator-cpu (
    ren dist\manga-translator-cpu manga-translator-cpu-final
    echo CPU build completed! Output in dist\manga-translator-cpu-final\
) else (
    echo Build failed!
)

REM Deactivate virtual environment
deactivate

pause