"""合成层：alpha 位图上色/描边/贴图、仿射特效（斜体/旋转/镜像）与其纯几何对应。

不持有渲染状态，只做 numpy/cv2 图层运算与 TextStyle 样式解析。
"""
import math
from typing import Optional, Tuple

import cv2
import numpy as np

from ...utils import parse_color
from ..rich_text import TextStyle

def add_color(bw_char_map, color, stroke_char_map, stroke_color):
    """合成文字和描边为 RGBA 图层。

    关键做法（解决灰边/脏边，同时防止描边层缺失时整段透明）：
    1. 强制 stroke_alpha = max(stroke_alpha, text_alpha)，保证描边在空间上完全
       覆盖文字的所有抗锯齿像素，消除因两次独立光栅化造成的对齐偏差；
       同时保证描边层为全零（stroke_ratio=0 等场景）时输出 alpha 仍含正文。
    2. 将描边视作文字的"底色"，抗锯齿过渡像素直接在描边纯色上混合，而不是
       两个半透明层的 over 叠加 —— 这是原来灰边的根源。
    """
    H, W = bw_char_map.shape[:2]
    if bw_char_map.size == 0:
        return np.zeros((H, W, 4), dtype=np.uint8)

    out = np.zeros((H, W, 4), dtype=np.uint8)
    color_arr = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)

    # 无描边：直接输出文字图层
    if stroke_color is None or stroke_char_map is None:
        out[:, :, :3] = np.clip(color_arr, 0, 255).astype(np.uint8)
        out[:, :, 3] = bw_char_map
        return out

    stroke_color_arr = np.asarray(stroke_color, dtype=np.float32).reshape(1, 1, 3)

    # 1) 强制 stroke_alpha >= text_alpha —— 描边层全零时输出退化为纯文字
    text_alpha_u8 = bw_char_map
    stroke_alpha_u8 = np.maximum(stroke_char_map, text_alpha_u8)

    # 2) 文字在描边纯色底上混合（局部不透明，无半透明层叠加）
    text_af = (text_alpha_u8.astype(np.float32) / 255.0)[:, :, None]
    rgb = color_arr * text_af + stroke_color_arr * (1.0 - text_af)

    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[:, :, 3] = stroke_alpha_u8
    return out


def _parse_rgb(value, fallback=(0, 0, 0)) -> Tuple[int, int, int]:
    """F16：薄委托 utils.generic.parse_color（#RGB/#RRGGBB/序列、钳制、回退）。

    在公共 helper 之上保证恒返回 RGB 三元组：fallback 本身也允许是
    hex 字符串/序列，两级都解析失败时回退黑色。
    """
    color = parse_color(value, None)
    if color is None:
        color = parse_color(fallback, None)
    return tuple(color) if color is not None else (0, 0, 0)


def _style_font_size(base_font_size: int, style: TextStyle) -> int:
    if style.font_size is not None:
        return max(1, int(round(style.font_size)))
    return max(1, int(round(base_font_size * (style.scale or 1.0))))


def _style_fill_color(style: TextStyle, fallback) -> Tuple[int, int, int]:
    return _parse_rgb(style.color, fallback)


def _style_stroke_color(style: TextStyle, fallback):
    if style.stroke and style.stroke.color:
        return _parse_rgb(style.stroke.color, fallback or (0, 0, 0))
    return None if fallback is None else _parse_rgb(fallback, (0, 0, 0))


def _style_stroke_ratio(style: TextStyle, font_size: int, global_stroke_ratio: float, global_stroke_color) -> float:
    if style.stroke:
        if style.stroke.width is not None:
            return max(0.0, float(style.stroke.width))
        return max(float(global_stroke_ratio), 0.07)
    return max(float(global_stroke_ratio), 0.0) if global_stroke_color is not None else 0.0


def _rgba_from_alpha_pair(text_alpha: np.ndarray, border_alpha: Optional[np.ndarray], fill_color, stroke_color):
    if text_alpha is None or text_alpha.size == 0:
        return None
    if stroke_color is None or border_alpha is None or border_alpha.size == 0:
        border_alpha = None
    return add_color(text_alpha, fill_color, border_alpha, stroke_color)


