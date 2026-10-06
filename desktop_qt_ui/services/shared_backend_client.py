"""Client for the local translation service shared by Qt and browser clients."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SharedBackendError(RuntimeError):
    """Raised when the local shared translation service cannot complete a task."""


SHARED_BACKEND_PROTOCOL = "manga-translator-ui-shared-v2"


class SharedBackendClient:
    """Start and communicate with one per-user shared backend process."""

    def __init__(
        self,
        root_dir: str | os.PathLike[str],
        *,
        host: str = "127.0.0.1",
        port: int = 5003,
        startup_timeout: float = 90.0,
        request_timeout: float = 1800.0,
        logger=None,
    ):
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.host = host
        self.port = int(port)
        self.startup_timeout = float(startup_timeout)
        self.request_timeout = float(request_timeout)
        self.logger = logger
        self._process = None
        self._log_handle = None
        self._active_response = None
        self._response_lock = threading.Lock()

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _url(self, path: str) -> str:
        return f"{self.endpoint}/{str(path).lstrip('/')}"

    def _log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.info(message)
            except Exception:
                pass

    def _request(self, method: str, path: str, payload=None, *, timeout=None):
        data = None
        headers = {"User-Agent": "MangaTranslatorQt/SharedBackend"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self._url(path), data=data, headers=headers, method=method)
        return urlopen(request, timeout=timeout or self.request_timeout)

    def health(self) -> dict:
        try:
            with self._request("GET", "/backend_info", timeout=3.0) as response:
                info_body = response.read()
            info = json.loads(info_body.decode("utf-8"))
            if (
                not isinstance(info, dict)
                or info.get("protocol") != SHARED_BACKEND_PROTOCOL
                or info.get("projectRoot") != str(self.root_dir)
            ):
                raise SharedBackendError("5003 不是 manga-translator-ui 的统一后端")
            with self._request("GET", "/is_locked", timeout=3.0) as response:
                state_body = response.read()
            state = json.loads(state_body.decode("utf-8"))
            if not isinstance(state, dict):
                raise SharedBackendError("统一后端返回了无效状态")
            state["backendInfo"] = info
            return state
        except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
            raise SharedBackendError(f"本机翻译后端不可用：{exc}") from exc
        except SharedBackendError:
            raise

    def ensure_running(self) -> dict:
        """Reuse an existing service, or start exactly one local process."""
        try:
            return self.health()
        except SharedBackendError:
            pass

        if not self.root_dir.is_dir():
            raise SharedBackendError(f"项目目录不存在：{self.root_dir}")

        python_bin = os.environ.get("MANGA_TRANSLATOR_PYTHON") or sys.executable
        log_path = Path.home() / "Library" / "Logs" / "MangaTranslatorUI" / "shared-backend.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        root = str(self.root_dir)
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.setdefault("MANGA_TRANSLATOR_ENV_PATH", str(self.root_dir / ".env"))
        command = [
            python_bin,
            "-m",
            "manga_translator",
            "shared",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        try:
            self._log_handle = log_path.open("a", encoding="utf-8")
            self._process = subprocess.Popen(
                command,
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=(os.name != "nt"),
            )
        except (OSError, ValueError) as exc:
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None
            raise SharedBackendError(f"启动本机翻译后端失败：{exc}") from exc

        deadline = time.monotonic() + self.startup_timeout
        last_error = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                break
            try:
                return self.health()
            except SharedBackendError as exc:
                last_error = exc
                time.sleep(0.5)

        detail = str(last_error) if last_error else "进程提前退出"
        raise SharedBackendError(f"本机翻译后端未在 {self.startup_timeout:g} 秒内就绪：{detail}")

    def reload_config(self) -> None:
        """Ask the daemon to reload the config flushed by the Qt app."""
        try:
            with self._request("POST", "/reload_config", {}, timeout=30.0) as response:
                body = response.read()
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict) or not value.get("success", False):
                raise SharedBackendError("本机翻译后端拒绝重新加载配置")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise SharedBackendError(
                f"本机翻译后端不支持配置重载（HTTP {exc.code}）：{detail}"
            ) from exc
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            raise SharedBackendError(f"重新加载本机翻译配置失败：{exc}") from exc

    @staticmethod
    def _decode_json(payload: bytes) -> dict:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise SharedBackendError("本机翻译后端返回了无效 JSON") from exc
        if not isinstance(value, dict):
            raise SharedBackendError("本机翻译后端返回了无效任务事件")
        return value

    @staticmethod
    def _progress_values(text: str, fallback_current: int, total: int) -> tuple[int, int]:
        parts = str(text or "").split(":")
        if len(parts) >= 4 and parts[0] == "batch":
            try:
                return max(0, int(parts[2])), max(0, int(parts[3]))
            except (TypeError, ValueError):
                pass
        return fallback_current, total

    def translate_files(self, payload: dict, progress_callback=None) -> list[dict]:
        """Submit a local-file batch and consume the shared binary event stream."""
        total = len(payload.get("files") or [])
        if progress_callback is not None:
            progress_callback(0, total, "已提交到本机翻译队列")

        results = []
        received_done = False
        buffer = bytearray()
        try:
            response = self._request(
                "POST",
                "/execute_files/batch_translate",
                payload,
            )
            with self._response_lock:
                self._active_response = response
            try:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while len(buffer) >= 5:
                        status = buffer[0]
                        length = int.from_bytes(buffer[1:5], "big")
                        if length > 16 * 1024 * 1024:
                            raise SharedBackendError("本机翻译后端返回了过大的任务帧")
                        if len(buffer) < 5 + length:
                            break
                        body = bytes(buffer[5:5 + length])
                        del buffer[:5 + length]

                        if status == 1:
                            text = body.decode("utf-8", errors="replace")
                            current, reported_total = self._progress_values(
                                text, len(results), total
                            )
                            if progress_callback is not None:
                                progress_callback(current, reported_total, text)
                        elif status in (0, 2, 3):
                            event = self._decode_json(body)
                            if status == 2:
                                raise SharedBackendError(
                                    str(event.get("error") or "本机翻译后端处理失败")
                                )
                            if event.get("type") == "file_result":
                                results.append(event)
                                if progress_callback is not None:
                                    progress_callback(len(results), total, "正在保存翻译结果")
                            if status == 3 or event.get("type") == "done":
                                received_done = True
                                break
                        else:
                            raise SharedBackendError(f"本机翻译后端返回了未知状态：{status}")
                    if received_done:
                        break
                if buffer:
                    raise SharedBackendError("本机翻译后端返回了不完整的数据帧")
                if not received_done:
                    raise SharedBackendError("本机翻译后端未发送任务完成事件")
            finally:
                response.close()
                with self._response_lock:
                    self._active_response = None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise SharedBackendError(
                f"本机翻译任务提交失败（HTTP {exc.code}）：{detail}"
            ) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise SharedBackendError(f"本机翻译任务连接失败：{exc}") from exc
        return results

    def cancel(self) -> None:
        """Close the active stream so the daemon can cancel the worker task."""
        with self._response_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
