"""
替换规则管理页面 - 可视化编辑 text_replacements.yaml
支持三个分组（common/horizontal/vertical），每条规则支持字面替换、正则替换、启用/禁用
支持表格模式和原始 YAML 编辑模式切换
"""
import os
import sys
from typing import Callable, Dict, List, Optional

import yaml
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from main_view_parts.theme import get_current_theme_colors


def _get_replacements_path() -> str:
    """获取 text_replacements.yaml 的路径"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    return os.path.join(base_path, 'examples', 'text_replacements.yaml')


class YamlHighlighter(QSyntaxHighlighter):
    """简单的 YAML 语法高亮"""

    def highlightBlock(self, text: str):
        colors = get_current_theme_colors()

        # 注释
        if text.lstrip().startswith('#'):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colors.get("text_secondary", "#888888")))
            fmt.setFontItalic(True)
            self.setFormat(0, len(text), fmt)
            return

        # key:
        colon_idx = text.find(':')
        if colon_idx > 0 and not text.lstrip().startswith('-'):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colors.get("cta_gradient_start", "#4a90d9")))
            fmt.setFontWeight(QFont.Weight.Bold)
            self.setFormat(0, colon_idx, fmt)


class ReplacementsEditorPanel(QWidget):
    """替换规则编辑面板 - 表格 + 原始编辑双模式"""

    data_changed = pyqtSignal()

    # 表格列索引
    COL_ENABLED = 0
    COL_PATTERN = 1
    COL_REPLACE = 2
    COL_REGEX = 3
    COL_COMMENT = 4
    COL_COUNT = 5

    _YES = "✓"
    _NO = "✗"

    def __init__(self, t_func: Callable = None, parent=None):
        super().__init__(parent)
        self._t = t_func or (lambda x, **kw: x)
        self._file_path = _get_replacements_path()
        self._modified = False
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- 顶部工具栏 ---
        toolbar = QWidget()
        toolbar.setObjectName("replacements_toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)

        self._add_button = QPushButton(self._t("Add Rule"))
        self._add_button.setProperty("chipButton", True)
        self._delete_button = QPushButton(self._t("Delete"))
        self._delete_button.setProperty("chipButton", True)
        self._move_up_button = QPushButton("↑")
        self._move_up_button.setProperty("chipButton", True)
        self._move_up_button.setFixedWidth(32)
        self._move_down_button = QPushButton("↓")
        self._move_down_button.setProperty("chipButton", True)
        self._move_down_button.setFixedWidth(32)

        self._select_all_button = QPushButton(self._t("Select All"))
        self._select_all_button.setProperty("chipButton", True)

        # 启用/禁用 + 正则切换按钮（根据选中行状态动态变化）
        self._toggle_enabled_button = QPushButton(self._t("Enable"))
        self._toggle_enabled_button.setProperty("chipButton", True)
        self._toggle_regex_button = QPushButton(self._t("Regex"))
        self._toggle_regex_button.setProperty("chipButton", True)

        # 模式切换按钮
        self._mode_button = QPushButton(self._t("Raw Edit"))
        self._mode_button.setProperty("chipButton", True)
        self._mode_button.setCheckable(True)

        self._save_button = QPushButton(self._t("Save"))
        self._save_button.setProperty("chipButton", True)
        self._save_button.setEnabled(False)

        toolbar_layout.addWidget(self._add_button)
        toolbar_layout.addWidget(self._delete_button)
        toolbar_layout.addWidget(self._move_up_button)
        toolbar_layout.addWidget(self._move_down_button)
        toolbar_layout.addWidget(self._select_all_button)
        toolbar_layout.addWidget(self._toggle_enabled_button)
        toolbar_layout.addWidget(self._toggle_regex_button)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self._mode_button)
        toolbar_layout.addWidget(self._save_button)
        layout.addWidget(toolbar)

        # --- 搜索 / 预设栏 ---
        filter_row = QWidget()
        filter_row.setObjectName("replacements_filter_row")
        filter_row_layout = QHBoxLayout(filter_row)
        filter_row_layout.setContentsMargins(0, 0, 0, 0)
        filter_row_layout.setSpacing(8)

        self._search_label = QLabel(self._t("Filter:"))
        self._search_input = QLineEdit()
        self._search_input.setObjectName("replacements_search")
        self._search_input.setPlaceholderText(self._t("Type to filter by pattern / replace / comment..."))
        self._search_input.setClearButtonEnabled(True)

        # 预设按钮位（接口预留：将来通过 register_preset_button 加按钮，目前为空隐藏）
        self._preset_slot = QWidget()
        self._preset_slot.setObjectName("replacements_preset_slot")
        self._preset_slot_layout = QHBoxLayout(self._preset_slot)
        self._preset_slot_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_slot_layout.setSpacing(6)

        filter_row_layout.addWidget(self._search_label)
        filter_row_layout.addWidget(self._search_input, 1)
        filter_row_layout.addWidget(self._preset_slot)
        layout.addWidget(filter_row)
        self._filter_row = filter_row

        # --- 双模式切换容器 ---
        self._mode_stack = QStackedWidget()

        # === 模式1: 表格模式 ===
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)

        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("replacements_tabs")

        self._tables: Dict[str, QTableWidget] = {}
        for group_key, group_label in [
            ("common", self._t("Common (Always)")),
            ("horizontal", self._t("Horizontal")),
            ("vertical", self._t("Vertical")),
        ]:
            table = self._create_table()
            self._tables[group_key] = table
            self._tab_widget.addTab(table, group_label)

        table_layout.addWidget(self._tab_widget)
        self._mode_stack.addWidget(table_container)

        # === 模式2: 原始 YAML 编辑 ===
        raw_container = QWidget()
        raw_layout = QVBoxLayout(raw_container)
        raw_layout.setContentsMargins(0, 0, 0, 0)
        raw_layout.setSpacing(4)

        raw_hint = QLabel(self._t("Edit raw YAML content directly. Save to apply changes."))
        raw_hint.setObjectName("page_subtitle")
        raw_hint.setWordWrap(True)
        raw_layout.addWidget(raw_hint)
        self._raw_hint_label = raw_hint

        self._raw_editor = QPlainTextEdit()
        self._raw_editor.setObjectName("replacements_raw_editor")
        self._raw_editor.setFont(QFont("Consolas", 10))
        self._raw_editor.setTabStopDistance(20)
        self._raw_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = YamlHighlighter(self._raw_editor.document())
        raw_layout.addWidget(self._raw_editor, 1)

        self._mode_stack.addWidget(raw_container)
        layout.addWidget(self._mode_stack, 1)

        # --- 状态栏 ---
        status_row = QWidget()
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(12)
        self._status_label = QLabel("")
        self._status_label.setObjectName("page_subtitle")
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()
        layout.addWidget(status_row)

        # --- 信号连接 ---
        self._add_button.clicked.connect(self._on_add_rule)
        self._delete_button.clicked.connect(self._on_delete_rule)
        self._move_up_button.clicked.connect(lambda: self._on_move_rule(-1))
        self._move_down_button.clicked.connect(lambda: self._on_move_rule(1))
        self._select_all_button.clicked.connect(self._on_select_all)
        self._toggle_enabled_button.clicked.connect(self._on_toggle_enabled)
        self._toggle_regex_button.clicked.connect(self._on_toggle_regex)
        self._mode_button.clicked.connect(self._on_toggle_mode)
        self._save_button.clicked.connect(self._on_save)
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        self._raw_editor.textChanged.connect(self._on_raw_changed)
        self._search_input.textChanged.connect(self._on_search_changed)

    def _create_table(self) -> QTableWidget:
        """创建规则编辑表格"""
        table = QTableWidget()
        table.setColumnCount(self.COL_COUNT)
        table.setHorizontalHeaderLabels([
            self._t("Enabled"),
            self._t("Pattern"),
            self._t("Replace"),
            self._t("Regex"),
            self._t("Comment"),
        ])
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        # 使用和提示词/字体列表一致的选中高光样式
        colors = get_current_theme_colors()
        table.setStyleSheet(f"""
            QTableWidget#replacements_table {{
                background: {colors["bg_list"]};
                border: 1px solid {colors["border_list"]};
                border-radius: 6px;
                gridline-color: {colors["divider_sub_line"]};
            }}
            QTableWidget#replacements_table::item {{
                padding: 4px 8px;
                border-radius: 4px;
            }}
            QTableWidget#replacements_table::item:hover {{
                background: {colors["list_item_hover"]};
            }}
            QTableWidget#replacements_table::item:selected {{
                background: {colors["list_item_hover"]};
                color: {colors["text_primary"]};
            }}
            QTableWidget#replacements_table QHeaderView::section {{
                background: {colors["bg_toolbar"]};
                color: {colors["text_secondary"]};
                font-weight: 600;
                font-size: 11px;
                padding: 5px 8px;
                border: none;
                border-bottom: 1px solid {colors["border_subtle"]};
            }}
        """)
        table.setObjectName("replacements_table")

        header = table.horizontalHeader()
        header.setSectionResizeMode(self.COL_ENABLED, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_PATTERN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_REPLACE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_REGEX, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_COMMENT, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(self.COL_ENABLED, 50)
        table.setColumnWidth(self.COL_REGEX, 50)

        table.cellChanged.connect(self._on_cell_changed)
        table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        table.itemSelectionChanged.connect(self._on_selection_changed)
        return table

    def _current_table(self) -> QTableWidget:
        idx = self._tab_widget.currentIndex()
        keys = ["common", "horizontal", "vertical"]
        return self._tables[keys[idx]]

    def _current_group_key(self) -> str:
        idx = self._tab_widget.currentIndex()
        return ["common", "horizontal", "vertical"][idx]

    # ─── 数据加载 ───

    def _load_data(self):
        """从 YAML 文件加载数据"""
        if not os.path.exists(self._file_path):
            self._set_status(self._t("File not found"), "warning")
            return

        try:
            with open(self._file_path, 'r', encoding='utf-8') as f:
                raw_content = f.read()
                data = yaml.safe_load(raw_content) or {}
        except Exception as e:
            self._set_status(f"{self._t('Load error')}: {e}", "error")
            return

        # 填充表格
        for group_key, table in self._tables.items():
            table.blockSignals(True)
            table.setRowCount(0)
            rules = data.get(group_key, [])
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                self._add_rule_to_table(table, rule)
            table.blockSignals(False)

        # 填充原始编辑器
        self._raw_editor.blockSignals(True)
        self._raw_editor.setPlainText(raw_content)
        self._raw_editor.blockSignals(False)

        self._modified = False
        self._save_button.setEnabled(False)
        self._update_status()

    def _add_rule_to_table(self, table: QTableWidget, rule: dict):
        """向表格添加一条规则"""
        row = table.rowCount()
        table.insertRow(row)

        pattern = rule.get('pattern', '')
        replace = rule.get('replace', '')
        is_regex = rule.get('regex', False)
        is_enabled = rule.get('enabled', True)
        comment = rule.get('comment', '')

        # 启用列：用文字 ✓/✗ 表示，双击切换
        enabled_item = QTableWidgetItem(self._YES if is_enabled else self._NO)
        enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        enabled_item.setFlags(enabled_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, self.COL_ENABLED, enabled_item)

        table.setItem(row, self.COL_PATTERN, QTableWidgetItem(pattern))
        table.setItem(row, self.COL_REPLACE, QTableWidgetItem(replace))

        # 正则列：用文字 ✓/✗ 表示，双击切换
        regex_item = QTableWidgetItem(self._YES if is_regex else self._NO)
        regex_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        regex_item.setFlags(regex_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, self.COL_REGEX, regex_item)

        table.setItem(row, self.COL_COMMENT, QTableWidgetItem(comment))

        # 禁用的规则灰显
        if not is_enabled:
            self._set_row_dimmed(table, row, True)

    def _set_row_dimmed(self, table: QTableWidget, row: int, dimmed: bool):
        """设置行的灰显状态"""
        colors = get_current_theme_colors()
        color = QColor(colors.get("text_disabled", "#aaaaaa")) if dimmed else QColor(colors.get("text_primary", "#1a1a1a"))
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item:
                item.setForeground(color)

    # ─── 操作 ───

    def _on_cell_double_clicked(self, row: int, col: int):
        """双击 启用/正则 列时切换状态"""
        if col not in (self.COL_ENABLED, self.COL_REGEX):
            return
        table = self.sender()
        if not table:
            return
        item = table.item(row, col)
        if not item:
            return

        table.blockSignals(True)
        if item.text() == self._YES:
            item.setText(self._NO)
        else:
            item.setText(self._YES)

        # 如果是启用列，更新灰显
        if col == self.COL_ENABLED:
            self._set_row_dimmed(table, row, item.text() == self._NO)
        table.blockSignals(False)
        self._mark_modified()

    def _on_add_rule(self):
        """添加新规则"""
        if self._mode_stack.currentIndex() == 1:
            return
        table = self._current_table()
        table.blockSignals(True)
        row = table.rowCount()
        table.insertRow(row)

        enabled_item = QTableWidgetItem(self._YES)
        enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        enabled_item.setFlags(enabled_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, self.COL_ENABLED, enabled_item)

        table.setItem(row, self.COL_PATTERN, QTableWidgetItem(""))
        table.setItem(row, self.COL_REPLACE, QTableWidgetItem(""))

        regex_item = QTableWidgetItem(self._NO)
        regex_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        regex_item.setFlags(regex_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, self.COL_REGEX, regex_item)

        table.setItem(row, self.COL_COMMENT, QTableWidgetItem(""))
        table.blockSignals(False)
        table.selectRow(row)
        table.scrollToItem(table.item(row, self.COL_PATTERN))
        table.editItem(table.item(row, self.COL_PATTERN))
        self._mark_modified()

    def _on_delete_rule(self):
        """删除选中的规则"""
        if self._mode_stack.currentIndex() == 1:
            return
        table = self._current_table()
        row = table.currentRow()
        if row < 0:
            return
        table.removeRow(row)
        self._mark_modified()

    def _on_move_rule(self, direction: int):
        """上移/下移规则"""
        if self._mode_stack.currentIndex() == 1:
            return
        table = self._current_table()
        row = table.currentRow()
        if row < 0:
            return
        target = row + direction
        if target < 0 or target >= table.rowCount():
            return

        table.blockSignals(True)
        for col in range(table.columnCount()):
            item_a = table.takeItem(row, col)
            item_b = table.takeItem(target, col)
            table.setItem(row, col, item_b)
            table.setItem(target, col, item_a)
        table.blockSignals(False)
        table.selectRow(target)
        self._mark_modified()

    def _on_select_all(self):
        """选中表格中所有行（仅限可见行）"""
        if self._mode_stack.currentIndex() == 1:
            return
        table = self._current_table()
        table.blockSignals(True)
        table.clearSelection()
        for r in range(table.rowCount()):
            if not table.isRowHidden(r):
                for c in range(table.columnCount()):
                    item = table.item(r, c)
                    if item:
                        item.setSelected(True)
        table.blockSignals(False)
        self._on_selection_changed()

    def _get_selected_rows(self) -> list:
        """获取当前表格中被选中且未被过滤隐藏的行号列表"""
        table = self._current_table()
        return sorted(set(idx.row() for idx in table.selectedIndexes() if not table.isRowHidden(idx.row())))

    def _on_selection_changed(self):
        """选中行变化时，更新启用/正则按钮的文字"""
        rows = self._get_selected_rows()
        table = self._current_table()

        if not rows:
            self._toggle_enabled_button.setText(self._t("Enable"))
            self._toggle_regex_button.setText(self._t("Regex"))
            return

        # 统计选中行中启用/正则的数量
        enabled_count = sum(
            1 for r in rows
            if table.item(r, self.COL_ENABLED) and table.item(r, self.COL_ENABLED).text() == self._YES
        )
        regex_count = sum(
            1 for r in rows
            if table.item(r, self.COL_REGEX) and table.item(r, self.COL_REGEX).text() == self._YES
        )

        # 多数已启用 → 按钮显示"禁用"，反之显示"启用"
        if enabled_count > len(rows) // 2:
            self._toggle_enabled_button.setText(self._t("Disable"))
        else:
            self._toggle_enabled_button.setText(self._t("Enable"))

        # 多数已正则 → 按钮显示"取消正则"，反之显示"正则"
        if regex_count > len(rows) // 2:
            self._toggle_regex_button.setText(self._t("Cancel Regex"))
        else:
            self._toggle_regex_button.setText(self._t("Regex"))

    def _on_toggle_enabled(self):
        """切换选中行的启用/禁用状态"""
        if self._mode_stack.currentIndex() == 1:
            return
        rows = self._get_selected_rows()
        if not rows:
            return
        table = self._current_table()

        # 根据按钮当前文字决定目标状态
        target = self._YES if self._toggle_enabled_button.text() == self._t("Enable") else self._NO

        table.blockSignals(True)
        for row in rows:
            item = table.item(row, self.COL_ENABLED)
            if item:
                item.setText(target)
                self._set_row_dimmed(table, row, target == self._NO)
        table.blockSignals(False)
        self._mark_modified()
        self._on_selection_changed()

    def _on_toggle_regex(self):
        """切换选中行的正则/字面状态"""
        if self._mode_stack.currentIndex() == 1:
            return
        rows = self._get_selected_rows()
        if not rows:
            return
        table = self._current_table()

        # 根据按钮当前文字决定目标状态
        target = self._YES if self._toggle_regex_button.text() == self._t("Regex") else self._NO

        table.blockSignals(True)
        for row in rows:
            item = table.item(row, self.COL_REGEX)
            if item:
                item.setText(target)
        table.blockSignals(False)
        self._mark_modified()
        self._on_selection_changed()

    def _on_toggle_mode(self):
        """切换表格/原始编辑模式"""
        if self._mode_button.isChecked():
            # 切换到原始模式
            yaml_content = self._tables_to_yaml()
            self._raw_editor.blockSignals(True)
            self._raw_editor.setPlainText(yaml_content)
            self._raw_editor.blockSignals(False)
            self._mode_stack.setCurrentIndex(1)
            self._mode_button.setText(self._t("Table View"))
            self._add_button.setEnabled(False)
            self._delete_button.setEnabled(False)
            self._move_up_button.setEnabled(False)
            self._move_down_button.setEnabled(False)
            self._select_all_button.setEnabled(False)
            self._filter_row.setVisible(False)
        else:
            # 切换回表格模式
            raw_text = self._raw_editor.toPlainText()
            try:
                data = yaml.safe_load(raw_text) or {}
                if not isinstance(data, dict):
                    raise ValueError("YAML root must be a dict")
            except Exception as e:
                QMessageBox.warning(
                    self, self._t("Parse Error"),
                    self._t("YAML syntax error, cannot switch to table view.") + f"\n\n{e}"
                )
                self._mode_button.setChecked(True)
                return

            for group_key, table in self._tables.items():
                table.blockSignals(True)
                table.setRowCount(0)
                rules = data.get(group_key, [])
                if isinstance(rules, list):
                    for rule in rules:
                        if isinstance(rule, dict):
                            self._add_rule_to_table(table, rule)
                table.blockSignals(False)

            self._mode_stack.setCurrentIndex(0)
            self._mode_button.setText(self._t("Raw Edit"))
            self._add_button.setEnabled(True)
            self._delete_button.setEnabled(True)
            self._move_up_button.setEnabled(True)
            self._move_down_button.setEnabled(True)
            self._select_all_button.setEnabled(True)
            self._filter_row.setVisible(True)
            self._apply_filter(self._current_table(), self._search_input.text())

        self._update_status()

    def _on_cell_changed(self, row: int, col: int):
        self._mark_modified()

    def _on_tab_changed(self, index: int):
        self._update_status()
        self._on_selection_changed()
        self._apply_filter(self._current_table(), self._search_input.text())

    def _on_search_changed(self, text: str):
        """搜索框内容变化：过滤当前表格的行"""
        if self._mode_stack.currentIndex() == 1:
            return
        self._apply_filter(self._current_table(), text)

    def _apply_filter(self, table: QTableWidget, query: str):
        """根据 query 过滤 table 的行；query 命中 pattern / replace / comment 即显示"""
        q = (query or '').strip().lower()
        if not q:
            for r in range(table.rowCount()):
                table.setRowHidden(r, False)
            return
        for r in range(table.rowCount()):
            pattern_item = table.item(r, self.COL_PATTERN)
            replace_item = table.item(r, self.COL_REPLACE)
            comment_item = table.item(r, self.COL_COMMENT)
            haystack = ' '.join([
                pattern_item.text() if pattern_item else '',
                replace_item.text() if replace_item else '',
                comment_item.text() if comment_item else '',
            ]).lower()
            table.setRowHidden(r, q not in haystack)

    def _on_raw_changed(self):
        self._mark_modified()

    def _mark_modified(self):
        self._modified = True
        self._save_button.setEnabled(True)
        self._update_status()
        self.data_changed.emit()

    # ─── 保存 ───

    def _on_save(self):
        """保存数据"""
        if self._mode_stack.currentIndex() == 1:
            raw_text = self._raw_editor.toPlainText()
            try:
                yaml.safe_load(raw_text)
            except Exception as e:
                QMessageBox.warning(
                    self, self._t("Save Error"),
                    self._t("YAML syntax error, please fix before saving.") + f"\n\n{e}"
                )
                return
            try:
                os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
                with open(self._file_path, 'w', encoding='utf-8') as f:
                    f.write(raw_text)
                self._modified = False
                self._save_button.setEnabled(False)
                self._set_status(self._t("Saved successfully"), "success")
            except Exception as e:
                self._set_status(f"{self._t('Save error')}: {e}", "error")
        else:
            yaml_content = self._tables_to_yaml()
            try:
                os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
                with open(self._file_path, 'w', encoding='utf-8') as f:
                    f.write(yaml_content)
                self._modified = False
                self._save_button.setEnabled(False)
                self._set_status(self._t("Saved successfully"), "success")
            except Exception as e:
                self._set_status(f"{self._t('Save error')}: {e}", "error")

    def _tables_to_yaml(self) -> str:
        """从表格数据生成 YAML 字符串"""
        data = {}
        for group_key, table in self._tables.items():
            rules = []
            for row in range(table.rowCount()):
                enabled_item = table.item(row, self.COL_ENABLED)
                pattern_item = table.item(row, self.COL_PATTERN)
                replace_item = table.item(row, self.COL_REPLACE)
                regex_item = table.item(row, self.COL_REGEX)
                comment_item = table.item(row, self.COL_COMMENT)

                pattern = pattern_item.text() if pattern_item else ""
                replace = replace_item.text() if replace_item else ""
                is_regex = (regex_item.text() == self._YES) if regex_item else False
                is_enabled = (enabled_item.text() == self._YES) if enabled_item else True
                comment = comment_item.text() if comment_item else ""

                if not pattern:
                    continue

                rule: dict = {'pattern': pattern, 'replace': replace}
                if is_regex:
                    rule['regex'] = True
                if not is_enabled:
                    rule['enabled'] = False
                if comment:
                    rule['comment'] = comment
                rules.append(rule)
            data[group_key] = rules

        return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # ─── 状态 ───

    def _update_status(self):
        group_key = self._current_group_key()
        table = self._tables[group_key]
        total = table.rowCount()
        enabled = sum(
            1 for r in range(total)
            if table.item(r, self.COL_ENABLED) and table.item(r, self.COL_ENABLED).text() == self._YES
        )
        modified_mark = " ●" if self._modified else ""
        mode = self._t("Raw Edit") if self._mode_stack.currentIndex() == 1 else self._t("Table View")
        self._status_label.setText(
            f"{group_key}: {enabled}/{total} {self._t('enabled')}{modified_mark}  [{mode}]"
        )

    def _set_status(self, text: str, kind: str = "info"):
        self._status_label.setText(text)

    # ─── 公共接口 ───

    def refresh(self):
        """刷新数据（重新加载文件）"""
        self._load_data()
        self._apply_filter(self._current_table(), self._search_input.text())

    def register_preset_button(self, label: str, callback: Callable) -> QPushButton:
        """
        预设按钮接口（接口预留 - 将来加"中文""全开""全关"等一键预设时使用）。
        callback 签名应为 () -> None。返回创建的 QPushButton 以便外部进一步定制。
        """
        btn = QPushButton(label)
        btn.setProperty("chipButton", True)
        btn.clicked.connect(callback)
        self._preset_slot_layout.addWidget(btn)
        return btn

    def clear_preset_buttons(self):
        """清空所有预设按钮（接口预留）"""
        while self._preset_slot_layout.count():
            item = self._preset_slot_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def apply_theme(self):
        """应用主题"""
        if hasattr(self, '_highlighter'):
            self._highlighter.rehighlight()

    def refresh_ui_texts(self):
        """刷新UI文本（语言切换）"""
        self._add_button.setText(self._t("Add Rule"))
        self._delete_button.setText(self._t("Delete"))
        self._save_button.setText(self._t("Save"))
        self._select_all_button.setText(self._t("Select All"))
        self._on_selection_changed()  # 刷新启用/正则按钮文字
        if self._mode_button.isChecked():
            self._mode_button.setText(self._t("Table View"))
        else:
            self._mode_button.setText(self._t("Raw Edit"))
        self._tab_widget.setTabText(0, self._t("Common (Always)"))
        self._tab_widget.setTabText(1, self._t("Horizontal"))
        self._tab_widget.setTabText(2, self._t("Vertical"))
        for table in self._tables.values():
            table.setHorizontalHeaderLabels([
                self._t("Enabled"),
                self._t("Pattern"),
                self._t("Replace"),
                self._t("Regex"),
                self._t("Comment"),
            ])
        if hasattr(self, '_raw_hint_label'):
            self._raw_hint_label.setText(
                self._t("Edit raw YAML content directly. Save to apply changes.")
            )
        if hasattr(self, '_search_label'):
            self._search_label.setText(self._t("Filter:"))
        if hasattr(self, '_search_input'):
            self._search_input.setPlaceholderText(self._t("Type to filter by pattern / replace / comment..."))
        self._update_status()
