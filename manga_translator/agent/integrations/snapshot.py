"""Decode saved page geometry and image layers without an editor data model."""

import base64
import io
import math

import numpy as np
from PIL import Image

from ..domain.tool_models import ToolError


def layout_box(region, lines):
    """Resolve the saved local layout rectangle and its world-space center."""
    vertices = lines.reshape(-1, 2)
    source_center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    center = np.asarray(
        source_center if region.get("center") is None else region["center"],
        dtype=float,
    )
    if center.shape != (2,) or not np.isfinite(center).all():
        raise ToolError("invalid_geometry", "Region requires a finite center")
    custom = region.get("white_frame_rect_local")
    target = region.get("render_box_rect_local")
    if custom is not None and (region.get("has_custom_white_frame") or target is None):
        target = custom
    if target is None:
        # An explicit center is the rendering anchor, not the OCR box origin.
        # Subtracting it here would cancel a center-only move on application.
        local = vertices - source_center
        target = (*local.min(axis=0), *local.max(axis=0))
    target = np.asarray(target, dtype=float)
    if (target.shape != (4,) or not np.isfinite(target).all()
            or target[2] <= target[0] or target[3] <= target[1]):
        raise ToolError("invalid_geometry", "Region has no positive layout box")
    angle = float(region.get("angle", 0) or 0)
    if not math.isfinite(angle):
        raise ToolError("invalid_geometry", "Region rotation must be finite")
    theta = math.radians(angle)
    x, y = (target[:2] + target[2:]) / 2
    world = center + (x * math.cos(theta) - y * math.sin(theta),
                      x * math.sin(theta) + y * math.cos(theta))
    return world.tolist(), target.tolist()


def composite_paste_layers(image, layers):
    """Apply saved RGBA image tiles; text is always drawn by the existing backend."""
    import cv2

    def number(layer, key, default):
        value = float(layer.get(key, default))
        if not math.isfinite(value):
            raise ToolError("invalid_overlay", "Image layer geometry must be finite")
        return value

    for layer in sorted(layers, key=lambda item: number(item, "z", 0)):
        if not layer.get("visible", True):
            continue
        encoded = layer.get("image", "")
        if not encoded:
            continue
        if len(encoded) > 24_000_000:
            raise ToolError("resource_limit", "Paste image exceeds byte limit")
        with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as tile:
            if tile.mode != "RGBA" or max(tile.size) > 8192:
                raise ToolError("invalid_overlay", "Paste image must be bounded RGBA")
            source = np.array(tile)
        height, width = source.shape[:2]
        target_w, target_h = number(layer, "width", width), number(layer, "height", height)
        opacity = max(0, min(1, number(layer, "opacity", 1)))
        if target_w <= 0 or target_h <= 0 or opacity == 0:
            continue
        source[..., 3] = (source[..., 3].astype(np.float32) * opacity).astype(np.uint8)
        premul = source.astype(np.float32)
        premul[..., :3] *= premul[..., 3:4] / 255
        sx = target_w / width * (-1 if layer.get("flip_h") else 1)
        sy = target_h / height * (-1 if layer.get("flip_v") else 1)
        angle = math.radians(number(layer, "rotation", 0))
        c, s = math.cos(angle), math.sin(angle)
        transform = np.array([[c * sx, -s * sy, 0], [s * sx, c * sy, 0]])
        transform[:, 2] = (
            number(layer, "center_x", 0), number(layer, "center_y", 0)
        ) - transform[:, :2] @ (width / 2, height / 2)
        corners = np.array([[0, 0], [width, 0], [width, height], [0, height]])
        corners = corners @ transform[:, :2].T + transform[:, 2]
        left, top = np.maximum(0, np.floor(corners.min(axis=0)).astype(int) - 1)
        right, bottom = np.minimum(image.size, np.ceil(corners.max(axis=0)).astype(int) + 1)
        if right <= left or bottom <= top:
            continue
        box = tuple(map(int, (left, top, right, bottom)))
        transform[:, 2] -= (left, top)
        patch = cv2.warpAffine(premul, transform, (box[2] - box[0], box[3] - box[1]),
                               flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        # Composite only the affected rectangle, including transparent backgrounds.
        base = np.array(image.crop(box), dtype=np.float32)
        base[..., :3] *= base[..., 3:4] / 255
        merged = patch + base * (1 - patch[..., 3:4] / 255)
        merged[..., :3] /= np.maximum(merged[..., 3:4] / 255, 1e-6)
        image.paste(Image.fromarray(np.clip(merged, 0, 255).astype(np.uint8)), box[:2])
    return image
