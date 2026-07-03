from __future__ import annotations

import textwrap

from PyQt6.QtWidgets import QApplication, QMessageBox
from qfluentwidgets import Dialog, FluentIcon as FIF, MessageBox, PlainTextEdit

_INSTALLED = False


def _dialog_parent(parent):
    return parent or QApplication.activeWindow()


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
    dialog: Dialog | MessageBox,
    buttons: QMessageBox.StandardButton,
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> tuple[QMessageBox.StandardButton, QMessageBox.StandardButton]:
    accept_button, reject_button = _resolve_dialog_buttons(buttons)
    dialog.yesButton.setText(_button_text(accept_button))
    dialog.yesButton.setIcon(_BUTTON_ICONS.get(accept_button, FIF.ACCEPT))

    if reject_button == QMessageBox.StandardButton.NoButton:
        dialog.hideCancelButton()
    else:
        dialog.cancelButton.setText(_button_text(reject_button))
        dialog.cancelButton.setIcon(_BUTTON_ICONS.get(reject_button, FIF.CANCEL))

    if default_button == reject_button and reject_button != QMessageBox.StandardButton.NoButton:
        dialog.cancelButton.setFocus()
    else:
        dialog.yesButton.setFocus()
    return accept_button, reject_button


def _exec_fluent_dialog(
    dialog: Dialog | MessageBox,
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
        dialog.textLayout.addWidget(details_edit)
        dialog.setFixedSize(720, 460)
    else:
        dialog.setFixedSize(max(dialog.width(), 520), max(dialog.height(), 220))

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
    if dialog_parent is not None:
        dialog = MessageBox(str(title or ""), content or " ", dialog_parent)
    else:
        dialog = Dialog(str(title or ""), content or " ")
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
