"""
Editor HTTP API Server

Provides a REST API for external tools (e.g. Claude) to read and modify the
editor's region data while the Qt editor is running.

Usage:
    server = EditorApiServer(controller)
    server.start()

Thread model:
    - HTTP server runs in a daemon thread.
    - Read requests snapshot Python data directly (GIL-safe).
    - Write requests emit a pyqtSignal; the slot executes in the Qt main thread
      so that model changes trigger UI signals correctly.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

if TYPE_CHECKING:
    from editor.editor_controller import EditorController

logger = logging.getLogger(__name__)

DEFAULT_PORT = 54321


class EditorApiServer(QObject):
    """Minimal HTTP API server embedded in the editor process."""

    # Emitted from the HTTP thread, handled in the Qt main thread via signal-slot.
    _patch_region_signal = pyqtSignal(int, object)  # region_index, patch_dict
    _replace_regions_signal = pyqtSignal(object)  # full list[dict]

    def __init__(self, controller: EditorController | None = None, port: int = DEFAULT_PORT):
        super().__init__()
        self._controller = controller
        self._port = port
        self._actual_port: int | None = None
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

        # Connect signals to slots that run in the main thread.
        self._patch_region_signal.connect(self._apply_patch)
        self._replace_regions_signal.connect(self._replace_all_regions)

    def set_controller(self, controller: EditorController) -> None:
        """Inject (or swap) the editor controller reference.

        Safe to call after start() — the server supports deferred binding
        because the editor is initialized lazily when the user first enters
        the editor view.
        """
        self._controller = controller

    # ── public API ──────────────────────────────────────────────

    @property
    def port(self) -> int | None:
        return self._actual_port

    def start(self) -> None:
        """Start the HTTP server in a daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("EditorApiServer is already running")
            return

        self._httpd = self._create_server()
        if self._httpd is None:
            return

        self._actual_port = self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="editor-api-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "EditorApiServer started on http://127.0.0.1:%d", self._actual_port
        )

    def stop(self) -> None:
        """Shut down the HTTP server."""
        if self._httpd:
            self._httpd.shutdown()
            self._httpd = None
            logger.info("EditorApiServer stopped")

    # ── server creation ─────────────────────────────────────────

    def _create_server(self) -> HTTPServer | None:
        """Bind to *:port, fall back to a random port on conflict."""
        handler = self._make_handler_factory()
        for attempt_port in (self._port, 0):
            try:
                server = HTTPServer(("127.0.0.1", attempt_port), handler)
                server.timeout = 1.0  # serve_forever wakes every 1s
                return server
            except OSError:
                if attempt_port == 0:
                    logger.exception("Failed to bind any port")
                    return None
                logger.warning("Port %d busy, trying random port", attempt_port)
        return None

    def _make_handler_factory(self):
        """Return a BaseHTTPRequestHandler subclass bound to this server instance."""

        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            # Silence per-request log lines (we log manually).
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                parsed = self.path.split("?")[0].rstrip("/")
                if parsed == "/api/regions":
                    self._send_json(server_ref._regions_snapshot())
                elif parsed.startswith("/api/regions/"):
                    idx = self._parse_index(parsed)
                    if idx is None:
                        self.send_error(400, "Invalid region index")
                        return
                    data = server_ref._region_by_index(idx)
                    if data is None:
                        self.send_error(404, "Region not found")
                        return
                    self._send_json(data)
                elif parsed == "/api/status":
                    self._send_json(server_ref._status())
                else:
                    self.send_error(404, "Not found")

            def do_PATCH(self):
                parsed = self.path.rstrip("/")
                if parsed.startswith("/api/regions/"):
                    idx = self._parse_index(parsed)
                    if idx is None:
                        self.send_error(400, "Invalid region index")
                        return
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        body = self.rfile.read(length) if length else b"{}"
                        patch = json.loads(body)
                    except (ValueError, json.JSONDecodeError) as exc:
                        self.send_error(400, f"Invalid JSON: {exc}")
                        return

                    if not isinstance(patch, dict):
                        self.send_error(400, "Body must be a JSON object")
                        return

                    server_ref._patch_region_signal.emit(idx, patch)
                    self._send_json({"ok": True, "index": idx})
                else:
                    self.send_error(404, "Not found")

            # ── helpers ─────────────────────────────────────

            def _send_json(self, data, status=200):
                payload = json.dumps(data, ensure_ascii=False, default=str).encode(
                    "utf-8"
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(payload)

            @staticmethod
            def _parse_index(path: str) -> int | None:
                try:
                    idx = int(path.split("/")[-1])
                    return idx if idx >= 0 else None
                except (ValueError, IndexError):
                    return None

        return _Handler

    # ── data access (called from HTTP thread) ──────────────────

    def _controller_or_none(self):
        """Return the controller, or None if the editor hasn't been opened yet."""
        return self._controller

    def _regions_snapshot(self) -> list[dict]:
        """Return a deep enough copy of all regions (safe from HTTP thread)."""
        import copy

        ctrl = self._controller_or_none()
        if ctrl is None:
            return []
        regions = ctrl.model.get_regions()
        return copy.deepcopy(regions)

    def _region_by_index(self, index: int) -> dict | None:
        import copy

        ctrl = self._controller_or_none()
        if ctrl is None:
            return None
        regions = ctrl.model.get_regions()
        if 0 <= index < len(regions):
            return copy.deepcopy(regions[index])
        return None

    def _status(self) -> dict:
        ctrl = self._controller_or_none()
        if ctrl is None:
            return {
                "source_image": "",
                "region_count": 0,
                "selected_indices": [],
                "port": self._actual_port,
                "editor_initialized": False,
            }
        model = ctrl.model
        regions = model.get_regions()
        src = model.get_source_image_path()
        return {
            "source_image": src or "",
            "region_count": len(regions),
            "selected_indices": model.get_selection(),
            "port": self._actual_port,
            "editor_initialized": True,
        }

    # ── write handlers (run in Qt main thread via signal) ──────

    @pyqtSlot(int, object)
    def _apply_patch(self, index: int, patch: dict):
        """Merge *patch* into the region at *index* and refresh the UI."""
        ctrl = self._controller
        if ctrl is None:
            logger.warning("ApiServer: cannot patch — editor not initialized")
            return
        regions = list(ctrl.model.get_regions())
        if not (0 <= index < len(regions)):
            logger.warning("ApiServer: patch index %d out of range", index)
            return

        old = regions[index]
        # Reject geometry-key changes (these should only come from the UI).
        _GEOMETRY_KEYS = {"center", "lines", "angle", "white_frame_rect_local",
                          "has_custom_white_frame", "render_box_rect_local"}
        geo_keys = _GEOMETRY_KEYS & patch.keys()
        if geo_keys:
            logger.warning("ApiServer: ignoring geometry keys: %s", geo_keys)
            for k in geo_keys:
                patch.pop(k, None)

        if not patch:
            return

        # 当设置 bg_colors 时同步更新 bg_color（属性面板优先读 bg_color）
        if "bg_colors" in patch:
            patch["bg_color"] = patch["bg_colors"]

        new_region = dict(old)
        new_region.update(patch)
        regions[index] = new_region

        ctrl.model.set_regions_silent(regions)
        ctrl.model.regions_changed.emit(regions)
        logger.info("ApiServer: patched region %d with %s", index, patch)

    @pyqtSlot(object)
    def _replace_all_regions(self, regions: list):
        """Replace all regions (not exposed via HTTP yet, available for extensibility)."""
        ctrl = self._controller
        if ctrl is None:
            return
        ctrl.model.set_regions_silent(regions)
        ctrl.model.regions_changed.emit(regions)
        logger.info("ApiServer: replaced all %d regions", len(regions))
