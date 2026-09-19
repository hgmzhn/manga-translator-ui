"""Shared tool context adapters, public payloads, and image return contracts."""

from __future__ import annotations

import functools
import json
from copy import deepcopy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import BinaryContent, RunContext
from pydantic_ai.messages import ToolReturn

from ...context.images import ORIGINAL_IMAGE_METADATA
from ...domain.tool_models import PageRef, ToolContext, ToolError


Limit = Annotated[int, Field(ge=1, le=100)]
Pages = Annotated[list[PageRef], Field(min_length=1, max_length=16)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
CommandId = Annotated[str, Field(min_length=1, max_length=200)]
TaskIds = Annotated[list[str], Field(min_length=1, max_length=16)]


class Crop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: Annotated[int, Field(ge=0)]
    top: Annotated[int, Field(ge=0)]
    right: Annotated[int, Field(gt=0)]
    bottom: Annotated[int, Field(gt=0)]


class PageAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: PageRef
    editable_regions: list[str]


_PAGE_FIELDS = (
    "page_id",
    "order",
    "folder",
    "name",
    "ocr_status",
    "base_status",
    "project_status",
    "width",
    "height",
)
_REGION_FIELDS = {
    "region_id",
    "text",
    "texts",
    "translation",
    "translation_raw",
    "translation_rich",
    "center",
    "angle",
    "locked",
    "is_locked",
    "font_size",
    "font_family",
    "bold",
    "italic",
    "direction",
    "alignment",
    "line_spacing",
    "letter_spacing",
    "stroke_width",
    "stroke_color",
    "disable_font_border",
    "font_color",
    "fg_colors",
    "bg_colors",
    "opacity",
    "text_offset",
    "language",
    "source_lang",
    "target_lang",
    "prob",
    "layout_mode",
    "adjust_bg_color",
    "shadow_radius",
    "shadow_strength",
    "shadow_color",
    "shadow_offset",
    "ocr_status",
}


def _guard(function):
    @functools.wraps(function)
    async def guarded(ctx: RunContext[ToolContext], *args, **kwargs):
        try:
            if ctx.deps.cancelled.is_set():
                raise ToolError("cancelled", "任务已取消")
            result = await function(ctx, *args, **kwargs)
            return public_payload(ctx.deps, result)
        except ToolError as error:
            return public_payload(ctx.deps, {
                "status": "error",
                "error": {
                    "code": error.code,
                    "message": str(error),
                    "details": error.details,
                },
            })

    return guarded


def public_payload(deps, value):
    """Translate internal resource identities and strip host-only concurrency data."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "page_id":
                result.update(deps.workspace.public_identity(item))
            elif key == "page_ids":
                result["pages"] = [deps.workspace.public_identity(pid) for pid in item]
            elif (
                key in {"revision", "revisions", "version", "versions", "text_fingerprint", "work_id", "chapter_id"}
                or key.endswith(("_revision", "_revisions", "_version", "_versions", "_asset"))
            ):
                continue
            else:
                result[key] = public_payload(deps, item)
        return result
    if isinstance(value, (list, tuple)):
        return [public_payload(deps, item) for item in value]
    return value


def _remember(ctx, snapshot, region_ids=None):
    pid = snapshot["page_id"]
    selected = None if region_ids is None else set(region_ids)
    prior = ctx.deps.read_snapshots.get(pid, {})
    regions = {r["region_id"]: r for r in prior.get("regions", [])}
    regions.update({
        r["region_id"]: {"region_id": r["region_id"], "version": r["version"]}
        for r in snapshot["regions"]
        if selected is None or r["region_id"] in selected
    })
    ctx.deps.read_snapshots[pid] = {
        "page_id": pid,
        "revision": snapshot["revision"],
        "policy_version": snapshot["policy_version"],
        "regions": list(regions.values()),
    }


def _baseline(ctx, page_id):
    snapshot = ctx.deps.read_snapshots.get(page_id)
    if snapshot is None:
        raise ToolError("read_required", "请先读取或观察目标页面，再提交修改")
    return snapshot


def _command(ctx, command_id, request, capture):
    """Keep the first CAS payload so the core can safely replay a command."""
    previous = ctx.deps.command_payloads.get(command_id)
    if previous is not None:
        if previous["request"] != request:
            raise ToolError("idempotency_conflict", "同一 command_id 不能用于不同命令")
        return previous["preconditions"]
    preconditions = capture()
    ctx.deps.command_payloads[command_id] = {
        "request": deepcopy(request), "preconditions": deepcopy(preconditions)
    }
    return preconditions


def _transactions(ctx, result):
    if isinstance(result, dict):
        if "transaction_id" in result and "region_versions" in result:
            ctx.deps.transaction_results[result["transaction_id"]] = deepcopy(result)
            renderer = ctx.deps.renderer
            if renderer is not None and hasattr(renderer, "schedule"):
                try:
                    snapshot = ctx.deps.workspace.page(ctx.deps, result["page_id"], result["revision"])
                    result["render_status"] = renderer.schedule(snapshot, cancelled=ctx.deps.cancelled)
                except ToolError as error:
                    result["render_status"] = "failed"
                    result["render_error"] = error.as_dict()
        for item in result.values():
            _transactions(ctx, item)
    elif isinstance(result, list):
        for item in result:
            _transactions(ctx, item)
    return result


def _comparison(ctx, transaction_id):
    transaction = ctx.deps.transaction_results.get(transaction_id)
    if transaction is None:
        raise ToolError("transaction_not_owned", "只能比较本任务已提交的事务")
    pid, revision = transaction["page_id"], transaction["revision"]
    old = ctx.deps.workspace.page(ctx.deps, pid, revision - 1)
    new = ctx.deps.workspace.page(ctx.deps, pid, revision)
    return old, new


def _service(ctx, name):
    service = getattr(ctx.deps, name, None)
    if service is None:
        raise ToolError("unavailable", f"宿主尚未配置 {name}")
    return service


def _window(items, cursor, limit):
    if not 1 <= limit <= 100:
        raise ToolError("invalid_input", "limit 必须为 1..100")
    if cursor is not None and (not cursor.isascii() or not cursor.isdecimal()):
        raise ToolError("invalid_cursor", "游标无效")
    start = int(cursor or 0)
    if start > len(items):
        raise ToolError("invalid_cursor", "游标超出结果范围")
    end = min(start + limit, len(items))
    return {
        "items": items[start:end],
        "total": len(items),
        "next_cursor": str(end) if end < len(items) else None,
    }


def _public_page(snapshot, region_ids=None, fields=None):
    result = {key: snapshot[key] for key in _PAGE_FIELDS if key in snapshot}
    selected = set(region_ids) if region_ids is not None else None
    available = {r["region_id"] for r in snapshot["regions"]}
    if selected is not None and not selected <= available:
        raise ToolError("not_found", "页面不包含指定区域")
    allowed = (
        _REGION_FIELDS if fields is None else set(fields) | {"region_id"}
    )
    if not allowed <= _REGION_FIELDS:
        raise ToolError(
            "invalid_input", "包含不支持的字段", {"allowed": sorted(_REGION_FIELDS)}
        )
    result["regions"] = [
        {k: v for k, v in region.items() if k in allowed}
        for region in snapshot["regions"]
        if selected is None or region["region_id"] in selected
    ]
    if len(json.dumps(result, ensure_ascii=False).encode()) > 256_000:
        raise ToolError("output_too_large", "请缩小区域或字段范围")
    return result


def _image_return(ctx, payload, **extra):
    metadata = {key: value for key, value in payload.items() if key != "image"}
    metadata.update(extra)
    metadata = public_payload(ctx.deps, metadata)
    image = payload.get("image")
    if image is None:
        return metadata
    if len(image) > 8_000_000:
        raise ToolError("output_too_large", "图片超过 8MB，请降低分辨率或裁剪")
    return ToolReturn(
        return_value=metadata,
        content=[
            json.dumps(metadata, ensure_ascii=False),
            BinaryContent(data=image, media_type=payload.get("mime_type", "image/png"),
                          vendor_metadata={ORIGINAL_IMAGE_METADATA: True}
                          if payload.get("view") == "original" else None),
        ],
    )


async def _observe(
    ctx, page_id, revision, view="rendered", crop=None, max_dimension=1600
):
    workspace = ctx.deps.workspace
    snapshot = workspace.page(ctx.deps, page_id, revision)
    payload = await _service(ctx, "renderer").observe(
        snapshot,
        view=view,
        crop=crop,
        max_dimension=max_dimension,
    )
    # Rendering can outlive a permission revocation. Recheck before releasing bytes.
    workspace.page(ctx.deps, page_id, revision)
    if ctx.deps.cancelled.is_set():
        raise ToolError("cancelled", "任务已取消")
    if view == "rendered":
        if payload.get("rendered_revision") != revision:
            raise ToolError("revision_mismatch", "渲染器没有返回请求版本")
        ctx.deps.observed_revisions[page_id] = revision
    _remember(ctx, snapshot, None if view == "rendered" and crop is None else [])
    return payload
