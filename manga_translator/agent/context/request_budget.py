"""Bound only the model-wire image projection, retaining native history verbatim."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields, is_dataclass, replace
from hashlib import sha256
import json

from pydantic_ai import BinaryContent, ModelRetry
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart

from ..domain.chat import ChatImage
from ..domain.tool_models import ToolError
from .limits import (
    ENVELOPE_RESERVE_BYTES, MAX_IMAGE_BYTES, MAX_IMAGE_DIMENSION,
    MAX_REQUEST_BYTES, MIN_IMAGE_DIMENSION,
)
from .media import fit_model_images

PAGE_IMAGE_METADATA = "workspace_page_image"


@dataclass(frozen=True)
class RequestImageBudget:
    """Leave headroom below the measured gateway limit; callers may override."""

    max_request_bytes: int = MAX_REQUEST_BYTES
    max_image_bytes: int = MAX_IMAGE_BYTES
    envelope_reserve_bytes: int = ENVELOPE_RESERVE_BYTES
    max_dimension: int = MAX_IMAGE_DIMENSION
    min_dimension: int = MIN_IMAGE_DIMENSION


def _size(value) -> int:
    """Estimate JSON bytes, including base64 expansion, without building base64."""
    if isinstance(value, BinaryContent):
        return 256 + 4 * ((len(value.data) + 2) // 3)
    if isinstance(value, bytes):
        return 256 + 4 * ((len(value) + 2) // 3)
    if is_dataclass(value) and not isinstance(value, type):
        return _size({field.name: getattr(value, field.name) for field in fields(value)})
    if isinstance(value, dict):
        return 2 + sum(_size(str(key)) + 1 + _size(item) + 1 for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return 2 + sum(_size(item) + 1 for item in value)
    if hasattr(value, "model_dump"):
        return _size(value.model_dump(mode="python"))
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8")) + 32


def _metadata(value):
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _identity(metadata):
    public = metadata.get("public_page") or metadata.get("page") or metadata
    if not isinstance(public, dict):
        public = {}
    if metadata.get("page_id"):
        return ("internal", str(metadata["page_id"]))
    if public.get("id") is not None:
        return ("public", str(public["id"]), str(public.get("folder", "")), str(public.get("name", "")))
    if public.get("name"):
        return ("path", str(public.get("folder", ".")), str(public["name"]))
    return None


@dataclass
class _ImageSlot:
    position: tuple[int, int, int]
    image: BinaryContent
    scope: tuple
    label: str
    occurrence: int


def _slots(messages):
    result = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        edit_metadata = {}
        for part in message.parts:
            identifiers = (getattr(part, "metadata", None) or {})
            if isinstance(identifiers, dict):
                for identifier in identifiers.get("workspace_edit_images") or ():
                    metadata = _metadata(getattr(part, "content", None)) or {}
                    edit_metadata[identifier] = metadata.get("canvas") or metadata
        for part_index, part in enumerate(message.parts):
            content = getattr(part, "content", None)
            if not isinstance(content, (list, tuple)):
                continue
            pending = []
            for item_index, item in enumerate(content):
                metadata = _metadata(item)
                if metadata is not None:
                    images = metadata.get("images")
                    if isinstance(images, list):
                        pending = [entry for entry in images if isinstance(entry, dict)]
                    elif _identity(metadata):
                        pending = [metadata]
                if not isinstance(item, BinaryContent) or not item.is_image:
                    continue
                fallback = pending.pop(0) if pending else edit_metadata.get(item.identifier, {})
                metadata = (item.vendor_metadata or {}).get(PAGE_IMAGE_METADATA) or fallback
                identity = _identity(metadata)
                public = metadata.get("public_page") or metadata
                if identity is not None:
                    view = metadata.get("view") or (
                        "original" if (item.vendor_metadata or {}).get("workspace_original_image") else "rendered"
                    )
                    # A recent crop does not replace the most recent full-page observation.
                    scope = (*identity, view, "crop" if metadata.get("crop") is not None else "full")
                    label = str(public.get("id") or public.get("name") or "page")
                else:
                    # Attachments/font specimens of unknown provenance cannot safely supersede each other.
                    scope = ("unidentified", sha256(item.data).hexdigest())
                    label = "attachment"
                result.append(_ImageSlot((message_index, part_index, item_index), item, scope, label, len(result)))
    return result


def _project(messages, replacements):
    """Copy only changed request/part containers; never mutate history objects."""
    projected = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            projected.append(message)
            continue
        parts = []
        changed = False
        for part_index, part in enumerate(message.parts):
            content = getattr(part, "content", None)
            if isinstance(content, (list, tuple)):
                copy = [replacements.get((message_index, part_index, index), item)
                        for index, item in enumerate(content)]
                if any(left is not right for left, right in zip(copy, content)):
                    part = replace(part, content=copy)
                    changed = True
            parts.append(part)
        projected.append(replace(message, parts=parts) if changed else message)
    return projected


class ModelImageBudget(AbstractCapability):
    """Project images at the model call boundary, after native history is recorded."""

    def __init__(self, *, budget: RequestImageBudget | None = None, trace=None):
        self.budget = budget or RequestImageBudget()
        self.trace = trace
        self._cache = {}
        self._deferred = set()
        self._recovery_attempts = 0
        if (self.budget.max_request_bytes <= self.budget.envelope_reserve_bytes
                or self.budget.max_image_bytes <= 0):
            raise ValueError("invalid request image budget")

    def project_request(self, request_context):
        """Pure request projection, usable for offline verification without a model."""
        budget = self.budget
        messages = request_context.messages
        slots = _slots(messages)
        deferred = {slot.position for slot in slots if self._slot_key(slot) in self._deferred}
        eligible = [slot for slot in slots if slot.position not in deferred]
        newest = {slot.scope: slot.position for slot in eligible}
        latest_request = max((index for index, message in enumerate(messages)
                              if isinstance(message, ModelRequest)), default=-1)
        last_response = max((index for index, message in enumerate(messages)
                             if isinstance(message, ModelResponse)), default=-1)
        keep = [slot for slot in eligible if newest[slot.scope] == slot.position
                or slot.position[0] > last_response]
        removable = [slot for slot in keep if slot.position[0] < last_response]
        overhead = (_size(request_context.model_request_parameters)
                    + _size(request_context.model_settings) + budget.envelope_reserve_bytes)

        def plan():
            retained = {slot.position for slot in keep}
            replacements = {}
            for slot in slots:
                if slot.position in retained:
                    continue
                if slot.position in deferred:
                    reason = "This image was NOT sent to the model and is NOT a visual observation."
                else:
                    reason = "This previously sent image was omitted to free request-body budget."
                replacements[slot.position] = (
                    f"[Image for {slot.label}: {reason} Original image, tool text and translations "
                    "remain in host history. Use read_page, one page and one image view per call, "
                    "to inspect it again. Do not guess unread image contents.]"
                )
            placeholders = {**replacements, **{slot.position: "" for slot in keep}}
            baseline = _size(_project(messages, placeholders)) + overhead + 1024 * (len(keep) + 1)
            raw_budget = max(0, (budget.max_request_bytes - baseline) * 3 // 4 - 3 * len(keep))
            return replacements, baseline, raw_budget

        # Reclaim older transmitted images before sacrificing resolution of new
        # tool results. Page/view uniqueness is not a reason to retain all history.
        replacements, baseline, raw_budget = plan()
        while removable and sum(len(slot.image.data) for slot in keep) > raw_budget:
            keep.remove(removable.pop(0))
            replacements, baseline, raw_budget = plan()
        available = budget.max_request_bytes - baseline
        if available <= 0:
            raise ToolError(
                "request_too_large",
                "模型请求的文字、工具定义或任务上下文已超过请求预算；未删除译文或任务信息。",
                {"estimated_text_bytes": baseline, "budget_bytes": budget.max_request_bytes},
            )
        compressed = 0
        while True:
            batch_key = (tuple((sha256(slot.image.data).digest(), slot.image.media_type) for slot in keep),
                         raw_budget, budget.max_image_bytes, budget.max_dimension, budget.min_dimension)
            fitted_batch = self._cache.get(batch_key)
            if fitted_batch is not None:
                break
            try:
                fitted_batch = fit_model_images(
                    [ChatImage(slot.image.data, slot.image.media_type) for slot in keep],
                    max_total_bytes=max(1, raw_budget), max_image_bytes=budget.max_image_bytes,
                    max_dimension=budget.max_dimension, min_dimension=budget.min_dimension,
                )
            except ValueError as error:
                if removable:
                    keep.remove(removable.pop(0))
                    replacements, baseline, raw_budget = plan()
                    continue
                raise ToolError(
                    "request_too_large", "最新工具图片批次超过可读图像预算；请每次只读取一页的一种视图。",
                    {"images_kept": len(keep), "image_batch_budget_bytes": raw_budget,
                     "budget_bytes": budget.max_request_bytes, "reason": str(error)},
                ) from error
            if len(self._cache) >= 4:
                self._cache.clear()
            self._cache[batch_key] = fitted_batch
            break
        for slot, fitted in zip(keep, fitted_batch):
            image = slot.image
            if fitted.data is not image.data:
                compressed += 1
                replacements[slot.position] = BinaryContent(
                    data=fitted.data, media_type=fitted.media_type,
                    identifier=image.identifier, vendor_metadata=image.vendor_metadata,
                )
        projected = _project(messages, replacements)
        if compressed and latest_request >= 0:
            message = projected[latest_request]
            note = UserPromptPart(
                "[Host transport note: image copies in this request use measured PNG/WebP/JPEG "
                "compression (45% starting quality) and may be resized to fit the request-body budget. "
                "Tool metadata still describes the original page. "
                "If lettering cannot be read, use read_page with a crop; do not guess its contents. "
                "All original images and text remain in the host's full history.]"
            )
            projected[latest_request] = replace(message, parts=[*message.parts, note])
        estimated = (_size(projected) + _size(request_context.model_request_parameters)
                     + _size(request_context.model_settings) + budget.envelope_reserve_bytes)
        if estimated > budget.max_request_bytes:
            raise ToolError("request_too_large", "模型请求超过发送预算，未发送或删除文字上下文。",
                            {"estimated_bytes": estimated, "budget_bytes": budget.max_request_bytes})
        summary = {
            "images_before": len(slots), "images_kept": len(keep),
            "older_images_omitted": len(slots) - len(keep), "images_compressed": compressed,
            "image_bytes_before": sum(len(slot.image.data) for slot in keep),
            "image_bytes_sent": sum(len(image.data) for image in fitted_batch),
            "image_formats_sent": [image.media_type for image in fitted_batch],
            "estimated_request_bytes": estimated, "request_budget_bytes": budget.max_request_bytes,
            "original_history_preserved": True,
            "unsent_images_deferred": len(deferred),
        }
        return replace(request_context, messages=projected), summary

    async def wrap_model_request(self, ctx, *, request_context, handler):
        # before_model_request would replace all_messages() in current PydanticAI.
        # This wrapper runs after that history bookkeeping for both streaming and non-streaming.
        try:
            projected, summary = await asyncio.to_thread(self.project_request, request_context)
        except ToolError as error:
            unsent = [slot for slot in _slots(request_context.messages)
                      if self._slot_key(slot) not in self._deferred
                      and slot.position[0] > max((index for index, message in enumerate(request_context.messages)
                                                 if isinstance(message, ModelResponse)), default=-1)]
            if error.code == "request_too_large" and unsent and self._recovery_attempts == 0:
                self._recovery_attempts += 1
                self._deferred.update(self._slot_key(slot) for slot in unsent)
                if self.trace is not None:
                    self.trace.add("request_budget", {"recovery": "reread_required", "unsent_images": len(unsent)})
                raise ModelRetry(
                    "上一批工具图片超过请求体预算，尚未发送给模型，因此不能认定已观察或验收。"
                    "原始文字和图片仍在宿主历史中。请重新调用 read_page，每次只传一页，"
                    "fields 只选一种图片视图（例如 image_rendered），需要细节时单独裁剪读取。"
                    "不要再次批量请求全部页的多种图片。此次自动恢复只执行一次。"
                ) from error
            raise
        if self.trace is not None:
            self.trace.add("request_budget", summary)
        try:
            response = await handler(projected)
            observations = getattr(getattr(ctx, "deps", None), "observed_revisions", None)
            if observations is not None:
                for slot in _slots(projected.messages):
                    for observation in (slot.image.vendor_metadata or {}).get("workspace_observations", ()):
                        page_id, revision = observation.get("page_id"), observation.get("revision")
                        if page_id is not None and revision is not None:
                            observations[page_id] = max(observations.get(page_id, revision), revision)
            return response
        except Exception as error:
            if getattr(error, "status_code", None) == 413:
                raise ToolError(
                    "request_too_large",
                    "服务端拒绝了请求体（HTTP 413）。图片已按预算整理，但实际代理上限可能更低；"
                    "请检查反向代理/API请求体上限。不会重试相同请求。",
                    summary,
                ) from error
            raise

    @staticmethod
    def _slot_key(slot):
        # PydanticAI may merge adjacent requests and move tool/retry parts before
        # user parts. Image occurrence order survives those container changes.
        return (slot.occurrence, slot.scope, slot.image.identifier)
