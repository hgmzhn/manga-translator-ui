"""Shared tool context adapters, public payloads, and image return contracts."""

from __future__ import annotations

import asyncio
import functools
import json
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import BinaryContent, RunContext
from pydantic_ai.messages import ToolReturn

from ...context.images import ORIGINAL_IMAGE_METADATA
from ...context.text_replacements import normalize_translation
from ...domain.tool_models import PageRef, ToolContext, ToolError


Limit = Annotated[int, Field(ge=1, le=100)]
Pages = Annotated[list[PageRef], Field(
    min_length=1, max_length=16,
    description="每次 1 到 16 页；更多页面须分批调用，例如 20 页拆为 16 页和 4 页。",
)]
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
    editable_regions: Annotated[
        list[Annotated[int, Field(strict=True, ge=1)]], Field(max_length=100)
    ] | Literal["all"] = Field(
        description="区域编号列表限定可编辑区域；[] 只读；显式 all 授予整页范围，仍受父级权限约束。"
    )


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
            elif key in {"created_region_ids", "deleted_region_ids"}:
                result[key.removesuffix("_ids") + "_count"] = len(item)
            elif (
                key in {"revision", "revisions", "version", "versions", "text_fingerprint", "work_id", "chapter_id"}
                or key in {"work_ids", "chapter_ids"}
                or key in {"region_id", "region_ids"}
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
    available = {r["region_id"] for r in snapshot["regions"]}
    regions = {r["region_id"]: r for r in prior.get("regions", [])
               if r["region_id"] in available}
    regions.update({
        r["region_id"]: {"region_id": r["region_id"], "version": r["version"]}
        for r in snapshot["regions"]
        if selected is None or r["region_id"] in selected
    })
    # A partial read keeps the page's public numbers, not positions in this
    # compact cache. Retain earlier numbers only while their identity still
    # matches the snapshot; structural edits must never retarget a number.
    current_numbers = {
        number: region["region_id"]
        for number, region in enumerate(snapshot["regions"], 1)
    }
    numbers = {
        number: rid for number, rid in prior.get("region_numbers", {}).items()
        if current_numbers.get(number) == rid
    }
    numbers.update({
        number: rid for number, rid in current_numbers.items()
        if selected is None or rid in selected
    })
    ctx.deps.read_snapshots[pid] = {
        "page_id": pid,
        "revision": snapshot["revision"],
        "policy_version": snapshot["policy_version"],
        "regions": list(regions.values()),
        "region_numbers": numbers,
    }


def _baseline(ctx, page_id):
    snapshot = ctx.deps.read_snapshots.get(page_id)
    if snapshot is None:
        raise ToolError("read_required", "请先读取或观察目标页面，再提交修改")
    return snapshot


def _region_id(snapshot, region_no):
    """Resolve an observed public number, never a compact cache position."""
    region_id = snapshot.get("region_numbers", {}).get(region_no)
    if region_id is None:
        raise ToolError("read_required", "请先读取该 region_no 的当前区域数据或整页渲染图")
    return region_id


def _resolved_edit_command(ctx, command_id, request, resolve):
    """Freeze number-to-ID resolution before an edit can change page order."""
    previous = ctx.deps.resolved_edit_commands.get(command_id)
    if previous is not None:
        if previous["request"] != request:
            raise ToolError("idempotency_conflict", "同一 command_id 不能用于不同命令")
        return deepcopy(previous["edits"])
    edits = resolve()
    ctx.deps.resolved_edit_commands[command_id] = {
        "request": deepcopy(request), "edits": deepcopy(edits),
    }
    return edits


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


def _public_page(snapshot, region_ids=None, fields=None, *, short_region_ids=False):
    result = {key: snapshot[key] for key in _PAGE_FIELDS if key in snapshot}
    selected = set(region_ids) if region_ids is not None else None
    available = {r["region_id"] for r in snapshot["regions"]}
    if selected is not None and not selected <= available:
        raise ToolError("not_found", "页面不包含指定区域")
    requested = None if fields is None else set(fields)
    allowed = _REGION_FIELDS if requested is None else requested | {"region_id"}
    if requested is not None and not requested <= (_REGION_FIELDS - {"translation_raw"}):
        raise ToolError(
            "invalid_input", "包含不支持的字段", {
                "allowed": sorted(_REGION_FIELDS - {"translation_raw"})
            }
        )
    result["regions"] = []
    for index, region in enumerate(snapshot["regions"], 1):
        if selected is not None and region["region_id"] not in selected:
            continue
        if requested is None:
            item = {k: v for k, v in region.items() if k in allowed}
        else:
            item = {
                key: region.get("translation") if key == "translation" else region.get(key)
                for key in requested
                if key == "translation" or key in region
            }
            item["region_id"] = region["region_id"]
        if short_region_ids:
            item.pop("region_id", None)
            item["region_no"] = index
        result["regions"].append(item)
    if len(json.dumps(result, ensure_ascii=False).encode()) > 256_000:
        raise ToolError("output_too_large", "请缩小区域或字段范围")
    return result


def _image_vendor_metadata(ctx, payload, *, observations=None):
    metadata = {}
    if payload.get("view") == "original":
        metadata[ORIGINAL_IMAGE_METADATA] = True
    if payload.get("page_id") is not None:
        metadata["workspace_page_image"] = {
            "page_id": payload["page_id"],
            "public_page": ctx.deps.workspace.public_identity(payload["page_id"]),
            "view": payload.get("view"), "crop": payload.get("crop"),
            "revision": payload.get("rendered_revision"),
        }
    # These are candidates, not proof of delivery. ModelImageBudget promotes
    # only images actually included in a successful model request.
    candidates = observations if observations is not None else [payload]
    metadata["workspace_observations"] = [
        {"page_id": item["page_id"], "revision": item["rendered_revision"]}
        for item in candidates
        if item.get("view") == "rendered" and item.get("crop") is None
        and item.get("image") and item.get("rendered_revision") is not None
    ]
    return metadata or None


async def _check_image_delivery(images):
    """Reject oversized tool output before it can poison the next model request."""
    from ...context.media import fit_model_images
    from ...domain.chat import ChatImage

    try:
        await asyncio.to_thread(
            fit_model_images, [ChatImage(item.data, item.media_type) for item in images],
            optimize=False,
        )
    except ValueError as error:
        raise ToolError(
            "image_batch_too_large",
            "本次图片批次超过可读编码预算，尚未交付给模型。请缩小读取批次，"
            "例如 page 只含一页，fields 只含 image_original 或 image_rendered；"
            "单张仍过大时先读取局部 crop，"
            "最终验收仍须读取完整 image_rendered，可降低 max_dimension。已提交编辑不要重复执行。",
            {"image_count": len(images), "suggested_fields": ["image_rendered"]},
        ) from error


def _remember_images(ctx, payloads):
    for payload in payloads:
        if payload.get("view") == "rendered" and payload.get("crop") is None:
            snapshot = ctx.deps.workspace.page(ctx.deps, payload["page_id"], payload["rendered_revision"])
            _remember(ctx, snapshot)


async def _image_return(ctx, payload, *, observations=None, **extra):
    metadata = {key: value for key, value in payload.items() if key != "image"}
    metadata.update(extra)
    metadata = public_payload(ctx.deps, metadata)
    image = payload.get("image")
    if image is None:
        return metadata
    if len(image) > 8_000_000:
        raise ToolError("output_too_large", "图片超过 8MB，请降低分辨率或裁剪")
    content_image = BinaryContent(
        data=image, media_type=payload.get("mime_type", "image/png"),
        vendor_metadata=_image_vendor_metadata(ctx, payload, observations=observations),
    )
    await _check_image_delivery([content_image])
    if ctx.deps.cancelled.is_set():
        raise ToolError("cancelled", "任务已取消")
    result = ToolReturn(
        return_value=metadata,
        content=[
            json.dumps(metadata, ensure_ascii=False),
            content_image,
        ],
    )
    _remember_images(ctx, observations if observations is not None else [payload])
    return result


async def _image_bundle_return(ctx, metadata, payloads):
    """Return one compact structured result followed by selected image blocks."""
    images = []
    content = []
    for payload in payloads:
        image = payload.get("image")
        if image is None:
            continue
        if len(image) > 8_000_000:
            raise ToolError("output_too_large", "图片超过 8MB，请降低分辨率或裁剪")
        image_metadata = public_payload(ctx.deps, {
            key: payload[key]
            for key in (
                "page_id", "view", "crop", "width", "height",
                "source_width", "source_height", "mime_type",
            )
            if key in payload
        })
        images.append(image_metadata)
        content.append(
            BinaryContent(
                data=image,
                media_type=payload.get("mime_type", "image/png"),
                vendor_metadata=_image_vendor_metadata(ctx, payload),
            )
        )
    await _check_image_delivery(content)
    if ctx.deps.cancelled.is_set():
        raise ToolError("cancelled", "任务已取消")
    result = public_payload(ctx.deps, {**metadata, "images": images})
    result = ToolReturn(
        return_value=result,
        content=[json.dumps(result, ensure_ascii=False), *content],
    )
    _remember_images(ctx, payloads)
    return result


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
    if not payload.get("image"):
        raise ToolError("missing_image", "渲染器未返回图片")
    if len(payload["image"]) > 8_000_000:
        raise ToolError("output_too_large", "图片超过 8MB，请降低分辨率或裁剪")
    # These identify the observation requested by the host, even when a custom
    # renderer omits its crop/view metadata. A crop can never count as a full page.
    return {**payload, "page_id": page_id, "view": view, "crop": crop}
