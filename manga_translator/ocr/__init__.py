import asyncio
import threading
from dataclasses import dataclass
import numpy as np
from typing import Any, List, Optional
from .common import CommonOCR, OfflineOCR
from .model_32px import Model32pxOCR
from .model_48px import Model48pxOCR
from .model_48px_ctc import Model48pxCTCOCR
# ModelMangaOCR 延迟导入，避免未使用时下载模型
from .model_paddleocr import ModelPaddleOCR, ModelPaddleOCRKorean, ModelPaddleOCRLatin, ModelPaddleOCRThai
from ..config import Ocr, OcrConfig
from ..utils import Quadrilateral
from ..utils.local_process_pool import get_or_create_process_pool, shutdown_process_pools


def _get_manga_ocr_class():
    """延迟导入 ModelMangaOCR，只有在真正使用 mocr 时才导入"""
    from .model_manga_ocr import ModelMangaOCR
    return ModelMangaOCR


def _get_paddleocr_vl_class():
    """延迟导入 ModelPaddleOCRVL，只有在真正使用 paddleocr_vl 时才导入"""
    from .model_paddleocr_vl import ModelPaddleOCRVL
    return ModelPaddleOCRVL


def _get_openai_ocr_class():
    """延迟导入 ModelOpenAIOCR，只有在真正使用 openai_ocr 时才导入"""
    from .model_api_ocr import ModelOpenAIOCR
    return ModelOpenAIOCR


def _get_gemini_ocr_class():
    """延迟导入 ModelGeminiOCR，只有在真正使用 gemini_ocr 时才导入"""
    from .model_api_ocr import ModelGeminiOCR
    return ModelGeminiOCR


OCRS = {
    Ocr.ocr32px: Model32pxOCR,
    Ocr.ocr48px: Model48pxOCR,
    Ocr.ocr48px_ctc: Model48pxCTCOCR,
    Ocr.mocr: _get_manga_ocr_class,  # 延迟导入
    Ocr.paddleocr: ModelPaddleOCR,
    Ocr.paddleocr_korean: ModelPaddleOCRKorean,
    Ocr.paddleocr_latin: ModelPaddleOCRLatin,
    Ocr.paddleocr_thai: ModelPaddleOCRThai,
    Ocr.paddleocr_vl: _get_paddleocr_vl_class,  # 延迟导入 PaddleOCR-VL
    Ocr.openai_ocr: _get_openai_ocr_class,
    Ocr.gemini_ocr: _get_gemini_ocr_class,
}
ocr_cache = {}
_ocr_pool_cache = {}
_ocr_pool_lock = threading.Lock()
_ocr_single_instance_locks = {}


@dataclass
class _OcrPoolState:
    queue: asyncio.Queue
    instances: List[Optional[CommonOCR]]


def _resolve_ocr_config(config: Optional[Any]) -> Any:
    if config is None:
        return OcrConfig()
    if isinstance(config, OcrConfig):
        return config
    nested_ocr_config = getattr(config, 'ocr', None)
    if isinstance(nested_ocr_config, OcrConfig):
        return nested_ocr_config
    if nested_ocr_config is not None and hasattr(nested_ocr_config, 'ignore_bubble'):
        return nested_ocr_config
    if isinstance(config, Ocr):
        return OcrConfig(ocr=config)
    return config


def _instantiate_ocr(key: Ocr, *args, **kwargs) -> CommonOCR:
    ocr_class = OCRS[key]
    if not isinstance(ocr_class, type):
        ocr_class = ocr_class()
    return ocr_class(*args, **kwargs)


def _enum_value(key: Ocr) -> str:
    return key.value if hasattr(key, "value") else str(key)


def _resolve_local_ocr_concurrency(config: Optional[Any], ocr_key: Optional[Ocr] = None) -> int:
    ocr_config = _resolve_ocr_config(config)
    try:
        concurrency = max(int(getattr(ocr_config, 'local_ocr_concurrency', 1) or 1), 1)
    except (TypeError, ValueError):
        concurrency = 1
    return concurrency


