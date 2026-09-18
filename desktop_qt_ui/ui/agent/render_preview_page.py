"""Read-only Agent render preview with automatic project sidecar discovery."""

from __future__ import annotations

import weakref
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, LineEdit, PrimaryPushButton, PushButton, TitleLabel

from manga_translator.agent.application.preview import RenderPreviewSession
from manga_translator.agent.domain.tool_models import ToolError
from manga_translator.image_formats import IMAGE_FILE_DIALOG_FILTER


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

    def __init__(self, t_func, parent=None):
        super().__init__(parent)
        self._t = t_func
        self._session = RenderPreviewSession()
        self._generation = 0
        self._active = None
        self._closing = False
        self._close_future = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.addWidget(TitleLabel(self._t("Agent render preview"), self))
        description = BodyLabel(self._t("Agent render preview description"), self)
        description.setWordWrap(True)
        layout.addWidget(description)
        files = QHBoxLayout()
        self.path_input = LineEdit(self)
        self.path_input.setPlaceholderText(self._t("Agent render source placeholder"))
        self.browse_button = PushButton(self._t("Agent choose image"), self)
        self.load_button = PrimaryPushButton(self._t("Agent load render"), self)
        files.addWidget(self.path_input, 1)
        files.addWidget(self.browse_button)
        files.addWidget(self.load_button)
        layout.addLayout(files)
        controls = QHBoxLayout()
        self.fit_button = PushButton(self._t("Agent fit preview"), self)
        self.actual_button = PushButton("100%", self)
        self.cancel_button = PushButton(self._t("Cancel"), self)
        self.cancel_button.setEnabled(False)
        controls.addWidget(self.fit_button)
        controls.addWidget(self.actual_button)
        controls.addStretch()
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
        self.cancel_button.clicked.connect(self.cancel)
        self.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        # Amortize process/font startup while the user chooses a file.
        self._warmup = self._submit(self._session.warmup(), notify=False)

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
        path, _ = QFileDialog.getOpenFileName(self, self._t("Agent choose image"), initial,
                                             IMAGE_FILE_DIALOG_FILTER)
        if path:
            self.load_file(path)

    def _load_input(self):
        path = self.path_input.text().strip().strip('"')
        if path:
            self.load_file(path)

    def load_file(self, path):
        if self._closing:
            return
        self.cancel()
        self._generation += 1
        self.path_input.setText(str(path))
        self.preview.set_png(None)
        self.image_ready.emit(None, None)
        self.status.setText(self._t("Agent render loading"))
        try:
            self._active = self._submit(self._session.load(str(path)))
            self.cancel_button.setEnabled(True)
        except Exception as error:
            self.status.setText(self._t("Agent render failed") + ": " + str(error))

    @pyqtSlot(int, object)
    def _finished(self, generation, future):
        if self._closing or generation != self._generation or future is not self._active:
            return
        self._active = None
        self.cancel_button.setEnabled(False)
        if future.cancelled():
            return
        try:
            result = future.result()
            payload = result["payload"]
            self.preview.set_png(payload["image"])
            self.image_ready.emit(payload["image"], result["regions"])
            self.status.setText(self._t(
                "Agent render ready", width=payload["width"], height=payload["height"],
                regions=len(result["regions"]), elapsed=round(result["load_ms"]),
                render=round(payload.get("render_ms", 0)),
            ))
            self.status.setToolTip(str(Path(result["source_path"])) + "\n" + str(result["base_path"]))
        except Exception as error:
            if isinstance(error, ToolError) and error.code in {"missing_project", "missing_asset", "base_dimension_mismatch"}:
                detail = self._t("Agent render missing data")
            else:
                detail = str(error)
            self.status.setText(self._t("Agent render failed") + ": " + detail)

    def cancel(self):
        self._generation += 1
        if self._active is not None:
            self._active.cancel()
            self._active = None
        self.cancel_button.setEnabled(False)
        if not self._closing:
            self.status.setText(self._t("Agent render cancelled"))

    def shutdown(self):
        if not self._closing:
            self._closing = True
            self.cancel()
            self._warmup.cancel()
            self._close_future = self._submit(self._session.close(), notify=False)
        return self._close_future
