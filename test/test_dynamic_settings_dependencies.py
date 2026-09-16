import _bootstrap  # noqa: F401

import pytest

from ui.main_page.dynamic_settings import _setting_dependencies_satisfied


def _ocr_config(primary: str, *, hybrid: bool = False, secondary: str = "48px") -> dict:
    return {
        "ocr": {
            "ocr": primary,
            "use_hybrid_ocr": hybrid,
            "secondary_ocr": secondary,
        }
    }


@pytest.mark.parametrize(
    "setting_key",
    [
        "ocr.ocr_vl_language_hint",
        "ocr.ocr_vl_custom_prompt",
    ],
)
def test_vlm_ocr_settings_are_disabled_for_local_ocr(setting_key: str):
    assert not _setting_dependencies_satisfied(_ocr_config("48px"), setting_key)
    assert _setting_dependencies_satisfied(_ocr_config("paddleocr_vl"), setting_key)


@pytest.mark.parametrize(
    "setting_key",
    [
        "ocr.ai_ocr_prompt_path",
        "ocr.ai_ocr_concurrency",
        "ocr.ai_ocr_custom_prompt",
    ],
)
def test_ai_ocr_settings_follow_the_active_ocr_backend(setting_key: str):
    assert not _setting_dependencies_satisfied(_ocr_config("48px"), setting_key)
    assert _setting_dependencies_satisfied(_ocr_config("openai_ocr"), setting_key)
    assert _setting_dependencies_satisfied(
        _ocr_config("48px", hybrid=True, secondary="gemini_ocr"),
        setting_key,
    )


def test_hybrid_vlm_fallback_enables_vlm_settings_only_when_hybrid_is_on():
    key = "ocr.ocr_vl_language_hint"
    assert not _setting_dependencies_satisfied(
        _ocr_config("48px", secondary="paddleocr_vl"),
        key,
    )
    assert _setting_dependencies_satisfied(
        _ocr_config("48px", hybrid=True, secondary="paddleocr_vl"),
        key,
    )
