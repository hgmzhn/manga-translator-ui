"""
贴片（图块叠加）数据层 —— 规范化、序列化与页面 JSON 解析。

贴片数据模型：每页一个 ``paste_overlays`` 列表（与 ``regions`` 平级），每一项表示一张可
自由放置/缩放/旋转的 PNG 素材（修补块、特效字、背景贴片等）。图片内容以 base64 PNG
（RGBA）内嵌在 JSON 里，与 ``mask_raw`` / ``paint_overlay`` / ``stamp_overlay`` 的存放
方式保持一致，便于整页随工程文件迁移。

页面 JSON 中的存储键::

    "paste_overlays": [{
        "id": "…", "name": "…",
        "z": 0, "visible": true, "opacity": 1.0,
        "center_x": 0.0, "center_y": 0.0,
        "width": 0.0, "height": 0.0,
        "rotation": 0.0, "flip_h": false, "flip_v": false,
        "image": "<base64 PNG, RGBA>"
    }, …]

几何字段均为源图分辨率下的数值（浮点）。本模块只依赖 ``numpy`` / ``cv2``，不依赖 Qt。
"""

from __future__ import annotations

import base64
import copy
import logging
import math
import uuid
from typing import Any, Iterable, Mapping

import cv2
import numpy as np

logger = logging.getLogger("manga_translator")

PAGE_KEY = "paste_overlays"

_ALPHA_ZERO = 0
_OPACITY_MIN = 0.0
_OPACITY_MAX = 1.0


def _to_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} 必须为数字，收到: {value!r}") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值，收到: {value!r}")
    return number


def _to_int(value: Any, name: str) -> int:
    number = _to_float(value, name)
    if not number.is_integer():
        raise ValueError(f"{name} 必须为整数，收到: {value!r}")
    return int(number)


def _to_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{name} 必须为布尔值，收到: {value!r}")
    return bool(value) if isinstance(value, str) else False


def _clamp_opacity(value: float) -> float:
    return max(_OPACITY_MIN, min(_OPACITY_MAX, value))


def _validate_image_field(image: Any) -> str:
    if not isinstance(image, str):
        raise ValueError(f"image 必须为 base64 PNG 字符串，收到: {type(image).__name__}")
    if not image:
        return ""
    try:
        base64.b64decode(image, validate=True)
    except Exception as error:
        raise ValueError("image 不是合法的 base64 数据") from error
    return image


def new_overlay_id() -> str:
    """生成一个贴片 id（去连字符的 uuid4 hex）。"""
    return uuid.uuid4().hex


