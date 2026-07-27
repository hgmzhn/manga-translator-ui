import logging
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from editor.controller_export_service import (  # noqa: E402
    EditorControllerExportService,
    ExportJob,
    ExportOutcome,
)


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
    return ExportJob(
        automatic=automatic,
        source_path=source,
        output_path=name,
        image=None,
        regions=[],
        mask=None,
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
