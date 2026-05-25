"""
Editor HTTP API Server

Provides a REST API for external tools (e.g. Claude) to read and modify the
editor's region data while the Qt editor is running.

Usage:
    server = EditorApiServer(controller, editor_logic)
    server.start()

Thread model:
    - HTTP server runs in a daemon thread.
    - Read requests snapshot Python data directly (GIL-safe).
    - Write requests to the *current page* emit a pyqtSignal so the Qt main
      thread processes them and triggers UI updates.
    - Write requests to *other pages* read/write the _translations.json on
      disk directly (no UI refresh needed — those pages aren't displayed).
    - Export requests run synchronously in the HTTP thread (blocking, may take
      several seconds).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from manga_translator.utils import open_pil_image, save_pil_image

if TYPE_CHECKING:
    from editor.editor_controller import EditorController
    from editor.editor_logic import EditorLogic

logger = logging.getLogger(__name__)

DEFAULT_PORT = 54321

# Fields that must not be overwritten via the API.
_GEOMETRY_KEYS = frozenset({
    "center", "lines", "angle", "white_frame_rect_local",
    "has_custom_white_frame", "render_box_rect_local",
})


class EditorApiServer(QObject):
    """Minimal HTTP API server embedded in the editor process."""

    # Emitted from the HTTP thread, handled in the Qt main thread via signal-slot.
    _patch_region_signal = pyqtSignal(int, object)  # region_index, patch_dict

    def __init__(self, controller: EditorController | None = None,
                 editor_logic: EditorLogic | None = None,
                 port: int = DEFAULT_PORT):
        super().__init__()
        self._controller = controller
        self._editor_logic = editor_logic
        self._port = port
        self._actual_port: int | None = None
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

        self._patch_region_signal.connect(self._apply_patch)

    def set_controller(self, controller: EditorController) -> None:
        self._controller = controller

    def set_editor_logic(self, editor_logic: EditorLogic) -> None:
        self._editor_logic = editor_logic

    # ── public API ──────────────────────────────────────────────

    @property
    def port(self) -> int | None:
        return self._actual_port

    def start(self) -> None:
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
        logger.info("EditorApiServer started on http://127.0.0.1:%d", self._actual_port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd = None
            logger.info("EditorApiServer stopped")

    # ── server creation ─────────────────────────────────────────

    def _create_server(self) -> HTTPServer | None:
        handler = self._make_handler_factory()
        for attempt_port in (self._port, 0):
            try:
                server = HTTPServer(("127.0.0.1", attempt_port), handler)
                server.timeout = 1.0
                return server
            except OSError:
                if attempt_port == 0:
                    logger.exception("Failed to bind any port")
                    return None
                logger.warning("Port %d busy, trying random port", attempt_port)
        return None

    def _make_handler_factory(self):
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _route_get(self, parsed, qs):
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
                elif parsed == "/api/files":
                    self._send_json(server_ref._file_list())
                elif parsed == "/api/search":
                    field = qs.get("field", ["translation"])[0]
                    value = qs.get("value", [""])[0]
                    if not value:
                        self.send_error(400, "Missing 'value' query parameter")
                        return
                    self._send_json(server_ref._search(field, value))
                else:
                    self.send_error(404, "Not found")

            def _route_patch(self, parsed):
                if parsed.startswith("/api/regions/"):
                    idx = self._parse_index(parsed)
                    if idx is None:
                        self.send_error(400, "Invalid region index")
                        return
                    patch = self._read_json_body()
                    if patch is None:
                        return
                    if not isinstance(patch, dict):
                        self.send_error(400, "Body must be a JSON object")
                        return
                    server_ref._patch_region_signal.emit(idx, patch)
                    self._send_json({"ok": True, "index": idx + 1})
                elif parsed.startswith("/api/files/"):
                    result = self._parse_file_region_path(parsed)
                    if result is None:
                        self.send_error(400, "Invalid path. Use /api/files/{fi}/regions/{ri}")
                        return
                    file_idx, region_idx = result
                    patch = self._read_json_body()
                    if patch is None:
                        return
                    if not isinstance(patch, dict):
                        self.send_error(400, "Body must be a JSON object")
                        return
                    ok = server_ref._patch_file_region(file_idx, region_idx, patch)
                    if ok is None:
                        self.send_error(404, "File or region not found")
                        return
                    self._send_json({"ok": True, "file_index": file_idx + 1, "region_index": region_idx + 1})
                else:
                    self.send_error(404, "Not found")

            def _route_post(self, parsed):
                body = self._read_json_body()

                if parsed == "/api/export":
                    # If body has "files" list, batch export; otherwise export current page
                    if body and isinstance(body, dict) and "files" in body:
                        file_indices = body["files"]
                        if not isinstance(file_indices, list) or not file_indices:
                            self.send_error(400, "'files' must be a non-empty list of file indices")
                            return
                        results = server_ref._export_batch(file_indices)
                        self._send_json({"results": results})
                    else:
                        result = server_ref._export_current_page()
                        self._send_json(result)
                elif parsed.startswith("/api/export/"):
                    idx = self._parse_index(parsed)
                    if idx is None:
                        self.send_error(400, "Invalid file index")
                        return
                    result = server_ref._export_file_by_index(idx)
                    if result is None:
                        self.send_error(404, "File not found or has no JSON data")
                        return
                    self._send_json(result)
                else:
                    self.send_error(404, "Not found")

            def do_GET(self):
                parsed_url = urllib.parse.urlparse(self.path)
                parsed = parsed_url.path.rstrip("/") if parsed_url.path else ""
                qs = urllib.parse.parse_qs(parsed_url.query)
                self._route_get(parsed, qs)

            def do_PATCH(self):
                parsed = self.path.rstrip("/")
                self._route_patch(parsed)

            def do_POST(self):
                parsed = self.path.rstrip("/")
                try:
                    self._route_post(parsed)
                except Exception as exc:
                    logger.exception("POST handler error")
                    try:
                        self.send_error(500, f"Internal error: {exc}")
                    except Exception:
                        pass

            # ── response helpers ────────────────────────────

            def _send_json(self, data, status=200):
                payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(payload)

            def _read_json_body(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length) if length else b"{}"
                    return json.loads(body) if body.strip() else {}
                except (ValueError, json.JSONDecodeError) as exc:
                    self.send_error(400, f"Invalid JSON: {exc}")
                    return None

            @staticmethod
            def _parse_index(path: str) -> int | None:
                try:
                    idx = int(path.split("/")[-1])
                    if idx < 1:
                        return None
                    return idx - 1
                except (ValueError, IndexError):
                    return None

            @staticmethod
            def _parse_file_region_path(path: str) -> tuple[int, int] | None:
                parts = path.split("/")
                if len(parts) != 6 or parts[3] == "" or parts[5] == "":
                    return None
                try:
                    fi = int(parts[3])
                    ri = int(parts[5])
                    if fi < 1 or ri < 1:
                        return None
                    return fi - 1, ri - 1
                except ValueError:
                    return None

        return _Handler

    # ── current-page data access ───────────────────────────────

    def _regions_snapshot(self) -> list[dict]:
        ctrl = self._controller
        if ctrl is None:
            return []
        return copy.deepcopy(ctrl.model.get_regions())

    def _region_by_index(self, index: int) -> dict | None:
        ctrl = self._controller
        if ctrl is None:
            return None
        regions = ctrl.model.get_regions()
        if 0 <= index < len(regions):
            return copy.deepcopy(regions[index])
        return None

    def _status(self) -> dict:
        ctrl = self._controller
        if ctrl is None:
            return {
                "source_image": "",
                "region_count": 0,
                "selected_indices": [],
                "port": self._actual_port,
                "editor_initialized": False,
            }
        model = ctrl.model
        return {
            "source_image": model.get_source_image_path() or "",
            "region_count": len(model.get_regions()),
            "selected_indices": [s + 1 for s in model.get_selection()],
            "port": self._actual_port,
            "editor_initialized": True,
        }

    # ── file list ──────────────────────────────────────────────

    def _file_list(self) -> list[dict]:
        logic = self._editor_logic
        if logic is None:
            return []
        result = []
        for i, item in enumerate(logic.file_model.files):
            result.append({
                "index": i + 1,
                "path": item.path,
                "json_path": item.json_path,
                "file_type": item.file_type.value,
            })
        return result

    # ── cross-file search ──────────────────────────────────────

    def _search(self, field: str, value: str) -> list[dict]:
        logic = self._editor_logic
        if logic is None:
            return []

        current_src = ""
        ctrl = self._controller
        if ctrl:
            current_src = ctrl.model.get_source_image_path() or ""

        matches: list[dict] = []
        for i, item in enumerate(logic.file_model.files):
            regions = self._load_regions_from_file(item.path, item.json_path)
            if regions is None:
                continue
            for ri, region in enumerate(regions):
                if str(region.get(field, "")) == value:
                    is_current = os.path.normpath(item.path) == os.path.normpath(current_src)
                    matches.append({
                        "file_index": i + 1,
                        "file_path": item.path,
                        "region_index": ri + 1,
                        "is_current_page": is_current,
                        "region": copy.deepcopy(region),
                    })
        return matches

    # ── cross-file PATCH (disk-based) ──────────────────────────

    def _patch_file_region(self, file_index: int, region_index: int, patch: dict) -> bool | None:
        logic = self._editor_logic
        if logic is None:
            return None
        files = logic.file_model.files
        if not (0 <= file_index < len(files)):
            return None

        item = files[file_index]

        ctrl = self._controller
        if ctrl:
            current_src = ctrl.model.get_source_image_path() or ""
            if os.path.normpath(item.path) == os.path.normpath(current_src):
                self._patch_region_signal.emit(region_index, patch)
                return True

        data, json_path = self._load_full_json(item.path, item.json_path)
        if data is None or json_path is None:
            return None

        image_key = os.path.abspath(item.path)
        image_data = data.get(image_key, data.get(next(iter(data), "")))
        if image_data is None:
            return None

        regions = image_data.get("regions", [])
        if not (0 <= region_index < len(regions)):
            return None

        geo_keys = _GEOMETRY_KEYS & patch.keys()
        for k in geo_keys:
            patch.pop(k, None)
        if not patch:
            return True

        if "bg_colors" in patch:
            patch["bg_color"] = patch["bg_colors"]

        regions[region_index].update(patch)
        image_data["regions"] = regions

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("ApiServer: patched file %d region %d on disk", file_index, region_index)
            return True
        except Exception:
            logger.exception("ApiServer: failed to write %s", json_path)
            return None

    # ── JSON file helpers ──────────────────────────────────────

    @staticmethod
    def _load_full_json(image_path: str, json_path: str | None) -> tuple[dict | None, str | None]:
        if not json_path or not os.path.exists(json_path):
            return None, None
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f), json_path
        except Exception:
            return None, None

    @staticmethod
    def _load_regions_from_file(image_path: str, json_path: str | None) -> list[dict] | None:
        data, _ = EditorApiServer._load_full_json(image_path, json_path)
        if data is None:
            return None
        image_key = os.path.abspath(image_path)
        image_data = data.get(image_key)
        if image_data is None and data:
            image_data = next(iter(data.values()))
        if image_data is None:
            return None
        return image_data.get("regions")

    # ── current-page write handler (Qt main thread) ────────────

    @pyqtSlot(int, object)
    def _apply_patch(self, index: int, patch: dict):
        ctrl = self._controller
        if ctrl is None:
            logger.warning("ApiServer: cannot patch — editor not initialized")
            return
        regions = list(ctrl.model.get_regions())
        if not (0 <= index < len(regions)):
            logger.warning("ApiServer: patch index %d out of range", index)
            return

        old = regions[index]
        geo_keys = _GEOMETRY_KEYS & patch.keys()
        if geo_keys:
            logger.warning("ApiServer: ignoring geometry keys: %s", geo_keys)
            for k in geo_keys:
                patch.pop(k, None)

        if not patch:
            return

        if "bg_colors" in patch:
            patch["bg_color"] = patch["bg_colors"]

        new_region = dict(old)
        new_region.update(patch)
        regions[index] = new_region

        ctrl.model.set_regions_silent(regions)
        ctrl.model.regions_changed.emit(regions)
        logger.info("ApiServer: patched region %d with %s", index, patch)

    # ── export ─────────────────────────────────────────────────

    def _get_config_dict(self) -> dict:
        """Get config as a plain dict from the config service."""
        from services import get_config_service

        cfg = get_config_service().get_config()
        return json.loads(cfg.model_dump_json())

    def _build_out_path(self, source_path: str) -> str:
        """Build output path in the project's out/ directory."""
        base = os.path.splitext(os.path.basename(source_path))[0]
        return os.path.join(self._project_root(), "out", f"{base}.jpeg")

    def _project_root(self) -> str:
        """Return the project root directory (parent of desktop_qt_ui/)."""
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _pil_from_model(self) -> Image.Image | None:
        """Get a PIL copy of the editor's current image (thread-safe)."""
        ctrl = self._controller
        if ctrl is None:
            return None
        img = ctrl.model.get_image()
        if img is None:
            return None
        # Convert whatever the editor stores to PIL.
        arr = np.asarray(img)
        if arr.ndim == 3:
            return Image.fromarray(arr)
        if arr.ndim == 2:
            return Image.fromarray(arr, mode="L")
        return None

    @staticmethod
    def _enhance_regions(regions: list[dict]) -> list[dict]:
        """Enrich regions with defaults and backend params (mirrors editor logic)."""
        from services import get_render_parameter_service

        render_svc = get_render_parameter_service()
        enhanced = []
        for idx, r in enumerate(regions):
            r = copy.deepcopy(r)
            if not r.get("translation"):
                r["translation"] = r.get("text", "")
            if not r.get("font_size"):
                r["font_size"] = 16
            if not r.get("alignment"):
                r["alignment"] = "center"
            if not r.get("direction"):
                r["direction"] = "auto"
            r.update(render_svc.export_parameters_for_backend(idx, r))
            enhanced.append(r)
        return enhanced

    def _run_export(self, image: Image.Image, regions: list[dict],
                    source_path: str, mask: np.ndarray | None = None) -> dict:
        """Run the backend render pipeline and return the result."""
        from services.export_service import ExportService

        config_dict = self._get_config_dict()
        out_path = self._build_out_path(source_path)

        enhanced = self._enhance_regions(regions)

        result = {"success": False, "output_path": out_path, "error": None}

        try:
            export_svc = ExportService()
            def _on_error(msg):
                result["error"] = msg

            export_svc._perform_backend_render_export(
                image=image,
                regions_data=enhanced,
                config=config_dict,
                output_path=out_path,
                mask=mask,
                progress_callback=lambda msg: None,
                success_callback=lambda msg: None,
                error_callback=_on_error,
                source_image_path=source_path,
                save_inpainted_only=False,
                editor_inpainted_image=None,
            )
            file_ok = os.path.exists(out_path)
            result["success"] = file_ok
            result["output_path"] = out_path
            if file_ok and result.get("error"):
                result["error"] = None
        except Exception as e:
            result["error"] = str(e)
            logger.exception("Export failed for %s", source_path)

        return result

    # ── export: current page ───────────────────────────────────

    def _export_current_page(self) -> dict:
        """Export the page currently open in the editor."""
        ctrl = self._controller
        if ctrl is None:
            return {"success": False, "error": "Editor not initialized"}

        source_path = ctrl.model.get_source_image_path()
        if not source_path:
            return {"success": False, "error": "No image loaded"}

        image = self._pil_from_model()
        if image is None:
            return {"success": False, "error": "Failed to get image from editor"}

        regions = ctrl.model.get_regions()
        if not regions:
            return {"success": False, "error": "No regions to export"}

        refined = ctrl.model.get_refined_mask()
        mask = refined if refined is not None else ctrl.model.get_raw_mask()
        mask_np = np.asarray(mask) if mask is not None else None

        return self._run_export(image, copy.deepcopy(regions), source_path, mask_np)

    # ── export: file by index ──────────────────────────────────

    def _export_file_by_index(self, file_index: int) -> dict | None:
        """Export a specific file from the file list by loading from disk."""
        logic = self._editor_logic
        if logic is None:
            return None
        files = logic.file_model.files
        if not (0 <= file_index < len(files)):
            return None

        item = files[file_index]

        # If this is the current page, delegate to in-memory export.
        ctrl = self._controller
        if ctrl:
            current_src = ctrl.model.get_source_image_path() or ""
            if os.path.normpath(item.path) == os.path.normpath(current_src):
                return self._export_current_page()

        if not os.path.exists(item.path):
            return None

        image = open_pil_image(item.path)
        if image is None:
            return {"success": False, "error": f"Cannot open image: {item.path}"}

        regions = self._load_regions_from_file(item.path, item.json_path)
        if regions is None:
            image.close()
            return {"success": False, "error": f"No JSON data for {item.path}"}

        return self._run_export(image, copy.deepcopy(regions), item.path)

    # ── export: batch ──────────────────────────────────────────

    def _export_batch(self, file_indices_1based: list[int]) -> list[dict]:
        """Export multiple files. *file_indices* are 1-based."""
        results = []
        for fi_1 in file_indices_1based:
            fi_0 = fi_1 - 1
            try:
                result = self._export_file_by_index(fi_0)
                results.append({
                    "file_index": fi_1,
                    "success": result.get("success", False) if result else False,
                    "output_path": result.get("output_path", "") if result else "",
                    "error": result.get("error") if result else "File not found",
                })
            except Exception as e:
                results.append({
                    "file_index": fi_1,
                    "success": False,
                    "error": str(e),
                })
        return results
