import os
import re
import textwrap
from functools import partial

from manga_translator.api_key_rotation import (
    ROTATION_STRATEGIES,
    APIEndpoint,
    get_indexed_env_key,
    get_rotation_slot_count,
    get_strategy_env_key,
    make_endpoint_status_key,
    normalize_rotation_strategy,
    record_api_failure,
    record_api_success,
)
from manga_translator.utils.openai_compat import is_openai_api_key_optional
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CardWidget, PushButton
from qfluentwidgets import LineEdit as FluentLineEdit

from ui.fluent_icon import themed_fluent_svg_icon
from ui.widgets.wheel_filter import NoWheelComboBox as QComboBox


class QLineEdit(FluentLineEdit):
    """Fluent LineEdit with the PyQt constructor forms used by env editors."""

    def __init__(self, text: str | QWidget | None = "", parent: QWidget | None = None):
        if isinstance(text, QWidget) and parent is None:
            parent = text
            text = ""
        super().__init__(parent)
        if text:
            self.setText(str(text))

    def addAction(self, action, position=FluentLineEdit.ActionPosition.TrailingPosition):
        if isinstance(action, QIcon):
            action = QAction(action, "", self)
        super().addAction(action, position)
        return action


def _get_env_widget_value(widget) -> str:
    if hasattr(widget, "currentData"):
        value = widget.currentData()
        if value is not None:
            return str(value)
        if hasattr(widget, "currentText"):
            return str(widget.currentText())
    if hasattr(widget, "text"):
        return str(widget.text())
    return ""


def _set_env_widget_value(widget, value: str) -> None:
    value = "" if value is None else str(value)
    if hasattr(widget, "findData") and hasattr(widget, "setCurrentIndex"):
        index = widget.findData(value)
        if index < 0 and hasattr(widget, "findText"):
            index = widget.findText(value)
        if index >= 0:
            widget.setCurrentIndex(index)
        return
    if hasattr(widget, "setText"):
        widget.setText(value)


def _display_env_label(self, key: str, index: int | None = None, *, include_index: bool = True) -> str:
    labels_map = self.controller.get_display_mapping("labels") or {}
    display_key = key
    label_text = labels_map.get(key)
    if not label_text:
        for prefix in ["OCR_", "COLOR_", "RENDER_"]:
            if key.startswith(prefix):
                display_key = key[len(prefix):]
                break
        label_text = labels_map.get(display_key, display_key)
    if include_index and index and index > 1:
        return f"{label_text} #{index}"
    return label_text


def _is_secret_env_key(key: str) -> bool:
    normalized_key = str(key or "").upper()
    return "API_KEY" in normalized_key or "AUTH_KEY" in normalized_key or "TOKEN" in normalized_key


def _make_secret_visibility_icon(hidden: bool):
    filename = "eye_off.svg" if hidden else "eye.svg"
    return themed_fluent_svg_icon(filename)


def _create_env_line_edit(self, key: str, value: str):
    widget = QLineEdit(str(value) if value else "")
    widget.setPlaceholderText(self._get_env_default_placeholder(key))
    if not _is_secret_env_key(key):
        return widget, widget

    widget.setEchoMode(QLineEdit.EchoMode.Password)
    show_icon = _make_secret_visibility_icon(hidden=True)
    hide_icon = _make_secret_visibility_icon(hidden=False)
    toggle_action = widget.addAction(show_icon, QLineEdit.ActionPosition.TrailingPosition)
    toggle_action.setToolTip(self._t("Show Secret"))

    def toggle_secret_visibility():
        if widget.echoMode() == QLineEdit.EchoMode.Normal:
            widget.setEchoMode(QLineEdit.EchoMode.Password)
            toggle_action.setIcon(show_icon)
            toggle_action.setToolTip(self._t("Show Secret"))
        else:
            widget.setEchoMode(QLineEdit.EchoMode.Normal)
            toggle_action.setIcon(hide_icon)
            toggle_action.setToolTip(self._t("Hide Secret"))

    toggle_action.triggered.connect(toggle_secret_visibility)
    return widget, widget


def _add_env_action_button(self, layout, row: int, env_key: str, action_key: str) -> None:
    if _is_secret_env_key(action_key):
        test_button = PushButton(self._t("Test"))
        test_button.setFixedWidth(60)
        test_button.clicked.connect(partial(self._on_test_api_clicked, env_key))
        layout.addWidget(test_button, row, 2)
    elif "MODEL" in action_key:
        get_models_button = PushButton(self._t("Get Models"))
        get_models_button.setFixedWidth(100)
        get_models_button.clicked.connect(partial(self._on_get_models_clicked, env_key))
        layout.addWidget(get_models_button, row, 2)


def create_env_widgets(self, keys: list, current_values: dict):
    """为给定的键创建标签和输入框。"""
    from PyQt6.QtWidgets import QGridLayout

    is_grid_layout = isinstance(self.env_layout, QGridLayout)
    row = self.env_layout.rowCount() if is_grid_layout else 0
    for key in keys:
        value = current_values.get(key, "")

        label_text = _display_env_label(self, key)
        label = QLabel(f"{label_text}:")
        widget, display_widget = _create_env_line_edit(self, key, value)
        widget.textChanged.connect(partial(self._debounced_save_env_var, key))
        widget.editingFinished.connect(partial(self._flush_env_var_immediately, key))

        if is_grid_layout:
            self.env_layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignLeft)
            self.env_layout.addWidget(display_widget, row, 1)
            _add_env_action_button(self, self.env_layout, row, key, key)
            row += 1
        else:
            self.env_layout.addRow(label, display_widget)
        self.env_widgets[key] = (label, widget)