def _stroke_alpha_from_text_alpha(text_alpha: np.ndarray, stroke_px: int):
    if text_alpha is None or text_alpha.size == 0:
        return np.zeros((0, 0), dtype=np.uint8), 0, 0
    pad = max(1, int(stroke_px)) + 1
    padded = cv2.copyMakeBorder(text_alpha, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    fg_mask = (padded >= 128).astype(np.uint8) * 255
    bg_mask = cv2.bitwise_not(fg_mask)
    dist = cv2.distanceTransform(bg_mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    stroke_region = np.clip((stroke_px + 0.5 - dist), 0.0, 1.0)
    stroke_alpha = (stroke_region * 255).astype(np.uint8)
    return np.maximum(stroke_alpha, padded), -pad, -pad


def _crop_rgba_fixed(canvas: np.ndarray, x0: int, x1: int, y0: int, y1: int):
    """按预定包络矩形裁切（不按墨迹紧裁）。

    富文本路径的"测量 == 输出面尺寸"契约：包络由度量几何给定，测量与
    绘制共用同一套数字，输出面必须严格等于测量框，偏移/切变等把墨迹
    推到框内任意位置都不会被裁掉（全透明时仍返回 None 表示无可绘内容）。
    """
    if canvas is None or canvas.size == 0 or canvas.shape[2] != 4:
        return None
    x0 = max(0, min(int(x0), canvas.shape[1]))
    x1 = max(x0, min(int(x1), canvas.shape[1]))
    y0 = max(0, min(int(y0), canvas.shape[0]))
    y1 = max(y0, min(int(y1), canvas.shape[0]))
    if x1 <= x0 or y1 <= y0:
        return None
    region = canvas[y0:y1, x0:x1]
    return region if region[:, :, 3].any() else None


def _stroke_pad_px(font_size: int, stroke_ratio: float) -> int:
    """描边给字形图层带来的四边外扩（像素）。

    与 _stroke_alpha_from_text_alpha 的 pad 公式一致，度量与绘制共用。
    """
    if stroke_ratio <= 0:
        return 0
    stroke_px = max(int(stroke_ratio * font_size), 1)
    return max(1, int(stroke_px)) + 1


def _paste_rgba(dst: np.ndarray, src: np.ndarray, x: int, y: int):
    if src is None or src.size == 0:
        return
    rows, width = src.shape[:2]
    x2, y2 = x + width, y + rows
    sx1, sy1, sx2, sy2 = max(0, x), max(0, y), min(dst.shape[1], x2), min(dst.shape[0], y2)
    if sx1 >= sx2 or sy1 >= sy2:
        return
    bx1, by1 = sx1 - x, sy1 - y
    src_view = src[by1:by1 + (sy2 - sy1), bx1:bx1 + (sx2 - sx1)]
    dst_view = dst[sy1:sy2, sx1:sx2]
    if not dst_view[:, :, 3].any():
        # F26 快路径：目标区域完全透明（竖排逐字符粘贴的常见情形），
        # alpha-over 退化为直接覆盖，跳过全量浮点混合
        dst_view[:] = src_view
        return
    src_crop = src_view.astype(np.float32)
    dst_crop = dst_view.astype(np.float32)
    src_a = src_crop[:, :, 3:4] / 255.0
    dst_a = dst_crop[:, :, 3:4] / 255.0
    out_a = src_a + dst_a * (1.0 - src_a)
    safe_a = np.where(out_a <= 0, 1.0, out_a)
    out_rgb = (src_crop[:, :, :3] * src_a + dst_crop[:, :, :3] * dst_a * (1.0 - src_a)) / safe_a
    out = np.zeros_like(dst_crop)
    out[:, :, :3] = np.clip(out_rgb, 0, 255)
    out[:, :, 3:4] = np.clip(out_a * 255.0, 0, 255)
    dst[sy1:sy2, sx1:sx2] = out.astype(np.uint8)


def _warp_geometry(height: int, width: int, matrix: np.ndarray):
    """仿射变换的纯几何计算：输入框 (height, width) 经 matrix 变换后的
    输出框尺寸与偏移。渲染（_warp_rgba_layer）与度量共用，保证几何一致。"""
    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]]).reshape(-1, 1, 2)
    transformed = cv2.transform(corners, matrix).reshape(-1, 2)
    min_x, min_y = transformed.min(axis=0)
    max_x, max_y = transformed.max(axis=0)
    out_w = max(1, int(math.ceil(max_x - min_x)))
    out_h = max(1, int(math.ceil(max_y - min_y)))
    return out_h, out_w, min_x, min_y


