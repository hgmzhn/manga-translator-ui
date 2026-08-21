"""
应用版本号辅助函数。
统一处理开发环境和 PyInstaller 打包环境下的版本读取与显示格式。
"""
from __future__ import annotations

from utils.resource_helper import iter_existing_resource_paths


def get_app_version(default: str = "unknown") -> str:
    """从唯一版本文件 packaging/VERSION 读取应用版本号。"""
    for version_path in iter_existing_resource_paths(("packaging/VERSION",)):
        try:
            with open(version_path, "r", encoding="utf-8") as version_file:
                version = version_file.read().strip()
        except OSError:
            continue
        if version:
            return version.lstrip("v")
    return default


def format_app_title(base_title: str, version: str | None) -> str:
    """生成带版本号的窗口标题。"""
    normalized_version = (version or "").strip()
    if not normalized_version or normalized_version == "unknown":
        return base_title
    return f"{base_title} v{normalized_version}"


def format_version_label(version: str | None) -> str:
    """生成侧边栏显示用的版本标签。"""
    normalized_version = (version or "").strip()
    if not normalized_version or normalized_version == "unknown":
        return ""
    return f"v{normalized_version}"
