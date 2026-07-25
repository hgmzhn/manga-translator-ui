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
