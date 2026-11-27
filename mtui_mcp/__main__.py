"""CLI entry point for MTUI-MCP server (`stdio`, `streamable-http`, `sse`)."""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import sys
from collections.abc import Sequence
from typing import Optional

from .config import get_runtime_settings
from .server import mcp
from .worker.client import get_worker_client

_VALID_TRANSPORTS = ("stdio", "streamable-http", "sse")


def _default_transport() -> str:
    env_val = os.environ.get("MTUI_MCP_TRANSPORT", "").strip().lower()
    return env_val if env_val in _VALID_TRANSPORTS else "stdio"


def _default_host() -> str:
    env_val = os.environ.get("MTUI_MCP_HOST", "").strip()
    return env_val or "127.0.0.1"


def _default_port() -> int:
    env_val = os.environ.get("MTUI_MCP_PORT", "").strip()
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return 8765


def _run_mcp_transport(transport: str, host: str, port: int) -> None:
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    settings_obj = getattr(mcp, "settings", None)
    if settings_obj is not None:
        if hasattr(settings_obj, "host"):
            settings_obj.host = host
        if hasattr(settings_obj, "port"):
            settings_obj.port = port

    run_params = inspect.signature(mcp.run).parameters
    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in run_params.values()
    )
    if accepts_kwargs or ("host" in run_params and "port" in run_params):
        mcp.run(transport=transport, host=host, port=port)  # type: ignore[arg-type]
    else:
        mcp.run(transport=transport)  # type: ignore[arg-type]


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mtui-mcp",
        description="MCP Server for manga-translator-ui (Antigravity, Codex, Claude Code, Cursor)",
    )
    parser.add_argument(
        "--mtui-root",
        default=None,
        help="Path to manga-translator-ui root directory (overrides MTUI_ROOT env var).",
    )
    parser.add_argument(
        "--mtui-python",
        default=None,
        help="Path to manga-translator-ui Python executable (overrides MTUI_PYTHON env var).",
    )
    parser.add_argument(
        "--transport",
        choices=_VALID_TRANSPORTS,
        default=_default_transport(),
        help="MCP transport protocol (default: MTUI_MCP_TRANSPORT or stdio).",
    )
    parser.add_argument(
        "--host",
        default=_default_host(),
        help="Bind host for streamable-http / sse transports (default: MTUI_MCP_HOST or 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_default_port(),
        help="Bind port for streamable-http / sse transports (default: MTUI_MCP_PORT or 8765).",
    )
    parser.add_argument(
        "--warm-up",
        action="store_true",
        help="Warm up the manga-translator-ui Qt6/PyTorch worker immediately at startup.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Stderr logging level (never writes to stdout).",
    )
    args = parser.parse_args(argv)

    # Strictly log to stderr so MCP stdio stdout stream remains 100% pure JSON-RPC
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )

    settings = get_runtime_settings()
    if args.mtui_root or args.mtui_python:
        settings.refresh(mtui_root=args.mtui_root, mtui_python=args.mtui_python)

    if args.warm_up and settings.is_engine_available:
        try:
            get_worker_client().ensure_started()
        except Exception as exc:
            logging.getLogger("mtui_mcp").warning("Initial worker warm-up failed: %s", exc)

    try:
        _run_mcp_transport(args.transport, args.host, args.port)
    finally:
        get_worker_client().shutdown()


if __name__ == "__main__":
    main()