def normalize_paste_overlay(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把任意输入规范化为一个贴片字典（纯 JSON 安全的 Python 值）。

    字段缺失时补默认值；数值强制转换；``opacity`` 收敛到 [0, 1]。
    非法输入抛出 :class:`ValueError`，由调用方决定是跳过还是报错。
    """
    if not isinstance(raw, Mapping):
        raise ValueError(f"贴片必须是字典，收到: {type(raw).__name__}")

    width = _to_float(raw.get("width", 0.0), "width")
    height = _to_float(raw.get("height", 0.0), "height")
    if width <= 0 or height <= 0:
        raise ValueError(f"width/height 必须为正数，收到 width={width} height={height}")

    return {
        "id": str(raw.get("id") or "").strip() or new_overlay_id(),
        "name": str(raw.get("name", "")).strip() or "贴片",
        "z": _to_int(raw.get("z", 0), "z"),
        "visible": _to_bool(raw.get("visible", True), "visible"),
        "opacity": _clamp_opacity(_to_float(raw.get("opacity", 1.0), "opacity")),
        "center_x": _to_float(raw.get("center_x", 0.0), "center_x"),
        "center_y": _to_float(raw.get("center_y", 0.0), "center_y"),
        "width": width,
        "height": height,
        "rotation": _to_float(raw.get("rotation", 0.0), "rotation"),
        "flip_h": _to_bool(raw.get("flip_h", False), "flip_h"),
        "flip_v": _to_bool(raw.get("flip_v", False), "flip_v"),
        "image": _validate_image_field(raw.get("image", "")),
    }


def _assign_unique_ids(overlays: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for overlay in overlays:
        overlay_id = str(overlay.get("id") or "").strip()
        while not overlay_id or overlay_id in seen:
            overlay_id = new_overlay_id()
        seen.add(overlay_id)
        overlay["id"] = overlay_id


def serialize_paste_overlays(
    overlays: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把贴片列表规范化为可直接 ``json.dump`` 的纯 Python 结构（写入端使用）。"""
    normalized = [normalize_paste_overlay(item) for item in overlays]
    _assign_unique_ids(normalized)
    return copy.deepcopy(normalized)


def parse_page_paste_overlays(
    image_data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """从页面 JSON 字典里读取 ``paste_overlays`` 键（读取端使用）。

    - 键缺失/为空 → 返回空列表；
    - 根类型错误 → 抛 :class:`ValueError`；
    - 单个贴片非法 → 记录 warning 并跳过，尽量不因一条脏数据拖垮整页加载。
    """
    raw = image_data.get(PAGE_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{PAGE_KEY} 必须是列表，收到: {type(raw).__name__}")
    overlays: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        try:
            overlays.append(normalize_paste_overlay(item))
        except Exception as error:
            logger.warning("跳过无效贴片 #%s: %s", index, error)
    _assign_unique_ids(overlays)
    return overlays


def rgba_overlay_to_png_base64(image_rgba: Any) -> str:
    """RGBA uint8 数组 → base64 PNG 字符串（与 paint/stamp 层同款编码）。"""
    array = np.asarray(image_rgba)
    if array.ndim != 3 or array.shape[2] != 4:
        raise ValueError(f"贴片必须为 RGBA，收到 shape {array.shape}")
    bgra = cv2.cvtColor(array.astype(np.uint8, copy=False), cv2.COLOR_RGBA2BGRA)
    ok, encoded = cv2.imencode(".png", bgra)
    if not ok:
        raise ValueError("贴片 PNG 编码失败")
    return base64.b64encode(encoded).decode("utf-8")


def png_base64_to_rgba_overlay(image_b64: str) -> np.ndarray | None:
    """base64 PNG 字符串 → RGBA uint8 数组；解码失败/非 RGBA 返回 None。"""
    if not isinstance(image_b64, str) or not image_b64:
        return None
    try:
        image_bytes = np.frombuffer(base64.b64decode(image_b64), dtype=np.uint8)
        bgra = cv2.imdecode(image_bytes, cv2.IMREAD_UNCHANGED)
    except Exception:
        return None
    if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
        return None
    array = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
    array.setflags(write=False)
    return array


def compose_paste_overlays(
    overlays: Iterable[Mapping[str, Any]],
    canvas_size: tuple[int, int],
) -> np.ndarray | None:
    """把贴片列表按各自几何 alpha 预合成到一张整页 RGBA 画布上（导出烘焙用）。

    ``canvas_size`` 为 (宽, 高) 源图像素尺寸；返回的数组与 paint/stamp 叠加层同构，
    由后端在渲染文字前与底图做 alpha 合成。无可见贴片时返回 None。
    注意：旋转/翻转的屏幕方向一致性以画布预览为准，后续若发现方向相反，
    只需调整本函数旋转角度的符号。
    """
    width, height = (int(canvas_size[0]), int(canvas_size[1]))
    if width <= 0 or height <= 0:
        return None

    canvas = np.zeros((height, width, 4), dtype=np.uint8)
    drawn = False
    for item in overlays:
        if not isinstance(item, Mapping):
            continue
        if not item.get("visible", True):
            continue
        source = png_base64_to_rgba_overlay(item.get("image", ""))
        if source is None or not np.any(source[..., 3]):
            continue
        target_width = max(1, int(round(float(item.get("width", 0)))))
        target_height = max(1, int(round(float(item.get("height", 0)))))
        center_x = float(item.get("center_x", 0.0))
        center_y = float(item.get("center_y", 0.0))
        rotation = float(item.get("rotation", 0.0))
        flip_h = bool(item.get("flip_h", False))
        flip_v = bool(item.get("flip_v", False))
        try:
            opacity = float(item.get("opacity", 1.0))
        except (TypeError, ValueError):
            opacity = 1.0
        opacity = max(0.0, min(1.0, opacity))
        if opacity <= 0.0:
            continue

        if source.shape[1] != target_width or source.shape[0] != target_height:
            source = cv2.resize(
                source,
                (target_width, target_height),
                interpolation=cv2.INTER_AREA,
            )
        if flip_h and flip_v:
            source = cv2.flip(source, -1)
        elif flip_h:
            source = cv2.flip(source, 1)
        elif flip_v:
            source = cv2.flip(source, 0)

        if opacity < 1.0:
            source = source.copy()
            source[..., 3] = (source[..., 3].astype(np.float32) * opacity).astype(np.uint8)

        matrix = np.zeros((2, 3), dtype=np.float64)
        if rotation:
            theta = math.radians(rotation)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            # 标准图像坐标(原点左上、y 向下)的旋转：dst = center + R(p - center)
            half_w, half_h = target_width / 2.0, target_height / 2.0
            rotated_center_x = cos_t * half_w - sin_t * half_h
            rotated_center_y = sin_t * half_w + cos_t * half_h
            matrix[0, 0], matrix[0, 1] = cos_t, -sin_t
            matrix[1, 0], matrix[1, 1] = sin_t, cos_t
            matrix[0, 2] = center_x - rotated_center_x
            matrix[1, 2] = center_y - rotated_center_y
        else:
            matrix[0, 0] = matrix[1, 1] = 1.0
            matrix[0, 2] = center_x - target_width / 2.0
            matrix[1, 2] = center_y - target_height / 2.0

        patch = cv2.warpAffine(
            source,
            matrix,
            (width, height),
            dst=canvas,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_TRANSPARENT,
        )
        canvas = patch
        drawn = True

    if not drawn or not np.any(canvas[..., 3]):
        return None
    canvas.setflags(write=False)
    return canvas
