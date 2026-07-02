import json
import os

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CardWidget, LineEdit as FluentLineEdit, PushButton as QPushButton
from utils.resource_helper import resource_path
from ui.widgets.wheel_filter import NoWheelComboBox as QComboBox
from ui.widgets.hover_hint import set_hover_hint
from ui.widgets.toggle_switch import ToggleSwitch


class QLineEdit(FluentLineEdit):
    """Fluent LineEdit with the PyQt constructor forms used by existing settings code."""

    def __init__(self, text: str | QWidget | None = "", parent: QWidget | None = None):
        if isinstance(text, QWidget) and parent is None:
            parent = text
            text = ""
        super().__init__(parent)
        if text:
            self.setText(str(text))


API_GROUP_SPECS = {
    "translator_openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_API_BASE"),
    "translator_gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_API_BASE"),
    "ocr_openai": ("OCR_OPENAI_API_KEY", "OCR_OPENAI_MODEL", "OCR_OPENAI_API_BASE"),
    "ocr_gemini": ("OCR_GEMINI_API_KEY", "OCR_GEMINI_MODEL", "OCR_GEMINI_API_BASE"),
    "color_openai": ("COLOR_OPENAI_API_KEY", "COLOR_OPENAI_MODEL", "COLOR_OPENAI_API_BASE"),
    "color_gemini": ("COLOR_GEMINI_API_KEY", "COLOR_GEMINI_MODEL", "COLOR_GEMINI_API_BASE"),
    "render_openai": ("RENDER_OPENAI_API_KEY", "RENDER_OPENAI_MODEL", "RENDER_OPENAI_API_BASE"),
    "render_gemini": ("RENDER_GEMINI_API_KEY", "RENDER_GEMINI_MODEL", "RENDER_GEMINI_API_BASE"),
}

SIMPLE_API_GROUP_SPECS = {
    "translator_sakura": ("SAKURA_API_BASE", "SAKURA_DICT_PATH"),
}


def _normalize_selected_value(value) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _selected_api_group_keys(config) -> dict[str, list[str]]:
    translator_value = _normalize_selected_value(getattr(config.translator, "translator", ""))
    ocr_value = _normalize_selected_value(getattr(config.ocr, "ocr", ""))
    secondary_ocr_value = _normalize_selected_value(getattr(config.ocr, "secondary_ocr", ""))
    colorizer_value = _normalize_selected_value(getattr(config.colorizer, "colorizer", ""))
    renderer_value = _normalize_selected_value(getattr(config.render, "renderer", ""))

    result = {
        "translation": [],
        "ocr": [],
        "color": [],
        "render": [],
    }

    if translator_value in {"openai", "openai_hq"}:
        result["translation"].append("translator_openai")
    elif translator_value in {"gemini", "gemini_hq"}:
        result["translation"].append("translator_gemini")
    elif translator_value == "sakura":
        result["translation"].append("translator_sakura")

    selected_ocr_values = [ocr_value]
    if bool(getattr(config.ocr, "use_hybrid_ocr", False)):
        selected_ocr_values.append(secondary_ocr_value)
    if "openai_ocr" in selected_ocr_values:
        result["ocr"].append("ocr_openai")
    if "gemini_ocr" in selected_ocr_values:
        result["ocr"].append("ocr_gemini")

    if colorizer_value == "openai_colorizer":
        result["color"].append("color_openai")
    elif colorizer_value == "gemini_colorizer":
        result["color"].append("color_gemini")

    if renderer_value == "openai_renderer":
        result["render"].append("render_openai")
    elif renderer_value == "gemini_renderer":
        result["render"].append("render_gemini")

    return result


def _add_empty_api_hint(self, layout, row: int, translation_key: str) -> int:
    notice = CardWidget()
    notice.setMinimumHeight(120)
    notice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    notice_layout = QHBoxLayout(notice)
    notice_layout.setContentsMargins(12, 12, 12, 12)
    notice_layout.setSpacing(0)
    notice_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    text = BodyLabel(self._t(translation_key))
    text.setAlignment(Qt.AlignmentFlag.AlignCenter)
    text.setWordWrap(True)
    text.setMinimumHeight(56)
    text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    notice_layout.addWidget(text, 1)
    layout.addWidget(notice, row, 0, 1, 3)
    layout.setRowStretch(row, 1)
    return row + 1


def _add_api_section_panel(
    self,
    container_layout,
    section_key: str,
    group_keys: list[str],
    current_env_values: dict,
    empty_hint_key: str,
):
    env_input_widget = QWidget()
    if not group_keys:
        env_input_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    self.env_layout = QGridLayout(env_input_widget)
    self.env_layout.setColumnStretch(1, 1)
    self.env_layout.setColumnStretch(2, 0)
    self.env_layout.setHorizontalSpacing(12)
    self.env_layout.setVerticalSpacing(10)
    self.env_layout.setContentsMargins(0, 0, 0, 0)

    self._create_api_feature_selector_row(section_key)

    if group_keys:
        for group_key in group_keys:
            if group_key in API_GROUP_SPECS:
                api_key_env, model_env, api_base_env = API_GROUP_SPECS[group_key]
                self._create_api_rotation_widgets(
                    api_key_env=api_key_env,
                    model_env=model_env,
                    api_base_env=api_base_env,
                    current_values=current_env_values,
                )
            elif group_key in SIMPLE_API_GROUP_SPECS:
                self._create_env_widgets(
                    list(SIMPLE_API_GROUP_SPECS[group_key]),
                    current_env_values,
                )
    else:
        _add_empty_api_hint(self, self.env_layout, self.env_layout.rowCount(), empty_hint_key)

    container_layout.addWidget(env_input_widget, 1 if not group_keys else 0)