def create_api_rotation_widgets(
    self,
    *,
    api_key_env: str,
    model_env: str,
    api_base_env: str,
    current_values: dict,
):
    """Create a provider API rotation editor backed by .env keys."""
    if not hasattr(self, "env_layout"):
        return

    layout = self.env_layout
    slot_keys = (api_key_env, model_env, api_base_env)
    slot_count = get_rotation_slot_count(current_values, slot_keys)
    strategy_key = get_strategy_env_key(api_key_env)
    row = layout.rowCount()

    if strategy_key:
        strategy_label = QLabel(self._t("API rotation strategy:"))
        strategy_combo = QComboBox()
        for value in ROTATION_STRATEGIES:
            strategy_combo.addItem(self._t(f"api_rotation_strategy_{value}"), value)
        current_strategy = normalize_rotation_strategy(current_values.get(strategy_key, ""))
        current_index = strategy_combo.findData(current_strategy)
        if current_index >= 0:
            strategy_combo.setCurrentIndex(current_index)
        strategy_combo.currentIndexChanged.connect(
            lambda _idx, key=strategy_key, combo=strategy_combo: self.env_var_changed.emit(
                key,
                _get_env_widget_value(combo),
            )
        )
        layout.addWidget(strategy_label, row, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(strategy_combo, row, 1)
        self.env_widgets[strategy_key] = (strategy_label, strategy_combo)
        row += 1

    def add_slot(index: int):
        nonlocal row

        slot_card = CardWidget()

        slot_card_layout = QVBoxLayout(slot_card)
        slot_card_layout.setContentsMargins(12, 10, 12, 12)
        slot_card_layout.setSpacing(10)

        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        slot_badge = BodyLabel(f"{index:02d}")
        slot_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        slot_badge.setFixedSize(28, 22)

        slot_label = BodyLabel(self._t("API slot {index}", index="").strip())

        slot_line = QFrame()
        slot_line.setFrameShape(QFrame.Shape.HLine)
        slot_line.setFrameShadow(QFrame.Shadow.Plain)

        header_layout.addWidget(slot_badge)
        header_layout.addWidget(slot_label)
        header_layout.addWidget(slot_line, 1)
        slot_card_layout.addWidget(header_widget)

        slot_grid = QGridLayout()
        slot_grid.setColumnStretch(1, 1)
        slot_grid.setColumnStretch(2, 0)
        slot_grid.setHorizontalSpacing(12)
        slot_grid.setVerticalSpacing(10)
        slot_grid.setContentsMargins(0, 0, 0, 0)
        slot_card_layout.addLayout(slot_grid)

        slot_row = 0

        for base_key in (api_key_env, model_env, api_base_env):
            key = get_indexed_env_key(base_key, index)
            if not key:
                continue
            value = current_values.get(key, "")
            label = QLabel(f"{_display_env_label(self, base_key, index, include_index=False)}:")
            widget, display_widget = _create_env_line_edit(self, base_key, value)
            widget.textChanged.connect(partial(self._debounced_save_env_var, key))
            widget.editingFinished.connect(partial(self._flush_env_var_immediately, key))
            slot_grid.addWidget(label, slot_row, 0, Qt.AlignmentFlag.AlignLeft)
            slot_grid.addWidget(display_widget, slot_row, 1)
            _add_env_action_button(self, slot_grid, slot_row, key, base_key)
            self.env_widgets[key] = (label, widget)
            slot_row += 1

        layout.addWidget(slot_card, row, 0, 1, 3)
        row += 1

    for slot_index in range(1, slot_count + 1):
        add_slot(slot_index)

    add_button = PushButton(self._t("+ Add API slot"))

    def add_next_slot():
        nonlocal row
        layout.removeWidget(add_button)
        row = max(0, row - 1)
        existing_values = {key: _get_env_widget_value(pair[1]) for key, pair in self.env_widgets.items()}
        next_index = get_rotation_slot_count(existing_values, slot_keys) + 1
        add_slot(next_index)
        layout.addWidget(add_button, row, 0, 1, 3, Qt.AlignmentFlag.AlignLeft)
        row += 1

    add_button.clicked.connect(add_next_slot)
    layout.addWidget(add_button, row, 0, 1, 3, Qt.AlignmentFlag.AlignLeft)
    row += 1


def get_env_default_placeholder(self, key: str) -> str:
    """返回环境变量输入框应显示的默认占位符。"""
    key_placeholder = self._t("placeholder_paste_key")
    token_placeholder = self._t("placeholder_paste_token")
    normalized_key = key.upper()
    slot_match = re.match(r"^(.+_(?:API_KEY|AUTH_KEY|TOKEN|API_BASE|BASE|MODEL))_\d+$", normalized_key)
    if slot_match:
        normalized_key = slot_match.group(1)

    default_placeholders = {
        "OCR_OPENAI_API_BASE": "https://api.openai.com/v1",
        "OCR_OPENAI_MODEL": "gpt-4o",
        "OCR_GEMINI_API_BASE": "https://generativelanguage.googleapis.com",
        "OCR_GEMINI_MODEL": "gemini-1.5-flash",
        "COLOR_OPENAI_API_BASE": "https://api.openai.com/v1",
        "COLOR_OPENAI_MODEL": "gpt-image-1",
        "COLOR_GEMINI_API_BASE": "https://generativelanguage.googleapis.com",
        "COLOR_GEMINI_MODEL": "gemini-2.0-flash-preview-image-generation",
        "RENDER_OPENAI_API_BASE": "https://api.openai.com/v1",
        "RENDER_OPENAI_MODEL": "gpt-image-1",
        "RENDER_GEMINI_API_BASE": "https://generativelanguage.googleapis.com",
        "RENDER_GEMINI_MODEL": "gemini-2.0-flash-preview-image-generation",
        "OPENAI_API_BASE": "https://api.openai.com/v1",
        "CUSTOM_OPENAI_API_BASE": "https://api.openai.com/v1",
        "GEMINI_API_BASE": "https://generativelanguage.googleapis.com",
        "SAKURA_API_BASE": "http://127.0.0.1:8080/v1",
        "SAKURA_DICT_PATH": "./dict/sakura_dict.txt",
        "OPENAI_MODEL": "gpt-4o",
        "CUSTOM_OPENAI_MODEL": "qwen2.5:7b",
        "GEMINI_MODEL": "gemini-1.5-flash-002",
        "GROQ_MODEL": "mixtral-8x7b-32768",
        "DEEPSEEK_MODEL": "deepseek-chat",
        "OPENAI_API_KEY": key_placeholder,
        "CUSTOM_OPENAI_API_KEY": key_placeholder,
        "GEMINI_API_KEY": key_placeholder,
        "GROQ_API_KEY": key_placeholder,
        "DEEPSEEK_API_KEY": key_placeholder,
        "DEEPL_AUTH_KEY": key_placeholder,
        "CAIYUN_TOKEN": token_placeholder,
    }
    exact_placeholder = default_placeholders.get(normalized_key)
    if exact_placeholder is not None:
        return exact_placeholder

    for prefix in ("OCR_", "COLOR_", "RENDER_"):
        if normalized_key.startswith(prefix):
            normalized_key = normalized_key[len(prefix):]
            break

    return default_placeholders.get(normalized_key, "")


def debounced_save_env_var(self, key: str, text: str):
    """防抖保存.env变量，支持多个 Key 同时暂存。"""
    if not hasattr(self, '_pending_env_vars'):
        self._pending_env_vars = {}
    self._pending_env_vars[key] = text
    self._env_debounce_timer.stop()
    try:
        self._env_debounce_timer.timeout.disconnect()
    except TypeError:
        pass
    self._env_debounce_timer.timeout.connect(lambda: flush_all_pending_env_vars(self))
    self._env_debounce_timer.start()


def flush_env_var_immediately(self, key: str):
    """立即保存指定 Key（失去焦点/回车时调用）。"""
    pending = getattr(self, '_pending_env_vars', {})
    if key in pending:
        value = pending.pop(key)
        self.env_var_changed.emit(key, value)


def flush_all_pending_env_vars(self):
    """立即保存所有暂存的环境变量。"""
    self._env_debounce_timer.stop()
    pending = getattr(self, '_pending_env_vars', {})
    if not pending:
        return
    for key, value in list(pending.items()):
        self.env_var_changed.emit(key, value)
    pending.clear()


API_FEATURE_SELECTOR_SPECS = [
    ("env_translation_feature_label", "env_translation_feature_combo", "label_translator", "translator.translator", "translator"),
    ("env_ocr_feature_label", "env_ocr_feature_combo", "label_ocr", "ocr.ocr", "ocr"),
    ("env_color_feature_label", "env_color_feature_combo", "label_colorizer", "colorizer.colorizer", "colorizer"),
    ("env_render_feature_label", "env_render_feature_combo", "label_renderer", "render.renderer", "renderer"),
]
API_FEATURE_SELECTOR_BY_SECTION = {
    "translation": API_FEATURE_SELECTOR_SPECS[0],
    "ocr": API_FEATURE_SELECTOR_SPECS[1],
    "color": API_FEATURE_SELECTOR_SPECS[2],
    "render": API_FEATURE_SELECTOR_SPECS[3],
}


def _normalize_config_value(value) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _resolve_config_value(config, full_key: str):
    current = config
    for part in str(full_key or "").split("."):
        current = getattr(current, part, None)
        if current is None:
            return ""
    return _normalize_config_value(current)


def _populate_api_feature_selector(self, label, combo, label_key: str, setting_key: str, options_key: str):
    config = self.controller.config_service.get_config()
    label.setText(f"{self._t(label_key)}:")
    current_value = _resolve_config_value(config, setting_key)
    options = self.controller.get_options_for_key(options_key) or []
    display_map = self.controller.get_display_mapping(options_key) or {}

    combo.blockSignals(True)
    combo.clear()
    for option in options:
        combo.addItem(display_map.get(option, option), option)
    index = combo.findData(current_value)
    if index >= 0:
        combo.setCurrentIndex(index)
    combo.blockSignals(False)


def create_api_feature_selector_row(self, section_key: str):
    """Create the feature selector row inside an API Management tab form."""
    spec = API_FEATURE_SELECTOR_BY_SECTION.get(section_key)
    if not spec or not hasattr(self, "env_layout"):
        return
    label_attr, combo_attr, label_key, setting_key, options_key = spec
    row = self.env_layout.rowCount()

    label = QLabel(f"{self._t(label_key)}:")
    combo = QComboBox()
    combo.setMinimumWidth(260)
    combo._api_feature_setting_key = setting_key
    combo._api_feature_options_key = options_key
    combo.currentIndexChanged.connect(lambda _idx, widget=combo: self._on_api_feature_combo_changed(widget))

    test_button = PushButton(self._t("Test Current Tab"))
    test_button.clicked.connect(lambda _checked=False, key=section_key: self._on_test_current_api_section_clicked(key))

    setattr(self, label_attr, label)
    setattr(self, combo_attr, combo)
    self.env_layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignLeft)
    self.env_layout.addWidget(combo, row, 1)
    self.env_layout.addWidget(test_button, row, 2)
    _populate_api_feature_selector(self, label, combo, label_key, setting_key, options_key)


