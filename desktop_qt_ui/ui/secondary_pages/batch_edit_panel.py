"""批量管理面板 —— 条件匹配 → 预览命中 → 批量写回。

作用范围跟随主页文件列表：``MainWindow`` 把 ``FileCatalogSnapshot`` 推进来，
面板只消费其中的 ``json_by_file``（图片路径 → ``_translations.json`` 路径），
不自己重扫磁盘。

预览是必经步骤，不提供"直接执行"。批量改写用户译文是不可逆操作，除了默认写前
备份，还必须让用户先看到命中列表并能逐条取消勾选。
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    TableWidget,
)

from services import get_config_service, get_i18n_manager
from services import batch_edit_engine as engine
from services.batch_edit_service import (
    CHANNEL_APPLY,
    CHANNEL_RESTORE,
    CHANNEL_SCAN,
    BatchEditService,
)
from services import batch_edit_schemes as scheme_store
from ui.secondary_pages.batch_edit_condition_widgets import (
    ConditionRow,
    ReplaceTextActionCard,
    RichTextActionCard,
    SetFieldsActionCard,
)
from ui.secondary_pages.themed_message_box import (
    show_error_dialog,
    themed_question,
    themed_warning,
)
from ui.secondary_pages.themed_progress_dialog import create_progress_dialog
from ui.secondary_pages.themed_text_input_dialog import themed_get_text


class BatchEditPanel(CardWidget):
    """批量管理主面板。"""

    data_changed = pyqtSignal()
    _AUTOSAVE_DELAY_MS = 600

    COL_CHECK, COL_IMAGE, COL_REGION, COL_BEFORE, COL_AFTER, COL_SUMMARY = range(6)

    def __init__(self, t_func: Callable = None, parent=None):
        super().__init__(parent)
        self._t = t_func or (lambda value, **kwargs: value)
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()

        self._schemes: list[dict] = []
        self._current_index = -1
        self._loading = False
        self._json_by_file: dict[str, str] = {}
        self._matches: list = []
        self._editor_image_getter: Optional[Callable[[], Optional[str]]] = None
        self._editor_reload: Optional[Callable[[str], None]] = None
        self._pending_editor_reload: Optional[str] = None
        self._progress = None

        self._service = BatchEditService(self)
        self._service.scan_ready.connect(self._on_scan_ready, Qt.ConnectionType.QueuedConnection)
        self._service.apply_ready.connect(self._on_apply_ready, Qt.ConnectionType.QueuedConnection)
        self._service.restore_ready.connect(self._on_restore_ready, Qt.ConnectionType.QueuedConnection)
        self._service.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self._service.error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.timeout.connect(self._save_current_scheme)

        self._condition_rows: list[ConditionRow] = []
        self._setup_ui()
        self._load_schemes()

    # ─── 构建 ───

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(ScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)

        layout.addWidget(self._build_scheme_bar(content))
        layout.addWidget(self._build_conditions_card(content))
        layout.addWidget(self._build_actions_card(content))
        layout.addWidget(self._build_preview_card(content), 1)

        scroll.setWidget(content)
        scroll.enableTransparentBackground()
        root.addWidget(scroll, 1)

        status_card = SimpleCardWidget(self)
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(10, 8, 10, 8)
        self.status_label = CaptionLabel("")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        self.scope_label = CaptionLabel("")
        status_layout.addWidget(self.scope_label)
        root.addWidget(status_card)

    def _build_scheme_bar(self, parent: QWidget) -> QWidget:
        card = SimpleCardWidget(parent)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.scheme_label = CaptionLabel(self._t("Scheme:"), card)
        self.scheme_combo = ComboBox(card)
        self.scheme_combo.setMinimumWidth(200)
        self.scheme_combo.currentIndexChanged.connect(self._on_scheme_selected)
        self.new_button = PushButton(self._t("New"), card, FIF.ADD)
        self.rename_button = PushButton(self._t("Rename"), card, FIF.EDIT)
        self.duplicate_button = PushButton(self._t("Duplicate"), card, FIF.COPY)
        self.delete_button = PushButton(self._t("Delete"), card, FIF.DELETE)
        self.new_button.clicked.connect(self._on_new_scheme)
        self.rename_button.clicked.connect(self._on_rename_scheme)
        self.duplicate_button.clicked.connect(self._on_duplicate_scheme)
        self.delete_button.clicked.connect(self._on_delete_scheme)

        layout.addWidget(self.scheme_label)
        layout.addWidget(self.scheme_combo)
        layout.addWidget(self.new_button)
        layout.addWidget(self.rename_button)
        layout.addWidget(self.duplicate_button)
        layout.addWidget(self.delete_button)
        layout.addStretch()
        return card

    def _build_conditions_card(self, parent: QWidget) -> QWidget:
        card = SimpleCardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QWidget(card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.conditions_title = StrongBodyLabel(self._t("Match conditions"), header)
        self.logic_combo = ComboBox(header)
        self.logic_combo.addItem(self._t("Match all"), userData=scheme_store.LOGIC_ALL)
        self.logic_combo.addItem(self._t("Match any"), userData=scheme_store.LOGIC_ANY)
        self.logic_combo.currentIndexChanged.connect(self._mark_dirty)
        self.add_condition_button = PushButton(self._t("Add condition"), header, FIF.ADD)
        self.add_condition_button.clicked.connect(lambda: self._add_condition_row())
        header_layout.addWidget(self.conditions_title)
        header_layout.addWidget(self.logic_combo)
        header_layout.addStretch()
        header_layout.addWidget(self.add_condition_button)
        layout.addWidget(header)

        self.conditions_hint = CaptionLabel(
            self._t("No conditions means every region in scope is selected."), card
        )
        self.conditions_hint.setWordWrap(True)
        layout.addWidget(self.conditions_hint)

        self._conditions_host = QWidget(card)
        self._conditions_layout = QVBoxLayout(self._conditions_host)
        self._conditions_layout.setContentsMargins(0, 0, 0, 0)
        self._conditions_layout.setSpacing(6)
        layout.addWidget(self._conditions_host)
        return card

    def _build_actions_card(self, parent: QWidget) -> QWidget:
        card = SimpleCardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.actions_title = StrongBodyLabel(self._t("Batch actions"), card)
        layout.addWidget(self.actions_title)
        self.actions_hint = CaptionLabel(
            self._t(
                "Applied in a fixed order: properties, then text replacement, then rich text. "
                "Changing the text clears styling on the changed range, so styling must come last. "
                "Within a block, entries run top to bottom."
            ),
            card,
        )
        self.actions_hint.setWordWrap(True)
        layout.addWidget(self.actions_hint)

        locale_getter = self.i18n.get_current_locale if self.i18n else None
        self.set_fields_card = SetFieldsActionCard(self._t, self.config_service, locale_getter, card)
        self.replace_card = ReplaceTextActionCard(self._t, card)
        self.rich_text_card = RichTextActionCard(self._t, card)
        for action_card in (self.set_fields_card, self.replace_card, self.rich_text_card):
            action_card.changed.connect(self._mark_dirty)
            layout.addWidget(action_card)
        return card

    def _build_preview_card(self, parent: QWidget) -> QWidget:
        card = SimpleCardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QWidget(card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.preview_button = PushButton(self._t("Preview matches"), header, FIF.SEARCH)
        self.preview_button.clicked.connect(self._on_preview)
        self.select_all_button = PushButton(self._t("Select All"), header, FIF.CHECKBOX)
        self.select_none_button = PushButton(self._t("Select None"), header)
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        self.match_summary = BodyLabel("", header)
        self.backup_box = CheckBox(self._t("Back up each file before writing"), header)
        self.backup_box.setChecked(True)
        self.backup_box.setToolTip(self._t("Writes a .bak next to each modified JSON"))
        header_layout.addWidget(self.preview_button)
        header_layout.addWidget(self.select_all_button)
        header_layout.addWidget(self.select_none_button)
        header_layout.addWidget(self.match_summary, 1)
        header_layout.addWidget(self.backup_box)
        layout.addWidget(header)

        self.table = TableWidget(card)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(self._table_headers())
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(220)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(self.COL_CHECK, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(self.COL_IMAGE, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(self.COL_REGION, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(self.COL_BEFORE, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(self.COL_AFTER, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(self.COL_SUMMARY, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(self.COL_CHECK, 40)
        self.table.setColumnWidth(self.COL_REGION, 70)
        layout.addWidget(self.table, 1)

        footer = QWidget(card)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.restore_button = PushButton(self._t("Restore from backup"), footer, FIF.HISTORY)
        self.restore_button.setToolTip(
            self._t("Roll every file in scope back to its .bak, then delete the .bak")
        )
        self.restore_button.clicked.connect(self._on_restore)
        footer_layout.addWidget(self.restore_button)
        footer_layout.addStretch()
        self.apply_button = PushButton(self._t("Apply to selected"), footer, FIF.ACCEPT)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._on_apply)
        footer_layout.addWidget(self.apply_button)
        layout.addWidget(footer)
        return card

    def _table_headers(self) -> list[str]:
        return [
            "",
            self._t("Image"),
            self._t("Region"),
            self._t("Before"),
            self._t("After"),
            self._t("Changes"),
        ]

    # ─── 外部注入 ───

    def set_catalog_snapshot(self, snapshot) -> None:
        """由 MainWindow 在主页文件列表快照就绪时推入。"""
        self._json_by_file = dict(getattr(snapshot, "json_by_file", {}) or {})
        self._update_scope_label()

    def set_editor_context(
        self,
        current_image_getter: Optional[Callable[[], Optional[str]]] = None,
        reload_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """编辑器当前图与重载入口 —— 用来处理内存覆盖盘上修改的风险。"""
        self._editor_image_getter = current_image_getter
        self._editor_reload = reload_callback

    def _json_paths(self) -> list[str]:
        seen: dict[str, None] = {}
        for json_path in self._json_by_file.values():
            if json_path:
                seen.setdefault(os.path.abspath(json_path), None)
        return list(seen)

    def _update_scope_label(self) -> None:
        self.scope_label.setText(
            self._t("Scope: {count} translated files from the main file list", count=len(self._json_paths()))
        )

    # ─── 方案 ───

    def _load_schemes(self) -> None:
        self._schemes = scheme_store.load_schemes()
        if not self._schemes:
            self._schemes = [scheme_store.new_scheme(self._t("New scheme"))]
        self._refresh_scheme_combo(0)

    def _refresh_scheme_combo(self, index: int) -> None:
        self._loading = True
        try:
            self.scheme_combo.clear()
            for scheme in self._schemes:
                self.scheme_combo.addItem(scheme["name"])
            index = max(0, min(index, len(self._schemes) - 1))
            self.scheme_combo.setCurrentIndex(index)
        finally:
            self._loading = False
        self._current_index = index
        self._load_scheme_into_ui(self._schemes[index] if self._schemes else None)

    def _on_scheme_selected(self, index: int) -> None:
        if self._loading or index < 0 or index >= len(self._schemes):
            return
        if self._autosave.isActive():
            self._autosave.stop()
            self._save_current_scheme()
        self._current_index = index
        self._load_scheme_into_ui(self._schemes[index])

    def _load_scheme_into_ui(self, scheme: Optional[dict]) -> None:
        self._loading = True
        try:
            scheme = scheme or scheme_store.new_scheme("")
            match = scheme.get("match") or {}
            logic_index = self.logic_combo.findData(match.get("logic", scheme_store.LOGIC_ALL))
            self.logic_combo.setCurrentIndex(logic_index if logic_index >= 0 else 0)

            for row in list(self._condition_rows):
                self._remove_condition_row(row, silent=True)
            for condition in match.get("conditions") or []:
                self._add_condition_row(condition, silent=True)

            by_type: dict[str, list[dict]] = {}
            for action in scheme.get("actions") or []:
                by_type.setdefault(action["type"], []).append(action)
            self.set_fields_card.load_actions(by_type.get(scheme_store.ACTION_SET_FIELDS) or [])
            self.replace_card.load_actions(by_type.get(scheme_store.ACTION_REPLACE_TEXT) or [])
            self.rich_text_card.load_actions(by_type.get(scheme_store.ACTION_RICH_TEXT) or [])
        finally:
            self._loading = False
        self._clear_matches()

    def _collect_scheme(self) -> dict:
        name = self._schemes[self._current_index]["name"] if 0 <= self._current_index < len(self._schemes) \
            else self._t("New scheme")
        actions: list[dict] = []
        for card in (self.set_fields_card, self.replace_card, self.rich_text_card):
            actions.extend(card.to_actions())
        return scheme_store.normalize_scheme({
            "name": name,
            "enabled": True,
            "match": {
                "logic": self.logic_combo.currentData() or scheme_store.LOGIC_ALL,
                "conditions": [row.to_dict() for row in self._condition_rows],
            },
            "actions": actions,
        }) or scheme_store.new_scheme(name)

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._autosave.start(self._AUTOSAVE_DELAY_MS)
        # 条件/动作一改，上一次的预览结果就作废了
        self._clear_matches()

    def _save_current_scheme(self) -> None:
        if not (0 <= self._current_index < len(self._schemes)):
            return
        self._schemes[self._current_index] = self._collect_scheme()
        try:
            scheme_store.save_schemes(self._schemes)
        except OSError as exc:
            self._set_status(f"{self._t('Save error')}: {exc}")
            return
        self._set_status(self._t("Saved automatically"))
        self.data_changed.emit()

    def _on_new_scheme(self) -> None:
        name = self._ask_name(self._t("New scheme"), "")
        if not name:
            return
        self._save_current_scheme()
        self._schemes.append(scheme_store.new_scheme(name))
        self._refresh_scheme_combo(len(self._schemes) - 1)
        self._save_current_scheme()

    def _on_duplicate_scheme(self) -> None:
        if not (0 <= self._current_index < len(self._schemes)):
            return
        self._save_current_scheme()
        source = self._schemes[self._current_index]
        name = self._ask_name(self._t("Duplicate"), f"{source['name']} 2")
        if not name:
            return
        clone = scheme_store.normalize_scheme({**source, "name": name})
        if clone is None:
            return
        self._schemes.append(clone)
        self._refresh_scheme_combo(len(self._schemes) - 1)
        self._save_current_scheme()

    def _on_rename_scheme(self) -> None:
        if not (0 <= self._current_index < len(self._schemes)):
            return
        name = self._ask_name(self._t("Rename"), self._schemes[self._current_index]["name"])
        if not name:
            return
        self._schemes[self._current_index]["name"] = name
        self._refresh_scheme_combo(self._current_index)
        self._save_current_scheme()

    def _on_delete_scheme(self) -> None:
        if not (0 <= self._current_index < len(self._schemes)):
            return
        name = self._schemes[self._current_index]["name"]
        if themed_question(
            self, self._t("Delete"),
            self._t("Delete scheme '{name}'?", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._autosave.stop()
        del self._schemes[self._current_index]
        if not self._schemes:
            self._schemes = [scheme_store.new_scheme(self._t("New scheme"))]
        self._refresh_scheme_combo(min(self._current_index, len(self._schemes) - 1))
        try:
            scheme_store.save_schemes(self._schemes)
        except OSError as exc:
            self._set_status(f"{self._t('Save error')}: {exc}")

    def _ask_name(self, title: str, default: str) -> str:
        text, ok = themed_get_text(
            self, title, self._t("Scheme name"), default,
            ok_text=self._t("OK"), cancel_text=self._t("Cancel"),
        )
        name = str(text or "").strip()
        if not ok or not name:
            return ""
        if any(scheme["name"] == name for scheme in self._schemes):
            themed_warning(self, title, self._t("A scheme named '{name}' already exists.", name=name))
            return ""
        return name

    # ─── 条件行 ───

    def _add_condition_row(self, condition: Optional[dict] = None, silent: bool = False) -> ConditionRow:
        locale_getter = self.i18n.get_current_locale if self.i18n else None
        row = ConditionRow(self._t, self.config_service, locale_getter, self._conditions_host)
        if condition:
            row.load(condition)
        row.changed.connect(self._mark_dirty)
        row.remove_requested.connect(self._remove_condition_row)
        self._conditions_layout.addWidget(row)
        self._condition_rows.append(row)
        if not silent:
            self._mark_dirty()
        return row

    def _remove_condition_row(self, row: ConditionRow, silent: bool = False) -> None:
        if row in self._condition_rows:
            self._condition_rows.remove(row)
        self._conditions_layout.removeWidget(row)
        row.deleteLater()
        if not silent:
            self._mark_dirty()

    # ─── 预览 ───

    def _clear_matches(self) -> None:
        self._matches = []
        self.table.setRowCount(0)
        self.match_summary.setText("")
        self.apply_button.setEnabled(False)

    def _on_preview(self) -> None:
        json_paths = self._json_paths()
        if not json_paths:
            themed_warning(
                self, self._t("Preview matches"),
                self._t("The main file list has no translated files yet. Add files on the "
                        "translation page first."),
            )
            return
        scheme = self._collect_scheme()
        if not scheme.get("actions"):
            themed_warning(self, self._t("Preview matches"),
                           self._t("Enable at least one batch action first."))
            return
        self._clear_matches()
        self._open_progress(self._t("Preview matches"), self._t("Scanning..."), len(json_paths), CHANNEL_SCAN)
        self._service.request_scan(json_paths, scheme)

    def _on_scan_ready(self, _generation: int, result) -> None:
        self._close_progress()
        self._matches = list(result.matches)
        self.table.setRowCount(0)
        for item in self._matches:
            self._append_match_row(item)
        self.match_summary.setText(self._t(
            "{regions} regions in {files} files",
            regions=len(self._matches), files=result.file_count,
        ))
        self.apply_button.setEnabled(bool(self._matches))
        detail = self._t("Scanned {regions} regions in {files} files",
                         regions=result.scanned_regions, files=result.scanned_files)
        if result.skipped_regions:
            detail += " / " + self._t("{count} malformed regions skipped", count=result.skipped_regions)
        self._set_status(detail)
        if result.errors:
            show_error_dialog(
                self, self._t("Preview matches"),
                self._t("{count} files could not be read", count=len(result.errors)),
                "\n".join(f"{path}\n    {message}" for path, message in result.errors),
            )

    def _append_match_row(self, item) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        check.setCheckState(Qt.CheckState.Checked)
        self.table.setItem(row, self.COL_CHECK, check)
        for column, value in (
            (self.COL_IMAGE, item.image_name),
            (self.COL_REGION, str(item.region_index)),
            (self.COL_BEFORE, item.before_text.replace("\n", "\\n")),
            (self.COL_AFTER, item.after_text.replace("\n", "\\n")),
            (self.COL_SUMMARY, item.summary),
        ):
            cell = QTableWidgetItem(value)
            cell.setToolTip(item.json_path if column == self.COL_IMAGE else value)
            self.table.setItem(row, column, cell)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_CHECK)
            if item is not None:
                item.setCheckState(state)

    def _selected_matches(self) -> list:
        selected = []
        for row, item in enumerate(self._matches):
            cell = self.table.item(row, self.COL_CHECK)
            if cell is not None and cell.checkState() == Qt.CheckState.Checked:
                selected.append(item)
        return selected

    # ─── 执行 ───

    def _conflicting_editor_image(self, target_paths) -> Optional[str]:
        """编辑器正打开的图是否在本次写回范围内。

        编辑器把 region 常驻内存且没有任何文件监听，切图时的自动导出会用内存里
        的旧数据全量覆盖 —— 不提示的话批量修改会被静默抹掉。
        """
        if self._editor_image_getter is None:
            return None
        try:
            current = self._editor_image_getter()
        except Exception:
            return None
        if not current:
            return None
        current_json = self._json_by_file.get(os.path.abspath(current)) or self._json_by_file.get(current)
        if not current_json:
            return None
        return current if os.path.abspath(current_json) in set(target_paths) else None

    def _on_apply(self) -> None:
        selected = self._selected_matches()
        if not selected:
            themed_warning(self, self._t("Apply to selected"), self._t("Nothing is selected."))
            return

        files = len({item.json_path for item in selected})
        message = self._t("Apply this scheme to {regions} regions in {files} files?",
                          regions=len(selected), files=files)
        conflict = self._conflicting_editor_image({item.json_path for item in selected})
        if conflict:
            message += "\n\n" + self._t(
                "The editor currently has '{name}' open. Its in-memory copy will overwrite these "
                "changes when you switch images. The editor will be reloaded after applying.",
                name=os.path.basename(conflict),
            )
        if not self.backup_box.isChecked():
            message += "\n\n" + self._t("Backups are disabled. This cannot be undone.")

        if themed_question(
            self, self._t("Apply to selected"), message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        self._pending_editor_reload = conflict
        self._open_progress(self._t("Apply to selected"), self._t("Writing..."), files, CHANNEL_APPLY)
        self._service.request_apply(
            [item.key for item in selected],
            self._collect_scheme(),
            backup=self.backup_box.isChecked(),
        )

    def _on_apply_ready(self, _generation: int, report) -> None:
        self._close_progress()
        self._set_status(self._t("Updated {regions} regions in {files} files",
                                 regions=report.changed_regions, files=len(report.written_files)))
        reload_target = getattr(self, "_pending_editor_reload", None)
        self._pending_editor_reload = None
        if reload_target and self._editor_reload is not None:
            try:
                self._editor_reload(reload_target)
            except Exception:
                pass
        if report.errors:
            show_error_dialog(
                self, self._t("Apply to selected"),
                self._t("{count} files could not be written", count=len(report.errors)),
                "\n".join(f"{path}\n    {message}" for path, message in report.errors),
            )
        # 盘上已经变了，旧的预览结果不再可信
        self._clear_matches()

    # ─── 恢复 ───

    def _restorable_paths(self) -> list[str]:
        return [path for path in self._json_paths() if engine.has_backup(path)]

    def _on_restore(self) -> None:
        paths = self._restorable_paths()
        if not paths:
            themed_warning(self, self._t("Restore from backup"),
                           self._t("No backup found for the files in scope."))
            return

        message = self._t("Roll {files} files back to their backup? The backup is consumed.",
                          files=len(paths))
        # 恢复也是全量覆盖，编辑器内存里的旧副本同样会把它盖回去
        conflict = self._conflicting_editor_image(os.path.abspath(path) for path in paths)
        if conflict:
            message += "\n\n" + self._t(
                "The editor currently has '{name}' open. Its in-memory copy will overwrite these "
                "changes when you switch images. The editor will be reloaded after applying.",
                name=os.path.basename(conflict),
            )
        if themed_question(
            self, self._t("Restore from backup"), message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        self._pending_editor_reload = conflict
        self._open_progress(self._t("Restore from backup"), self._t("Restoring..."),
                            len(paths), CHANNEL_RESTORE)
        self._service.request_restore(paths)

    def _on_restore_ready(self, _generation: int, report) -> None:
        self._close_progress()
        self._set_status(self._t("Restored {files} files", files=len(report.restored_files)))
        reload_target = getattr(self, "_pending_editor_reload", None)
        self._pending_editor_reload = None
        if reload_target and self._editor_reload is not None:
            try:
                self._editor_reload(reload_target)
            except Exception:
                pass
        if report.errors:
            show_error_dialog(
                self, self._t("Restore from backup"),
                self._t("{count} files could not be written", count=len(report.errors)),
                "\n".join(f"{path}\n    {message}" for path, message in report.errors),
            )
        self._clear_matches()

    # ─── 进度 / 状态 ───

    def _open_progress(self, title: str, label: str, total: int, channel: str) -> None:
        self._close_progress()
        self._progress = create_progress_dialog(self, title, label, self._t("Cancel"))
        self._progress.setRange(0, max(total, 0))
        self._progress.rejected.connect(lambda: self._on_cancelled(channel))
        self._progress.show()

    def _on_cancelled(self, channel: str) -> None:
        """取消后 worker 不再发结果信号，状态得在这里收尾。"""
        self._service.cancel(channel)
        self._progress = None
        self._set_status(self._t("Cancelled"))

    def _on_progress(self, _channel: str, _generation: int, done: int, total: int) -> None:
        if self._progress is None:
            return
        self._progress.setRange(0, max(total, 0))
        self._progress.setValue(done)

    def _close_progress(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_error(self, channel: str, _generation: int, message: str) -> None:
        self._close_progress()
        self._set_status(f"{self._t('Error')}: {message}")
        themed_warning(
            self,
            self._t("Preview matches") if channel == CHANNEL_SCAN else self._t("Apply to selected"),
            message,
        )

    # ─── 页面约定 ───

    def refresh(self) -> None:
        if self._autosave.isActive():
            return
        self._load_schemes()
        self._update_scope_label()

    def refresh_ui_texts(self) -> None:
        self.scheme_label.setText(self._t("Scheme:"))
        self.new_button.setText(self._t("New"))
        self.rename_button.setText(self._t("Rename"))
        self.duplicate_button.setText(self._t("Duplicate"))
        self.delete_button.setText(self._t("Delete"))
        self.conditions_title.setText(self._t("Match conditions"))
        self.logic_combo.setItemText(0, self._t("Match all"))
        self.logic_combo.setItemText(1, self._t("Match any"))
        self.add_condition_button.setText(self._t("Add condition"))
        self.conditions_hint.setText(self._t("No conditions means every region in scope is selected."))
        self.actions_title.setText(self._t("Batch actions"))
        self.actions_hint.setText(self._t(
            "Applied in a fixed order: properties, then text replacement, then rich text. "
            "Changing the text clears styling on the changed range, so styling must come last. "
            "Within a block, entries run top to bottom."
        ))
        self.preview_button.setText(self._t("Preview matches"))
        self.select_all_button.setText(self._t("Select All"))
        self.select_none_button.setText(self._t("Select None"))
        self.backup_box.setText(self._t("Back up each file before writing"))
        self.backup_box.setToolTip(self._t("Writes a .bak next to each modified JSON"))
        self.apply_button.setText(self._t("Apply to selected"))
        self.restore_button.setText(self._t("Restore from backup"))
        self.restore_button.setToolTip(
            self._t("Roll every file in scope back to its .bak, then delete the .bak")
        )
        self.table.setHorizontalHeaderLabels(self._table_headers())
        for row in self._condition_rows:
            row.refresh_ui_texts()
        for action_card in (self.set_fields_card, self.replace_card, self.rich_text_card):
            action_card.refresh_ui_texts()
        self._update_scope_label()

    def apply_theme(self) -> None:
        from ui.widgets.color_picker import ColorPickerWidget

        for picker in self.findChildren(ColorPickerWidget):
            picker.refresh_theme()
        self.update()

    def shutdown(self) -> None:
        if self._autosave.isActive():
            self._autosave.stop()
            self._save_current_scheme()
        self._service.shutdown()