def _clear_layout_widgets(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()


def _refresh_env_api_groups(self):
    if not all(
        hasattr(self, attr)
        for attr in (
            "env_group_container_layout",
            "ocr_container_layout",
            "color_container_layout",
            "render_container_layout",
        )
    ):
        return

    self.env_widgets.clear()
    for layout in [
        self.env_group_container_layout,
        self.ocr_container_layout,
        self.color_container_layout,
        self.render_container_layout,
    ]:
        _clear_layout_widgets(layout)

    active_api_groups = _selected_api_group_keys(self.controller.config_service.get_config())
    current_env_values = self.controller.config_service.load_env_vars()

    _add_api_section_panel(
        self,
        self.env_group_container_layout,
        "translation",
        active_api_groups["translation"],
        current_env_values,
        "No translation API required",
    )
    self.env_group_container_layout.addStretch()

    _add_api_section_panel(
        self,
        self.ocr_container_layout,
        "ocr",
        active_api_groups["ocr"],
        current_env_values,
        "No OCR API required",
    )
    self.ocr_container_layout.addStretch()

    _add_api_section_panel(
        self,
        self.color_container_layout,
        "color",
        active_api_groups["color"],
        current_env_values,
        "No colorization API required",
    )
    self.color_container_layout.addStretch()

    _add_api_section_panel(
        self,
        self.render_container_layout,
        "render",
        active_api_groups["render"],
        current_env_values,
        "No render API required",
    )
    self.render_container_layout.addStretch()


def _get_setting_description(view, full_key: str) -> str:
    """通过 i18n 获取设置项描述，key 格式为 desc_{full_key} (. 替换为 _)"""
    desc_key = "desc_" + full_key.replace(".", "_")
    if hasattr(view, '_t'):
        result = view._t(desc_key)
        # 如果翻译结果等于 key 本身，说明没有对应翻译
        if result != desc_key:
            return result
    return ""



def _open_filter_list(self):
    """打开过滤列表编辑器"""
    from manga_translator.utils.text_filter import ensure_filter_list_exists

    filter_path = ensure_filter_list_exists()
    from ui.secondary_pages.filter_list_editor import FilterListEditorDialog

    dialog = FilterListEditorDialog(filter_path, t_func=self._t, parent=self)
    dialog.exec()


def _open_ai_ocr_prompt_editor(self):
    _open_fixed_prompt_editor(self, "ocr.ai_ocr_prompt_path")


def _open_ai_colorizer_prompt_editor(self):
    _open_fixed_prompt_editor(self, "colorizer.ai_colorizer_prompt_path")


def _open_ai_renderer_prompt_editor(self):
    _open_fixed_prompt_editor(self, "render.ai_renderer_prompt_path")


def _get_fixed_prompt_editor_spec(self, full_key: str):
    if full_key == "ocr.ai_ocr_prompt_path":
        from manga_translator.ocr.prompt_loader import (
            DEFAULT_AI_OCR_PROMPT,
            DEFAULT_AI_OCR_PROMPT_PATH,
            ensure_ai_ocr_prompt_file,
            load_ai_ocr_prompt_file,
            save_ai_ocr_prompt_file,
        )

        return {
            "label": self._t("label_ai_ocr_prompt_path"),
            "description": self._t("desc_ocr_ai_ocr_prompt_path"),
            "section": self._t("label_ai_ocr_prompt_path"),
            "hint": DEFAULT_AI_OCR_PROMPT_PATH,
            "default_prompt": DEFAULT_AI_OCR_PROMPT,
            "ensure_func": ensure_ai_ocr_prompt_file,
            "load_func": load_ai_ocr_prompt_file,
            "save_func": save_ai_ocr_prompt_file,
        }

    if full_key == "colorizer.ai_colorizer_prompt_path":
        from manga_translator.colorization.prompt_loader import (
            DEFAULT_AI_COLORIZER_PROMPT,
            DEFAULT_AI_COLORIZER_PROMPT_PATH,
            ensure_ai_colorizer_prompt_file,
            load_ai_colorizer_prompt_file,
            save_ai_colorizer_prompt_file,
        )

        return {
            "label": self._t("label_ai_colorizer_prompt_path"),
            "description": self._t("desc_colorizer_ai_colorizer_prompt_path"),
            "section": self._t("label_ai_colorizer_prompt_path"),
            "hint": DEFAULT_AI_COLORIZER_PROMPT_PATH,
            "default_prompt": DEFAULT_AI_COLORIZER_PROMPT,
            "ensure_func": ensure_ai_colorizer_prompt_file,
            "load_func": load_ai_colorizer_prompt_file,
            "save_func": save_ai_colorizer_prompt_file,
        }

    if full_key == "render.ai_renderer_prompt_path":
        from manga_translator.rendering.prompt_loader import (
            DEFAULT_AI_RENDERER_PROMPT,
            DEFAULT_AI_RENDERER_PROMPT_PATH,
            ensure_ai_renderer_prompt_file,
            load_ai_renderer_prompt_file,
            save_ai_renderer_prompt_file,
        )

        return {
            "label": self._t("label_ai_renderer_prompt_path"),
            "description": self._t("desc_render_ai_renderer_prompt_path"),
            "section": self._t("label_ai_renderer_prompt_path"),
            "hint": DEFAULT_AI_RENDERER_PROMPT_PATH,
            "default_prompt": DEFAULT_AI_RENDERER_PROMPT,
            "ensure_func": ensure_ai_renderer_prompt_file,
            "load_func": load_ai_renderer_prompt_file,
            "save_func": save_ai_renderer_prompt_file,
        }

    return None


def _open_fixed_prompt_editor(self, full_key: str):
    spec = _get_fixed_prompt_editor_spec(self, full_key)
    if not spec:
        return

    abs_path = spec["ensure_func"](spec["hint"])
    if full_key == "colorizer.ai_colorizer_prompt_path":
        from ui.secondary_pages.ai_colorizer_prompt_editor import (
            AIColorizerPromptEditorDialog,
        )

        dialog = AIColorizerPromptEditorDialog(abs_path, t_func=self._t, parent=self)
        dialog.exec()
        return

    from ui.secondary_pages.simple_prompt_editor_dialog import SimplePromptEditorDialog
    dialog = SimplePromptEditorDialog(
        abs_path,
        title_text=spec["label"],
        description_text=spec["description"],
        section_text=spec["section"],
        hint_text=spec["hint"],
        default_prompt_text=spec["default_prompt"],
        ensure_prompt_func=spec["ensure_func"],
        load_prompt_func=spec["load_func"],
        save_prompt_func=spec["save_func"],
        t_func=self._t,
        parent=self,
    )
    dialog.exec()


def _create_fixed_prompt_editor_row(self, parent_layout, full_key: str):
    spec = _get_fixed_prompt_editor_spec(self, full_key)
    if not spec:
        return False

    label_text = spec["label"]
    label = QLabel(f"{label_text}:")
    label.setMinimumWidth(120)

    container = QWidget()
    hbox = QHBoxLayout(container)
    hbox.setContentsMargins(0, 0, 0, 0)

    edit_button = QPushButton(self._t("Edit"))
    edit_button.setFixedWidth(120)
    if full_key == "ocr.ai_ocr_prompt_path":
        edit_button.clicked.connect(self._open_ai_ocr_prompt_editor)
    elif full_key == "colorizer.ai_colorizer_prompt_path":
        edit_button.clicked.connect(self._open_ai_colorizer_prompt_editor)
    elif full_key == "render.ai_renderer_prompt_path":
        edit_button.clicked.connect(self._open_ai_renderer_prompt_editor)

    hbox.addWidget(edit_button)
    hbox.addStretch(1)

    row = _ClickableRow(self, full_key, label, container)
    parent_layout.addRow(row)
    return True

@pyqtSlot(dict)
def set_parameters(self, config: dict):
    """
    Receives a config dictionary and starts the incremental creation of setting widgets.
    """
    try:
        config_signature = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        config_signature = None

    if (
        config_signature is not None
        and getattr(self, "_settings_ui_ready", False)
        and getattr(self, "_settings_rendered_signature", None) == config_signature
    ):
        return

    self._settings_ui_ready = False
    self._settings_pending_signature = config_signature

    # Clear existing widgets immediately
    for panel in self.tab_frames.values():
        layout = panel.layout()
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    if getattr(self, "_settings_tabs_use_reclassify", False):
        _populate_settings_by_reclassify_layout(self, config)
        self._finalize_settings_ui()
        return

    # Store config and sections to process
    self._config_to_process = config
    self._sections_to_process = [
        "translator", "cli", "detector", "inpainter",
        "render", "upscale", "colorizer", "ocr", "app", "global"
    ]

    # Schedule the first chunk of work
    QTimer.singleShot(0, self._process_next_setting_chunk)


def _resolve_config_value(config: dict, full_key: str):
    parts = str(full_key or "").split(".")
    current = config
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _add_settings_divider(self, parent_layout, title: str, is_sub: bool = False):
    row = QWidget()
    row_layout = QHBoxLayout(row)

    if is_sub:
        row_layout.setContentsMargins(24, 10, 0, 4)
        row_layout.setSpacing(8)

        dot_label = QLabel("◆")
        dot_label.setFixedWidth(14)

        title_label = QLabel(title)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)

        row_layout.addWidget(dot_label)
        row_layout.addWidget(title_label)
        row_layout.addWidget(line, 1)
    else:
        row_layout.setContentsMargins(0, 18, 0, 6)
        row_layout.setSpacing(10)

        title_label = QLabel(title.upper())

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)

        row_layout.addWidget(title_label)
        row_layout.addWidget(line, 1)

    parent_layout.addRow(row)


