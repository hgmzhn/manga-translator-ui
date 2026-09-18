"""Local plugin manager exports and the host default instance."""

from __future__ import annotations

import atexit
from pathlib import Path
from threading import Lock

from .api import PluginRegistrar, PluginRegistration
from .loader import PluginManager
from .manifest import PluginManifest

_default_lock = Lock()
_default_manager: PluginManager | None = None


def get_default_manager() -> PluginManager:
    """Return the process-wide manager for ``extensions/agent``.

    The directory is intentionally not created: absence means no local
    extensions, and installation remains a host-only operation.
    """
    global _default_manager
    with _default_lock:
        if _default_manager is None:
            root = Path(__file__).resolve().parents[3] / "extensions" / "agent"
            _default_manager = PluginManager(root)
            atexit.register(_default_manager.close)
        return _default_manager


def close_default_manager() -> None:
    with _default_lock:
        if _default_manager is not None:
            _default_manager.close()


__all__ = [
    "PluginManager",
    "PluginManifest",
    "PluginRegistrar",
    "PluginRegistration",
    "get_default_manager",
    "close_default_manager",
]
