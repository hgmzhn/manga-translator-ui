import base64
import struct
from typing import Dict, List, Annotated

import cv2
import numpy as np
from pydantic import BaseModel, Field, WithJsonSchema

from manga_translator import Context
from manga_translator.utils import TextBlock


#input:PIL,
#result:PIL
#img_colorized: PIL
#upscaled:PIL
#img_rgb:array
#img_alpha:None
#textlines:list[Quadrilateral]
#text_regions:list[TextBlock]
#translations: map[str, arr[str]]
#img_inpainted: array
#gimp_mask:array
#img_rendered: array
#mask_raw: array
#mask:array
NumpyNdarray = Annotated[
    np.ndarray,
    WithJsonSchema({'type': 'string', "format": "base64","examples": ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAA..."]}),
]

class Translation(BaseModel):
    lines: list
    texts: list[str]
    text: str
    translation: str
    angle: float | int
    font_size: int
    fg_colors: list[int]
    bg_colors: list[int]
    direction: str
    alignment: str
    target_lang: str
    source_lang: str
    line_spacing: float
    default_stroke_width: float
    adjust_bg_color: bool
    prob: float

    def to_bytes(self):
        coords_bytes = struct.pack('4i', self.minX, self.minY, self.maxX, self.maxY)
        is_bulleted_list_byte = struct.pack('?', self.is_bulleted_list)
        angle_bytes = struct.pack('f', float(self.angle) if isinstance(self.angle, int) else self.angle)
        prob_bytes = struct.pack('f', self.prob)
        fg = struct.pack('3B', self.text_color.fg[0], self.text_color.fg[1], self.text_color.fg[2])
        bg = struct.pack('3B', self.text_color.bg[0], self.text_color.bg[1], self.text_color.bg[2])
        text_bytes = struct.pack('i', len(self.text.items()))
        for key, value in self.text.items():
            text_bytes += struct.pack('I', len(key.encode('utf-8'))) + key.encode('utf-8')
            text_bytes += struct.pack('I', len(value.encode('utf-8'))) + value.encode('utf-8')
        # background_bytes 已移除
        return coords_bytes + is_bulleted_list_byte + angle_bytes + prob_bytes + fg + bg + text_bytes

class TranslationResponse(BaseModel):
    regions: List[Translation]
    original_width: int
    original_height: int
    upscale_ratio: float | None = None
    upscaler: str | None = None
    colorizer: str | None = None
    mask_raw: str | None = None
    mask_is_refined: bool = False

    def to_bytes(self):
        items = [v.to_bytes() for v in self.regions]
        return struct.pack('i', len(items)) + b''.join(items)

def to_translation(ctx: Context) -> TranslationResponse:
    text_regions:list[TextBlock] = ctx.text_regions
    results = []
    for i, blk in enumerate(text_regions):
        # 直接使用 region.to_dict()，与主翻译程序保持一致
        region_dict = text_regions[i].to_dict()
        results.append(Translation(**region_dict))

    # 获取图片尺寸
    if ctx.input is not None:
        original_width, original_height = ctx.input.size
    elif ctx.result is not None:
        original_width, original_height = ctx.result.size
    elif hasattr(ctx, 'img_rgb') and ctx.img_rgb is not None:
        original_height, original_width = ctx.img_rgb.shape[:2]
    else:
        # 默认值
        original_width, original_height = 0, 0
    
    # 构建响应数据
    response_data = {
        'regions': results,
        'original_width': original_width,
        'original_height': original_height
    }
    
    # 添加超分和上色配置信息（如果有）
    if hasattr(ctx, '_config') and ctx._config:
        config = ctx._config
        if hasattr(config, 'upscale') and config.upscale and hasattr(config.upscale, 'upscale_ratio'):
            if config.upscale.upscale_ratio:
                response_data['upscale_ratio'] = config.upscale.upscale_ratio
                if hasattr(config.upscale, 'upscaler') and config.upscale.upscaler:
                    response_data['upscaler'] = config.upscale.upscaler
        
        if hasattr(config, 'colorizer') and config.colorizer:
            if hasattr(config.colorizer, 'colorizer') and config.colorizer.colorizer and config.colorizer.colorizer != 'none':
                response_data['colorizer'] = config.colorizer.colorizer
    
    # 添加蒙版（如果有）
    if hasattr(ctx, 'mask_raw') and ctx.mask_raw is not None:
        try:
            import base64
            import cv2
            _, buffer = cv2.imencode('.png', ctx.mask_raw)
            mask_base64 = base64.b64encode(buffer).decode('utf-8')
            response_data['mask_raw'] = mask_base64
        except Exception as e:
            pass
    
    # 添加 mask_is_refined 标志（如果有）
    if hasattr(ctx, 'mask_is_refined') and ctx.mask_is_refined is not None:
        response_data['mask_is_refined'] = ctx.mask_is_refined
    else:
        response_data['mask_is_refined'] = False

    return TranslationResponse(**response_data)