def _create_widget_from_full_key(self, config: dict, full_key: str, parent_layout):
    if full_key in {
        "ocr.ai_ocr_prompt_path",
        "colorizer.ai_colorizer_prompt_path",
        "render.ai_renderer_prompt_path",
    }:
        return _create_fixed_prompt_editor_row(self, parent_layout, full_key)

    exists, value = _resolve_config_value(config, full_key)
    if not exists:
        return False

    before = parent_layout.rowCount() if isinstance(parent_layout, QFormLayout) else -1
    if "." in full_key:
        section, key = full_key.split(".", 1)
        self._create_param_widgets({key: value}, parent_layout, section)
    else:
        self._create_param_widgets({full_key: value}, parent_layout, "")
    if isinstance(parent_layout, QFormLayout):
        return parent_layout.rowCount() > before
    return True


def _populate_settings_by_reclassify_layout(self, config: dict):
    rendered_rows = 0
    for tab in getattr(self, "settings_tab_layout", []) or []:
        tab_id = str(tab.get("id", "")).strip()
        panel = self.tab_frames.get(tab_id)
        if not panel:
            continue

        panel_layout = panel.layout()
        if panel_layout is None or not isinstance(panel_layout, QFormLayout):
            continue

        has_primary_divider = False
        for item in tab.get("items", []):
            if isinstance(item, dict) and str(item.get("kind", "")).lower() == "divider":
                title_key = str(item.get("title", "")).strip() or "Group"
                title = self._t(title_key)
                is_sub = title_key == "Advanced" and has_primary_divider
                _add_settings_divider(self, panel_layout, title, is_sub=is_sub)
                if not is_sub:
                    has_primary_divider = True
                continue

            full_key = str(item or "").strip()
            if not full_key:
                continue
            if _create_widget_from_full_key(self, config, full_key, panel_layout):
                rendered_rows += 1
    return rendered_rows


