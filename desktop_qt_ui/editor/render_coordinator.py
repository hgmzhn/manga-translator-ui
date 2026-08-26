from __future__ import annotations

from typing import Any, Optional


class RenderCoordinator:
    """集中管理视图派生出来的渲染状态。"""

    def __init__(self):
        self._document_revision: Optional[int] = None
        self.reset()

    def reset(self) -> None:
        self.text_blocks: list[Any] = []
        self.dst_points: list[Any] = []
        self.render_snapshots: list[Any] = []

    def invalidate_document(self, revision: Optional[int] = None) -> None:
        self._document_revision = revision
        self.reset()

    def sync_document_revision(self, revision: Optional[int]) -> None:
        if revision != self._document_revision:
            self.invalidate_document(revision)

    def clear_render_snapshots(self) -> None:
        self.render_snapshots = []

    def ensure_region_capacity(self, index: int) -> None:
        missing = int(index) + 1
        for cache in (self.text_blocks, self.dst_points, self.render_snapshots):
            cache.extend([None] * (missing - len(cache)))

    def insert_region(self, index: int) -> None:
        index = max(0, min(int(index), len(self.text_blocks)))
        for cache in (self.text_blocks, self.dst_points, self.render_snapshots):
            cache.insert(index, None)

    def remove_region(self, index: int) -> None:
        index = int(index)
        for cache in (self.text_blocks, self.dst_points, self.render_snapshots):
            if 0 <= index < len(cache):
                cache.pop(index)

    def trim_regions(self, count: int) -> None:
        for cache in (self.text_blocks, self.dst_points, self.render_snapshots):
            del cache[count:]
