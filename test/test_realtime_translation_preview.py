import _bootstrap  # noqa: F401, I001

import json
import os
import threading
from types import SimpleNamespace

from PIL import Image

from desktop_qt_ui.app_logic import MainAppLogic
from editor.controller_document_service import EditorControllerDocumentService
from editor.document_state import DocumentSnapshot
from editor.editor_logic import EditorLogic
from editor.file_list_model import FileListModel, FileType
from manga_translator.manga_translator import MangaTranslator
from manga_translator.utils import Context
from services.file_list_data_service import (
    KIND_FOLDER,
    KIND_IMAGE,
    FileCatalogNode,
    FileCatalogSnapshot,
)
from ui.widgets.file_list_view import FileCatalogModel, _resolve_thumbnail_source_path


class _SignalRecorder:
    def __init__(self):
        self.values = []

    def emit(self, *args):
        self.values.append(args)


def test_manga_translator_notify_file_completed_is_idempotent(tmp_path):
    source_file = tmp_path / "page1.png"
    output_file = tmp_path / "out" / "page1.png"
    json_file = tmp_path / "manga_translator_work" / "json" / "page1_translations.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "white").save(source_file)
    Image.new("RGB", (8, 8), "green").save(output_file)
    json_file.write_text("{}", encoding="utf-8")

    translator = MangaTranslator.__new__(MangaTranslator)
    translator._file_completed_hooks = []
    events = []
    translator.add_file_completed_hook(lambda payload: events.append(payload))

    ctx = Context()
    ctx.image_name = str(source_file)
    ctx.output_path = str(output_file)
    ctx.json_path = str(json_file)
    ctx.success = True

    translator._notify_file_completed(ctx)
    translator._notify_file_completed(ctx)

    assert len(events) == 1
    assert events[0] is ctx

    failed_save_ctx = Context()
    failed_save_ctx.image_name = str(source_file)
    failed_save_ctx.result = object()
    failed_save_ctx.success = False
    translator._notify_file_completed(failed_save_ctx)
    assert len(events) == 1

    missing_orig_ctx = Context(
        image_name=str(source_file),
        success=True,
        skipped=True,
        skip_reason="missing_required_original_text",
    )
    translator._notify_file_completed(missing_orig_ctx)
    assert len(events) == 1


def test_app_logic_on_file_completed_filters_task_id_and_emits_signal(tmp_path):
    source_file = str(tmp_path / "page1.png")
    output_file = str(tmp_path / "out" / "page1.png")
    json_file = str(tmp_path / "work" / "page1_translations.json")

    logic = SimpleNamespace(
        current_task_id=5,
        _shutdown_started=False,
        completed_output_sources={},
        file_translated=_SignalRecorder(),
        _path_key=MainAppLogic._path_key,
    )

    # Stale task_id should be ignored
    MainAppLogic.on_file_completed(
        logic,
        {
            "source_path": source_file,
            "output_path": output_file,
            "json_path": json_file,
        },
        task_id=4,
    )
    assert logic.file_translated.values == []
    assert logic.completed_output_sources == {}

    # Active task_id should record mapping and emit signal
    MainAppLogic.on_file_completed(
        logic,
        {
            "source_path": source_file,
            "output_path": output_file,
            "json_path": json_file,
            "skipped": True,
        },
        task_id=5,
    )
    assert len(logic.file_translated.values) == 1
    emitted = logic.file_translated.values[0][0]
    assert emitted["source_path"] == source_file
    assert emitted["output_path"] == output_file
    assert emitted["json_path"] == json_file
    assert emitted["skipped"] is True
    assert logic.completed_output_sources[
        MainAppLogic._path_key(output_file)
    ] == os.path.normpath(source_file)


