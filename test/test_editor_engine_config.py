import _bootstrap  # noqa: F401, I001

from core.config_models import AppSettings, AppSection


def test_editor_engine_defaults_are_independent_from_homepage_defaults():
    settings = AppSettings(
        translator={"translator": "gemini"},
        ocr={"ocr": "48px"},
    )

    assert settings.app.editor_ocr == "mocr"
    assert settings.app.editor_translator == "openai"
    assert settings.translator.translator == "gemini"
    assert settings.ocr.ocr == "48px"


def test_editor_engine_settings_round_trip_without_overwriting_homepage():
    section = AppSection(editor_ocr="paddleocr", editor_translator="sakura")
    restored = AppSection.model_validate(section.model_dump())

    assert restored.editor_ocr == "paddleocr"
    assert restored.editor_translator == "sakura"
    assert restored.model_dump()["editor_ocr"] == "paddleocr"
    assert restored.model_dump()["editor_translator"] == "sakura"
