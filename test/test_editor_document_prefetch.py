import _bootstrap  # noqa: F401, I001

import concurrent.futures
import logging
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from editor.controller_document_service import EditorControllerDocumentService
from editor.document_load_worker import DocumentLoadWorker
from editor.document_state import DocumentSnapshot
from editor.editor_logic import EditorLogic
from editor.core.resource_manager import ResourceManager


def test_neighbor_range_prefers_forward_then_backward_with_radius_two(tmp_path):
    paths = [str(tmp_path / f"page-{index}.png") for index in range(6)]
    logic = EditorLogic.__new__(EditorLogic)
    logic.file_model = SimpleNamespace(
        files=[SimpleNamespace(path=path) for path in paths]
    )

    assert logic._adjacent_image_paths(paths[2]) == [
        paths[3],
        paths[1],
        paths[4],
        paths[0],
    ]


def test_document_worker_prefetches_complete_snapshot_without_changing_current(tmp_path):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (32, 24), (90, 40, 20)).save(image_path)
    manager = ResourceManager()
    logger = logging.getLogger(__name__)
    service = SimpleNamespace(
        controller=SimpleNamespace(),
        logger=logger,
        resource_manager=manager,
        resolve_editor_image_paths=lambda _path: (str(image_path), str(image_path)),
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        result = DocumentLoadWorker(
            service,
            str(image_path),
            executor,
            prefetch=True,
        ).load()

    assert isinstance(result, DocumentSnapshot)
    assert result.source_path == str(image_path)
    assert result.display_image_path == str(image_path)
    assert result.image.size == (32, 24)
    assert result.source_qimage is not None
    assert manager.get_current_image() is None


def test_prefetched_snapshot_is_consumed_once():
    service = EditorControllerDocumentService.__new__(EditorControllerDocumentService)
    service._prefetch_lock = threading.RLock()
    service._prefetched_documents = {}
    service._desired_prefetch_keys = set()
    path = "page.png"
    snapshot = DocumentSnapshot(
        source_path=path,
        image=Image.new("RGB", (4, 3), "white"),
        display_image_path=path,
    )
    service._prefetched_documents[service._prefetch_key(path)] = snapshot

    assert service._take_prefetched_document(path) is snapshot
    assert service._take_prefetched_document(path) is None


def test_load_uses_prefetched_snapshot_without_submitting_disk_worker():
    class Signal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    class RejectingExecutor:
        def submit(self, *_args, **_kwargs):
            raise AssertionError("cache hit must not submit a disk-load worker")

    snapshot = DocumentSnapshot(
        source_path="page.png",
        image=Image.new("RGB", (4, 3), "white"),
        display_image_path="page.png",
    )
    signal = Signal()
    activated = []
    service = EditorControllerDocumentService.__new__(EditorControllerDocumentService)
    service._is_shutdown = False
    service._load_generation = 7
    service._active_load_future = None
    service.clear_editor_state = lambda: None
    service._take_prefetched_document = lambda _path: snapshot
    resource_manager = SimpleNamespace(
        activate_prefetched_image=lambda *args, **kwargs: activated.append(
            (args, kwargs)
        )
    )
    service.controller = SimpleNamespace(
        get_toast_manager=lambda: None,
        _load_result_ready=signal,
        _loading_toast=None,
        resource_manager=resource_manager,
        logger=logging.getLogger(__name__),
    )
    service._load_executor = RejectingExecutor()

    service.do_load_image("page.png")

    assert len(activated) == 1
    assert signal.values == [(7, snapshot)]


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        test_neighbor_range_prefers_forward_then_backward_with_radius_two(tmp_path)
        test_document_worker_prefetches_complete_snapshot_without_changing_current(
            tmp_path
        )
    test_prefetched_snapshot_is_consumed_once()
    test_load_uses_prefetched_snapshot_without_submitting_disk_worker()
    print("4 document prefetch regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
