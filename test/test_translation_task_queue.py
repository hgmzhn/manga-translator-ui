from __future__ import annotations

import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

import desktop_qt_ui.app_logic as app_logic_module
from desktop_qt_ui.app_logic import (
    DeferredHtmlSource,
    MainAppLogic,
    TranslationTaskRequest,
)


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, *args):
        self.values.append(args)


class _State:
    def __init__(self):
        self.translating = False
        self.status = ""

    def set_translating(self, value):
        self.translating = bool(value)

    def set_status_message(self, value):
        self.status = str(value)


def _logic_with_file_list():
    logic = SimpleNamespace(
        state_manager=_State(),
        source_files=["/input/first.png", "/input/second.png"],
        _source_folders={},
        excluded_subfolders={"/input/skip"},
        excluded_files={"/input/ignored.png"},
        file_to_folder_map={},
        files_cleared=_Signal(),
        file_sources_changed=_Signal(),
        task_queue_changed=_Signal(),
        _task_queue=[],
        _ui_log=lambda *_args: None,
        logger=SimpleNamespace(info=lambda *_args: None),
    )
    logic._clear_file_list_state = MethodType(
        MainAppLogic._clear_file_list_state, logic
    )
    logic._emit_task_queue_changed = MethodType(
        MainAppLogic._emit_task_queue_changed, logic
    )
    logic._has_active_translation_work = MethodType(
        MainAppLogic._has_active_translation_work, logic
    )
    return logic


def test_submitting_a_task_snapshots_inputs_and_clears_editable_list():
    logic = _logic_with_file_list()

    assert MainAppLogic._enqueue_current_file_list(logic, {"app": {"id": 1}})

    assert logic.source_files == []
    assert logic.excluded_subfolders == set()
    assert logic.excluded_files == set()
    assert len(logic._task_queue) == 1
    request = logic._task_queue[0]
    assert request.source_files == ["/input/first.png", "/input/second.png"]
    assert request.excluded_subfolders == {"/input/skip"}
    assert request.excluded_files == {"/input/ignored.png"}
    assert request.task_config == {"app": {"id": 1}}
    assert logic.state_manager.translating
    assert logic.task_queue_changed.values[-1] == (1,)


def test_submitting_html_only_task_snapshots_pending_sources_and_clears_them():
    logic = _logic_with_file_list()
    logic.source_files = []
    logic._pending_html_sources = [
        DeferredHtmlSource("https://reader.example/chapter", "url", "网址")
    ]
    logic.html_sources_changed = _Signal()
    logic._emit_html_sources_changed = MethodType(
        MainAppLogic._emit_html_sources_changed, logic
    )

    assert MainAppLogic._enqueue_current_file_list(logic, {"app": {"id": 2}})

    assert logic.source_files == []
    assert logic._pending_html_sources == []
    assert logic._task_queue[0].html_sources == [
        DeferredHtmlSource("https://reader.example/chapter", "url", "网址")
    ]
    assert logic.html_sources_changed.values[-1] == (0,)


def test_deferred_html_sources_download_only_when_task_scanning_starts(monkeypatch):
    calls = []

    def fake_download(source, *, output_dir):
        calls.append((source, output_dir))
        return SimpleNamespace(
            image_paths=(f"{output_dir}/page.png",),
            extracted_count=1,
            failures=(),
        )

    monkeypatch.setattr(app_logic_module, "download_html_images_from_url", fake_download)
    source = DeferredHtmlSource("https://reader.example/chapter", "url", "网址")

    class Worker:
        _is_running = True

        def __init__(self):
            self.progress = []

        def _emit_progress(self, message):
            self.progress.append(message)

    worker = Worker()
    logic = SimpleNamespace()

    assert calls == []
    downloaded = MainAppLogic._download_deferred_html_sources(
        logic,
        [source],
        "/tmp/output",
        worker,
    )

    assert calls == [("https://reader.example/chapter", "/tmp/output")]
    assert downloaded == ["/tmp/output/page.png"]
    assert any("正在下载图片" in message for message in worker.progress)


