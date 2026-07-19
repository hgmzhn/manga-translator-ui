import subprocess
import sys
import os
import json
import argparse
from pathlib import Path
import io

# Force stdout and stderr to use UTF-8 encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def run_command_realtime(cmd, cwd=None):
    """实时执行一个 shell 命令并打印输出。"""
    use_shell = isinstance(cmd, str)
    print(f"\nExecuting: {cmd}")
    try:
        # Set PYTHONUTF8 environment variable to ensure UTF-8 is used by subprocesses
        env = os.environ.copy()
        env['PYTHONUTF8'] = '1'
        
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            shell=use_shell,
            env=env
        )
        
        # Real-time output
        for line in process.stdout:
            print(line.strip())
        
        returncode = process.wait()
        print(f"Exit code: {returncode}")
        return returncode == 0
    except Exception as e:
        # Try to print the exception, but be prepared for encoding errors
        try:
            print(f"Error executing command: {e}")
        except UnicodeEncodeError:
            print(f"Error executing command (ascii representation): {repr(e)}")
        return False


class Builder:
    """封装了构建和打包逻辑的类"""

    def __init__(self, app_version=None):
        # Strip 'v' prefix from version string, if present
        self.app_version = app_version.lstrip('v') if app_version else None
        self.version_file = Path("packaging/VERSION")

    def get_effective_version(self):
        """获取当前构建应使用的版本号。"""
        if self.app_version:
            return self.app_version.strip()

        try:
            if self.version_file.exists():
                stored_version = self.version_file.read_text(encoding="utf-8").strip()
                if stored_version:
                    return stored_version.lstrip("v")
        except OSError as exc:
            print(f"Warning: failed to read version file '{self.version_file}': {exc}")
        return None

    def sync_version_file(self):
        """将传入的构建版本写回 packaging/VERSION，便于本地构建与调试。"""
        effective_version = self.get_effective_version()
        if not effective_version:
            print("Warning: no application version available.")
            return None

        try:
            self.version_file.write_text(f"{effective_version}\n", encoding="utf-8")
            print(f"Synced version file: {self.version_file} -> {effective_version}")
        except OSError as exc:
            print(f"Warning: failed to write version file '{self.version_file}': {exc}")
        return effective_version

    def write_runtime_version_file(self, dist_dir, app_version):
        """将版本号写入运行时产物，供 Qt 打包版启动时显示。"""
        if not app_version:
            return

        runtime_version_file = dist_dir / "VERSION"
        runtime_version_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_version_file.write_text(f"{app_version}\n", encoding="utf-8")
        print(f"Created runtime version file at: {runtime_version_file}")

    def build_executables(self, version_type):
        """使用 PyInstaller 构建指定版本 (cpu 或 gpu)"""
        print("=" * 60)
        print(f"Building {version_type.upper()} Executable")
        print("=" * 60)

        venv_path = Path(f".venv_{version_type}")
        
        # Spec files are now in packaging/ directory
        spec_file = Path("packaging") / f"manga-translator-{version_type}.spec"

        # Prefer local venv python if it exists, otherwise use the python running this script
        python_exe_in_venv = venv_path / 'Scripts' / 'python.exe' if sys.platform == 'win32' else venv_path / 'bin' / 'python'
        if os.path.exists(python_exe_in_venv):
            python_exe = str(python_exe_in_venv)
            print(f"Using python from venv: {python_exe}")
        else:
            python_exe = sys.executable
            print(f"Venv python not found. Using system python: {python_exe}")

        if not spec_file.exists():
            print(f"Error: Spec file '{spec_file}' not found.")
            return False
        
        # In a CI environment, we assume dependencies are pre-installed by the workflow.
        print(f"Running PyInstaller for {version_type.upper()}...")
        # Specify output directories to be in the project root, not packaging/
        project_root = Path.cwd()
        cmd_pyinstaller = [
            python_exe, "-m", "PyInstaller", str(spec_file),
            "--distpath", str(project_root / "dist"),
            "--workpath", str(project_root / "build")
        ]
        if not run_command_realtime(cmd_pyinstaller):
            print(f"PyInstaller build failed for {version_type.upper()}.")
            return False

        # Create build_info.json
        dist_dir = Path("dist") / f"manga-translator-{version_type}"
        app_version = self.get_effective_version()
        self.write_runtime_version_file(dist_dir, app_version)
        build_info_path = dist_dir / "build_info.json"
        print(f"Creating build info file at: {build_info_path}")
        with open(build_info_path, "w", encoding="utf-8") as f:
            json.dump({"variant": version_type, "version": app_version}, f, indent=2)

        print(f"{version_type.upper()} build completed!")
        return True


def main():
    import logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description="Manga Translator UI Builder")
    parser.add_argument("version", nargs='?', default=None, help="The application version to build (e.g., 1.4.0)")
    parser.add_argument("--build", choices=['cpu', 'gpu', 'both'], default='both', help="Which version(s) to build.")
    args = parser.parse_args()

    if not args.version:
        parser.error("the following arguments are required: version")

    builder = Builder(args.version)
    builder.sync_version_file()

    print(f"--- Starting build process for version {args.version} ---")

    versions_to_process = []
    if args.build in ['cpu', 'both']:
        versions_to_process.append('cpu')
    if args.build in ['gpu', 'both']:
        versions_to_process.append('gpu')

    for v_type in versions_to_process:
        if not builder.build_executables(v_type):
            print(f"\nFATAL: Build failed for {v_type.upper()}. Halting.")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("ALL BUILDS COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
