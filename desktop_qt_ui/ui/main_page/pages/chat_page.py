"""Basic chat UI using the application's background asyncio service."""
from __future__ import annotations

import json
import weakref
from html import escape
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    TitleLabel,
)

from manga_translator.agent.domain.chat import ChatActivity, ChatCanvas, ChatImage
from ui.agent.conversation_view import ConversationView, ToolRecord
from ui.agent.image_input import ImageAttachmentStrip, ImagePasteTextEdit
from ui.widgets.collapsible_frame import CollapsibleFrame
from ui.widgets.wheel_filter import NoWheelComboBox as ComboBox


@dataclass
class _DisplayedMessage:
    role: str
    chunks: list[str] = field(default_factory=list)
    image_sizes: tuple[tuple[int, int], ...] = ()
    incomplete: bool = False
    rendered_text: str | None = None
    rendered_html: str = ""


@dataclass(frozen=True)
class _PendingImage:
    image: ChatImage
    size: tuple[int, int]


@dataclass(frozen=True)
class _CurrentImageContext:
    image: ChatImage | None = None
    size: tuple[int, int] = ()
    region_prompt: str = ""
    region_count: int | None = None


class _LiveImagePreview(QLabel):
    """Small, non-editable preview redrawn only by queued UI updates."""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self._placeholder = placeholder
        self._pixmap = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(180, 110)
        self.setFixedHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setText(placeholder)

    def set_placeholder(self, text: str):
        self._placeholder = text
        if self._pixmap.isNull():
            self.setText(text)
    @property
    def source_size(self):
        return self._pixmap.width(), self._pixmap.height()


    def set_png(self, data: bytes | None):
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data, "PNG")
        self._pixmap = pixmap
        self._render()

    def _render(self):
        if self._pixmap.isNull():
            self.setPixmap(QPixmap())
            self.setText(self._placeholder)
            return
        self.setText("")
        self.setPixmap(self._pixmap.scaled(
            self.contentsRect().size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()


class ChatPage(QWidget):
    """Own one conversation; only queued Qt slots update widgets."""

    task_finished = pyqtSignal(int, object)
    text_received = pyqtSignal(int, str)
    activity_received = pyqtSignal(int, object)
    request_body_received = pyqtSignal(str)
    debug_event_received = pyqtSignal(object)
    thinking_received = pyqtSignal(int, str)
    canvas_received = pyqtSignal(object)
    canvas_updated = pyqtSignal(object)
    # Hosts may call the method on the GUI thread or emit this signal from a
    # worker; the signal is delivered to the page through the Qt event queue.
    current_image_context_pushed = pyqtSignal(object, object)
    _current_image_context_received = pyqtSignal(int, object, object)


    def __init__(self, t_func: Callable, service=None, parent=None, *, config_service=None,
                 show_request_body=True, debug_context=False):
        super().__init__(parent)
        self._t = t_func
        self.chat_service = service
        self._backend = None
        self._tool_context = None
        self._debug_context = debug_context
        self.config_service = config_service
        self._config_error_key = None
        self._injected_service = service is not None
        self._active_task = None
        self._operation = None
        self._generation = 0
        self._closing = False
        self._messages = []
        self._tool_records = {}
        self._turn_start = 0
        self._turn_open = False
        self._assistant_message = None
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render_transcript)
        self._pending_images = {}
        self._current_image_context = _CurrentImageContext()
        self._current_image_context_generation = 0
        self._current_context_title_text = "Current editor image"
        self._current_context_empty_text = "No current rendered image"
        self._status_key = "Chat ready" if service is not None else "Chat configuration required"
        self._status_args = {}
        self._thinking_status_key = "Chat thinking idle"
        self._thinking_snapshot = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)
        self.title_label = TitleLabel(self)
        layout.addWidget(self.title_label)
        self.description_label = BodyLabel(self)
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        self.config_card = CardWidget(self)
        config_layout = QFormLayout(self.config_card)
        self.base_label = BodyLabel(self.config_card)
        self.model_label = BodyLabel(self.config_card)
        self.key_label = BodyLabel(self.config_card)
        self.base_input = LineEdit(self.config_card)
        self.model_input = LineEdit(self.config_card)
        self.key_input = LineEdit(self.config_card)
        self.reasoning_label = BodyLabel(self.config_card)
        self.reasoning_input = ComboBox(self.config_card)
        for effort in ("", "low", "medium", "high"):
            self.reasoning_input.addItem(effort, userData=effort)
        self._connection_inputs = {
            "AGENT_OPENAI_API_BASE": self.base_input,
            "AGENT_OPENAI_MODEL": self.model_input,
            "AGENT_OPENAI_API_KEY": self.key_input,
        }
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.base_input.setPlaceholderText("https://api.openai.com/v1")
        config_layout.addRow(self.base_label, self.base_input)
        config_layout.addRow(self.model_label, self.model_input)
        config_layout.addRow(self.key_label, self.key_input)
        config_layout.addRow(self.reasoning_label, self.reasoning_input)
        self.config_card.setVisible(not self._injected_service)
        layout.addWidget(self.config_card)
        self.config_status_label = BodyLabel(self)
        self.config_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.config_status_label.setWordWrap(True)
        self.config_status_label.hide()
        layout.addWidget(self.config_status_label)
        self.current_context_surface = QWidget(self)
        context_layout = QHBoxLayout(self.current_context_surface)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(10)
        self.current_image_preview = _LiveImagePreview(
            self._current_context_empty_text, self.current_context_surface
        )
        context_layout.addWidget(self.current_image_preview, 1)
        context_info = QVBoxLayout()
        self.current_context_title = BodyLabel(self.current_context_surface)
        self.current_context_title.setText(self._current_context_title_text)
        self.current_context_metadata = BodyLabel(self.current_context_surface)
        self.current_context_metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.current_context_metadata.setWordWrap(True)
        self.current_context_metadata.setText(self._current_context_empty_text)
        context_info.addWidget(self.current_context_title)
        context_info.addWidget(self.current_context_metadata)
        context_info.addStretch()
        context_layout.addLayout(context_info, 1)

        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        self.messages = ConversationView(card)
        self.messages.anchorClicked.connect(self._on_transcript_link)
        self.request_body = None
        self.request_title = None
        if show_request_body:
            self.request_body = PlainTextEdit(card)
            self.request_body.setReadOnly(True)
            self.request_body.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
            debug_panel = QWidget(card)
            debug_layout = QVBoxLayout(debug_panel)
            debug_layout.setContentsMargins(0, 0, 0, 0)
            self.request_title = BodyLabel(debug_panel)
            debug_layout.addWidget(self.request_title)
            debug_layout.addWidget(self.request_body, 1)
            splitter = QSplitter(Qt.Orientation.Horizontal, card)
            splitter.addWidget(self.messages)
            splitter.addWidget(debug_panel)
            splitter.setChildrenCollapsible(False)
            splitter.setSizes([500, 400])
            card_layout.addWidget(splitter, 1)
        else:
            card_layout.addWidget(self.messages, 1)
        self.thinking_panel = CollapsibleFrame(parent=card)
        self.thinking_description = BodyLabel(self.thinking_panel)
        self.thinking_description.setTextFormat(Qt.TextFormat.PlainText)
        self.thinking_description.setWordWrap(True)
        self.thinking_text = PlainTextEdit(self.thinking_panel)
        self.thinking_text.setReadOnly(True)
        self.thinking_text.setMinimumHeight(80)
        self.thinking_text.setMaximumHeight(160)
        self.thinking_panel.add_widget(self.thinking_description)
        self.thinking_panel.add_widget(self.thinking_text)
        self.thinking_panel.header_button.setChecked(False)
        card_layout.addWidget(self.thinking_panel)
        self.thinking_panel.hide()
        self.input = ImagePasteTextEdit(card)
        self.input.setMaximumHeight(120)
        card_layout.addWidget(self.input)
        self.attachment_strip = ImageAttachmentStrip(card)
        card_layout.addWidget(self.attachment_strip)
        buttons = QHBoxLayout()
        self.send_button = PrimaryPushButton(card)
        self.stop_button = PushButton(card)
        self.clear_button = PushButton(card)
        buttons.addStretch()
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.send_button)
        card_layout.addLayout(buttons)
        self.status_label = BodyLabel(card)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)
        layout.addWidget(card, 1)
        layout.addWidget(self.current_context_surface)
        self.send_button.clicked.connect(self.send_message)
        self.stop_button.clicked.connect(self.stop_generation)
        self.clear_button.clicked.connect(self.clear_conversation)
        self.input.image_pasted.connect(self._add_pasted_image)
        self.attachment_strip.attachment_removed.connect(self._remove_pending_image)
        self.task_finished.connect(self._on_task_finished, Qt.ConnectionType.QueuedConnection)
        self.text_received.connect(self._on_text_received, Qt.ConnectionType.QueuedConnection)
        self.activity_received.connect(self._on_activity_received, Qt.ConnectionType.QueuedConnection)
        self.request_body_received.connect(self._on_request_body_received, Qt.ConnectionType.QueuedConnection)
        self.thinking_received.connect(self._on_thinking_received, Qt.ConnectionType.QueuedConnection)
        self.canvas_received.connect(self._on_canvas_received, Qt.ConnectionType.QueuedConnection)
        self.current_image_context_pushed.connect(
            self.set_current_image_context, Qt.ConnectionType.QueuedConnection
        )
        self._current_image_context_received.connect(
            self._on_current_image_context_received, Qt.ConnectionType.QueuedConnection
        )
        if self.config_service is not None:
            self._load_connection_settings()
            for control in self._connection_inputs.values():
                control.textChanged.connect(self._save_connection_settings)
            self.reasoning_input.currentIndexChanged.connect(self._save_connection_settings)
            self.config_service.write_failed.connect(
                self._on_config_write_failed, Qt.ConnectionType.QueuedConnection,
            )
        self.refresh_ui_texts()

    def _load_connection_settings(self):
        try:
            saved = self.config_service.load_env_vars()
            for key, control in self._connection_inputs.items():
                control.setText(saved.get(key, ""))
            effort = saved.get("AGENT_OPENAI_REASONING_EFFORT", "").strip()
            self.reasoning_input.setCurrentIndex(max(0, self.reasoning_input.findData(effort)))
        except Exception:
            self._set_config_error("Chat settings load failed")

    @pyqtSlot()
    def _save_connection_settings(self):
        if self._closing or self.config_service is None:
            return
        values = {key: control.text().strip() for key, control in self._connection_inputs.items()}
        values["AGENT_OPENAI_REASONING_EFFORT"] = self.reasoning_input.currentData() or ""
        try:
            accepted = self.config_service.save_env_vars(values)
        except Exception:
            accepted = False
        if not accepted:
            self._set_config_error("Chat settings save failed")
        # Acceptance only queues the debounced write; do not claim disk success.

    @pyqtSlot(str)
    def _on_config_write_failed(self, error):
        if not self._closing:
            self._set_config_error("Chat settings save failed")

    def _set_config_error(self, key):
        self._config_error_key = key
        self.config_status_label.setText(self._t(key))
        self.config_status_label.show()

    @staticmethod
    def _region_count(regions):
        if isinstance(regions, (list, tuple)):
            return len(regions)
        if isinstance(regions, dict):
            values = regions.get("regions")
            if isinstance(values, (list, tuple)):
                return len(values)
        return None

    @staticmethod
    def _region_prompt(regions):
        if regions is None:
            return ""
        if isinstance(regions, str):
            try:
                regions = json.loads(regions)
            except ValueError:
                return regions

        def model_metadata(value):
            # Keep source quadrilaterals in the editor; omit them only from model context.
            if isinstance(value, dict):
                return {key: model_metadata(item) for key, item in value.items() if key != "lines"}
            if isinstance(value, (list, tuple)):
                return [model_metadata(item) for item in value]
            return value

        regions = model_metadata(regions)
        try:
            return json.dumps(regions, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            return str(regions)

    @pyqtSlot(object, object)
    def set_current_image_context(self, png, regions=None):
        """Push the editor's latest rendered PNG and region metadata.

        Call this from the GUI thread after the editor has produced a PNG, or
        emit ``current_image_context_pushed`` from a worker. The PNG is kept
        as a ``ChatImage`` and is attached to the next model turn; region
        metadata is serialized into the final context block of that turn.
        Widget work is queued and generation-checked, so an older update
        cannot repaint a newer preview.
        """
        if png is not None:
            if not isinstance(png, (bytes, bytearray, memoryview)):
                raise TypeError("current rendered image must be PNG bytes or None")
            png = bytes(png)
            if not png:
                raise ValueError("current rendered image must not be empty")
            image = ChatImage(data=png, media_type="image/png")
        else:
            image = None
        context = _CurrentImageContext(
            image=image,
            region_prompt=self._region_prompt(regions),
            region_count=self._region_count(regions),
        )
        self._current_image_context_generation += 1
        generation = self._current_image_context_generation
        self._current_image_context = context
        self._current_image_context_received.emit(generation, png, context)

    @pyqtSlot(int, object, object)
    def _on_current_image_context_received(self, generation, png, context):
        if self._closing or generation != self._current_image_context_generation:
            return
        self.current_image_preview.set_png(png)
        if context.image is None:
            self.current_context_metadata.setText(self._current_context_empty_text)
            return
        width, height = self.current_image_preview.source_size
        size = self._t("Chat image size", width=width, height=height)
        count = (
            str(context.region_count)
            if context.region_count is not None
            else "metadata available"
        )
        self.current_context_metadata.setText(
            self._t("Chat image summary", count=1, sizes=size)
            + "\nRegions: " + count
            + ("\n" + self._t("Chat original image reference")
               if self._tool_context is not None and self._tool_context.original_image is not None else "")
        )

    def _model_text_with_current_context(self, text):
        context = self._current_image_context
        if context.image is None and not context.region_prompt:
            return text
        parts = [text] if text else []
        parts.extend(("", "Current editor context:"))
        if context.image is not None:
            parts.append("The current rendered PNG is attached after the user attachments.")
        if context.region_prompt:
            parts.append("Current region metadata: " + context.region_prompt)
        return "\n".join(parts)

    @pyqtSlot(object)
    def set_tool_context(self, context):
        """Bind the host-owned workspace; the Agent owns all tool execution."""
        if self._tool_context is context:
            return
        self.stop_generation()
        if self._tool_context is not None:
            self._tool_context.cancelled.set()
        self._tool_context = context

    @pyqtSlot(object)
    def _on_canvas_received(self, canvas):
        if (self._closing or self._tool_context is None
                or canvas.context_id != self._tool_context.task_id
                or self._tool_context.cancelled.is_set()):
            return
        # A committed edit remains visible even if its following text was stopped.
        self.set_current_image_context(canvas.image.data, canvas.page.get("regions", []))
        self.canvas_updated.emit(canvas)


    @pyqtSlot(bytes, int, int)
    def _add_pasted_image(self, data, width, height):
        image = _PendingImage(ChatImage(data=data, media_type="image/png"), (width, height))
        attachment_id = self.attachment_strip.add_png(data, width, height)
        self._pending_images[attachment_id] = image

    @pyqtSlot(int)
    def _remove_pending_image(self, attachment_id):
        self._pending_images.pop(attachment_id, None)
        self.attachment_strip.remove(attachment_id)

    def send_message(self):
        text = self.input.toPlainText().strip()
        image_sizes = tuple(item.size for item in self._pending_images.values())
        images = tuple(item.image for item in self._pending_images.values())
        current_image = self._current_image_context.image
        if current_image is not None:
            images = images + (current_image,)
        model_text = self._model_text_with_current_context(text)
        if (not text and not self._pending_images and current_image is None) or self._active_task is not None or self._closing:
            return
        if self.chat_service is None:
            base_url = self.base_input.text().strip()
            model = self.model_input.text().strip()
            api_key = self.key_input.text().strip()
            if not base_url or not model:
                self._set_status("Chat configuration required")
                return
            try:
                from manga_translator.agent.application.service import ChatService
                from manga_translator.agent.providers.openai import OpenAIResponsesBackend

                page_ref = weakref.ref(self)

                def show_request_body(body):
                    page = page_ref()
                    if page is not None:
                        try:
                            page.request_body_received.emit(body)
                        except RuntimeError:
                            pass

                def show_debug_event(event):
                    page = page_ref()
                    if page is not None:
                        try:
                            page.debug_event_received.emit(event)
                        except RuntimeError:
                            pass

                backend = OpenAIResponsesBackend(
                    api_key=api_key, model=model, base_url=base_url,
                    reasoning_effort=self.reasoning_input.currentData() or None,
                    on_request_body=show_request_body if self.request_body is not None else None,
                    on_debug_event=show_debug_event if self._debug_context else None,
                )
                self.chat_service = ChatService(backend)
                self._backend = backend
            except Exception as exc:
                self._show_error(exc)
                return
        def commit_draft():
            self.input.clear()
            self.attachment_strip.clear()
            self._pending_images.clear()
            self._turn_start = len(self._messages)
            self._turn_open = True
            self._append("user", text, image_sizes)
            self._assistant_message = None
            self._tool_records = {}
        service = self.chat_service

        backend = self._backend
        tool_context = self._tool_context
        session_id = tool_context.task_id if tool_context is not None else "default"
        page_ref = weakref.ref(self)

        async def send(generation):
            def show_thinking(snapshot):
                page = page_ref()
                if page is not None:
                    try:
                        page.thinking_received.emit(generation, snapshot)
                    except RuntimeError:
                        pass

            if backend is not None:
                backend.set_thinking_observer(show_thinking)
            async with aclosing(service.stream(model_text, images=images, session_id=session_id,
                                               tool_context=tool_context)) as stream:
                async for delta in stream:
                    page = page_ref()
                    if page is None:
                        return
                    try:
                        if isinstance(delta, ChatCanvas):
                            page.canvas_received.emit(delta)
                        elif isinstance(delta, ChatActivity):
                            page.activity_received.emit(generation, delta)
                        else:
                            page.text_received.emit(generation, delta)
                    except RuntimeError:
                        return
                    finally:
                        del page

        self._submit(send, "send", on_submitted=commit_draft)

    def _submit(self, work, operation, *, on_submitted=None):
        self._generation += 1
        generation = self._generation
        self._operation = operation
        if operation == "send":
            self._reset_thinking("Chat thinking waiting")
        self._set_status("Chat processing" if operation == "send" else "Chat clearing")
        coroutine = work(generation)
        try:
            from services import get_async_service

            future = get_async_service().submit_task(coroutine)
            if future is None:
                raise RuntimeError(self._t("Chat background service unavailable"))
        except Exception as exc:
            coroutine.close()
            self._operation = None
            if operation == "send":
                self._set_thinking_status("Chat thinking failed")
            self._show_error(exc)
            self._update_controls()
            return
        self._active_task = future
        if on_submitted is not None:
            on_submitted()
        self._update_controls()
        page_ref = weakref.ref(self)

        def notify_gui(done):
            page = page_ref()
            if page is not None:
                try:
                    page.task_finished.emit(generation, done)
                except RuntimeError:
                    # Qt may already have destroyed the page during shutdown.
                    pass

        future.add_done_callback(notify_gui)

    @pyqtSlot(int, str)
    def _on_text_received(self, generation, delta):
        if (
            self._closing
            or generation != self._generation
            or self._operation != "send"
            or not delta
        ):
            return
        if self._assistant_message is None:
            self._assistant_message = self._append("assistant", "")
        self._assistant_message.chunks.append(delta)
        self._schedule_transcript()

    @pyqtSlot(int, object)
    def _on_activity_received(self, generation, activity):
        if self._closing or generation != self._generation or self._operation != "send":
            return
        if activity.kind == "response_start":
            self._assistant_message = None
            return
        record = self._tool_records.get(activity.tool_call_id)
        if record is None or activity.kind == "tool_call":
            record = ToolRecord(activity.tool_call_id, activity.tool_name)
            self._tool_records[activity.tool_call_id] = record
            self._messages.append(record)
        if activity.kind == "tool_call":
            record.arguments = activity.data.get("arguments")
            if activity.data.get("args_valid") is False:
                record.status = "invalid"
        elif activity.kind == "tool_result":
            record.finish(activity.data.get("result"),
                          validation_error=activity.data.get("validation_error", False))
        self._assistant_message = None
        self._schedule_transcript()

    @pyqtSlot(int, str)
    def _on_thinking_received(self, generation, snapshot):
        if self._closing or generation != self._generation or self._operation != "send":
            return
        self._thinking_snapshot = snapshot
        self.thinking_text.setPlainText(snapshot)
        self._set_thinking_status("Chat thinking streaming" if snapshot else "Chat thinking waiting")

    def _set_thinking_status(self, key):
        self._thinking_status_key = key
        self.thinking_panel.header_button.setText(
            self._t("Chat thinking summary") + " — " + self._t(key)
        )
        self.thinking_text.setPlaceholderText(self._t(key))

    def _reset_thinking(self, key="Chat thinking idle"):
        self._thinking_snapshot = ""
        self.thinking_text.clear()
        self.thinking_panel.header_button.setChecked(False)
        self._set_thinking_status(key)

    @pyqtSlot(int, object)
    def _on_task_finished(self, generation, future):
        if self._closing or generation != self._generation or future is not self._active_task:
            return
        operation = self._operation
        self._active_task = None
        self._operation = None
        try:
            if future.cancelled():
                self._mark_incomplete()
                if operation == "send":
                    self._set_thinking_status("Chat thinking cancelled")
                self._set_status("Chat cancelled")
                return
            future.result()
            if operation == "send":
                self._turn_open = False
                self._assistant_message = None
                self._set_thinking_status(
                    "Chat thinking complete" if self._thinking_snapshot else "Chat thinking unavailable"
                )
            elif not self._injected_service:
                self.chat_service = None
                self._backend = None
            self._set_status("Chat ready" if self.chat_service is not None else "Chat configuration required")
        except Exception as exc:
            if operation == "send":
                self._set_thinking_status("Chat thinking failed")
            self._show_error(exc)
        finally:
            self._render_transcript()
            self._update_controls()

    def stop_generation(self):
        if self._closing or self._operation != "send" or self._active_task is None:
            return
        # A finished worker may still have queued deltas. Let those and its
        # completion arrive rather than labelling an already committed turn partial.
        if not self._active_task.cancel():
            return
        self._generation += 1
        self._active_task = None
        self._operation = None
        self._mark_incomplete()
        self._set_thinking_status("Chat thinking cancelled")
        self._set_status("Chat cancelled")
        self._update_controls()

    def clear_conversation(self):
        if self._closing or self._operation == "clear":
            return
        self._generation += 1
        if self._active_task is not None:
            self._active_task.cancel()
            self._active_task = None
        self._messages.clear()
        self._tool_records.clear()
        self._turn_open = False
        self._turn_start = 0
        self._assistant_message = None
        self._render_timer.stop()
        self.messages.clear()
        self._reset_thinking()
        self.input.clear()
        self.attachment_strip.clear()
        self._pending_images.clear()
        service = self.chat_service
        if service is None:
            self._operation = None
            self._set_status("Chat configuration required")
            self._update_controls()
            return

        session_id = self._tool_context.task_id if self._tool_context is not None else "default"

        async def clear(generation):
            # Clear on the same event loop as streaming; never mutate history from Qt.
            service.clear(session_id)

        self._submit(clear, "clear")

    @pyqtSlot(str)
    def _on_request_body_received(self, body):
        if not self._closing and self.request_body is not None:
            # Keep the last actual request visible across stop/clear for debugging.
            self.request_body.setPlainText(body)

    def append_edit_history(self, text: str):
        """Append one explicit editor change to the visible conversation."""
        if self._closing or not isinstance(text, str) or not text.strip():
            return
        self._append("edit", text.strip())

    def _append(self, role, text, image_sizes=()):
        if role not in {"user", "assistant", "error", "edit"}:
            raise ValueError(f"unsupported visible chat role: {role!r}")
        message = _DisplayedMessage(role, [text] if text else [], image_sizes)
        self._messages.append(message)
        self._schedule_transcript()
        return message


    def _message_text(self, message):
        text = "".join(message.chunks)
        if not message.image_sizes:
            return text
        sizes = ", ".join(
            self._t("Chat image size", width=width, height=height)
            for width, height in message.image_sizes
        )
        summary = self._t("Chat image summary", count=len(message.image_sizes), sizes=sizes)
        return f"{text}\n{summary}" if text else summary

    def _schedule_transcript(self):
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render_transcript(self):
        self._render_timer.stop()
        entries = []
        for index, message in enumerate(self._messages):
            if isinstance(message, ToolRecord):
                entries.append(message.html(index, self._t))
                continue
            text = self._message_text(message)
            if message.rendered_text != text:
                message.rendered_html = (self.messages.markdown(text) if message.role == "assistant"
                                         else f'<p>{escape(text).replace(chr(10), "<br>")}</p>')
                message.rendered_text = text
            body = message.rendered_html
            if message.incomplete:
                body += f'<p><i>{escape(self._t("Chat response incomplete"))}</i></p>'
            entries.append(f'<p><b>{escape(self._role_label(message.role))}</b></p>{body}')
        self.messages.show_entries(entries)

    def _on_transcript_link(self, url):
        if url.scheme() != "tool":
            return
        try:
            index = int(url.path())
            if index < 0:
                return
            message = self._messages[index]
        except (ValueError, IndexError):
            return
        if isinstance(message, ToolRecord):
            message.expanded = not message.expanded
            self._render_transcript()

    def _mark_incomplete(self):
        if not self._turn_open:
            return
        self._turn_open = False
        last_response = next((message for message in reversed(self._messages[self._turn_start:])
                              if isinstance(message, _DisplayedMessage) and message.role == "assistant"), None)
        if last_response is not None:
            last_response.incomplete = True
        else:
            self._append("error", self._t("Chat response incomplete"))
        self._assistant_message = None
        for record in self._tool_records.values():
            if record.status == "running":
                record.status = "interrupted"
        self._schedule_transcript()

    def _role_label(self, role):
        labels = {
            "user": "Chat user",
            "assistant": "Chat assistant",
            "error": "Chat error",
            "edit": "Chat edit history",
        }
        key = labels[role]
        translated = self._t(key)
        return "Edit" if role == "edit" and translated == key else translated

    def _show_error(self, error):
        self._mark_incomplete()
        detail = str(error)
        # Some providers echo request data; never display the configured secret.
        key = self.key_input.text().strip()
        if key:
            detail = detail.replace(key, "[redacted]")
        self._append("error", detail)
        self._set_status("Chat request failed", error=detail)

    def _set_status(self, key, **kwargs):
        self._status_key = key
        self._status_args = kwargs
        self.status_label.setText(self._t(key, **kwargs))

    def _update_controls(self):
        self.send_button.setEnabled(not self._closing and self._active_task is None)
        self.stop_button.setEnabled(not self._closing and self._operation == "send")
        self.clear_button.setEnabled(not self._closing and self._operation != "clear")
        self.config_card.setEnabled(self.chat_service is None and not self._closing)

    def refresh_ui_texts(self):
        self.title_label.setText(self._t("Chat"))
        if self.request_body is not None:
            self.request_title.setText(self._t("Chat request body"))
            self.request_body.setPlaceholderText(self._t("Chat request body placeholder"))
        storage_key = "Chat connection saved locally" if self.config_service is not None else "Chat connection memory only"
        self.description_label.setText(self._t("Chat description") + " " + self._t(storage_key))
        translated_title = self._t("Chat current image")
        self._current_context_title_text = (
            "Current editor image" if translated_title == "Chat current image" else translated_title
        )
        self.current_context_title.setText(self._current_context_title_text)
        self.current_image_preview.set_placeholder(self._current_context_empty_text)
        if self._current_image_context.image is None:
            self.current_context_metadata.setText(self._current_context_empty_text)
        if self._config_error_key is not None:
            self.config_status_label.setText(self._t(self._config_error_key))
        self.base_label.setText(self._t("Chat API base URL"))
        self.model_label.setText(self._t("Chat model"))
        self.key_label.setText(self._t("Chat API key"))
        self.key_input.setPlaceholderText(self._t("Chat key placeholder"))
        self.reasoning_label.setText(self._t("Chat reasoning effort"))
        self.reasoning_input.setToolTip(self._t("Chat reasoning effort hint"))
        for index, key in enumerate((
            "Chat reasoning default", "Chat reasoning low", "Chat reasoning medium", "Chat reasoning high",
        )):
            self.reasoning_input.setItemText(index, self._t(key))
        self.thinking_description.setText(self._t("Chat thinking description"))
        self._set_thinking_status(self._thinking_status_key)
        self.messages.setPlaceholderText(self._t("Chat messages placeholder"))
        self.input.setPlaceholderText(self._t("Chat input placeholder"))
        self.attachment_strip.set_remove_tooltip(self._t("Chat remove image"))
        self.send_button.setText(self._t("Chat send"))
        self.stop_button.setText(self._t("Chat stop"))
        self.clear_button.setText(self._t("Chat clear"))
        self.status_label.setText(self._t(self._status_key, **self._status_args))
        self._render_transcript()
        self._update_controls()

    def shutdown(self):
        self._closing = True
        self._generation += 1
        if self._active_task is not None:
            self._active_task.cancel()
            self._active_task = None
        self._operation = None
        self._mark_incomplete()
        self._reset_thinking()
        self.attachment_strip.clear()
        self._pending_images.clear()
        self._update_controls()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def create_chat_page(self) -> QWidget:
    return ChatPage(
        self._t,
        service=getattr(self, "chat_service", None),
        config_service=self.config_service,
    )