def refresh_api_feature_selectors(self):
    """Refresh API Management page feature selectors from the current config."""
    for label_attr, combo_attr, label_key, setting_key, options_key in API_FEATURE_SELECTOR_SPECS:
        label = getattr(self, label_attr, None)
        combo = getattr(self, combo_attr, None)
        if label is None or combo is None:
            continue
        _populate_api_feature_selector(self, label, combo, label_key, setting_key, options_key)


def on_api_feature_combo_changed(self, combo):
    """Handle feature selector changes inside API Management tabs."""
    if combo is None:
        return
    setting_key = getattr(combo, "_api_feature_setting_key", None)
    value = combo.currentData()
    if not setting_key or value is None:
        return
    self.setting_changed.emit(str(setting_key), str(value))
    QTimer.singleShot(100, lambda: self._refresh_env_api_groups())
    QTimer.singleShot(120, lambda: refresh_api_feature_selectors(self))


def _detect_test_target(env_key: str, translator_key: str) -> str:
    scope, provider, _ = _split_env_key(env_key)

    scoped_targets = {
        ("OCR_", "OPENAI"): "openai_ocr",
        ("OCR_", "GEMINI"): "gemini_ocr",
        ("COLOR_", "OPENAI"): "openai_colorizer",
        ("COLOR_", "GEMINI"): "gemini_colorizer",
        ("RENDER_", "OPENAI"): "openai_renderer",
        ("RENDER_", "GEMINI"): "gemini_renderer",
    }
    target = scoped_targets.get((scope, provider))
    if target:
        return target

    provider_targets = {
        "OPENAI": "openai",
        "CUSTOM_OPENAI": "custom_openai",
        "DEEPSEEK": "deepseek",
        "GROQ": "groq",
        "GEMINI": "gemini",
        "SAKURA": "sakura",
    }
    target = provider_targets.get(provider)
    if target:
        return target

    normalized_translator_key = (translator_key or "").strip().lower()
    return normalized_translator_key or "openai"


