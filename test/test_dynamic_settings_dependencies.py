import _bootstrap  # noqa: F401

import pytest

from ui.main_page.dynamic_settings import (
    _filter_ocr_options_for_mode,
    _ocr_mode_for_config,
    _setting_dependencies_satisfied,
    _setting_visibility_satisfied,
)


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


def test_ocr_mode_infers_legacy_configs_and_filters_model_options():
    local_config = _ocr_config("48px")
    ai_vlm_config = _ocr_config("qwen_vl")

    assert _ocr_mode_for_config(local_config) == "local"
    assert _ocr_mode_for_config(ai_vlm_config) == "ai_vlm"

    options = ["48px", "mocr", "paddleocr_vl", "qwen_vl", "openai_ocr"]
    assert _filter_ocr_options_for_mode(options, "local") == ["48px", "mocr"]
    assert _filter_ocr_options_for_mode(options, "ai_vlm") == [
        "paddleocr_vl",
        "qwen_vl",
        "openai_ocr",
    ]


def test_local_ocr_hides_vlm_only_settings():
    local_config = _ocr_config("48px")
    ai_vlm_config = _ocr_config("qwen_vl")
    ai_vlm_config["ocr"]["ocr_mode"] = "ai_vlm"

    assert not _setting_visibility_satisfied(local_config, "ocr.ocr_vl_language_hint")
    assert not _setting_visibility_satisfied(local_config, "ocr.ai_ocr_custom_prompt")
    assert _setting_visibility_satisfied(ai_vlm_config, "ocr.ocr_vl_language_hint")
    assert not _setting_visibility_satisfied(ai_vlm_config, "ocr.ai_ocr_custom_prompt")
