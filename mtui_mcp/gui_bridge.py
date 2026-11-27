"""Live IPC bridge client for a running `manga-translator-ui` Qt desktop GUI session.

Connects over a Windows Named Pipe (`\\\\.\\pipe\\mtui-<hash>`) or POSIX Unix Domain Socket
authenticated via `~/.cache/mtui-mcp/mtui-<hash>.key`, started automatically by `McpService`
when the desktop Qt UI runs with MCP enabled (or `--mcp`).
When no desktop GUI window is running (or on older stock upstream builds without the GUI bridge),
`is_live_gui_session_available()` safely returns `False`, and all headless `mtui_*` tools plus
MTUI-MCP's warm GPU worker operate directly on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from multiprocessing.connection import Client
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Optional

MAX_GUI_REQUEST_BYTES = 256 * 1024


def gui_session_paths(session_name: Optional[str] = None) -> tuple[str, Path]:
    sess = session_name or os.getenv("MTUI_MCP_SESSION", "default")
    identity = os.path.normcase(str(Path.home())) + "|" + sess
    name = "mtui-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    key_file = Path.home() / ".cache" / "mtui-mcp" / f"{name}.key"
    address = (
        f"\\\\.\\pipe\\{name}"
        if os.name == "nt"
        else os.path.join(gettempdir(), f"{name}.sock")
    )
    return address, key_file


def is_live_gui_session_available(session_name: Optional[str] = None) -> bool:
    _, key_file = gui_session_paths(session_name)
    if not key_file.is_file():
        return False
    try:
        res = call_live_gui("list_pages", session_name=session_name, timeout_sec=2.0, limit=1)
        return isinstance(res, dict)
    except Exception:
        return False


def call_live_gui(
    method: str,
    *,
    session_name: Optional[str] = None,
    timeout_sec: float = 30.0,
    **kwargs: Any,
) -> Any:
    packet = json.dumps({"method": method, "args": kwargs}, ensure_ascii=False).encode("utf-8")
    if len(packet) > MAX_GUI_REQUEST_BYTES:
        raise ValueError("Live GUI IPC request exceeds 256 KiB")
    address, key_file = gui_session_paths(session_name)
    if not key_file.is_file():
        raise RuntimeError(
            "No active manga-translator-ui --mcp GUI session key found "
            "(desktop GUI is not running or running a stock upstream build without --mcp IPC; "
            "use headless mtui_* tools directly)."
        )

    with Client(address, authkey=key_file.read_bytes()) as peer:
        peer.send_bytes(packet)
        if not peer.poll(timeout_sec):
            raise TimeoutError("Timed out waiting for live manga-translator-ui GUI")
        message = json.loads(peer.recv_bytes().decode("utf-8"))

    if isinstance(message, dict) and "error" in message:
        raise RuntimeError(str(message["error"]))
    return message.get("result") if isinstance(message, dict) else message


def wait_for_live_gui_translation(
    timeout_seconds: int = 25,
    session_name: Optional[str] = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, min(120, int(timeout_seconds)))
    while True:
        pages = call_live_gui("list_pages", session_name=session_name)
        if not pages.get("translation_running") or time.monotonic() >= deadline:
            return pages
        time.sleep(0.5)