def _process_next_setting_chunk(self):
    """
    Processes one section of the settings UI and schedules the next one.
    """
    if not self._sections_to_process:
        self._finalize_settings_ui()
        return

    section = self._sections_to_process.pop(0)
    config = self._config_to_process

    # 使用固定的英文键名
    panel_map = {
        "translator": self.tab_frames.get("Basic Settings"),
        "cli": self.tab_frames.get("Basic Settings"),
        "detector": self.tab_frames.get("Advanced Settings"),
        "inpainter": self.tab_frames.get("Advanced Settings"),
        "render": self.tab_frames.get("Advanced Settings"),
        "upscale": self.tab_frames.get("Advanced Settings"),
        "colorizer": self.tab_frames.get("Advanced Settings"),
        "ocr": self.tab_frames.get("Options"),
        "app": self.tab_frames.get("Application Settings"),
        "global": self.tab_frames.get("Options"),
    }

    panel = panel_map.get(section)
    if section == "global":
        # 处理顶层的全局参数
        global_params = {k: v for k, v in config.items() if k not in ["translator", "cli", "detector", "inpainter", "render", "upscale", "colorizer", "ocr", "app"]}
        if global_params and panel:
            self._create_param_widgets(global_params, panel.layout(), "")
    elif panel and section in config:
        self._create_param_widgets(config[section], panel.layout(), section)

    # Schedule the next chunk
    QTimer.singleShot(0, self._process_next_setting_chunk)

def _finalize_settings_ui(self):
    """
    Called after all incremental updates are done. Sets up dependent UI like .env section.
    """
    # 在 CLI 配置区域最上面添加"翻译完成后卸载模型"复选框
    cli_panel = self.tab_frames.get("Basic Settings")
    if cli_panel and not getattr(self, "_settings_tabs_use_reclassify", False):
        cli_layout = cli_panel.layout()
        if cli_layout is not None and isinstance(cli_layout, QFormLayout):
            # 创建滑块开关
            unload_models_checkbox = ToggleSwitch()
            
            # 从配置中读取初始状态
            config = self.config_service.get_config()
            unload_models_checkbox.setCheckedNoSignal(config.app.unload_models_after_translation)
            
            # 连接信号
            unload_models_checkbox.stateChanged.connect(
                lambda state: self.controller.update_single_config(
                    'app.unload_models_after_translation', 
                    bool(state)
                )
            )
            
            # 创建标签
            label_text = self._t("label_unload_models_after_translation")
            if not label_text or label_text == "label_unload_models_after_translation":
                label_text = "Unload Models After Translation"
            unload_models_label = QLabel(f"{label_text}:")
            
            # 插入到最上面（索引0）
            cli_layout.insertRow(0, unload_models_label, unload_models_checkbox)
    
    if hasattr(self, "env_tab_widget"):
        # Update tab text matching locale dynamically if needed
        self.env_tab_widget.setTabText(0, self._t("Translation"))
        self.env_tab_widget.setTabText(1, self._t("OCR"))
        self.env_tab_widget.setTabText(2, self._t("Colorization"))
        self.env_tab_widget.setTabText(3, self._t("Render"))

    # Clear containers
    for layout in [self.env_preset_layout, self.env_group_container_layout, self.ocr_container_layout, self.color_container_layout, self.render_container_layout]:
        _clear_layout_widgets(layout)
                
    # --- 全局 API Preset Toolbar ---
    preset_label = QLabel(self._t("Preset:"))
    self.preset_combo = QComboBox()
    self.preset_combo.setMinimumWidth(180)
    self.preset_combo.setEditable(False)
    self._refresh_preset_list()

    saved_preset = self.controller.config_service.get_current_preset()
    index = self.preset_combo.findText(saved_preset)
    if index >= 0:
        self.preset_combo.setCurrentIndex(index)

    self.add_preset_button = QPushButton("+")
    self.add_preset_button.setFixedWidth(36)
    set_hover_hint(self.add_preset_button, self._t("Add new preset"))

    self.delete_preset_button = QPushButton(self._t("Delete"))
    set_hover_hint(self.delete_preset_button, self._t("Delete selected preset"))

    self.env_preset_layout.addWidget(preset_label)
    self.env_preset_layout.addWidget(self.preset_combo)
    self.env_preset_layout.addWidget(self.add_preset_button)
    self.env_preset_layout.addWidget(self.delete_preset_button)
    self.env_preset_layout.addStretch()

    self._current_preset_name = self.preset_combo.currentText() if self.preset_combo.count() > 0 else ""

    self.add_preset_button.clicked.connect(self._on_add_preset_clicked)
    self.delete_preset_button.clicked.connect(self._on_delete_preset_clicked)
    self.preset_combo.currentTextChanged.connect(self._on_preset_changed)
    
    _refresh_env_api_groups(self)
    self._refresh_api_feature_selectors()

    self._refresh_prompt_manager()
    self._refresh_font_manager()
    self._settings_rendered_signature = getattr(self, "_settings_pending_signature", None)
    self._settings_ui_ready = True