def _get_api_address_example(api_type: str) -> str:
    normalized = (api_type or "").lower()
    if "gemini" in normalized:
        return "https://generativelanguage.googleapis.com"
    if "deepseek" in normalized:
        return "https://api.deepseek.com"
    if "groq" in normalized:
        return "https://api.groq.com/openai/v1"
    if "sakura" in normalized:
        return "http://127.0.0.1:8080/v1"
    return "https://api.openai.com/v1"


def _wrap_error_text(message: str, width: int = 60) -> str:
    wrapped_lines: list[str] = []
    for line in str(message or "").splitlines():
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


def _format_test_connection_error(api_type: str, message: str) -> str:
    raw_message = str(message or "").strip()
    analysis_message = raw_message
    for prefix in ("连接失败:", "连接失败：", "api connection failed:", "connection failed:"):
        if analysis_message.lower().startswith(prefix):
            analysis_message = analysis_message[len(prefix):].strip()
            break
    error_lower = analysis_message.lower()

    network_keywords = (
        "connection",
        "connect",
        "failed to connect",
        "could not connect to server",
        "cannot connect to host",
        "connection refused",
        "connection reset",
        "connection timed out",
        "network",
        "timeout",
        "timed out",
        "timed out after",
        "curl: (7)",
        "curl: (28)",
        "dns",
        "host",
        "hostname",
        "getaddrinfo",
        "name or service not known",
        "no address associated with hostname",
        "nodename nor servname provided",
        "failed to resolve",
        "temporary failure in name resolution",
        "远程主机",
        "连接",
        "超时",
        "网络",
        "主机",
    )

    service_keywords = (
        "502",
        "503",
        "504",
        "service unavailable",
        "server error",
        "bad gateway",
        "gateway timeout",
        "upstream",
        "overloaded",
        "distributor",
        "channel",
        "unavailable",
        "not available",
        "无可用渠道",
        "渠道",
        "服务不可用",
        "服务异常",
        "站点异常",
        "模型不可用",
    )

    is_network_error = any(keyword in error_lower for keyword in network_keywords)
    is_service_error = any(keyword in error_lower for keyword in service_keywords)

    if is_network_error:
        friendly_message = (
            "检测到连接错误、超时或 Host 解析错误。\n"
            "请先检查模型、API 地址和 API 密钥是否正确；如果配置无误，再检查网络连接，并尝试开启 TUN（虚拟网卡模式）。"
        )
    elif is_service_error:
        friendly_message = (
            "请先检查模型、API 地址和 API 密钥是否正确。\n"
            "如果配置无误，这也可能是 API 站点、中转渠道或服务端暂时异常，或当前网络链路不稳定；建议稍后重试，或更换 API 站点 / 渠道。"
        )
    else:
        friendly_message = "请检查模型、API 地址和 API 密钥是否正确。"

    friendly_message += f"\n\nAPI 地址示例：{_get_api_address_example(api_type)}"
    if raw_message:
        friendly_message += f"\n\n原始错误：\n{_wrap_error_text(raw_message)}"

    return friendly_message


