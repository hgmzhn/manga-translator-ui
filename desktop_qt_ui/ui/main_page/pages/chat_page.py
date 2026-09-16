"""Basic chat UI using the application's background asyncio service."""
from __future__ import annotations

import weakref
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QFormLayout, QHBoxLayout, QLineEdit, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    TitleLabel,
)

from manga_translator.agent.domain.chat import ChatImage
from ui.agent.image_input import ImageAttachmentStrip, ImagePasteTextEdit
from ui.widgets.collapsible_frame import CollapsibleFrame
from ui.widgets.wheel_filter import NoWheelComboBox as ComboBox


@dataclass
class _DisplayedMessage:
    role: str
    chunks: list[str] = field(default_factory=list)
    image_sizes: tuple[tuple[int, int], ...] = ()
    incomplete: bool = False


@dataclass(frozen=True)
class _PendingImage:
    image: ChatImage
    size: tuple[int, int]

class ChatPage(QWidget):
    """Own one conversation; only queued Qt slots update widgets."""

    task_finished = pyqtSignal(int, object)
    text_received = pyqtSignal(int, str)
    request_body_received = pyqtSignal(str)
    thinking_received = pyqtSignal(int, str)

    def __init__(self, t_func: Callable, service=None, parent=None, *, config_service=None):
        super().__init__(parent)
        self._t = t_func
        self.chat_service = service
        self._backend = None
        self.config_service = config_service
        self._config_error_key = None
        self._injected_service = service is not None
        self._active_task = None
        self._operation = None
        self._generation = 0
        self._closing = False
        self._messages = []
        self._assistant_message = None
        self._pending_images = {}
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

        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        self.messages = PlainTextEdit(card)
        self.messages.setReadOnly(True)
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
        self.send_button.clicked.connect(self.send_message)
        self.stop_button.clicked.connect(self.stop_generation)
        self.clear_button.clicked.connect(self.clear_conversation)
        self.input.image_pasted.connect(self._add_pasted_image)
        self.attachment_strip.attachment_removed.connect(self._remove_pending_image)
        self.task_finished.connect(self._on_task_finished, Qt.ConnectionType.QueuedConnection)
        self.text_received.connect(self._on_text_received, Qt.ConnectionType.QueuedConnection)
        self.request_body_received.connect(self._on_request_body_received, Qt.ConnectionType.QueuedConnection)
        self.thinking_received.connect(self._on_thinking_received, Qt.ConnectionType.QueuedConnection)
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
        if (not text and not images) or self._active_task is not None or self._closing:
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

                backend = OpenAIResponsesBackend(
                    api_key=api_key, model=model, base_url=base_url,
                    reasoning_effort=self.reasoning_input.currentData() or None,
                    on_request_body=show_request_body,
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
            self._append("user", text, image_sizes)
            self._assistant_message = self._append("assistant", "")
        service = self.chat_service

        backend = self._backend
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
            async with aclosing(service.stream(text, images=images)) as stream:
                async for delta in stream:
                    page = page_ref()
                    if page is None:
                        return
                    try:
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
            or self._assistant_message is None
            or not delta
        ):
            return
        self._assistant_message.chunks.append(delta)
        self._insert_text(delta)

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
        self._assistant_message = None
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

        async def clear(generation):
            # Clear on the same event loop as streaming; never mutate history from Qt.
            service.clear()

        self._submit(clear, "clear")

    @pyqtSlot(str)
    def _on_request_body_received(self, body):
        if not self._closing:
            # Keep the last actual request visible across stop/clear for debugging.
            self.request_body.setPlainText(body)

    def _append(self, role, text, image_sizes=()):
        message = _DisplayedMessage(role, [text] if text else [], image_sizes)
        prefix = "\n\n" if self._messages else ""
        self._messages.append(message)
        self._insert_text(f"{prefix}{self._role_label(role)}:\n{self._message_text(message)}")
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

    def _insert_text(self, text):
        cursor = self.messages.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.messages.setTextCursor(cursor)
        self.messages.ensureCursorVisible()

    def _mark_incomplete(self):
        if self._assistant_message is None:
            return
        self._assistant_message.incomplete = True
        self._assistant_message = None
        self._insert_text("\n" + self._t("Chat response incomplete"))

    def _role_label(self, role):
        return self._t({"user": "Chat user", "assistant": "Chat assistant", "error": "Chat error"}[role])

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
        self.request_title.setText(self._t("Chat request body"))
        self.request_body.setPlaceholderText(self._t("Chat request body placeholder"))
        storage_key = "Chat connection saved locally" if self.config_service is not None else "Chat connection memory only"
        self.description_label.setText(self._t("Chat description") + " " + self._t(storage_key))
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
        self.messages.setPlainText("\n\n".join(
            f"{self._role_label(message.role)}:\n{self._message_text(message)}"
            + ("\n" + self._t("Chat response incomplete") if message.incomplete else "")
            for message in self._messages
        ))
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
