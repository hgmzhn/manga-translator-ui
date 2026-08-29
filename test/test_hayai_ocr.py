import asyncio
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import torch
import transformers

from manga_translator.config import Ocr
from manga_translator.ocr.model_hayai import ModelHayaiOCR


def test_hayai_model_is_registered_and_uses_local_model_tree():
    assert Ocr.hayai_ocr_v2.value == "hayai_ocr_v2"
    assert ModelHayaiOCR.MODEL_DIR_NAME == "hayai-ocr-v2"
    assert ModelHayaiOCR._MODEL_MAPPING["model"]["file"] == str(
        Path("hayai-ocr-v2") / "model.safetensors"
    )
    assert ModelHayaiOCR._MODEL_MAPPING["processor"]["file"] == str(
        Path("hayai-ocr-v2") / "processor" / "preprocessor_config.json"
    )
    assert "color_model" in ModelHayaiOCR._MODEL_MAPPING
    assert "color_dict" in ModelHayaiOCR._MODEL_MAPPING
    assert ModelHayaiOCR._MODEL_MAPPING["color_model"]["url"][0].endswith(
        "ocr_ar_48px.ckpt"
    )
    assert ModelHayaiOCR._MODEL_MAPPING["color_dict"]["url"][0].endswith(
        "alphabet-all-v7.txt"
    )


def test_hayai_loader_uses_local_model_and_processor(monkeypatch, tmp_path):
    calls = {}

    class FakeModel:
        def to(self, device):
            calls["to"] = device
            return self

        def eval(self):
            calls["eval"] = True
            return self

    class FakeTokenizer:
        pass

    def load_processor(path, **kwargs):
        calls["processor"] = (Path(path), kwargs)
        return object()

    def load_tokenizer(path, **kwargs):
        calls["tokenizer"] = (Path(path), kwargs)
        return FakeTokenizer()

    def load_model(path, **kwargs):
        calls["model"] = (Path(path), kwargs)
        return FakeModel()

    monkeypatch.setattr(ModelHayaiOCR, "_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(
        transformers.AutoImageProcessor,
        "from_pretrained",
        staticmethod(load_processor),
    )
    monkeypatch.setattr(
        transformers.PreTrainedTokenizerFast,
        "from_pretrained",
        staticmethod(load_tokenizer),
    )
    monkeypatch.setattr(
        transformers.AutoModel,
        "from_pretrained",
        staticmethod(load_model),
    )

    ocr = ModelHayaiOCR.__new__(ModelHayaiOCR)
    async def load_color_model(device):
        calls["color_device"] = device
    ocr._load_color_model = load_color_model
    asyncio.run(ocr._load("cpu"))

    model_path = tmp_path / "ocr" / "hayai-ocr-v2"
    processor_path = model_path / "processor"
    assert calls["processor"] == (processor_path, {"local_files_only": True})
    assert calls["tokenizer"] == (model_path, {"local_files_only": True})
    assert calls["model"] == (
        model_path,
        {
            "trust_remote_code": True,
            "local_files_only": True,
            "dtype": torch.float32,
            "device_map": None,
        },
    )
    assert calls["to"] == "cpu"
    assert calls["eval"] is True
    assert calls["color_device"] == "cpu"


def test_hayai_recognition_passes_native_crop_inputs():
    calls = {}

    class FakeProcessor:
        def __call__(self, **kwargs):
            calls["processor"] = kwargs
            return {"pixel_values": torch.zeros((1, 3, 16, 16))}

    class FakeModel:
        def generate(self, **kwargs):
            calls["generate"] = kwargs
            return ["识别结果"]

    class FakeTokenizer:
        def decode(self, ids, **kwargs):
            calls["decode"] = (ids, kwargs)
            return "识别结果"

    ocr = ModelHayaiOCR.__new__(ModelHayaiOCR)
    ocr.processor = FakeProcessor()
    ocr.model = FakeModel()
    ocr.tokenizer = FakeTokenizer()
    ocr.device = "cpu"

    text = ocr._recognize_single(np.zeros((24, 36, 3), dtype=np.uint8), "ignored")

    assert text == "识别结果"
    assert calls["processor"]["images"][0].size == (36, 24)
    assert calls["processor"]["max_num_patches"] == 256
    assert calls["generate"]["tokenizer"] is ocr.tokenizer
    assert calls["generate"]["num_beams"] == 4
    assert calls["generate"]["repetition_penalty"] == 1.0