def _show_api_error_dialog(parent, title: str, heading: str, details: str) -> None:
    from PyQt6.QtWidgets import QMessageBox

    from ui.secondary_pages.themed_message_box import show_error_dialog

    show_error_dialog(parent, title, heading, details, icon=QMessageBox.Icon.Critical)


def _show_api_success_dialog(parent, title: str, heading: str, details: str) -> None:
    from PyQt6.QtWidgets import QMessageBox

    from ui.secondary_pages.themed_message_box import show_error_dialog

    show_error_dialog(parent, title, heading, details, icon=QMessageBox.Icon.Information)


def _split_env_key(env_key: str) -> tuple[str, str, str]:
    normalized_key = (env_key or "").upper()
    scope = ""
    for prefix in ("OCR_", "COLOR_", "RENDER_"):
        if normalized_key.startswith(prefix):
            scope = prefix
            normalized_key = normalized_key[len(prefix):]
            break

    for provider in ("CUSTOM_OPENAI", "OPENAI", "GEMINI", "DEEPSEEK", "GROQ", "SAKURA"):
        provider_prefix = f"{provider}_"
        if normalized_key.startswith(provider_prefix):
            field = normalized_key[len(provider_prefix):]
            return scope, provider, field

    return scope, "", normalized_key


def _build_related_env_key(scope: str, provider: str, field: str) -> str | None:
    if not provider:
        return None
    return f"{scope}{provider}_{field}"


def _split_slot_field(field: str) -> tuple[str, int]:
    match = re.match(r"^(API_KEY|AUTH_KEY|TOKEN|API_BASE|BASE|MODEL)_(\d+)$", field or "")
    if not match:
        return field, 1
    try:
        return match.group(1), int(match.group(2))
    except ValueError:
        return match.group(1), 1


def _build_related_slot_env_key(scope: str, provider: str, field: str, slot_index: int) -> str | None:
    base_key = _build_related_env_key(scope, provider, field)
    return get_indexed_env_key(base_key, slot_index)


def _read_env_widget_value(self, env_key: str | None) -> str | None:
    if not env_key:
        return None
    pair = self.env_widgets.get(env_key)
    if not pair:
        return None
    return _get_env_widget_value(pair[1]).strip() or None


def _read_env_candidates(self, *env_keys: str | None) -> str | None:
    for env_key in env_keys:
        value = _read_env_widget_value(self, env_key)
        if value:
            return value
    return None


def _resolve_api_context(self, env_key: str, translator_key: str) -> tuple[str, str | None, str | None, str | None]:
    test_target = _detect_test_target(env_key, translator_key)
    scope, provider, field = _split_env_key(env_key)
    base_field, slot_index = _split_slot_field(field)

    api_key = None
    if base_field in ("API_KEY", "AUTH_KEY", "TOKEN"):
        api_key = _read_env_candidates(
            self,
            env_key,
            _build_related_slot_env_key("", provider, "API_KEY", slot_index) if scope else None,
            _build_related_slot_env_key("", provider, "AUTH_KEY", slot_index) if scope else None,
            _build_related_slot_env_key("", provider, "TOKEN", slot_index) if scope else None,
        )
    else:
        api_key = _read_env_candidates(
            self,
            *[
                _build_related_slot_env_key(scope, provider, candidate_field, slot_index)
                for candidate_field in ("API_KEY", "AUTH_KEY", "TOKEN")
            ],
            *(
                [
                    _build_related_slot_env_key("", provider, candidate_field, slot_index)
                    for candidate_field in ("API_KEY", "AUTH_KEY", "TOKEN")
                ]
                if scope
                else []
            ),
        )

    api_base = _read_env_candidates(
        self,
        *[
            _build_related_slot_env_key(scope, provider, candidate_field, slot_index)
            for candidate_field in ("API_BASE", "BASE")
        ],
        *(
            [
                _build_related_slot_env_key("", provider, candidate_field, slot_index)
                for candidate_field in ("API_BASE", "BASE")
            ]
            if scope
            else []
        ),
    )

    model = _read_env_candidates(
        self,
        _build_related_slot_env_key(scope, provider, "MODEL", slot_index),
        _build_related_slot_env_key("", provider, "MODEL", slot_index) if scope else None,
    )
    return test_target, api_key, api_base, model


def _test_target_status_identity(test_target: str) -> tuple[str, str] | None:
    normalized = (test_target or "").strip().lower()
    target_map = {
        "openai": ("translator", "openai"),
        "openai_hq": ("translator", "openai"),
        "gemini": ("translator", "gemini"),
        "gemini_hq": ("translator", "gemini"),
        "openai_ocr": ("ocr", "openai"),
        "gemini_ocr": ("ocr", "gemini"),
        "openai_colorizer": ("colorizer", "openai"),
        "gemini_colorizer": ("colorizer", "gemini"),
        "openai_renderer": ("renderer", "openai"),
        "gemini_renderer": ("renderer", "gemini"),
    }
    return target_map.get(normalized)


def _build_test_status_endpoint(
    self,
    env_key: str,
    test_target: str,
    api_key: str | None,
    api_base: str | None,
    model: str | None,
) -> APIEndpoint | None:
    identity = _test_target_status_identity(test_target)
    if not identity:
        return None

    feature, provider = identity
    _scope, _provider, field = _split_env_key(env_key)
    _base_field, slot_index = _split_slot_field(field)
    base_url = (api_base or _get_api_address_example(test_target)).rstrip("/")
    model_name = (model or "").strip()
    status_key = make_endpoint_status_key(
        feature,
        provider,
        slot_index,
        base_url,
        model_name,
        api_key=api_key,
    )
    return APIEndpoint(
        feature=feature,
        provider=provider,
        slot=slot_index,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        status_key=status_key,
        label=f"{provider} #{slot_index}",
    )


