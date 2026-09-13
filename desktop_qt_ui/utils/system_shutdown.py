"""Cross-platform helpers for scheduling a safe delayed system shutdown."""

from __future__ import annotations

import logging
import math
import subprocess
import sys
from collections.abc import Callable


logger = logging.getLogger(__name__)

DEFAULT_SHUTDOWN_DELAY_SECONDS = 60


def build_system_shutdown_command(
    delay_seconds: int = DEFAULT_SHUTDOWN_DELAY_SECONDS,
    *,
    platform_name: str | None = None,
) -> list[str]:
    """Build the native command used to schedule a local power-off."""
    delay_seconds = max(1, int(delay_seconds))
    platform_name = platform_name or sys.platform

    if platform_name == "win32":
        return [
            "shutdown",
            "/s",
            "/t",
            str(delay_seconds),
            "/d",
            "p:0:0",
            "/c",
            "Manga Translator translation completed",
        ]

    if platform_name == "darwin" or platform_name.startswith("linux"):
        delay_minutes = max(1, math.ceil(delay_seconds / 60))
        return ["shutdown", "-h", f"+{delay_minutes}"]

    raise RuntimeError(f"Automatic shutdown is not supported on {platform_name!r}.")


def schedule_system_shutdown(
    delay_seconds: int = DEFAULT_SHUTDOWN_DELAY_SECONDS,
    *,
    platform_name: str | None = None,
    runner: Callable[..., object] | None = None,
) -> bool:
    """Schedule a delayed local power-off and report whether it was accepted."""
    try:
        command = build_system_shutdown_command(
            delay_seconds,
            platform_name=platform_name,
        )
    except RuntimeError as exc:
        logger.warning("Unable to schedule automatic shutdown: %s", exc)
        return False
    run = runner or subprocess.run
    try:
        run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        logger.warning("Unable to schedule automatic shutdown: %s", exc)
        return False
    return True