def _warp_rgba_layer(layer: np.ndarray, matrix: np.ndarray):
    if layer is None or layer.size == 0:
        return layer, 0.0, 0.0
    h, w = layer.shape[:2]
    out_h, out_w, min_x, min_y = _warp_geometry(h, w, matrix)
    adjusted = matrix.copy()
    adjusted[0, 2] -= min_x
    adjusted[1, 2] -= min_y
    warped = cv2.warpAffine(
        layer,
        adjusted,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    return warped, float(min_x), float(min_y)


DEFAULT_ITALIC_ANGLE = 15.0
_MAX_ITALIC_ANGLE = 85.0


def _style_italic_shear(style: TextStyle) -> float:
    """italic 协议值 → 图层水平切变系数。

    True = 参考实现（mtu-json-gui）斜体按钮的默认 15°；数字 = 角度（度）。
    图层坐标 y 向下，向右倾斜的切变系数为 -tan(angle)。竖排横躺字符的位图
    在 _vertical_base 已按 90° 预旋转，对旋转后的位图施加同一切变矩阵 M
    恰好满足 M·R = R·S（S 为参考实现在旋转坐标系内的换轴切变），因此
    所有字符统一用本系数，无需按是否旋转分支。
    """
    italic = style.italic
    if italic is True:
        angle = DEFAULT_ITALIC_ANGLE
    elif isinstance(italic, (int, float)) and not isinstance(italic, bool):
        angle = float(italic)
    else:
        return 0.0
    angle = max(-_MAX_ITALIC_ANGLE, min(_MAX_ITALIC_ANGLE, angle))
    if not angle:
        return 0.0
    return -math.tan(math.radians(angle))


def _italic_shear_matrix(shear: float, height: float) -> np.ndarray:
    """斜体切变矩阵，轴在图层竖直中心。

    参考实现先 translate 到字符中心再 skew（上半右移、下半左移对称）；
    平移项 -shear·h/2 把切变轴从图层顶边挪到中线，经 _warp_rgba_layer /
    _warp_geometry 的 min_x 归一后，dx 会带回这一平移，绘制位置随之对称，
    否则字身相对槽位整体滑向一侧（横排左滑 tan(角度)·ascent）。
    """
    return np.float32([[1.0, shear, -shear * (float(height) / 2.0)], [0.0, 1.0, 0.0]])


def _apply_style_layer_effects(layer: np.ndarray, style: TextStyle, font_size: int):
    if layer is None or layer.size == 0:
        return layer, 0.0, 0.0

    result = layer
    offset_x = 0.0
    offset_y = 0.0

    if style.transform.mirror_x:
        result = cv2.flip(result, 1)
    if style.transform.mirror_y:
        result = cv2.flip(result, 0)

    shear = _style_italic_shear(style)
    if shear:
        matrix = _italic_shear_matrix(shear, result.shape[0])
        result, dx, dy = _warp_rgba_layer(result, matrix)
        offset_x += dx
        offset_y += dy

    if style.transform.rotation:
        h, w = result.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), float(style.transform.rotation), 1.0)
        result, dx, dy = _warp_rgba_layer(result, matrix)
        offset_x += dx
        offset_y += dy

    return result, offset_x, offset_y


def _style_layer_effects_geometry(height: int, width: int, style: TextStyle):
    """_apply_style_layer_effects 的纯几何版本（度量用，F21）。

    mirror 不改变尺寸；italic/rotation 用与渲染路径相同的矩阵经 _warp_geometry
    按角点计算输出框与偏移，不做实际 warp。返回 (height, width, offset_x, offset_y)。
    """
    if height <= 0 or width <= 0:
        return height, width, 0.0, 0.0

    offset_x = 0.0
    offset_y = 0.0

    shear = _style_italic_shear(style)
    if shear:
        matrix = _italic_shear_matrix(shear, height)
        height, width, dx, dy = _warp_geometry(height, width, matrix)
        offset_x += float(dx)
        offset_y += float(dy)

    if style.transform.rotation:
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), float(style.transform.rotation), 1.0)
        height, width, dx, dy = _warp_geometry(height, width, matrix)
        offset_x += float(dx)
        offset_y += float(dy)

    return height, width, offset_x, offset_y


