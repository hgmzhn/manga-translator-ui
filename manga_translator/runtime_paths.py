"""Runtime paths shared by the CLI, web server, and desktop application."""

from __future__ import annotations

import os
import sys


CONFIG_DIR_NAME = "config"


def get_application_dir() -> str:
    """Return the directory that owns user-editable application files."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))

    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def get_config_dir() -> str:
    """Return the external configuration directory.

    In packaged builds this is ``config`` next to the executable, never a
    directory below PyInstaller's ``_internal``/``sys._MEIPASS`` location.
    """
    return os.path.join(get_application_dir(), CONFIG_DIR_NAME)


def get_config_path(*parts: str) -> str:
    """Build an absolute path below the external configuration directory."""
    return os.path.join(get_config_dir(), *parts)
