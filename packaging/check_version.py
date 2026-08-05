#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版本检查脚本 - 从launch.py合并而来
检查当前版本和远程版本
"""
import subprocess
import sys
import os
import argparse
from pathlib import Path

# Git路径配置
def get_git_command():
    """获取git命令路径"""
    PATH_ROOT = Path(__file__).parent.parent
    portable_git = PATH_ROOT / "PortableGit" / "cmd" / "git.exe"
    if portable_git.exists():
        return str(portable_git)
    return os.environ.get('GIT', "git")

def get_current_version():
    """获取当前版本"""
    version_file = Path(__file__).parent / "VERSION"
    try:
        if version_file.exists():
            return version_file.read_text(encoding='utf-8').strip()
        else:
            return "unknown"
    except Exception as e:
        return "unknown"

def get_remote_version():
    """获取远程版本"""
    git_cmd = get_git_command()
    try:
        # 使用git show命令获取远程VERSION文件内容
        result = subprocess.run(
            [git_cmd, 'show', 'origin/main:packaging/VERSION'],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return "unknown"
    except Exception:
        return "unknown"

def fetch_remote_refs(git_cmd):
    """同步远程引用，失败时返回 False。"""
    try:
        result = subprocess.run(
            [git_cmd, 'fetch', 'origin'],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except Exception:
        return False

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='版本检查脚本')
    parser.add_argument('--brief', action='store_true', help='简洁模式：仅显示版本和更新提示')
    parser.add_argument('--export-vars', action='store_true', help='导出环境变量格式（用于bat脚本）')
    args = parser.parse_args()
    
    current_version = get_current_version()
    
    git_cmd = get_git_command()
    fetch_ok = fetch_remote_refs(git_cmd)
    remote_version = get_remote_version() if fetch_ok else "unknown"
    
    # 导出环境变量格式（用于bat脚本）
    if args.export_vars:
        print(f"CURRENT_VERSION={current_version}")
        print(f"REMOTE_VERSION={remote_version}")
        return 0
    
    if args.brief:
        # 简洁模式 - 用于脚本3（启动界面）
        print("")
        print("========================================")
        print("漫画翻译器 - 启动中")
        print("========================================")
        print(f"当前版本 - {current_version}")
        
        if remote_version == "unknown":
            print("")
            print("[警告] 无法获取远程版本信息,可能网络问题")
            print("")
            return 1
        if current_version != remote_version:
            print(f"远程版本 - {remote_version}")
            print("")
            print("[提示] 发现新版本可用！")
            print("请运行 步骤4-更新维护.bat 进行更新")
            print("")
        else:
            print("")
            print("[信息] 已是最新版本")
            print("")
    else:
        # 详细模式 - 用于脚本4（更新维护）
        print(f"当前版本 - {current_version}")
        print(f"远程版本 - {remote_version}")
        
        # 检查是否有更新
        if remote_version == "unknown":
            print("")
            print("[警告] 无法获取远程版本信息,可能网络问题")
            return 1
        elif current_version == remote_version:
            print("")
            print("[信息] 当前已是最新版本")
            return 0
        else:
            print("")
            print("[发现新版本]")
            print("")
            
            # 尝试读取远程 CHANGELOG
            doc_dir = Path(__file__).parent.parent / "doc"
            # 去除版本号中可能的 'v' 前缀
            version_clean = remote_version.lstrip('v')
            changelog_file = doc_dir / f"CHANGELOG_v{version_clean}.md"
            
            # 优先显示 CHANGELOG 文件
            changelog_shown = False
            if changelog_file.exists():
                try:
                    changelog_content = changelog_file.read_text(encoding='utf-8')
                    print(f"版本 {version_clean} 更新内容:")
                    print("========================================")
                    print(changelog_content)
                    print("========================================")
                    changelog_shown = True
                except Exception as e:
                    print(f"[警告] 无法读取更新文档: {e}")
            
            # 如果没有 CHANGELOG 文件，显示 git log
            if not changelog_shown:
                print("最新更新内容 (最近10条):")
                print("----------------------------------------")
                
                try:
                    result = subprocess.run(
                        [git_cmd, 'log', 'HEAD..origin/main', '--oneline', '--decorate', '--no-color', '-10'],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        check=False
                    )
                    if result.returncode == 0 and result.stdout:
                        print(result.stdout.strip())
                    else:
                        print("(无法获取更新日志)")
                except Exception:
                    print("(无法获取更新日志)")
                
                print("----------------------------------------")
            
            return 2  # 有更新
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