def _create_dynamic_settings(self):
    """读取配置文件并动态创建所有设置控件"""
    try:
        config = self.config_service.get_config().model_dump() # Get default config
        self.set_parameters(config)
    except Exception as e:
        print(f"Error creating dynamic settings: {e}")

def _on_setting_changed(self, value, full_key, display_map=None):
    """A slot to handle when any setting widget is changed by the user."""
    final_value = value
    # Handle reverse mapping for QComboBox
    if display_map:
        reverse_map = {v: k for k, v in display_map.items()}
        final_value = reverse_map.get(value, value) # Fallback to value itself if not in map
    
    # 特殊处理：当 upscaler 变化时，更新 upscale_ratio 动态下拉框
    if full_key == "upscale.upscaler":
        self._update_upscale_ratio_options(final_value)
    
    self.setting_changed.emit(full_key, final_value)
    if full_key in {
        "translator.translator",
        "ocr.ocr",
        "ocr.secondary_ocr",
        "ocr.use_hybrid_ocr",
        "colorizer.colorizer",
        "render.renderer",
    }:
        QTimer.singleShot(100, lambda: _refresh_env_api_groups(self))

def _on_upscale_ratio_changed(self, text, full_key):
    """处理 upscale_ratio 动态下拉框的变化"""
    config = self.config_service.get_config()
    
    if config.upscale.upscaler == "realcugan":
        # 当前是 realcugan
        if text == self._t("upscale_ratio_not_use"):
            # 禁用超分
            self.setting_changed.emit("upscale.upscale_ratio", None)
            self.setting_changed.emit("upscale.realcugan_model", None)
        else:
            # text 可能是中文显示名称，需要转换回英文值
            display_map = self.controller.get_display_mapping("realcugan_model")
            model_value = text
            
            # 如果有display_map，进行反向查找
            if display_map:
                reverse_map = {v: k for k, v in display_map.items()}
                model_value = reverse_map.get(text, text)
            
            # 从模型名称中提取倍率
            scale_str = model_value.split('x')[0] if 'x' in model_value else None
            if scale_str and scale_str.isdigit():
                scale = int(scale_str)
                # 同时更新 realcugan_model 和 upscale_ratio
                self.setting_changed.emit("upscale.realcugan_model", model_value)
                self.setting_changed.emit("upscale.upscale_ratio", scale)
            else:
                # 无法解析倍率，只更新模型
                self.setting_changed.emit("upscale.realcugan_model", model_value)
    elif config.upscale.upscaler == "mangajanai":
        # 当前是 mangajanai，直接把选项存到 upscale_ratio
        if text == self._t("upscale_ratio_not_use"):
            # 禁用超分
            self.setting_changed.emit("upscale.upscale_ratio", None)
        else:
            # 直接存储选项字符串 (x2, x4, DAT2 x4)
            self.setting_changed.emit("upscale.upscale_ratio", text)
    else:
        # 当前是其他超分模型，text 是倍率
        if text == self._t("upscale_ratio_not_use"):
            self.setting_changed.emit(full_key, None)
        else:
            try:
                ratio = int(text)
                self.setting_changed.emit(full_key, ratio)
            except ValueError:
                self.setting_changed.emit(full_key, None)

def _on_numeric_input_changed(self, text, full_key, value_type):
    """统一处理数值类型输入框的变化（支持 int 和 float）"""
    if not text or not text.strip():
        # 空值 = 使用默认值 (None)
        self.setting_changed.emit(full_key, None)
    else:
        try:
            value = value_type(text)
            self.setting_changed.emit(full_key, value)
        except ValueError:
            # 无效输入 = 使用默认值
            self.setting_changed.emit(full_key, None)

def _update_upscale_ratio_options(self, upscaler):
    """当 upscaler 变化时，更新 upscale_ratio 下拉框的选项"""
    # 查找 upscale_ratio_dynamic widget
    upscale_ratio_widget = self.findChild(QComboBox, "upscale_ratio_dynamic")
    if not upscale_ratio_widget:
        return
    
    # 阻止信号触发
    upscale_ratio_widget.blockSignals(True)
    
    # 清空并重新填充
    upscale_ratio_widget.clear()
    
    if upscaler == "realcugan":
        # 显示 Real-CUGAN 模型列表（使用中文显示）
        realcugan_models = self.controller.get_options_for_key("realcugan_model")
        display_map = self.controller.get_display_mapping("realcugan_model")
        
        if realcugan_models:
            # 如果有display_map，使用中文名称
            if display_map:
                display_options = [display_map.get(model, model) for model in realcugan_models]
                all_options = [self._t("upscale_ratio_not_use")] + display_options
            else:
                all_options = [self._t("upscale_ratio_not_use")] + realcugan_models
            
            upscale_ratio_widget.addItems(all_options)
        
        # 设置默认值
        config = self.config_service.get_config()
        if config.upscale.realcugan_model:
            # 如果有display_map，显示中文名称
            if display_map:
                display_name = display_map.get(config.upscale.realcugan_model, config.upscale.realcugan_model)
                upscale_ratio_widget.setCurrentText(display_name)
            else:
                upscale_ratio_widget.setCurrentText(config.upscale.realcugan_model)
        elif config.upscale.upscale_ratio is None:
            upscale_ratio_widget.setCurrentText(self._t("upscale_ratio_not_use"))
        elif realcugan_models:
            if display_map:
                upscale_ratio_widget.setCurrentText(display_map.get(realcugan_models[0], realcugan_models[0]))
            else:
                upscale_ratio_widget.setCurrentText(realcugan_models[0])
    elif upscaler == "mangajanai":
        # 显示 MangaJaNai 特殊选项
        mangajanai_options = ["x2", "x4", "DAT2 x4"]
        all_options = [self._t("upscale_ratio_not_use")] + mangajanai_options
        upscale_ratio_widget.addItems(all_options)
        
        # 设置默认值 - upscale_ratio 直接存储选项字符串
        config = self.config_service.get_config()
        ratio = config.upscale.upscale_ratio
        if ratio is None:
            upscale_ratio_widget.setCurrentText(self._t("upscale_ratio_not_use"))
        elif isinstance(ratio, str) and ratio in mangajanai_options:
            upscale_ratio_widget.setCurrentText(ratio)
        elif ratio == 2:
            upscale_ratio_widget.setCurrentText("x2")
        else:
            upscale_ratio_widget.setCurrentText("x4")
    else:
        # 显示普通倍率选项
        ratio_options = [self._t("upscale_ratio_not_use"), "2", "3", "4"]
        upscale_ratio_widget.addItems(ratio_options)
        # 设置默认值
        config = self.config_service.get_config()
        if config.upscale.upscale_ratio is None:
            upscale_ratio_widget.setCurrentText(self._t("upscale_ratio_not_use"))
        else:
            upscale_ratio_widget.setCurrentText(str(config.upscale.upscale_ratio))
    
    # 恢复信号
    upscale_ratio_widget.blockSignals(False)

