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
import math
import os
import threading
import time
from multiprocessing.connection import Client, answer_challenge, deliver_challenge
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Optional

MAX_GUI_REQUEST_BYTES = 256 * 1024
_ORIG_MP_CLIENT = Client


def _monotonic() -> float:
    return time.monotonic()


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
    if conn is None or getattr(conn, "closed", False):
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


def _connect_windows_pipe_with_deadline(address: str, deadline: float) -> Any:
    import _winapi
    from multiprocessing.connection import PipeConnection

    while True:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out connecting to live manga-translator-ui GUI")
        try:
            handle = _winapi.CreateFile(
                address,
                _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0,
                _winapi.NULL,
                _winapi.OPEN_EXISTING,
                _winapi.FILE_FLAG_OVERLAPPED,
                _winapi.NULL,
            )
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (
                _winapi.ERROR_SEM_TIMEOUT,
                _winapi.ERROR_PIPE_BUSY,
            ):
                raise
            remaining = deadline - _monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Timed out waiting for busy live manga-translator-ui GUI pipe"
                ) from exc
            wait_ms = max(1, min(100, int(remaining * 1000)))
            try:
                _winapi.WaitNamedPipe(address, wait_ms)
            except OSError as wait_exc:
                if getattr(wait_exc, "winerror", None) not in (
                    _winapi.ERROR_SEM_TIMEOUT,
                    _winapi.ERROR_PIPE_BUSY,
                ):
                    raise
                if deadline - _monotonic() <= 0:
                    raise TimeoutError(
                        "Timed out waiting for busy live manga-translator-ui GUI pipe"
                    ) from wait_exc
            else:
                nap = min(0.005, max(0.0, deadline - _monotonic()))
                if nap > 0:
                    time.sleep(nap)
            continue

        try:
            _winapi.SetNamedPipeHandleState(
                handle, _winapi.PIPE_READMODE_MESSAGE, None, None
            )
            return PipeConnection(handle)
        except BaseException:
            try:
                _winapi.CloseHandle(handle)
            except OSError:
                pass
            raise


def _connect_ipc_client(address: str, deadline: float) -> Any:
    if (
        os.name == "nt"
        and Client is _ORIG_MP_CLIENT
        and address.startswith("\\\\.\\pipe\\")
    ):
        return _connect_windows_pipe_with_deadline(address, deadline)
    return Client(address, authkey=None)


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
    raw_budget = float(timeout_sec)
    if not math.isfinite(raw_budget):
        raise ValueError("timeout_sec must be a finite number")
    budget = max(0.05, raw_budget)
    deadline = _monotonic() + budget
    fixed_deadline = math.floor(deadline * 1_000_000) / 1_000_000

    min_probe_packet = json.dumps(
        {
            "method": method,
            "args": kwargs,
            "timeout_sec": 0,
            "deadline_monotonic": fixed_deadline,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(min_probe_packet) > MAX_GUI_REQUEST_BYTES:
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

    done = threading.Event()
    state_lock = threading.Lock()
    state: dict[str, Any] = {
        "peer": None,
        "timed_out": False,
        "closed": False,
        "request_sent": False,
        "reply_ready": False,
        "message": None,
        "error": None,
    }

    def _timeout_error_for_state(req_sent: bool) -> TimeoutError:
        if method == "save_page" and req_sent:
            return TimeoutError(
                "Timed out waiting for live manga-translator-ui GUI reply to save_page; "
                "save outcome is unknown (save may still be in progress or completed)"
            )
        return TimeoutError("Timed out waiting for live manga-translator-ui GUI")

    def _exchange() -> None:
        peer = None
        try:
            peer = _connect_ipc_client(address, deadline)
            with state_lock:
                state["peer"] = peer
                if state["timed_out"]:
                    if not state["closed"]:
                        state["closed"] = True
                        abort_ipc_connection(peer)
                    return

            answer_challenge(peer, auth)
            deliver_challenge(peer, auth)

            send_remaining = deadline - _monotonic()
            if send_remaining <= 0:
                raise _timeout_error_for_state(False)
            wire_payload = {
                "method": method,
                "args": kwargs,
                "timeout_sec": round(send_remaining, 6),
                "deadline_monotonic": fixed_deadline,
            }
            wire_packet = json.dumps(wire_payload, ensure_ascii=False).encode("utf-8")
            if len(wire_packet) > MAX_GUI_REQUEST_BYTES:
                wire_packet = json.dumps(
                    wire_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            if len(wire_packet) > MAX_GUI_REQUEST_BYTES:
                raise ValueError("Live GUI IPC request exceeds 256 KiB")
            with state_lock:
                if state["timed_out"]:
                    raise _timeout_error_for_state(False)
                state["request_sent"] = True
            peer.send_bytes(wire_packet)

            remaining = deadline - _monotonic()
            if remaining <= 0 or not peer.poll(remaining):
                raise _timeout_error_for_state(True)
            with state_lock:
                state["reply_ready"] = True
            raw_reply = peer.recv_bytes()
            state["message"] = json.loads(raw_reply.decode("utf-8"))
        except BaseException as exc:  # noqa: BLE001
            state["error"] = exc
        finally:
            if peer is not None:
                with state_lock:
                    if not state["closed"]:
                        state["closed"] = True
                        try:
                            peer.close()
                        except Exception:
                            pass
            done.set()

    worker = threading.Thread(target=_exchange, name="mtui-gui-bridge-client", daemon=True)
    worker.start()

    wait_remaining = max(0.0, deadline - _monotonic())
    if not done.wait(wait_remaining):
        with state_lock:
            reply_already_ready = bool(state["reply_ready"])
        if not (reply_already_ready and done.wait(0.02)):
            with state_lock:
                state["timed_out"] = True
                req_sent = bool(state["request_sent"])
                peer_to_abort = state["peer"]
                if peer_to_abort is not None and not state["closed"]:
                    state["closed"] = True
                    abort_ipc_connection(peer_to_abort)
            worker.join(timeout=0.1)
            if done.is_set() and state["message"] is not None and state["error"] is None:
                message = state["message"]
                if isinstance(message, dict) and "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message.get("result") if isinstance(message, dict) else message
            prior_err = state["error"] if done.is_set() else None
            if prior_err is not None:
                raise _timeout_error_for_state(req_sent) from prior_err
            raise _timeout_error_for_state(req_sent)

    err = state["error"]
    if err is not None:
        if method == "save_page" and state["request_sent"]:
            if isinstance(err, TimeoutError):
                raise _timeout_error_for_state(True) from err
            detail = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
            raise RuntimeError(
                "Live GUI save_page failed after request was sent; "
                f"save outcome is unknown (save may have already completed): {detail}"
            ) from err
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
    deadline = _monotonic() + total_budget
    last_pages: Optional[dict[str, Any]] = None

    while True:
        remaining = deadline - _monotonic()
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

        sleep_for = min(0.5, deadline - _monotonic())
        if sleep_for <= 0:
            return pages
        time.sleep(sleep_for)
