import _bootstrap  # noqa: F401, I001

import asyncio
import json
from pathlib import Path
from PIL import Image
import pytest

from manga_translator.config import Config, Translator, TranslatorConfig
from manga_translator.manga_translator import MangaTranslator
from manga_translator.utils import Context
from manga_translator.utils.batch_skip import (
    input_path,
    plan_batch_inputs,
    slice_batch_indices,
)
from manga_translator.utils.concurrent_pipeline import ConcurrentPipeline
from manga_translator.utils.path_manager import (
    get_json_path,
    get_original_txt_path,
    get_translated_txt_path,
)


def _translator(**params) -> MangaTranslator:
    return MangaTranslator(
        params={
            "translator": "none",
            "use_gpu": False,
            "filter_text_enabled": False,
            **params,
        }
    )


def _config() -> Config:
    return Config(translator=TranslatorConfig(translator=Translator.openai))


def _save_info(output_dir: Path) -> dict:
    return {
        "output_folder": str(output_dir),
        "format": None,
        "overwrite": False,
        "input_folders": set(),
    }


def _write_history(image_path: Path, text: str, translation: str) -> None:
    json_path = Path(get_json_path(str(image_path), create_dir=True))
    json_path.write_text(
        json.dumps(
            {
                str(image_path): {
                    "regions": [
                        {
                            "text": text,
                            "translation": translation,
                            "lines": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_backend_plan_owns_existing_output_skip_and_resume_order(tmp_path):
    source_paths = [tmp_path / f"page-{index}.png" for index in range(1, 4)]
    for path in source_paths:
        path.write_bytes(b"source")

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / source_paths[1].name).write_bytes(b"existing output")
    _write_history(source_paths[1], "前文", "Previous line")

    translator = _translator(context_size=2)
    items = [(str(path), _config()) for path in source_paths]
    plan = plan_batch_inputs(translator, items, _save_info(output_dir))

    assert [input_path(item) for item in plan.pending_items] == [
        str(source_paths[0]),
        str(source_paths[2]),
    ]
    assert len(plan.skipped_contexts) == 1
    skipped = plan.skipped_contexts[0]
    assert skipped.image_name == str(source_paths[1])
    assert skipped.skip_reason == "existing_output"
    assert skipped.skip_message == f"输出文件已存在: {source_paths[1].name}"
    assert skipped.output_path == str(output_dir / source_paths[1].name)
    assert plan.resume_pages[0][0] == 1
    assert plan.resume_pages[0][2][0]["translation"] == "Previous line"

    slices = slice_batch_indices(
        plan.pending_items,
        3,
        plan.resume_pages,
        plan.resume_order,
    )
    translator._prepare_resume_context(plan)
    translator._resume_context_cursor = 1
    translator._prepare_resume_context(plan)
    merged = plan.merge_results(
        [
            Context(image_name=str(source_paths[2])),
            Context(image_name=str(source_paths[0])),
        ]
    )
    assert [ctx.image_name for ctx in merged] == [str(path) for path in source_paths]
    assert translator._resume_context_cursor == 0
    assert slices == [(0, 1), (1, 2)]


def test_translate_batch_returns_backend_skip_without_materializing_image(
    tmp_path, monkeypatch
):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"source")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output_path = output_dir / image_path.name
    output_path.write_bytes(b"existing output")

    translator = _translator(context_size=0)
    monkeypatch.setattr(
        translator,
        "_materialize_batch_inputs",
        lambda _items: (_ for _ in ()).throw(
            AssertionError(
                "an existing output must be skipped before image materialization"
            )
        ),
    )
    progress = []

    async def progress_hook(state, _finished):
        if state.startswith("batch:"):
            progress.append(state)

    translator.add_progress_hook(progress_hook)
    contexts = asyncio.run(
        translator.translate_batch(
            [(str(image_path), _config())],
            save_info=_save_info(output_dir),
        )
    )

    assert len(contexts) == 1
    assert contexts[0].success is True
    assert contexts[0].skipped is True
    assert contexts[0].skip_reason == "existing_output"
    assert contexts[0].output_path == str(output_path)
    assert progress[-1] == "batch:1:1:1:0:1"


def test_save_race_is_reported_as_skip_without_overwriting(tmp_path):
    image_path = tmp_path / "page.png"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output_path = output_dir / image_path.name
    output_path.write_bytes(b"winner")

    translator = _translator(context_size=0)
    ctx = Context(
        image_name=str(image_path),
        input=Image.new("RGB", (2, 2), "white"),
        result=Image.new("RGB", (2, 2), "black"),
    )
    try:
        success = translator._save_and_cleanup_context(
            ctx,
            _save_info(output_dir),
            _config(),
        )
    finally:
        ctx.input.close()

    assert success is True
    assert ctx.success is True
    assert ctx.skipped is True
    assert ctx.skip_reason == "existing_output_race"
    assert output_path.read_bytes() == b"winner"


@pytest.mark.parametrize(
    ("translator_params", "artifact_kind", "expected_reason"),
    [
        ({"template": True, "save_text": True}, "original", "existing_original_text"),
        ({"generate_and_export": True}, "translated", "existing_translated_text"),
        ({"load_text": True}, "image", "existing_output"),
        ({"colorize_only": True}, "image", "existing_output"),
        ({"upscale_only": True}, "image", "existing_output"),
        ({"inpaint_only": True}, "image", "existing_output"),
        ({"replace_translation": True}, "image", "existing_output"),
    ],
)
def test_backend_applies_workflow_specific_skip_conditions(
    tmp_path,
    translator_params,
    artifact_kind,
    expected_reason,
):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"source")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    if artifact_kind == "original":
        artifact_path = Path(get_original_txt_path(str(image_path), create_dir=True))
    elif artifact_kind == "translated":
        artifact_path = Path(get_translated_txt_path(str(image_path), create_dir=True))
    else:
        artifact_path = output_dir / image_path.name
    artifact_path.write_bytes(b"existing")

    plan = plan_batch_inputs(
        _translator(**translator_params),
        [(str(image_path), _config())],
        _save_info(output_dir),
    )

    assert plan.pending_items == []
    assert plan.skipped_contexts[0].skip_reason == expected_reason
    assert plan.skipped_contexts[0].context_eligible is False


def test_missing_json_only_input_is_not_resume_context(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"source")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_history(image_path, "原文", "Translation")

    translator = _translator(translate_json_only=True, context_size=3)
    plan = plan_batch_inputs(
        translator,
        [(str(image_path), _config())],
        _save_info(output_dir),
    )

    assert plan.skipped_contexts[0].skip_reason == "missing_required_original_text"
    assert plan.skipped_contexts[0].context_eligible is False
    assert plan.resume_pages == []
    assert plan.resume_order == {}


def test_concurrent_translation_splits_at_resume_boundary(tmp_path, monkeypatch):
    source_paths = [tmp_path / f"page-{index}.png" for index in range(1, 4)]
    for path in source_paths:
        path.write_bytes(b"source")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / source_paths[1].name).write_bytes(b"existing output")
    _write_history(source_paths[1], "跳过页", "Skipped page")

    translator = _translator(context_size=3)
    plan = plan_batch_inputs(
        translator,
        [(str(path), _config()) for path in source_paths],
        _save_info(output_dir),
    )
    translator._prepare_resume_context(plan)
    observed = []

    async def fake_translate(batch, _batch_size):
        observed.append(
            (
                [ctx.image_name for ctx, _config_value in batch],
                [
                    page[0]["translation"]
                    for page in translator.all_page_translations
                    if page
                ],
            )
        )
        for ctx, _config_value in batch:
            translator.all_page_translations.append(
                [{"text": ctx.image_name, "translation": ctx.image_name}]
            )
        return batch

    monkeypatch.setattr(translator, "_batch_translate_contexts", fake_translate)
    contexts = [
        (Context(image_name=str(source_paths[0]), text_regions=[]), _config()),
        (Context(image_name=str(source_paths[2]), text_regions=[]), _config()),
    ]
    pipeline = ConcurrentPipeline(translator, batch_size=2)
    try:
        asyncio.run(pipeline._process_translation_batch(contexts))
    finally:
        pipeline._detection_executor.shutdown(wait=False)
        pipeline._translation_executor.shutdown(wait=False)
        pipeline._inpaint_executor.shutdown(wait=False)
        pipeline._render_executor.shutdown(wait=False)

    assert [item[0] for item in observed] == [
        [str(source_paths[0])],
        [str(source_paths[2])],
    ]
    assert observed[0][1] == []
    assert observed[1][1] == [str(source_paths[0]), "Skipped page"]


def main() -> int:
    return pytest.main([__file__])


if __name__ == "__main__":
    raise SystemExit(main())
