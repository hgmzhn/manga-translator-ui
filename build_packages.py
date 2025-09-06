#!/usr/bin/env python3
"""
Manga Translator Package Builder
Builds both GPU and CPU versions of the application
"""
import subprocess
import sys
import os
import shutil
from pathlib import Path

def run_command(cmd, cwd=None):
    """Execute a command and return the result"""
    print(f"Executing: {cmd}")
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=600
        )
        print(f"Exit code: {result.returncode}")
        if result.stdout:
            print(f"STDOUT:\n{result.stdout}")
        if result.stderr:
            print(f"STDERR:\n{result.stderr}")
        return result.returncode == 0
    except Exception as e:
        print(f"Error executing command: {e}")
        return False

def build_gpu_version():
    """Build GPU version with CUDA 11.8"""
    print("=" * 60)
    print("Building GPU Version with CUDA 11.8")
    print("=" * 60)
    
    venv_path = Path(".venv_gpu")
    
    # Create or use existing virtual environment
    if not venv_path.exists():
        print("Creating virtual environment for GPU build...")
        if not run_command(f"{sys.executable} -m venv .venv_gpu"):
            return False
    else:
        print("Using existing GPU virtual environment...")
    
    # Activate virtual environment commands
    if os.name == 'nt':  # Windows
        activate_cmd = f".venv_gpu\\Scripts\\activate && "
        python_cmd = "python"
    else:  # Linux/Mac
        activate_cmd = "source .venv_gpu/bin/activate && "
        python_cmd = "python3"
    
    # Upgrade pip
    print("Upgrading pip...")
    if not run_command(f"{activate_cmd}{python_cmd} -m pip install --upgrade pip"):
        return False
    
    # Install PyTorch with CUDA 11.8
    print("Installing PyTorch with CUDA 11.8...")
    torch_cmd = f"{activate_cmd}pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118"
    if not run_command(torch_cmd):
        return False
    
    # Install other dependencies
    print("Installing GPU dependencies...")
    if not run_command(f"{activate_cmd}pip install -r requirements_gpu.txt"):
        return False
    
    # Install PyInstaller
    print("Installing PyInstaller...")
    if not run_command(f"{activate_cmd}pip install pyinstaller"):
        return False
    
    # Clean previous builds
    for folder in ["dist/manga-translator-gpu", "dist/manga-translator-gpu-cuda118"]:
        if os.path.exists(folder):
            print(f"Cleaning previous build: {folder}")
            shutil.rmtree(folder)
    
    # Check dependencies
    print("Checking dependencies...")
    run_command(f"{activate_cmd}{python_cmd} -m pip check")
    
    # Build the application
    print("Building GPU application...")
    if not run_command(f"{activate_cmd}pyinstaller manga-translator-gpu.spec"):
        return False
    
    # Rename output folder
    if os.path.exists("dist/manga-translator-gpu"):
        if os.path.exists("dist/manga-translator-gpu-cuda118"):
            shutil.rmtree("dist/manga-translator-gpu-cuda118")
        os.rename("dist/manga-translator-gpu", "dist/manga-translator-gpu-cuda118")
        print("GPU build completed! Output in dist/manga-translator-gpu-cuda118/")
        return True
    else:
        print("GPU build failed - output directory not found!")
        return False

def build_cpu_version():
    """Build CPU version"""
    print("=" * 60)
    print("Building CPU Version")
    print("=" * 60)
    
    # Create virtual environment for CPU
    if os.path.exists(".venv_cpu"):
        shutil.rmtree(".venv_cpu")
    
    print("Creating virtual environment for CPU build...")
    if not run_command(f"{sys.executable} -m venv .venv_cpu"):
        return False
    
    # Activate virtual environment commands
    if os.name == 'nt':  # Windows
        activate_cmd = f".venv_cpu\\Scripts\\activate && "
        python_cmd = "python"
    else:  # Linux/Mac
        activate_cmd = "source .venv_cpu/bin/activate && "
        python_cmd = "python3"
    
    # Upgrade pip
    print("Upgrading pip...")
    if not run_command(f"{activate_cmd}{python_cmd} -m pip install --upgrade pip"):
        return False
    
    # Install CPU dependencies
    print("Installing CPU dependencies...")
    if not run_command(f"{activate_cmd}pip install -r requirements_cpu.txt"):
        return False
    
    # Install PyInstaller
    print("Installing PyInstaller...")
    if not run_command(f"{activate_cmd}pip install pyinstaller"):
        return False
    
    # Clean previous builds
    for folder in ["dist/manga-translator-cpu", "dist/manga-translator-cpu-final"]:
        if os.path.exists(folder):
            print(f"Cleaning previous build: {folder}")
            shutil.rmtree(folder)
    
    # Check dependencies
    print("Checking dependencies...")
    run_command(f"{activate_cmd}{python_cmd} -m pip check")
    
    # Build the application
    print("Building CPU application...")
    if not run_command(f"{activate_cmd}pyinstaller manga-translator-cpu.spec"):
        return False
    
    # Rename output folder
    if os.path.exists("dist/manga-translator-cpu"):
        if os.path.exists("dist/manga-translator-cpu-final"):
            shutil.rmtree("dist/manga-translator-cpu-final")
        os.rename("dist/manga-translator-cpu", "dist/manga-translator-cpu-final")
        print("CPU build completed! Output in dist/manga-translator-cpu-final/")
        return True
    else:
        print("CPU build failed - output directory not found!")
        return False

def main():
    """Main build function"""
    print("Manga Translator Package Builder")
    print("=" * 60)
    
    # Create dist directory if it doesn't exist
    os.makedirs("dist", exist_ok=True)
    
    # Ask user what to build
    print("Select build option:")
    print("[1] Build GPU version (CUDA 11.8)")
    print("[2] Build CPU version")
    print("[3] Build both versions")
    
    choice = input("Enter your choice (1-3): ").strip()
    
    success = True
    
    if choice == "1":
        success = build_gpu_version()
    elif choice == "2":
        success = build_cpu_version()
    elif choice == "3":
        print("Building both versions...")
        success = build_gpu_version() and build_cpu_version()
    else:
        print("Invalid choice!")
        return False
    
    if success:
        print("\n" + "=" * 60)
        print("BUILD COMPLETED SUCCESSFULLY!")
        print("=" * 60)
        if os.path.exists("dist/manga-translator-gpu-cuda118"):
            print("GPU Version: dist/manga-translator-gpu-cuda118/")
        if os.path.exists("dist/manga-translator-cpu-final"):
            print("CPU Version: dist/manga-translator-cpu-final/")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("BUILD FAILED!")
        print("=" * 60)
    
    return success

if __name__ == "__main__":
    main()