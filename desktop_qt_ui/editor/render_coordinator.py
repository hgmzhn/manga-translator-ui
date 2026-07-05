from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional


class RenderCoordinator:
    """集中管理视图派生出来的渲染缓存。"""

    def __init__(self, text_render_cache_limit: int = 64):
        self._document_revision: Optional[int] = None
        self._text_render_cache_limit = max(8, int(text_render_cache_limit))
        self.reset()

    def reset(self) -> None:
        self.text_render_cache: OrderedDict[Any, Any] = OrderedDict()
        self.text_blocks: list[Any] = []
        self.dst_points: list[Any] = []
        self.render_snapshots: list[Any] = []

    def invalidate_document(self, revision: Optional[int] = None) -> None:
        self._document_revision = revision
        self.reset()

    def sync_document_revision(self, revision: Optional[int]) -> None:
        if revision != self._document_revision:
            self.invalidate_document(revision)

    def clear_text_render_cache(self) -> None:
        self.text_render_cache.clear()

    def get_text_render(self, key: Any) -> Any:
        if key is None:
            return None
        value = self.text_render_cache.get(key)
        if value is not None:
            self.text_render_cache.move_to_end(key)
        return value

    def store_text_render(self, key: Any, value: Any) -> None:
        if key is None or value is None:
            return
        self.text_render_cache[key] = value
        self.text_render_cache.move_to_end(key)
        while len(self.text_render_cache) > self._text_render_cache_limit:
            self.text_render_cache.popitem(last=False)

    def clear_render_snapshots(self) -> None:
        self.render_snapshots = []

    def ensure_region_capacity(self, index: int) -> None:
        while len(self.text_blocks) <= index:
            self.text_blocks.append(None)
        while len(self.dst_points) <= index:
            self.dst_points.append(None)
        while len(self.render_snapshots) <= index:
            self.render_snapshots.append(None)

    def insert_region(self, index: int) -> None:
        index = max(0, min(int(index), len(self.text_blocks)))
        self.text_blocks.insert(index, None)
        self.dst_points.insert(index, None)
        self.render_snapshots.insert(index, None)

    def remove_region(self, index: int) -> None:
        index = int(index)
        if 0 <= index < len(self.text_blocks):
            self.text_blocks.pop(index)
        if 0 <= index < len(self.dst_points):
            self.dst_points.pop(index)
        if 0 <= index < len(self.render_snapshots):
            self.render_snapshots.pop(index)

    def trim_regions(self, count: int) -> None:
        del self.text_blocks[count:]
        del self.dst_points[count:]
        del self.render_snapshots[count:]
