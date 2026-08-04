"""批量管理的后台执行 —— 扫描与写回都不能占着 UI 线程。

结构对齐 ``file_list_data_service``：``ThreadPoolExecutor`` + 每个频道一个
generation + ``threading.Event`` 取消；纯逻辑全在 ``batch_edit_engine`` 里，
这层只负责调度与信号。接收端一律用 ``Qt.ConnectionType.QueuedConnection``，
因为回调是在 worker 线程上发的信号。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Iterable, Optional, Sequence

from PyQt6.QtCore import QObject, pyqtSignal

from .batch_edit_engine import (
    ApplyReport,
    BatchEditCancelled,
    RestoreReport,
    ScanResult,
    apply_matches,
    restore_files,
    scan_matches,
)

CHANNEL_SCAN = "scan"
CHANNEL_APPLY = "apply"
CHANNEL_RESTORE = "restore"


class BatchEditService(QObject):
    """扫描/执行各占一个频道，每个频道只接受最新 generation 的结果。"""

    scan_ready = pyqtSignal(int, object)        # generation, ScanResult
    apply_ready = pyqtSignal(int, object)       # generation, ApplyReport
    restore_ready = pyqtSignal(int, object)     # generation, RestoreReport
    progress = pyqtSignal(str, int, int, int)   # channel, generation, done, total
    error = pyqtSignal(str, int, str)           # channel, generation, message

    def __init__(self, parent: Optional[QObject] = None, max_workers: int = 2):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="batch_edit")
        self._lock = threading.Lock()
        self._generations: dict[str, int] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future] = {}
        self._shutdown = False

    # ─── 提交 ───

    def _begin(self, channel: str) -> tuple[int, threading.Event]:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("BatchEditService 已关闭")
            generation = self._generations.get(channel, 0) + 1
            self._generations[channel] = generation
            previous = self._cancel_events.get(channel)
            if previous is not None:
                previous.set()
            cancel_event = threading.Event()
            self._cancel_events[channel] = cancel_event
        return generation, cancel_event

    def _submit(self, channel: str, generation: int, func, *args, **kwargs) -> int:
        future = self._executor.submit(func, *args, **kwargs)
        with self._lock:
            self._futures[channel] = future
        future.add_done_callback(
            lambda completed, name=channel, token=generation: self._on_done(name, token, completed)
        )
        return generation

    def request_scan(self, json_paths: Sequence[str], scheme: dict) -> int:
        generation, cancel_event = self._begin(CHANNEL_SCAN)
        return self._submit(
            CHANNEL_SCAN,
            generation,
            scan_matches,
            tuple(json_paths),
            scheme,
            cancel_event=cancel_event,
            progress=self._progress_reporter(CHANNEL_SCAN, generation),
        )

    def request_apply(
        self,
        selected: Iterable[tuple[str, str, int]],
        scheme: dict,
        backup: bool = True,
    ) -> int:
        generation, cancel_event = self._begin(CHANNEL_APPLY)
        return self._submit(
            CHANNEL_APPLY,
            generation,
            apply_matches,
            tuple(selected),
            scheme,
            backup=backup,
            cancel_event=cancel_event,
            progress=self._progress_reporter(CHANNEL_APPLY, generation),
        )

    # ─── 回调 ───

    def request_restore(self, json_paths: Sequence[str]) -> int:
        generation, cancel_event = self._begin(CHANNEL_RESTORE)
        return self._submit(
            CHANNEL_RESTORE,
            generation,
            restore_files,
            tuple(json_paths),
            cancel_event=cancel_event,
            progress=self._progress_reporter(CHANNEL_RESTORE, generation),
        )

    # ─── 回调 ───

    def _progress_reporter(self, channel: str, generation: int):
        def report(done: int, total: int) -> None:
            if not self._is_active(channel, generation):
                return
            try:
                self.progress.emit(channel, generation, done, total)
            except RuntimeError:
                pass
        return report

    def _is_active(self, channel: str, generation: int) -> bool:
        with self._lock:
            return not self._shutdown and self._generations.get(channel) == generation

    def _on_done(self, channel: str, generation: int, future: Future) -> None:
        try:
            result = future.result()
        except BatchEditCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - 后台异常必须回到 UI，不能吞
            if self._is_active(channel, generation):
                try:
                    self.error.emit(channel, generation, str(exc))
                except RuntimeError:
                    pass
            return

        with self._lock:
            active = not self._shutdown and self._generations.get(channel) == generation
            if self._futures.get(channel) is future:
                self._futures.pop(channel, None)
        if not active:
            return
        try:
            if isinstance(result, ScanResult):
                self.scan_ready.emit(generation, result)
            elif isinstance(result, ApplyReport):
                self.apply_ready.emit(generation, result)
            elif isinstance(result, RestoreReport):
                self.restore_ready.emit(generation, result)
        except RuntimeError:
            pass

    # ─── 取消 / 关闭 ───

    def cancel(self, channel: str) -> None:
        with self._lock:
            self._generations[channel] = self._generations.get(channel, 0) + 1
            cancel_event = self._cancel_events.pop(channel, None)
            future = self._futures.pop(channel, None)
        if cancel_event is not None:
            cancel_event.set()
        if future is not None:
            future.cancel()

    def cancel_all(self) -> None:
        for channel in (CHANNEL_SCAN, CHANNEL_APPLY, CHANNEL_RESTORE):
            self.cancel(channel)

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            cancel_events = tuple(self._cancel_events.values())
            self._cancel_events.clear()
            self._futures.clear()
        for cancel_event in cancel_events:
            cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