def _is_test_item_configured(test_target: str, api_key: str | None, api_base: str | None) -> bool:
    if str(api_key or "").strip():
        return True
    normalized = (test_target or "").strip().lower()
    if "sakura" in normalized:
        return bool(str(api_base or "").strip())
    return "openai" in normalized and is_openai_api_key_optional("", api_base or "")


def _get_current_translator_key(self) -> str:
    combo = getattr(self, "env_translation_feature_combo", None) or getattr(self, "translator_combo", None)
    if combo is not None:
        data = combo.currentData() if hasattr(combo, "currentData") else None
        if data:
            return str(data)
        display_map = self.controller.get_display_mapping("translator") or {}
        reverse_map = {v: k for k, v in display_map.items()}
        current_text = combo.currentText() if hasattr(combo, "currentText") else ""
        return reverse_map.get(current_text, str(current_text or "").lower())
    return _resolve_config_value(self.controller.config_service.get_config(), "translator.translator")


def _collect_api_test_items(self, section_key: str) -> list[dict]:
    section_scopes = {
        "translation": "",
        "ocr": "OCR_",
        "color": "COLOR_",
        "render": "RENDER_",
    }
    expected_scope = section_scopes.get(section_key)
    translator_key = _get_current_translator_key(self)
    items: list[dict] = []
    seen: set[str] = set()

    if section_key == "translation" and (translator_key or "").strip().lower() == "sakura":
        api_base = _read_env_widget_value(self, "SAKURA_API_BASE")
        if _is_test_item_configured("sakura", None, api_base):
            items.append(
                {
                    "label": _display_env_label(self, "SAKURA_API_BASE"),
                    "test_target": "sakura",
                    "api_key": None,
                    "api_base": api_base,
                    "model": None,
                    "endpoint": None,
                }
            )

    for key in list(self.env_widgets.keys()):
        scope, provider, field = _split_env_key(key)
        base_field, slot_index = _split_slot_field(field)
        if base_field not in ("API_KEY", "AUTH_KEY", "TOKEN"):
            continue
        if scope != expected_scope:
            continue

        test_target, api_key, api_base, model = _resolve_api_context(self, key, translator_key)
        if not _is_test_item_configured(test_target, api_key, api_base):
            continue
        status_endpoint = _build_test_status_endpoint(self, key, test_target, api_key, api_base, model)
        if status_endpoint is None:
            continue
        unique_key = f"{status_endpoint.feature}:{status_endpoint.provider}:{status_endpoint.slot}"
        if unique_key in seen:
            continue
        seen.add(unique_key)
        label_key = _build_related_env_key(scope, provider, base_field) or key
        items.append(
            {
                "label": _display_env_label(self, label_key, slot_index),
                "test_target": test_target,
                "api_key": api_key,
                "api_base": api_base,
                "model": model,
                "endpoint": status_endpoint,
            }
        )
    return items


def _format_api_batch_result_text(self, results: list[dict]) -> str:
    lines = []
    for item in results:
        if item.get("success"):
            continue
        state_text = self._t("API test unavailable")
        lines.append(f"[{state_text}] {item.get('label') or item.get('test_target')}")
        message = str(item.get("message") or "").strip()
        if message:
            lines.append(_wrap_error_text(message, width=100))
        lines.append("")
    return "\n".join(lines).rstrip() or self._t("No unavailable API")


def _show_api_batch_test_results(self, results: list[dict]) -> None:
    from PyQt6.QtWidgets import QMessageBox

    from ui.secondary_pages.themed_message_box import show_error_dialog

    available = sum(1 for item in results if item.get("success"))
    unavailable = len(results) - available
    heading = self._t(
        "API batch test summary",
        total=len(results),
        available=available,
        unavailable=unavailable,
    )
    show_error_dialog(
        self,
        self._t("API Batch Test Results"),
        heading,
        _format_api_batch_result_text(self, results),
        icon=QMessageBox.Icon.Information if unavailable == 0 else QMessageBox.Icon.Warning,
    )


def _run_api_batch_test(self, items: list[dict]):
    import asyncio

    from PyQt6.QtCore import QThread

    from ui.secondary_pages.themed_message_box import themed_information
    from ui.secondary_pages.themed_progress_dialog import create_progress_dialog
    from utils.asyncio_cleanup import shutdown_event_loop

    if not items:
        themed_information(self, self._t("API Batch Test"), self._t("No API channels to test"))
        return

    concurrency = 3
    progress = create_progress_dialog(
        self,
        self._t("API Batch Test"),
        self._t("Testing API channels", count=len(items), concurrency=concurrency),
        self._t("Cancel"),
    )
    progress.show()

    async def run_all_tests():
        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(item: dict) -> dict:
            async with semaphore:
                try:
                    success, message = await self.controller.test_api_connection_async(
                        item["test_target"],
                        item.get("api_key"),
                        item.get("api_base"),
                        item.get("model"),
                    )
                except Exception as exc:
                    success, message = False, str(exc)

                endpoint = item.get("endpoint")
                if endpoint is not None:
                    if success:
                        record_api_success(endpoint)
                    else:
                        record_api_failure(endpoint, Exception(str(message or "")))

                result = dict(item)
                result["success"] = success
                result["message"] = message
                return result

        return await asyncio.gather(*(run_one(item) for item in items))

    def run_test_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(run_all_tests())
        finally:
            shutdown_event_loop(loop, label="API batch test loop")

    class BatchTestThread(QThread):
        finished_signal = pyqtSignal(list)

        def run(self):
            try:
                self.finished_signal.emit(run_test_thread())
            except Exception as exc:
                fallback_results = []
                for item in items:
                    result = dict(item)
                    result["success"] = False
                    result["message"] = str(exc)
                    fallback_results.append(result)
                self.finished_signal.emit(fallback_results)

    def on_finished(results):
        progress.close()
        if progress.wasCanceled():
            return
        _show_api_batch_test_results(self, results)

    thread = BatchTestThread()
    thread.finished_signal.connect(on_finished)
    thread.start()
    self._api_batch_test_thread = thread


