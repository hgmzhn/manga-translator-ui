"""Client manager for the warm manga-translator-ui engine worker process."""

from __future__ import annotations

import atexit
import collections
import http.client
import json
import logging
import os
import queue
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

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SHUTDOWN_DRAIN_WAIT_SEC = 14.0


class MtuiWorkerClient:
    """Manages lifecycle, health checks, and RPC calls to `engine_worker.py`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state_cond = threading.Condition(self._lock)
        self._proc: Optional[subprocess.Popen[str]] = None
        self._shutting_down_procs: set[subprocess.Popen[str]] = set()
        self._port: Optional[int] = None
        self._token: str = ""
        self._bound_root: Optional[Path] = None
        self._bound_python: Optional[Path] = None
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

    def _reap_shutting_down_procs_locked(self) -> bool:
        """Remove processes in `_shutting_down_procs` ONLY after `poll()` confirms OS exit (R3-F11)."""
        reaped_any = False
        for p in list(self._shutting_down_procs):
            if p.poll() is not None:
                self._shutting_down_procs.discard(p)
                reaped_any = True
        if reaped_any:
            self._state_cond.notify_all()
        return reaped_any

    def _wait_for_shutting_down_procs_locked(self, deadline: float) -> None:
        self._reap_shutting_down_procs_locked()
        while self._shutting_down_procs:
            remaining_drain = deadline - time.monotonic()
            if remaining_drain <= 0:
                self._reap_shutting_down_procs_locked()
                if not self._shutting_down_procs:
                    break
                lingering_pids = [
                    pid
                    for p in self._shutting_down_procs
                    if p.poll() is None and (pid := getattr(p, "pid", None)) is not None
                ]
                pid_suffix = f" (lingering worker pids: {lingering_pids})" if lingering_pids else ""
                raise TimeoutError(
                    f"Timed out waiting for previous MTUI Engine Worker to finish shutting down{pid_suffix}"
                )
            self._state_cond.wait(timeout=min(0.25, remaining_drain))
            self._reap_shutting_down_procs_locked()

    def _ensure_started_instance(
        self,
        startup_timeout_sec: float = 45.0,
    ) -> tuple[subprocess.Popen[str], int, str]:
        deadline = time.monotonic() + max(0.1, float(startup_timeout_sec))

        with self._lock:
            # R2-F27 & R3-F11: Never spawn a replacement worker while a previous worker process is still alive/draining
            self._wait_for_shutting_down_procs_locked(deadline)

            settings = get_runtime_settings()
            if not settings.is_engine_available or settings.mtui_root is None or settings.mtui_python is None:
                raise RuntimeError(
                    "manga-translator-ui runtime is not configured or its Python environment was not found. "
                    "Set MTUI_ROOT environment variable or call mtui_status(mtui_root=...)."
                )

            if (
                self.is_running
                and self._proc is not None
                and self._port is not None
                and self._bound_root == settings.mtui_root
                and self._bound_python == settings.mtui_python
            ):
                return self._proc, int(self._port), self._token

            self.shutdown()
            self._wait_for_shutting_down_procs_locked(deadline)

            # R3-F38: Re-check startup budget after draining old workers before spawning a new process
            if deadline - time.monotonic() <= 0:
                raise TimeoutError("Timed out starting MTUI Engine Worker before process spawn")

            worker_script = Path(__file__).resolve().parent / "engine_worker.py"
            token = secrets.token_hex(32)
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

            is_frozen_self = bool(
                getattr(sys, "frozen", False)
                and sys.executable
                and os.path.normcase(os.path.abspath(str(settings.mtui_python)))
                == os.path.normcase(os.path.abspath(sys.executable))
            )
            if is_frozen_self:
                cmd = [
                    str(settings.mtui_python),
                    "--mtui-engine-worker",
                    "--mtui-root",
                    str(settings.mtui_root),
                    "--port",
                    "0",
                    "--token",
                    token,
                ]
            else:
                cmd = [
                    str(settings.mtui_python),
                    "-u",
                    str(worker_script),
                    "--mtui-root",
                    str(settings.mtui_root),
                    "--port",
                    "0",
                    "--token",
                    token,
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
            self._token = token
            self._bound_root = settings.mtui_root
            self._bound_python = settings.mtui_python

            if proc.stderr is not None:
                threading.Thread(target=self._drain_stderr, args=(proc.stderr,), daemon=True).start()

            # R2-F57: Keep `handshake_q` strictly bounded to READY or EOF; stream pre-READY logs into `_stderr_ring`
            handshake_q: queue.Queue[Optional[str]] = queue.Queue(maxsize=2)

            def _read_stdout(pipe: Any) -> None:
                handshake_done = False
                try:
                    for raw_line in iter(pipe.readline, ""):
                        if not raw_line:
                            break
                        stripped = raw_line.rstrip("\r\n")
                        if not handshake_done and "MTUI_WORKER_READY" in stripped:
                            handshake_done = True
                            handshake_q.put(stripped)
                        else:
                            self._stderr_ring.append(stripped)
                except Exception:
                    pass
                finally:
                    if not handshake_done:
                        try:
                            handshake_q.put_nowait(None)
                        except queue.Full:
                            pass

            if proc.stdout is not None:
                threading.Thread(target=_read_stdout, args=(proc.stdout,), daemon=True).start()
            else:
                handshake_q.put_nowait(None)

            ready_port: Optional[int] = None
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        err_tail = "\n".join(self._stderr_ring)
                        raise TimeoutError(f"Timed out starting MTUI Engine Worker:\n{err_tail}")

                    try:
                        line = handshake_q.get(timeout=min(0.25, remaining))
                    except queue.Empty:
                        if proc.poll() is not None:
                            err_tail = "\n".join(self._stderr_ring)
                            raise RuntimeError(
                                f"MTUI Engine Worker exited prematurely (code {proc.returncode}):\n{err_tail}"
                            )
                        continue

                    if line is None:
                        try:
                            proc.wait(timeout=1.0)
                        except Exception:
                            pass
                        err_tail = "\n".join(self._stderr_ring)
                        raise RuntimeError(
                            f"MTUI Engine Worker exited prematurely (code {proc.returncode}):\n{err_tail}"
                        )

                    if "MTUI_WORKER_READY" in line:
                        json_part = line.split("MTUI_WORKER_READY", 1)[1].strip()
                        try:
                            info = json.loads(json_part)
                            if not isinstance(info, dict):
                                raise ValueError("READY payload must be a JSON object")
                            parsed_port = int(info["port"])
                            if not (1 <= parsed_port <= 65535):
                                raise ValueError(f"Port out of valid range 1..65535: {parsed_port}")
                        except Exception as parse_exc:
                            err_tail = "\n".join(self._stderr_ring)
                            raise RuntimeError(
                                f"Invalid MTUI Engine Worker READY handshake ({line!r}): {parse_exc}\n{err_tail}"
                            ) from parse_exc
                        ready_port = parsed_port
                        break
                    else:
                        self._stderr_ring.append(line)
            except BaseException:
                self.shutdown(expected_proc=proc)
                raise

            self._port = ready_port
            return proc, ready_port, token

    def ensure_started(self, startup_timeout_sec: float = 45.0) -> int:
        _, port, _ = self._ensure_started_instance(startup_timeout_sec=startup_timeout_sec)
        return port

    def _send_rpc(
        self,
        *,
        proc: subprocess.Popen[str],
        port: int,
        token: str,
        action: str,
        params: Optional[dict[str, Any]],
        timeout_sec: float,
    ) -> dict[str, Any]:
        url = f"http://127.0.0.1:{port}/rpc"
        payload = json.dumps({"action": action, "params": params or {}}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-MTUI-Worker-Token": token,
            },
            method="POST",
        )
        try:
            with _LOOPBACK_OPENER.open(req, timeout=timeout_sec) as resp:
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
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as conn_err:
            err_tail = "\n".join(self._stderr_ring)
            self.shutdown(expected_proc=proc)
            raise RuntimeError(f"MTUI Worker connection lost during {action}: {conn_err}\n{err_tail}") from conn_err

        if not parsed.get("ok"):
            raise RuntimeError(parsed.get("error") or f"Worker action {action} failed")
        return parsed.get("data") or {}

    def call(self, action: str, params: Optional[dict[str, Any]] = None, timeout_sec: float = 300.0) -> dict[str, Any]:
        proc, port, token = self._ensure_started_instance()
        return self._send_rpc(
            proc=proc,
            port=port,
            token=token,
            action=action,
            params=params,
            timeout_sec=timeout_sec,
        )

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
        with self._lock:
            if not self.is_running or self._proc is None or self._port is None or not self._token:
                return {"status": "idle", "worker_running": False, "unloaded": []}
            proc = self._proc
            port = int(self._port)
            token = self._token

        url = f"http://127.0.0.1:{port}/rpc"
        payload = b'{"action":"release_vram","params":{}}'
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-MTUI-Worker-Token": token,
            },
            method="POST",
        )
        try:
            with _LOOPBACK_OPENER.open(req, timeout=30.0) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
        except urllib.error.HTTPError as http_err:
            if http_err.code == 503:
                return {"status": "idle", "worker_running": False, "unloaded": []}
            err_body = http_err.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error") or err_body
            except Exception:
                msg = err_body
            raise RuntimeError(f"MTUI Worker error (release_vram): {msg}") from http_err
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError):
            if proc.poll() is not None:
                self.shutdown(expected_proc=proc)
                return {"status": "idle", "worker_running": False, "unloaded": []}
            self.shutdown(expected_proc=proc)
            return {"status": "idle", "worker_running": False, "unloaded": []}

        if not parsed.get("ok"):
            raise RuntimeError(parsed.get("error") or "Worker action release_vram failed")
        return parsed.get("data") or {}

    def shutdown(self, expected_proc: Optional[subprocess.Popen[str]] = None) -> None:
        wait_deadline = time.monotonic() + _SHUTDOWN_DRAIN_WAIT_SEC + 4.0
        with self._lock:
            if expected_proc is not None and self._proc is not expected_proc:
                self._reap_shutting_down_procs_locked()
                while expected_proc in self._shutting_down_procs and time.monotonic() < wait_deadline:
                    self._state_cond.wait(timeout=0.25)
                    self._reap_shutting_down_procs_locked()
                return
            proc = self._proc
            port = self._port
            token = self._token
            self._proc = None
            self._port = None
            self._token = ""
            self._bound_root = None
            self._bound_python = None
            if proc is None:
                self._reap_shutting_down_procs_locked()
                while self._shutting_down_procs and time.monotonic() < wait_deadline:
                    self._state_cond.wait(timeout=0.25)
                    self._reap_shutting_down_procs_locked()
                return
            self._shutting_down_procs.add(proc)

        try:
            sent_shutdown = False
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
                    with _LOOPBACK_OPENER.open(req, timeout=3.0) as resp:
                        resp.read()
                    sent_shutdown = True
                except Exception:
                    pass

            if sent_shutdown:
                try:
                    proc.wait(timeout=_SHUTDOWN_DRAIN_WAIT_SEC)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
            else:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
        finally:
            for pipe in (proc.stdout, proc.stderr, proc.stdin):
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            with self._lock:
                # R3-F11: Only unregister `proc` once OS exit is confirmed (`proc.poll() is not None`)
                if proc.poll() is not None:
                    self._shutting_down_procs.discard(proc)
                self._reap_shutting_down_procs_locked()
                self._state_cond.notify_all()


_WORKER_CLIENT: Optional[MtuiWorkerClient] = None
_CLIENT_LOCK = threading.Lock()


def get_worker_client() -> MtuiWorkerClient:
    global _WORKER_CLIENT
    if _WORKER_CLIENT is None:
        with _CLIENT_LOCK:
            if _WORKER_CLIENT is None:
                _WORKER_CLIENT = MtuiWorkerClient()
    return _WORKER_CLIENT
