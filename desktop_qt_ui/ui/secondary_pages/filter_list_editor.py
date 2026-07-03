import json
from typing import Any, Callable

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CaptionLabel,
    FluentIcon as FIF,
    HorizontalSeparator,
    PlainTextEdit,
    PopUpAniStackedWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    SimpleCardWidget,
    StrongBodyLabel,
    TitleLabel,
)
from ui.secondary_pages.fluent_dialog import FluentSecondaryDialog
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


class FilterListEditorDialog(FluentSecondaryDialog):
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

        header_card = CardWidget(self)
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(4)

        title = TitleLabel(self._t("Edit Filter List"), header_card)
        subtitle = BodyLabel(self._t("Edit OCR text filter rules skipped during translation."), header_card)
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header_card)

        self.tab_segmented = SegmentedWidget(self)
        self.tab_stack = PopUpAniStackedWidget(self)
        root.addWidget(self.tab_segmented)
        root.addWidget(self.tab_stack, 1)

        self._build_rules_tab()
        self._build_raw_tab()

        self.status_label = CaptionLabel("")
        root.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.refresh_button = PushButton(self._t("Refresh"))
        self.refresh_button.setIcon(FIF.SYNC)
        self.refresh_button.clicked.connect(self._load_from_disk)

        self.cancel_button = PushButton(self._t("Cancel"))
        self.cancel_button.setIcon(FIF.CANCEL)
        self.cancel_button.clicked.connect(self.reject)

        self.save_button = PrimaryPushButton(self._t("Save"))
        self.save_button.setIcon(FIF.SAVE)
        self.save_button.clicked.connect(self._save)

        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)
        root.addLayout(button_row)

    def _build_rules_tab(self):
        page = ScrollArea(self.tab_stack)
        page.setWidgetResizable(True)
        page.setFrameShape(ScrollArea.Shape.NoFrame)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        host = QWidget(page)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        card = SimpleCardWidget(host)
        page_layout = QVBoxLayout(card)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(10)

        contains_title = StrongBodyLabel(self._t("Contains Filter"), card)
        contains_hint = CaptionLabel(self._t("Skip when OCR text contains any of these rules."), card)
        contains_hint.setWordWrap(True)

        self.contains_editor = PlainTextEdit(card)
        self.contains_editor.setFont(_monospace_font())
        self.contains_editor.setTabStopDistance(28)
        self.contains_editor.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.contains_editor.setPlaceholderText(self._t("One rule per line"))
        self.contains_editor.setMinimumHeight(180)

        exact_title = StrongBodyLabel(self._t("Exact Filter"), card)
        exact_hint = CaptionLabel(self._t("Skip only when OCR text exactly matches one of these rules."), card)
        exact_hint.setWordWrap(True)

        self.exact_editor = PlainTextEdit(card)
        self.exact_editor.setFont(_monospace_font())
        self.exact_editor.setTabStopDistance(28)
        self.exact_editor.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.exact_editor.setPlaceholderText(self._t("One rule per line"))
        self.exact_editor.setMinimumHeight(180)

        page_layout.addWidget(contains_title)
        page_layout.addWidget(contains_hint)
        page_layout.addWidget(self.contains_editor, 1)
        page_layout.addWidget(HorizontalSeparator(card))
        page_layout.addWidget(exact_title)
        page_layout.addWidget(exact_hint)
        page_layout.addWidget(self.exact_editor, 1)
        host_layout.addWidget(card)
        page.setWidget(host)
        page.enableTransparentBackground()

        route_key = "filter_rules"
        page_index = self.tab_stack.count()
        self.tab_stack.addWidget(page)
        self.tab_segmented.addItem(
            route_key,
            self._t("Filter Rules"),
            onClick=lambda checked=False: (
                self.tab_stack.setCurrentIndex(page_index),
                self.tab_segmented.setCurrentItem(route_key),
            ),
        )
        self.tab_stack.setCurrentIndex(page_index)
        self.tab_segmented.setCurrentItem(route_key)

    def _build_raw_tab(self):
        page = ScrollArea(self.tab_stack)
        page.setWidgetResizable(True)
        page.setFrameShape(ScrollArea.Shape.NoFrame)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        host = QWidget(page)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        card = SimpleCardWidget(host)
        page_layout = QVBoxLayout(card)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(8)

        hint = CaptionLabel(self._t("Edit the raw file content directly"), card)
        hint.setWordWrap(True)
        page_layout.addWidget(hint)

        self.raw_editor = PlainTextEdit(card)
        self.raw_editor.setFont(_monospace_font())
        self.raw_editor.setTabStopDistance(28)
        self.raw_editor.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.raw_editor.setMinimumHeight(360)
        page_layout.addWidget(self.raw_editor, 1)
        host_layout.addWidget(card)
        page.setWidget(host)
        page.enableTransparentBackground()

        route_key = "raw_edit"
        page_index = self.tab_stack.count()
        self.tab_stack.addWidget(page)
        self.tab_segmented.addItem(
            route_key,
            self._t("Raw Edit"),
            onClick=lambda checked=False: (
                self.tab_stack.setCurrentIndex(page_index),
                self.tab_segmented.setCurrentItem(route_key),
            ),
        )
        if page_index == 0:
            self.tab_stack.setCurrentIndex(page_index)
            self.tab_segmented.setCurrentItem(route_key)

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
            if self.tab_stack.currentIndex() == 0:
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
