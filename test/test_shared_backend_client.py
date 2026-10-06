from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desktop_qt_ui.services.shared_backend_client import SharedBackendClient


def _frame(status: int, payload) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return bytes([status]) + len(body).to_bytes(4, "big") + body


class _Handler(BaseHTTPRequestHandler):
    project_root = ""

    def do_GET(self):  # noqa: N802
        if self.path == "/backend_info":
            self._send_json({
                "service": "manga-translator-ui",
                "mode": "shared",
                "protocol": "manga-translator-ui-shared-v2",
                "pid": 123,
                "projectRoot": self.project_root,
            })
            return
        if self.path != "/is_locked":
            self.send_error(404)
            return
        self._send_json({"locked": False, "queued": 0})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/reload_config":
            self._send_json({"success": True})
            return
        if self.path != "/execute_files/batch_translate":
            self.send_error(404)
            return

        files = payload.get("files") or []
        result = {
            "type": "file_result",
            "index": 0,
            "original_path": files[0] if files else "",
            "output_path": "/tmp/output.png",
            "success": True,
            "skipped": False,
        }
        stream = b"".join(
            (
                _frame(1, "batch:0:0:1"),
                _frame(0, result),
                _frame(3, {"type": "done", "processed": 1, "total": 1}),
            )
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(stream)))
        self.end_headers()
        self.wfile.write(stream)

    def _send_json(self, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def test_shared_backend_client_reuses_service_and_reads_event_stream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            _Handler.project_root = str(Path.cwd())
            source = Path(temp_dir) / "page.png"
            source.write_bytes(b"not used by fake server")
            client = SharedBackendClient(
                Path.cwd(),
                port=server.server_port,
                startup_timeout=1,
            )
            assert client.ensure_running()["locked"] is False
            client.reload_config()
            progress = []
            results = client.translate_files(
                {"files": [str(source)], "outputFolder": temp_dir},
                lambda current, total, message: progress.append((current, total, message)),
            )
            assert results[0]["original_path"] == str(source)
            assert any(item[2] == "正在保存翻译结果" for item in progress)
    finally:
        _Handler.project_root = ""
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
