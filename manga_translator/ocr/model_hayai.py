"""Hayai OCR v2 local OCR backend.

Hayai is a crop-level vision-to-text model.  Its custom Transformers model
and tokenizer are downloaded into the normal ``models/ocr`` tree.  The
SigLIP2 image processor configuration is downloaded there as well, so OCR
does not need to fetch processor metadata at load time.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

import numpy as np
import torch
from PIL import Image

from ..config import OcrConfig
from ..utils.image_modes import normalize_rgb_image
from .model_paddleocr_vl import ModelPaddleOCRVL

_HAYAI_MODEL_SCOPE_BASE_URL = (
    "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/hayai-ocr-v2"
)
_HAYAI_HUGGINGFACE_BASE_URL = (
    "https://huggingface.co/JustANormalTinkerer/hayai-ocr-v2/resolve/"
    "4a4ce477c9a8841f208b94e1d9ed5c0938965e05"
)
_HAYAI_PROCESSOR_ID = "google/siglip2-base-patch16-naflex"
_HAYAI_PROCESSOR_URL = (
    "https://huggingface.co/google/siglip2-base-patch16-naflex/resolve/main/preprocessor_config.json"
)
_HAYAI_PROCESSOR_SHA256 = "1125703e5446d5b6ff4d5893a33bac128cdd21dc12e3dad2469a648fb0ae3bf7"


def _hayai_mapping(filename: str, sha256: str) -> dict[str, Any]:
    return {
        "url": [
            f"{_HAYAI_MODEL_SCOPE_BASE_URL}/{filename}",
            f"{_HAYAI_HUGGINGFACE_BASE_URL}/{filename}",
        ],
        "hash": sha256,
        "file": os.path.join("hayai-ocr-v2", filename),
    }


def _hayai_processor_mapping() -> dict[str, Any]:
    return {
        "url": _HAYAI_PROCESSOR_URL,
        "hash": _HAYAI_PROCESSOR_SHA256,
        "file": os.path.join("hayai-ocr-v2", "processor", "preprocessor_config.json"),
    }


class ModelHayaiOCR(ModelPaddleOCRVL):
    """Hayai OCR v2 using the same crop and result pipeline as PaddleOCR-VL."""

    MODEL_DIR_NAME = "hayai-ocr-v2"
    PROCESSOR_ID = _HAYAI_PROCESSOR_ID
    _MODEL_MAPPING: ClassVar[dict[str, dict[str, Any]]] = {
        "readme": _hayai_mapping(
            "README.md",
            "af82c015c5369047da42a32ec5df2ded87e9b3235a8945bd28d7d4a3fd652e57",
        ),
        "config": _hayai_mapping(
            "config.json",
            "581b762f1dfd55d0108f3f84e3f157bc762524af37fb0c19a7172a18b75582e2",
        ),
        "configuration": _hayai_mapping(
            "configuration_hayai.py",
            "47abd38cf1bae7aef27d01f5b8b4aa0960a7bc625a8afad79c4762ff5e5ed970",
        ),
        "model": _hayai_mapping(
            "model.safetensors",
            "4c645b221db8428cda04991be234c18133bb8861142a3d87cba04c5099b02328",
        ),
        "modeling": _hayai_mapping(
            "modeling_hayai.py",
            "3d78976206549964abd55f776ab059e002adc72d2167daf168e46a12a5f4ae62",
        ),
        "tokenizer": _hayai_mapping(
            "tokenizer.json",
            "f8a0a909c628a684fe463094614e236a8b1d3609e7770f77e7beafaf1056bf13",
        ),
        "tokenizer_config": _hayai_mapping(
            "tokenizer_config.json",
            "6fb6c69afaedf1275872d3e62e276fd4467bd00da7a84cbbb5566a2cd28f58f6",
        ),
        "processor": _hayai_processor_mapping(),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tokenizer = None

    async def _download(self):
        os.makedirs(self._get_file_path(self.MODEL_DIR_NAME), exist_ok=True)
        os.makedirs(self._get_file_path(self.MODEL_DIR_NAME, "processor"), exist_ok=True)
        await super()._download()

    async def _load(self, device: str):
        """Load the local Hayai model and its SigLIP2 image processor."""
        if device == "cuda" and torch.cuda.is_available():
            self.device = "cuda"
            self.use_gpu = True
            model_dtype = torch.bfloat16
        elif device == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            self.device = "mps"
            self.use_gpu = True
            model_dtype = torch.float16
        else:
            self.device = "cpu"
            self.use_gpu = False
            model_dtype = torch.float32

        from transformers import AutoImageProcessor, AutoModel, PreTrainedTokenizerFast

        model_path = self._get_file_path(self.MODEL_DIR_NAME)
        processor_path = self._get_file_path(self.MODEL_DIR_NAME, "processor")
        self.processor = AutoImageProcessor.from_pretrained(
            processor_path,
            local_files_only=True,
        )
        self.tokenizer = PreTrainedTokenizerFast.from_pretrained(
            model_path,
            local_files_only=True,
        )
        self.model = AutoModel.from_pretrained(
            model_path,
            trust_remote_code=True,
            local_files_only=True,
            dtype=model_dtype,
            device_map=self.device if self.device != "cpu" else None,
        )
        if self.device == "cpu":
            self.model = self.model.to(self.device)
        self.model.eval()

    async def _unload(self):
        await super()._unload()
        self.tokenizer = None

    def _build_ocr_prompt(self, config: OcrConfig) -> str:
        """Hayai does not use a text prompt; keep the shared call signature."""
        return ""

    def _recognize_single(self, img: np.ndarray, prompt_text: str) -> str:
        """Run Hayai's crop-level image-to-text generation."""
        if isinstance(img, np.ndarray):
            pil_img = Image.fromarray(img)
        else:
            pil_img = img
        pil_img = normalize_rgb_image(pil_img)

        inputs = self.processor(
            images=[pil_img],
            max_num_patches=256,
            return_tensors="pt",
        )
        inputs = {
            key: value.to(self.device) if isinstance(value, torch.Tensor) else value
            for key, value in inputs.items()
        }
        with torch.no_grad():
            generated = self.model.generate(
                **inputs,
                tokenizer=self.tokenizer,
                max_new_tokens=128,
                num_beams=4,
                repetition_penalty=1.0,
            )
        if isinstance(generated, str):
            return generated.strip()
        if isinstance(generated, (list, tuple)) and generated and isinstance(generated[0], str):
            return generated[0].strip()
        return self.tokenizer.decode(generated[0], skip_special_tokens=True).strip()
