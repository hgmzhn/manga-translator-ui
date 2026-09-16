from __future__ import annotations

from pathlib import Path

from PIL import Image

from desktop_qt_ui.services.long_image_service import build_long_image
from manga_translator.manga_translator import MangaTranslator


def _write_image(path: Path, size: tuple[int, int], color: str) -> None:
    Image.new("RGB", size, color).save(path)


def test_long_image_can_be_written_to_a_parent_output_directory(tmp_path):
    output_root = tmp_path / "output"
    process_dir = output_root / "translated_pages"
    process_dir.mkdir(parents=True)
    first = process_dir / "page_01.png"
    second = process_dir / "page_02.png"
    _write_image(first, (4, 3), "red")
    _write_image(second, (4, 2), "blue")

    result = build_long_image([first, second], output_dir=output_root)

    assert Path(result.output_path) == output_root.resolve() / "page_long.png"
    assert Path(result.output_path).parent == output_root.resolve()
    assert Path(result.source_paths[0]).parent == process_dir.resolve()
    with Image.open(result.output_path) as image:
        assert image.size == (4, 5)


def test_long_image_work_directory_overrides_source_directory_output(tmp_path):
    source_dir = tmp_path / "chapter"
    source_dir.mkdir()
    source_path = source_dir / "page.png"
    work_dir = tmp_path / "output" / "translated_pages"

    translator = object.__new__(MangaTranslator)
    output_path = translator._calculate_output_path(
        str(source_path),
        {
            "output_folder": str(tmp_path / "output"),
            "input_folders": {str(source_dir)},
            "format": None,
            "save_to_source_dir": True,
            "long_image_work_dir": str(work_dir),
        },
    )

    assert Path(output_path) == work_dir / "chapter" / "page.png"
