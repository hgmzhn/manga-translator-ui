from __future__ import annotations

import _bootstrap  # noqa: F401  —— sys.path / offscreen / torch 先于 PyQt6

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = _bootstrap.ROOT

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
from qfluentwidgets import Theme, TreeView, qconfig, setTheme

import desktop_qt_ui.services.file_list_data_service as catalog_module
from desktop_qt_ui.editor.file_list_model import FileListModel
from desktop_qt_ui.services.file_list_data_service import (
    KIND_FOLDER,
    FileCatalogCancelled,
    FileCatalogNode,
    FileCatalogSnapshot,
    FileListDataService,
    build_file_catalog_snapshot,
)
from desktop_qt_ui.ui.widgets.file_list_view import (
    THUMBNAIL_ROLE,
    FileCatalogModel,
    FileListView,
    _load_thumbnail_worker,
)


_APPLICATION: QApplication | None = None


def _app() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    return _APPLICATION


def _save_image(path: Path) -> None:
    image = QImage(96, 64, QImage.Format.Format_RGB32)
    image.fill(0xFF7799BB)
    assert image.save(str(path))


def _catalog_path(path: str | Path) -> str:
    return os.path.abspath(os.path.normpath(path))


def _wait_until(predicate, timeout_ms: int = 3000) -> None:
    elapsed = 0
    while elapsed < timeout_ms and not predicate():
        QTest.qWait(10)
        elapsed += 10
    assert predicate()


def test_snapshot_is_complete_sorted_deduplicated_and_metadata_ready() -> None:
    _app()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "book"
        chapter10 = root / "chapter10"
        chapter2 = root / "chapter2"
        chapter10.mkdir(parents=True)
        chapter2.mkdir()

        source = chapter2 / "page2.png"
        excluded = chapter2 / "page3.png"
        translated = chapter10 / "page10.png"
        _save_image(source)
        _save_image(excluded)
        _save_image(translated)
        (root / "book.cbz").write_bytes(b"")

        json_dir = chapter2 / "manga_translator_work" / "json"
        json_dir.mkdir(parents=True)
        source_json = json_dir / "page2_translations.json"
        source_json.write_text("{}", encoding="utf-8")
        (chapter10 / "translation_map.json").write_text(
            json.dumps({str(translated): str(source)}),
            encoding="utf-8",
        )

        snapshot = build_file_catalog_snapshot(
            [str(source), str(root), str(root)],
            excluded_files=[str(excluded)],
        )

        assert [node.name for node in snapshot.roots[0].children] == [
            "chapter2",
            "chapter10",
            "book.cbz",
        ]
        assert snapshot.files == (
            _catalog_path(source),
            _catalog_path(translated),
            _catalog_path(root / "book.cbz"),
        )
        assert snapshot.editor_files == (_catalog_path(source),)
        assert snapshot.source_by_file[_catalog_path(translated)] == _catalog_path(source)
        assert snapshot.json_by_file[_catalog_path(source)] == _catalog_path(source_json)
        assert snapshot.roots[0].file_count == 3
        assert snapshot.images_only().files == snapshot.image_files

        file_model = FileListModel()
        added = file_model.add_files([str(source), str(source)])
        assert len(added) == 1
        assert file_model.get_file_item(str(source)) is added[0]


def test_service_only_delivers_latest_generation() -> None:
    app = _app()
    original_builder = catalog_module.build_file_catalog_snapshot
    first_started = threading.Event()
    release_first = threading.Event()

    def controlled_builder(sources, **kwargs):
        generation = kwargs["generation"]
        cancel_event = kwargs["cancel_event"]
        if generation == 1:
            first_started.set()
            release_first.wait(2)
            if cancel_event.is_set():
                raise FileCatalogCancelled()
        return FileCatalogSnapshot.empty(generation)

    catalog_module.build_file_catalog_snapshot = controlled_builder
    service = FileListDataService(max_workers=1)
    delivered: list[int] = []
    service.snapshot_ready.connect(lambda _channel, generation, _snapshot: delivered.append(generation))
    try:
        service.request_snapshot("view", ["first"])
        assert first_started.wait(1)
        service.request_snapshot("view", ["second"])
        release_first.set()
        _wait_until(lambda: delivered == [2])
        app.processEvents()
        assert delivered == [2]
    finally:
        service.shutdown()
        catalog_module.build_file_catalog_snapshot = original_builder


