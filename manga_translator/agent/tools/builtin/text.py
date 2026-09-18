"""Reference page lookup and scoped text search and replacement tools."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import RunContext

from ...domain.tool_models import PageRef, ToolContext, ToolScope
from .shared import (
    CommandId,
    Limit,
    Text,
    _PAGE_FIELDS,
    _command,
    _guard,
    _remember,
    _transactions,
    _window,
)


@_guard
async def find_reference_pages(
    ctx: RunContext[ToolContext],
    scope: ToolScope,
    anchor: PageRef,
    relation: Literal["previous", "next", "all"] = "previous",
    query: Annotated[str, Field(max_length=4000)] = "",
    cursor: str | None = None,
    limit: Limit = 50,
) -> dict:
    """依全局自然顺序检索已加载参考页，可跨作品；仅检索正文，不根据文件名推断剧情。"""
    from ...workspace.text import text_of

    workspace = ctx.deps.workspace
    anchor_page_id = workspace.resolve_page(ctx.deps, anchor)
    anchor_id = workspace.public_identity(anchor_page_id)["id"]
    pages = workspace.pages(ctx.deps, workspace.resolve_scope(ctx.deps, scope))
    items = []
    for page in pages:
        if page["page_id"] == anchor_page_id:
            continue
        page_number = workspace.public_identity(page["page_id"])["id"]
        if relation == "previous" and page_number >= anchor_id:
            continue
        if relation == "next" and page_number <= anchor_id:
            continue
        hits = []
        for region in page["regions"]:
            for field in ("source", "translation"):
                value = text_of(region, field) or ""
                offset = value.find(query) if query else -1
                if offset >= 0:
                    hits.append(
                        {
                            "region_id": region["region_id"],
                            "field": field,
                            "context": value[
                                max(0, offset - 40) : offset + len(query) + 40
                            ],
                        }
                    )
        if query and not hits:
            continue
        items.append(
            {
                key: page[key]
                for key in _PAGE_FIELDS if key in page
            }
            | {"hits": hits[:20], "hit_count": len(hits)}
        )
    result = _window(items, cursor, limit)
    by_id = {page["page_id"]: page for page in pages}
    for item in result["items"]:
        _remember(ctx, by_id[item["page_id"]], [hit["region_id"] for hit in item["hits"]])
    return result


@_guard
async def find_text(
    ctx: RunContext[ToolContext],
    scope: ToolScope,
    query: Text,
    field: Literal["source", "translation"] = "translation",
    mode: Literal["literal", "regex"] = "literal",
    cursor: str | None = None,
    limit: Limit = 50,
) -> dict:
    """搜索已加载页面正文，返回 occurrence ID 和 Unicode 文本半开区间，支持跨富文本 run。"""
    workspace = ctx.deps.workspace
    result = workspace.find_text(
        ctx.deps, workspace.resolve_scope(ctx.deps, scope), query, field, mode, cursor, limit
    )
    selected = {}
    for match in result["matches"]:
        key = (match["page_id"], match["revision"])
        selected.setdefault(key, set()).add(match["region_id"])
    for key, region_ids in selected.items():
        _remember(ctx, workspace.page(ctx.deps, *key), region_ids)
    return result


@_guard
async def replace_text(
    ctx: RunContext[ToolContext],
    scope: ToolScope,
    field: Literal["translation"],
    old: Text,
    new: Annotated[str, Field(max_length=16000)],
    command_id: CommandId,
    mode: Literal["literal", "regex"] = "literal",
) -> dict:
    """直接替换授权范围译文，无需先搜索；逐页原子提交，宿主捕获当前基准并防止重试重复替换。"""
    workspace = ctx.deps.workspace
    resolved = workspace.resolve_scope(ctx.deps, scope)
    expected = _command(ctx, command_id, {
        "op": "replace_text", "scope": resolved.model_dump(),
        "field": field, "old": old, "new": new, "mode": mode,
    }, lambda: {page["page_id"]: page["revision"] for page in workspace.pages(ctx.deps, resolved)})
    return _transactions(ctx, workspace.replace_text(
        ctx.deps, resolved, field, old, new, mode, command_id, expected
    ))


