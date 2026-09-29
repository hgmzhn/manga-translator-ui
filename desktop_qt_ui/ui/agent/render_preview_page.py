"""Load an Agent page and display the exact canvases returned by its backend."""

from __future__ import annotations

import weakref
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, LineEdit, PrimaryPushButton, PushButton

from manga_translator.agent.application.preview import RenderPreviewSession
from manga_translator.agent.domain.tool_models import ToolError


class _ImagePreview(QScrollArea):
    """Pan with scrollbars; fit-to-window and 1:1 never alter the source image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidgetResizable(False)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidget(self.label)
        self._source = QPixmap()
        self._fit = True

    def set_png(self, data):
        image = QPixmap()
        if data and not image.loadFromData(data, "PNG"):
            raise ValueError("Could not decode the rendered PNG")
        self._source = image
        self._update_image()

    def fit(self):
        self._fit = True
        self._update_image()

    def actual_size(self):
        self._fit = False
        self._update_image()

    def _update_image(self):
        if self._source.isNull():
            self.label.clear()
            self.label.resize(1, 1)
            return
        image = self._source
        if self._fit:
            image = image.scaled(self.viewport().size(), Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(image)
        self.label.resize(image.size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self._update_image()


class RenderPreviewPage(QWidget):
    finished = pyqtSignal(int, object)
    image_ready = pyqtSignal(object, object)
    workspace_ready = pyqtSignal(object)
    pages_ready = pyqtSignal(object)
    page_selected = pyqtSignal(object)

    def __init__(self, t_func, parent=None, *, config_service=None):
        super().__init__(parent)
        self._t = t_func
        self._config_service = config_service
        self._session = RenderPreviewSession(
            include_system_fonts=self._include_system_fonts_enabled()
        )
        self._generation = 0
        self._active = None
        self._active_kind = None
        self._pending_refresh = False
        self._closing = False
        self._close_future = None
        self._context_id = None
        self._current_page = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        files = QHBoxLayout()
        files.setSpacing(8)
        self.path_input = LineEdit(self)
        self.path_input.setPlaceholderText(self._t("Agent render folder placeholder"))
        self.path_input.setToolTip(self._t("Agent render folder placeholder"))
        self.browse_button = PushButton(self._t("Agent choose folder"), self)
        self.load_button = PrimaryPushButton(self._t("Agent load render"), self)
        files.addWidget(self.path_input, 1)
        files.addWidget(self.browse_button)
        files.addWidget(self.load_button)
        layout.addLayout(files)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.current_page_label = BodyLabel(self._t("Agent render choose first"), self)
        self.current_page_label.setTextFormat(Qt.TextFormat.PlainText)
        self.current_page_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        controls.addWidget(self.current_page_label, 1)
        self.fit_button = PushButton(self._t("Agent fit preview"), self)
        self.actual_button = PushButton("100%", self)
        self.refresh_button = PushButton(self._t("Agent refresh preview"), self)
        self.refresh_button.setEnabled(False)
        self.cancel_button = PushButton(self._t("Cancel"), self)
        self.cancel_button.setEnabled(False)
        controls.addWidget(self.fit_button)
        controls.addWidget(self.actual_button)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.preview = _ImagePreview(self)
        layout.addWidget(self.preview, 1)
        self.status = BodyLabel(self._t("Agent render choose first"), self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.browse_button.clicked.connect(self._browse)
        self.load_button.clicked.connect(self._load_input)
        self.path_input.returnPressed.connect(self._load_input)
        self.fit_button.clicked.connect(self.preview.fit)
        self.actual_button.clicked.connect(self.preview.actual_size)
        self.refresh_button.clicked.connect(self.refresh_selected_page)
        self.cancel_button.clicked.connect(self.cancel)
        if self._config_service is not None:
            self._config_service.config_changed.connect(self._config_changed)
        self.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        # Amortize process/font startup while the user chooses a file.
        self._warmup = self._submit(self._session.warmup(), notify=False)

    def _include_system_fonts_enabled(self) -> bool:
        try:
            config = self._config_service.get_config()
            render = getattr(config, "render", None)
            return not bool(getattr(render, "disable_system_fonts", False))
        except Exception:
            return True

    @pyqtSlot(dict)
    def _config_changed(self, config):
        render = config.get("render", {}) if isinstance(config, dict) else {}
        self._session.set_system_fonts_enabled(
            not bool(render.get("disable_system_fonts", False))
        )

    def _submit(self, coroutine, *, notify=True):
        from services import get_async_service

        try:
            service = get_async_service()
            future = service.submit_task(coroutine) if service is not None else None
            if future is None:
                raise RuntimeError("Background service is unavailable")
        except Exception:
            coroutine.close()
            raise
        if notify:
            page_ref = weakref.ref(self)
            generation = self._generation

            def complete(done):
                page = page_ref()
                if page is not None:
                    try:
                        page.finished.emit(generation, done)
                    except RuntimeError:
                        pass
            future.add_done_callback(complete)
        return future

    def _browse(self):
        initial = self.path_input.text().strip()
        path = QFileDialog.getExistingDirectory(self, self._t("Agent choose folder"), initial)
        if path:
            self.load_file(path)

    def _load_input(self):
        path = self.path_input.text().strip().strip('"')
        if path:
            self.load_file(path)

    def load_file(self, path):
        if self._closing:
            return
        self._session.set_system_fonts_enabled(self._include_system_fonts_enabled())
        self._cancel_active()
        self.path_input.setText(str(path))
        self.path_input.setToolTip(str(path))
        self.preview.set_png(None)
        self._context_id = None
        self._current_page = None
        self.refresh_button.setEnabled(False)
        self._update_page_title()
        self.workspace_ready.emit(None)
        self.pages_ready.emit([])
        self.page_selected.emit(None)
        self.image_ready.emit(None, None)
        self.status.setText(self._t("Agent render loading"))
        try:
            self._active_kind = "load"
            self._active = self._submit(self._session.load(str(path)))
            self.cancel_button.setEnabled(True)
        except Exception as error:
            self._active_kind = None
            self.status.setText(self._t("Agent render failed") + ": " + str(error))

    @property
    def current_page(self):
        return dict(self._current_page) if self._current_page is not None else None

    @pyqtSlot(object)
    def select_page(self, page_ref):
        """Navigate inside the loaded workspace without restarting its agents."""
        if self._closing or self._context_id is None or not isinstance(page_ref, dict):
            return
        if not ("id" in page_ref or {"folder", "name"} <= page_ref.keys()):
            return
        if (self._same_page(page_ref, self._current_page)
                and (self._active is not None or not self.preview._source.isNull())):
            return
        self._navigate(page_ref)

    @pyqtSlot()
    def refresh_selected_page(self):
        """Refresh a selected page after a task edits its workspace snapshot."""
        if self._closing or self._context_id is None or self._current_page is None:
            return
        if self._active is not None:
            self._pending_refresh = True
            return
        self._navigate(self._current_page, refresh=True)

    def _navigate(self, page_ref, *, refresh=False):
        self._cancel_active()
        self._current_page = {key: page_ref[key] for key in ("id", "folder", "name") if key in page_ref}
        self._update_page_title()
        self.page_selected.emit(self.current_page)
        if not refresh:
            self.preview.set_png(None)
            self.image_ready.emit(None, None)
        self.status.setText(self._t("Agent render refreshing" if refresh else "Agent render page loading"))
        try:
            self._active_kind = "refresh" if refresh else "navigate"
            self._active = self._submit(self._session.select_page(self.current_page))
            self.cancel_button.setEnabled(True)
        except Exception as error:
            self._active_kind = None
            self.status.setText(self._t("Agent render failed") + ": " + str(error))

    @staticmethod
    def _same_page(left, right):
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        if "id" in left and "id" in right:
            return left["id"] == right["id"] and all(
                left[key] == right[key] for key in ("folder", "name")
                if key in left and key in right
            )
        return ({"folder", "name"} <= left.keys() and {"folder", "name"} <= right.keys()
                and all(left[key] == right[key] for key in ("folder", "name")))

    def _update_page_title(self):
        page = self._current_page
        if page is None:
            title = self._t("Agent render choose first")
        else:
            number = f"{page['id']:03d}" if type(page.get("id")) is int else "?"
            title = self._t("Agent render current page", number=number,
                            path=f"{page.get('folder', '.')}/{page.get('name', '')}")
        self.current_page_label.setText(title)
        self.current_page_label.setToolTip(title)

    @pyqtSlot(int, object)
    def _finished(self, generation, future):
        if self._closing or generation != self._generation or future is not self._active:
            return
        operation = self._active_kind
        self._active = None
        self._active_kind = None
        refresh_after = self._pending_refresh
        self._pending_refresh = False
        self.cancel_button.setEnabled(False)
        if future.cancelled():
            return
        try:
            result = future.result()
            payload = result["payload"]
            if operation == "load":
                self._context_id = result["context"].task_id
                self.workspace_ready.emit(result["context"])
                self.pages_ready.emit(result.get("pages", []))
            elif (result["context"].task_id != self._context_id
                    or not self._same_page(result["current_page"], self._current_page)):
                return
            self._current_page = result["current_page"]
            self._update_page_title()
            self.refresh_button.setEnabled(True)
            self.page_selected.emit(self.current_page)
            self.preview.set_png(payload["image"])
            self.image_ready.emit(payload["image"], result["regions"])
            self.status.setText(self._t(
                "Agent render ready", width=payload["width"], height=payload["height"],
                regions=len(result["regions"]), elapsed=round(result["load_ms"]),
                render=round(payload.get("render_ms", 0)),
            ))
            self.status.setToolTip(str(result.get("source_path") or "") + "\n"
                                   + str(result.get("base_path") or ""))
            if refresh_after:
                self.refresh_selected_page()
        except Exception as error:
            if isinstance(error, ToolError) and error.code in {"missing_project", "missing_asset", "base_dimension_mismatch"}:
                detail = self._t("Agent render missing data")
            else:
                detail = str(error)
            self.status.setText(self._t("Agent render failed") + ": " + detail)

    @pyqtSlot(object)
    def show_canvas(self, canvas):
        if (self._closing or canvas.context_id != self._context_id
                or not self._same_page(canvas.page, self._current_page)):
            return
        # Tool canvases intentionally omit revision metadata. Reading the latest
        # snapshot prevents a queued canvas from reverting a newer navigation.
        # During navigation this coalesces into one refresh after it completes.
        self.refresh_selected_page()

    def _cancel_active(self):
        self._generation += 1
        if self._active is not None:
            self._active.cancel()
            self._active = None
        self._active_kind = None
        self._pending_refresh = False
        self.cancel_button.setEnabled(False)

    def cancel(self):
        self._cancel_active()
        if not self._closing:
            self.status.setText(self._t("Agent render cancelled"))

    def shutdown(self):
        if not self._closing:
            self._closing = True
            self._context_id = None
            self._current_page = None
            self.workspace_ready.emit(None)
            self.cancel()
            self._warmup.cancel()
            self._close_future = self._submit(self._session.close(), notify=False)
        return self._close_future
