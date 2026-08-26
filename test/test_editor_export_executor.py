import _bootstrap  # noqa: F401, I001

import logging
import threading
import time

import numpy as np
import pytest
from PIL import Image

from editor.controller_export_service import (
    EditorControllerExportService,
    ExportJob,
    ExportOutcome,
)
from editor.document_state import ExportBase
from editor.inpaint_state import InpaintKey


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, value):
        self.values.append(value)


class _Controller:
    logger = logging.getLogger(__name__)

    def __init__(self):
        self._export_queue_status_signal = _Signal()
        self._export_job_finished_signal = _Signal()

    def get_toast_manager(self):
        return None


def _job(
    name: str,
    *,
    source: str = "same-source.png",
    automatic: bool = True,
) -> ExportJob:
    image = np.zeros((4, 6, 3), dtype=np.uint8)
    return ExportJob(
        automatic=automatic,
        source_path=source,
        output_path=name,
        export_base=ExportBase("source", image, image, None, None),
        regions=[],
        config={},
    )


def _success(job: ExportJob) -> ExportOutcome:
    return ExportOutcome(
        automatic=job.automatic,
        source_path=job.source_path,
        output_path=job.output_path,
        success=True,
    )


def test_serial_executor_keeps_only_latest_pending_auto_export():
    controller = _Controller()
    service = EditorControllerExportService(controller)
    first_started = threading.Event()
    release_first = threading.Event()
    ran = []

    def run(job):
        ran.append(job.output_path)
        if job.output_path == "first":
            first_started.set()
            assert release_first.wait(2)
        return _success(job)

    service.execute_export_job = run
    try:
        first = service._submit_job(_job("first"))
        assert first is not None and first_started.wait(2)
        replaced = service._submit_job(_job("replaced"))
        latest = service._submit_job(_job("latest"))
        assert replaced is not None and replaced.cancelled()

        release_first.set()
        assert latest is not None
        first.result(timeout=2)
        latest.result(timeout=2)
    finally:
        release_first.set()
        service.shutdown()

    assert ran == ["first", "latest"]
    assert service.unfinished_count() == 0
    assert controller._export_queue_status_signal.values[-1] == 0


def test_different_images_run_fifo_with_single_concurrency():
    service = EditorControllerExportService(_Controller())
    lock = threading.Lock()
    ran = []
    active = 0
    max_active = 0

    def run(job):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        ran.append(job.output_path)
        with lock:
            active -= 1
        return _success(job)

    service.execute_export_job = run
    try:
        futures = [
            service._submit_job(
                _job(str(index), source=f"page-{index}.png", automatic=False)
            )
            for index in range(3)
        ]
        assert all(future is not None for future in futures)
        assert [future.result(timeout=2).output_path for future in futures] == ["0", "1", "2"]
    finally:
        service.shutdown()

    assert ran == ["0", "1", "2"]
    assert max_active == 1


def test_failed_job_does_not_block_the_next_export():
    controller = _Controller()
    service = EditorControllerExportService(controller)

    def run(job):
        if job.output_path == "bad":
            raise RuntimeError("boom")
        return _success(job)

    service.execute_export_job = run
    try:
        failed = service._submit_job(_job("bad", automatic=False))
        succeeded = service._submit_job(_job("good", automatic=False))
        assert failed is not None and succeeded is not None
        with pytest.raises(RuntimeError, match="boom"):
            failed.result(timeout=2)
        assert succeeded.result(timeout=2).success
    finally:
        service.shutdown()

    assert [outcome.success for outcome in controller._export_job_finished_signal.values] == [
        False,
        True,
    ]


def test_shutdown_drains_all_accepted_exports():
    service = EditorControllerExportService(_Controller())
    ran = []

    def run(job):
        time.sleep(0.01)
        ran.append(job.output_path)
        return _success(job)

    service.execute_export_job = run
    futures = [
        service._submit_job(_job(str(index), automatic=False))
        for index in range(3)
    ]
    assert all(future is not None for future in futures)

    service.shutdown()

    assert ran == ["0", "1", "2"]
    assert all(future.done() and future.result().success for future in futures)
    assert service.unfinished_count() == 0


