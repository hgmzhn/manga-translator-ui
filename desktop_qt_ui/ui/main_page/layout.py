import os
import shutil

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush
from PyQt6.QtWidgets import (
    QFileDialog,
    QListWidgetItem,
    QMessageBox,
)
from qfluentwidgets import ListWidget as QListWidget
from qfluentwidgets import themeColor

from theme_registry import THEME_OPTIONS
from utils.resource_helper import resource_path

_CURRENT_ASSET_PREFIX = "* "
_PROMPT_EXTENSIONS = (".yaml", ".yml", ".json", ".txt")


def _set_prompt_status(self, translation_key: str, **kwargs):
    if hasattr(self, "prompt_status_label"):
        self.prompt_status_label.setText(self._t(translation_key, **kwargs))


def _normalize_asset_filename(path_or_name: str | None) -> str:
    if not path_or_name:
        return ""
    return os.path.basename(str(path_or_name).replace("\\", "/").rstrip("/"))


def _get_asset_item_filename(item: QListWidgetItem | None) -> str:
    if not item:
        return ""
    raw_value = item.data(Qt.ItemDataRole.UserRole)
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value.strip()
    text = item.text().strip()
    if text.startswith(_CURRENT_ASSET_PREFIX):
        return text[len(_CURRENT_ASSET_PREFIX):].strip()
    return text


def _find_asset_item(list_widget: QListWidget, filename: str) -> QListWidgetItem | None:
    if not filename:
        return None
    for index in range(list_widget.count()):
        item = list_widget.item(index)
        if _get_asset_item_filename(item) == filename:
            return item
    return None


def _create_asset_list_item(self, filename: str, *, is_current: bool, tooltip_text: str | None = None) -> QListWidgetItem:
    item = QListWidgetItem(filename)
    item.setData(Qt.ItemDataRole.UserRole, filename)
    if is_current:
        item.setText(f"{_CURRENT_ASSET_PREFIX}{filename}")
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        accent_color = themeColor().toRgb()
        if accent_color.isValid():
            item.setForeground(QBrush(accent_color))
        if tooltip_text:
            item.setToolTip(tooltip_text)
    return item


def _sanitize_file_stem(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("_", "-", ".", " ")).strip()


def _normalize_prompt_filename(name: str, default_extension: str = ".yaml") -> str:
    safe_name = _sanitize_file_stem(name)
    if not safe_name:
        return ""

    stem, ext = os.path.splitext(safe_name)
    if ext and ext.lower() not in _PROMPT_EXTENSIONS:
        safe_name = stem
        stem, ext = os.path.splitext(safe_name)

    if ext.lower() not in _PROMPT_EXTENSIONS:
        safe_name = f"{safe_name}{default_extension}"

    final_stem = os.path.splitext(safe_name)[0].strip()
    if not final_stem:
        return ""
    return safe_name


def populate_theme_combo(self):
    if not hasattr(self, "theme_combo"):
        return
    config = self.config_service.get_config()
    theme_options = [(theme_key, self._t(theme_label)) for theme_key, theme_label in THEME_OPTIONS]
    self.theme_combo.blockSignals(True)
    self.theme_combo.clear()
    selected_index = 0
    for idx, (theme_key, theme_label) in enumerate(theme_options):
        self.theme_combo.addItem(theme_label, theme_key)
        if config.app.theme == theme_key:
            selected_index = idx
    self.theme_combo.setCurrentIndex(selected_index)
    self.theme_combo.blockSignals(False)


def populate_language_combo(self):
    if not hasattr(self, "language_combo"):
        return
    current_language = self.config_service.get_config().app.ui_language
    self.language_combo.blockSignals(True)
    self.language_combo.clear()
    if self.i18n:
        available_locales = self.i18n.get_available_locales()
        selected_index = 0
        for idx, (locale_code, locale_info) in enumerate(available_locales.items()):
            self.language_combo.addItem(locale_info.name, locale_code)
            if current_language == locale_code:
                selected_index = idx
        if self.language_combo.count() > 0:
            self.language_combo.setCurrentIndex(selected_index)
    self.language_combo.blockSignals(False)


