"""Small model-facing editing interfaces over the shared transaction executor."""

from copy import deepcopy
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from ...domain.tool_models import (
    CreateRegion, DeleteRegion, GeometryPatch, NewRegion, PageById, RegionEdit,
    RegionStylePatch, RichEdit, SetGeometry, SetRegionStyle, SetTranslation,
    ToolContext, ToolError,
)
from ...workspace.regions import create_region
from .editing import edit_feedback
from .page import apply_edits
from .shared import CommandId, _baseline, _command, _guard, _service


PublicPageId = Annotated[int, Field(strict=True, gt=0)]


@_guard
async def create_regions(
    ctx: RunContext[ToolContext],
    page_id: PublicPageId,
    regions: Annotated[list[NewRegion], Field(min_length=1, max_length=100)],
    command_id: CommandId,
) -> ToolReturn | dict:
    """批量创建文本框，按框宽高与文字自动计算字号，返回新区域 ID、当前属性和新图。

    center/width/height 使用页面像素；direction=auto 按框形状选择横竖排。
    需要整页排版、几何和译文权限；同一创建重试沿用 command_id。
    """
    workspace = ctx.deps.workspace
    pid = workspace.resolve_page(ctx.deps, PageById(id=page_id))

    def capture():
        baseline = _baseline(ctx, pid)
        return {"revision": baseline["revision"], "policy": baseline["policy_version"],
                "ids": [uuid4().hex for _ in regions]}

    expected = _command(ctx, command_id, {
        "op": "create_regions", "page_id": pid,
        "regions": [region.model_dump(mode="json") for region in regions],
    }, capture)
    if "edits" not in expected:
        # Validate scope before invoking the worker; the commit rechecks it after
        # fitting, including live parent permissions and cancellation.
        for rid in expected["ids"]:
            for capability in ("layout_pages", "geometry_pages", "translation_pages"):
                workspace._authorize(ctx.deps, capability, pid, rid)
        grant = ctx.deps.grant
        while grant is not None:
            if pid in grant.region_ids:
                raise ToolError("permission_denied", "新建文本框需要整页编辑授权")
            grant = grant.parent
        snapshot = workspace.page(ctx.deps, pid)
        if snapshot["revision"] != expected["revision"]:
            raise ToolError("revision_conflict", "页面已变化，请重新读取并使用新的 command_id")
        if len(snapshot["regions"]) + len(regions) > 5000:
            raise ToolError("resource_limit", "Page exceeds the region limit")
        edits = [CreateRegion(**region.model_dump(), region_id=rid, font_size=1)
                 for region, rid in zip(regions, expected["ids"])]
        candidate = deepcopy(snapshot)
        candidate["regions"].extend(create_region(candidate, edit) for edit in edits)
        renderer = _service(ctx, "renderer")
        for edit in edits:
            if ctx.deps.cancelled.is_set():
                raise ToolError("cancelled", "任务已取消")
            fitted = await renderer.fit(candidate, edit.region_id, 1, 8192)
            if not fitted["fits"]:
                raise ToolError("text_does_not_fit", "文本框过小，最小字号仍无法容纳文字，请扩大框",
                                {"index": expected["ids"].index(edit.region_id)})
            edit.font_size = fitted["proposed_edits"][0]["style"]["font_size"]
        # Replays retain the same fitted values even if the worker is later unavailable.
        expected["edits"] = [edit.model_dump(mode="json") for edit in edits]
        ctx.deps.command_payloads[command_id]["preconditions"]["edits"] = deepcopy(expected["edits"])
    edits = [CreateRegion.model_validate(edit) for edit in expected["edits"]]
    result = workspace.apply_edits(
        ctx.deps, pid, {rid: 0 for rid in expected["ids"]}, expected["policy"],
        edits, command_id, expected_revision=expected["revision"],
    )
    return await edit_feedback(ctx, result)


async def delete_regions(
    ctx: RunContext[ToolContext],
    page_id: PublicPageId,
    region_ids: Annotated[list[Annotated[str, Field(min_length=1)]], Field(min_length=1, max_length=100)],
    command_id: CommandId,
) -> ToolReturn | dict:
    """批量删除文本框及其译文，保留当前底图，返回当前属性和新图。

    region_ids 使用页面已有区域 ID；检查锁定及排版、几何、译文权限。
    同一删除重试沿用 command_id，事务可通过 revert_edits 回退。
    """
    return await apply_edits(ctx, PageById(id=page_id),
                             [DeleteRegion(region_id=rid) for rid in region_ids], command_id)


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
