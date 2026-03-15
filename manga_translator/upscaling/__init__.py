import asyncio
import threading
from dataclasses import dataclass
from typing import List
from PIL import Image

from .common import CommonUpscaler, OfflineUpscaler
from .waifu2x import Waifu2xUpscaler
from .esrgan import ESRGANUpscaler
from .esrgan_pytorch import ESRGANUpscalerPytorch
from .realcugan import RealCUGANUpscaler
from .mangajanai import MangaJaNaiUpscaler
from ..config import Upscaler
from ..utils.local_process_pool import get_or_create_process_pool, shutdown_process_pools

UPSCALERS = {
    Upscaler.waifu2x: Waifu2xUpscaler,
    Upscaler.esrgan: ESRGANUpscaler,
    Upscaler.upscler4xultrasharp: ESRGANUpscalerPytorch,
    Upscaler.realcugan: RealCUGANUpscaler,
    Upscaler.mangajanai: MangaJaNaiUpscaler,
}
upscaler_cache = {}
_upscaler_pool_cache = {}
_upscaler_pool_lock = threading.Lock()
_upscaler_single_instance_locks = {}


@dataclass
class _UpscalerPoolState:
    queue: asyncio.Queue
    instances: List[CommonUpscaler | None]


def _build_upscaler_cache_key(key: Upscaler, **kwargs) -> str:
    cache_key_parts = [str(key)]
    if key == Upscaler.realcugan and 'model_name' in kwargs:
        cache_key_parts.append(kwargs['model_name'])
    if key == Upscaler.mangajanai and 'model_name' in kwargs:
        cache_key_parts.append(kwargs['model_name'])
    if 'tile_size' in kwargs:
        cache_key_parts.append(f"tile{kwargs['tile_size']}")
    return '_'.join(cache_key_parts)


def _instantiate_upscaler(key: Upscaler, *args, **kwargs) -> CommonUpscaler:
    upscaler = UPSCALERS[key]
    return upscaler(*args, **kwargs)


def _enum_value(key: Upscaler) -> str:
    return key.value if hasattr(key, "value") else str(key)


def _resolve_local_upscaling_concurrency(config) -> int:
    upscale_config = getattr(config, 'upscale', None) if config is not None else None
    try:
        return max(int(getattr(upscale_config, 'local_upscaling_concurrency', 1) or 1), 1)
    except (TypeError, ValueError):
        return 1


def _get_or_create_upscaler_pool_state(pool_key: tuple, concurrency: int) -> _UpscalerPoolState:
    with _upscaler_pool_lock:
        state = _upscaler_pool_cache.get(pool_key)
        if state is None:
            queue = asyncio.Queue()
            for index in range(concurrency):
                queue.put_nowait(index)
            state = _UpscalerPoolState(queue=queue, instances=[None] * concurrency)
            _upscaler_pool_cache[pool_key] = state
        return state


def _get_single_instance_lock(key: str) -> threading.Lock:
    with _upscaler_pool_lock:
        lock = _upscaler_single_instance_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _upscaler_single_instance_locks[key] = lock
        return lock

def get_upscaler(key: Upscaler, *args, **kwargs) -> CommonUpscaler:
    if key not in UPSCALERS:
        raise ValueError(f'Could not find upscaler for: "{key}". Choose from the following: %s' % ','.join(UPSCALERS))
    
    cache_key = _build_upscaler_cache_key(key, **kwargs)
    
    if cache_key not in upscaler_cache:
        upscaler_cache[cache_key] = _instantiate_upscaler(key, *args, **kwargs)
    return upscaler_cache[cache_key]

async def prepare(upscaler_key: Upscaler, **kwargs):
    upscaler = get_upscaler(upscaler_key, **kwargs)
    if isinstance(upscaler, OfflineUpscaler):
        await upscaler.download()

async def dispatch(upscaler_key: Upscaler, image_batch: List[Image.Image], upscale_ratio: int, device: str = 'cpu', **kwargs) -> List[Image.Image]:
    if upscale_ratio == 1:
        return image_batch
    runtime_config = kwargs.pop('config', None)
    local_concurrency = _resolve_local_upscaling_concurrency(runtime_config)
    upscaler_key_value = _enum_value(upscaler_key)
    cache_key = _build_upscaler_cache_key(upscaler_key, **kwargs)

    async def _run_upscale_with_instance(upscaler: CommonUpscaler) -> List[Image.Image]:
        if isinstance(upscaler, OfflineUpscaler):
            await upscaler.load(device)
        return await upscaler.upscale(image_batch, upscale_ratio)

    if local_concurrency <= 1:
        lock = _get_single_instance_lock(f"{cache_key}::{device}")
        with lock:
            return await _run_upscale_with_instance(get_upscaler(upscaler_key, **kwargs))

    pool_key = ("upscaling", cache_key, device, local_concurrency)
    process_pool = get_or_create_process_pool(
        pool_key,
        worker_kind="upscaling",
        worker_params={
            "key": upscaler_key_value,
            "device": device,
            "init_kwargs": dict(kwargs),
        },
        concurrency=local_concurrency,
    )
    future = process_pool.submit(
        {
            "image_batch": image_batch,
            "upscale_ratio": upscale_ratio,
        }
    )
    return await asyncio.wrap_future(future)

async def unload(upscaler_key: Upscaler, **kwargs):
    """卸载超分模型并清理显存"""
    upscaler_key_value = _enum_value(upscaler_key)
    cache_key = _build_upscaler_cache_key(upscaler_key, **kwargs)
    
    if cache_key in upscaler_cache:
        upscaler = upscaler_cache.pop(cache_key)
        if isinstance(upscaler, OfflineUpscaler):
            await upscaler.unload()

    pool_keys = [key for key in list(_upscaler_pool_cache.keys()) if key[0] == cache_key or key[0].startswith(f"{upscaler_key_value}_")]
    for pool_key in pool_keys:
        state = _upscaler_pool_cache.pop(pool_key, None)
        if not state:
            continue
        for instance in state.instances:
            if isinstance(instance, OfflineUpscaler):
                await instance.unload()
    with _upscaler_pool_lock:
        stale_lock_keys = [key for key in list(_upscaler_single_instance_locks.keys()) if key.startswith(f"{cache_key}::")]
        for lock_key in stale_lock_keys:
            _upscaler_single_instance_locks.pop(lock_key, None)
    shutdown_process_pools(prefix="upscaling")

    # 统一的显存清理（适用于所有超分模型）
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, 'ipc_collect'):
                torch.cuda.ipc_collect()
            torch.cuda.synchronize()
    except Exception:
        pass
