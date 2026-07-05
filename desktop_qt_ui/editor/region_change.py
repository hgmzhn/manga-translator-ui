from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

RegionChangeKind = Literal["reset", "updated", "inserted", "removed"]


@dataclass(frozen=True)
class RegionChange:
    """region 数据的一次变更通知：kind 决定视图的最小刷新方式。

    - updated:  indices 中各 region 的内容变化，item 就地刷新
    - inserted: indices 位置插入了新 region
    - removed:  indices 位置的 region 已删除
    - reset:    文档级变化（换图/清空/导入/全局渲染参数），全量重建
    """

    kind: RegionChangeKind
    indices: tuple[int, ...] = ()
    fields: tuple[str, ...] = ()
    source: str = ""

    @classmethod
    def reset(cls, *, source: str = "") -> "RegionChange":
        return cls("reset", source=source)

    @classmethod
    def updated(
        cls,
        indices: Iterable[int],
        *,
        fields: Iterable[str] | None = None,
        source: str = "",
    ) -> "RegionChange":
        return cls("updated", _normalize_indices(indices), _normalize_fields(fields), source)

    @classmethod
    def inserted(cls, indices: Iterable[int], *, source: str = "") -> "RegionChange":
        return cls("inserted", _normalize_indices(indices), source=source)

    @classmethod
    def removed(cls, indices: Iterable[int], *, source: str = "") -> "RegionChange":
        return cls("removed", _normalize_indices(indices), source=source)


def _normalize_indices(indices: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted({int(index) for index in indices}))


def _normalize_fields(fields: Iterable[str] | None) -> tuple[str, ...]:
    if fields is None:
        return ()
    return tuple(sorted({str(field) for field in fields if field is not None}))
