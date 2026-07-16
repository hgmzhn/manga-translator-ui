import json
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "desktop_qt_ui" / "services" / "preset_service.py"
spec = importlib.util.spec_from_file_location("preset_service", MODULE_PATH)
preset_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preset_service)

ATLAS_CLOUD_OPENAI_BASE = preset_service.ATLAS_CLOUD_OPENAI_BASE
ATLAS_CLOUD_TEXT_MODEL = preset_service.ATLAS_CLOUD_TEXT_MODEL
PresetService = preset_service.PresetService


def test_builtin_presets_include_atlas_cloud_openai_compatible_config(tmp_path):
    service = PresetService(presets_dir=str(tmp_path))

    assert service.get_presets_list() == ["Atlas Cloud", "默认"]

    atlas_preset = json.loads((tmp_path / "Atlas Cloud.json").read_text(encoding="utf-8"))

    assert atlas_preset["OPENAI_API_KEY"] == ""
    assert atlas_preset["OPENAI_API_BASE"] == ATLAS_CLOUD_OPENAI_BASE
    assert atlas_preset["OPENAI_MODEL"] == ATLAS_CLOUD_TEXT_MODEL
    assert atlas_preset["OCR_OPENAI_API_KEY"] == ""
    assert atlas_preset["COLOR_OPENAI_API_KEY"] == ""
    assert atlas_preset["RENDER_OPENAI_API_KEY"] == ""


def test_builtin_presets_do_not_overwrite_user_customizations(tmp_path):
    atlas_path = tmp_path / "Atlas Cloud.json"
    atlas_path.write_text(
        json.dumps({"OPENAI_API_KEY": "user-key", "OPENAI_API_BASE": "https://example.test/v1"}),
        encoding="utf-8",
    )

    PresetService(presets_dir=str(tmp_path))

    atlas_preset = json.loads(atlas_path.read_text(encoding="utf-8"))
    assert atlas_preset == {
        "OPENAI_API_KEY": "user-key",
        "OPENAI_API_BASE": "https://example.test/v1",
    }
