"""Embedded background HTTP/SSE MCP server for manga-translator-ui auto-start."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
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
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _build_mcp_subapps(host: str, port: int) -> tuple[Starlette, Starlette]:
    """Create fresh Streamable HTTP (`/mcp`) and SSE (`/sse` + `/messages/`) Starlette apps."""
    settings_obj = getattr(mcp, "settings", None)
    if settings_obj is not None:
        if hasattr(settings_obj, "host"):
            settings_obj.host = host
        if hasattr(settings_obj, "port"):
            settings_obj.port = port

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
        stream_kwargs["host"] = host
    stream_app = mcp.streamable_http_app(**stream_kwargs)

    sse_kwargs: dict[str, Any] = {}
    if "host" in inspect.signature(mcp.sse_app).parameters:
        sse_kwargs["host"] = host
    sse_app = mcp.sse_app(**sse_kwargs)

    return stream_app, sse_app


class EmbeddedMcpHttpServer:
    """Non-blocking background `uvicorn.Server` hosting Streamable HTTP, SSE, and `/health`."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        mtui_root: str | Path | None = None,
        mtui_python: str | Path | None = None,
        warm_up: bool = False,
    ) -> None:
        self._host: str = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self._port: int = int(port)
        self._mtui_root: Optional[Path] = Path(mtui_root) if mtui_root is not None else None
        self._mtui_python: Optional[Path] = Path(mtui_python) if mtui_python is not None else None
        self._warm_up: bool = bool(warm_up)

        self._lock = threading.RLock()
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

    def _build_asgi_app(self, bound_port: int) -> Starlette:
        stream_app, sse_app = _build_mcp_subapps(self._host, bound_port)

        async def health_endpoint(_request: Request) -> JSONResponse:
            rt = get_runtime_settings()
            return JSONResponse(
                {
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
                    "mtui_root": str(rt.mtui_root) if rt.mtui_root else None,
                    "engine_available": rt.is_engine_available,
                    "worker_running": get_worker_client().is_running,
                }
            )

        return Starlette(
            debug=False,
            routes=[
                Route("/health", endpoint=health_endpoint, methods=["GET"]),
                *list(stream_app.routes),
                *list(sse_app.routes),
            ],
            middleware=list(getattr(stream_app, "user_middleware", [])),
            lifespan=stream_app.router.lifespan_context,
        )

    def _bind_listener_socket(self) -> Optional[tuple[socket.socket, int]]:
        family = socket.AF_INET6 if ":" in self._host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                excl_opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if excl_opt is not None:
                    sock.setsockopt(socket.SOL_SOCKET, excl_opt, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._host, self._port))
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

    def start(self, timeout: float = 5.0) -> bool:
        with self._lock:
            if self.is_running:
                return True

            # Ensure any leftover stopped state is cleared before re-binding
            if self._server is not None or self._thread is not None or self._socket is not None:
                self.stop(timeout=min(1.0, max(0.1, timeout)))

            settings = get_runtime_settings()
            if self._mtui_root is not None or self._mtui_python is not None:
                settings.refresh(mtui_root=self._mtui_root, mtui_python=self._mtui_python)

            if self._warm_up and settings.is_engine_available:
                try:
                    get_worker_client().ensure_started()
                except Exception as exc:
                    logger.warning("Initial worker warm-up failed during embedded start: %s", exc)

            bound = self._bind_listener_socket()
            if bound is None:
                return False
            sock, bound_port = bound

            try:
                asgi_app = self._build_asgi_app(bound_port)
                config = uvicorn.Config(
                    app=asgi_app,
                    host=self._host,
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
            if server.started and not server.should_exit:
                logger.info(
                    "Embedded MCP HTTP server listening on %s (SSE: %s)",
                    self.streamable_http_url,
                    self.sse_url,
                )
                return True
            if not thread.is_alive():
                break
            time.sleep(0.01)

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
