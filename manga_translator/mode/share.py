import asyncio
import base64
import io
import json
import os
import pickle
import re
import shutil
import tempfile
import time
import traceback
from threading import Lock
from pathlib import Path as FilePath

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
LEGACY_CACHE_ROOT = FilePath.home() / "Library" / "Application Support" / "ImmersiveTranslate" / "manga-cache"
PROCESS_DIR_NAME = ".immersive-translate"


def _load_desktop_config() -> tuple[Config, dict, dict]:
    """Load the same user config used by the desktop application.

    The shared bridge intentionally reads the desktop config locally instead of
    accepting a browser-supplied config or API credentials.
    """
    raw_config = json.loads(DESKTOP_CONFIG_PATH.read_text(encoding="utf-8"))
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


def _cache_root(output_folder: FilePath) -> FilePath:
    return _process_root(output_folder) / "cache"


def _cache_task_dir(task_id: str, cache_root: FilePath | None = None) -> FilePath | None:
    safe_id = _safe_task_id(task_id)
    if not safe_id:
        return None
    return (cache_root or LEGACY_CACHE_ROOT) / safe_id


def _cache_page_file(
    task_id: str,
    page_index: object,
    cache_root: FilePath | None = None,
) -> FilePath | None:
    task_dir = _cache_task_dir(task_id, cache_root)
    try:
        index = int(page_index)
    except (TypeError, ValueError):
        return None
    if task_dir is None or index < 0 or index > 10000:
        return None
    return task_dir / f"page-{index:04d}.png"


def _write_cached_result(
    task_id: str,
    page_index: object,
    result_bytes: bytes,
    *,
    source_url: str = "",
    image_url: str = "",
    filename: str = "",
    cache_root: FilePath | None = None,
) -> None:
    page_file = _cache_page_file(task_id, page_index, cache_root)
    if page_file is None:
        return
    task_dir = page_file.parent
    task_dir.mkdir(parents=True, exist_ok=True)
    page_file.write_bytes(result_bytes)

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
    try:
        index = int(page_index)
    except (TypeError, ValueError):
        return
    manifest["pages"][str(index)] = {
        "index": index,
        "filename": FilePath(str(filename or "page.png")).name,
        "imageUrl": str(image_url or "")[:4000],
        "path": page_file.name,
        "updatedAt": int(time.time()),
    }
    temp_manifest = manifest_file.with_suffix(".json.tmp")
    temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_manifest.replace(manifest_file)


class MangaShare:
    def __init__(self, params: dict = None):
        params = params or {}
        self.desktop_config, translator_params, self.save_info = _load_desktop_config()
        self.manga = MangaTranslator(translator_params)
        self.host = params.get('host', '127.0.0.1')
        self.port = int(params.get('port', '5003'))
        self.nonce = params.get('nonce', None)

        # each chunk has a structure like this status_code(int/1byte),len(int/4bytes),bytechunk
        # status codes are 0 for result, 1 for progress report, 2 for error
        self.progress_queue = asyncio.Queue()
        self.lock = Lock()

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

    async def progress_stream(self):
        """
        loops until the status is != 1 which is eiter an error or the result
        """
        while True:
            progress = await self.progress_queue.get()
            yield progress
            if progress[0] != 1:
                break

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
            self.lock.release()

    async def run_image_method(self, payload: dict):
        """Translate one image with the desktop application's core pipeline."""
        temp_dir = None
        previous_tempdir = tempfile.tempdir
        previous_runtime_result_root = getattr(self.manga, "_runtime_result_root", None)
        try:
            image = _decode_image(payload.get("image", ""))
            filename = FilePath(str(payload.get("filename") or "manga-page.png")).name
            output_path = self._resolve_output_folder(payload.get("outputFolder", ""))
            process_root = _process_root(output_path)
            task_folder = _safe_task_id(payload.get("taskId")) or "single"
            task_root = process_root / "tasks" / task_folder
            temp_root = task_root / "temp"
            debug_root = task_root / "debug"
            temp_root.mkdir(parents=True, exist_ok=True)
            debug_root.mkdir(parents=True, exist_ok=True)

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
            save_info["output_folder"] = str(output_path)
            save_info["input_folders"] = set()
            save_info["save_to_source_dir"] = False
            self.manga._runtime_result_root = str(debug_root)
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
            _write_cached_result(
                payload.get("taskId", ""),
                payload.get("pageIndex"),
                result_bytes,
                source_url=payload.get("sourceUrl", ""),
                image_url=payload.get("imageUrl", ""),
                filename=filename,
                cache_root=_cache_root(output_path),
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
            self.manga._runtime_result_root = previous_runtime_result_root
            tempfile.tempdir = previous_tempdir
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            self.lock.release()


    def check_nonce(self, request: Request):
        if self.nonce:
            nonce = request.headers.get('X-Nonce')
            if nonce != self.nonce:
                raise HTTPException(401, detail="Nonce does not match")

    def check_lock(self):
        if not self.lock.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="some Method is already being executed.")

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
        async def read_logs(offset: int = 0):
            """Read a bounded incremental chunk for the extension debug panel."""
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
        async def read_cache_manifest(task_id: str, outputFolder: str = ""):
            output_path = self._resolve_output_folder(outputFolder)
            task_dir = _cache_task_dir(task_id, _cache_root(output_path))
            if task_dir is None:
                raise HTTPException(status_code=422, detail="任务 ID 格式不正确")
            manifest_file = task_dir / "manifest.json"
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
        async def read_cached_image(task_id: str, page_index: int, outputFolder: str = ""):
            output_path = self._resolve_output_folder(outputFolder)
            page_file = _cache_page_file(task_id, page_index, _cache_root(output_path))
            if page_file is None or not page_file.is_file():
                raise HTTPException(status_code=404, detail="翻译缓存图片不存在")
            return FileResponse(page_file, media_type="image/png")

        @app.get("/is_locked")
        async def is_locked():
            if self.lock.locked():
                return {"locked": True}
            return {"locked": False}

        @app.post("/simple_execute/{method_name}")
        async def execute_method(request: Request, method_name: str = Path(...)):
            self.check_nonce(request)
            method = self.get_fn(method_name)
            self.check_lock()
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
                self.lock.release()

        @app.post("/execute/{method_name}")
        async def execute_method_stream(request: Request, method_name: str = Path(...)):
            self.check_nonce(request)
            method = self.get_fn(method_name)
            self.check_lock()
            try:
                attr = _decode_attributes(method_name, await request.json())
            except Exception:
                self.lock.release()
                raise

            # streaming response
            streaming_response = StreamingResponse(self.progress_stream(), media_type="application/octet-stream")
            asyncio.create_task(self.run_method(method, **attr))
            return streaming_response

        @app.post("/execute_image/{method_name}")
        async def execute_image_stream(request: Request, method_name: str = Path(...)):
            self.check_nonce(request)
            if method_name != "translate":
                raise HTTPException(status_code=403, detail="Only translate is allowed")
            self.check_lock()
            try:
                payload = await request.json()
                if not isinstance(payload, dict) or not payload.get("image"):
                    raise HTTPException(status_code=422, detail="Invalid image data")
            except Exception:
                self.lock.release()
                raise

            streaming_response = StreamingResponse(
                self.progress_stream(),
                media_type="application/octet-stream",
            )
            asyncio.create_task(self.run_image_method(payload))
            return streaming_response

        config = uvicorn.Config(
            app, 
            host=self.host, 
            port=self.port,
            timeout_keep_alive=1800  # 保持连接30分钟以支持批量翻译
        )
        server = uvicorn.Server(config)
        await server.serve()
