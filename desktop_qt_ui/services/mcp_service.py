"""Desktop MCP lifecycle manager and live Qt GUI IPC session bridge."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import weakref
from dataclasses import dataclass
from multiprocessing.connection import (
    BufferTooShort,
    Client,
    Listener,
    answer_challenge,
    deliver_challenge,
)
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PyQt6.QtCore import QCoreApplication, QObject, Qt, QThread, pyqtSignal, pyqtSlot

MAX_GUI_REQUEST_BYTES = 256 * 1024
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8765
DEFAULT_IPC_HANDSHAKE_TIMEOUT = 5.0

_TRUTHY_ENV = frozenset({"1", "true", "yes", "on"})
_FALSY_ENV = frozenset({"0", "false", "no", "off"})

_ACTIVE_BRIDGE_LOCK = threading.Lock()
_ACTIVE_BRIDGE_ADDRESSES: set[str] = set()


def parse_env_bool(name: str) -> Optional[bool]:
    """Parse an optional boolean environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _TRUTHY_ENV:
        return True
    if normalized in _FALSY_ENV:
        return False
    return None


def parse_env_port(name: str) -> Optional[int]:
    """Parse an optional TCP port environment variable."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        port = int(raw.strip())
    except ValueError:
        return None
    if 0 <= port <= 65535:
        return port
    return None


def gui_session_paths(session_name: Optional[str] = None) -> tuple[str, Path]:
    """Compute IPC endpoint and auth key path matching `mtui_mcp.gui_bridge`."""
    try:
        from mtui_mcp.gui_bridge import gui_session_paths as shared_gui_session_paths

        return shared_gui_session_paths(session_name)
    except Exception:
        sess = session_name or os.getenv("MTUI_MCP_SESSION", "default")
        identity = os.path.normcase(str(Path.home())) + "|" + sess
        name = "mtui-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        key_file = Path.home() / ".cache" / "mtui-mcp" / f"{name}.key"
        address = (
            f"\\\\.\\pipe\\{name}"
            if os.name == "nt"
            else os.path.join(tempfile.gettempdir(), f"{name}.sock")
        )
        return address, key_file


def _abort_ipc_connection(conn: Any) -> None:
    """Force-unblock pending I/O on a stalled IPC connection and close it."""
    try:
        from mtui_mcp.gui_bridge import abort_ipc_connection

        abort_ipc_connection(conn)
        return
    except Exception:
        pass

    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def _acquire_session_lockfile(lock_path: Path) -> Optional[int]:
    """Acquire an exclusive non-blocking OS lock on ``lock_path`` (held while bridge is live)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd: Optional[int] = None
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"1")
                os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return None