def on_test_current_api_section_clicked(self, section_key: str):
    flush_all_pending_env_vars(self)
    _run_api_batch_test(self, _collect_api_test_items(self, section_key))


def on_open_custom_api_params_file(self):
    """打开自定义 API 参数编辑器。"""
    from manga_translator.custom_api_params import (
        ensure_custom_api_params_file,
        get_custom_api_params_path,
    )

    try:
        config_path = ensure_custom_api_params_file(get_custom_api_params_path())
    except Exception as e:
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.warning(self, self._t("Error"), f"创建配置文件失败: {e}")
        return

    try:
        from ui.secondary_pages.custom_api_params_editor import (
            CustomApiParamsEditorDialog,
        )

        dialog = CustomApiParamsEditorDialog(config_path, t_func=self._t, parent=self)
        dialog.exec()
    except Exception as e:
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.warning(self, self._t("Error"), f"打开编辑器失败: {e}")


def on_test_api_clicked(self, key: str):
    """测试API连接。"""
    flush_all_pending_env_vars(self)
    import asyncio

    from PyQt6.QtCore import QThread

    from ui.secondary_pages.themed_progress_dialog import create_progress_dialog
    from utils.asyncio_cleanup import shutdown_event_loop

    if key not in self.env_widgets:
        return

    translator_key = _get_current_translator_key(self)
    test_target, api_key, api_base, model = _resolve_api_context(self, key, translator_key)
    status_endpoint = _build_test_status_endpoint(self, key, test_target, api_key, api_base, model)

    progress = create_progress_dialog(
        self,
        self._t("Testing"),
        self._t("Testing API connection, please wait..."),
        self._t("Cancel"),
    )
    progress.show()

    def run_test():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.controller.test_api_connection_async(test_target, api_key, api_base, model)
            )
        finally:
            shutdown_event_loop(loop, label="API test loop")

    class TestThread(QThread):
        finished_signal = pyqtSignal(bool, str)

        def run(self):
            try:
                success, message = run_test()
                self.finished_signal.emit(success, message)
            except Exception as e:
                self.finished_signal.emit(False, str(e))

    def on_test_finished(success, message):
        progress.close()
        if progress.wasCanceled():
            return
        if status_endpoint is not None:
            if success:
                record_api_success(status_endpoint)
            else:
                record_api_failure(status_endpoint, Exception(str(message or "")))
        if success:
            success_details = _wrap_error_text(message) if message else self._t("API connection test successful!")
            _show_api_success_dialog(
                self,
                self._t("Success"),
                self._t("API connection test successful!"),
                success_details,
            )
        else:
            friendly_message = _format_test_connection_error(test_target, message)
            _show_api_error_dialog(
                self,
                self._t("Error"),
                self._t("API connection test failed"),
                friendly_message,
            )

    test_thread = TestThread()
    test_thread.finished_signal.connect(on_test_finished)
    test_thread.start()
    self._test_thread = test_thread


def on_get_models_clicked(self, key: str):
    """获取可用模型列表。"""
    flush_all_pending_env_vars(self)
    import asyncio

    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QMessageBox

    from ui.secondary_pages.model_selector_dialog import ModelSelectorDialog
    from ui.secondary_pages.themed_progress_dialog import create_progress_dialog
    from utils.asyncio_cleanup import shutdown_event_loop

    translator_key = _get_current_translator_key(self)
    model_api_type, api_key, api_base, _ = _resolve_api_context(self, key, translator_key)

    progress = create_progress_dialog(
        self,
        self._t("Get Models"),
        self._t("Fetching models, please wait..."),
        self._t("Cancel"),
    )
    progress.show()

    def run_get_models():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.controller.get_available_models_async(model_api_type, api_key, api_base)
            )
        finally:
            shutdown_event_loop(loop, label="model fetch loop")

    class GetModelsThread(QThread):
        finished_signal = pyqtSignal(bool, list, str)

        def run(self):
            try:
                success, models, message = run_get_models()
                self.finished_signal.emit(success, models, message)
            except Exception as e:
                self.finished_signal.emit(False, [], str(e))

    def on_get_models_finished(success, models, message):
        progress.close()
        if progress.wasCanceled():
            return
        if success:
            if models:
                selected_model, ok = ModelSelectorDialog.get_model(
                    models,
                    self._t("Select Model"),
                    self._t("Available models:"),
                    parent=self,
                    t_func=self._t,
                )
                if ok and selected_model and key in self.env_widgets:
                    _, widget = self.env_widgets[key]
                    widget.setText(selected_model)
                    self.env_var_changed.emit(key, selected_model)
            else:
                QMessageBox.warning(self, self._t("Warning"), self._t("No models available"))
        else:
            friendly_message = _format_test_connection_error(model_api_type, message)
            _show_api_error_dialog(
                self,
                self._t("Error"),
                self._t("Failed to get models"),
                friendly_message,
            )

    get_models_thread = GetModelsThread()
    get_models_thread.finished_signal.connect(on_get_models_finished)
    get_models_thread.start()
    self._get_models_thread = get_models_thread