def test_start_file_scanning_merges_downloaded_html_images_before_scan(monkeypatch):
    captured = {}

    class Scanner:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.source_files = list(kwargs["source_files"])
            self.output_base_dir = kwargs["output_base_dir"]
            self._is_running = True

        def _emit_progress(self, _message):
            pass

        def run(self):
            captured["ran_source_files"] = list(self.source_files)

    class Executor:
        def submit(self, callback):
            callback()
            return object()

    request = TranslationTaskRequest(
        ["/input/regular.png"],
        set(),
        set(),
        {"app": {"last_output_path": "/tmp/output"}},
        [DeferredHtmlSource("https://reader.example/chapter", "url", "网址")],
    )
    logic = SimpleNamespace(
        _scan_request_id=0,
        _shutdown_started=False,
        _scan_future=None,
        current_worker=None,
        state_manager=_State(),
        config_service=SimpleNamespace(flush_pending_writes=lambda: True),
        file_service=object(),
        _task_executor=Executor(),
        on_scanning_finished=lambda *args: None,
        on_scanning_error=lambda *args: None,
        on_worker_log=lambda *args: None,
        _ui_log=lambda *_args: None,
    )
    def download_deferred(sources, output_dir, worker):
        captured["download_args"] = (sources, output_dir, worker)
        return ["/tmp/output/downloaded.png"]

    logic._download_deferred_html_sources = download_deferred
    monkeypatch.setattr(app_logic_module, "FileScannerRunnable", Scanner)

    MainAppLogic.start_file_scanning(logic, request.task_config, request=request)

    assert captured["ran_source_files"] == [
        "/input/regular.png",
        "/tmp/output/downloaded.png",
    ]
    assert captured["download_args"][0] == request.html_sources


def test_queue_starts_tasks_in_fifo_order(monkeypatch):
    logic = SimpleNamespace(
        _shutdown_started=False,
        _stop_requested=False,
        _task_queue=[
            TranslationTaskRequest(["first"], set(), set(), {"id": 1}),
            TranslationTaskRequest(["second"], set(), set(), {"id": 2}),
        ],
        _active_task_request=None,
        current_worker=None,
        _scan_future=None,
        _translate_future=None,
        state_manager=_State(),
        task_queue_changed=_Signal(),
    )
    started = []
    logic.start_file_scanning = lambda config, request=None: started.append(
        (config, request.source_files)
    )
    logic._emit_task_queue_changed = MethodType(
        MainAppLogic._emit_task_queue_changed, logic
    )
    logic._has_active_translation_work = MethodType(
        MainAppLogic._has_active_translation_work, logic
    )

    MainAppLogic._start_next_queued_task(logic)
    assert started == [({"id": 1}, ["first"])]
    assert logic._active_task_request.task_config == {"id": 1}
    assert logic._task_queue[0].source_files == ["second"]

    logic._active_task_request = None
    MainAppLogic._start_next_queued_task(logic)
    assert started == [({"id": 1}, ["first"]), ({"id": 2}, ["second"])]


def test_finishing_current_task_keeps_processing_state_when_queue_has_items(monkeypatch):
    logic = SimpleNamespace(
        _active_task_request=object(),
        current_worker=object(),
        _translation_started_at=1.0,
        _translation_total_images=2,
        _task_queue=[object()],
        _shutdown_started=False,
        _stop_requested=False,
        state_manager=_State(),
        _cleanup_after_task=lambda: None,
    )
    logic._start_next_queued_task = MethodType(
        MainAppLogic._start_next_queued_task, logic
    )
    scheduled = []
    monkeypatch.setattr(
        app_logic_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    MainAppLogic._finish_active_task(logic, "任务完成")

    assert logic.state_manager.translating
    assert "队列剩余 1 个任务" in logic.state_manager.status
    assert logic._active_task_request is None
    assert scheduled and scheduled[0][0] == 100
