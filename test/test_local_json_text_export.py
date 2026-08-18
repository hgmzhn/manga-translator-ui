import _bootstrap  # noqa: F401, I001

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop_qt_ui.services import workflow_service
from desktop_qt_ui.core.config_models import CliSettings
from manga_translator.config import Config, TranslatorConfig
from desktop_qt_ui.app_logic import MainAppLogic
from manga_translator.manga_translator import MangaTranslator
from manga_translator.utils.path_manager import (
    get_json_path,
    get_original_txt_path,
    get_translated_txt_path,
)


def _write_project_json(image_path: Path) -> Path:
    json_path = Path(get_json_path(str(image_path)))
    payload = {
        str(image_path): {
            "regions": [
                {
                    "text": "本地[BR]原文",
                    "translation": "用户[BR]终稿",
                }
            ],
            "editor_marker": "must survive unchanged",
        }
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return json_path


def _forbid_image_processing(*args, **kwargs):
    raise AssertionError(
        "local JSON export must not enter image processing or JSON write-back"
    )


def test_local_json_text_export_defaults_enabled():
    assert Config().cli.export_from_local_json is True
    assert CliSettings().export_from_local_json is True


@pytest.mark.parametrize(
    ("params", "path_factory", "expected"),
    [
        (
            {"generate_and_export": True},
            get_translated_txt_path,
            "本地原文|用户终稿",
        ),
        (
            {"template": True, "save_text": True},
            get_original_txt_path,
            "本地原文|用户终稿",
        ),
    ],
)
def test_local_json_export_reads_text_without_processing_or_json_writeback(
    tmp_path, monkeypatch, params, path_factory, expected
):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"not-an-image: local export must not open it")
    json_path = _write_project_json(image_path)
    original_json = json_path.read_bytes()

    template_path = tmp_path / "translation_template.txt"
    template_path.write_text(
        '"output_format": "txt",\n<original>|<translated>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workflow_service,
        "get_template_path_from_config",
        lambda: str(template_path),
    )

    translator = MangaTranslator(
        params={
            **params,
            "export_from_local_json": True,
            "translator": "none",
            "use_gpu": False,
        }
    )
    monkeypatch.setattr(
        translator, "_translate_until_translation", _forbid_image_processing
    )
    monkeypatch.setattr(translator, "_save_text_to_file", _forbid_image_processing)
    config = Config(translator=TranslatorConfig(translator="none"))

    contexts = asyncio.run(
        translator.translate_batch([(str(image_path), config)], global_total=1)
    )

    assert len(contexts) == 1
    assert contexts[0].success is True
    assert not getattr(contexts[0], "translation_error", None)
    output_path = Path(path_factory(str(image_path), output_format="txt"))
    assert output_path.read_text(encoding="utf-8") == expected
    assert json_path.read_bytes() == original_json


@pytest.mark.parametrize(
    "cli",
    [
        SimpleNamespace(
            export_from_local_json=True,
            generate_and_export=True,
            template=False,
            save_text=True,
        ),
        SimpleNamespace(
            export_from_local_json=True,
            generate_and_export=False,
            template=True,
            save_text=True,
        ),
    ],
)
def test_local_json_exports_do_not_require_api_candidates(cli):
    controller = SimpleNamespace(
        main_view=SimpleNamespace(
            _validate_api_candidate_availability=lambda: (_ for _ in ()).throw(
                AssertionError("local JSON export must bypass API candidate validation")
            )
        )
    )
    config = SimpleNamespace(cli=cli)

    assert MainAppLogic._validate_runtime_api_requirements(controller, config) is True


def test_legacy_export_still_uses_api_validation():
    controller = SimpleNamespace(
        main_view=SimpleNamespace(_validate_api_candidate_availability=lambda: False)
    )
    config = SimpleNamespace(
        cli=SimpleNamespace(
            export_from_local_json=False,
            generate_and_export=True,
            template=False,
            save_text=True,
        )
    )

    assert MainAppLogic._validate_runtime_api_requirements(controller, config) is False


def test_local_json_export_fails_without_falling_back_to_ocr(tmp_path, monkeypatch):
    image_path = tmp_path / "missing-project.png"
    image_path.write_bytes(b"not-an-image")
    translator = MangaTranslator(
        params={
            "generate_and_export": True,
            "export_from_local_json": True,
            "translator": "none",
            "use_gpu": False,
        }
    )
    monkeypatch.setattr(
        translator, "_translate_until_translation", _forbid_image_processing
    )
    config = Config(translator=TranslatorConfig(translator="none"))

    contexts = asyncio.run(
        translator.translate_batch([(str(image_path), config)], global_total=1)
    )

    assert len(contexts) == 1
    assert contexts[0].success is False
    assert "Local project JSON not found" in contexts[0].translation_error


def main() -> int:
    raise SystemExit("Run with pytest to provide tmp_path and monkeypatch fixtures")


if __name__ == "__main__":
    main()