def on_theme_combo_changed(self, index: int):
    if index < 0 or not hasattr(self, "theme_combo"):
        return
    theme_key = self.theme_combo.itemData(index)
    if theme_key:
        self.theme_change_requested.emit(theme_key)


def on_language_combo_changed(self, index: int):
    if index < 0 or not hasattr(self, "language_combo"):
        return
    locale_code = self.language_combo.itemData(index)
    if locale_code:
        drop_menu = getattr(self.language_combo, "dropMenu", None)
        if drop_menu is not None:
            self.language_combo.hidePopup()
        self._pending_language_change_locale = locale_code
        if getattr(self, "_language_change_emit_scheduled", False):
            return
        self._language_change_emit_scheduled = True
        QTimer.singleShot(0, lambda view=self: _emit_pending_language_change(view))


def _emit_pending_language_change(self):
    locale_code = getattr(self, "_pending_language_change_locale", None)
    self._pending_language_change_locale = None
    self._language_change_emit_scheduled = False
    if locale_code:
        self.language_change_requested.emit(locale_code)


def refresh_prompt_manager(self):
    if not hasattr(self, "prompt_list_widget"):
        return
    prompt_files = self.controller.get_hq_prompt_options()
    selected_prompt_path = self.config_service.get_config().translator.high_quality_prompt_path
    selected_filename = _normalize_asset_filename(selected_prompt_path)
    current_item = self.prompt_list_widget.currentItem()
    current_filename = _get_asset_item_filename(current_item)
    preferred_filename = current_filename or selected_filename
    signature = (tuple(prompt_files), selected_filename)

    if (
        getattr(self, "_prompt_manager_signature", None) == signature
        and self.prompt_list_widget.count() == len(prompt_files)
    ):
        _set_prompt_status(self, "Found {count} prompt files.", count=len(prompt_files))
        return

    self.prompt_list_widget.blockSignals(True)
    self.prompt_list_widget.clear()
    for prompt in prompt_files:
        item = _create_asset_list_item(
            self,
            prompt,
            is_current=(prompt == selected_filename),
            tooltip_text=self._t("Current prompt: {filename}", filename=prompt),
        )
        self.prompt_list_widget.addItem(item)
    self.prompt_list_widget.blockSignals(False)

    if preferred_filename:
        matching_item = _find_asset_item(self.prompt_list_widget, preferred_filename)
        if matching_item:
            self.prompt_list_widget.setCurrentItem(matching_item)
    else:
        self.prompt_list_widget.clearSelection()

    self._prompt_manager_signature = signature
    if not self.prompt_list_widget.currentItem() and hasattr(self, "prompt_preview_panel"):
        self.prompt_preview_panel.clear()
    _set_prompt_status(self, "Found {count} prompt files.", count=len(prompt_files))


def apply_selected_prompt(self):
    current_item = self.prompt_list_widget.currentItem() if hasattr(self, "prompt_list_widget") else None
    if not current_item:
        return
    filename = _get_asset_item_filename(current_item)
    if not filename:
        return
    selected_path = os.path.join("dict", filename).replace("\\", "/")
    self.setting_changed.emit("translator.high_quality_prompt_path", selected_path)
    self._refresh_prompt_manager()
    _set_prompt_status(self, "Current prompt: {filename}", filename=filename)


def on_prompt_selection_changed(self, current, previous):
    """Prompt 列表选中变化时加载预览。"""
    if not hasattr(self, "prompt_preview_panel"):
        return
    if not current:
        self.prompt_preview_panel.clear()
        return
    filename = _get_asset_item_filename(current)
    if not filename:
        self.prompt_preview_panel.clear()
        return
    dict_dir = resource_path("dict")
    file_path = os.path.join(dict_dir, filename)
    self.prompt_preview_panel.load_file(file_path)


