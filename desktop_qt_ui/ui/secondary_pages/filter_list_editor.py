import json
from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CardWidget, CaptionLabel, PlainTextEdit, PrimaryPushButton, PushButton, SegmentedWidget, TitleLabel
from ui.theme import (
    monospace_font as _monospace_font,
)

from manga_translator.utils.text_filter import (
    ensure_filter_list_exists,
    save_filter_list_config,
)


def _split_rules(text: str) -> list[str]:
    rules = []
    for line in text.splitlines():
        normalized = line.strip()
        if normalized:
            rules.append(normalized)
    return rules


def _sanitize_rule_values(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    rules = []
    for value in values:
        text = str(value or "").strip()
        if text:
            rules.append(text)
    return rules


class _SegmentedTabWidget(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._keys: list[str] = []
        self._segmented = SegmentedWidget(self)
        self._stack = QStackedWidget(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._segmented)
        layout.addWidget(self._stack, 1)

    def addTab(self, widget: QWidget, text: str):
        index = len(self._keys)
        key = f"tab_{index}"
        self._keys.append(key)
        self._stack.addWidget(widget)
        self._segmented.addItem(key, text, onClick=lambda i=index: self.setCurrentIndex(i))
        if index == 0:
            self.setCurrentIndex(0)

    def currentIndex(self) -> int:
        return self._stack.currentIndex()

    def setCurrentIndex(self, index: int):
        if index < 0 or index >= len(self._keys):
            return
        self._stack.setCurrentIndex(index)
        self._segmented.setCurrentItem(self._keys[index])
        self.currentChanged.emit(index)


class FilterListEditorDialog(QDialog):
    def __init__(self, file_path: str | None = None, t_func: Callable[[str], str] | None = None, parent=None):
        super().__init__(parent)
        self._t = t_func or (lambda text, **kwargs: text.format(**kwargs) if kwargs else text)
        self._file_path = file_path or ensure_filter_list_exists()
        self._original_content = ""
        self._extra_data: dict[str, Any] = {}
        self._setup_ui()
        self._load_from_disk()

    def _setup_ui(self):
        self.setWindowTitle(self._t("Edit Filter List"))
        self.setMinimumSize(880, 620)
        self.resize(980, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = TitleLabel(self._t("Edit Filter List"))
        subtitle = BodyLabel(self._t("Edit OCR text filter rules skipped during translation."))
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self.tabs = _SegmentedTabWidget()
        root.addWidget(self.tabs, 1)

        self._build_rules_tab()
        self._build_raw_tab()

        self.status_label = CaptionLabel("")
        root.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.refresh_button = PushButton(self._t("Refresh"))
        self.refresh_button.clicked.connect(self._load_from_disk)

        self.cancel_button = PushButton(self._t("Cancel"))
        self.cancel_button.clicked.connect(self.reject)

        self.save_button = PrimaryPushButton(self._t("Save"))
        self.save_button.clicked.connect(self._save)

        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)
        root.addLayout(button_row)

    def _build_rules_tab(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(8, 8, 8, 8)
        page_layout.setSpacing(10)

        page_layout.addWidget(self._build_rules_card(
            self._t("Contains Filter"),
            self._t("Skip when OCR text contains any of these rules."),
            "contains",
        ))
        page_layout.addWidget(self._build_rules_card(
            self._t("Exact Filter"),
            self._t("Skip only when OCR text exactly matches one of these rules."),
            "exact",
        ))
        page_layout.addStretch(1)

        self.tabs.addTab(page, self._t("Filter Rules"))

    def _build_rules_card(self, title_text: str, hint_text: str, mode: str) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        title = BodyLabel(title_text)
        hint = CaptionLabel(hint_text)
        hint.setWordWrap(True)

        editor = PlainTextEdit()
        editor.setFont(_monospace_font())
        editor.setTabStopDistance(28)
        editor.setPlaceholderText(self._t("One rule per line"))

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(editor, 1)

        if mode == "contains":
            self.contains_editor = editor
        else:
            self.exact_editor = editor
        return card

    def _build_raw_tab(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(8, 8, 8, 8)
        page_layout.setSpacing(8)

        hint = CaptionLabel(self._t("Edit the raw file content directly"))
        page_layout.addWidget(hint)

        self.raw_editor = PlainTextEdit()
        self.raw_editor.setFont(_monospace_font())
        self.raw_editor.setTabStopDistance(28)
        page_layout.addWidget(self.raw_editor, 1)

        self.tabs.addTab(page, self._t("Raw Edit"))

    def _set_status(self, message: str, kind: str = "default"):
        del kind
        self.status_label.setText(message)

    def _load_from_disk(self):
        self._file_path = ensure_filter_list_exists()
        try:
            with open(self._file_path, 'r', encoding='utf-8') as handle:
                content = handle.read().strip()
        except FileNotFoundError:
            content = "{}"
        except Exception as exc:
            self._set_status(f"{self._t('Load failed')}: {exc}", kind="error")
            return

        if not content:
            content = "{}"

        self._original_content = content
        self.raw_editor.setPlainText(content)

        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError(self._t("JSON root must be an object"))
        except Exception as exc:
            self.contains_editor.setPlainText("")
            self.exact_editor.setPlainText("")
            self._extra_data = {}
            self._set_status(f"{self._t('JSON format error')}: {exc}", kind="error")
            return

        self._extra_data = {k: v for k, v in parsed.items() if k not in ("contains", "exact")}
        self.contains_editor.setPlainText("\n".join(_sanitize_rule_values(parsed.get("contains", []))))
        self.exact_editor.setPlainText("\n".join(_sanitize_rule_values(parsed.get("exact", []))))
        self._set_status(self._t("Loaded successfully"))

    def _collect_structured_data(self) -> dict[str, Any]:
        data = dict(self._extra_data)
        data["contains"] = _split_rules(self.contains_editor.toPlainText())
        data["exact"] = _split_rules(self.exact_editor.toPlainText())
        return data

    def _collect_raw_data(self) -> dict[str, Any]:
        content = self.raw_editor.toPlainText().strip() or "{}"
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError(self._t("JSON root must be an object"))
        return parsed

    def _save(self):
        try:
            if self.tabs.currentIndex() == 0:
                data = self._collect_structured_data()
            else:
                data = self._collect_raw_data()
        except json.JSONDecodeError as exc:
            self._set_status(f"{self._t('JSON format error')}: {exc}", kind="error")
            return
        except ValueError as exc:
            self._set_status(str(exc), kind="error")
            return

        try:
            save_filter_list_config(data)
        except Exception as exc:
            self._set_status(f"{self._t('Save failed')}: {exc}", kind="error")
            return

        formatted = json.dumps(data, indent=2, ensure_ascii=False)
        self._original_content = formatted
        self.raw_editor.setPlainText(formatted)
        self._extra_data = {k: v for k, v in data.items() if k not in ("contains", "exact")}
        self.contains_editor.setPlainText("\n".join(data.get("contains", [])))
        self.exact_editor.setPlainText("\n".join(data.get("exact", [])))
        self._set_status(self._t("Saved successfully"), kind="success")

    def get_was_modified(self) -> bool:
        current = self.raw_editor.toPlainText().strip()
        return current != self._original_content.strip()
