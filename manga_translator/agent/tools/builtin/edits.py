"""Small model-facing editing interfaces over the shared transaction executor."""

from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from ...domain.tool_models import (
    GeometryPatch, PageById, RegionEdit, RegionStylePatch, RichEdit,
    SetGeometry, SetRegionStyle, SetTranslation, ToolContext,
)
from .page import apply_edits
from .shared import CommandId


PublicPageId = Annotated[int, Field(strict=True, gt=0)]


async def edit_regions(
    ctx: RunContext[ToolContext],
    page_id: PublicPageId,
    edits: Annotated[list[RegionEdit], Field(min_length=1, max_length=100)],
    command_id: CommandId,
) -> ToolReturn | dict:
    """批量修改区域字号、样式、位置或译文，返回当前属性和新图。

    page_id 使用页面公开整数 id；每项直接填写 region_id 和要改的字段，省略未改字段。
    center 使用页面像素，angle 使用角度；竖排 alignment=left 表示列顶对齐。
    同一修改重试沿用 command_id；富文本局部样式先 read_skill("rich-text")。
    """
    operations = []
    for edit in edits:
        patch = edit.model_dump(exclude_unset=True)
        style = {key: value for key, value in patch.items() if key in RegionStylePatch.model_fields}
        geometry = {key: value for key, value in patch.items() if key in GeometryPatch.model_fields}
        if style:
            operations.append(SetRegionStyle(region_id=edit.region_id, style=RegionStylePatch(**style)))
        if geometry:
            operations.append(SetGeometry(region_id=edit.region_id, geometry=GeometryPatch(**geometry)))
        if "translation" in patch:
            operations.append(SetTranslation(region_id=edit.region_id, text=patch["translation"]))
    # At most 300 internal operations, committed together under the core's 1000-operation limit.
    return await apply_edits(ctx, PageById(id=page_id), operations, command_id)


async def edit_rich_text(
    ctx: RunContext[ToolContext],
    page_id: PublicPageId,
    edits: Annotated[list[RichEdit], Field(min_length=1, max_length=100)],
    command_id: CommandId,
) -> ToolReturn | dict:
    """替换区域富文本文档或修改有效 occurrence_id 的局部样式，返回当前属性和新图。

    遵循 rich-text skill；保留未改正文和样式。同一修改重试沿用 command_id。
    """
    return await apply_edits(ctx, PageById(id=page_id), edits, command_id)
