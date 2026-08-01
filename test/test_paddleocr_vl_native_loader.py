import asyncio
import logging
import sys
from pathlib import Path

import numpy as np
import transformers
from transformers.models.paddleocr_vl.configuration_paddleocr_vl import PaddleOCRTextConfig
from transformers.models.paddleocr_vl import processing_paddleocr_vl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manga_translator.ocr.model_paddleocr_vl import ModelPaddleOCRVL
from manga_translator.config import OcrConfig
from manga_translator.utils import Quadrilateral


def test_paddleocr_vl_16_uses_native_transformers_loader(monkeypatch, tmp_path):
    calls = {}

    class FakeModel:
        def to(self, device):
            calls["to"] = device
            return self

        def eval(self):
            calls["eval"] = True

    class FakeTokenizer:
        chat_template = "template"

    class FakeProcessor:
        def __init__(self, **kwargs):
            calls["processor"] = kwargs

    def load_image_processor(path, **kwargs):
        calls["image_processor"] = (Path(path), kwargs)
        return object()

    def load_tokenizer(path, **kwargs):
        calls["tokenizer"] = (Path(path), kwargs)
        return FakeTokenizer()

    def load_model(path, **kwargs):
        calls["model"] = (Path(path), kwargs)
        return FakeModel()

    monkeypatch.setattr(ModelPaddleOCRVL, "_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(PaddleOCRTextConfig, "ignore_keys_at_rope_validation", set())
    monkeypatch.setattr(transformers.AutoImageProcessor, "from_pretrained", staticmethod(load_image_processor))
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", staticmethod(load_tokenizer))
    monkeypatch.setattr(processing_paddleocr_vl, "PaddleOCRVLProcessor", FakeProcessor)
    monkeypatch.setattr(
        transformers.PaddleOCRVLForConditionalGeneration,
        "from_pretrained",
        staticmethod(load_model),
    )

    ocr = ModelPaddleOCRVL.__new__(ModelPaddleOCRVL)

    async def skip_color_model(_device):
        pass

    ocr._load_color_model = skip_color_model
    asyncio.run(ocr._load("cpu"))

    expected_path = tmp_path / "ocr" / "PaddleOCR-VL-1.6"
    expected_loader_call = (
        expected_path,
        {"trust_remote_code": False, "local_files_only": True},
    )
    assert calls["image_processor"] == expected_loader_call
    assert calls["tokenizer"] == expected_loader_call
    assert calls["processor"]["chat_template"] == "template"
    assert PaddleOCRTextConfig.ignore_keys_at_rope_validation == {"mrope_section"}
    assert calls["model"][0] == expected_path
    assert calls["model"][1]["key_mapping"] == {
        r"^visual\.": r"model.visual.",
        r"^mlp_AR\.": r"model.projector.",
        r"^model\.(?!visual\.|projector\.|language_model\.)": r"model.language_model.",
    }
    assert calls["to"] == "cpu"
    assert calls["eval"] is True


def test_paddleocr_vl_recognition_keeps_native_vertical_crop():
    image = np.full((195, 137, 3), 255, dtype=np.uint8)
    image[20:90, 35:100] = 0
    q = Quadrilateral(
        pts=np.array([[0, 0], [136, 0], [136, 194], [0, 194]], dtype=np.int32),
        text="",
        prob=1.0,
    )
    seen = {}
    ocr = ModelPaddleOCRVL.__new__(ModelPaddleOCRVL)
    ocr.logger = logging.getLogger("test.paddleocr_vl")
    ocr.use_gpu = False

    def recognize(region, _prompt):
        seen["recognition"] = region.copy()
        return "お・・・"

    def estimate_colors(source_image, _q, direction):
        seen["color_source"] = source_image
        seen["color_direction"] = direction

    ocr._recognize_single = recognize
    ocr._estimate_colors_48px = estimate_colors
    ocr._cleanup_ocr_memory = lambda *_args: None

    result = asyncio.run(ocr._infer(
        image,
        [q],
        OcrConfig(ignore_bubble=0, ocr_vl_language_hint="Japanese"),
    ))

    assert np.array_equal(seen["recognition"], image)
    assert seen["color_source"] is image
    assert seen["color_direction"] == "v"
    assert result[0].text == "お・・・"
