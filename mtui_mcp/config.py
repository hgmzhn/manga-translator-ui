"""Runtime configuration and auto-discovery for manga-translator-ui."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("mtui_mcp.config")


def _is_valid_mtui_root(path: Path) -> bool:
    """Return True if ``path`` looks like a manga-translator-ui root directory."""
    try:
        return (
            path.is_dir()
            and (path / "manga_translator" / "__init__.py").is_file()
            and (path / "manga_translator" / "manga_translator.py").is_file()
        )
    except OSError:
        return False


def _is_current_python_mtui_capable() -> bool:
    """Return True if the active non-frozen Python interpreter has core MTUI packages."""
    if getattr(sys, "frozen", False) or not sys.executable:
        return False
    try:
        return (
            importlib.util.find_spec("PyQt6") is not None
            and importlib.util.find_spec("torch") is not None
        )
    except Exception:
        return False


def _discover_mtui_root(explicit: Optional[str | Path] = None) -> Optional[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    for env_key in ("MTUI_ROOT", "MANGA_TRANSLATOR_UI_ROOT", "MANGA_TRANSLATOR_ROOT"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            candidates.append(Path(env_val).expanduser())

    # Walk ancestor directories of `mtui_mcp/config.py` first so `<repo_root>/mtui_mcp`
    # resolves `<repo_root>` regardless of current working directory or clone folder name.
    this_file = Path(__file__).resolve()
    candidates.extend(this_file.parents)

    if getattr(sys, "frozen", False) and sys.executable:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass))

    cwd = Path.cwd()
    candidates.extend(
        [
            cwd,
            cwd / "manga-translator-ui",
            cwd.parent / "manga-translator-ui",
            Path.home() / "manga-translator-ui",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        norm = os.path.normcase(str(resolved))
        if norm in seen:
            continue
        seen.add(norm)
        if _is_valid_mtui_root(resolved):
            return resolved
    return None


def _discover_mtui_python(
    mtui_root: Optional[Path],
    explicit: Optional[str | Path] = None,
) -> Optional[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    env_py = os.environ.get("MTUI_PYTHON", "").strip()
    if env_py:
        candidates.append(Path(env_py).expanduser())

    if mtui_root is not None:
        if sys.platform == "win32":
            candidates.extend(
                [
                    mtui_root / "packaging" / "python" / "python.exe",
                    mtui_root / ".venv" / "Scripts" / "python.exe",
                    mtui_root / "env" / "python.exe",
                    mtui_root / "env" / "Scripts" / "python.exe",
                    mtui_root / "python" / "python.exe",
                    mtui_root / "runtime" / "python.exe",
                ]
            )
        else:
            candidates.extend(
                [
                    mtui_root / "packaging" / "python" / "bin" / "python",
                    mtui_root / ".venv" / "bin" / "python",
                    mtui_root / "env" / "bin" / "python",
                ]
            )

        if _is_current_python_mtui_capable():
            candidates.append(Path(sys.executable))

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


@dataclass
class MtuiRuntimeSettings:
    """Resolved runtime paths and settings for manga-translator-ui."""

    mtui_root: Optional[Path] = None
    mtui_python: Optional[Path] = None
    worker_idle_timeout_sec: int = 600
    auto_backup_on_first_edit: bool = True
    return_inline_images_default: bool = True
    max_inline_image_edge: int = 1800
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def refresh(
        self,
        mtui_root: Optional[str | Path] = None,
        mtui_python: Optional[str | Path] = None,
    ) -> None:
        with self._lock:
            resolved_root = _discover_mtui_root(mtui_root)
            if resolved_root is not None or mtui_root is not None:
                self.mtui_root = resolved_root
            resolved_py = _discover_mtui_python(self.mtui_root, mtui_python)
            if resolved_py is not None or mtui_python is not None:
                self.mtui_python = resolved_py

    @property
    def is_engine_available(self) -> bool:
        return (
            self.mtui_root is not None
            and self.mtui_root.is_dir()
            and self.mtui_python is not None
            and self.mtui_python.is_file()
        )

    @property
    def fonts_dir(self) -> Optional[Path]:
        if self.mtui_root is None:
            return None
        p = self.mtui_root / "fonts"
        return p if p.is_dir() else None

    @property
    def dict_dir(self) -> Optional[Path]:
        if self.mtui_root is None:
            return None
        p = self.mtui_root / "dict"
        return p if p.is_dir() else None

    @property
    def config_dir(self) -> Optional[Path]:
        if self.mtui_root is None:
            return None
        p = self.mtui_root / "config"
        return p if p.is_dir() else None

    def get_user_config_path(self) -> Optional[Path]:
        cfg_dir = self.config_dir
        if cfg_dir is None:
            return None
        user_cfg = cfg_dir / "config.json"
        if user_cfg.is_file():
            return user_cfg
        example_cfg = cfg_dir / "config-example.json"
        if example_cfg.is_file():
            return example_cfg
        return None

    def load_mtui_config_dict(self) -> dict[str, Any]:
        """Load manga-translator-ui's active config.json (or config-example.json)."""
        cfg_path = self.get_user_config_path()
        if cfg_path is None:
            return {}
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Failed to parse MTUI config %s: %s", cfg_path, exc)
            return {}


_RUNTIME_SETTINGS: Optional[MtuiRuntimeSettings] = None
_SETTINGS_LOCK = threading.Lock()


def get_runtime_settings() -> MtuiRuntimeSettings:
    global _RUNTIME_SETTINGS
    if _RUNTIME_SETTINGS is None:
        with _SETTINGS_LOCK:
            if _RUNTIME_SETTINGS is None:
                settings = MtuiRuntimeSettings()
                settings.refresh()
                _RUNTIME_SETTINGS = settings
    return _RUNTIME_SETTINGS
