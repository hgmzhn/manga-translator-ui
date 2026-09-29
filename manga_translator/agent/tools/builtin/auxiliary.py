"""Font discovery for the page toolset."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from ...domain.tool_models import ToolContext
from .shared import (
    Limit,
    _guard,
    _image_return,
    _service,
)


@_guard
async def list_fonts(
    ctx: RunContext[ToolContext],
    query: str = "",
    characters: str = "",
    cursor: str | None = None,
    limit: Limit = 50,
) -> dict:
    """列出真实可用字体和样式，不生成样张。"""
    return await _service(ctx, "renderer").fonts(query, characters, limit, cursor)


@_guard
async def preview_font(
    ctx: RunContext[ToolContext],
    font_family: Annotated[str, Field(min_length=1, max_length=256)],
    style: Annotated[str, Field(min_length=1, max_length=128)],
    sample_text: Annotated[str, Field(min_length=1, max_length=200)],
) -> ToolReturn | dict:
    """按指定字体族和样式生成单独样张。"""
    return await _image_return(
        ctx,
        await _service(ctx, "renderer").font_sample(font_family, style, sample_text)
    )
