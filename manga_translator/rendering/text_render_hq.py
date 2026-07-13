"""
高质量文本渲染模块 - 专门针对低分辨率图片优化

核心思路：
1. 先用大字号（2-4倍）渲染整个文本块
2. 然后整体缩放到目标尺寸
3. 避免逐字符渲染的位置累积误差
4. 使用高质量插值算法保证缩放后的清晰度

优势：
- 大字号渲染时字形更清晰，抗锯齿效果更好
- 整体缩放避免了每个字符的位置误差累积
- 对于低分辨率图片效果显著提升
"""

import logging
from typing import Optional, Tuple

import cv2
import numpy as np

from . import text_render

logger = logging.getLogger(__name__)

def render_text_with_upscale(
    font_size: int,
    text: str,
    width: int,
    height: int,
    alignment: str,
    fg: Tuple[int, int, int],
    bg: Optional[Tuple[int, int, int]],
    line_spacing: float,
    config=None,
    is_horizontal: bool = True,
    upscale_factor: int = None,
    region_count: int = 1,
    # 横排专用参数
    reversed_direction: bool = False,
    target_lang: str = 'en_US',
    hyphenate: bool = True,
    stroke_width: float = None,
    letter_spacing: float = 1.0
) -> np.ndarray:
    """
    使用放大渲染+缩小技术，提高低分辨率下的文本质量
    
    核心原理：
    1. 用大字号渲染整个文本块（避免小字号的锯齿和位置误差）
    2. 整体缩放到目标尺寸（保持字符间的相对位置精确）
    
    Args:
        font_size: 目标字体大小
        upscale_factor: 放大倍数，None则自动计算（小字号用更大倍数）
    """
    
    # 自动计算放大倍数：字号越小，放大倍数越大
    if upscale_factor is None:
        if font_size < 15:
            upscale_factor = 4  # 极小字号用4倍
        elif font_size < 25:
            upscale_factor = 3  # 小字号用3倍
        elif font_size < 35:
            upscale_factor = 2  # 中等字号用2倍
        else:
            upscale_factor = 1  # 大字号不需要放大
    
    def _render(fs: int, w: int, h: int) -> Optional[np.ndarray]:
        """按给定字号/画布尺寸调用底层渲染器；横竖排参数差异在此收口。"""
        if is_horizontal:
            return text_render.put_text_horizontal(
                fs, text, w, h, alignment,
                reversed_direction, fg, bg, target_lang, hyphenate,
                line_spacing, config, region_count, stroke_width, letter_spacing=letter_spacing
            )
        return text_render.put_text_vertical(
            fs, text, h, alignment, fg, bg,
            line_spacing, config, region_count, stroke_width, letter_spacing=letter_spacing
        )

    # 如果不需要放大，直接使用原始渲染
    if upscale_factor == 1:
        return _render(font_size, width, height)

    logger.debug(f"[HQ_RENDER] 使用 {upscale_factor}x 放大渲染 (原始字号={font_size})")

    # 放大所有参数
    upscaled_font_size = font_size * upscale_factor
    upscaled_width = width * upscale_factor
    upscaled_height = height * upscale_factor

    # 用放大的参数渲染整个文本块
    try:
        upscaled_canvas = _render(upscaled_font_size, upscaled_width, upscaled_height)
    except Exception as e:
        logger.error(f"[HQ_RENDER] 放大渲染失败: {e}，回退到普通渲染")
        return _render(font_size, width, height)
    
    if upscaled_canvas is None:
        logger.warning("[HQ_RENDER] 放大渲染返回 None")
        return None
    
    # 整体缩小到目标尺寸
    # 使用 INTER_AREA 算法，对于缩小效果最好（保留细节，减少锯齿）
    original_h, original_w = upscaled_canvas.shape[:2]
    target_w = original_w // upscale_factor
    target_h = original_h // upscale_factor
    
    # 确保目标尺寸至少为1
    target_w = max(1, target_w)
    target_h = max(1, target_h)
    
    # 关键修复：对 RGBA 做 Alpha 预乘再缩放，避免 INTER_AREA 把边缘像素和
    # 透明黑 (0,0,0,0) 混合成灰/黑色晕边。缩放后保持预乘状态交给下游
    # （rendering/__init__.py 的合成链路已改为预乘合成公式）。
    if upscaled_canvas.ndim == 3 and upscaled_canvas.shape[2] == 4:
        premul = upscaled_canvas.copy()
        a_f = premul[:, :, 3].astype(np.float32) / 255.0
        for c in range(3):
            premul[:, :, c] = np.clip(
                premul[:, :, c].astype(np.float32) * a_f, 0, 255
            ).astype(np.uint8)
        downscaled = cv2.resize(premul, (target_w, target_h), interpolation=cv2.INTER_AREA)
        # 还原为非预乘，保持接口一致（下游会再次预乘）
        downscaled_canvas = downscaled.copy()
        a2 = downscaled[:, :, 3].astype(np.float32)
        safe_a = np.where(a2 == 0, 1.0, a2)
        for c in range(3):
            downscaled_canvas[:, :, c] = np.clip(
                downscaled[:, :, c].astype(np.float32) * 255.0 / safe_a, 0, 255
            ).astype(np.uint8)
    else:
        downscaled_canvas = cv2.resize(
            upscaled_canvas,
            (target_w, target_h),
            interpolation=cv2.INTER_AREA
        )
    
    logger.debug(f"[HQ_RENDER] 渲染完成: {original_w}x{original_h} -> {target_w}x{target_h}")
    
    return downscaled_canvas


def should_use_hq_rendering(font_size: int, image_resolution: Tuple[int, int]) -> bool:
    """
    判断是否应该使用高质量渲染
    
    触发条件：
    1. 字体小于35px（小字号容易出现锯齿和位置偏移）
    2. 或者图片分辨率较低（整体渲染质量会受影响）
    
    Args:
        font_size: 字体大小
        image_resolution: 图片分辨率 (width, height)
    
    Returns:
        是否使用高质量渲染
    """
    # 字体小于35px时使用高质量渲染
    if font_size < 35:
        return True
    
    # 图片分辨率较低时也使用（宽度或高度小于1000px）
    width, height = image_resolution
    if width < 1000 or height < 1000:
        return True
    
    return False
