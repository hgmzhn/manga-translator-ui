from __future__ import annotations

import _bootstrap  # noqa: F401  —— sys.path / offscreen / torch 先于 PyQt6

import asyncio
import concurrent.futures
import json
import logging
import os
import queue
import sys
import threading
from pathlib import Path
from unittest import mock

ROOT = _bootstrap.ROOT

from PyQt6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer, pyqtSignal

import services as services_module
from desktop_qt_ui.services import config_service as config_module
from desktop_qt_ui.services.config_service import ConfigService
from desktop_qt_ui.services.log_service import (
    BoundedQueueHandler,
    DrainingQueueListener,
    LogService,
    RecentLogHandler,
)
from desktop_qt_ui.ui.main_page import env_management
from manga_translator.utils.dotenv_utils import read_dotenv_file


def _application() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def _create_config_service(root: Path) -> ConfigService:
    config_dir = root / "config"

    def temp_config_path(*parts: str) -> str:
        return str(config_dir.joinpath(*parts))

    no_op = lambda *args, **kwargs: None
    with (
        mock.patch.object(config_module, "get_config_path", temp_config_path),
        mock.patch.object(config_module, "ensure_custom_api_params_file", no_op),
        mock.patch.object(config_module, "ensure_ai_ocr_prompt_file", no_op),
        mock.patch.object(config_module, "ensure_ai_renderer_prompt_file", no_op),
        mock.patch.object(config_module, "ensure_ai_colorizer_prompt_file", no_op),
    ):
        return ConfigService(str(root))


def test_config_writes_are_coalesced_atomic_and_memory_first(tmp_path: Path) -> None:
    app = _application()
    (tmp_path / ".env").write_text('# keep this comment\nEXISTING="yes"\n', encoding="utf-8")
    service = _create_config_service(tmp_path)
    original_writer = service._write_snapshots
    write_batches = []

    def counted_writer(config_writes, env_snapshot, env_path):
        write_batches.append((len(config_writes), env_snapshot is not None))
        original_writer(config_writes, env_snapshot, env_path)

    service._write_snapshots = counted_writer
    env_key = "MANGA_TRANSLATOR_TEST_BATCHED_ENV"
    try:
        for index in range(100):
            service.current_config.app.last_output_path = f"output-{index}"
            assert service.save_config_file()
            assert service.save_env_var(env_key, f"value-{index}")

        assert service.load_env_vars()[env_key] == "value-99"
        assert os.environ[env_key] == "value-99"
        debounce_loop = QEventLoop()
        QTimer.singleShot(service.SAVE_DEBOUNCE_MS + 100, debounce_loop.quit)
        debounce_loop.exec()
        app.processEvents()
        assert service.flush_pending_writes()
        assert write_batches == [(2, True)]

        user_payload = json.loads(Path(service.user_config_path).read_text(encoding="utf-8"))
        assert user_payload["app"]["last_output_path"] == "output-99"
        assert read_dotenv_file(service.env_path)[env_key] == "value-99"
        assert '# keep this comment' in Path(service.env_path).read_text(encoding="utf-8")
        assert not list(tmp_path.rglob("*.tmp"))

        assert service.save_env_var(env_key, "worker-flush")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(service.flush_pending_writes).result(timeout=5)
        assert read_dotenv_file(service.env_path)[env_key] == "worker-flush"
    finally:
        assert service.shutdown()
        os.environ.pop(env_key, None)


def test_logging_listener_handles_sinks_and_reports_low_level_drops() -> None:
    class CollectHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append((threading.get_ident(), record.getMessage()))

    recent_view = LogService.__new__(LogService)
    LogService.clear_recent_logs(recent_view)
    log_queue = queue.Queue(maxsize=128)
    queue_handler = BoundedQueueHandler(log_queue)
    collector = CollectHandler()
    listener = DrainingQueueListener(log_queue, collector, RecentLogHandler())
    logger = logging.getLogger("test.ui_io_pipeline")
    logger.handlers = [queue_handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    caller_thread = threading.get_ident()
    formatted_threads = []

    class DeferredValue:
        def __str__(self):
            formatted_threads.append(threading.get_ident())
            return "deferred"

    listener.start()
    try:
        logger.info("format-%s", DeferredValue())
        for index in range(50):
            logger.info("record-%d", index)
    finally:
        logger.handlers = []
        listener.stop()

    assert len(collector.records) == 51
    assert all(thread_id != caller_thread for thread_id, _message in collector.records)
    assert formatted_threads and all(thread_id != caller_thread for thread_id in formatted_threads)
    assert LogService.get_recent_logs(recent_view, limit=1)[0]["message"] == "record-49"

    tiny_queue = queue.Queue(maxsize=1)
    bounded = BoundedQueueHandler(tiny_queue)
    first = logging.LogRecord("drop-test", logging.INFO, __file__, 1, "first", (), None)
    second = logging.LogRecord("drop-test", logging.INFO, __file__, 2, "second", (), None)
    third = logging.LogRecord("drop-test", logging.INFO, __file__, 3, "third", (), None)
    bounded.handle(first)
    bounded.handle(second)
    assert tiny_queue.get_nowait().getMessage() == "first"
    bounded.handle(third)
    assert "已丢弃 1 条" in tiny_queue.get_nowait().getMessage()
    bounded.flush_dropped_summary()
    assert "已丢弃 1 条" in tiny_queue.get_nowait().getMessage()


def test_api_future_result_returns_to_qt_thread() -> None:
    app = _application()
    caller_thread = threading.get_ident()

    class FakeAsyncService:
        def __init__(self):
            self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def submit_task(self, coro):
            return self.executor.submit(asyncio.run, coro)

    class Progress(QObject):
        rejected = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True

        def wasCanceled(self):
            return False

    class Owner(QObject):
        api_task_finished = pyqtSignal(str, object)

        def __init__(self):
            super().__init__()
            self._active_api_tasks = {}

    async def work():
        await asyncio.sleep(0.01)
        return 42

    service = FakeAsyncService()
    owner = Owner()
    progress = Progress()
    results = []
    loop = QEventLoop()
    owner.api_task_finished.connect(
        lambda kind, future: env_management.on_api_task_future_finished(owner, kind, future)
    )

    def on_finished(value, error):
        results.append((value, error, threading.get_ident()))
        loop.quit()

    try:
        with mock.patch.object(services_module, "get_async_service", return_value=service):
            assert env_management._start_managed_api_task(
                owner,
                "test",
                work(),
                progress,
                on_finished,
            )
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            app.processEvents()
    finally:
        service.executor.shutdown(wait=True)

    assert results == [(42, None, caller_thread)]
    assert progress.closed
    assert not owner._active_api_tasks


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        test_config_writes_are_coalesced_atomic_and_memory_first(Path(temp_dir))
    test_logging_listener_handles_sinks_and_reports_low_level_drops()
    test_api_future_result_returns_to_qt_thread()
    print("ui io pipeline regression checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
