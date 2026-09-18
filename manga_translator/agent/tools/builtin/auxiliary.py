"""Font discovery, layout measurement, and optional checking tools."""

from __future__ import annotations

import json
import re
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from ...domain.tool_models import PageRef, ToolContext, ToolError
from .shared import (
    Limit,
    Text,
    _comparison,
    _guard,
    _image_return,
    _remember,
    _service,
)


@_guard
async def list_fonts(
    ctx: RunContext[ToolContext],
    query: str = "",
    characters: str = "",
    sample_text: Annotated[str, Field(max_length=200)] | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> ToolReturn | dict:
    """列出真实可用字体，可按字符覆盖筛选并返回真实样张。"""
    return _image_return(
        ctx,
        await _service(ctx, "renderer").fonts(
            query, characters, sample_text, limit, cursor
        )
    )


@_guard
async def measure_layout(
    ctx: RunContext[ToolContext],
    page: PageRef,
    region_ids: list[str] | None = None,
) -> dict:
    """用实际渲染引擎测量当前页面；几何结果不等于已通过视觉审查。"""
    page_id = ctx.deps.workspace.resolve_page(ctx.deps, page)
    snapshot = ctx.deps.workspace.page(ctx.deps, page_id)
    result = await _service(ctx, "renderer").measure(snapshot, region_ids)
    ctx.deps.workspace.page(ctx.deps, page_id, snapshot["revision"])
    _remember(ctx, snapshot, region_ids)
    return result


@_guard
async def fit_text(
    ctx: RunContext[ToolContext],
    page: PageRef,
    region_id: str,
    min_size: Annotated[int, Field(ge=1, le=8192)] = 1,
    max_size: Annotated[int, Field(ge=1, le=8192)] = 300,
) -> dict:
    """测量当前区域并建议字号，不自动修改；应用建议后仍需观察真实图片。"""
    page_id = ctx.deps.workspace.resolve_page(ctx.deps, page)
    snapshot = ctx.deps.workspace.page(ctx.deps, page_id)
    result = await _service(ctx, "renderer").fit(
        snapshot, region_id, min_size, max_size
    )
    ctx.deps.workspace.page(ctx.deps, page_id, snapshot["revision"])
    _remember(ctx, snapshot, [region_id])
    return result


@_guard
async def check_font_coverage(
    ctx: RunContext[ToolContext],
    font_family: str,
    text: Annotated[str, Field(max_length=4000)],
) -> dict:
    """通过真实字体 cmap/字形支持检测缺字，不以系统回退字体冒充指定字体支持。"""
    return await _service(ctx, "renderer").coverage(font_family, text)


@_guard
async def check_text_changes(
    ctx: RunContext[ToolContext],
    transaction_id: str,
    terms: Annotated[list[Text], Field(max_length=100)] | None = None,
) -> dict:
    """对照本任务事务提交前后检查译文、数字与指定术语计数；不证明完整语义等价。"""
    terms = terms or []
    old, new = _comparison(ctx, transaction_id)
    from ...workspace.text import text_of

    previous = {r["region_id"]: text_of(r, "translation") for r in old["regions"]}
    changes = []
    for region in new["regions"]:
        a, b = previous.get(region["region_id"], ""), text_of(region, "translation")
        if a != b:
            changes.append(
                {
                    "region_id": region["region_id"],
                    "before": a,
                    "after": b,
                    "numbers_before": re.findall(r"\d+(?:[.,]\d+)*", a),
                    "numbers_after": re.findall(r"\d+(?:[.,]\d+)*", b),
                    "term_changes": [
                        {"term": term, "before": a.count(term), "after": b.count(term)}
                        for term in terms
                        if a.count(term) != b.count(term)
                    ],
                }
            )
    result = {
        "page_id": new["page_id"],
        "transaction_id": transaction_id,
        "changes": changes,
        "semantic_equivalence": "not_checked",
    }
    if len(json.dumps(result, ensure_ascii=False).encode()) > 256_000:
        raise ToolError("output_too_large", "差异过大，请缩小页面正文后分步复核")
    _remember(ctx, new)
    return result


