from pathlib import Path

from desktop_qt_ui.services.html_image_export_service import (
    build_html_document,
    write_task_html,
)


def test_build_html_document_embeds_image_bytes_without_paths(tmp_path: Path):
    image_path = tmp_path / "page 1.png"
    image_bytes = b"fake-png-bytes"
    image_path.write_bytes(image_bytes)

    document, image_count, skipped_count = build_html_document(
        [str(image_path)],
        view_mode="paged",
        title="测试任务",
    )

    assert image_count == 1
    assert skipped_count == 0
    assert 'data-view-mode="paged"' in document
    assert "data:image/png;base64,ZmFrZS1wbmctYnl0ZXM=" in document
    assert str(image_path) not in document
    assert "使用 ← / → 翻页" in document
    assert "ArrowLeft" in document
    assert 'class="page"' in document
    assert "gap: 0;" in document
    assert "padding: 0;" in document
    assert "第 1 页" not in document


def test_write_task_html_keeps_order_and_skips_missing_files(tmp_path: Path):
    first = tmp_path / "page-2.jpg"
    second = tmp_path / "page-10.jpg"
    missing = tmp_path / "missing.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    result = write_task_html(
        [str(first), str(missing), str(second), str(first)],
        tmp_path / "output",
        task_id=7,
        view_mode="unknown",
    )

    html = Path(result.output_path).read_text(encoding="utf-8")
    assert result.image_count == 2
    assert result.skipped_count == 2
    assert result.view_mode == "scroll"
    assert html.index("Zmlyc3Q=") < html.index("c2Vjb25k")
    assert "ArrowUp" in html
    assert "data:image/jpeg;base64" in html
