"""跨子模块共享的小工具：性能计时累加与 LRU 缓存原语、缓存容量常量。"""
from time import perf_counter
from typing import Optional

def _profile_add(profile_stats: Optional[dict], key: str, start_time: Optional[float]) -> None:
    if profile_stats is not None and start_time is not None:
        profile_stats[key] = profile_stats.get(key, 0.0) + (perf_counter() - start_time) * 1000.0

_QT_FONT_PROBE_SIZE = 32.0
_RAW_FONT_CACHE_MAX = 128
_QFONT_CACHE_MAX = 192
_GLYPH_SPEC_CACHE_MAX = 4096
_GLYPH_RASTER_CACHE_MAX = 2048
_VERTICAL_CACHE_MAX = 2048


def _cache_get(cache: dict, key):
    if key not in cache:
        return None
    value = cache.pop(key)
    cache[key] = value
    return value


def _cache_put(cache: dict, key, value, max_entries: int):
    if key in cache:
        cache.pop(key)
    cache[key] = value
    while len(cache) > max_entries:
        cache.popitem(last=False)
    return value
