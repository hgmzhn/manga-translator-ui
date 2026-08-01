from __future__ import annotations

import os
import sys
from concurrent.futures import Future
from pathlib import Path
from types import MethodType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

import desktop_qt_ui.app_logic as app_logic_module
from desktop_qt_ui.app_logic import MainAppLogic
from desktop_qt_ui.ui.main_page import runtime as main_view_runtime


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, *args):
        self.values.append(args)


def _logic():
    return SimpleNamespace(
        state_manager=SimpleNamespace(is_translating=lambda: False),
        source_files=[],
        _source_folders={},
        excluded_subfolders=set(),
        excluded_files=set(),
        file_to_folder_map={},
        files_added=_Signal(),
        file_sources_changed=_Signal(),
        logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
        _ui_log=lambda *_args: None,
        _path_key=MainAppLogic._path_key,
        _path_is_within=MainAppLogic._path_is_within,
    )


def test_batch_single_files_do_not_run_pairwise_containment(monkeypatch):
    logic = _logic()
    containment_calls = 0

    def count_containment(path, folder):
        nonlocal containment_calls
        containment_calls += 1
        return MainAppLogic._path_is_within(path, folder)

    logic._path_is_within = count_containment
    monkeypatch.setattr(app_logic_module.os.path, "isdir", lambda _path: False)
    paths = [rf"C:\batch\page{i}.png" for i in range(2000)]

    MainAppLogic.add_files(logic, paths)

    assert len(logic.source_files) == 2000
    assert containment_calls == 0


def test_parent_source_collapses_children_and_readding_excluded_descendant(monkeypatch):
    logic = _logic()
    parent = os.path.normpath(r"C:\book")
    child = os.path.normpath(r"C:\book\page1.png")
    excluded_folder = os.path.normpath(r"C:\book\chapter")
    descendant = os.path.normpath(r"C:\book\chapter\page2.png")
    directory_keys = {
        MainAppLogic._path_key(parent),
        MainAppLogic._path_key(excluded_folder),
    }
    monkeypatch.setattr(
        app_logic_module.os.path,
        "isdir",
        lambda path: MainAppLogic._path_key(path) in directory_keys,
    )

    MainAppLogic.add_files(logic, [child, parent])
    assert logic.source_files == [parent]
    assert logic._source_folders == {MainAppLogic._path_key(parent): parent}

    logic.excluded_subfolders.add(excluded_folder)
    MainAppLogic.add_files(logic, [descendant])
    assert logic.source_files == [parent]
    assert logic.excluded_subfolders == set()


def test_stopping_state_remains_until_worker_and_cleanup_finish(monkeypatch):
    class State:
        def __init__(self):
            self.translating = True
            self.status = ""

        def is_translating(self):
            return self.translating

        def set_translating(self, value):
            self.translating = value

        def set_status_message(self, value):
            self.status = value

    class Worker:
        stopped = False

        def stop(self):
            self.stopped = True

    class MainView:
        stopping = False
        reset = False

        def set_stopping_state(self):
            self.stopping = True

        def reset_progress(self):
            self.reset = True

    state = State()
    worker = Worker()
    main_view = MainView()
    scan_future = Future()
    cleanup_future = Future()
    scheduled = []
    logic = SimpleNamespace(
        state_manager=state,
        current_worker=worker,
        main_view=main_view,
        _stop_requested=False,
        _scan_request_id=1,
        current_task_id=1,
        _scan_future=scan_future,
        _translate_future=None,
        _cleanup_future=None,
        _shutdown_started=False,
        _ui_log=lambda *_args: None,
    )

    def start_cleanup():
        logic._cleanup_future = cleanup_future
        return cleanup_future

    logic._cleanup_after_task = start_cleanup
    logic._finish_stop_task = MethodType(MainAppLogic._finish_stop_task, logic)
    logic._cleanup_stopped_task_when_idle = MethodType(
        MainAppLogic._cleanup_stopped_task_when_idle, logic
    )
    monkeypatch.setattr(
        app_logic_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    assert MainAppLogic.stop_task(logic)
    assert worker.stopped
    assert logic._stop_requested
    assert state.translating
    assert state.status == "正在停止..."
    assert main_view.stopping

    delayed_button = SimpleNamespace(
        setEnabled=lambda *_args: (_ for _ in ()).throw(AssertionError("button overwritten"))
    )
    main_view_runtime.enable_stop_button(
        SimpleNamespace(controller=logic, start_button=delayed_button)
    )

    scheduled.pop(0)[1]()
    assert state.translating
    scan_future.set_result(None)
    scheduled.pop(0)[1]()
    assert state.translating
    cleanup_future.set_result(None)
    scheduled.pop(0)[1]()

    assert not logic._stop_requested
    assert not state.translating
    assert state.status == "任务已停止"
    assert main_view.reset


def test_shutdown_waits_for_worker_before_qt_teardown():
    calls = []

    class Worker:
        def stop(self):
            calls.append("worker.stop")

    class Executor:
        def shutdown(self, *, wait, cancel_futures):
            calls.append(("executor.shutdown", wait, cancel_futures))
            raise RuntimeError("stop after executor assertion")

    logic = SimpleNamespace(
        _shutdown_started=False,
        _scan_request_id=0,
        current_task_id=0,
        current_worker=Worker(),
        state_manager=SimpleNamespace(set_translating=lambda _value: None),
        _task_executor=Executor(),
        _ui_log=lambda *_args: None,
    )

    MainAppLogic.shutdown(logic)

    assert calls == ["worker.stop", ("executor.shutdown", True, True)]
    assert logic._shutdown_started
    assert logic.current_worker is None
