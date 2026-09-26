"""Page reading, visual observation, and transactional editing tools."""

from __future__ import annotations

import io
from typing import Annotated, Literal

from PIL import Image, ImageDraw
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from ...domain.tool_models import Edit, PageRef, ToolContext, ToolError
from .editing import edit_feedback
from .shared import (
    CommandId,
    Crop,
    _baseline,
    _command,
    _comparison,
    _guard,
    _image_return,
    _observe,
    _public_page,
    _remember,
)


@_guard
async def read_page(
    ctx: RunContext[ToolContext],
    page: PageRef,
    region_ids: list[str] | None = None,
    fields: list[str] | None = None,
) -> dict:
    """读取页面当前草稿与选定字段；页面用全局 ID 或相对文件夹和完整文件名指定。"""
    page_id = ctx.deps.workspace.resolve_page(ctx.deps, page)
    snapshot = ctx.deps.workspace.page(ctx.deps, page_id)
    result = _public_page(snapshot, region_ids, fields)
    _remember(ctx, snapshot, region_ids)
    return result


@_guard
async def observe_canvas(
    ctx: RunContext[ToolContext],
    page: PageRef,
    view: Literal["original", "base", "rendered"] = "rendered",
    crop: Crop | None = None,
    max_dimension: Annotated[int, Field(ge=64, le=4096)] = 1600,
) -> ToolReturn | dict:
    """观察页面当前真实图片；图片通过原生多模态内容返回，不返回本地路径。"""
    page_id = ctx.deps.workspace.resolve_page(ctx.deps, page)
    snapshot = ctx.deps.workspace.page(ctx.deps, page_id)
    bounds = (crop.left, crop.top, crop.right, crop.bottom) if crop else None
    return _image_return(
        ctx, await _observe(ctx, page_id, snapshot["revision"], view, bounds, max_dimension)
    )


@_guard
async def apply_edits(
    ctx: RunContext[ToolContext],
    page: PageRef,
    edits: Annotated[list[Edit], Field(min_length=1, max_length=100)],
    command_id: CommandId,
) -> ToolReturn | dict:
    """原子编辑已读取的区域，自动返回执行状态和新图，不附加完整区域属性。

    检查冲突、锁定和权限；成功提交后追加新图，保留完整历史供对比。
    render_status=failed 只表示新图失败，已提交的修改仍生效。
    """
    page_id = ctx.deps.workspace.resolve_page(ctx.deps, page)

    def capture():
        snapshot = _baseline(ctx, page_id)
        versions = {region["region_id"]: region["version"] for region in snapshot["regions"]}
        touched = {edit.region_id for edit in edits}
        if not touched <= versions.keys():
            raise ToolError("read_required", "请先读取或观察所有待修改区域")
        return {"versions": {rid: versions[rid] for rid in touched}, "policy": snapshot["policy_version"]}

    expected = _command(ctx, command_id, {
        "op": "apply_edits", "page_id": page_id,
        "edits": [edit.model_dump(mode="json", exclude_unset=True) for edit in edits],
    }, capture)
    return await edit_feedback(ctx, ctx.deps.workspace.apply_edits(
        ctx.deps, page_id, expected["versions"], expected["policy"], edits, command_id
    ))


@_guard
async def revert_edits(
    ctx: RunContext[ToolContext],
    transaction_id: str,
    command_id: CommandId,
) -> ToolReturn | dict:
    """补偿本任务事务，自动返回执行状态和新图；保留历史图片、文字与思考供对比。

    相关区域后续已变化时拒绝，不撤销其他任务成果。
    """
    def capture():
        transaction = ctx.deps.transaction_results.get(transaction_id)
        if transaction is None:
            raise ToolError("transaction_not_owned", "只能撤销本任务已提交的事务")
        return transaction["region_versions"]

    expected = _command(ctx, command_id, {
        "op": "revert_edits", "transaction_id": transaction_id,
    }, capture)
    return await edit_feedback(ctx, ctx.deps.workspace.revert_edits(
        ctx.deps, transaction_id, expected, command_id
    ))


@_guard
async def compare_revisions(
    ctx: RunContext[ToolContext],
    transaction_id: str,
    region_ids: list[str] | None = None,
) -> ToolReturn | dict:
    """比较本任务事务提交前后的真实渲染图及字段差异；不改变草稿。"""
    before_snapshot, after_snapshot = _comparison(ctx, transaction_id)
    page_id = after_snapshot["page_id"]
    before, after = before_snapshot["revision"], after_snapshot["revision"]
    old_ids = {r["region_id"] for r in before_snapshot["regions"]}
    new_ids = {r["region_id"] for r in after_snapshot["regions"]}
    selected = old_ids | new_ids if region_ids is None else set(region_ids)
    if not selected <= old_ids | new_ids:
        raise ToolError("not_found", "事务前后均不包含指定区域")
    old = _public_page(before_snapshot, list(selected & old_ids))
    new = _public_page(after_snapshot, list(selected & new_ids))
    old_regions = {r["region_id"]: r for r in old["regions"]}
    new_regions = {r["region_id"]: r for r in new["regions"]}
    diffs = []
    for rid in dict.fromkeys([*old_regions, *new_regions]):
        prior, region = old_regions.get(rid, {}), new_regions.get(rid, {})
        changes = {
            key: {"before": prior.get(key), "after": region.get(key)}
            for key in prior.keys() | region.keys()
            if prior.get(key) != region.get(key)
        }
        if changes:
            diffs.append({"region_id": rid, "changes": changes})
    images = []
    for revision in (before, after):
        payload = await _observe(ctx, page_id, revision, max_dimension=1200)
        with Image.open(io.BytesIO(payload["image"])) as image:
            images.append(image.convert("RGB"))
    output = Image.new(
        "RGB",
        (sum(i.width for i in images), max(i.height for i in images) + 32),
        "white",
    )
    left = 0
    for label, image in zip(("before", "after"), images):
        output.paste(image, (left, 32))
        ImageDraw.Draw(output).text((left + 8, 8), label, fill="black")
        left += image.width
    buffer = io.BytesIO()
    output.save(buffer, format="PNG")
    return _image_return(
        ctx,
        {
            "image": buffer.getvalue(),
            "mime_type": "image/png",
            "page_id": page_id,
            "transaction_id": transaction_id,
            "snapshots": ["before", "after"],
            "differences": diffs,
        }
    )
