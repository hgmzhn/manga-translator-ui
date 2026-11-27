"""Client manager for the warm manga-translator-ui engine worker process."""

from __future__ import annotations

import atexit
import collections
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from ..config import get_runtime_settings

logger = logging.getLogger("mtui_mcp.worker_client")


class MtuiWorkerClient:
    """Manages lifecycle, health checks, and RPC calls to `engine_worker.py`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen[str]] = None
        self._port: Optional[int] = None
        self._token: str = ""
        self._bound_root: Optional[Path] = None
        self._stderr_ring: collections.deque[str] = collections.deque(maxlen=120)
        atexit.register(self.shutdown)

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._port is not None

    def _drain_stderr(self, pipe: Any) -> None:
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                self._stderr_ring.append(line.rstrip("\r\n"))
        except Exception:
            pass

    def ensure_started(self, startup_timeout_sec: float = 45.0) -> int:
        settings = get_runtime_settings()
        if not settings.is_engine_available or settings.mtui_root is None or settings.mtui_python is None:
            raise RuntimeError(
                "manga-translator-ui runtime is not configured or its Python environment was not found. "
                "Set MTUI_ROOT environment variable or call mtui_status(mtui_root=...)."
            )

        with self._lock:
            if self.is_running and self._bound_root == settings.mtui_root:
                return int(self._port)  # type: ignore[arg-type]

            self.shutdown()

            worker_script = Path(__file__).resolve().parent / "engine_worker.py"
            self._token = secrets.token_hex(32)
            self._stderr_ring.clear()

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["TQDM_DISABLE"] = "1"
            env["QT_QPA_PLATFORM"] = "offscreen"
            env.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
            env.setdefault("QT_IMAGEIO_MAXALLOC", "1024")

            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            cmd = [
                str(settings.mtui_python),
                "-u",
                str(worker_script),
                "--mtui-root",
                str(settings.mtui_root),
                "--port",
                "0",
                "--token",
                self._token,
            ]

            proc = subprocess.Popen(
                cmd,
                cwd=str(settings.mtui_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            self._proc = proc
            self._bound_root = settings.mtui_root

            if proc.stderr is not None:
                threading.Thread(target=self._drain_stderr, args=(proc.stderr,), daemon=True).start()

            deadline = time.monotonic() + startup_timeout_sec
            ready_port: Optional[int] = None
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    err_tail = "\n".join(self._stderr_ring)
                    raise RuntimeError(
                        f"MTUI Engine Worker exited prematurely (code {proc.returncode}):\n{err_tail}"
                    )
                line = proc.stdout.readline() if proc.stdout is not None else ""
                if line and "MTUI_WORKER_READY" in line:
                    json_part = line.split("MTUI_WORKER_READY", 1)[1].strip()
                    info = json.loads(json_part)
                    ready_port = int(info["port"])
                    break

            if ready_port is None:
                self.shutdown()
                err_tail = "\n".join(self._stderr_ring)
                raise TimeoutError(f"Timed out starting MTUI Engine Worker:\n{err_tail}")

            if proc.stdout is not None:
                threading.Thread(target=self._drain_stderr, args=(proc.stdout,), daemon=True).start()

            self._port = ready_port
            return ready_port

    def call(self, action: str, params: Optional[dict[str, Any]] = None, timeout_sec: float = 300.0) -> dict[str, Any]:
        port = self.ensure_started()
        url = f"http://127.0.0.1:{port}/rpc"
        payload = json.dumps({"action": action, "params": params or {}}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-MTUI-Worker-Token": self._token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"MTUI Worker error ({action}): {msg}") from http_err
        except urllib.error.URLError as url_err:
            err_tail = "\n".join(self._stderr_ring)
            self.shutdown()
            raise RuntimeError(f"MTUI Worker connection lost during {action}: {url_err}\n{err_tail}") from url_err

        if not parsed.get("ok"):
            raise RuntimeError(parsed.get("error") or f"Worker action {action} failed")
        return parsed.get("data") or {}

    def detect_fonts(
        self,
        target_path: str | Path,
        *,
        force_refresh: bool = False,
        timeout_sec: float = 600.0,
    ) -> dict[str, Any]:
        return self.call(
            "detect_fonts",
            {"target_path": str(target_path), "force_refresh": bool(force_refresh)},
            timeout_sec=timeout_sec,
        )

    def release_vram(self) -> dict[str, Any]:
        """Release cached GPU models inside the warm worker if running (no-op if worker is not started)."""
        if not self.is_running:
            return {"status": "idle", "worker_running": False, "unloaded": []}
        return self.call("release_vram", {}, timeout_sec=30.0)

    def shutdown(self) -> None:
        with self._lock:
            proc = self._proc
            port = self._port
            token = self._token
            self._proc = None
            self._port = None
            self._token = ""

        if proc is None:
            return

        if proc.poll() is None and port is not None and token:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/rpc",
                    data=b'{"action":"shutdown"}',
                    headers={
                        "Content-Type": "application/json",
                        "X-MTUI-Worker-Token": token,
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=2.0).read()
            except Exception:
                pass

        try:
            proc.wait(timeout=3.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


_WORKER_CLIENT: Optional[MtuiWorkerClient] = None
_CLIENT_LOCK = threading.Lock()


def get_worker_client() -> MtuiWorkerClient:
    global _WORKER_CLIENT
    if _WORKER_CLIENT is None:
        with _CLIENT_LOCK:
            if _WORKER_CLIENT is None:
                _WORKER_CLIENT = MtuiWorkerClient()
    return _WORKER_CLIENT