def test_catalog_snapshot_and_model_mark_file_translated_and_resolve_thumbnail(tmp_path):
    folder = tmp_path / "chapter1"
    folder.mkdir()
    source_path = folder / "001.png"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output_path = output_dir / "001.png"
    json_dir = folder / "manga_translator_work" / "json"
    json_dir.mkdir(parents=True)
    json_path = json_dir / "001_translations.json"

    Image.new("RGB", (16, 16), (255, 0, 0)).save(source_path)
    Image.new("RGB", (16, 16), (0, 255, 0)).save(output_path)
    json_path.write_text(
        json.dumps(
            {
                str(source_path): {
                    "last_output_path": str(output_path),
                    "last_export_dir": str(output_dir),
                }
            }
        ),
        encoding="utf-8",
    )

    child_node = FileCatalogNode(
        path=str(source_path),
        kind=KIND_IMAGE,
        source_root=str(folder),
        mtime_ns=source_path.stat().st_mtime_ns,
        json_path=None,
    )
    folder_node = FileCatalogNode(
        path=str(folder),
        kind=KIND_FOLDER,
        source_root=str(folder),
        children=(child_node,),
        file_count=1,
    )
    snapshot = FileCatalogSnapshot(
        generation=1,
        sources=(str(folder),),
        roots=(folder_node,),
        files=(str(source_path),),
        image_files=(str(source_path),),
        editor_files=(str(source_path),),
        file_to_folder={str(source_path): str(folder)},
        source_by_file={str(source_path): str(source_path)},
        json_by_file={str(source_path): None},
    )

    updated_snapshot = snapshot.with_file_translated(
        str(source_path),
        json_path=str(json_path),
        output_path=str(output_path),
    )
    updated_leaf = updated_snapshot.roots[0].children[0]
    assert updated_leaf.json_path == os.path.abspath(os.path.normpath(str(json_path)))
    assert updated_leaf.output_path == os.path.abspath(
        os.path.normpath(str(output_path))
    )
    assert updated_snapshot.json_by_file[
        os.path.abspath(os.path.normpath(str(source_path)))
    ] == os.path.abspath(os.path.normpath(str(json_path)))

    model = FileCatalogModel()
    model.set_snapshot(snapshot)
    item = model.item_for_path(str(source_path))
    assert item is not None
    old_thumb_key = item.thumbnail_key
    model.set_thumbnail(str(source_path), "old_pixmap")

    updated_paths = model.mark_file_translated(
        str(source_path),
        output_path=str(output_path),
        json_path=str(json_path),
    )
    assert updated_paths == (str(source_path),)
    assert item.json_path == os.path.abspath(os.path.normpath(str(json_path)))
    assert item.output_path == os.path.abspath(os.path.normpath(str(output_path)))
    assert item.thumbnail_key != old_thumb_key
    assert model.set_thumbnail(
        str(source_path), "new_pixmap", expected_key=item.thumbnail_key
    )
    assert not model.set_thumbnail(
        str(source_path), "stale_pixmap", expected_key=old_thumb_key
    )

    # Verify FileListView._on_thumbnail_loaded compares the 6-tuple (*item.thumbnail_key, _thumbnail_size)
    from collections import OrderedDict
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QImage
    from ui.widgets.file_list_view import FileListView, THUMBNAIL_ROLE

    _app = QApplication.instance() or QApplication([])
    dummy_view = SimpleNamespace(
        _view_generation=1,
        _thumbnail_size=160,
        _THUMBNAIL_CACHE_SIZE=16,
        _thumbnail_pending=set(),
        _thumbnail_cache=OrderedDict(),
        catalog_model=model,
    )
    qimg = QImage(4, 4, QImage.Format.Format_RGB32)
    qimg.fill(0xFF00FF00)
    stale_cache_key = (*old_thumb_key, 160)
    fresh_cache_key = (*item.thumbnail_key, 160)

    FileListView._on_thumbnail_loaded(
        dummy_view, 1, stale_cache_key, str(source_path), qimg
    )
    assert stale_cache_key not in dummy_view._thumbnail_cache

    FileListView._on_thumbnail_loaded(
        dummy_view, 1, fresh_cache_key, str(source_path), qimg
    )
    assert fresh_cache_key in dummy_view._thumbnail_cache
    assert (
        model.data(model.index_for_path(str(source_path)), THUMBNAIL_ROLE)
        == dummy_view._thumbnail_cache[fresh_cache_key]
    )

    # Thumbnail resolver should prefer output_path, or resolve from json_path
    assert _resolve_thumbnail_source_path(
        str(source_path),
        output_path=str(output_path),
        json_path=str(json_path),
    ) == str(output_path)
    assert _resolve_thumbnail_source_path(
        str(source_path),
        output_path=None,
        json_path=str(json_path),
    ) == os.path.normpath(str(output_path))