def _draw_rgba_disc(dst: np.ndarray, center_x: float, center_y: float, radius: float, color):
    radius = max(1, int(round(radius)))
    size = radius * 2 + 3
    alpha = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(alpha, (size // 2, size // 2), radius, 255, -1, lineType=cv2.LINE_AA)
    layer = np.zeros((size, size, 4), dtype=np.uint8)
    layer[:, :, :3] = np.asarray(_parse_rgb(color), dtype=np.uint8)
    layer[:, :, 3] = alpha
    _paste_rgba(dst, layer, int(round(center_x)) - size // 2, int(round(center_y)) - size // 2)


def _paste_bitmap(canvas: np.ndarray, bitmap_arr: np.ndarray, x: int, y: int, mode: str = 'max'):
    if bitmap_arr is None or bitmap_arr.size == 0:
        return
    rows, width = bitmap_arr.shape
    x2, y2 = x + width, y + rows
    sx1, sy1, sx2, sy2 = max(0, x), max(0, y), min(canvas.shape[1], x2), min(canvas.shape[0], y2)
    if sx1 >= sx2 or sy1 >= sy2:
        return
    bx1, by1 = sx1 - x, sy1 - y
    bitmap = bitmap_arr[by1:by1 + (sy2 - sy1), bx1:bx1 + (sx2 - sx1)]
    target = canvas[sy1:sy2, sx1:sx2]
    if mode == 'add':
        # 使用 cv2.add 避免 numpy uint8 加法溢出导致的脏斑点
        cv2.add(target, bitmap, dst=target)
    else:
        np.maximum(target, bitmap, out=target)


def _crop_pair(text_canvas: np.ndarray, border_canvas: np.ndarray):
    combined = cv2.add(text_canvas, border_canvas)
    if not np.any(combined):
        return None
    x, y, w, h = cv2.boundingRect(combined)
    return None if w == 0 or h == 0 else (text_canvas[y:y+h, x:x+w], border_canvas[y:y+h, x:x+w], x, y, w, h)


def _glyph_pair_rgba(char_bitmap: np.ndarray, border_bitmap: Optional[np.ndarray], fill, stroke):
    if char_bitmap is None or char_bitmap.size == 0:
        return None
    if border_bitmap is None or border_bitmap.size == 0 or stroke is None:
        return _rgba_from_alpha_pair(char_bitmap, None, fill, None), 0, 0
    border_x = -round((border_bitmap.shape[1] - char_bitmap.shape[1]) / 2.0)
    border_y = -round((border_bitmap.shape[0] - char_bitmap.shape[0]) / 2.0)
    left = min(0, border_x)
    top = min(0, border_y)
    right = max(char_bitmap.shape[1], border_x + border_bitmap.shape[1])
    bottom = max(char_bitmap.shape[0], border_y + border_bitmap.shape[0])
    text_alpha = np.zeros((bottom - top, right - left), dtype=np.uint8)
    border_alpha = np.zeros_like(text_alpha)
    _paste_bitmap(text_alpha, char_bitmap, -left, -top)
    _paste_bitmap(border_alpha, border_bitmap, border_x - left, border_y - top, mode='add')
    return _rgba_from_alpha_pair(text_alpha, border_alpha, fill, stroke), left, top


def _bitmap_ink_rect(bitmap: Optional[np.ndarray]) -> Optional[Tuple[int, int, int, int]]:
    if bitmap is None or bitmap.size == 0:
        return None
    nz = cv2.findNonZero(bitmap)
    return None if nz is None else tuple(map(int, cv2.boundingRect(nz)))


def _stroke_bitmap_from_alpha(text_alpha: np.ndarray, font_size: int, stroke_ratio: float):
    stroke_px = max(int(stroke_ratio * font_size), 1)
    bitmap, _, _ = _stroke_alpha_from_text_alpha(text_alpha, stroke_px)
    return None if bitmap.size == 0 else bitmap