def open_prompt_editor(self, file_path: str):
    """弹出编辑器对话框，关闭后刷新预览。"""
    from ui.secondary_pages.ai_colorizer_prompt_editor import (
        AIColorizerPromptEditorDialog,
        is_ai_colorizer_prompt_file,
    )

    if is_ai_colorizer_prompt_file(file_path):
        dlg = AIColorizerPromptEditorDialog(file_path, t_func=self._t, parent=self)
    else:
        from ui.secondary_pages.prompt_preview import PromptEditorDialog

        dlg = PromptEditorDialog(file_path, t_func=self._t, parent=self)
    dlg.exec()
    # 编辑器关闭后刷新预览
    if dlg.get_was_modified() and hasattr(self, "prompt_preview_panel"):
        self.prompt_preview_panel.load_file(file_path)


def _get_selected_prompt_filename(self) -> str | None:
    current = self.prompt_list_widget.currentItem() if hasattr(self, "prompt_list_widget") else None
    if not current:
        return None
    filename = _get_asset_item_filename(current)
    return filename or None


def _select_prompt_item(self, filename: str):
    if not filename or not hasattr(self, "prompt_list_widget"):
        return
    item = _find_asset_item(self.prompt_list_widget, filename)
    if item:
        self.prompt_list_widget.setCurrentItem(item)


def _prompt_file_path(filename: str) -> str:
    return os.path.join(resource_path("dict"), filename)


def create_new_prompt(self):
    """弹出输入框，创建新的 YAML 提示词文件。"""
    from ui.secondary_pages.themed_text_input_dialog import themed_get_text
    name, ok = themed_get_text(
        self,
        title=self._t("New Prompt"),
        label=self._t("Enter prompt file name (without extension):"),
        ok_text=self._t("OK"),
        cancel_text=self._t("Cancel"),
    )
    if not ok or not name.strip():
        return
    filename = _normalize_prompt_filename(name.strip(), ".yaml")
    if not filename:
        QMessageBox.warning(self, self._t("Warning"), self._t("Invalid file name."))
        return
    dict_dir = resource_path("dict")
    os.makedirs(dict_dir, exist_ok=True)
    file_path = os.path.join(dict_dir, filename)

    if os.path.exists(file_path):
        QMessageBox.warning(self, self._t("Warning"), self._t("File already exists") + f": {filename}")
        return

    # 默认 YAML 模板
    default_content = (
        '# 自定义翻译提示词模板\n'
        '# Custom translation prompt template\n'
        '#\n'
        '# 使用方法：\n'
        '#   1. 复制此文件并重命名（例如 my_manga_prompt.yaml）\n'
        '#   2. 编辑下面的 system_prompt 和 glossary 部分\n'
        '#   3. 在翻译设置中选择此文件\n'
        '#\n'
        '# 提示词中可以使用 {{{target_lang}}} 占位符，会被替换为目标语言名称\n'
        '\n'
        '# 自定义系统提示词（留空则仅使用内置的基础提示词，此处内容会叠加在基础提示词之前）\n'
        'system_prompt: ""\n'
        '\n'
        '# 术语表（确保角色名、地名等翻译一致）\n'
        'glossary:\n'
        '  Person:\n'
        '    - original: ""\n'
        '      translation: ""\n'
        '  Location: []\n'
        '  Org: []\n'
        '  Item: []\n'
        '  Skill: []\n'
        '  Creature: []\n'
    )

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(default_content)
    except Exception as e:
        QMessageBox.critical(self, self._t("Error"), str(e))
        return

    self._refresh_prompt_manager()
    _select_prompt_item(self, filename)
    _set_prompt_status(self, "Created: {filename}", filename=filename)


