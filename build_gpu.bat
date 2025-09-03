@echo OFF
echo =======================================
echo Building GPU Version...
echo =======================================

echo Step 1: Setting up Python environment...
echo.
echo IMPORTANT: Please make sure you have installed the correct PyTorch version for your CUDA setup.
echo See requirements_gpu.txt for instructions.
echo.
pause

pip install -r requirements_gpu.txt

echo Step 2: Running PyInstaller...
pyinstaller build.spec

echo =======================================
echo GPU Version Build Finished.
echo Find the executable in the 'dist' folder.
echo =======================================
pause
