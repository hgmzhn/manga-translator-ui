import _bootstrap  # noqa: F401

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from desktop_qt_ui.ui.main_page import env_management
from manga_translator.config import Ocr, OcrConfig
from manga_translator.ocr import OCRS
from manga_translator.ocr.model_api_ocr import (
    ModelDoubaoVLOCR,
    ModelGLMVLOCR,
    ModelKimiVLOCR,
    ModelQwenVLOCR,
)


DOMESTIC_VLM_CASES = [
    (Ocr.qwen_vl, ModelQwenVLOCR, "qwen3-vl-plus", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    (Ocr.doubao_vl, ModelDoubaoVLOCR, "doubao-1.5-vision-pro-32k", "https://ark.cn-beijing.volces.com/api/v3"),
    (Ocr.glm_vl, ModelGLMVLOCR, "glm-4v-flash", "https://open.bigmodel.cn/api/paas/v4"),
    (Ocr.kimi_vl, ModelKimiVLOCR, "kimi-k3", "https://api.moonshot.cn/v1"),
]


@pytest.mark.parametrize("ocr_key, expected_class, model_name, base_url", DOMESTIC_VLM_CASES)
def test_domestic_vlm_ocr_routes_have_provider_defaults(ocr_key, expected_class, model_name, base_url):
    assert ocr_key in OCRS
    assert OCRS[ocr_key]() is expected_class
    assert expected_class.DEFAULT_MODEL == model_name
    assert expected_class.DEFAULT_API_BASE == base_url
    assert expected_class.API_KEY_ENV.startswith("OCR_")


@pytest.mark.parametrize("ocr_class", [ModelQwenVLOCR, ModelDoubaoVLOCR, ModelGLMVLOCR, ModelKimiVLOCR])
def test_domestic_vlm_prompt_uses_vlm_settings(ocr_class):
    model = ocr_class.__new__(ocr_class)
    assert "Japanese" in model._build_ocr_prompt(OcrConfig(ocr_vl_language_hint="Japanese"))
    assert model._build_ocr_prompt(
        OcrConfig(ocr_vl_custom_prompt="只返回原文")
    ) == "只返回原文"


class _FakeCompletions:
    def __init__(self):
        self.request = None

    async def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="识别结果"))]
        )


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_domestic_vlm_uses_openai_image_message_shape():
    model = ModelQwenVLOCR.__new__(ModelQwenVLOCR)
    model.logger = SimpleNamespace(info=lambda *_args: None)
    image = np.zeros((8, 12, 3), dtype=np.uint8)
    client = _FakeClient()

    text = asyncio.run(
        model._request_ocr_text(
            client=client,
            model_name="qwen3-vl-plus",
            img=image,
            prompt_text="OCR: Extract all visible text.",
        )
    )

    request = client.chat.completions.request
    assert text == "识别结果"
    assert request["model"] == "qwen3-vl-plus"
    content = request["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("env_key", "target", "base_url"),
    [
        ("OCR_QWEN_API_KEY", "qwen_vl", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("OCR_DOUBAO_API_KEY", "doubao_vl", "https://ark.cn-beijing.volces.com/api/v3"),
        ("OCR_GLM_API_KEY", "glm_vl", "https://open.bigmodel.cn/api/paas/v4"),
        ("OCR_KIMI_API_KEY", "kimi_vl", "https://api.moonshot.cn/v1"),
    ],
)
def test_domestic_vlm_api_management_targets(env_key, target, base_url):
    assert env_management._detect_test_target(env_key, "") == target
    assert env_management._get_api_address_example(target) == base_url
    assert env_management._test_target_status_identity(target) == ("ocr", target.removesuffix("_vl"))
