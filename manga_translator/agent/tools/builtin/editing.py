"""Post-edit feedback belongs to the tool, not the host's initial prompt."""

import logging
from uuid import uuid4

from pydantic_ai import BinaryContent
from pydantic_ai.messages import ToolReturn

from ...context.images import EDIT_IMAGE_METADATA
from ...domain.tool_models import GeometryPatch, RegionStylePatch, ToolError
from ...prompts import load_prompt
from .shared import _observe, _public_page, _remember, _transactions, public_payload


logger = logging.getLogger(__name__)


async def edit_feedback(ctx, result):
    """Append full public page facts and the exact committed render, in that order."""
    result = _transactions(ctx, result)
    page_id, revision = result["page_id"], result["revision"]
    snapshot = ctx.deps.workspace.page(ctx.deps, page_id, revision)
    metadata = public_payload(ctx.deps, result)
    metadata["editable_fields"] = {
        "set_region_style": list(RegionStylePatch.model_fields),
        "set_geometry": list(GeometryPatch.model_fields),
    }
    try:
        metadata["page"] = public_payload(ctx.deps, _public_page(snapshot))
        _remember(ctx, snapshot)
    except ToolError as error:
        metadata["page_error"] = error.as_dict()
    image = None
    previous_observation = ctx.deps.observed_revisions.get(page_id)
    try:
        payload = await _observe(ctx, page_id, revision)
        data = payload.get("image")
        if not data:
            raise ToolError("missing_image", "渲染器未返回图片")
        if len(data) > 8_000_000:
            raise ToolError("output_too_large", "图片超过 8MB，请 observe_canvas 降低分辨率或裁剪")
        image = BinaryContent(data=data, media_type=payload.get("mime_type", "image/png"),
                              identifier=uuid4().hex)
        metadata["render_status"] = "rendered"
        metadata.pop("render_error", None)
        metadata["canvas"] = public_payload(ctx.deps, {
            key: value for key, value in payload.items() if key != "image"
        })
    except Exception as error:
        if isinstance(error, ToolError) and error.code == "cancelled":
            raise
        logger.exception("Post-edit render failed: task=%s page=%s revision=%s",
                         ctx.deps.task_id, page_id, revision)
        if previous_observation is None:
            ctx.deps.observed_revisions.pop(page_id, None)
        else:
            ctx.deps.observed_revisions[page_id] = previous_observation
        if not isinstance(error, ToolError):
            error = ToolError("render_failed", "渲染失败", {"error_type": type(error).__name__})
        metadata["render_status"] = "failed"
        metadata["render_error"] = public_payload(ctx.deps, error.as_dict())
    # Region JSON lives only in the structured result, never duplicated as text.
    content = [load_prompt("editing")]
    if image is not None:
        content.extend(["## 最新渲染图", image])
    return ToolReturn(
        return_value=metadata,
        content=content,
        metadata={EDIT_IMAGE_METADATA: [image.identifier] if image is not None else []},
    )
