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
import threading
import time
from multiprocessing.connection import Client, answer_challenge, deliver_challenge
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


def abort_ipc_connection(conn: Any) -> None:
    """Force-unblock any pending I/O on a `multiprocessing.connection` object and close it."""
    if conn is None:
        return
    try:
        handle = conn.fileno()
    except Exception:
        handle = None

    if handle is not None:
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.windll.kernel32
                kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
                kernel32.CancelIoEx.restype = wintypes.BOOL
                kernel32.CancelIoEx(wintypes.HANDLE(int(handle)), None)
            except Exception:
                pass
        else:
            try:
                import socket as _socket

                dup_sock = _socket.socket(fileno=os.dup(int(handle)))
                try:
                    dup_sock.shutdown(_socket.SHUT_RDWR)
                finally:
                    dup_sock.close()
            except Exception:
                pass

    try:
        conn.close()
    except Exception:
        pass


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

    auth = key_file.read_bytes()
    if not auth:
        raise RuntimeError("Live GUI session key file is empty")

    budget = max(0.05, float(timeout_sec))
    deadline = time.monotonic() + budget
    done = threading.Event()
    state_lock = threading.Lock()
    state: dict[str, Any] = {
        "peer": None,
        "timed_out": False,
        "message": None,
        "error": None,
    }

    def _exchange() -> None:
        peer = None
        try:
            peer = Client(address, authkey=None)
            with state_lock:
                state["peer"] = peer
                if state["timed_out"]:
                    abort_ipc_connection(peer)
                    return

            answer_challenge(peer, auth)
            deliver_challenge(peer, auth)
            peer.send_bytes(packet)

            remaining = deadline - time.monotonic()
            if remaining <= 0 or not peer.poll(remaining):
                raise TimeoutError("Timed out waiting for live manga-translator-ui GUI")
            raw_reply = peer.recv_bytes()
            state["message"] = json.loads(raw_reply.decode("utf-8"))
        except BaseException as exc:  # noqa: BLE001
            state["error"] = exc
        finally:
            if peer is not None:
                try:
                    peer.close()
                except Exception:
                    pass
            done.set()

    worker = threading.Thread(target=_exchange, name="mtui-gui-bridge-client", daemon=True)
    worker.start()

    wait_remaining = max(0.0, deadline - time.monotonic())
    if not done.wait(wait_remaining):
        with state_lock:
            state["timed_out"] = True
            peer_to_abort = state["peer"]
        abort_ipc_connection(peer_to_abort)
        worker.join(timeout=0.1)
        raise TimeoutError("Timed out waiting for live manga-translator-ui GUI")

    err = state["error"]
    if err is not None:
        if isinstance(err, TimeoutError):
            raise err
        raise err

    message = state["message"]
    if isinstance(message, dict) and "error" in message:
        raise RuntimeError(str(message["error"]))
    return message.get("result") if isinstance(message, dict) else message


def wait_for_live_gui_translation(
    timeout_seconds: int = 25,
    session_name: Optional[str] = None,
) -> dict[str, Any]:
    total_budget = float(max(1, min(120, int(timeout_seconds))))
    deadline = time.monotonic() + total_budget
    last_pages: Optional[dict[str, Any]] = None

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_pages is not None:
                return last_pages
            raise TimeoutError("Timed out waiting for live manga-translator-ui GUI")

        try:
            pages = call_live_gui(
                "list_pages",
                session_name=session_name,
                timeout_sec=remaining,
            )
        except TimeoutError:
            if last_pages is not None:
                return last_pages
            raise

        last_pages = pages
        if not pages.get("translation_running"):
            return pages

        sleep_for = min(0.5, deadline - time.monotonic())
        if sleep_for <= 0:
            return pages
        time.sleep(sleep_for)
