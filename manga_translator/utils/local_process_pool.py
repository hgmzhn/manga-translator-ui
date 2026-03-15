import asyncio
import atexit
import logging
import multiprocessing
import queue
import threading
import traceback
import uuid
from concurrent.futures import Future
from typing import Any, Dict


logger = logging.getLogger("manga_translator")

_process_pool_cache: dict[tuple, "LocalProcessPool"] = {}
_process_pool_lock = threading.Lock()


def _run_coro(loop: asyncio.AbstractEventLoop, coro):
    return loop.run_until_complete(coro)


def _instantiate_worker(worker_kind: str, worker_params: Dict[str, Any]):
    if worker_kind == "ocr":
        from manga_translator.config import Ocr
        from manga_translator.ocr import _instantiate_ocr

        return _instantiate_ocr(Ocr(worker_params["key"]))

    if worker_kind == "inpainting":
        from manga_translator.config import Inpainter
        from manga_translator.inpainting import _instantiate_inpainter

        return _instantiate_inpainter(Inpainter(worker_params["key"]))

    if worker_kind == "upscaling":
        from manga_translator.config import Upscaler
        from manga_translator.upscaling import _instantiate_upscaler

        init_kwargs = dict(worker_params.get("init_kwargs") or {})
        return _instantiate_upscaler(Upscaler(worker_params["key"]), **init_kwargs)

    raise ValueError(f"Unsupported worker kind: {worker_kind}")


async def _handle_request(instance, worker_kind: str, worker_params: Dict[str, Any], payload: Dict[str, Any]):
    if worker_kind == "ocr":
        from manga_translator.ocr.common import OfflineOCR

        if isinstance(instance, OfflineOCR):
            await instance.load(worker_params["device"])
        return await instance.recognize(
            payload["image"],
            payload["regions"],
            payload["config"],
            payload.get("verbose", False),
        )

    if worker_kind == "inpainting":
        from manga_translator.inpainting.common import OfflineInpainter

        if isinstance(instance, OfflineInpainter):
            await instance.load(
                worker_params["device"],
                force_torch=bool(worker_params.get("force_torch", False)),
            )
        return await instance.inpaint(
            payload["image"],
            payload["mask"],
            payload["config"],
            payload.get("inpainting_size", 1024),
            payload.get("verbose", False),
        )

    if worker_kind == "upscaling":
        from manga_translator.upscaling.common import OfflineUpscaler

        if isinstance(instance, OfflineUpscaler):
            await instance.load(worker_params["device"])
        return await instance.upscale(payload["image_batch"], payload["upscale_ratio"])

    raise ValueError(f"Unsupported worker kind: {worker_kind}")


async def _shutdown_instance(instance):
    if instance is None:
        return
    unload = getattr(instance, "unload", None)
    if unload is not None:
        await unload()


def _worker_main(
    worker_kind: str,
    worker_params: Dict[str, Any],
    request_queue: multiprocessing.Queue,
    response_queue: multiprocessing.Queue,
):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    instance = None

    try:
        while True:
            message = request_queue.get()
            if message is None:
                break

            request_id = message["id"]
            payload = message["payload"]
            try:
                if instance is None:
                    instance = _instantiate_worker(worker_kind, worker_params)
                result = _run_coro(loop, _handle_request(instance, worker_kind, worker_params, payload))
                response_queue.put({"id": request_id, "ok": True, "result": result})
            except Exception as exc:
                response_queue.put(
                    {
                        "id": request_id,
                        "ok": False,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                )
    finally:
        try:
            _run_coro(loop, _shutdown_instance(instance))
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()


class LocalProcessPool:
    def __init__(self, worker_kind: str, worker_params: Dict[str, Any], concurrency: int):
        self.worker_kind = worker_kind
        self.worker_params = dict(worker_params)
        self.concurrency = max(1, int(concurrency or 1))
        self.ctx = multiprocessing.get_context("spawn")
        self.request_queue = self.ctx.Queue()
        self.response_queue = self.ctx.Queue()
        self.pending: dict[str, Future] = {}
        self.pending_lock = threading.Lock()
        self.closed = False
        self.workers = []

        for index in range(self.concurrency):
            process = self.ctx.Process(
                target=_worker_main,
                args=(self.worker_kind, self.worker_params, self.request_queue, self.response_queue),
                name=f"{self.worker_kind}-proc-{index + 1}",
            )
            process.start()
            self.workers.append(process)

        self.listener = threading.Thread(
            target=self._response_loop,
            name=f"{self.worker_kind}-process-listener",
            daemon=True,
        )
        self.listener.start()

    def _response_loop(self):
        while True:
            if self.closed and not self.pending:
                break
            try:
                message = self.response_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                break

            if message is None:
                break

            request_id = message.get("id")
            with self.pending_lock:
                future = self.pending.pop(request_id, None)
            if future is None or future.done():
                continue

            if message.get("ok"):
                future.set_result(message.get("result"))
            else:
                error = RuntimeError(
                    f"{self.worker_kind} 子进程执行失败: {message.get('error')}\n{message.get('traceback', '')}"
                )
                future.set_exception(error)

    def submit(self, payload: Dict[str, Any]) -> Future:
        if self.closed:
            raise RuntimeError(f"{self.worker_kind} 进程池已关闭")
        future: Future = Future()
        request_id = uuid.uuid4().hex
        with self.pending_lock:
            self.pending[request_id] = future
        try:
            self.request_queue.put({"id": request_id, "payload": payload})
        except Exception:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise
        return future

    def close(self):
        if self.closed:
            return
        self.closed = True

        for _ in self.workers:
            try:
                self.request_queue.put(None)
            except Exception:
                break

        for process in self.workers:
            process.join(timeout=5.0)
            if process.is_alive():
                logger.warning(f"[本地进程池] 强制终止子进程: {process.name}")
                process.terminate()
                process.join(timeout=2.0)

        try:
            self.response_queue.put(None)
        except Exception:
            pass
        self.listener.join(timeout=1.0)

        with self.pending_lock:
            pending_items = list(self.pending.values())
            self.pending.clear()
        for future in pending_items:
            if not future.done():
                future.set_exception(RuntimeError(f"{self.worker_kind} 进程池已关闭"))

        for queue_obj in (self.request_queue, self.response_queue):
            try:
                queue_obj.close()
            except Exception:
                pass
            try:
                queue_obj.join_thread()
            except Exception:
                pass


def get_or_create_process_pool(pool_key: tuple, worker_kind: str, worker_params: Dict[str, Any], concurrency: int) -> LocalProcessPool:
    with _process_pool_lock:
        pool = _process_pool_cache.get(pool_key)
        if pool is None:
            pool = LocalProcessPool(worker_kind, worker_params, concurrency)
            _process_pool_cache[pool_key] = pool
        return pool


def shutdown_process_pools(prefix: str | None = None):
    with _process_pool_lock:
        if prefix is None:
            keys = list(_process_pool_cache.keys())
        else:
            keys = [key for key in list(_process_pool_cache.keys()) if str(key[0]) == prefix]
        pools = [(key, _process_pool_cache.pop(key, None)) for key in keys]

    for _, pool in pools:
        if pool is not None:
            pool.close()


atexit.register(shutdown_process_pools)