def test_editor_on_file_translated_evicts_stale_prefetch_and_updates_file_model(tmp_path):
    page1 = str(tmp_path / "001.png")
    page2 = str(tmp_path / "002.png")
    json2 = str(tmp_path / "002_translations.json")
    out2 = str(tmp_path / "out_002.png")

    doc_service = EditorControllerDocumentService.__new__(
        EditorControllerDocumentService
    )
    doc_service._is_shutdown = False
    doc_service._prefetch_lock = threading.RLock()
    doc_service._prefetch_generation = 3
    key2 = doc_service._prefetch_key(page2)
    doc_service._prefetched_documents = {
        key2: DocumentSnapshot(
            source_path=page2,
            image=Image.new("RGB", (4, 4), "white"),
            display_image_path=page2,
            regions=[],
        )
    }
    doc_service._desired_prefetch_keys = {key2}
    submitted_prefetch = []
    doc_service._prefetch_executor = SimpleNamespace(
        submit=lambda fn, docs, gen, **kwargs: submitted_prefetch.append(
            (fn, docs, gen, kwargs)
        )
    )
    reloaded = []
    doc_service.do_load_image = lambda path: reloaded.append(path)
    doc_service.controller = SimpleNamespace(
        model=SimpleNamespace(
            get_source_image_path=lambda: page1,
            get_regions=lambda: [{"text": "already loaded"}],
        ),
        export_service=SimpleNamespace(has_unsaved_changes=lambda: False),
    )

    logic = EditorLogic.__new__(EditorLogic)
    logic.controller = SimpleNamespace(document_service=doc_service)
    logic._snapshot = FileCatalogSnapshot.empty()
    logic._source_by_key = {}
    logic.file_model = FileListModel()
    logic.file_model.replace_from_snapshot(
        SimpleNamespace(
            editor_files=(page1, page2),
            json_by_file={page1: None, page2: None},
        )
    )
    emitted_items = []
    logic.file_item_translated = SimpleNamespace(
        emit=lambda src, out, jsn: emitted_items.append((src, out, jsn))
    )

    assert logic.file_model.get_file_item(page2).file_type == FileType.UNTRANSLATED

    logic.on_file_translated(page2, output_path=out2, json_path=json2)

    item2 = logic.file_model.get_file_item(page2)
    assert item2.file_type == FileType.SOURCE
    assert item2.json_path == json2
    assert len(emitted_items) == 1
    assert key2 not in doc_service._prefetched_documents
    assert len(submitted_prefetch) == 1
    assert submitted_prefetch[0][1] == [(key2, os.path.abspath(os.path.normpath(page2)))]
    assert submitted_prefetch[0][3] == {"force": True}
    assert reloaded == []


def test_in_flight_prefetch_cannot_write_stale_snapshot_after_file_translated(
    tmp_path, monkeypatch
):
    import editor.controller_document_service as cds_module

    page2 = str(tmp_path / "002.png")
    doc_service = EditorControllerDocumentService.__new__(
        EditorControllerDocumentService
    )
    doc_service._is_shutdown = False
    doc_service._prefetch_lock = threading.RLock()
    doc_service._prefetch_generation = 1
    doc_service._aux_load_executor = None
    key2 = doc_service._prefetch_key(page2)
    doc_service._prefetched_documents = {}
    doc_service._prefetch_key_versions = {}
    doc_service._prefetched_document_versions = {}
    doc_service._desired_prefetch_keys = {key2}
    doc_service._prefetch_executor = SimpleNamespace(submit=lambda *_a, **_kw: None)
    doc_service.controller = SimpleNamespace(
        model=SimpleNamespace(
            get_source_image_path=lambda: None,
            get_regions=lambda: [],
        ),
        export_service=SimpleNamespace(has_unsaved_changes=lambda: False),
    )

    stale_snapshot = DocumentSnapshot(
        source_path=page2,
        image=Image.new("RGB", (4, 4), "white"),
        display_image_path=page2,
        regions=[],
    )

    class _FakeWorker:
        def __init__(self, *_args, **_kwargs):
            pass

        def load(self):
            # Simulate translation completing while this worker was loading pre-translation data
            doc_service.on_file_translated(page2)
            return stale_snapshot

    monkeypatch.setattr(cds_module, "DocumentLoadWorker", _FakeWorker)
    doc_service._prefetch_documents_worker([(key2, page2)], generation=1)

    assert key2 not in doc_service._prefetched_documents
    assert doc_service._take_prefetched_document(page2) is None

