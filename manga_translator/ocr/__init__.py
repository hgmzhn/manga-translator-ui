from typing import Any, List, Optional

import numpy as np

from ..config import Ocr, OcrConfig
from ..utils import Quadrilateral
from .common import CommonOCR, OfflineOCR
from .model_32px import Model32pxOCR
from .model_48px import Model48pxOCR
from .model_48px_ctc import Model48pxCTCOCR

# ModelMangaOCR 延迟导入，避免未使用时下载模型
from .model_paddleocr import (
    ModelPaddleOCR,
    ModelPaddleOCRKorean,
    ModelPaddleOCRLatin,
    ModelPaddleOCRThai,
)


def _get_manga_ocr_class():
    """延迟导入 ModelMangaOCR，只有在真正使用 mocr 时才导入"""
    from .model_manga_ocr import ModelMangaOCR
    return ModelMangaOCR


def _get_paddleocr_vl_class():
    """延迟导入 ModelPaddleOCRVL，只有在真正使用 paddleocr_vl 时才导入"""
    from .model_paddleocr_vl import ModelPaddleOCRVL
    return ModelPaddleOCRVL

def _get_hayai_ocr_class():
    """延迟导入 ModelHayaiOCR，只有在真正使用 hayai_ocr_v2 时才导入"""
    from .model_hayai import ModelHayaiOCR
    return ModelHayaiOCR


def _get_openai_ocr_class():
    """延迟导入 ModelOpenAIOCR，只有在真正使用 openai_ocr 时才导入"""
    from .model_api_ocr import ModelOpenAIOCR
    return ModelOpenAIOCR


def _get_gemini_ocr_class():
    """延迟导入 ModelGeminiOCR，只有在真正使用 gemini_ocr 时才导入"""
    from .model_api_ocr import ModelGeminiOCR
    return ModelGeminiOCR

OCRS = {
    Ocr.ocr32px: Model32pxOCR,
    Ocr.ocr48px: Model48pxOCR,
    Ocr.ocr48px_ctc: Model48pxCTCOCR,
    Ocr.mocr: _get_manga_ocr_class,  # 延迟导入
    Ocr.paddleocr: ModelPaddleOCR,
    Ocr.paddleocr_korean: ModelPaddleOCRKorean,
    Ocr.paddleocr_latin: ModelPaddleOCRLatin,
    Ocr.paddleocr_thai: ModelPaddleOCRThai,
    Ocr.paddleocr_vl: _get_paddleocr_vl_class,  # 延迟导入 PaddleOCR-VL
    Ocr.hayai_ocr_v2: _get_hayai_ocr_class,  # 延迟导入 Hayai OCR
    Ocr.openai_ocr: _get_openai_ocr_class,
    Ocr.gemini_ocr: _get_gemini_ocr_class,
}
ocr_cache = {}


def _resolve_ocr_config(config: Optional[Any]) -> Any:
    if config is None:
        return OcrConfig()
    if isinstance(config, OcrConfig):
        return config
    nested_ocr_config = getattr(config, 'ocr', None)
    if isinstance(nested_ocr_config, OcrConfig):
        return nested_ocr_config
    if nested_ocr_config is not None and hasattr(nested_ocr_config, 'ignore_bubble'):
        return nested_ocr_config
    if isinstance(config, Ocr):
        return OcrConfig(ocr=config)
    return config

def get_ocr(key: Ocr, *args, **kwargs) -> CommonOCR:
    if key not in OCRS:
        raise ValueError(f'Could not find OCR for: "{key}". Choose from the following: %s' % ','.join(OCRS))
    # Use cache to avoid reloading models in the same translation session
    if key not in ocr_cache:
        ocr_class = OCRS[key]
        # 处理延迟导入的情况
        if not isinstance(ocr_class, type):
            ocr_class = ocr_class()  # 调用函数获取真正的类
        ocr_cache[key] = ocr_class(*args, **kwargs)
    return ocr_cache[key]

async def prepare(ocr_key: Ocr, device: str = 'cpu'):
    ocr = get_ocr(ocr_key)
    if isinstance(ocr, OfflineOCR):
        await ocr.download()
        await ocr.load(device)

async def dispatch(
    ocr_key: Ocr,
    image: np.ndarray,
    regions: List[Quadrilateral],
    config: Optional[OcrConfig] = None,
    device: str = 'cpu',
    verbose: bool = False,
    runtime_config=None,
) -> List[Quadrilateral]:
    ocr = get_ocr(ocr_key)
    if isinstance(ocr, OfflineOCR):
        await ocr.load(device)
    runtime_config = runtime_config or config
    ocr_config = _resolve_ocr_config(config)
    if getattr(ocr, "SUPPORTS_RUNTIME_CONFIG", False):
        return await ocr.recognize(
            image,
            regions,
            ocr_config,
            verbose,
            runtime_config=runtime_config,
        )
    return await ocr.recognize(image, regions, ocr_config, verbose)

async def unload(ocr_key: Ocr):
    ocr = ocr_cache.pop(ocr_key, None)
    if isinstance(ocr, OfflineOCR):
        await ocr.unload()
