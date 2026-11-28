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

    def start(self, timeout: float = 5.0) -> bool:
        with self._lock:
            if self.is_running:
                return True

            # Ensure any leftover stopped state is cleared before re-binding
            if self._server is not None or self._thread is not None or self._socket is not None:
                self.stop(timeout=min(1.0, max(0.1, timeout)))

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

            thread = threading.Thread(
                target=_serve_thread,
                name=f"mtui-mcp-http-{bound_port}",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        deadline = time.monotonic() + max(0.1, float(timeout))
        while time.monotonic() < deadline:
            if not self._is_start_generation_active(my_gen, server, thread):
                return False
            if server.started and not server.should_exit:
                if self._warm_up and settings.is_engine_available:
                    if not self._is_start_generation_active(my_gen, server, thread):
                        return False
                    try:
                        get_worker_client().ensure_started()
                    except Exception as exc:
                        logger.warning("Initial worker warm-up failed during embedded start: %s", exc)
                with self._lock:
                    still_active = bool(
                        self._start_generation == my_gen
                        and self._server is server
                        and server.started
                        and not server.should_exit
                        and self._thread is thread
                        and thread.is_alive()
                    )
                    has_newer_active_server = bool(
                        self._server is not None
                        and self._server is not server
                        and not self._server.should_exit
                    )
                if not still_active:
                    if not has_newer_active_server:
                        get_worker_client().shutdown()
                    return False
                logger.info(
                    "Embedded MCP HTTP server listening on %s (SSE: %s)",
                    self.streamable_http_url,
                    self.sse_url,
                )
                return True
            if not thread.is_alive():
                break
            time.sleep(0.01)

        with self._lock:
            should_stop_this_gen = (
                self._start_generation == my_gen and self._server is server
            )
        if should_stop_this_gen:
            logger.warning(
                "Embedded MCP HTTP server on %s:%s did not become ready within %.1fs.",
                self._host,
                bound_port,
                timeout,
            )
            self.stop(timeout=1.0)
        return False

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._start_generation += 1
            server = self._server
            thread = self._thread
            sock = self._socket
            self._server = None
            self._thread = None
            self._socket = None
            self._bound_port = None

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

        get_worker_client().shutdown()