def copy_selected_prompt(self):
    """复制选中的提示词文件。"""
    from ui.secondary_pages.themed_text_input_dialog import themed_get_text

    filename = _get_selected_prompt_filename(self)
    if not filename:
        QMessageBox.warning(self, self._t("Warning"), self._t("Please select a prompt file first."))
        return

    source_path = _prompt_file_path(filename)
    if not os.path.isfile(source_path):
        QMessageBox.warning(self, self._t("Warning"), self._t("Selected prompt file does not exist."))
        return

    stem, ext = os.path.splitext(filename)
    default_name = f"{stem}_copy"
    new_name, ok = themed_get_text(
        self,
        title=self._t("Copy Prompt"),
        label=self._t("Enter new prompt file name (without extension):"),
        text=default_name,
        ok_text=self._t("OK"),
        cancel_text=self._t("Cancel"),
    )
    if not ok or not new_name.strip():
        return

    target_filename = _normalize_prompt_filename(new_name.strip(), ext or ".yaml")
    if not target_filename:
        QMessageBox.warning(self, self._t("Warning"), self._t("Invalid file name."))
        return

    target_path = _prompt_file_path(target_filename)
    if os.path.exists(target_path):
        QMessageBox.warning(self, self._t("Warning"), self._t("File already exists") + f": {target_filename}")
        return

    try:
        shutil.copy2(source_path, target_path)
    except Exception as e:
        QMessageBox.critical(self, self._t("Error"), str(e))
        return

    self._refresh_prompt_manager()
    _select_prompt_item(self, target_filename)
    _set_prompt_status(self, "Copied: {filename}", filename=target_filename)


def rename_selected_prompt(self):
    """重命名选中的提示词文件。"""
    from ui.secondary_pages.themed_text_input_dialog import themed_get_text

    filename = _get_selected_prompt_filename(self)
    if not filename:
        QMessageBox.warning(self, self._t("Warning"), self._t("Please select a prompt file first."))
        return

    source_path = _prompt_file_path(filename)
    if not os.path.isfile(source_path):
        QMessageBox.warning(self, self._t("Warning"), self._t("Selected prompt file does not exist."))
        return

    stem, ext = os.path.splitext(filename)
    new_name, ok = themed_get_text(
        self,
        title=self._t("Rename Prompt"),
        label=self._t("Enter new prompt file name (without extension):"),
        text=stem,
        ok_text=self._t("OK"),
        cancel_text=self._t("Cancel"),
    )
    if not ok or not new_name.strip():
        return

    target_filename = _normalize_prompt_filename(new_name.strip(), ext or ".yaml")
    if not target_filename:
        QMessageBox.warning(self, self._t("Warning"), self._t("Invalid file name."))
        return
    if target_filename == filename:
        return

    target_path = _prompt_file_path(target_filename)
    if os.path.exists(target_path):
        QMessageBox.warning(self, self._t("Warning"), self._t("File already exists") + f": {target_filename}")
        return

    try:
        os.replace(source_path, target_path)
    except Exception as e:
        QMessageBox.critical(self, self._t("Error"), str(e))
        return

    current_prompt_path = self.config_service.get_config().translator.high_quality_prompt_path or ""
    if os.path.basename(current_prompt_path) == filename:
        self.setting_changed.emit(
            "translator.high_quality_prompt_path",
            os.path.join("dict", target_filename).replace("\\", "/"),
        )

    self._refresh_prompt_manager()
    _select_prompt_item(self, target_filename)
    _set_prompt_status(self, "Renamed to: {filename}", filename=target_filename)


def delete_selected_prompt(self):
    """删除选中的提示词文件。"""
    filename = _get_selected_prompt_filename(self)
    if not filename:
        QMessageBox.warning(self, self._t("Warning"), self._t("Please select a prompt file first."))
        return

    current_prompt_path = self.config_service.get_config().translator.high_quality_prompt_path or ""
    was_active_prompt = os.path.basename(current_prompt_path) == filename

    reply = QMessageBox.question(
        self, self._t("Confirm Delete"),
        self._t("Are you sure you want to delete this prompt file?") + f"\n\n{filename}",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    dict_dir = resource_path("dict")
    file_path = os.path.join(dict_dir, filename)
    try:
        if not os.path.exists(file_path):
            QMessageBox.warning(self, self._t("Warning"), self._t("Selected prompt file does not exist."))
            return
        os.remove(file_path)
    except Exception as e:
        QMessageBox.critical(self, self._t("Error"), str(e))
        return

    if was_active_prompt:
        self.setting_changed.emit("translator.high_quality_prompt_path", None)

    if hasattr(self, "prompt_preview_panel"):
        self.prompt_preview_panel.clear()
    self._refresh_prompt_manager()
    _set_prompt_status(self, "Deleted: {filename}", filename=filename)
