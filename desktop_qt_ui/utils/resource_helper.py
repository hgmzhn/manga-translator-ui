"""
资源路径辅助函数
用于处理开发环境和 PyInstaller 打包环境的资源路径
"""
import os
import sys
from typing import Iterable


def _resource_base_candidates() -> list[str]:
    """Return application resource roots for bundled and dev environments."""
    base_candidates: list[str] = []

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        base_candidates.append(exe_dir)
    else:
        base_candidates.append(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        )

    seen: set[str] = set()
    unique_candidates: list[str] = []
    for candidate in base_candidates:
        normalized = os.path.abspath(candidate)
        if normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(normalized)
    return unique_candidates


def resource_path(relative_path):
    """
    Get an absolute external-resource path for dev and PyInstaller builds.

    Args:
        relative_path: 相对于项目根目录的路径

    Returns:
        绝对路径
    """
    return os.path.join(_resource_base_candidates()[0], relative_path)


def iter_existing_resource_paths(relative_paths: Iterable[str]):
    """Yield existing resource files from all known resource bases."""
    seen: set[str] = set()
    for base_path in _resource_base_candidates():
        for relative_path in relative_paths:
            abs_path = os.path.abspath(os.path.join(base_path, relative_path))
            if abs_path in seen:
                continue
            seen.add(abs_path)
            if os.path.exists(abs_path):
                yield abs_path


def load_icon_from_resources(relative_paths: Iterable[str]):
    """
    Load an icon eagerly from resource files to avoid lazy path-based failures.

    Returns:
        (QIcon, source_path) or (None, None) if all candidates fail.
    """
    from PyQt6.QtGui import QIcon

    icon = QIcon()
    loaded_from = None
    target_sizes = (16, 24, 32, 48, 64, 128, 256)

    for abs_path in iter_existing_resource_paths(relative_paths):
        candidate = QIcon(abs_path)
        if candidate.isNull():
            continue

        loaded_any = False
        for size in target_sizes:
            pixmap = candidate.pixmap(size, size)
            if pixmap.isNull():
                continue
            icon.addPixmap(pixmap)
            loaded_any = True

        if not loaded_any:
            continue

        loaded_from = abs_path

    if icon.isNull():
        return None, None
    return icon, loaded_from
