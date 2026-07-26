from __future__ import annotations

import textwrap
from typing import Callable

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMessageBox
from qfluentwidgets import Dialog, FluentIcon as FIF, PlainTextEdit, PushButton

from ui.secondary_pages.fluent_dialog import normalize_dialog_parent as _dialog_parent

_INSTALLED = False


def _wrap_dialog_text(text: str, width: int = 92) -> str:
    wrapped_lines: list[str] = []
    for line in str(text or "").splitlines():
        if not line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            textwrap.wrap(
                line,
                width=width,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return "\n".join(wrapped_lines)


_BUTTON_LABELS = {
    QMessageBox.StandardButton.Ok: "OK",
    QMessageBox.StandardButton.Yes: "Yes",
    QMessageBox.StandardButton.No: "No",
    QMessageBox.StandardButton.Cancel: "Cancel",
    QMessageBox.StandardButton.Close: "Close",
}

_BUTTON_ICONS = {
    QMessageBox.StandardButton.Ok: FIF.ACCEPT,
    QMessageBox.StandardButton.Yes: FIF.ACCEPT,
    QMessageBox.StandardButton.No: FIF.CANCEL,
    QMessageBox.StandardButton.Cancel: FIF.CANCEL,
    QMessageBox.StandardButton.Close: FIF.CLOSE,
}


def _button_icon(button: QMessageBox.StandardButton, fallback=FIF.ACCEPT) -> QIcon:
    icon = _BUTTON_ICONS.get(button, fallback)
    if isinstance(icon, QIcon):
        return icon
    return icon.icon()


def _button_text(button: QMessageBox.StandardButton) -> str:
    return _BUTTON_LABELS.get(button, "OK")


def _resolve_dialog_buttons(
    buttons: QMessageBox.StandardButton,
) -> tuple[QMessageBox.StandardButton, QMessageBox.StandardButton]:
    if buttons == QMessageBox.StandardButton.NoButton:
        buttons = QMessageBox.StandardButton.Ok

    preferred_accept = (
        QMessageBox.StandardButton.Yes,
        QMessageBox.StandardButton.Ok,
        QMessageBox.StandardButton.Close,
    )
    preferred_reject = (
        QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Close,
    )

    accept_button = QMessageBox.StandardButton.NoButton
    reject_button = QMessageBox.StandardButton.NoButton
    for button in preferred_accept:
        if buttons & button:
            accept_button = button
            break
    for button in preferred_reject:
        if buttons & button and button != accept_button:
            reject_button = button
            break

    if accept_button == QMessageBox.StandardButton.NoButton:
        accept_button = QMessageBox.StandardButton.Ok
    return accept_button, reject_button


def _configure_dialog_buttons(
    dialog: Dialog,
    buttons: QMessageBox.StandardButton,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> tuple[QMessageBox.StandardButton, QMessageBox.StandardButton]:
    accept_button, reject_button = _resolve_dialog_buttons(buttons)
    dialog.yesButton.setText(_button_text(accept_button))
    dialog.yesButton.setIcon(_button_icon(accept_button, FIF.ACCEPT))

    if reject_button == QMessageBox.StandardButton.NoButton:
        dialog.hideCancelButton()
    else:
        dialog.cancelButton.setText(_button_text(reject_button))
        dialog.cancelButton.setIcon(_button_icon(reject_button, FIF.CANCEL))

    if default_button == reject_button and reject_button != QMessageBox.StandardButton.NoButton:
        dialog.cancelButton.setFocus()
    else:
        dialog.yesButton.setFocus()
    return accept_button, reject_button


def _exec_fluent_dialog(
    dialog: Dialog,
    buttons: QMessageBox.StandardButton,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> QMessageBox.StandardButton:
    accept_button, reject_button = _configure_dialog_buttons(dialog, buttons, default_button)
    dialog.setTitleBarVisible(False)
    dialog.setContentCopyable(True)
    result = dialog.exec()
    if result == Dialog.DialogCode.Accepted:
        return accept_button
    if reject_button != QMessageBox.StandardButton.NoButton:
        return reject_button
    return QMessageBox.StandardButton.NoButton


def _apply_flexible_size(dialog: Dialog, min_width: int, min_height: int) -> None:
    """按内容自适应尺寸，替代布局激活前的 setFixedSize。

    qfluentwidgets 的 Dialog 在构造末尾会 setFixedSize(布局激活前的尺寸)，
    此时读到的 width/height 是无意义的初始值。这里先解除固定尺寸约束，
    在内容装配完、布局激活之后取真实的内容 sizeHint，再与给定下限取大。
    注意：Dialog 的 vBoxLayout 是 SetMinimumSize 约束，每次布局激活都会
    重写控件 minimumSize，所以下限必须通过 resize 落地而不是 setMinimumSize。
    """
    dialog.setMaximumSize(16777215, 16777215)
    layout = dialog.layout()
    if layout is not None:
        layout.activate()
    hint = dialog.sizeHint()
    dialog.resize(max(hint.width(), min_width), max(hint.height(), min_height))


def apply_message_box_style(box: QMessageBox) -> QMessageBox:
    box.setTextFormat(box.textFormat())
    return box


def apply_error_dialog_style(dialog: Dialog) -> Dialog:
    dialog.setContentCopyable(True)
    return dialog


def show_error_dialog(
    parent,
    window_title: str,
    heading: str,
    details: str,
    icon: QMessageBox.Icon = QMessageBox.Icon.NoIcon,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    extra_button_text: str | None = None,
    extra_button_callback: Callable[[], None] | None = None,
    extra_button_icon=FIF.FOLDER,
) -> QMessageBox.StandardButton:
    del icon
    dialog_parent = _dialog_parent(parent)
    summary = str(heading or "").strip()
    detail_text = str(details or "").strip()
    content = "\n\n".join(part for part in (summary, _wrap_dialog_text(detail_text)) if part)

    dialog = Dialog(str(window_title or ""), content or " ", dialog_parent)
    dialog.setContentCopyable(True)

    if len(detail_text) > 900:
        dialog.contentLabel.setText(summary or "")
        details_edit = PlainTextEdit(dialog)
        details_edit.setReadOnly(True)
        details_edit.setPlainText(detail_text)
        details_edit.setMinimumHeight(260)
        # 详情区带 stretch，对话框放大时详情区跟着长
        dialog.textLayout.addWidget(details_edit, 1)
        min_size = (720, 460)
    else:
        min_size = (520, 220)

    if extra_button_text and extra_button_callback:
        extra_button = PushButton(str(extra_button_text), dialog.buttonGroup)
        if extra_button_icon:
            extra_button.setIcon(
                extra_button_icon if isinstance(extra_button_icon, QIcon) else extra_button_icon.icon()
            )
        extra_button.clicked.connect(extra_button_callback)
        dialog.buttonLayout.insertWidget(0, extra_button, 1)

    # 内容（含额外按钮）全部装配完成后再定尺寸
    _apply_flexible_size(dialog, *min_size)

    return _exec_fluent_dialog(dialog, buttons, default_button)


def _show_message_box(
    parent,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> QMessageBox.StandardButton:
    del icon
    dialog_parent = _dialog_parent(parent)
    content = _wrap_dialog_text(text, width=72)
    dialog = Dialog(str(title or ""), content or " ", dialog_parent)
    return _exec_fluent_dialog(dialog, buttons, default_button)


def themed_information(
    parent,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
):
    return _show_message_box(parent, QMessageBox.Icon.Information, title, text, buttons, default_button)


def themed_warning(
    parent,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
):
    return _show_message_box(parent, QMessageBox.Icon.Warning, title, text, buttons, default_button)


def themed_critical(
    parent,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
):
    return _show_message_box(parent, QMessageBox.Icon.Critical, title, text, buttons, default_button)


def themed_question(
    parent,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
):
    return _show_message_box(parent, QMessageBox.Icon.Question, title, text, buttons, default_button)


def install_themed_message_boxes() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    QMessageBox.information = staticmethod(themed_information)
    QMessageBox.warning = staticmethod(themed_warning)
    QMessageBox.critical = staticmethod(themed_critical)
    QMessageBox.question = staticmethod(themed_question)
    _INSTALLED = True