def test_view_uses_model_delegate_and_gui_thread_pixmap() -> None:
    app = _app()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        first = root / "page1.png"
        second = root / "page2.png"
        _save_image(first)
        _save_image(second)
        snapshot = build_file_catalog_snapshot([str(root)])

        worker_image = _load_thumbnail_worker(str(first))[1]
        assert isinstance(worker_image, QImage)
        assert not isinstance(worker_image, QPixmap)

        service = FileListDataService(max_workers=1)
        view = FileListView(None, data_service=service)
        selected: list[str] = []
        removed: list[str] = []
        view.file_selected.connect(selected.append)
        view.file_remove_requested.connect(removed.append)
        try:
            view.resize(360, 240)
            view.set_snapshot(snapshot)
            view.expandAll()
            view.show()
            view.select_next_image()

            first_index = view.catalog_model.index_for_path(str(first))
            _wait_until(lambda: isinstance(first_index.data(THUMBNAIL_ROLE), QPixmap))
            assert isinstance(view.model(), FileCatalogModel)
            assert view.indexWidget(first_index) is None
            assert selected[-1] == _catalog_path(first)

            view.select_next_image()
            assert view.currentIndex().data().startswith("page2")
            second_index = view.catalog_model.index_for_path(str(second))
            second_rect = view.visualRect(second_index)
            QTest.mouseClick(
                view.viewport(),
                Qt.MouseButton.LeftButton,
                pos=QPoint(second_rect.right() - 18, second_rect.center().y()),
            )
            _wait_until(lambda: removed == [_catalog_path(second)])
            view.remove_file(removed[0])
            assert _catalog_path(second) not in view.catalog_model.image_paths()
        finally:
            view.close()
            view.deleteLater()
            app.processEvents()
            service.shutdown()


def _check_view_and_empty_hint_follow_fluent_theme() -> None:
    app = _app()
    previous_theme = qconfig.theme
    view = FileListView()
    try:
        assert isinstance(view, TreeView)
        assert "background-color: transparent" in view.styleSheet()
        assert view.empty_hint_label.lightColor == QColor(0, 0, 0, 130)
        assert view.empty_hint_label.darkColor == QColor(255, 255, 255, 150)

        setTheme(Theme.DARK)
        app.processEvents()
        assert "treeviewopen_white.svg" in view.styleSheet().lower()
        assert "color:#96ffffff" in view.empty_hint_label.styleSheet().replace(" ", "").lower()

        setTheme(Theme.LIGHT)
        app.processEvents()
        assert "treeviewopen_black.svg" in view.styleSheet().lower()
        assert "color:#82000000" in view.empty_hint_label.styleSheet().replace(" ", "").lower()

        folder = FileCatalogNode(r"C:\book", KIND_FOLDER, file_count=2)
        view.set_snapshot(FileCatalogSnapshot(1, (), (folder,), (), (), (), {}, {}, {}))
        index = view.catalog_model.index(0, 0)

        def render_folder_icon_brightness(theme: Theme) -> float:
            setTheme(theme)
            app.processEvents()
            image = QImage(320, 66, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 320, 66)
            option.font = app.font()
            option.state = QStyle.StateFlag.State_Enabled
            painter = QPainter(image)
            view.itemDelegate().paint(painter, option, index)
            painter.end()
            pixels = [
                image.pixelColor(x, y)
                for y in range(10, 56)
                for x in range(10, 56)
                if image.pixelColor(x, y).alpha() > 20
            ]
            return sum(pixel.lightness() for pixel in pixels) / len(pixels)

        assert render_folder_icon_brightness(Theme.LIGHT) < 32
        assert render_folder_icon_brightness(Theme.DARK) > 223
    finally:
        setTheme(previous_theme)
        view.close()
        view.deleteLater()
        app.processEvents()


def test_view_and_empty_hint_follow_fluent_theme() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--theme-check-child"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_images_only_keeps_json_for_resolved_editor_source() -> None:
    translated = r"C:\book\translated.png"
    source = r"C:\book\source.png"
    json_path = r"C:\book\source.json"
    snapshot = FileCatalogSnapshot(
        generation=1,
        sources=(translated,),
        roots=(),
        files=(translated,),
        image_files=(translated,),
        editor_files=(source,),
        file_to_folder={translated: None},
        source_by_file={translated: source},
        json_by_file={source: json_path},
    )

    assert snapshot.images_only().json_by_file == {source: json_path}


def main() -> int:
    test_snapshot_is_complete_sorted_deduplicated_and_metadata_ready()
    test_service_only_delivers_latest_generation()
    test_view_uses_model_delegate_and_gui_thread_pixmap()
    test_view_and_empty_hint_follow_fluent_theme()
    test_images_only_keeps_json_for_resolved_editor_source()
    print("file-list snapshot/model-view checks passed")
    return 0


if __name__ == "__main__":
    if "--theme-check-child" in sys.argv:
        _check_view_and_empty_hint_follow_fluent_theme()
        raise SystemExit(0)
    raise SystemExit(main())
