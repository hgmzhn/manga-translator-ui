"""Keep original references while discarding superseded editing snapshots."""

from dataclasses import replace

from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)

EDIT_IMAGE_METADATA = "workspace_edit_images"
ORIGINAL_IMAGE_METADATA = "workspace_original_image"
_REMOVED = "[历史图片已移除；请以最近一次编辑返回的新图为准。]"
_REGIONS_REMOVED = "[历史区域数据已移除；请以最近一次编辑返回的区域数据为准。]"


def original_image_content(image, reference_id, messages):
    """Attach the host's immutable original once per conversation, including after clear."""
    if image is None:
        return []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, (list, tuple)):
                if any(isinstance(item, BinaryContent)
                       and (item.vendor_metadata or {}).get(ORIGINAL_IMAGE_METADATA) == reference_id
                       for item in part.content):
                    return []
    return [
        "原图（固定参照，用于核对原文与参考原文排版；编辑坐标以当前页面数据为准）：",
        BinaryContent(data=image.data, media_type=image.media_type,
                      vendor_metadata={ORIGINAL_IMAGE_METADATA: reference_id}),
    ]


def prune_images_after_edit(messages):
    """Act only on host-marked edit results; leave initial context alone otherwise.

    Native response parts, including signed/encrypted thinking, are never copied
    or reconstructed. Replacing requests also leaves a caller's saved history
    untouched if the current run later fails or is cancelled.
    """
    boundary = None
    keep = set()
    latest_result = None
    for index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.metadata, dict):
                identifiers = part.metadata.get(EDIT_IMAGE_METADATA)
                if identifiers is not None:
                    boundary, keep = index, set(identifiers)
                    latest_result = part
    if boundary is None:
        return

    def clean(value, *, region_data=False):
        if isinstance(value, ImageUrl) or isinstance(value, BinaryContent) and value.is_image:
            if (value.vendor_metadata or {}).get(ORIGINAL_IMAGE_METADATA):
                return value
            return value if value.identifier in keep else _REMOVED
        if isinstance(value, list):
            return [clean(item, region_data=region_data) for item in value]
        if isinstance(value, tuple):
            return tuple(clean(item, region_data=region_data) for item in value)
        if isinstance(value, dict):
            return {key: _REGIONS_REMOVED if region_data and key == "regions"
                    else clean(item, region_data=region_data) for key, item in value.items()}
        return value

    for index, message in enumerate(messages[: boundary + 1]):
        if isinstance(message, ModelRequest):
            messages[index] = replace(message, parts=[
                replace(part, content=clean(part.content, region_data=(part is not latest_result)))
                if isinstance(part, (UserPromptPart, ToolReturnPart)) else part
                for part in message.parts
            ])
