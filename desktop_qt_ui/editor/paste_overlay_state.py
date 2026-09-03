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
    合成顺序按各贴片 ``z`` 升序（z 大的在上）；采用 source-over alpha 合成，
    半透明贴片不会覆盖下层；缩放由仿射矩阵完成，不产生中间大图。
    注意：旋转/翻转的屏幕方向一致性以画布预览为准，后续若发现方向相反，
    只需调整本函数旋转角度的符号。
    """
    width, height = (int(canvas_size[0]), int(canvas_size[1]))
    if width <= 0 or height <= 0:
        return None

    # 画布全程使用预乘 alpha 累积：仿射前把 RGB 预乘 alpha，插值就不会把
    # 透明边缘的颜色“渗深/渗黑”；合成用 pre-mul source-over，最后反预乘输出。
    canvas = np.zeros((height, width, 4), dtype=np.float32)

    def _blend_premultiplied(base: np.ndarray, patch: np.ndarray) -> np.ndarray:
        """pre-mul source-over：RGB 已预乘，alpha 通道为 0..255 原始值。"""
        patch_coverage = patch[..., 3:4] / 255.0
        merged = np.empty_like(base)
        merged[..., :3] = patch[..., :3] + base[..., :3] * (1.0 - patch_coverage)
        merged[..., 3:4] = patch[..., 3:4] + base[..., 3:4] * (1.0 - patch_coverage)
        return merged

    # 单张贴片图片体积极限：防御手工构造的超大 base64（见 CodeRabbit CWE-400）
    max_image_chars = 24_000_000
    # 解码后像素最大边长：超过即视为异常工程，跳过该贴片
    max_source_side = 8192

    items = [
        item
        for item in overlays
        if isinstance(item, Mapping) and item.get("visible", True)
    ]
    # z 升序合成：低 z 先画（在下层），同 z 保持列表顺序
    items.sort(key=lambda item: float(item.get("z", 0)) or 0.0)

    drawn = False
    for item in items:
        image_b64 = item.get("image", "")
        if not isinstance(image_b64, str) or not image_b64:
            continue
        if len(image_b64) > max_image_chars:
            logger.warning("跳过超大贴片图片数据（>%s 字符 base64）", max_image_chars)
            continue
        source = png_base64_to_rgba_overlay(image_b64)
        if source is None or not np.any(source[..., 3]):
            continue
        source_h, source_w = source.shape[:2]
        if source_w <= 0 or source_h <= 0 or max(source_w, source_h) > max_source_side:
            logger.warning("跳过异常尺寸贴片 (%dx%d)", source_w, source_h)
            continue

        target_width = float(item.get("width", source_w))
        target_height = float(item.get("height", source_h))
        if target_width <= 0 or target_height <= 0:
            continue
        center_x = float(item.get("center_x", 0.0))
        center_y = float(item.get("center_y", 0.0))
        rotation = float(item.get("rotation", 0.0))
        flip_h = -1.0 if item.get("flip_h") else 1.0
        flip_v = -1.0 if item.get("flip_v") else 1.0
        try:
            opacity = float(item.get("opacity", 1.0))
        except (TypeError, ValueError):
            opacity = 1.0
        opacity = max(0.0, min(1.0, opacity))
        if opacity <= 0.0:
            continue
        if opacity < 1.0:
            source = source.copy()
            source[..., 3] = (source[..., 3].astype(np.float32) * opacity).astype(np.uint8)

        # 预乘：RGB × (alpha/255)，参与插值的 RGB 是颜色×覆盖度，透明处为 0
        premul_source = np.empty((source_h, source_w, 4), dtype=np.float32)
        premul_source[..., 3:4] = source[..., 3:4].astype(np.float32)
        premul_source[..., :3] = (
            source[..., :3].astype(np.float32)
            * (premul_source[..., 3:4] / 255.0)
        )

        # 仿射矩阵：p_scene = center + R * S * (p_source - source_center)
        # S 内置非等比缩放与水平/垂直翻转，无需先放大中间图
        scale_x = flip_h * (target_width / source_w)
        scale_y = flip_v * (target_height / source_h)
        theta = math.radians(rotation)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        a00 = cos_t * scale_x
        a01 = -sin_t * scale_y
        a10 = sin_t * scale_x
        a11 = cos_t * scale_y
        offset_x = center_x - (a00 * (source_w / 2.0) + a01 * (source_h / 2.0))
        offset_y = center_y - (a10 * (source_w / 2.0) + a11 * (source_h / 2.0))
        matrix = np.array(
            [[a00, a01, offset_x], [a10, a11, offset_y]], dtype=np.float64
        )

        # 只对变换后的包围盒做 warp+blend：避免大页面上每张贴片都分配一张整页
        # float32 画布（8192² 单张就 ~1GiB，多贴片直接 OOM）
        src_corners = np.array(
            [[0.0, 0.0], [source_w, 0.0], [source_w, source_h], [0.0, source_h]],
            dtype=np.float64,
        )
        transformed_x = (
            matrix[0, 0] * src_corners[:, 0]
            + matrix[0, 1] * src_corners[:, 1]
            + matrix[0, 2]
        )
        transformed_y = (
            matrix[1, 0] * src_corners[:, 0]
            + matrix[1, 1] * src_corners[:, 1]
            + matrix[1, 2]
        )
        min_x = max(0, int(math.floor(transformed_x.min())) - 1)
        max_x = min(width, int(math.ceil(transformed_x.max())) + 1)
        min_y = max(0, int(math.floor(transformed_y.min())) - 1)
        max_y = min(height, int(math.ceil(transformed_y.max())) + 1)
        if max_x <= min_x or max_y <= min_y:
            # 贴片完全落在画布外
            continue

        box_width = max_x - min_x
        box_height = max_y - min_y
        patch = np.zeros((box_height, box_width, 4), dtype=np.float32)
        sub_matrix = matrix.copy()
        sub_matrix[0, 2] -= min_x
        sub_matrix[1, 2] -= min_y
        patch = cv2.warpAffine(
            premul_source,
            sub_matrix,
            (box_width, box_height),
            dst=patch,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_TRANSPARENT,
        )
        canvas[min_y:max_y, min_x:max_x] = _blend_premultiplied(
            canvas[min_y:max_y, min_x:max_x], patch
        )
        drawn = True

    if not drawn:
        return None

    # 反预乘：直通 alpha 输出，供后端再按 straight alpha 与底图合成
    alpha_raw = canvas[..., 3:4]
    coverage = alpha_raw / 255.0
    safe = np.maximum(coverage, 1e-6)
    output = np.empty((height, width, 4), dtype=np.uint8)
    output[..., :3] = np.clip(canvas[..., :3] / safe, 0, 255)
    output[..., 3:4] = np.clip(alpha_raw, 0, 255)
    if not np.any(output[..., 3]):
        return None
    output.setflags(write=False)
    return output