def _release_session_lockfile(fd: Optional[int]) -> None:
    """Release and close an exclusive session lockfile descriptor."""
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _json_sanitize(value: Any) -> Any:
    """Convert numpy scalars/arrays, Paths, and tuples into JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _json_sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_sanitize(v) for v in value]
    return str(value)


@dataclass
class _MainThreadCall:
    fn: Callable[[], Any]
    done: threading.Event
    state: str = "PENDING"  # PENDING -> RUNNING -> DONE | CANCELLED
    result: Any = None
    error: Optional[BaseException] = None


class _MainThreadDispatcher(QObject):
    """Safely marshals callables onto the Qt GUI main thread."""

    _invoke_signal = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__(None)
        self._state_lock = threading.Lock()
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() != app.thread():
            try:
                self.moveToThread(app.thread())
            except Exception:
                pass
        self._invoke_signal.connect(
            self._on_invoke,
            type=Qt.ConnectionType.QueuedConnection,
        )

    @pyqtSlot(object)
    def _on_invoke(self, call: object) -> None:
        if not isinstance(call, _MainThreadCall):
            return
        with self._state_lock:
            if call.state != "PENDING":
                return
            call.state = "RUNNING"

        result: Any = None
        error: Optional[BaseException] = None
        try:
            result = call.fn()
        except BaseException as exc:  # noqa: BLE001
            error = exc

        with self._state_lock:
            call.result = result
            call.error = error
            call.state = "DONE"
            call.done.set()

    def dispatch(
        self,
        fn: Callable[[], Any],
        timeout: float = 25.0,
        *,
        on_running_timeout: Optional[Callable[[_MainThreadCall], Any]] = None,
    ) -> Any:
        app = QCoreApplication.instance()
        target_thread = self.thread() if app is not None else None
        if app is None or target_thread is None:
            raise RuntimeError("Qt GUI application is not available")
        if QThread.currentThread() == target_thread:
            return fn()
        if QCoreApplication.closingDown():
            raise RuntimeError("Qt GUI event loop is unavailable")

        call = _MainThreadCall(fn=fn, done=threading.Event())
        self._invoke_signal.emit(call)

        deadline = time.monotonic() + max(0.01, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if call.done.wait(min(0.1, remaining)):
                break
            if QCoreApplication.closingDown():
                with self._state_lock:
                    if call.state == "PENDING":
                        call.state = "CANCELLED"
                        raise RuntimeError("Qt GUI event loop is unavailable")

        if call.done.is_set():
            if call.error is not None:
                raise call.error
            return call.result

        with self._state_lock:
            state = call.state
            if state == "PENDING":
                call.state = "CANCELLED"

        if state == "DONE":
            if call.error is not None:
                raise call.error
            return call.result

        if state == "PENDING":
            if QCoreApplication.closingDown():
                raise RuntimeError("Qt GUI event loop is unavailable")
            raise TimeoutError("Timed out waiting for Qt GUI main thread")

        # state == "RUNNING": callback has already started on the Qt main thread
        if on_running_timeout is not None:
            return on_running_timeout(call)
        raise TimeoutError("Timed out waiting for Qt GUI main thread")


class McpGuiBridgeServer:
    """Authenticated local Named Pipe / Unix Socket RPC bridge for live Qt GUI state."""

    SUPPORTED_METHODS = frozenset(
        {"list_pages", "open_page", "read_page", "history_step", "save_page"}
    )

    def __init__(
        self,
        main_window: Any = None,
        *,
        app_logic: Any = None,
        editor_controller: Any = None,
        editor_model: Any = None,
        editor_logic: Any = None,
        session_name: Optional[str] = None,
        dispatch_timeout: float = 25.0,
        handshake_timeout: float = DEFAULT_IPC_HANDSHAKE_TIMEOUT,
    ) -> None:
        self.logger = logging.getLogger("desktop_qt_ui.mcp_gui_bridge")
        self._main_window = main_window
        self._app_logic = app_logic
        self._editor_controller = editor_controller
        self._editor_model = editor_model
        self._editor_logic = editor_logic
        self.session_name = session_name
        self.dispatch_timeout = float(dispatch_timeout)
        self.handshake_timeout = max(0.05, float(handshake_timeout))

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._bridge_generation: int = 0
        self._listener: Optional[Listener] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._authkey: Optional[bytes] = None
        self._address: Optional[str] = None
        self._key_file: Optional[Path] = None
        self._lock_fd: Optional[int] = None
        self._owns_key_file = False
        self._dispatcher = _MainThreadDispatcher()

    def bind_main_window(self, main_window: Any) -> None:
        with self._lock:
            self._main_window = main_window

    @property
    def is_running(self) -> bool:
        with self._lock:
            return (
                self._listener is not None
                and self._accept_thread is not None
                and self._accept_thread.is_alive()
                and not self._stop_event.is_set()
            )

    @property
    def address(self) -> Optional[str]:
        with self._lock:
            return self._address

    @property
    def key_file(self) -> Optional[Path]:
        with self._lock:
            return self._key_file

    def _verify_bridge_generation(self, expected_generation: int) -> None:
        with self._lock:
            if self._stop_event.is_set() or self._bridge_generation != expected_generation:
                raise RuntimeError("Live GUI MCP bridge session was stopped or restarted")

    @staticmethod
    def _is_peer_session_alive(address: str, key_file: Path) -> bool:
        if not key_file.is_file():
            return False
        try:
            from mtui_mcp.gui_bridge import call_live_gui

            # Only used as a fallback check when no OS lockfile is held yet
            sess = os.getenv("MTUI_MCP_SESSION", "default")
            expected_addr, _ = gui_session_paths(sess)
            if address == expected_addr:
                res = call_live_gui("list_pages", session_name=sess, timeout_sec=0.75, limit=1)
                return isinstance(res, dict)
        except Exception:
            pass
        try:
            auth = key_file.read_bytes()
            if not auth:
                return False
            packet = json.dumps(
                {"method": "list_pages", "args": {"limit": 1}},
                ensure_ascii=False,
            ).encode("utf-8")
            done = threading.Event()
            ok_box = [False]
            peer_box: list[Any] = [None]

            def _probe() -> None:
                peer = None
                try:
                    peer = Client(address, authkey=None)
                    peer_box[0] = peer
                    answer_challenge(peer, auth)
                    deliver_challenge(peer, auth)
                    peer.send_bytes(packet)
                    if peer.poll(0.75):
                        reply = json.loads(peer.recv_bytes(MAX_GUI_REQUEST_BYTES).decode("utf-8"))
                        ok_box[0] = isinstance(reply, dict) and "result" in reply
                except Exception:
                    ok_box[0] = False
                finally:
                    if peer is not None:
                        try:
                            peer.close()
                        except Exception:
                            pass
                    done.set()

            t = threading.Thread(target=_probe, daemon=True)
            t.start()
            if not done.wait(0.75):
                _abort_ipc_connection(peer_box[0])
                return False
            return ok_box[0]
        except Exception:
            return False

    @staticmethod
    def _write_key_file(key_file: Path, authkey: bytes) -> None:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(key_file.parent),
            prefix=f".{key_file.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "wb") as handle:
                handle.write(authkey)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, key_file)
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return True

            self._dispatcher = _MainThreadDispatcher()
            address, key_file = gui_session_paths(self.session_name)
            addr_key = os.path.normcase(address)

            with _ACTIVE_BRIDGE_LOCK:
                if addr_key in _ACTIVE_BRIDGE_ADDRESSES:
                    self.logger.warning(
                        "Another live manga-translator-ui GUI bridge is already active at %s; skipping bind.",
                        address,
                    )
                    return False
                _ACTIVE_BRIDGE_ADDRESSES.add(addr_key)

            lock_path = key_file.with_suffix(".lock")
            lock_fd = _acquire_session_lockfile(lock_path)
            if lock_fd is None:
                with _ACTIVE_BRIDGE_LOCK:
                    _ACTIVE_BRIDGE_ADDRESSES.discard(addr_key)
                self.logger.warning(
                    "Another live manga-translator-ui GUI bridge holds ownership lock for %s; skipping bind.",
                    address,
                )
                return False

            if self._is_peer_session_alive(address, key_file):
                _release_session_lockfile(lock_fd)
                with _ACTIVE_BRIDGE_LOCK:
                    _ACTIVE_BRIDGE_ADDRESSES.discard(addr_key)
                self.logger.warning(
                    "Another live manga-translator-ui GUI bridge is already active at %s; skipping bind.",
                    address,
                )
                return False

            if os.name != "nt" and os.path.exists(address):
                try:
                    os.unlink(address)
                except OSError as exc:
                    _release_session_lockfile(lock_fd)
                    with _ACTIVE_BRIDGE_LOCK:
                        _ACTIVE_BRIDGE_ADDRESSES.discard(addr_key)
                    self.logger.warning("Could not unlink stale GUI socket %s: %s", address, exc)
                    return False

            authkey = secrets.token_bytes(32)
            try:
                # Bind with authkey=None so accept() never blocks on stalled client handshakes;
                # HMAC challenge/response runs per-connection with a deadline in _handle_connection.
                listener = Listener(address, authkey=None)
            except OSError as exc:
                _release_session_lockfile(lock_fd)
                with _ACTIVE_BRIDGE_LOCK:
                    _ACTIVE_BRIDGE_ADDRESSES.discard(addr_key)
                self.logger.warning("Failed to bind live GUI IPC listener at %s: %s", address, exc)
                return False

            try:
                self._write_key_file(key_file, authkey)
            except Exception as exc:
                try:
                    listener.close()
                except Exception:
                    pass
                _release_session_lockfile(lock_fd)
                with _ACTIVE_BRIDGE_LOCK:
                    _ACTIVE_BRIDGE_ADDRESSES.discard(addr_key)
                self.logger.warning("Failed to write live GUI IPC key %s: %s", key_file, exc)
                return False

            self._bridge_generation += 1
            bridge_gen = self._bridge_generation
            self._stop_event.clear()
            self._listener = listener
            self._authkey = authkey
            self._address = address
            self._key_file = key_file
            self._lock_fd = lock_fd
            self._owns_key_file = True

            accept_thread = threading.Thread(
                target=self._accept_loop,
                args=(listener, authkey, bridge_gen),
                name="mtui-gui-bridge-accept",
                daemon=True,
            )
            self._accept_thread = accept_thread
            accept_thread.start()
            return True

    def stop(self, timeout: float = 3.0) -> None:
        with self._lock:
            self._stop_event.set()
            self._bridge_generation += 1
            listener = self._listener
            accept_thread = self._accept_thread
            authkey = self._authkey
            address = self._address
            key_file = self._key_file
            lock_fd = self._lock_fd
            owns_key = self._owns_key_file

            self._listener = None
            self._accept_thread = None
            self._authkey = None
            self._lock_fd = None
            self._owns_key_file = False

        if owns_key and key_file is not None:
            try:
                if key_file.is_file() and (authkey is None or key_file.read_bytes() == authkey):
                    key_file.unlink(missing_ok=True)
            except OSError:
                pass

        # Wake any blocking accept() call (especially Windows Named Pipe ConnectNamedPipe)
        if (
            listener is not None
            and accept_thread is not None
            and accept_thread.is_alive()
            and address
        ):
            def _poke() -> None:
                try:
                    with Client(address, authkey=None):
                        pass
                except Exception:
                    pass

            poker = threading.Thread(target=_poke, name="mtui-gui-bridge-poke", daemon=True)
            poker.start()
            poker.join(timeout=0.4)

        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass

        if accept_thread is not None and accept_thread is not threading.current_thread():
            accept_thread.join(timeout=max(0.1, float(timeout)))

        if owns_key and os.name != "nt" and address and os.path.exists(address):
            try:
                os.unlink(address)
            except OSError:
                pass

        _release_session_lockfile(lock_fd)
        if owns_key and address:
            with _ACTIVE_BRIDGE_LOCK:
                _ACTIVE_BRIDGE_ADDRESSES.discard(os.path.normcase(address))

    def _accept_loop(self, listener: Listener, authkey: bytes, bridge_gen: int) -> None:
        while not self._stop_event.is_set():
            try:
                conn = listener.accept()
            except (OSError, EOFError, ValueError):
                if self._stop_event.is_set():
                    break
                continue
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                self.logger.debug("Live GUI IPC accept error: %s", exc)
                continue

            if self._stop_event.is_set():
                try:
                    conn.close()
                except Exception:
                    pass
                break

            worker = threading.Thread(
                target=self._handle_connection,
                args=(conn, authkey, bridge_gen),
                name="mtui-gui-bridge-conn",
                daemon=True,
            )
            worker.start()

    def _authenticate_connection(self, conn: Any, authkey: bytes) -> bool:
        timed_out = threading.Event()

        def _on_handshake_timeout() -> None:
            timed_out.set()
            _abort_ipc_connection(conn)

        timer = threading.Timer(self.handshake_timeout, _on_handshake_timeout)
        timer.daemon = True
        timer.start()
        try:
            deliver_challenge(conn, authkey)
            answer_challenge(conn, authkey)
            return not timed_out.is_set()
        except Exception as exc:
            self.logger.debug("Live GUI IPC authentication failed: %s", exc)
            return False
        finally:
            timer.cancel()

    def _handle_connection(self, conn: Any, authkey: bytes, bridge_gen: int) -> None:
        try:
            if not self._authenticate_connection(conn, authkey):
                return

            if not conn.poll(5.0):
                return
            try:
                raw = conn.recv_bytes(MAX_GUI_REQUEST_BYTES)
            except BufferTooShort:
                self._send_reply(conn, {"error": "Live GUI IPC request exceeds 256 KiB"})
                return

            if len(raw) > MAX_GUI_REQUEST_BYTES:
                self._send_reply(conn, {"error": "Live GUI IPC request exceeds 256 KiB"})
                return

            try:
                request = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                self._send_reply(conn, {"error": f"Invalid JSON request: {exc}"})
                return

            if not isinstance(request, dict):
                self._send_reply(conn, {"error": "IPC request must be a JSON object"})
                return

            method = request.get("method")
            args = request.get("args", {})
            if not isinstance(method, str) or method not in self.SUPPORTED_METHODS:
                self._send_reply(conn, {"error": f"Unsupported live GUI method: {method!r}"})
                return
            if args is None:
                args = {}
            if not isinstance(args, dict):
                self._send_reply(conn, {"error": "IPC request 'args' must be an object"})
                return

            try:
                result = self._execute_rpc(method, args, bridge_generation=bridge_gen)
                self._send_reply(conn, {"result": _json_sanitize(result)})
            except Exception as exc:
                self._send_reply(conn, {"error": str(exc)})
        except Exception as exc:
            self.logger.debug("Live GUI IPC connection handler failed: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _send_reply(conn: Any, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.send_bytes(data)

    # ------------------------------------------------------------------
    # Component & State Resolution Helpers (called on Qt main thread)
    # ------------------------------------------------------------------

    def _resolve_components(self) -> tuple[Any, Any, Any, Any, Any]:
        from services import ServiceManager

        mw = self._main_window
        app_logic = (
            self._app_logic
            or (getattr(mw, "app_logic", None) if mw is not None else None)
            or ServiceManager.get_service("app_logic")
        )
        editor_controller = self._editor_controller or (
            getattr(mw, "editor_controller", None) if mw is not None else None
        )
        editor_model = self._editor_model or (
            getattr(mw, "editor_model", None)
            if mw is not None
            else (getattr(editor_controller, "model", None) if editor_controller is not None else None)
        )
        editor_logic = self._editor_logic or (
            getattr(mw, "editor_logic", None) if mw is not None else None
        )
        return mw, app_logic, editor_controller, editor_model, editor_logic

    def _compute_revision_on_main(
        self,
        editor_controller: Any,
        editor_model: Any,
    ) -> str:
        if editor_model is None:
            return "gui_rev_empty"
        source_path = editor_model.get_source_image_path()
        if not source_path:
            return "gui_rev_empty"

        history_service = getattr(editor_controller, "history_service", None)
        if history_service is None:
            from services import get_history_service

            history_service = get_history_service()

        regions = editor_model.get_regions() or []
        doc_id = int(editor_model.get_document_id())
        mask_rev = int(editor_model.get_mask_revision())
        hist_rev = int(history_service.current_revision()) if history_service is not None else 0
        is_clean = bool(history_service.is_clean()) if history_service is not None else True

        hasher = hashlib.sha256()
        hasher.update(os.path.normcase(os.path.abspath(source_path)).encode("utf-8"))
        hasher.update(
            f"|doc={doc_id}|hist={hist_rev}|mask={mask_rev}|clean={int(is_clean)}|n={len(regions)}|".encode(
                "utf-8"
            )
        )
        for idx, reg in enumerate(regions):
            if not isinstance(reg, dict):
                continue
            snippet = (
                f"{idx}:{reg.get('text', '')}:{reg.get('translation', '')}:"
                f"{reg.get('font_size', '')}:{reg.get('font_family', '')}:"
                f"{reg.get('center', '')}:{reg.get('angle', '')}|"
            )
            hasher.update(snippet.encode("utf-8", errors="replace"))
        return "gui_rev_" + hasher.hexdigest()[:16]

    @staticmethod
    def _summarize_region(
        index: int,
        reg: dict[str, Any],
        *,
        include_polygons: bool = False,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "region_index": index,
            "source_text": str(reg.get("text", "") or ""),
            "translation": str(reg.get("translation", "") or ""),
            "font_size": reg.get("font_size"),
            "font_family": str(reg.get("font_family", "") or ""),
            "font_color": reg.get("font_color"),
            "direction": reg.get("direction", "auto"),
            "alignment": reg.get("alignment", "auto"),
            "angle": float(reg.get("angle", 0.0) or 0.0),
            "center": list(reg["center"]) if isinstance(reg.get("center"), (list, tuple)) else None,
            "has_rich_text": bool(reg.get("translation_rich")),
            "has_perspective_quad": bool(reg.get("white_frame_quad_local")),
        }
        if "region_uid" in reg:
            summary["region_uid"] = reg["region_uid"]
        if include_polygons:
            summary["lines"] = reg.get("lines", [])
        return summary

    # ------------------------------------------------------------------
    # RPC Dispatch & Implementations
    # ------------------------------------------------------------------

    def _execute_rpc(
        self,
        method: str,
        args: dict[str, Any],
        *,
        bridge_generation: Optional[int] = None,
    ) -> dict[str, Any]:
        if bridge_generation is None:
            with self._lock:
                bridge_generation = self._bridge_generation

        self._verify_bridge_generation(bridge_generation)

        if method == "open_page":
            return self._rpc_open_page(args, bridge_generation=bridge_generation)

        if method == "save_page":
            def _on_save_running_timeout(call: _MainThreadCall) -> dict[str, Any]:
                if call.done.wait(min(0.25, max(0.02, self.dispatch_timeout * 0.25))):
                    if call.error is not None:
                        raise call.error
                    return call.result
                return {
                    "status": "in_progress",
                    "saved": False,
                    "save_in_progress": True,
                    "message": "Save has already started on the Qt GUI main thread and is still completing",
                }

            return self._dispatcher.dispatch(
                lambda: self._dispatch_main_thread_method(method, args, bridge_generation),
                timeout=self.dispatch_timeout,
                on_running_timeout=_on_save_running_timeout,
            )

        return self._dispatcher.dispatch(
            lambda: self._dispatch_main_thread_method(method, args, bridge_generation),
            timeout=self.dispatch_timeout,
        )

    def _dispatch_main_thread_method(
        self,
        method: str,
        args: dict[str, Any],
        bridge_generation: int,
    ) -> dict[str, Any]:
        self._verify_bridge_generation(bridge_generation)
        if method == "list_pages":
            return self._rpc_list_pages_main(args)
        if method == "read_page":
            return self._rpc_read_page_main(args)
        if method == "history_step":
            return self._rpc_history_step_main(args)
        if method == "save_page":
            return self._rpc_save_page_main(args)
        raise ValueError(f"Unsupported live GUI method: {method!r}")

    def _rpc_list_pages_main(self, args: dict[str, Any]) -> dict[str, Any]:
        from services import get_state_manager

        mw, app_logic, editor_controller, editor_model, editor_logic = self._resolve_components()
        state_manager = (
            getattr(app_logic, "state_manager", None)
            if app_logic is not None
            else None
        ) or get_state_manager()

        translate_future = getattr(app_logic, "_translate_future", None) if app_logic is not None else None
        scan_future = getattr(app_logic, "_scan_future", None) if app_logic is not None else None
        translation_running = bool(
            (state_manager is not None and state_manager.is_translating())
            or (app_logic is not None and getattr(app_logic, "current_worker", None) is not None)
            or (translate_future is not None and not translate_future.done())
            or (scan_future is not None and not scan_future.done())
        )

        raw_pages: list[str] = []
        seen_keys: set[str] = set()

        def _add_page(p: Optional[str]) -> None:
            if not p:
                return
            norm = os.path.abspath(os.path.normpath(p))
            key = os.path.normcase(norm)
            if key not in seen_keys:
                seen_keys.add(key)
                raw_pages.append(norm)

        if mw is not None:
            snapshot = getattr(mw, "_file_catalog_snapshot", None)
            if snapshot is not None:
                for p in getattr(snapshot, "editor_files", ()) or ():
                    _add_page(p)

        if editor_logic is not None and getattr(editor_logic, "file_model", None) is not None:
            for item in getattr(editor_logic.file_model, "files", ()) or ():
                _add_page(getattr(item, "path", None))

        if not raw_pages and app_logic is not None:
            for src in getattr(app_logic, "source_files", ()) or ():
                if os.path.isfile(src):
                    _add_page(src)
                elif os.path.isdir(src):
                    try:
                        from mtui_mcp.path_resolver import list_manga_pages_in_path

                        for page_info in list_manga_pages_in_path(src):
                            _add_page(str(page_info.source_path))
                    except Exception:
                        pass

        active_page = editor_model.get_source_image_path() if editor_model is not None else None
        if active_page:
            _add_page(active_page)
        active_key = (
            os.path.normcase(os.path.abspath(os.path.normpath(active_page)))
            if active_page
            else None
        )

        offset = max(0, int(args.get("offset", 0) or 0))
        raw_limit = args.get("limit", 50)
        limit = max(1, min(500, int(raw_limit))) if raw_limit is not None else 50

        sliced = raw_pages[offset : offset + limit]
        page_items = [
            {
                "page_index": offset + idx,
                "path": p,
                "page_name": os.path.basename(p),
                "is_active": bool(active_key and os.path.normcase(p) == active_key),
            }
            for idx, p in enumerate(sliced)
        ]

        dirty = False
        unfinished_exports = 0
        if editor_controller is not None and getattr(editor_controller, "export_service", None) is not None:
            unfinished_exports = int(editor_controller.export_service.unfinished_count())
            if active_page:
                dirty = bool(editor_controller.export_service.has_unsaved_changes())

        active_view = "main"
        if (
            mw is not None
            and getattr(mw, "editor_view", None) is not None
            and getattr(mw, "stacked_widget", None) is not None
            and mw.stacked_widget.currentWidget() is mw.editor_view
        ):
            active_view = "editor"

        return {
            "status": "ok",
            "translation_running": translation_running,
            "unfinished_exports": unfinished_exports,
            "active_view": active_view,
            "active_page": active_page,
            "active_page_dirty": dirty,
            "revision": self._compute_revision_on_main(editor_controller, editor_model),
            "total_pages": len(raw_pages),
            "source_files": list(getattr(app_logic, "source_files", []) or []) if app_logic is not None else [],
            "pages": page_items,
        }

    def _rpc_read_page_main(self, args: dict[str, Any]) -> dict[str, Any]:
        _mw, _app_logic, editor_controller, editor_model, _editor_logic = self._resolve_components()
        if editor_controller is not None:
            editor_controller.commit_pending_edits()

        source_path = editor_model.get_source_image_path() if editor_model is not None else None
        history_service = getattr(editor_controller, "history_service", None) if editor_controller is not None else None
        if history_service is None:
            from services import get_history_service

            history_service = get_history_service()

        revision = self._compute_revision_on_main(editor_controller, editor_model)
        if not source_path or editor_model is None:
            return {
                "status": "ok",
                "page_open": False,
                "source_path": None,
                "page_name": None,
                "revision": revision,
                "dirty": False,
                "can_undo": False,
                "can_redo": False,
                "region_count": 0,
                "selected_indices": [],
                "regions": [],
            }

        include_polygons = bool(args.get("include_polygons", False))
        offset = max(0, int(args.get("offset", 0) or 0))
        raw_limit = args.get("limit")
        all_regions = editor_model.get_regions() or []
        end_idx = len(all_regions) if raw_limit is None else offset + max(0, int(raw_limit))

        region_summaries = [
            self._summarize_region(i, all_regions[i], include_polygons=include_polygons)
            for i in range(offset, min(len(all_regions), end_idx))
        ]

        dirty = (
            bool(editor_controller.export_service.has_unsaved_changes())
            if editor_controller is not None and getattr(editor_controller, "export_service", None) is not None
            else False
        )

        return {
            "status": "ok",
            "page_open": True,
            "source_path": source_path,
            "page_name": os.path.basename(source_path),
            "revision": revision,
            "dirty": dirty,
            "can_undo": bool(history_service.can_undo()) if history_service is not None else False,
            "can_redo": bool(history_service.can_redo()) if history_service is not None else False,
            "region_count": len(all_regions),
            "selected_indices": list(editor_model.get_selection() or []),
            "regions": region_summaries,
        }

    @staticmethod
    def _reserve_doc_load_generation(doc_service: Any) -> int:
        cancel_fn = getattr(doc_service, "_cancel_pending_load", None)
        if callable(cancel_fn):
            return int(cancel_fn())
        next_gen = int(getattr(doc_service, "_load_generation", 0)) + 1
        setattr(doc_service, "_load_generation", next_gen)
        return next_gen

    def _rpc_open_page(self, args: dict[str, Any], *, bridge_generation: int) -> dict[str, Any]:
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            raise ValueError("path is required for open_page")
        force = bool(args.get("force", False))
        reload_if_same = bool(args.get("reload", False))

        candidate_path = os.path.abspath(os.path.normpath(raw_path))
        try:
            from mtui_mcp.path_resolver import resolve_page_paths

            resolved_candidate = str(resolve_page_paths(candidate_path).source_path)
            if os.path.isfile(resolved_candidate):
                candidate_path = resolved_candidate
        except Exception:
            pass

        def _check_or_save_unsaved_on_main(editor_controller: Any, doc_service: Any) -> None:
            has_unsaved = bool(editor_controller.export_service.has_unsaved_changes())
            if not has_unsaved:
                return
            auto_save = bool(doc_service._auto_save_on_switch_enabled())
            auto_export = bool(doc_service._auto_export_on_switch_enabled())
            if auto_save:
                if not editor_controller.save_editor_state():
                    raise RuntimeError("Auto-save of current editor page failed; switch aborted")
                if auto_export:
                    editor_controller.export_image(automatic=True)
            elif not force:
                raise RuntimeError(
                    "Current editor page has unsaved changes; call save_page first or pass force=True"
                )

        def _prepare_on_main() -> tuple[Any, str, int, Optional[dict[str, Any]]]:
            self._verify_bridge_generation(bridge_generation)
            mw, _app_logic, editor_controller, editor_model, _editor_logic = self._resolve_components()
            if mw is not None and hasattr(mw, "_ensure_editor_initialized"):
                mw._ensure_editor_initialized()
                mw, _app_logic, editor_controller, editor_model, _editor_logic = self._resolve_components()

            if editor_controller is None or editor_model is None:
                raise RuntimeError("Live GUI editor is not initialized")

            doc_service = getattr(editor_controller, "document_service", None)
            if doc_service is None:
                raise RuntimeError("Editor document service is not available")

            source_path, _display_path = doc_service.resolve_editor_image_paths(candidate_path)
            if not os.path.isfile(source_path):
                raise FileNotFoundError(f"Page image not found: {raw_path}")

            editor_controller.commit_pending_edits()
            current_source = editor_model.get_source_image_path()
            same_page = bool(
                current_source
                and os.path.normcase(os.path.abspath(current_source))
                == os.path.normcase(os.path.abspath(source_path))
            )

            if same_page and not reload_if_same:
                if mw is not None and getattr(mw, "editor_view", None) is not None:
                    mw.switchTo(mw.editor_view)
                return doc_service, source_path, 0, self._rpc_read_page_main({"limit": 50})

            _check_or_save_unsaved_on_main(editor_controller, doc_service)

            # Reserve a monotonic load generation WITHOUT clearing current editor state yet,
            # so a failed background load never destroys the open page or unsaved edits (F73).
            expected_load_gen = self._reserve_doc_load_generation(doc_service)
            return doc_service, source_path, expected_load_gen, None

        doc_service, source_path, expected_load_gen, already_open_payload = self._dispatcher.dispatch(
            _prepare_on_main,
            timeout=self.dispatch_timeout,
        )
        if already_open_payload is not None:
            return already_open_payload

        from editor.document_load_worker import DocumentLoadWorker
        from editor.document_state import DocumentLoadFailure, DocumentSnapshot

        load_result = DocumentLoadWorker(
            doc_service,
            source_path,
            doc_service._aux_load_executor,
        ).load()

        if isinstance(load_result, DocumentLoadFailure):
            raise RuntimeError(f"Failed to load page {source_path}: {load_result.error}")
        if not isinstance(load_result, DocumentSnapshot):
            raise RuntimeError(f"Unexpected editor load result for {source_path}")

        self._verify_bridge_generation(bridge_generation)

        def _install_on_main() -> dict[str, Any]:
            self._verify_bridge_generation(bridge_generation)
            current_gen = int(getattr(doc_service, "_load_generation", expected_load_gen))
            if current_gen != expected_load_gen:
                raise RuntimeError("Page open request was superseded by a newer page load")

            mw, _app_logic, editor_controller, _em, editor_logic = self._resolve_components()
            if editor_controller is not None:
                editor_controller.commit_pending_edits()
                _check_or_save_unsaved_on_main(editor_controller, doc_service)

            if mw is not None and getattr(mw, "editor_view", None) is not None:
                mw.switchTo(mw.editor_view)

            doc_service.clear_editor_state()
            install_gen = int(getattr(doc_service, "_load_generation", expected_load_gen))
            apply_load_result = getattr(doc_service, "apply_load_result", None)
            if callable(apply_load_result):
                apply_load_result((install_gen, load_result))
            else:
                doc_service.apply_loaded_data_to_model(load_result)

            if editor_logic is not None and getattr(editor_logic, "file_model", None) is not None:
                if not editor_logic.file_model.get_file_item(source_path):
                    try:
                        editor_logic.add_files([source_path])
                    except Exception:
                        pass
            return self._rpc_read_page_main({"limit": 50})

        return self._dispatcher.dispatch(_install_on_main, timeout=self.dispatch_timeout)

    def _rpc_history_step_main(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "").strip().lower()
        if action not in ("undo", "redo"):
            raise ValueError(f"Unsupported history_step action: {action!r}")

        _mw, _app_logic, editor_controller, editor_model, _editor_logic = self._resolve_components()
        if editor_controller is None or editor_model is None:
            raise RuntimeError("Live GUI editor is not initialized")

        editor_controller.commit_pending_edits()
        source_path = editor_model.get_source_image_path()
        if not source_path:
            raise RuntimeError("No page is currently open in the live GUI editor")

        expected_revision = args.get("revision")
        current_revision = self._compute_revision_on_main(editor_controller, editor_model)
        if expected_revision and str(expected_revision) != current_revision:
            raise RuntimeError(
                f"Live GUI revision mismatch: expected {expected_revision}, current {current_revision}"
            )

        history_service = editor_controller.history_service
        applied = False
        if action == "undo" and history_service.can_undo():
            editor_controller.undo()
            applied = True
        elif action == "redo" and history_service.can_redo():
            editor_controller.redo()
            applied = True

        new_revision = self._compute_revision_on_main(editor_controller, editor_model)
        return {
            "status": "ok",
            "action": action,
            "applied": applied,
            "source_path": source_path,
            "revision": new_revision,
            "can_undo": bool(history_service.can_undo()),
            "can_redo": bool(history_service.can_redo()),
            "dirty": bool(editor_controller.export_service.has_unsaved_changes()),
        }

    def _rpc_save_page_main(self, args: dict[str, Any]) -> dict[str, Any]:
        _mw, _app_logic, editor_controller, editor_model, _editor_logic = self._resolve_components()
        if editor_controller is None or editor_model is None:
            raise RuntimeError("Live GUI editor is not initialized")

        editor_controller.commit_pending_edits()
        source_path = editor_model.get_source_image_path()
        if not source_path:
            raise RuntimeError("No page is currently open in the live GUI editor")

        expected_revision = args.get("revision")
        current_revision = self._compute_revision_on_main(editor_controller, editor_model)
        if expected_revision and str(expected_revision) != current_revision:
            raise RuntimeError(
                f"Live GUI revision mismatch: expected {expected_revision}, current {current_revision}"
            )

        saved = bool(editor_controller.save_editor_state())
        if not saved:
            raise RuntimeError(f"Editor failed to save page state for {source_path}")

        export_queued = False
        if bool(args.get("export_rendered", False)):
            export_queued = editor_controller.export_image(automatic=False) is not None

        new_revision = self._compute_revision_on_main(editor_controller, editor_model)
        return {
            "status": "ok",
            "saved": True,
            "export_queued": export_queued,
            "source_path": source_path,
            "revision": new_revision,
            "dirty": bool(editor_controller.export_service.has_unsaved_changes()),
        }


class McpService:
    """Lifecycle controller for the embedded MCP HTTP server and live GUI bridge."""

    def __init__(
        self,
        root_dir: str,
        *,
        main_window: Any = None,
        session_name: Optional[str] = None,
    ) -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.logger = logging.getLogger("desktop_qt_ui.mcp_service")
        self._lock = threading.RLock()
        self._main_window = main_window
        self._session_name = session_name

        self._cli_enabled: Optional[bool] = None
        self._cli_host: Optional[str] = None
        self._cli_port: Optional[int] = None

        self._host: str = DEFAULT_MCP_HOST
        self._port: int = DEFAULT_MCP_PORT
        self._has_started_once = False
        self._http_server: Optional[Any] = None
        self._gui_bridge = McpGuiBridgeServer(
            main_window=main_window,
            session_name=session_name,
        )
        self._atexit_registered = False

    def _ensure_atexit_hook(self) -> None:
        if self._atexit_registered:
            return
        self._atexit_registered = True
        weak_self = weakref.ref(self)

        def _cleanup() -> None:
            svc = weak_self()
            if svc is not None:
                svc.shutdown()

        atexit.register(_cleanup)

    def bind_main_window(self, main_window: Any) -> None:
        with self._lock:
            self._main_window = main_window
            self._gui_bridge.bind_main_window(main_window)

    def set_cli_overrides(
        self,
        *,
        enabled: Optional[bool] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._cli_enabled = enabled
            self._cli_host = host.strip() if isinstance(host, str) and host.strip() else None
            self._cli_port = int(port) if port is not None else None

    @property
    def has_started_once(self) -> bool:
        with self._lock:
            return self._has_started_once

    def is_autostart_suppressed_by_pytest(self) -> bool:
        """Skip automatic MainWindow startup under pytest unless forced."""
        with self._lock:
            if self._cli_enabled is True:
                return False
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        force = parse_env_bool("MTUI_MCP_FORCE_AUTOSTART")
        return not bool(force)

    def resolve_effective_config(self, config: Any = None) -> tuple[bool, str, int]:
        if config is None:
            from services import get_config_service

            config_service = get_config_service()
            if config_service is not None:
                config = config_service.get_config()

        app_cfg = getattr(config, "app", None) if config is not None else None
        enabled = bool(getattr(app_cfg, "mcp_server_enabled", True))
        host = str(getattr(app_cfg, "mcp_server_host", DEFAULT_MCP_HOST) or DEFAULT_MCP_HOST).strip()
        raw_cfg_port = getattr(app_cfg, "mcp_server_port", None)
        port = int(raw_cfg_port) if raw_cfg_port is not None else DEFAULT_MCP_PORT

        env_enabled = parse_env_bool("MTUI_MCP_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled
        env_host = os.environ.get("MTUI_MCP_HOST")
        if env_host and env_host.strip():
            host = env_host.strip()
        env_port = parse_env_port("MTUI_MCP_PORT")
        if env_port is not None:
            port = env_port

        with self._lock:
            if self._cli_enabled is not None:
                enabled = self._cli_enabled
            if self._cli_host:
                host = self._cli_host
            if self._cli_port is not None:
                port = self._cli_port

        return enabled, host, port

    @property
    def http_running(self) -> bool:
        with self._lock:
            return bool(self._http_server is not None and self._http_server.is_running)

    @property
    def gui_bridge_running(self) -> bool:
        with self._lock:
            return self._gui_bridge.is_running

    @property
    def is_running(self) -> bool:
        return self.http_running or self.gui_bridge_running

    @property
    def host(self) -> str:
        with self._lock:
            if self._http_server is not None:
                return str(self._http_server.host)
            return self._host

    @property
    def port(self) -> int:
        with self._lock:
            if self._http_server is not None and self._http_server.bound_port is not None:
                return int(self._http_server.bound_port)
            return self._port

    @property
    def bound_port(self) -> Optional[int]:
        with self._lock:
            if self._http_server is not None:
                return self._http_server.bound_port
            return None

    @property
    def streamable_http_url(self) -> Optional[str]:
        with self._lock:
            if self._http_server is not None and self._http_server.is_running:
                return str(self._http_server.streamable_http_url)
            return None

    @property
    def sse_url(self) -> Optional[str]:
        with self._lock:
            if self._http_server is not None and self._http_server.is_running:
                return str(self._http_server.sse_url)
            return None

    @property
    def health_url(self) -> Optional[str]:
        with self._lock:
            if self._http_server is not None and self._http_server.is_running:
                return str(self._http_server.health_url)
            return None

    @property
    def pipe_address(self) -> Optional[str]:
        return self._gui_bridge.address

    @property
    def key_file(self) -> Optional[Path]:
        return self._gui_bridge.key_file

    @property
    def gui_bridge(self) -> McpGuiBridgeServer:
        return self._gui_bridge

    def is_healthy(self) -> bool:
        return True

    def autostart(self) -> bool:
        """Automatic startup entry point used by MainWindow after window creation."""
        if self.is_autostart_suppressed_by_pytest():
            self.logger.debug("Skipping automatic MCP startup under PYTEST_CURRENT_TEST.")
            return False
        return self.start_from_config()

    def start_from_config(self, config: Any = None) -> bool:
        enabled, host, port = self.resolve_effective_config(config)
        if not enabled:
            self.shutdown()
            return False
        return self.start(host=host, port=port)

    def start(
        self,
        host: str = DEFAULT_MCP_HOST,
        port: int = DEFAULT_MCP_PORT,
        *,
        start_http: bool = True,
        start_gui_bridge: bool = True,
        warm_up: bool = False,
        timeout: float = 5.0,
    ) -> bool:
        host = (host or DEFAULT_MCP_HOST).strip()
        port = int(port)

        with self._lock:
            self._ensure_atexit_hook()
            self._has_started_once = True
            self._host = host
            self._port = port

            bridge_ok = False
            if start_gui_bridge:
                bridge_ok = self._gui_bridge.start()

            http_ok = False
            if start_http:
                if (
                    self._http_server is not None
                    and self._http_server.is_running
                    and str(self._http_server.host) == host
                    and (
                        port == 0
                        or int(self._http_server.port) == port
                        or self._http_server.bound_port == port
                    )
                ):
                    http_ok = True
                else:
                    if self._http_server is not None:
                        try:
                            self._http_server.stop()
                        except Exception:
                            pass
                        self._http_server = None

                    try:
                        from mtui_mcp.embedded_server import EmbeddedMcpHttpServer

                        server = EmbeddedMcpHttpServer(
                            host=host,
                            port=port,
                            mtui_root=self.root_dir,
                            warm_up=warm_up,
                        )
                        http_ok = bool(server.start(timeout=timeout))
                        if http_ok:
                            self._http_server = server
                    except Exception as exc:
                        self.logger.warning(
                            "Embedded MCP HTTP server could not start on %s:%s: %s",
                            host,
                            port,
                            exc,
                        )
                        http_ok = False

            if http_ok and self._http_server is not None:
                self.logger.info(
                    "Built-in MCP server active at %s (SSE: %s, GUI bridge: %s)",
                    self._http_server.streamable_http_url,
                    self._http_server.sse_url,
                    "on" if bridge_ok else "off",
                )
            elif bridge_ok and not start_http:
                self.logger.info("Live GUI MCP bridge active at %s", self._gui_bridge.address)

            if start_http and start_gui_bridge:
                return http_ok or bridge_ok
            if start_http:
                return http_ok
            if start_gui_bridge:
                return bridge_ok
            return False

    def apply_config(
        self,
        enabled: bool,
        host: Optional[str] = None,
        port: Optional[int] = None,
        *,
        override_host: bool = False,
        override_port: bool = False,
        override_enabled: bool = True,
    ) -> bool:
        """Apply live UI or reloaded setting changes while preserving unmodified CLI overrides."""
        with self._lock:
            if override_enabled:
                self._cli_enabled = None
            elif self._cli_enabled is not None:
                enabled = self._cli_enabled

            if override_host:
                self._cli_host = None
            if override_port:
                self._cli_port = None

            if self._cli_host:
                effective_host = self._cli_host
            elif host is not None and str(host).strip():
                effective_host = str(host).strip()
            else:
                env_host = os.environ.get("MTUI_MCP_HOST")
                effective_host = (
                    env_host.strip()
                    if env_host and env_host.strip()
                    else (self._host or DEFAULT_MCP_HOST)
                )

            if self._cli_port is not None:
                effective_port = self._cli_port
            elif port is not None:
                effective_port = int(port)
            else:
                env_port = parse_env_port("MTUI_MCP_PORT")
                effective_port = env_port if env_port is not None else self._port

        if not bool(enabled):
            self.shutdown()
            return False
        return self.start(host=effective_host, port=effective_port)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Idempotently stop both the live GUI bridge and the embedded HTTP MCP server."""
        with self._lock:
            http_server = self._http_server
            self._http_server = None

        try:
            self._gui_bridge.stop(timeout=min(3.0, max(0.5, float(timeout))))
        except Exception as exc:
            self.logger.warning("Error stopping MCP GUI bridge: %s", exc)

        if http_server is not None:
            try:
                http_server.stop(timeout=timeout)
            except Exception as exc:
                self.logger.warning("Error stopping embedded MCP HTTP server: %s", exc)
