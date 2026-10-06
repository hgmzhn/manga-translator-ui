import asyncio
import base64
from contextlib import suppress
import hashlib
import io
import json
import mimetypes
import os
import pickle
import re
import shutil
import sys
import tempfile
import time
import traceback
from threading import Lock, get_ident
from pathlib import Path as FilePath
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Request, Response
from PIL import Image
from starlette.responses import FileResponse, StreamingResponse

from manga_translator import Config, MangaTranslator
from manga_translator.server.server_utils import transform_to_image


PROJECT_ROOT = FilePath(__file__).resolve().parents[2]
DESKTOP_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"
BACKEND_LOG_PATH = FilePath(
    os.environ.get(
        "IMMERSIVE_TRANSLATE_LOG_PATH",
        str(FilePath.home() / "Library" / "Logs" / "ImmersiveTranslate" / "manga-backend.log"),
    )
).expanduser()
MAX_LOG_READ_BYTES = 128 * 1024
PROCESS_DIR_NAME = ".immersive-translate"
CACHE_WRITE_LOCK = Lock()
SHARED_BACKEND_PROTOCOL = "manga-translator-ui-shared-v2"
BROWSER_MANGA_BATCH_WINDOW_SIZE = 10


def _read_desktop_config() -> dict:
    raw_config = json.loads(DESKTOP_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象")
    return raw_config


def _config_revision(raw_config: dict) -> str:
    encoded = json.dumps(
        raw_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_desktop_config(raw_config: dict | None = None) -> tuple[Config, dict, dict]:
    """Load the same user config used by the desktop application.

    The shared bridge defaults to the desktop config; the extension may apply
    a validated document for a translation session through its protected API.
    """
    raw_config = raw_config if raw_config is not None else _read_desktop_config()
    if not isinstance(raw_config, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象")
    config = Config.model_validate(raw_config)

    # desktop_qt_ui/app_logic.py builds MangaTranslator params by flattening
    # the CLI section over the full config dictionary. Keep that behaviour so
    # model/device/runtime choices match the desktop app.
    translator_params = dict(raw_config.get("cli") or {})
    translator_params.update(raw_config)
    font_family = (raw_config.get("render") or {}).get("font_family")
    if font_family:
        translator_params["font_family"] = font_family

    app_config = raw_config.get("app") or {}
    output_folder = app_config.get("last_output_path") or str(PROJECT_ROOT / "result")
    output_folder = str(FilePath(output_folder).expanduser())
    save_info = {
        "output_folder": output_folder,
        "input_folders": set(),
        "format": (raw_config.get("cli") or {}).get("format"),
        "overwrite": (raw_config.get("cli") or {}).get("overwrite", True),
        "save_to_source_dir": False,
    }
    return config, translator_params, save_info


def _decode_image(value: str) -> Image.Image:
    try:
        raw = base64.b64decode(value, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            return image.copy()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid image data")


def _decode_attributes(method_name: str, payload: dict) -> dict:
    try:
        config = Config.model_validate(payload["config"])
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid translation config")

    if method_name == "translate":
        return {"image": _decode_image(payload.get("image", "")), "config": config}

    try:
        images = [_decode_image(value) for value in payload["images"]]
        batch_size = payload.get("batch_size")
    except (KeyError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid batch request")
    return {
        "images_with_configs": [(image, config) for image in images],
        "batch_size": batch_size,
    }


def _safe_task_id(value: object) -> str:
    task_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", task_id):
        return ""
    return task_id


def _process_root(output_folder: FilePath) -> FilePath:
    return output_folder / PROCESS_DIR_NAME


def _safe_work_name(value: object) -> str:
    """Build one stable, readable directory name from a page URL.

    Chapter numbers are intentionally excluded so all chapters of one work
    share a parent directory, while the host is retained to avoid collisions
    between sites that use the same slug.
    """
    raw = str(value or '').strip()
    if not raw:
        return 'unknown-work'
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or parsed.netloc or '').lower()
        path_parts = [part for part in parsed.path.split('/') if part]
    except ValueError:
        host = ''
        path_parts = []
    work_parts = []
    for part in path_parts:
        if re.fullmatch(r'(?:chapter|ch)[-_]?\d+[a-z0-9-]*', part, re.IGNORECASE):
            break
        work_parts.append(part)
    components = [component for component in [host, *work_parts] if component]
    candidate = '__'.join(components) or raw
    candidate = re.sub(r'[^A-Za-z0-9._-]+', '-', candidate).strip('.-_')
    candidate = re.sub(r'-{2,}', '-', candidate)
    return candidate[:160] or 'unknown-work'


def _task_dir(
    task_id: str,
    output_folder: FilePath,
    source_url: str = '',
    *,
    for_write: bool = True,
) -> FilePath | None:
    safe_id = _safe_task_id(task_id)
    if not safe_id:
        return None
    tasks_root = _process_root(output_folder) / "tasks"
    nested = tasks_root / _safe_work_name(source_url) / safe_id
    if for_write:
        return nested
    legacy = tasks_root / safe_id
    if (nested / 'manifest.json').is_file() or not (legacy / 'manifest.json').is_file():
        return nested
    return legacy


def _task_manifest_file(
    task_id: str,
    output_folder: FilePath,
    source_url: str = '',
    *,
    for_write: bool = False,
) -> FilePath | None:
    task_dir = _task_dir(task_id, output_folder, source_url, for_write=for_write)
    return task_dir / "manifest.json" if task_dir is not None else None


def _manifest_page_path(
    task_id: str,
    page_index: object,
    output_folder: FilePath,
    source_url: str = '',
) -> FilePath | None:
    try:
        index = int(page_index)
    except (TypeError, ValueError):
        return None
    if index < 0 or index > 10000:
        return None
    manifest_file = _task_manifest_file(task_id, output_folder, source_url)
    if manifest_file is None or not manifest_file.is_file():
        return None
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        page = (manifest.get("pages") or {}).get(str(index))
        relative_path = page.get("path") if isinstance(page, dict) else None
        if not relative_path:
            return None
        candidate = (output_folder / str(relative_path)).resolve()
        output_root = output_folder.resolve()
        candidate.relative_to(output_root)
        if candidate.is_file():
            return candidate

        # Manifests written before the work-folder layout may still contain
        # .immersive-translate/tasks/<taskId>/... paths. The task directory
        # itself is now nested under the work key, so resolve that old path
        # against the current task directory as a compatibility fallback.
        old_prefix = f'.immersive-translate/tasks/{_safe_task_id(task_id)}/'
        raw_path = str(relative_path)
        if raw_path.startswith(old_prefix):
            task_dir = _task_dir(task_id, output_folder, source_url, for_write=False)
            if task_dir is not None:
                relocated = (task_dir / raw_path[len(old_prefix):]).resolve()
                relocated.relative_to(task_dir.resolve())
                if relocated.is_file():
                    return relocated
        return candidate
    except (OSError, ValueError, TypeError):
        return None


def _write_task_manifest_result(
    task_id: str,
    page_index: object,
    *,
    output_folder: FilePath,
    result_path: FilePath,
    source_url: str = "",
    image_url: str = "",
    filename: str = "",
) -> None:
    """Record the canonical translated image without creating a second copy."""
    task_dir = _task_dir(task_id, output_folder, source_url, for_write=True)
    if task_dir is None:
        return
    try:
        index = int(page_index)
        output_root = output_folder.resolve()
        resolved_result = result_path.resolve()
        relative_path = resolved_result.relative_to(output_root)
    except (OSError, TypeError, ValueError):
        return
    if not resolved_result.is_file():
        return

    with CACHE_WRITE_LOCK:
        task_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = task_dir / "manifest.json"
        manifest = {
            "taskId": _safe_task_id(task_id),
            "sourceUrl": str(source_url or "")[:4000],
            "updatedAt": int(time.time()),
            "pages": {},
        }
        if manifest_file.is_file():
            try:
                existing = json.loads(manifest_file.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    manifest.update({key: existing[key] for key in ("sourceUrl", "updatedAt") if key in existing})
                    if isinstance(existing.get("pages"), dict):
                        manifest["pages"].update(existing["pages"])
            except (OSError, ValueError, TypeError):
                pass
        manifest["pages"][str(index)] = {
            "index": index,
            "filename": FilePath(str(filename or "page.png")).name,
            "imageUrl": str(image_url or "")[:4000],
            "path": str(relative_path),
            "updatedAt": int(time.time()),
        }
        temp_manifest = manifest_file.with_suffix(".json.tmp")
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_manifest.replace(manifest_file)


class MangaShare:
    def __init__(self, params: dict = None):
        params = params or {}
        self.config_document = _read_desktop_config()
        self.desktop_config, translator_params, self.save_info = _load_desktop_config(self.config_document)
        self.config_revision = _config_revision(self.config_document)
        self.manga = MangaTranslator(translator_params)
        self.host = params.get('host', '127.0.0.1')
        self.port = int(params.get('port', '5003'))
        self.nonce = params.get('nonce', None)
        self.started_at = time.time()

        # each chunk has a structure like this status_code(int/1byte),len(int/4bytes),bytechunk
        # status codes are 0 for result, 1 for progress report, 2 for error
        self.progress_queue = asyncio.Queue()
        # The shared backend owns one mutable MangaTranslator instance.  Keep
        # execution serialized, but let later HTTP requests wait instead of
        # failing immediately with a misleading HTTP 429.  The old
        # non-blocking threading.Lock also had no way to cancel the owner when
        # its streaming client disappeared.
        self.lock = asyncio.Lock()
        self._lock_waiters = 0
        self._active_job = None
        self._active_job_started_at = None

        self._register_progress_hook()

    def _register_progress_hook(self) -> None:
        """Route core progress messages to the currently active HTTP stream."""

        async def hook(state: str, finished: bool):
            state_data = state.encode("utf-8")
            progress_data = b'\x01' + len(state_data).to_bytes(4, 'big') + state_data
            await self.progress_queue.put(progress_data)
            await asyncio.sleep(0)

        self.manga.add_progress_hook(hook)

    def _resolve_output_folder(self, requested: object = "") -> FilePath:
        """Resolve and create the selected plugin output directory."""
        value = str(requested or self.save_info.get("output_folder") or "").strip()
        output_folder = FilePath(value).expanduser()
        if not output_folder.is_absolute():
            raise HTTPException(status_code=422, detail="插件保存路径必须是绝对路径")
        output_folder.mkdir(parents=True, exist_ok=True)
        return output_folder

    async def progress_stream(self, progress_queue=None, stop_statuses=(0, 2, 3)):
        """
        Yield progress frames until one of the configured terminal statuses.
        Batch image translation keeps status 0 result frames flowing until
        its final status 3 completion frame.
        """
        progress_queue = progress_queue or self.progress_queue
        while True:
            progress = await progress_queue.get()
            yield progress
            if progress[0] in stop_statuses:
                break

    async def _acquire_lock(self, job_label: str):
        """Queue a shared-core request and expose its owner for diagnostics."""
        self._lock_waiters += 1
        try:
            await self.lock.acquire()
        finally:
            self._lock_waiters = max(0, self._lock_waiters - 1)
        # Each request gets its own stream queue. This prevents a terminal
        # frame from a completed request being consumed by the next request
        # when the two response coroutines are scheduled close together.
        self.progress_queue = asyncio.Queue()
        self._active_job = str(job_label or "shared-task")
        self._active_job_started_at = time.time()
        return self.progress_queue

    def _release_lock(self):
        """Release the current request lock exactly once."""
        self._active_job = None
        self._active_job_started_at = None
        if self.lock.locked():
            self.lock.release()

    async def _stream_with_disconnect_cleanup(
        self,
        worker_task: asyncio.Task,
        progress_queue,
        stop_statuses=(0, 2, 3),
    ):
        """Forward frames and cancel the worker if its HTTP stream disappears.

        A StreamingResponse is independent from the task created to run the
        translator.  Without this wrapper, closing the Qt/browser process can
        leave that task running until the provider call happens to return,
        keeping the shared core occupied and causing later 429 responses.
        """
        completed = False
        try:
            async for progress in self.progress_stream(
                progress_queue=progress_queue,
                stop_statuses=stop_statuses,
            ):
                yield progress
                if progress[0] in stop_statuses:
                    completed = True
        finally:
            if not completed and not worker_task.done():
                worker_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await worker_task

    async def run_method(self, method, **attributes):
        try:
            if asyncio.iscoroutinefunction(method):
                result = await method(**attributes)
            else:
                result = method(**attributes)

            # 检查是否使用占位符，如果是则创建最小化的结果对象
            if hasattr(result, 'use_placeholder') and result.use_placeholder:
                # 创建一个最小的Context对象，只包含占位符图片，避免传输大量数据
                from PIL import Image

                from manga_translator import Context
                minimal_result = Context()
                minimal_result.result = Image.new('RGB', (1, 1), color='white')
                minimal_result.use_placeholder = True
                result_bytes = pickle.dumps(minimal_result)
            else:
                result_bytes = pickle.dumps(result)

            encoded_result = b'\x00' + len(result_bytes).to_bytes(4, 'big') + result_bytes
            await self.progress_queue.put(encoded_result)
        except Exception as exc:
            print(f"[SHARED ERROR] {type(exc).__name__}")
            err_bytes = b"Shared worker failed"
            encoded_result = b'\x02' + len(err_bytes).to_bytes(4, 'big') + err_bytes
            await self.progress_queue.put(encoded_result)
        finally:
            self._release_lock()

    async def run_image_method(self, payload: dict):
        """Translate one image with the desktop application's core pipeline."""
        temp_dir = None
        previous_tempdir = tempfile.tempdir
        previous_runtime_result_root = getattr(self.manga, "_runtime_result_root", None)
        previous_verbose = getattr(self.manga, "verbose", False)
        try:
            image = _decode_image(payload.get("image", ""))
            filename = FilePath(str(payload.get("filename") or "manga-page.png")).name
            output_path = self._resolve_output_folder(payload.get("outputFolder", ""))
            process_root = _process_root(output_path)
            task_folder = _safe_task_id(payload.get("taskId")) or "single"
            source_url = str(payload.get("sourceUrl") or "")
            task_root = _task_dir(task_folder, output_path, source_url, for_write=True) or (
                process_root / "tasks" / _safe_work_name(source_url) / task_folder
            )
            temp_root = task_root / "temp"
            translated_root = task_root / "translated"
            temp_root.mkdir(parents=True, exist_ok=True)
            translated_root.mkdir(parents=True, exist_ok=True)

            # Keep temporary files created by the core and optional upscalers
            # under the selected output directory for this request.
            tempfile.tempdir = str(temp_root)
            # The desktop pipeline accepts an in-memory PIL image, but several
            # compatibility paths use image.name as a real source path for
            # sidecars, resume context, or metadata. Give those paths a real,
            # short-lived file without exposing browser URLs to the core.
            temp_dir = FilePath(tempfile.mkdtemp(prefix="immersive-translate-input-", dir=str(temp_root)))
            input_path = temp_dir / filename
            image.save(input_path, format="PNG")
            image.name = str(input_path)
            save_info = dict(self.save_info)
            save_info["output_folder"] = str(translated_root)
            save_info["input_folders"] = set()
            save_info["save_to_source_dir"] = False
            # The browser bridge must never persist App debug images. Keep
            # any non-debug runtime artifacts under the task, if a core path
            # needs them, without creating a dedicated debug directory.
            self.manga.verbose = False
            self.manga._runtime_result_root = str(task_root / "runtime")
            result = await self.manga.translate(
                image=image,
                config=self.desktop_config,
                image_name=filename,
                save_info=save_info,
            )
            # The desktop save path intentionally releases ctx.result after it
            # writes the final image. The browser still needs those final bytes,
            # so reopen the verified output instead of treating the cleared
            # in-memory result as a translation failure.
            if getattr(result, "result", None) is None:
                result_output_path = getattr(result, "output_path", None)
                output_file = FilePath(str(result_output_path)) if result_output_path else None
                if (
                    getattr(result, "success", False)
                    and output_file is not None
                    and output_file.is_file()
                ):
                    with Image.open(output_file) as saved_image:
                        saved_image.load()
                        result.result = saved_image.copy()
            result_bytes = transform_to_image(result)
            result_path = FilePath(str(getattr(result, "output_path", "") or translated_root / filename))
            _write_task_manifest_result(
                payload.get("taskId", ""),
                payload.get("pageIndex"),
                output_folder=output_path,
                result_path=result_path,
                source_url=source_url,
                image_url=payload.get("imageUrl", ""),
                filename=filename,
            )
            await self.progress_queue.put(
                b'\x00' + len(result_bytes).to_bytes(4, 'big') + result_bytes
            )
        except Exception as exc:
            print(f"[SHARED IMAGE ERROR] {type(exc).__name__}: {exc}")
            traceback.print_exc()
            error_data = json.dumps(
                {"error": str(exc), "stage": "translate"},
                ensure_ascii=False,
            ).encode("utf-8")
            await self.progress_queue.put(
                b'\x02' + len(error_data).to_bytes(4, 'big') + error_data
            )
        finally:
            self.manga.verbose = previous_verbose
            self.manga._runtime_result_root = previous_runtime_result_root
            tempfile.tempdir = previous_tempdir
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            self._release_lock()

    async def run_batch_image_method(self, payload: dict):
        """Run the App concurrent pipeline and stream each finished page."""
        temp_root = None
        previous_tempdir = tempfile.tempdir
        previous_runtime_result_root = getattr(self.manga, "_runtime_result_root", None)
        previous_verbose = getattr(self.manga, "verbose", False)
        previous_stream_callback = getattr(self.manga, "_stream_result_callback", None)
        previous_batch_concurrent = getattr(self.manga, "batch_concurrent", False)
        try:
            entries = payload.get("images")
            if not isinstance(entries, list) or not entries:
                raise HTTPException(status_code=422, detail="批量翻译没有图片")
            if len(entries) > BROWSER_MANGA_BATCH_WINDOW_SIZE:
                raise HTTPException(
                    status_code=422,
                    detail=f"单次批量翻译最多支持{BROWSER_MANGA_BATCH_WINDOW_SIZE}张图片",
                )

            output_path = self._resolve_output_folder(payload.get("outputFolder", ""))
            process_root = _process_root(output_path)
            task_folder = _safe_task_id(payload.get("taskId")) or "single"
            source_url = str(payload.get("sourceUrl") or "")
            task_root = _task_dir(task_folder, output_path, source_url, for_write=True) or (
                process_root / "tasks" / _safe_work_name(source_url) / task_folder
            )
            temp_root = task_root / "batch-temp"
            translated_root = task_root / "translated"
            temp_root.mkdir(parents=True, exist_ok=True)
            translated_root.mkdir(parents=True, exist_ok=True)
            tempfile.tempdir = str(temp_root)

            try:
                batch_size = int(payload.get("batchSize") or 3)
            except (TypeError, ValueError):
                batch_size = 3
            batch_size = max(1, min(batch_size, BROWSER_MANGA_BATCH_WINDOW_SIZE))

            loop = asyncio.get_running_loop()
            stream_loop_thread = get_ident()
            page_by_input_path = {}
            page_by_basename = {}
            emitted_pages = set()
            images_with_configs = []

            def put_frame_from_worker(status: int, value: object):
                if isinstance(value, bytes):
                    encoded = value
                else:
                    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
                frame = bytes([status]) + len(encoded).to_bytes(4, "big") + encoded
                # The real callback runs in ConcurrentPipeline's render
                # worker. Keep this same-thread branch deterministic for
                # lightweight/test pipelines without blocking the event loop
                # waiting on itself.
                if get_ident() == stream_loop_thread:
                    self.progress_queue.put_nowait(frame)
                    return
                future = asyncio.run_coroutine_threadsafe(self.progress_queue.put(frame), loop)
                future.result()

            def resolve_page(ctx):
                image_name = str(getattr(ctx, "image_name", "") or "")
                try:
                    resolved_name = str(FilePath(image_name).resolve())
                except OSError:
                    resolved_name = image_name
                meta = page_by_input_path.get(resolved_name)
                if meta is None:
                    basename = FilePath(image_name).name
                    candidates = page_by_basename.get(basename, [])
                    meta = next((item for item in candidates if item["pageIndex"] not in emitted_pages), None)
                return meta

            def stream_result(ctx):
                meta = resolve_page(ctx)
                if meta is None:
                    return
                page_index = meta["pageIndex"]
                emitted_pages.add(page_index)
                error_message = getattr(ctx, "translation_error", None) or getattr(ctx, "error", None)
                if error_message:
                    put_frame_from_worker(0, {
                        "type": "error",
                        "taskId": task_folder,
                        "pageIndex": page_index,
                        "filename": meta["filename"],
                        "error": str(error_message),
                    })
                    return
                if getattr(ctx, "result", None) is None:
                    put_frame_from_worker(0, {
                        "type": "error",
                        "taskId": task_folder,
                        "pageIndex": page_index,
                        "filename": meta["filename"],
                        "error": "翻译后端没有返回结果图片",
                    })
                    return

                try:
                    result_bytes = transform_to_image(ctx)
                    result_path = FilePath(str(
                        getattr(ctx, "output_path", "") or translated_root / meta["filename"]
                    ))
                    _write_task_manifest_result(
                        payload.get("taskId", ""),
                        page_index,
                        output_folder=output_path,
                        result_path=result_path,
                        source_url=source_url,
                        image_url=meta["imageUrl"],
                        filename=meta["filename"],
                    )
                    put_frame_from_worker(0, {
                        "type": "result",
                        "taskId": task_folder,
                        "pageIndex": page_index,
                        "filename": meta["filename"],
                        "mimeType": "image/png",
                        "data": base64.b64encode(result_bytes).decode("ascii"),
                    })
                except Exception as error:
                    put_frame_from_worker(0, {
                        "type": "error",
                        "taskId": task_folder,
                        "pageIndex": page_index,
                        "filename": meta["filename"],
                        "error": str(error),
                        "stage": "stream-result",
                    })

            self.manga._stream_result_callback = stream_result
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or not entry.get("image"):
                    raise HTTPException(status_code=422, detail=f"第 {index + 1} 张图片数据无效")
                image = _decode_image(entry["image"])
                filename = FilePath(str(entry.get("filename") or f"page-{index + 1}.png")).name
                page_index = int(entry.get("pageIndex", index))
                cached_path = _manifest_page_path(
                    task_folder,
                    page_index,
                    output_path,
                    source_url,
                )
                if cached_path is not None and cached_path.is_file():
                    emitted_pages.add(page_index)
                    put_frame_from_worker(0, {
                        "type": "skipped",
                        "taskId": task_folder,
                        "pageIndex": page_index,
                        "filename": filename,
                        "error": "缓存中已有结果图片",
                    })
                    continue
                page_dir = temp_root / f"{index:04d}"
                page_dir.mkdir(parents=True, exist_ok=True)
                input_path = page_dir / filename
                image.save(input_path, format="PNG")
                image.name = str(input_path)
                meta = {
                    "pageIndex": page_index,
                    "filename": filename,
                    "imageUrl": str(entry.get("imageUrl") or "")[:4000],
                }
                page_by_input_path[str(input_path.resolve())] = meta
                page_by_basename.setdefault(filename, []).append(meta)
                images_with_configs.append((image, self.desktop_config))

            save_info = dict(self.save_info)
            save_info["output_folder"] = str(translated_root)
            save_info["input_folders"] = set()
            save_info["save_to_source_dir"] = False
            save_info["retain_result_for_stream"] = True
            self.manga.verbose = False
            self.manga._runtime_result_root = str(task_root / "runtime")
            if not images_with_configs:
                done_payload = {
                    "type": "done",
                    "taskId": task_folder,
                    "success": True,
                    "processed": len(emitted_pages),
                    "total": len(entries),
                }
                encoded_done = json.dumps(done_payload, ensure_ascii=False).encode("utf-8")
                await self.progress_queue.put(
                    b'\x03' + len(encoded_done).to_bytes(4, "big") + encoded_done
                )
                return
            # The browser bridge explicitly uses the App's concurrent pipeline.
            self.manga.batch_concurrent = True
            contexts = await self.manga.translate_batch(
                images_with_configs,
                batch_size=batch_size,
                save_info=save_info,
            )

            # Skipped outputs do not pass through the render callback. Close
            # those page slots so the browser can finish the current batch.
            for ctx in contexts:
                meta = resolve_page(ctx)
                if meta is None or meta["pageIndex"] in emitted_pages:
                    continue
                emitted_pages.add(meta["pageIndex"])
                _write_task_manifest_result(
                    payload.get("taskId", ""),
                    meta["pageIndex"],
                    output_folder=output_path,
                    result_path=FilePath(str(
                        getattr(ctx, "output_path", "") or translated_root / meta["filename"]
                    )),
                    source_url=source_url,
                    image_url=meta["imageUrl"],
                    filename=meta["filename"],
                )
                put_frame_from_worker(0, {
                    "type": "skipped",
                    "taskId": task_folder,
                    "pageIndex": meta["pageIndex"],
                    "filename": meta["filename"],
                    "error": getattr(ctx, "skip_message", "输出文件已存在"),
                })

            done_payload = {
                "type": "done",
                "taskId": task_folder,
                "success": True,
                "processed": len(emitted_pages),
                "total": len(entries),
            }
            await self.progress_queue.put(
                b'\x03' + len(json.dumps(done_payload, ensure_ascii=False).encode("utf-8")).to_bytes(4, "big")
                + json.dumps(done_payload, ensure_ascii=False).encode("utf-8")
            )
        except Exception as exc:
            error_payload = json.dumps(
                {"type": "error", "error": str(exc), "stage": "batch-translate"},
                ensure_ascii=False,
            ).encode("utf-8")
            await self.progress_queue.put(b'\x02' + len(error_payload).to_bytes(4, "big") + error_payload)
        finally:
            self.manga.verbose = previous_verbose
            self.manga._stream_result_callback = previous_stream_callback
            self.manga.batch_concurrent = previous_batch_concurrent
            self.manga._runtime_result_root = previous_runtime_result_root
            tempfile.tempdir = previous_tempdir
            if temp_root is not None:
                shutil.rmtree(temp_root, ignore_errors=True)
            self._release_lock()

    async def run_file_batch_method(self, payload: dict):
        """Run local files through the same worker used by browser batches."""
        previous_runtime_result_root = getattr(self.manga, "_runtime_result_root", None)
        previous_verbose = getattr(self.manga, "verbose", False)
        previous_batch_concurrent = getattr(self.manga, "batch_concurrent", False)
        try:
            raw_files = payload.get("files")
            if not isinstance(raw_files, list) or not raw_files:
                raise HTTPException(status_code=422, detail="批量文件任务没有输入文件")
            if len(raw_files) > 10000:
                raise HTTPException(status_code=422, detail="单次批量文件任务最多支持10000个文件")

            files = []
            for index, raw_path in enumerate(raw_files):
                path = FilePath(str(raw_path or "")).expanduser()
                if not path.is_absolute() or not path.is_file():
                    raise HTTPException(status_code=422, detail=f"第 {index + 1} 个输入文件不存在")
                files.append(path)

            output_path = self._resolve_output_folder(payload.get("outputFolder", ""))
            input_folders = {
                str(FilePath(str(value)).expanduser())
                for value in (payload.get("inputFolders") or [])
                if str(value or "").strip()
            }
            save_info = {
                "output_folder": str(output_path),
                "format": payload.get("format"),
                "overwrite": bool(payload.get("overwrite", True)),
                "input_folders": input_folders,
                "save_to_source_dir": bool(payload.get("saveToSourceDir", False)),
            }
            long_image_work_dir = str(payload.get("longImageWorkDir") or "").strip()
            if long_image_work_dir:
                long_image_path = FilePath(long_image_work_dir).expanduser()
                if not long_image_path.is_absolute():
                    raise HTTPException(status_code=422, detail="长图工作目录必须是绝对路径")
                save_info["long_image_work_dir"] = str(long_image_path)

            try:
                batch_size = int(payload.get("batchSize") or 1)
            except (TypeError, ValueError):
                batch_size = 1
            batch_size = max(1, min(batch_size, 100))
            self.manga.batch_concurrent = bool(
                payload.get("batchConcurrent", previous_batch_concurrent)
            )

            contexts = await self.manga.translate_batch(
                [(str(path), self.desktop_config) for path in files],
                batch_size=batch_size,
                save_info=save_info,
            )

            results = []
            for index, path in enumerate(files):
                ctx = contexts[index] if index < len(contexts) else None

                def context_value(key: str, default=None):
                    if isinstance(ctx, dict):
                        return ctx.get(key, default)
                    return getattr(ctx, key, default) if ctx is not None else default

                error_message = (
                    context_value("translation_error")
                    or context_value("error")
                    or context_value("critical_error_msg")
                )
                output_file = context_value("output_path")
                result = {
                    "type": "file_result",
                    "index": index,
                    "original_path": str(context_value("image_name") or path),
                    "output_path": str(output_file) if output_file else None,
                    "success": not bool(error_message) and bool(
                        context_value("success")
                        or context_value("result")
                        or output_file
                    ),
                    "skipped": bool(context_value("skipped", False)),
                    "skip_reason": context_value("skip_reason"),
                    "skip_message": context_value("skip_message"),
                    "error": str(error_message) if error_message else None,
                }
                results.append(result)
                encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
                await self.progress_queue.put(
                    b'\x00' + len(encoded).to_bytes(4, "big") + encoded
                )

            done_payload = {
                "type": "done",
                "success": all(item["success"] for item in results),
                "processed": sum(1 for item in results if item["success"]),
                "failed": sum(1 for item in results if not item["success"]),
                "total": len(results),
            }
            encoded_done = json.dumps(done_payload, ensure_ascii=False).encode("utf-8")
            await self.progress_queue.put(
                b'\x03' + len(encoded_done).to_bytes(4, "big") + encoded_done
            )
        except Exception as exc:
            error_payload = json.dumps(
                {"type": "error", "error": str(exc), "stage": "file-batch-translate"},
                ensure_ascii=False,
            ).encode("utf-8")
            await self.progress_queue.put(
                b'\x02' + len(error_payload).to_bytes(4, "big") + error_payload
            )
        finally:
            self.manga.verbose = previous_verbose
            self.manga.batch_concurrent = previous_batch_concurrent
            self.manga._runtime_result_root = previous_runtime_result_root
            self._release_lock()

    async def reload_runtime_config(self) -> None:
        """Reload the desktop configuration before a new client task."""
        raw_config = _read_desktop_config()
        desktop_config, translator_params, save_info = _load_desktop_config(raw_config)
        self.config_document = raw_config
        self.config_revision = _config_revision(raw_config)
        self.desktop_config = desktop_config
        self.save_info = save_info
        self.manga = MangaTranslator(translator_params)
        self.progress_queue = asyncio.Queue()
        self._register_progress_hook()

    async def apply_runtime_config(self, raw_config: dict) -> bool:
        """Validate and apply a browser-selected Manga Translator config."""
        try:
            desktop_ui_path = str(PROJECT_ROOT / "desktop_qt_ui")
            if desktop_ui_path not in sys.path:
                sys.path.insert(0, desktop_ui_path)
            from core.config_models import AppSettings

            AppSettings.model_validate(raw_config)
            desktop_config, translator_params, save_info = _load_desktop_config(raw_config)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="配置格式或参数值不符合当前 manga-translator-ui 版本",
            ) from exc

        revision = _config_revision(raw_config)
        if revision == self.config_revision:
            return False

        # Keep the local desktop app and the local bridge on the same file.
        # AIGate applies the same document in memory so user credentials do not
        # get persisted to the shared /home/waas disk.
        if not self.nonce:
            temporary_file = DESKTOP_CONFIG_PATH.with_suffix(".json.tmp")
            temporary_file.write_text(
                json.dumps(raw_config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_file.replace(DESKTOP_CONFIG_PATH)

        self.config_document = raw_config
        self.config_revision = revision
        self.desktop_config = desktop_config
        self.save_info = save_info
        self.manga = MangaTranslator(translator_params)
        self.progress_queue = asyncio.Queue()
        self._register_progress_hook()
        return True

    def require_config_revision(self, payload: dict) -> None:
        requested = str(payload.get("configRevision") or "").strip()
        if requested and requested != self.config_revision:
            raise HTTPException(
                status_code=409,
                detail="翻译配置版本已变化，请重新同步配置后再继续当前章节",
            )


    def check_nonce(self, request: Request):
        if self.nonce:
            nonce = request.headers.get('X-Nonce')
            if nonce != self.nonce:
                raise HTTPException(401, detail="Nonce does not match")

    def get_fn(self, method_name: str):
        if method_name not in {"translate", "translate_batch"}:
            raise HTTPException(status_code=403, detail="These functions are not allowed to be executed remotely")
        method = getattr(self.manga, method_name, None)
        if not method:
            raise HTTPException(status_code=404, detail="Method not found")
        return method

    async def listen(self, translation_params: dict = None):
        app = FastAPI()

        @app.get("/logs")
        async def read_logs(request: Request, offset: int = 0):
            """Read a bounded incremental chunk for the extension debug panel."""
            self.check_nonce(request)
            try:
                requested_offset = max(0, int(offset))
            except (TypeError, ValueError):
                requested_offset = 0

            try:
                log_size = BACKEND_LOG_PATH.stat().st_size
                # Start with the tail on first load, and recover if the log was
                # rotated or truncated while the page was polling.
                if requested_offset <= 0:
                    read_offset = max(0, log_size - MAX_LOG_READ_BYTES)
                elif requested_offset > log_size:
                    read_offset = max(0, log_size - MAX_LOG_READ_BYTES)
                else:
                    read_offset = requested_offset

                with BACKEND_LOG_PATH.open("rb") as log_file:
                    log_file.seek(read_offset)
                    data = log_file.read(MAX_LOG_READ_BYTES)
                    next_offset = log_file.tell()
                return {
                    "text": data.decode("utf-8", errors="replace"),
                    "offset": next_offset,
                    "size": log_size,
                    "path": str(BACKEND_LOG_PATH),
                }
            except FileNotFoundError:
                return {
                    "text": "",
                    "offset": 0,
                    "size": 0,
                    "path": str(BACKEND_LOG_PATH),
                }
            except OSError as error:
                raise HTTPException(status_code=500, detail=f"读取后端日志失败：{error}")

        @app.get("/cache/task/{task_id}/manifest")
        async def read_cache_manifest(task_id: str, sourceUrl: str = "", outputFolder: str = ""):
            output_path = self._resolve_output_folder(outputFolder)
            manifest_file = _task_manifest_file(task_id, output_path, sourceUrl)
            if manifest_file is None:
                raise HTTPException(status_code=422, detail="任务 ID 格式不正确")
            if not manifest_file.is_file():
                return {"found": False, "taskId": _safe_task_id(task_id), "pages": []}
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise HTTPException(status_code=500, detail=f"读取翻译缓存失败：{error}")
            if not isinstance(manifest, dict):
                return {"found": False, "taskId": _safe_task_id(task_id), "pages": []}
            pages = manifest.get("pages") if isinstance(manifest.get("pages"), dict) else {}
            return {
                "found": bool(pages),
                "taskId": _safe_task_id(task_id),
                "sourceUrl": manifest.get("sourceUrl", ""),
                "updatedAt": manifest.get("updatedAt", 0),
                "pages": list(pages.values()),
            }

        @app.get("/cache/task/{task_id}/image/{page_index}")
        async def read_cached_image(
            task_id: str,
            page_index: int,
            sourceUrl: str = "",
            outputFolder: str = "",
        ):
            output_path = self._resolve_output_folder(outputFolder)
            page_file = _manifest_page_path(task_id, page_index, output_path, sourceUrl)
            if page_file is None or not page_file.is_file():
                raise HTTPException(status_code=404, detail="翻译缓存图片不存在")
            media_type = mimetypes.guess_type(page_file.name)[0] or "application/octet-stream"
            return FileResponse(page_file, media_type=media_type)

        @app.post("/cache/task/{task_id}/image/{page_index}")
        async def save_external_translation(
            task_id: str,
            page_index: int,
            request: Request,
        ):
            """Persist an image translated by a remote worker into the local cache."""
            payload = await request.json()
            image_data = str(payload.get("image") or "")
            if not image_data or len(image_data) > 180 * 1024 * 1024:
                raise HTTPException(status_code=422, detail="翻译图片为空或过大")
            try:
                image_bytes = base64.b64decode(image_data, validate=True)
                with Image.open(io.BytesIO(image_bytes)) as image:
                    image.load()
                    rgb_or_rgba = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            except Exception:
                raise HTTPException(status_code=422, detail="翻译结果不是有效图片")

            output_path = self._resolve_output_folder(payload.get("outputFolder", ""))
            source_url = str(payload.get("sourceUrl") or "")[:4000]
            task_dir = _task_dir(task_id, output_path, source_url, for_write=True)
            if task_dir is None:
                raise HTTPException(status_code=422, detail="任务 ID 格式不正确")
            try:
                index = int(page_index)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="页码格式不正确")
            if index < 0 or index > 10000:
                raise HTTPException(status_code=422, detail="页码超出范围")

            original_name = FilePath(str(payload.get("filename") or f"page-{index + 1}.png")).name
            result_stem = FilePath(original_name).stem[:120] or f"page-{index + 1}"
            result_name = f"page-{index:04d}-{result_stem}.png"
            translated_root = task_dir / "translated"
            translated_root.mkdir(parents=True, exist_ok=True)
            result_path = translated_root / result_name
            temporary_path = result_path.with_suffix(".png.tmp")
            try:
                rgb_or_rgba.save(temporary_path, format="PNG")
                temporary_path.replace(result_path)
                _write_task_manifest_result(
                    task_id,
                    index,
                    output_folder=output_path,
                    result_path=result_path,
                    source_url=source_url,
                    image_url=str(payload.get("imageUrl") or "")[:4000],
                    filename=original_name,
                )
            except OSError as error:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise HTTPException(status_code=500, detail=f"保存翻译结果失败：{error}")

            return {
                "success": True,
                "taskId": _safe_task_id(task_id),
                "pageIndex": index,
                "path": str(result_path.relative_to(output_path)),
            }

        @app.get("/is_locked")
        async def is_locked():
            return {
                "locked": self.lock.locked(),
                "activeJob": self._active_job,
                "activeSince": self._active_job_started_at,
                "queued": self._lock_waiters,
            }

        @app.get("/backend_info")
        async def backend_info():
            """Identify the one local shared core used by Qt and extensions."""
            return {
                "service": "manga-translator-ui",
                "mode": "shared",
                "protocol": SHARED_BACKEND_PROTOCOL,
                "configApiVersion": 1,
                "pid": os.getpid(),
                "host": self.host,
                "port": self.port,
                "startedAt": self.started_at,
                "projectRoot": str(PROJECT_ROOT),
            }

        @app.get("/config")
        async def get_config(request: Request):
            self.check_nonce(request)
            return {
                "success": True,
                "config": self.config_document,
                "revision": self.config_revision,
            }

        @app.post("/config/apply")
        async def apply_config(request: Request):
            self.check_nonce(request)
            try:
                payload = await request.json()
            except Exception as exc:
                raise HTTPException(status_code=400, detail="配置必须是 JSON 对象") from exc
            raw_config = payload.get("config") if isinstance(payload, dict) else None
            if not isinstance(raw_config, dict):
                raise HTTPException(status_code=422, detail="配置必须是 JSON 对象")

            await self._acquire_lock("apply-config")
            try:
                changed = await self.apply_runtime_config(raw_config)
                return {
                    "success": True,
                    "changed": changed,
                    "revision": self.config_revision,
                    "persisted": not bool(self.nonce),
                }
            finally:
                self._release_lock()

        @app.post("/reload_config")
        async def reload_config(request: Request):
            """Reload the persisted desktop configuration before a task."""
            self.check_nonce(request)
            await self._acquire_lock("reload-config")
            try:
                await self.reload_runtime_config()
                return {"success": True}
            finally:
                self._release_lock()

        @app.post("/simple_execute/{method_name}")
        async def execute_method(request: Request, method_name: str = Path(...)):
            self.check_nonce(request)
            method = self.get_fn(method_name)
            await self._acquire_lock(f"simple:{method_name}")
            try:
                attr = _decode_attributes(method_name, await request.json())
                if asyncio.iscoroutinefunction(method):
                    result = await method(**attr)
                else:
                    result = method(**attr)
                result_bytes = pickle.dumps(result)
                return Response(content=result_bytes, media_type="application/octet-stream")
            except HTTPException:
                raise
            except Exception:
                print("[SHARED ERROR] Worker execution failed")
                raise HTTPException(status_code=500, detail="Shared worker failed")
            finally:
                self._release_lock()

        @app.post("/execute/{method_name}")
        async def execute_method_stream(request: Request, method_name: str = Path(...)):
            self.check_nonce(request)
            method = self.get_fn(method_name)
            progress_queue = await self._acquire_lock(f"stream:{method_name}")
            try:
                attr = _decode_attributes(method_name, await request.json())
            except Exception:
                self._release_lock()
                raise

            # streaming response
            try:
                worker_task = asyncio.create_task(self.run_method(method, **attr))
            except Exception:
                self._release_lock()
                raise
            streaming_response = StreamingResponse(
                self._stream_with_disconnect_cleanup(worker_task, progress_queue),
                media_type="application/octet-stream",
            )
            return streaming_response

        @app.post("/execute_image/batch_translate")
        async def execute_image_batch_stream(request: Request):
            """Run the App concurrent pipeline and stream page results."""
            self.check_nonce(request)
            payload = None
            try:
                payload = await request.json()
                entries = payload.get("images") if isinstance(payload, dict) else None
                if not isinstance(entries, list) or not entries:
                    raise HTTPException(status_code=422, detail="Invalid batch image data")
            except Exception:
                raise

            progress_queue = await self._acquire_lock(
                f"image-batch:{payload.get('taskId') or 'anonymous'}"
            )

            try:
                self.require_config_revision(payload)
                worker_task = asyncio.create_task(self.run_batch_image_method(payload))
            except Exception:
                self._release_lock()
                raise
            streaming_response = StreamingResponse(
                self._stream_with_disconnect_cleanup(
                    worker_task,
                    progress_queue,
                    stop_statuses={2, 3},
                ),
                media_type="application/octet-stream",
            )
            return streaming_response

        @app.post("/execute_image/{method_name}")
        async def execute_image_stream(request: Request, method_name: str = Path(...)):
            self.check_nonce(request)
            if method_name != "translate":
                raise HTTPException(status_code=403, detail="Only translate is allowed")
            payload = None
            try:
                payload = await request.json()
                if not isinstance(payload, dict) or not payload.get("image"):
                    raise HTTPException(status_code=422, detail="Invalid image data")
            except Exception:
                raise

            progress_queue = await self._acquire_lock(
                f"image:{payload.get('taskId') or 'anonymous'}"
            )

            try:
                self.require_config_revision(payload)
                worker_task = asyncio.create_task(self.run_image_method(payload))
            except Exception:
                self._release_lock()
                raise
            streaming_response = StreamingResponse(
                self._stream_with_disconnect_cleanup(worker_task, progress_queue),
                media_type="application/octet-stream",
            )
            return streaming_response

        @app.post("/execute_files/batch_translate")
        async def execute_files_batch_stream(request: Request):
            """Queue local files for the same shared translator process."""
            self.check_nonce(request)
            payload = None
            try:
                payload = await request.json()
                if not isinstance(payload, dict):
                    raise HTTPException(status_code=422, detail="批量文件任务数据无效")
                if not isinstance(payload.get("files"), list) or not payload["files"]:
                    raise HTTPException(status_code=422, detail="批量文件任务没有输入文件")
            except Exception:
                raise

            progress_queue = await self._acquire_lock(
                f"file-batch:{payload.get('taskId') or 'anonymous'}"
            )
            try:
                worker_task = asyncio.create_task(self.run_file_batch_method(payload))
            except Exception:
                self._release_lock()
                raise
            streaming_response = StreamingResponse(
                self._stream_with_disconnect_cleanup(
                    worker_task,
                    progress_queue,
                    stop_statuses={2, 3},
                ),
                media_type="application/octet-stream",
            )
            return streaming_response

        config = uvicorn.Config(
            app, 
            host=self.host, 
            port=self.port,
            timeout_keep_alive=1800  # 保持连接30分钟以支持批量翻译
        )
        server = uvicorn.Server(config)
        await server.serve()
