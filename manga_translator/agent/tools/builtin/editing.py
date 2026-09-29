"""Post-edit feedback belongs to the tool, not the host's initial prompt."""

import logging
from uuid import uuid4

from pydantic_ai import BinaryContent
from pydantic_ai.messages import ToolReturn

from ...context.images import EDIT_IMAGE_METADATA
from ...domain.tool_models import ToolError
from ...prompts import load_prompt
from .shared import (
    _check_image_delivery, _image_vendor_metadata, _observe, _remember,
    _transactions, public_payload,
)


logger = logging.getLogger(__name__)


async def edit_feedback(ctx, result):
    """Return the commit receipt and render without a full region snapshot."""
    result = _transactions(ctx, result)
    page_id, revision = result["page_id"], result["revision"]
    snapshot = ctx.deps.workspace.page(ctx.deps, page_id, revision)
    metadata = public_payload(ctx.deps, result)
    # Keep concurrency baselines inside the host; details remain available via read_page.
    _remember(ctx, snapshot)
    image = None
    try:
        payload = await _observe(ctx, page_id, revision)
        data = payload.get("image")
        if not data:
            raise ToolError("missing_image", "渲染器未返回图片")
        if len(data) > 8_000_000:
            raise ToolError("output_too_large", "图片超过 8MB，请用 read_page 的 image_rendered 降低分辨率或裁剪")
        image = BinaryContent(data=data, media_type=payload.get("mime_type", "image/png"),
                              identifier=uuid4().hex,
                              vendor_metadata=_image_vendor_metadata(ctx, payload))
        await _check_image_delivery([image])
        if ctx.deps.cancelled.is_set():
            raise ToolError("cancelled", "任务已取消")
        metadata["render_status"] = "rendered"
        metadata.pop("render_error", None)
        metadata["canvas"] = public_payload(ctx.deps, {
            key: value for key, value in payload.items() if key != "image"
        })
    except Exception as error:
        if isinstance(error, ToolError) and error.code == "cancelled":
            raise
        image = None
        logger.exception("Post-edit render failed: task=%s page=%s revision=%s",
                         ctx.deps.task_id, page_id, revision)
        if not isinstance(error, ToolError):
            error = ToolError("render_failed", "渲染失败", {"error_type": type(error).__name__})
        metadata["render_status"] = "failed"
        metadata["render_error"] = public_payload(ctx.deps, error.as_dict())
    # No region snapshot or repeated edit schema is added to model history.
    content = [load_prompt("editing")]
    if image is not None:
        content.extend(["## 本次修改的渲染图", image])
    response = ToolReturn(
        return_value=metadata,
        content=content,
        metadata={EDIT_IMAGE_METADATA: [image.identifier] if image is not None else []},
    )
    return response