def _get_or_create_ocr_pool_state(pool_key: tuple, concurrency: int) -> _OcrPoolState:
    with _ocr_pool_lock:
        state = _ocr_pool_cache.get(pool_key)
        if state is None:
            queue = asyncio.Queue()
            for index in range(concurrency):
                queue.put_nowait(index)
            state = _OcrPoolState(queue=queue, instances=[None] * concurrency)
            _ocr_pool_cache[pool_key] = state
        return state


def _get_single_instance_lock(key: str) -> threading.Lock:
    with _ocr_pool_lock:
        lock = _ocr_single_instance_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _ocr_single_instance_locks[key] = lock
        return lock

def get_ocr(key: Ocr, *args, **kwargs) -> CommonOCR:
    if key not in OCRS:
        raise ValueError(f'Could not find OCR for: "{key}". Choose from the following: %s' % ','.join(OCRS))
    # Use cache to avoid reloading models in the same translation session
    if key not in ocr_cache:
        ocr_cache[key] = _instantiate_ocr(key, *args, **kwargs)
    return ocr_cache[key]

async def prepare(ocr_key: Ocr, device: str = 'cpu'):
    ocr = get_ocr(ocr_key)
    if isinstance(ocr, OfflineOCR):
        await ocr.download()
        await ocr.load(device)

async def dispatch(
    ocr_key: Ocr,
    image: np.ndarray,
    regions: List[Quadrilateral],
    config: Optional[OcrConfig] = None,
    device: str = 'cpu',
    verbose: bool = False,
    runtime_config=None,
) -> List[Quadrilateral]:
    runtime_config = runtime_config or config
    ocr_config = _resolve_ocr_config(config)

    # API OCR 已经有单独的并发控制，这里只为本地 OCR 准备多实例池。
    if ocr_key in (Ocr.openai_ocr, Ocr.gemini_ocr):
        ocr = get_ocr(ocr_key)
        if isinstance(ocr, OfflineOCR):
            await ocr.load(device)
        if getattr(ocr, "SUPPORTS_RUNTIME_CONFIG", False):
            return await ocr.recognize(
                image,
                regions,
                ocr_config,
                verbose,
                runtime_config=runtime_config,
            )
        return await ocr.recognize(image, regions, ocr_config, verbose)

    local_concurrency = _resolve_local_ocr_concurrency(ocr_config, ocr_key)
    if local_concurrency <= 1:
        ocr = get_ocr(ocr_key)
        ocr_key_value = _enum_value(ocr_key)
        lock = _get_single_instance_lock(f"{ocr_key_value}::{device}")
        with lock:
            if isinstance(ocr, OfflineOCR):
                await ocr.load(device)
            return await ocr.recognize(image, regions, ocr_config, verbose)

    ocr_key_value = _enum_value(ocr_key)
    pool_key = ("ocr", ocr_key_value, device, local_concurrency)
    process_pool = get_or_create_process_pool(
        pool_key,
        worker_kind="ocr",
        worker_params={
            "key": ocr_key_value,
            "device": device,
        },
        concurrency=local_concurrency,
    )
    future = process_pool.submit(
        {
            "image": image,
            "regions": regions,
            "config": ocr_config,
            "verbose": verbose,
        }
    )
    return await asyncio.wrap_future(future)

async def unload(ocr_key: Ocr):
    ocr_key_value = _enum_value(ocr_key)
    ocr = ocr_cache.pop(ocr_key, None)
    if isinstance(ocr, OfflineOCR):
        await ocr.unload()
    pool_keys = [key for key in list(_ocr_pool_cache.keys()) if key[0] == ocr_key_value]
    for pool_key in pool_keys:
        state = _ocr_pool_cache.pop(pool_key, None)
        if not state:
            continue
        for instance in state.instances:
            if isinstance(instance, OfflineOCR):
                await instance.unload()
    with _ocr_pool_lock:
        stale_lock_keys = [key for key in list(_ocr_single_instance_locks.keys()) if key.startswith(f"{ocr_key_value}::")]
        for lock_key in stale_lock_keys:
            _ocr_single_instance_locks.pop(lock_key, None)
    shutdown_process_pools(prefix="ocr")