def refresh_preset_list(self):
    """刷新预设列表。"""
    if not hasattr(self, "preset_combo"):
        return

    current_text = self.preset_combo.currentText()
    current_index = self.preset_combo.currentIndex()

    self.preset_combo.blockSignals(True)
    self.preset_combo.clear()

    presets = self.controller.get_presets_list()
    if not presets:
        self.controller.save_preset("默认", copy_current=False)
        presets = self.controller.get_presets_list()

    if presets:
        self.preset_combo.addItems(presets)

        if current_text and current_text in presets:
            self.preset_combo.setCurrentText(current_text)
            self.preset_combo.blockSignals(False)
        else:
            new_index = min(current_index, len(presets) - 1)
            self.preset_combo.setCurrentIndex(new_index)
            new_preset = self.preset_combo.currentText()
            self.preset_combo.blockSignals(False)
            self._on_preset_changed(new_preset)
            return

    self.preset_combo.blockSignals(False)


def on_add_preset_clicked(self):
    """添加新预设。"""
    from PyQt6.QtWidgets import QMessageBox

    from ui.secondary_pages.themed_text_input_dialog import themed_get_text

    preset_name, ok = themed_get_text(
        self,
        title=self._t("Add Preset"),
        label=self._t("Enter preset name:"),
        ok_text=self._t("OK"),
        cancel_text=self._t("Cancel"),
    )

    if ok and preset_name:
        preset_name = preset_name.strip()
        if not preset_name:
            QMessageBox.warning(self, self._t("Warning"), self._t("Preset name cannot be empty"))
            return

        existing_presets = self.controller.get_presets_list()
        if preset_name in existing_presets:
            reply = QMessageBox.question(
                self,
                self._t("Confirm"),
                self._t("Preset '{name}' already exists. Overwrite?", name=preset_name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        success = self.controller.save_preset(preset_name, copy_current=False)
        if success:
            self._refresh_preset_list()
            self.preset_combo.setCurrentText(preset_name)
        else:
            QMessageBox.critical(self, self._t("Error"), self._t("Failed to create preset"))


def on_delete_preset_clicked(self):
    """删除选中的预设。"""
    from PyQt6.QtWidgets import QMessageBox

    preset_name = self.preset_combo.currentText()
    if not preset_name:
        QMessageBox.warning(self, self._t("Warning"), self._t("Please select a preset to delete"))
        return

    reply = QMessageBox.question(
        self,
        self._t("Confirm"),
        self._t("Are you sure you want to delete preset '{name}'?", name=preset_name),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )

    if reply == QMessageBox.StandardButton.Yes:
        success = self.controller.delete_preset(preset_name)
        if success:
            self._refresh_preset_list()
            QMessageBox.information(self, self._t("Success"), self._t("Preset deleted successfully"))
        else:
            QMessageBox.critical(self, self._t("Error"), self._t("Failed to delete preset"))


def on_preset_changed(self, new_preset_name: str):
    """切换预设时加载新预设。"""
    flush_all_pending_env_vars(self)
    if not new_preset_name:
        return

    old_preset_name = getattr(self, "_current_preset_name", "")
    if old_preset_name == new_preset_name:
        return

    if self._env_debounce_timer.isActive():
        self._env_debounce_timer.stop()
        for key, (label, widget) in self.env_widgets.items():
            current_value = _get_env_widget_value(widget)
            self.controller.save_env_var(key, current_value)

    if old_preset_name:
        existing_presets = self.controller.get_presets_list()
        if old_preset_name in existing_presets:
            self.controller.save_preset(old_preset_name, copy_current=True)

    success = self.controller.load_preset(new_preset_name)
    if success:
        self._current_preset_name = new_preset_name
        self.controller.config_service.set_current_preset(new_preset_name)

        current_env_values = self.config_service.load_env_vars()
        for key, (label, widget) in self.env_widgets.items():
            new_value = current_env_values.get(key, "")
            widget.blockSignals(True)
            _set_env_widget_value(widget, str(new_value) if new_value else "")
            if hasattr(widget, "setPlaceholderText"):
                widget.setPlaceholderText(self._get_env_default_placeholder(key))
            widget.blockSignals(False)
        self._refresh_env_api_groups()
        self._refresh_api_feature_selectors()


def update_output_path_display(self, path: str):
    """更新输出目录输入框显示。"""
    self.output_folder_input.setText(path)


def trigger_add_files(self):
    """触发添加文件对话框。"""
    last_dir = self.controller.get_last_open_dir()
    file_paths, _ = QFileDialog.getOpenFileNames(
        self,
        self._t("Add Files"),
        last_dir,
        "All Supported Files (*.png *.jpg *.jpeg *.bmp *.webp *.avif *.heic *.heif *.pdf *.epub *.cbz *.cbr *.zip);;"
        "Image Files (*.png *.jpg *.jpeg *.bmp *.webp *.avif *.heic *.heif);;"
        "PDF Files (*.pdf);;"
        "EPUB Files (*.epub);;"
        "Comic Book Archives (*.cbz *.cbr *.zip)",
    )
    if file_paths:
        self.controller.add_files(file_paths)
        new_dir = os.path.dirname(file_paths[0])
        self.controller.set_last_open_dir(new_dir)
