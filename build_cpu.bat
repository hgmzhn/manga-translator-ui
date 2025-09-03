@echo OFF
echo =======================================
echo Building CPU Version...
echo =======================================

echo Step 1: Setting up Python environment...
pip install -r requirements_cpu.txt

echo Step 2: Running PyInstaller...
pyinstaller build.spec

echo =======================================
echo CPU Version Build Finished.
echo Find the executable in the 'dist' folder.
echo =======================================
pause