def _backend_inpaint_base() -> ExportBase:
    source = np.full((6, 8, 3), 37, dtype=np.uint8)
    mask = np.zeros((6, 8), dtype=np.uint8)
    mask[1:5, 2:7] = 255
    return ExportBase(
        "backend_inpaint",
        source,
        source,
        mask,
        InpaintKey(3, 5, 7, 11),
    )


def test_backend_inpaint_failure_does_not_write_output(monkeypatch, tmp_path):
    from services.export_service import ExportService

    service = ExportService()
    output_path = tmp_path / "failed.png"
    saved = []

    def fail_backend(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_execute_backend_render", fail_backend)
    monkeypatch.setattr(
        service,
        "_save_rendered_image",
        lambda *_args, **_kwargs: saved.append(True),
    )

    outcome = service._perform_backend_render_export(
        _backend_inpaint_base(),
        [],
        {},
        str(output_path),
        source_image_path=str(tmp_path / "page.png"),
    )

    assert outcome is None
    assert saved == []
    assert not output_path.exists()


def test_ai_renderer_cannot_bypass_required_backend_inpaint(monkeypatch, tmp_path):
    from services.export_service import BackendRenderResult, ExportService

    service = ExportService()
    output_path = tmp_path / "ai-rendered.png"
    saved = []

    def execute_backend(*_args, **_kwargs):
        return BackendRenderResult(Image.new("RGB", (8, 6), "white"), None)

    monkeypatch.setattr(service, "_execute_backend_render", execute_backend)
    monkeypatch.setattr(
        service,
        "_save_rendered_image",
        lambda *_args, **_kwargs: saved.append(True),
    )

    outcome = service._perform_backend_render_export(
        _backend_inpaint_base(),
        [],
        {"render": {"renderer": "openai_renderer"}},
        str(output_path),
        source_image_path=str(tmp_path / "page.png"),
    )

    assert outcome is None
    assert saved == []
    assert not output_path.exists()


def test_backend_inpaint_success_persists_and_returns_generated_artifact(
    monkeypatch,
    tmp_path,
):
    from services.export_service import BackendRenderResult, ExportService

    service = ExportService()
    export_base = _backend_inpaint_base()
    generated = np.full((6, 8, 3), 149, dtype=np.uint8)
    persisted = []
    saved = []

    monkeypatch.setattr(
        service,
        "_execute_backend_render",
        lambda *_args, **_kwargs: BackendRenderResult(
            Image.new("RGB", (8, 6), "white"),
            generated,
        ),
    )
    monkeypatch.setattr(
        service,
        "_persist_backend_inpainted_image",
        lambda *args, **kwargs: persisted.append((args, kwargs)) or "paired.png",
    )
    monkeypatch.setattr(
        service,
        "_save_rendered_image",
        lambda *args, **kwargs: saved.append((args, kwargs)),
    )

    outcome = service._perform_backend_render_export(
        export_base,
        [],
        {},
        str(tmp_path / "rendered.png"),
        source_image_path=str(tmp_path / "page.png"),
    )

    assert outcome is not None
    assert np.array_equal(outcome.generated_inpainted_image, generated)
    assert len(persisted) == 1
    persist_args, _ = persisted[0]
    assert persist_args[0] == str(tmp_path / "page.png")
    assert np.array_equal(persist_args[1], generated)
    assert persist_args[2] == {}
    assert len(saved) == 1


def test_export_job_wraps_generated_image_with_snapshot_identity(
    monkeypatch,
    tmp_path,
):
    from services.export_service import BackendExportResult, ExportService

    generated = np.full((6, 8, 3), 203, dtype=np.uint8)
    monkeypatch.setattr(
        ExportService,
        "_perform_backend_render_export",
        lambda *_args, **_kwargs: BackendExportResult(generated),
    )
    export_base = _backend_inpaint_base()
    job = ExportJob(
        automatic=False,
        source_path=str(tmp_path / "page.png"),
        output_path=str(tmp_path / "rendered.png"),
        export_base=export_base,
        regions=[],
        config={},
    )
    service = EditorControllerExportService(_Controller())
    try:
        outcome = service.execute_export_job(job)
    finally:
        service.shutdown()

    assert outcome.success
    assert outcome.generated_artifact is not None
    assert outcome.generated_artifact.key == export_base.inpaint_key
    assert np.array_equal(outcome.generated_artifact.mask, export_base.mask)
    assert np.array_equal(outcome.generated_artifact.image, generated)
