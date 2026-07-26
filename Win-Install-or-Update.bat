@echo off
setlocal EnableDelayedExpansion

REM Avoid conda/python encoding issues
set "PYTHONUTF8=1"

REM Use the script's own directory as the working directory
REM (fixes %CD% being system32 when run as administrator)
cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM Portable Git (optional)
if exist "%SCRIPT_DIR%\PortableGit\cmd\git.exe" set "PATH=%SCRIPT_DIR%\PortableGit\cmd;%PATH%"

REM ===== 1) Bundled portable Python - preferred =====
set "PY="
if exist "%SCRIPT_DIR%\packaging\python\python.exe" (
    set "PY=%SCRIPT_DIR%\packaging\python\python.exe"
    echo [OK] Using bundled Python: !PY!
    goto :env_ready
)

echo [INFO] Bundled Python not found, trying Conda - legacy layout...

REM ===== 2) Conda fallback - legacy installs =====
set "CONDA_ENV_NAME=manga-env"
set "LEGACY_ENV=%SCRIPT_DIR%\conda_env"
set "MINICONDA_ROOT="
set "DEFAULT_ROOT=%SCRIPT_DIR%\Miniconda3"
set "ALT_ROOT=%~d0\Miniconda3"

REM If the path contains non-ASCII characters, the local Miniconda
REM is expected at the drive root instead
powershell -NoProfile -Command "$p='%SCRIPT_DIR%'; if ($p -match '[^\x00-\x7F]') { exit 1 } else { exit 0 }" >nul 2>&1
if errorlevel 1 set "DEFAULT_ROOT=%ALT_ROOT%"

if exist "%DEFAULT_ROOT%\Scripts\conda.exe" set "MINICONDA_ROOT=%DEFAULT_ROOT%"
if not defined MINICONDA_ROOT if exist "%ALT_ROOT%\Scripts\conda.exe" set "MINICONDA_ROOT=%ALT_ROOT%"
if not defined MINICONDA_ROOT if defined CONDA_EXE (
    for /f "delims=" %%i in ('"%CONDA_EXE%" info --base 2^>nul') do (
        if exist "%%i\Scripts\conda.exe" set "MINICONDA_ROOT=%%i"
    )
)
if not defined MINICONDA_ROOT (
    for /f "delims=" %%i in ('conda info --base 2^>nul') do (
        if exist "%%i\Scripts\conda.exe" set "MINICONDA_ROOT=%%i"
    )
)

REM Resolve the environment path: named env first, then legacy path env
set "ENV_PATH="
if defined MINICONDA_ROOT if exist "%MINICONDA_ROOT%\envs\%CONDA_ENV_NAME%\python.exe" set "ENV_PATH=%MINICONDA_ROOT%\envs\%CONDA_ENV_NAME%"
if not defined ENV_PATH if defined MINICONDA_ROOT (
    for /f "tokens=1,2,3" %%a in ('"%MINICONDA_ROOT%\Scripts\conda.exe" info --envs 2^>nul ^| findstr /B /C:"%CONDA_ENV_NAME%"') do (
        if "%%b"=="*" ( set "ENV_PATH=%%c" ) else ( set "ENV_PATH=%%b" )
    )
)
if not defined ENV_PATH if exist "%LEGACY_ENV%\python.exe" set "ENV_PATH=%LEGACY_ENV%"

if not defined ENV_PATH (
    echo [ERROR] Neither bundled Python nor Conda environment was found.
    echo The package layout may be broken. Please re-download the package.
    pause
    exit /b 1
)

REM Prepend the env runtime directories to PATH - equivalent to activation
set "PATH=!ENV_PATH!;!ENV_PATH!\Library\mingw-w64\bin;!ENV_PATH!\Library\usr\bin;!ENV_PATH!\Library\bin;!ENV_PATH!\Scripts;!ENV_PATH!\bin;%PATH%"
set "CONDA_PREFIX=!ENV_PATH!"
set "CONDA_DEFAULT_ENV=%CONDA_ENV_NAME%"
set "PY=!ENV_PATH!\python.exe"
echo [OK] Using Conda environment: !ENV_PATH!

:env_ready

"!PY!" packaging\launch.py --maintenance
set "EXITCODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXITCODE%
