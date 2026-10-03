"""Native completion actions; the GUI owns the cancellable countdown."""

import logging
import subprocess
import sys

DEFAULT_SHUTDOWN_DELAY_SECONDS = 60
logger = logging.getLogger(__name__)


def build_completion_command(action: str, platform_name: str | None = None) -> list[str]:
    """Build an immediate native power command without changing system settings."""
    platform_name = platform_name or sys.platform
    if action not in {"shutdown", "sleep", "hibernate"}:
        raise ValueError(f"Not a system power action: {action}")
    if platform_name == "win32":
        if action == "shutdown":
            return ["shutdown", "/s", "/t", "0"]
        state = "Hibernate" if action == "hibernate" else "Suspend"
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "if (-not [System.Windows.Forms.Application]::SetSuspendState("
                f"[System.Windows.Forms.PowerState]::{state}, $false, $false)) "
                "{ exit 1 }"]
    if platform_name.startswith("linux"):
        return ["systemctl", {"shutdown": "poweroff", "sleep": "suspend",
                              "hibernate": "hibernate"}[action]]
    if platform_name == "darwin":
        if action == "sleep":
            return ["pmset", "sleepnow"]
        if action == "shutdown":
            return ["osascript", "-e", 'tell application "System Events" to shut down']
    raise ValueError(f"{action} is not supported on {platform_name}")


def execute_completion_action(action: str, *, platform_name=None, runner=None) -> bool:
    """Request a native action, reporting denied or unsupported operations."""
    try:
        command = build_completion_command(action, platform_name)
        (runner or subprocess.run)(command, check=True, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   text=True)
        return True
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        logger.warning("Completion action failed: %s", exc)
        return False
