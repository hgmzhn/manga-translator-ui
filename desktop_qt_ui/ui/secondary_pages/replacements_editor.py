"""
替换规则管理页面 - 可视化编辑 text_replacements.yaml
基于 BaseYamlRuleEditorPanel 抽象基类实现，支持通用/横排/竖排三分组与表格/源码双模式。
"""

from __future__ import annotations

import sys
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QWidget,
)
from qfluentwidgets import (
    PushButton as QPushButton,
    TableWidget as QTableWidget,
)

from ui.secondary_pages.base_rule_editor import (
    BaseYamlRuleEditorPanel,
    YamlHighlighter,
    _fixed_width_font,
)


def _get_replacements_path() -> str:
    """获取 text_replacements.yaml 的路径"""
    from manga_translator.rendering.text_replacements import ensure_text_replacements_exists

    return ensure_text_replacements_exists()


class ReplacementsEditorPanel(BaseYamlRuleEditorPanel):
    """文本替换规则编辑面板"""

    COL_REPLACE = 2

    def _get_file_path(self) -> str:
        return _get_replacements_path()

    def _get_header_labels(self) -> List[str]:
        return [
            self._t("Enabled"),
            self._t("Pattern"),
            self._t("Replace"),
            self._t("Regex"),
            self._t("Comment"),
        ]

    def _get_filter_placeholder(self) -> str:
        return self._t("Type to filter by pattern / replace / comment...")

    def _get_restore_confirm_message(self) -> str:
        return self._t(
            "Restore replacement rules to the built-in defaults? Current custom rules will be overwritten."
        )

    def _do_restore_default(self) -> None:
        from manga_translator.rendering.text_replacements import (
            reset_text_replacements_to_default,
        )

        reset_text_replacements_to_default(self._file_path)

    def _invalidate_cache(self) -> None:
        module = sys.modules.get("manga_translator.rendering.text_replacements")
        if module and hasattr(module, "invalidate_replacements_cache"):
            try:
                module.invalidate_replacements_cache(self._file_path)
            except Exception:
                pass

    def _create_rule_template(self) -> dict:
        return {
            "enabled": True,
            "pattern": "",
            "replace": "",
            "regex": False,
            "comment": "",
        }

    def _init_extra_filter_widgets(self, filter_layout: QHBoxLayout) -> None:
        self._preset_slot = QWidget(filter_layout.parentWidget())
        self._preset_slot_layout = QHBoxLayout(self._preset_slot)
        self._preset_slot_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_slot_layout.setSpacing(6)
        filter_layout.addWidget(self._preset_slot)

    def _populate_row(self, table: QTableWidget, row: int, rule: dict) -> None:
        pattern = str(rule.get("pattern", ""))
        replace = str(rule.get("replace", ""))
        is_regex = bool(rule.get("regex", False))
        is_enabled = bool(rule.get("enabled", True))
        comment = str(rule.get("comment", ""))

        enabled_item = QTableWidgetItem(self._YES if is_enabled else self._NO)
        enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        enabled_item.setFlags(enabled_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, self.COL_ENABLED, enabled_item)

        table.setItem(row, self.COL_PATTERN, QTableWidgetItem(pattern))
        table.setItem(row, self.COL_REPLACE, QTableWidgetItem(replace))

        regex_item = QTableWidgetItem(self._YES if is_regex else self._NO)
        regex_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        regex_item.setFlags(regex_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, self.COL_REGEX, regex_item)

        table.setItem(row, self.COL_COMMENT, QTableWidgetItem(comment))
        if not is_enabled:
            self._set_row_dimmed(table, row, True)

    def _extract_row_data(self, table: QTableWidget, row: int) -> Optional[dict]:
        pattern_item = table.item(row, self.COL_PATTERN)
        replace_item = table.item(row, self.COL_REPLACE)
        regex_item = table.item(row, self.COL_REGEX)
        enabled_item = table.item(row, self.COL_ENABLED)
        comment_item = table.item(row, self.COL_COMMENT)

        pattern = pattern_item.text() if pattern_item else ""
        replace = replace_item.text() if replace_item else ""
        is_regex = (regex_item.text() == self._YES) if regex_item else False
        is_enabled = (enabled_item.text() == self._YES) if enabled_item else True
        comment = comment_item.text() if comment_item else ""

        rule: dict = {"pattern": pattern, "replace": replace}
        if is_regex:
            rule["regex"] = True
        if not is_enabled:
            rule["enabled"] = False
        if comment:
            rule["comment"] = comment
        return rule

    def _get_row_filter_haystack(self, table: QTableWidget, row: int) -> str:
        pattern = table.item(row, self.COL_PATTERN)
        replace = table.item(row, self.COL_REPLACE)
        comment = table.item(row, self.COL_COMMENT)
        return " ".join([
            pattern.text() if pattern else "",
            replace.text() if replace else "",
            comment.text() if comment else "",
        ]).lower()

    def _set_row_dimmed(self, table: QTableWidget, row: int, dimmed: bool) -> None:
        del dimmed
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item:
                item.setForeground(QTableWidgetItem().foreground())

    # ─── 预设按钮扩展接口 ───

    def register_preset_button(self, label: str, callback: Callable) -> QPushButton:
        """预设按钮接口（将来加'中文'、'全开'、'全关'等一键预设时使用）"""
        btn = QPushButton(label)
        btn.clicked.connect(callback)
        self._preset_slot_layout.addWidget(btn)
        return btn

    def clear_preset_buttons(self) -> None:
        """清空所有预设按钮"""
        while self._preset_slot_layout.count():
            item = self._preset_slot_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