def _create_param_widgets(self, data, parent_layout, prefix=""):
    if not isinstance(data, dict):
        return

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        # 跳过这些选项，因为已经用下拉框替代或不需要在UI中显示
        # realcugan_model 将通过 upscale_ratio 动态下拉框处理
        # gimp_font 已废弃，使用 font_path 代替
        # replace_translation 和 replace_translation_mode 通过工作流模式下拉框控制
        # app 配置组的字段：last_open_dir, last_output_path, favorite_folders, current_preset 是内部状态，不显示在UI中
        if full_key in ["cli.load_text", "cli.translate_json_only", "cli.template", "cli.generate_and_export", "cli.colorize_only", "cli.upscale_only", "cli.inpaint_only", "cli.replace_translation", "cli.replace_translation_mode", "upscale.realcugan_model", "render.gimp_font", "render.font_path", "translator.high_quality_prompt_path", "app.last_open_dir", "app.last_output_path", "app.favorite_folders", "app.current_preset"]:
            continue

        label_text = key
        if full_key == "app.theme":
            label_text = self._t("Theme:").rstrip(":：")
        elif full_key == "app.ui_language":
            label_text = self._t("Language:").rstrip(":：")
        elif full_key == "app.unload_models_after_translation":
            translated = self._t("label_unload_models_after_translation")
            label_text = translated if translated != "label_unload_models_after_translation" else "Unload Models After Translation"
        if self.controller.get_display_mapping('labels') and self.controller.get_display_mapping('labels').get(key):
            label_text = self.controller.get_display_mapping('labels').get(key)
        label = QLabel(f"{label_text}:")
        label.setMinimumWidth(120)
        widget = None

        options = self.controller.get_options_for_key(key)
        display_map = self.controller.get_display_mapping(key)

        if full_key == "app.theme":
            widget = QComboBox()
            self.theme_label = label
            self.theme_combo = widget
            widget.currentIndexChanged.connect(self._on_theme_combo_changed)
            self._populate_theme_combo()

        elif full_key == "app.ui_language":
            widget = QComboBox()
            self.language_label = label
            self.language_combo = widget
            widget.currentIndexChanged.connect(self._on_language_combo_changed)
            self._populate_language_combo()

        elif full_key == "filter_text_enabled":
            # 特殊处理：过滤列表开关 + 编辑过滤列表按钮
            container = QWidget()
            hbox = QHBoxLayout(container)
            hbox.setContentsMargins(0, 0, 0, 0)
            
            checkbox = ToggleSwitch(checked=value)
            checkbox.stateChanged.connect(lambda state, k=full_key: self._on_setting_changed(bool(state), k, None))
            
            open_btn = QPushButton(self._t("btn_open_filter_list"))
            open_btn.clicked.connect(self._open_filter_list)
            
            hbox.addWidget(checkbox)
            hbox.addWidget(open_btn)
            hbox.addStretch()
            widget = container

        elif full_key == "render.font_path":
            container = QWidget()
            hbox = QHBoxLayout(container)
            hbox.setContentsMargins(0, 0, 0, 0)
            
            # 创建自定义ComboBox,在下拉时刷新字体列表
            class RefreshableComboBox(QComboBox):
                def showPopup(self):
                    current_text = self.currentText()
                    self.clear()
                    try:
                        fonts_dir = resource_path('fonts')
                        if os.path.isdir(fonts_dir):
                            font_files = sorted([f for f in os.listdir(fonts_dir) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
                            self.addItems(font_files)
                    except Exception as e:
                        print(f"Error scanning fonts directory: {e}")
                    # 恢复之前选择的值
                    if current_text:
                        index = self.findText(current_text)
                        if index >= 0:
                            self.setCurrentIndex(index)
                        else:
                            self.setCurrentText(current_text)
                    super().showPopup()
            
            combo = RefreshableComboBox()
            combo.setMinimumWidth(260)
            try:
                fonts_dir = resource_path('fonts')
                if os.path.isdir(fonts_dir):
                    font_files = sorted([f for f in os.listdir(fonts_dir) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
                    combo.addItems(font_files)
            except Exception as e:
                print(f"Error scanning fonts directory: {e}")
            combo.setCurrentText(str(value) if value else "")
            combo.currentTextChanged.connect(lambda text, k=full_key: self._on_setting_changed(text, k, None))
            button = QPushButton(self._t("Open Directory"))
            button.clicked.connect(self.controller.open_font_directory)
            hbox.addWidget(combo)
            hbox.addWidget(button)
            widget = container

        elif full_key == "translator.high_quality_prompt_path":
            container = QWidget()
            hbox = QHBoxLayout(container)
            hbox.setContentsMargins(0, 0, 0, 0)
            
            # 创建自定义ComboBox,在下拉时刷新提示词列表
            class RefreshablePromptComboBox(QComboBox):
                def __init__(self, controller_ref, parent=None):
                    super().__init__(parent)
                    self.controller_ref = controller_ref
                
                def showPopup(self):
                    current_text = self.currentText()
                    self.clear()
                    prompt_files = self.controller_ref.get_hq_prompt_options()
                    if prompt_files:
                        self.addItems(prompt_files)
                    # 恢复之前选择的值
                    if current_text:
                        index = self.findText(current_text)
                        if index >= 0:
                            self.setCurrentIndex(index)
                        else:
                            self.setCurrentText(current_text)
                    super().showPopup()
            
            combo = RefreshablePromptComboBox(self.controller)
            combo.setMinimumWidth(260)
            prompt_files = self.controller.get_hq_prompt_options()
            if prompt_files:
                combo.addItems(prompt_files)
            filename = os.path.basename(value) if value else ""
            combo.setCurrentText(filename)
            combo.currentTextChanged.connect(lambda text, k=full_key: self._on_setting_changed(os.path.join('dict', text).replace('\\', '/') if text else None, k, None))
            button = QPushButton(self._t("Open Directory"))
            button.clicked.connect(self.controller.open_dict_directory)
            hbox.addWidget(combo)
            hbox.addWidget(button)
            widget = container

        elif isinstance(value, bool):
            # 特殊处理：use_custom_api_params 需要添加"打开文件"按钮
            if full_key == "use_custom_api_params":
                container = QWidget()
                container_layout = QHBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                
                checkbox = ToggleSwitch(checked=value)
                checkbox.stateChanged.connect(lambda state, k=full_key: self._on_setting_changed(bool(state), k, None))
                
                open_file_button = QPushButton(self._t("Edit"))
                open_file_button.setFixedWidth(100)
                open_file_button.clicked.connect(self._on_open_custom_api_params_file)
                
                container_layout.addWidget(checkbox)
                container_layout.addWidget(open_file_button)
                container_layout.addStretch()
                
                widget = container
            else:
                widget = ToggleSwitch(checked=value)
                widget.stateChanged.connect(lambda state, k=full_key: self._on_setting_changed(bool(state), k, None))

        # 特殊处理：upscale_ratio 动态下拉框（必须在 int/float 判断之前）
        elif full_key == "upscale.upscale_ratio":
            widget = QComboBox()
            widget.setObjectName("upscale_ratio_dynamic")
            widget.setMinimumWidth(100)  # 设置最小宽度，让选项显示更完整
            
            # 获取当前的 upscaler 值来决定显示什么选项
            config = self.config_service.get_config()
            current_upscaler = config.upscale.upscaler
            
            if current_upscaler == "realcugan":
                # 显示 Real-CUGAN 模型列表（使用中文显示）
                realcugan_models = self.controller.get_options_for_key("realcugan_model")
                display_map = self.controller.get_display_mapping("realcugan_model")
                
                if realcugan_models:
                    # 如果有display_map，使用中文名称
                    if display_map:
                        display_options = [display_map.get(model, model) for model in realcugan_models]
                        all_options = [self._t("upscale_ratio_not_use")] + display_options
                    else:
                        all_options = [self._t("upscale_ratio_not_use")] + realcugan_models
                    widget.addItems(all_options)
                
                # 设置当前值（从 realcugan_model 获取）
                current_model = config.upscale.realcugan_model
                if current_model:
                    # 如果有display_map，显示中文名称
                    if display_map:
                        display_name = display_map.get(current_model, current_model)
                        widget.setCurrentText(display_name)
                    else:
                        widget.setCurrentText(current_model)
                elif value is None:
                    widget.setCurrentText(self._t("upscale_ratio_not_use"))
                elif realcugan_models:
                    widget.setCurrentText(realcugan_models[0])
            elif current_upscaler == "mangajanai":
                # 显示 MangaJaNai 特殊选项
                mangajanai_options = ["x2", "x4", "DAT2 x4"]
                all_options = [self._t("upscale_ratio_not_use")] + mangajanai_options
                widget.addItems(all_options)
                
                # 设置当前值 - upscale_ratio 直接存储选项字符串
                if value is None:
                    widget.setCurrentText(self._t("upscale_ratio_not_use"))
                elif isinstance(value, str) and value in mangajanai_options:
                    widget.setCurrentText(value)
                elif value == 2:
                    widget.setCurrentText("x2")
                else:
                    widget.setCurrentText("x4")
            else:
                # 显示普通倍率选项
                ratio_options = [self._t("upscale_ratio_not_use"), "2", "3", "4"]
                widget.addItems(ratio_options)
                # 设置当前值
                if value is None:
                    widget.setCurrentText(self._t("upscale_ratio_not_use"))
                else:
                    widget.setCurrentText(str(value))
            
            widget.currentTextChanged.connect(lambda text, k=full_key: self._on_upscale_ratio_changed(text, k))
        
        elif isinstance(value, (int, float)):
            widget = QLineEdit(str(value))
            widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, float if isinstance(value, float) else int))

        elif value is None and key in ['tile_size', 'line_spacing', 'letter_spacing', 'font_size', 'psd_font', 'ocr_vl_custom_prompt', 'ai_ocr_custom_prompt']:
            # 处理值为 None 的可选参数（数值/字符串）
            widget = QLineEdit("")
            # 根据参数名设置提示文本
            if key == 'tile_size':
                widget.setPlaceholderText(self._t("Default: 400"))
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, int))
            elif key == 'line_spacing':
                widget.setPlaceholderText(self._t("Default: 1.0 (multiplier for base spacing)"))
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, float))
            elif key == 'letter_spacing':
                widget.setPlaceholderText(self._t("Default: 1.0 (multiplier for base spacing)"))
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, float))
            elif key == 'font_size':
                widget.setPlaceholderText(self._t("Auto"))
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_numeric_input_changed(w.text(), k, int))
            elif key == 'psd_font':
                widget.setPlaceholderText(self._t("Photoshop Font Name (e.g. AdobeHeitiStd-Regular)"))
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_setting_changed(w.text(), k, None))
            elif key == 'ocr_vl_custom_prompt':
                widget.setMinimumWidth(320)
                widget.setPlaceholderText("OCR: Extract all Arabic text.")
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_setting_changed(w.text(), k, None))
            elif key == 'ai_ocr_custom_prompt':
                widget.setMinimumWidth(320)
                widget.setPlaceholderText("Read the text and return only the recognized text.")
                widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_setting_changed(w.text(), k, None))

        elif (isinstance(value, str) or value is None) and (options or display_map):
            widget = QComboBox()
            if key == "translator":
                widget.setObjectName("translator.translator")
                widget.setMinimumWidth(180)  # 设置翻译器下拉框最小宽度
            elif full_key == "ocr.ocr_vl_language_hint":
                widget.setMinimumWidth(260)  # OCR语言全称较长，避免被截断
            else:
                widget.setMinimumWidth(180)
            
            if display_map:
                widget.addItems(list(display_map.values()))
                current_display_name = display_map.get(value) if value is not None else None
                if current_display_name:
                    widget.setCurrentText(current_display_name)
                widget.currentTextChanged.connect(lambda text, k=full_key, dm=display_map: self._on_setting_changed(text, k, dm))
            else:
                widget.addItems(options)
                if value is not None:
                    widget.setCurrentText(value)
                else:
                    # 对于 None 值，设置第一个选项为默认值（通常是 "不使用"）
                    if options:
                        widget.setCurrentText(options[0])
                widget.currentTextChanged.connect(lambda text, k=full_key: self._on_setting_changed(text, k, None))

        elif isinstance(value, str):
            widget = QLineEdit(value)
            if full_key in {"ocr.ocr_vl_custom_prompt", "ocr.ai_ocr_custom_prompt"}:
                widget.setMinimumWidth(320)
                if full_key == "ocr.ocr_vl_custom_prompt":
                    widget.setPlaceholderText("OCR: Extract all Arabic text.")
                else:
                    widget.setPlaceholderText("Read the text and return only the recognized text.")
            widget.editingFinished.connect(lambda k=full_key, w=widget: self._on_setting_changed(w.text(), k, None))
        
        if widget is not None:
            # 使用 ClickableRow 包装 label + widget，整行可点击、可高亮
            row = _ClickableRow(self, full_key, label, widget)
            parent_layout.addRow(row)


