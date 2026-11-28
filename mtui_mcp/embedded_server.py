"""Embedded background HTTP/SSE MCP server for manga-translator-ui auto-start."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import inspect
import ipaddress
import logging
import os
import socket
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__
from .config import get_runtime_settings
from .server import mcp
from .worker.client import get_worker_client

logger = logging.getLogger("mtui_mcp.embedded_server")

_EMBEDDED_WARMUP_LOCK = threading.RLock()
_EMBEDDED_LIFECYCLE_LOCK = threading.RLock()
_ACTIVE_SERVER_GENERATIONS: dict[
    tuple[int, int],
    tuple[weakref.ref[EmbeddedMcpHttpServer], uvicorn.Server, threading.Thread],
] = {}
_IN_PROGRESS_WARMUP_GENERATIONS: set[tuple[int, int]] = set()
_PENDING_STOP_TOKENS: set[object] = set()
_LAST_OWNED_WORKER_PROC: Any = None
_UNOWNED_EXTERNAL_WORKER_PROC: Any = None
_UNOWNED_EXTERNAL_PROCELESS: bool = False


def _is_worker_currently_alive(wc: Any) -> tuple[bool, bool, Any]:
    has_proc_attr = hasattr(wc, "_proc")
    proc = getattr(wc, "_proc", None)
    proc_alive = proc is not None and (
        not callable(getattr(proc, "poll", None)) or proc.poll() is None
    )
    running = bool(getattr(wc, "is_running", False)) and (
        proc_alive if has_proc_attr else True
    )
    return running, has_proc_attr, proc


def _prune_dead_generations_locked() -> None:
    stale_keys = [
        key
        for key, (srv_ref, srv_obj, thr_obj) in _ACTIVE_SERVER_GENERATIONS.items()
        if (
            srv_ref() is None
            or srv_obj is None
            or srv_obj.should_exit
            or thr_obj is None
            or not thr_obj.is_alive()
        )
    ]
    for key in stale_keys:
        _ACTIVE_SERVER_GENERATIONS.pop(key, None)


def _refresh_external_worker_snapshot_locked() -> None:
    global _UNOWNED_EXTERNAL_WORKER_PROC, _UNOWNED_EXTERNAL_PROCELESS
    wc = get_worker_client()
    running, has_proc_attr, proc = _is_worker_currently_alive(wc)
    if running:
        if has_proc_attr:
            if _LAST_OWNED_WORKER_PROC is not None and proc is _LAST_OWNED_WORKER_PROC:
                _UNOWNED_EXTERNAL_WORKER_PROC = None
                _UNOWNED_EXTERNAL_PROCELESS = False
            else:
                _UNOWNED_EXTERNAL_WORKER_PROC = proc
                _UNOWNED_EXTERNAL_PROCELESS = False
        else:
            _UNOWNED_EXTERNAL_WORKER_PROC = None
            _UNOWNED_EXTERNAL_PROCELESS = True
    else:
        _UNOWNED_EXTERNAL_WORKER_PROC = None
        _UNOWNED_EXTERNAL_PROCELESS = False


def _mark_worker_owned_locked(*, proc: Any, has_proc_attr: bool) -> None:
    global _LAST_OWNED_WORKER_PROC, _UNOWNED_EXTERNAL_WORKER_PROC, _UNOWNED_EXTERNAL_PROCELESS
    if has_proc_attr:
        if proc is not None:
            _LAST_OWNED_WORKER_PROC = proc
            if _UNOWNED_EXTERNAL_WORKER_PROC is proc:
                _UNOWNED_EXTERNAL_WORKER_PROC = None
    else:
        _UNOWNED_EXTERNAL_PROCELESS = False


def _is_unowned_external_worker_locked(
    *,
    proc: Any,
    has_proc_attr: bool,
) -> bool:
    if has_proc_attr:
        return _UNOWNED_EXTERNAL_WORKER_PROC is not None and proc is _UNOWNED_EXTERNAL_WORKER_PROC
    return _UNOWNED_EXTERNAL_PROCELESS


def _shutdown_worker_for_last_owner_locked() -> None:
    wc = get_worker_client()
    has_proc_attr = hasattr(wc, "_proc")
    cur_proc = getattr(wc, "_proc", None)
    if _is_unowned_external_worker_locked(proc=cur_proc, has_proc_attr=has_proc_attr):
        return
    if has_proc_attr:
        if cur_proc is not None:
            _shutdown_worker_if_owned(
                wc,
                expected_proc=cur_proc,
                has_proc_attr=True,
            )
            return
        shutting_down = getattr(wc, "_shutting_down_procs", None)
        if shutting_down:
            shutdown_fn = getattr(wc, "shutdown", None)
            if callable(shutdown_fn):
                shutdown_fn()
        return
    _shutdown_worker_if_owned(
        wc,
        expected_proc=None,
        has_proc_attr=False,
    )


def _shutdown_worker_if_owned(
    wc: Any,
    *,
    expected_proc: Any,
    has_proc_attr: bool,
) -> None:
    shutdown_fn = getattr(wc, "shutdown", None)
    if not callable(shutdown_fn):
        return
    if has_proc_attr:
        if expected_proc is None:
            return
        try:
            params = inspect.signature(shutdown_fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "expected_proc" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        ):
            shutdown_fn(expected_proc=expected_proc)
            return
        if getattr(wc, "_proc", None) is not expected_proc:
            return
    shutdown_fn()


def _format_url_host(host: str) -> str:
    if not host:
        return "0.0.0.0"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def is_loopback_host(host: str) -> bool:
    """Return True iff ``host`` binds strictly to loopback (`127.0.0.1`, `::1`, `localhost`)."""
    cleaned = str(host or "").strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        return False
    if cleaned.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped is not None and mapped.is_loopback)


def resolve_http_auth_token(explicit_token: Optional[str] = None) -> str:
    """Resolve optional HTTP bearer token from explicit parameter or ``MTUI_MCP_AUTH_TOKEN``."""
    if explicit_token is not None:
        return str(explicit_token).strip()
    return os.environ.get("MTUI_MCP_AUTH_TOKEN", "").strip()


def _extract_bearer_token_from_raw_headers(raw_headers: Any) -> Optional[str]:
    for key, val in raw_headers or ():
        try:
            k_str = key.decode("latin-1") if isinstance(key, (bytes, bytearray)) else str(key)
            if k_str.lower() != "authorization":
                continue
            v_str = val.decode("latin-1") if isinstance(val, (bytes, bytearray)) else str(val)
        except Exception:
            continue
        scheme, sep, token = v_str.strip().partition(" ")
        if sep and scheme.lower() == "bearer":
            stripped = token.strip()
            if stripped:
                return stripped
    return None


def has_valid_bearer_token(raw_headers: Any, expected_token: str) -> bool:
    if not expected_token:
        return True
    candidate = _extract_bearer_token_from_raw_headers(raw_headers)
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected_token.encode("utf-8"))


class BearerAuthMiddleware:
    """ASGI middleware enforcing ``Authorization: Bearer <token>`` on MCP HTTP/SSE endpoints."""

    def __init__(self, app: Any, token: str, *, allow_unauthenticated_health: bool = True) -> None:
        self.app = app
        self.token = token
        self.allow_unauthenticated_health = allow_unauthenticated_health

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        authenticated = has_valid_bearer_token(scope.get("headers", ()), self.token)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["mtui_authenticated"] = authenticated

        path = str(scope.get("path") or "")
        if authenticated or (self.allow_unauthenticated_health and scope_type == "http" and path == "/health"):
            await self.app(scope, receive, send)
            return

        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return

        resp = JSONResponse(
            {"error": "Unauthorized: valid Authorization: Bearer <token> header required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await resp(scope, receive, send)


def _configure_mcp_host_and_security(host: str, port: int) -> None:
    settings_obj = getattr(mcp, "settings", None)
    if settings_obj is None:
        return
    if hasattr(settings_obj, "host"):
        settings_obj.host = host
    if hasattr(settings_obj, "port"):
        settings_obj.port = port

    ts = getattr(settings_obj, "transport_security", None)
    if ts is not None and hasattr(ts, "enable_dns_rebinding_protection"):
        if is_loopback_host(host):
            ts.enable_dns_rebinding_protection = True
        else:
            # Non-loopback binds require Bearer token authentication, while LAN/wildcard
            # Host headers (e.g. 192.168.x.x) would otherwise be rejected as non-localhost.
            ts.enable_dns_rebinding_protection = False


def build_mcp_subapps(host: str, port: int) -> tuple[Starlette, Starlette]:
    """Create fresh Streamable HTTP (`/mcp`) and SSE (`/sse` + `/messages/`) Starlette apps."""
    clean_host = (
        host[1:-1].strip()
        if host.startswith("[") and host.endswith("]")
        else host
    )
    _configure_mcp_host_and_security(clean_host, port)

    # In mcp 1.x (FastMCP), `_session_manager` is cached on the instance and
    # `StreamableHTTPSessionManager.run()` can only be entered once per instance.
    # Resetting `_session_manager` before building the app ensures clean restarts.
    if hasattr(mcp, "_session_manager"):
        try:
            setattr(mcp, "_session_manager", None)
        except Exception:
            pass

    stream_kwargs: dict[str, Any] = {}
    if "host" in inspect.signature(mcp.streamable_http_app).parameters:
        stream_kwargs["host"] = clean_host
    stream_app = mcp.streamable_http_app(**stream_kwargs)

    sse_kwargs: dict[str, Any] = {}
    if "host" in inspect.signature(mcp.sse_app).parameters:
        sse_kwargs["host"] = clean_host
    sse_app = mcp.sse_app(**sse_kwargs)

    return stream_app, sse_app


_build_mcp_subapps = build_mcp_subapps


class EmbeddedMcpHttpServer:
    """Non-blocking background `uvicorn.Server` hosting Streamable HTTP, SSE, and `/health`."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        mtui_root: str | Path | None = None,
        mtui_python: str | Path | None = None,
        warm_up: bool = False,
        auth_token: Optional[str] = None,
    ) -> None:
        self._host: str = "127.0.0.1" if host is None else str(host).strip()
        self._port: int = int(port)
        self._mtui_root: Optional[Path] = Path(mtui_root) if mtui_root is not None else None
        self._mtui_python: Optional[Path] = Path(mtui_python) if mtui_python is not None else None
        self._warm_up: bool = bool(warm_up)
        self._auth_token: Optional[str] = auth_token

        self._lock = threading.RLock()
        self._start_generation: int = 0
        self._has_unstopped_start: bool = False
        self._bound_port: Optional[int] = None
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def bound_port(self) -> int | None:
        return self._bound_port

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(
                self._server is not None
                and self._server.started
                and not self._server.should_exit
                and self._thread is not None
                and self._thread.is_alive()
            )

    def _effective_port(self) -> int:
        return self._bound_port if self._bound_port is not None else self._port

    @property
    def streamable_http_url(self) -> str:
        return f"http://{_format_url_host(self._host)}:{self._effective_port()}/mcp"

    @property
    def sse_url(self) -> str:
        return f"http://{_format_url_host(self._host)}:{self._effective_port()}/sse"

    @property
    def health_url(self) -> str:
        return f"http://{_format_url_host(self._host)}:{self._effective_port()}/health"

    def _build_asgi_app(self, bound_port: int, *, required_token: Optional[str] = None) -> Any:
        stream_app, sse_app = build_mcp_subapps(self._host, bound_port)

        async def health_endpoint(request: Request) -> JSONResponse:
            rt = get_runtime_settings()
            authenticated = True
            if required_token:
                authenticated = bool(
                    getattr(request.state, "mtui_authenticated", False)
                    or has_valid_bearer_token(request.scope.get("headers", ()), required_token)
                )

            payload: dict[str, Any] = {
                "status": "ok",
                "server": "mtui-mcp",
                "version": __version__,
                "host": self._host,
                "port": self._bound_port if self._bound_port is not None else bound_port,
                "endpoints": {
                    "streamable_http": "/mcp",
                    "sse": "/sse",
                    "messages": "/messages/",
                    "health": "/health",
                },
                "engine_available": rt.is_engine_available,
                "worker_running": get_worker_client().is_running,
            }
            if required_token:
                payload["auth_required"] = True
            if authenticated:
                payload["mtui_root"] = str(rt.mtui_root) if rt.mtui_root else None
            return JSONResponse(payload)

        starlette_app = Starlette(
            debug=False,
            routes=[
                Route("/health", endpoint=health_endpoint, methods=["GET"]),
                *list(stream_app.routes),
                *list(sse_app.routes),
            ],
            middleware=list(getattr(stream_app, "user_middleware", [])),
            lifespan=stream_app.router.lifespan_context,
        )
        if required_token:
            return BearerAuthMiddleware(starlette_app, required_token, allow_unauthenticated_health=True)
        return starlette_app

    def _bind_listener_socket(self) -> Optional[tuple[socket.socket, int]]:
        clean_host = (
            self._host[1:-1].strip()
            if self._host.startswith("[") and self._host.endswith("]")
            else self._host
        )
        family = socket.AF_INET6 if ":" in clean_host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                excl_opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if excl_opt is not None:
                    sock.setsockopt(socket.SOL_SOCKET, excl_opt, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((clean_host, self._port))
            sock.listen(128)
            sock.setblocking(False)
            sock_addr = sock.getsockname()
            bound_port = int(sock_addr[1])
            return sock, bound_port
        except OSError as exc:
            with contextlib.suppress(OSError):
                sock.close()
            logger.warning(
                "Could not bind embedded MCP HTTP server on %s:%s (%s); skipping HTTP auto-start.",
                self._host,
                self._port,
                exc,
            )
            return None

    def _is_start_generation_active(
        self,
        expected_gen: int,
        expected_server: uvicorn.Server,
        expected_thread: threading.Thread,
    ) -> bool:
        with self._lock:
            return bool(
                self._start_generation == expected_gen
                and self._server is expected_server
                and not expected_server.should_exit
                and self._thread is expected_thread
                and expected_thread.is_alive()
            )

    def _has_other_active_embedded_server(self, current_server: uvicorn.Server) -> bool:
        with self._lock:
            if (
                self._server is not None
                and self._server is not current_server
                and not self._server.should_exit
                and self._thread is not None
                and self._thread.is_alive()
            ):
                return True
        with _EMBEDDED_LIFECYCLE_LOCK:
            _prune_dead_generations_locked()
            for _srv_ref, srv_obj, _thr_obj in _ACTIVE_SERVER_GENERATIONS.values():
                if srv_obj is not current_server:
                    return True
        return False

    @staticmethod
    def _stop_detached_handles(
        server: Optional[uvicorn.Server],
        thread: Optional[threading.Thread],
        sock: Optional[socket.socket],
        *,
        timeout: float,
    ) -> None:
        if server is not None:
            server.should_exit = True

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            join_timeout = max(0.1, float(timeout))
            thread.join(timeout=join_timeout)
            if thread.is_alive() and server is not None:
                server.force_exit = True
                thread.join(timeout=0.5)

        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()

    def start(self, timeout: float = 5.0) -> bool:
        with self._lock:
            if self.is_running:
                return True
            needs_cleanup = (
                self._server is not None
                or self._thread is not None
                or self._socket is not None
            )
        if needs_cleanup:
            self.stop(timeout=min(1.0, max(0.1, timeout)))

        with self._lock:
            if self.is_running:
                return True

            loopback = is_loopback_host(self._host)
            token = resolve_http_auth_token(self._auth_token)
            if not loopback and not token:
                logger.warning(
                    "Refusing to bind embedded MCP HTTP server on non-loopback host %s:%s without MTUI_MCP_AUTH_TOKEN.",
                    self._host,
                    self._port,
                )
                return False

            required_token = token if ((not loopback) or (self._auth_token is not None and bool(token))) else None

            settings = get_runtime_settings()
            if self._mtui_root is not None or self._mtui_python is not None:
                settings.refresh(mtui_root=self._mtui_root, mtui_python=self._mtui_python)
            settings.allow_runtime_python_override = False

            bound = self._bind_listener_socket()
            if bound is None:
                return False
            sock, bound_port = bound

            try:
                asgi_app = self._build_asgi_app(bound_port, required_token=required_token)
                clean_host = (
                    self._host[1:-1].strip()
                    if self._host.startswith("[") and self._host.endswith("]")
                    else self._host
                )
                config = uvicorn.Config(
                    app=asgi_app,
                    host=clean_host,
                    port=bound_port,
                    log_level="warning",
                    access_log=False,
                    lifespan="on",
                )
                server = uvicorn.Server(config)
                server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
                if hasattr(server, "capture_signals"):
                    server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
            except Exception as exc:
                with contextlib.suppress(OSError):
                    sock.close()
                logger.warning("Failed to initialize embedded MCP HTTP server: %s", exc)
                return False

            self._start_generation += 1
            my_gen = self._start_generation
            gen_key = (id(self), my_gen)
            self._socket = sock
            self._bound_port = bound_port
            self._server = server

            def _serve_thread() -> None:
                try:
                    asyncio.run(server.serve(sockets=[sock]))
                except BaseException as exc:  # pragma: no cover - defensive guard for background thread
                    if not isinstance(exc, SystemExit):
                        logger.warning("Embedded MCP HTTP server exited with error: %s", exc)
                finally:
                    with contextlib.suppress(OSError):
                        sock.close()
                    with self._lock:
                        if (
                            self._start_generation == my_gen
                            and self._thread is threading.current_thread()
                        ):
                            self._server = None
                            self._thread = None
                            self._socket = None
                            self._bound_port = None
                        with _EMBEDDED_LIFECYCLE_LOCK:
                            _ACTIVE_SERVER_GENERATIONS.pop(gen_key, None)
                            _prune_dead_generations_locked()

            thread = threading.Thread(
                target=_serve_thread,
                name=f"mtui-mcp-http-{bound_port}",
                daemon=True,
            )
            self._thread = thread
            with _EMBEDDED_LIFECYCLE_LOCK:
                _prune_dead_generations_locked()
                if (
                    not _ACTIVE_SERVER_GENERATIONS
                    and not _IN_PROGRESS_WARMUP_GENERATIONS
                    and not _PENDING_STOP_TOKENS
                ):
                    _refresh_external_worker_snapshot_locked()
                try:
                    thread.start()
                except BaseException:
                    self._server = None
                    self._thread = None
                    self._socket = None
                    self._bound_port = None
                    self._has_unstopped_start = False
                    with contextlib.suppress(OSError):
                        sock.close()
                    raise
                _ACTIVE_SERVER_GENERATIONS[gen_key] = (weakref.ref(self), server, thread)
                self._has_unstopped_start = True

        started_successfully = False
        try:
            deadline = time.monotonic() + max(0.1, float(timeout))
            while time.monotonic() < deadline:
                if not self._is_start_generation_active(my_gen, server, thread):
                    return False
                if server.started and not server.should_exit:
                    if self._warm_up and settings.is_engine_available:
                        with _EMBEDDED_WARMUP_LOCK:
                            with self._lock:
                                if not self._is_start_generation_active(my_gen, server, thread):
                                    return False
                                with _EMBEDDED_LIFECYCLE_LOCK:
                                    _IN_PROGRESS_WARMUP_GENERATIONS.add(gen_key)
                            try:
                                wc = get_worker_client()
                                was_running_before, had_proc_attr, proc_before = (
                                    _is_worker_currently_alive(wc)
                                )
                                warmup_ok = False
                                try:
                                    wc.ensure_started()
                                    warmup_ok = True
                                except Exception as exc:
                                    logger.warning(
                                        "Initial worker warm-up failed during embedded start: %s",
                                        exc,
                                    )

                                wc_after = get_worker_client()
                                has_proc_now = had_proc_attr or hasattr(wc_after, "_proc")
                                proc_after = getattr(wc_after, "_proc", None)
                                if not warmup_ok:
                                    started_by_this_call = False
                                elif has_proc_now:
                                    started_by_this_call = (proc_after is not None) and not (
                                        was_running_before and proc_after is proc_before
                                    )
                                else:
                                    started_by_this_call = not was_running_before
                                if started_by_this_call:
                                    with _EMBEDDED_LIFECYCLE_LOCK:
                                        _mark_worker_owned_locked(
                                            proc=proc_after,
                                            has_proc_attr=has_proc_now,
                                        )
                            finally:
                                with _EMBEDDED_LIFECYCLE_LOCK:
                                    _IN_PROGRESS_WARMUP_GENERATIONS.discard(gen_key)

                            with self._lock:
                                still_active = bool(
                                    self._start_generation == my_gen
                                    and self._server is server
                                    and server.started
                                    and not server.should_exit
                                    and self._thread is thread
                                    and thread.is_alive()
                                )
                                if not still_active and self._start_generation == my_gen:
                                    self._has_unstopped_start = False

                            if not still_active:
                                with _EMBEDDED_LIFECYCLE_LOCK:
                                    _ACTIVE_SERVER_GENERATIONS.pop(gen_key, None)
                                    _prune_dead_generations_locked()
                                    if (
                                        started_by_this_call
                                        and not _ACTIVE_SERVER_GENERATIONS
                                        and not _is_unowned_external_worker_locked(
                                            proc=proc_after,
                                            has_proc_attr=has_proc_now,
                                        )
                                    ):
                                        _shutdown_worker_if_owned(
                                            wc_after,
                                            expected_proc=proc_after,
                                            has_proc_attr=has_proc_now,
                                        )
                                return False
                    else:
                        with self._lock:
                            still_active = bool(
                                self._start_generation == my_gen
                                and self._server is server
                                and server.started
                                and not server.should_exit
                                and self._thread is thread
                                and thread.is_alive()
                            )
                            if not still_active and self._start_generation == my_gen:
                                self._has_unstopped_start = False
                        if not still_active:
                            return False

                    logger.info(
                        "Embedded MCP HTTP server listening on %s (SSE: %s)",
                        self.streamable_http_url,
                        self.sse_url,
                    )
                    started_successfully = True
                    return True
                if not thread.is_alive():
                    break
                time.sleep(0.01)

            detached_server: Optional[uvicorn.Server] = None
            detached_thread: Optional[threading.Thread] = None
            detached_sock: Optional[socket.socket] = None
            with self._lock:
                if self._start_generation == my_gen and self._server is server:
                    self._start_generation += 1
                    self._has_unstopped_start = False
                    detached_server = self._server
                    detached_thread = self._thread
                    detached_sock = self._socket
                    self._server = None
                    self._thread = None
                    self._socket = None
                    self._bound_port = None
                    with _EMBEDDED_LIFECYCLE_LOCK:
                        _ACTIVE_SERVER_GENERATIONS.pop(gen_key, None)
                        _prune_dead_generations_locked()
                    should_stop_this_gen = True
                else:
                    should_stop_this_gen = False
            if should_stop_this_gen:
                logger.warning(
                    "Embedded MCP HTTP server on %s:%s did not become ready within %.1fs.",
                    self._host,
                    bound_port,
                    timeout,
                )
                self._stop_detached_handles(
                    detached_server,
                    detached_thread,
                    detached_sock,
                    timeout=1.0,
                )
            return False
        finally:
            if not started_successfully:
                with self._lock:
                    if self._start_generation == my_gen:
                        self._has_unstopped_start = False
                        if self._server is server:
                            self._server = None
                            self._thread = None
                            self._socket = None
                            self._bound_port = None
                    with _EMBEDDED_LIFECYCLE_LOCK:
                        _ACTIVE_SERVER_GENERATIONS.pop(gen_key, None)
                        _IN_PROGRESS_WARMUP_GENERATIONS.discard(gen_key)
                        _prune_dead_generations_locked()

    def stop(self, timeout: float = 5.0) -> None:
        stop_token = object()
        with self._lock:
            detached_gen = self._start_generation
            self._start_generation += 1
            had_unstopped = self._has_unstopped_start
            self._has_unstopped_start = False
            server = self._server
            thread = self._thread
            sock = self._socket
            self._server = None
            self._thread = None
            self._socket = None
            self._bound_port = None
            with _EMBEDDED_LIFECYCLE_LOCK:
                popped_active = False
                for key in [
                    k
                    for k in _ACTIVE_SERVER_GENERATIONS
                    if k[0] == id(self) and k[1] <= detached_gen
                ]:
                    _ACTIVE_SERVER_GENERATIONS.pop(key, None)
                    popped_active = True
                had_warmup = any(
                    k[0] == id(self) and k[1] <= detached_gen
                    for k in _IN_PROGRESS_WARMUP_GENERATIONS
                )
                will_stop = not (
                    server is None
                    and thread is None
                    and sock is None
                    and not had_unstopped
                    and not popped_active
                    and not had_warmup
                )
                if will_stop:
                    _PENDING_STOP_TOKENS.add(stop_token)

        if not will_stop:
            return

        try:
            self._stop_detached_handles(server, thread, sock, timeout=timeout)
        finally:
            with _EMBEDDED_LIFECYCLE_LOCK:
                _PENDING_STOP_TOKENS.discard(stop_token)
                _prune_dead_generations_locked()
                if not _ACTIVE_SERVER_GENERATIONS:
                    _shutdown_worker_for_last_owner_locked()
