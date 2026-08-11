import _bootstrap  # noqa: F401, I001

from services.preset_service import DEFAULT_PRESET_NAME, PresetService


def test_default_api_preset_cannot_be_deleted(tmp_path):
    service = PresetService(presets_dir=str(tmp_path))
    default_path = tmp_path / f"{DEFAULT_PRESET_NAME}.json"
    original_contents = default_path.read_text(encoding="utf-8")

    assert service.delete_preset(DEFAULT_PRESET_NAME) is False
    assert default_path.read_text(encoding="utf-8") == original_contents


def test_custom_api_preset_can_still_be_deleted(tmp_path):
    service = PresetService(presets_dir=str(tmp_path))
    assert service.save_preset("custom", {"OPENAI_API_KEY": "test-key"}) is True

    assert service.delete_preset("custom") is True
    assert not (tmp_path / "custom.json").exists()