class _ClickableRow(QWidget):
    """整行可点击、可高亮的设置行，包含 label 和控件。"""

    def __init__(self, view, full_key: str, label: QLabel, widget: QWidget):
        super().__init__()
        self._view = view
        self._full_key = full_key
        self._label = label
        self._selected = False

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(8, 4, 8, 4)
        row_layout.setSpacing(12)

        label.setMinimumWidth(120)
        row_layout.addWidget(label)

        if isinstance(widget, ToggleSwitch):
            row_layout.addWidget(widget)
            row_layout.addStretch(1)
        elif isinstance(widget, QWidget):
            row_layout.addWidget(widget, 1)

        # 给所有子控件安装事件过滤器，点击子控件时也触发行高亮
        self._install_child_event_filter(widget)
        label.installEventFilter(self)

    def _install_child_event_filter(self, widget):
        """递归给所有子控件安装事件过滤器"""
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            self._activate()
        return False  # 不消费事件，让子控件正常工作

    def _activate(self):
        """激活此行：更新描述面板 + 高亮"""
        desc = _get_setting_description(self._view, self._full_key)
        label_text = self._label.text().rstrip(':：')
        if hasattr(self._view, '_show_setting_description'):
            self._view._show_setting_description(self._full_key, label_text, desc)

        # 取消之前高亮的行
        for old in getattr(self._view, '_highlighted_rows', []):
            try:
                old._set_selected(False)
            except (RuntimeError, AttributeError):
                pass
        self._set_selected(True)
        self._view._highlighted_rows = [self]

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._activate()
        super().mouseReleaseEvent(event)

    def _set_selected(self, selected: bool):
        self._selected = selected
        self.update()

    def paintEvent(self, event):
        if self._selected:
            from PyQt6.QtCore import QRectF
            from PyQt6.QtGui import QPainter, QPainterPath
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 6, 6)
            p.fillPath(path, QColor(50, 90, 140, 64))
            p.end()
        super().paintEvent(event)
