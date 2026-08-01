import asyncio
import base64
import io
import pickle
from threading import Lock

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Request, Response
from PIL import Image
from starlette.responses import StreamingResponse

from manga_translator import Config, MangaTranslator


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


class MangaShare:
    def __init__(self, params: dict = None):
        self.manga = MangaTranslator(params)
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

        config = uvicorn.Config(
            app, 
            host=self.host, 
            port=self.port,
            timeout_keep_alive=1800  # 保持连接30分钟以支持批量翻译
        )
        server = uvicorn.Server(config)
        await server.serve()
