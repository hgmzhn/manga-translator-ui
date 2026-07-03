import json
import logging
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CardWidget, CaptionLabel, HorizontalSeparator, PopUpAniStackedWidget, PushButton, ScrollArea, SegmentedWidget, StrongBodyLabel, TitleLabel


def _resolve_settings_tab_layout_file() -> str:
    """打包/开发环境通用地定位 settings_tab_layout.json。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "desktop_qt_ui", "ui", "main_page", "settings_tab_layout.json")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "settings_tab_layout.json",
    )


_SETTINGS_TAB_LAYOUT_FILE = _resolve_settings_tab_layout_file()


def _make_settings_route_key(raw_key: str) -> str:
    suffix = "".join(
        char.lower() if char.isascii() and char.isalnum() else "_"
        for char in str(raw_key or "")
    ).strip("_")
    return f"settings_{suffix or 'tab'}"


def _load_reclassify_settings_layout():
    """从 ui/main_page/settings_tab_layout.json 加载设置页分类排序布局。"""
    try:
        with open(_SETTINGS_TAB_LAYOUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("tabs", [])
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "加载 settings_tab_layout.json 失败 (%s): %s", _SETTINGS_TAB_LAYOUT_FILE, exc
        )
        return []


def _create_settings_tab_page() -> tuple[QWidget, QWidget]:
    tab_content_widget = QWidget()
    tab_layout = QVBoxLayout(tab_content_widget)
    tab_layout.setContentsMargins(0, 0, 0, 0)
    tab_layout.setSpacing(0)

    scroll = ScrollArea()
    scroll.setWidgetResizable(True)
    tab_layout.addWidget(scroll)

    scroll_content = QWidget()
    scroll_content_layout = QVBoxLayout(scroll_content)
    scroll_content_layout.setContentsMargins(16, 14, 16, 16)
    scroll_content_layout.setSpacing(8)

    settings_rows_widget = QWidget(scroll_content)
    settings_rows_layout = QVBoxLayout(settings_rows_widget)
    settings_rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    settings_rows_layout.setSpacing(8)
    settings_rows_layout.setContentsMargins(0, 0, 0, 0)
    scroll_content_layout.addWidget(settings_rows_widget, 1)

    scroll.setWidget(scroll_content)
    scroll.enableTransparentBackground()
    return tab_content_widget, settings_rows_widget


def create_settings_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    header_card = CardWidget()
    header_layout = QHBoxLayout(header_card)
    header_layout.setContentsMargins(16, 12, 16, 12)
    header_layout.setSpacing(8)

    title_col = QVBoxLayout()
    title_col.setSpacing(2)
    self.settings_page_title = TitleLabel(self._t("Settings Page Title"))
    self.settings_page_subtitle = BodyLabel(
        self._t("Settings Page Subtitle")
    )
    self.settings_page_subtitle.setWordWrap(True)
    title_col.addWidget(self.settings_page_title)
    title_col.addWidget(self.settings_page_subtitle)
    header_layout.addLayout(title_col, 1)

    self.export_config_button = PushButton(self._t("Export Config"))
    self.import_config_button = PushButton(self._t("Import Config"))
    header_layout.addWidget(self.export_config_button)
    header_layout.addWidget(self.import_config_button)
    page_layout.addWidget(header_card)

    self.export_config_button.clicked.connect(self.controller.export_config)
    self.import_config_button.clicked.connect(self.controller.import_config)

    settings_body = QWidget()
    settings_body_layout = QHBoxLayout(settings_body)
    settings_body_layout.setContentsMargins(12, 12, 12, 12)
    settings_body_layout.setSpacing(12)
    page_layout.addWidget(settings_body, 1)

    settings_tabs_panel = QWidget()
    settings_tabs_layout = QVBoxLayout(settings_tabs_panel)
    settings_tabs_layout.setContentsMargins(0, 0, 0, 0)
    settings_tabs_layout.setSpacing(8)

    self.settings_tabs = SegmentedWidget(settings_tabs_panel)
    self.settings_stack = PopUpAniStackedWidget(settings_tabs_panel)
    self.settings_tab_routes = []
    self.settings_tab_route_indexes = {}
    self.settings_tab_route_widgets = {}
    settings_tabs_layout.addWidget(self.settings_tabs)
    settings_tabs_layout.addWidget(self.settings_stack, 1)

    def switch_settings_tab(route_key: str):
        widget = self.settings_tab_route_widgets.get(route_key)
        if widget is not None:
            self.settings_stack.setCurrentWidget(widget)

    self.settings_tabs.currentItemChanged.connect(switch_settings_tab)
    settings_body_layout.addWidget(settings_tabs_panel, 3)

    desc_panel = QWidget()
    desc_panel_layout = QVBoxLayout(desc_panel)
    desc_panel_layout.setContentsMargins(16, 16, 16, 16)
    desc_panel_layout.setSpacing(12)

    self.settings_desc_header_label = StrongBodyLabel(self._t("Settings Desc Header"))
    desc_panel_layout.addWidget(self.settings_desc_header_label)

    desc_panel_layout.addWidget(HorizontalSeparator())

    self.settings_desc_name = StrongBodyLabel("")
    self.settings_desc_name.setWordWrap(True)
    desc_panel_layout.addWidget(self.settings_desc_name)

    self.settings_desc_key = CaptionLabel("")
    self.settings_desc_key.setWordWrap(True)
    self.settings_desc_key.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    desc_panel_layout.addWidget(self.settings_desc_key)

    self.settings_desc_text = BodyLabel(self._t("Settings Desc Placeholder"))
    self.settings_desc_text.setWordWrap(True)
    self.settings_desc_text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    desc_panel_layout.addWidget(self.settings_desc_text, 1)

    settings_body_layout.addWidget(desc_panel, 1)

    self.tab_frames = {}
    self.settings_tab_layout = _load_reclassify_settings_layout()
    self._settings_tabs_use_reclassify = bool(self.settings_tab_layout)
    self.settings_tab_title_keys = []
    self.settings_tab_title_key_by_route = {}

    def add_settings_tab(route_key: str, widget: QWidget, text: str):
        index = self.settings_stack.count()
        self.settings_stack.addWidget(widget)
        self.settings_tab_routes.append(route_key)
        self.settings_tab_route_indexes[route_key] = index
        self.settings_tab_route_widgets[route_key] = widget
        self.settings_tabs.addItem(route_key, text)
        if index == 0:
            self.settings_tabs.setCurrentItem(route_key)
            self.settings_stack.setCurrentWidget(widget)

    if self._settings_tabs_use_reclassify:
        for tab in self.settings_tab_layout:
            tab_id = tab["id"]
            tab_title_key = str(tab.get("title", "")).strip() or "Group"
            tab_display_name = self._t(tab_title_key)

            tab_content_widget, form_host = _create_settings_tab_page()
            route_key = _make_settings_route_key(tab_id)
            add_settings_tab(route_key, tab_content_widget, tab_display_name)
            self.settings_tab_title_keys.append(tab_title_key)
            self.settings_tab_title_key_by_route[route_key] = tab_title_key
            self.tab_frames[tab_id] = form_host
    else:
        tabs_config = [
            ("Application Settings", self._t("Application Settings")),
            ("Basic Settings", self._t("Basic Settings")),
            ("Advanced Settings", self._t("Advanced Settings")),
            ("Options", self._t("Options")),
        ]
        for tab_key, tab_display_name in tabs_config:
            tab_content_widget, form_host = _create_settings_tab_page()
            route_key = _make_settings_route_key(tab_key)
            add_settings_tab(route_key, tab_content_widget, tab_display_name)
            self.settings_tab_title_keys.append(tab_key)
            self.settings_tab_title_key_by_route[route_key] = tab_key
            self.tab_frames[tab_key] = form_host

    self._populate_theme_combo()
    self._populate_language_combo()
    return page
