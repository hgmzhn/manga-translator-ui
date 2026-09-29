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
    Pages,
    _baseline,
    _command,
    _comparison,
    _guard,
    _image_bundle_return,
    _image_return,
    _observe,
    _public_page,
    _remember,
)


ReadField = Literal[
    "text", "texts", "translation", "translation_rich",
    "center", "angle", "locked", "is_locked", "font_size", "font_family",
    "bold", "italic", "direction", "alignment", "line_spacing", "letter_spacing",
    "stroke_width", "stroke_color", "disable_font_border", "font_color",
    "fg_colors", "bg_colors", "opacity", "text_offset", "language", "source_lang",
    "target_lang", "prob", "layout_mode", "adjust_bg_color", "shadow_radius",
    "shadow_strength", "shadow_color", "shadow_offset", "ocr_status",
    "image_original", "image_base", "image_rendered",
]

_IMAGE_FIELDS = {
    "image_original": "original",
    "image_base": "base",
    "image_rendered": "rendered",
}


@_guard
async def read_page(
    ctx: RunContext[ToolContext],
    page: Pages,
    fields: Annotated[list[ReadField], Field(max_length=16)] | None = None,
    region_nos: Annotated[list[int], Field(min_length=1, max_length=100)] | None = None,
    crop: Crop | None = None,
    max_dimension: Annotated[int, Field(ge=64, le=4096)] = 1600,
) -> ToolReturn | dict:
    """按清单读取页面；空清单默认返回译文和实时渲染图。

    可选字段包括区域数据（如 ``translation``、``font_size``、``font_family``）
    和图片（``image_original``、``image_base``、``image_rendered``）。传入清单后
    只返回所选字段和图片；``page`` 必须是含 1..16 页的列表，超过 16 页分批调用。
    图片支持多页多视图，按整批编码字节数和最终请求体大小校验，超限时缩小批次。
    ``region_nos`` 应用到每页；最终验收必须读取完整 image_rendered，不传 crop。
    """
    selected = set(fields or ())
    if not selected:
        selected = {"translation", "image_rendered"}
    image_fields = selected & _IMAGE_FIELDS.keys()
    data_fields = selected - _IMAGE_FIELDS.keys()
    snapshots = []
    results = []
    remembered = []
    for page_ref in page:
        page_id = ctx.deps.workspace.resolve_page(ctx.deps, page_ref)
        snapshot = ctx.deps.workspace.page(ctx.deps, page_id)
        if region_nos is not None:
            if any(no < 1 or no > len(snapshot["regions"]) for no in region_nos):
                raise ToolError("not_found", "页面不包含指定的 region_no")
            selected_ids = [snapshot["regions"][no - 1]["region_id"] for no in region_nos]
        else:
            selected_ids = None
        result = _public_page(
            snapshot, selected_ids, data_fields, short_region_ids=True
        )
        if not data_fields:
            result.pop("regions", None)
        else:
            remembered.append((snapshot, selected_ids))
        snapshots.append(snapshot)
        results.append(result)
    if not image_fields:
        for snapshot, selected_ids in remembered:
            _remember(ctx, snapshot, selected_ids)
        return {"pages": results}
    payloads = []
    bounds = (crop.left, crop.top, crop.right, crop.bottom) if crop else None
    for snapshot in snapshots:
        for field in ("image_original", "image_base", "image_rendered"):
            if field in image_fields:
                payloads.append(await _observe(
                    ctx, snapshot["page_id"], snapshot["revision"],
                    _IMAGE_FIELDS[field], bounds, max_dimension
                ))
    response = await _image_bundle_return(ctx, {"pages": results}, payloads)
    for snapshot, selected_ids in remembered:
        _remember(ctx, snapshot, selected_ids)
    return response


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
    region_nos: list[Annotated[int, Field(ge=1)]] | None = None,
) -> ToolReturn | dict:
    """比较本任务事务前后真实渲染图及字段；region_nos 使用事务后编号，省略则含已删除框。"""
    before_snapshot, after_snapshot = _comparison(ctx, transaction_id)
    page_id = after_snapshot["page_id"]
    before, after = before_snapshot["revision"], after_snapshot["revision"]
    old_ids = {r["region_id"] for r in before_snapshot["regions"]}
    new_ids = {r["region_id"] for r in after_snapshot["regions"]}
    if region_nos is not None and any(no > len(after_snapshot["regions"]) for no in region_nos):
        raise ToolError("not_found", "事务后的页面不包含指定区域编号")
    selected = (old_ids | new_ids if region_nos is None else
                {after_snapshot["regions"][no - 1]["region_id"] for no in region_nos})
    before_numbers = {region["region_id"]: number for number, region in enumerate(before_snapshot["regions"], 1)}
    after_numbers = {region["region_id"]: number for number, region in enumerate(after_snapshot["regions"], 1)}
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
            if key != "region_id" and prior.get(key) != region.get(key)
        }
        if changes:
            diffs.append({"before_region_no": before_numbers.get(rid),
                          "after_region_no": after_numbers.get(rid), "changes": changes})
    images = []
    observations = []
    for revision in (before, after):
        payload = await _observe(ctx, page_id, revision, max_dimension=1200)
        observations.append(payload)
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
    return await _image_return(
        ctx,
        {
            "image": buffer.getvalue(),
            "mime_type": "image/png",
            "page_id": page_id,
            "transaction_id": transaction_id,
            "snapshots": ["before", "after"],
            "differences": diffs,
        },
        observations=observations,
    )
