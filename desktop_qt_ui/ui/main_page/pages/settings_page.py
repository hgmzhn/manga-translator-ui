import json
import logging
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from theme_registry import THEME_OPTIONS


def _resolve_settings_tab_layout_file() -> str:
    """打包/开发环境通用地定位 settings_tab_layout.json。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "desktop_qt_ui", "ui", "main_page", "settings_tab_layout.json")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "settings_tab_layout.json",
    )


_SETTINGS_TAB_LAYOUT_FILE = _resolve_settings_tab_layout_file()


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

def create_settings_page(self) -> QWidget:
    page = QWidget()
    page.setObjectName("content_page_settings")
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    # Header card with title + config IO buttons
    header_card = QWidget()
    header_card.setObjectName("header_card")
    header_layout = QHBoxLayout(header_card)
    header_layout.setContentsMargins(12, 4, 12, 4)
    header_layout.setSpacing(8)

    title_col = QVBoxLayout()
    title_col.setSpacing(2)
    self.settings_page_title = QLabel(self._t("Settings Page Title"))
    self.settings_page_title.setObjectName("page_title")
    self.settings_page_subtitle = QLabel(
        self._t("Settings Page Subtitle")
    )
    self.settings_page_subtitle.setObjectName("page_subtitle")
    self.settings_page_subtitle.setWordWrap(True)
    title_col.addWidget(self.settings_page_title)
    title_col.addWidget(self.settings_page_subtitle)
    header_layout.addLayout(title_col, 1)

    self.export_config_button = QPushButton(self._t("Export Config"))
    self.import_config_button = QPushButton(self._t("Import Config"))
    self.export_config_button.setProperty("chipButton", True)
    self.import_config_button.setProperty("chipButton", True)
    header_layout.addWidget(self.export_config_button)
    header_layout.addWidget(self.import_config_button)
    page_layout.addWidget(header_card)

    self.export_config_button.clicked.connect(self.controller.export_config)
    self.import_config_button.clicked.connect(self.controller.import_config)

    # --- 主体区域：左侧 tabs + 右侧描述面板 ---
    settings_body_splitter = QSplitter(Qt.Orientation.Horizontal)
    settings_body_splitter.setObjectName("settings_body_splitter")
    page_layout.addWidget(settings_body_splitter, 1)

    self.settings_tabs = QTabWidget()
    self.settings_tabs.setObjectName("settings_tabs")
    settings_body_splitter.addWidget(self.settings_tabs)

    # 右侧描述面板
    desc_panel = QWidget()
    desc_panel.setObjectName("settings_desc_panel")
    desc_panel_layout = QVBoxLayout(desc_panel)
    desc_panel_layout.setContentsMargins(16, 16, 16, 16)
    desc_panel_layout.setSpacing(12)

    self.settings_desc_header_label = QLabel(self._t("Settings Desc Header"))
    self.settings_desc_header_label.setObjectName("settings_desc_header")
    desc_panel_layout.addWidget(self.settings_desc_header_label)

    desc_divider = QFrame()
    desc_divider.setFrameShape(QFrame.Shape.HLine)
    desc_divider.setObjectName("settings_desc_divider")
    desc_panel_layout.addWidget(desc_divider)

    self.settings_desc_name = QLabel("")
    self.settings_desc_name.setObjectName("settings_desc_name")
    self.settings_desc_name.setWordWrap(True)
    desc_panel_layout.addWidget(self.settings_desc_name)

    self.settings_desc_key = QLabel("")
    self.settings_desc_key.setObjectName("settings_desc_key")
    self.settings_desc_key.setWordWrap(True)
    self.settings_desc_key.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    desc_panel_layout.addWidget(self.settings_desc_key)

    self.settings_desc_text = QLabel(self._t("Settings Desc Placeholder"))
    self.settings_desc_text.setObjectName("settings_desc_text")
    self.settings_desc_text.setWordWrap(True)
    self.settings_desc_text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    desc_panel_layout.addWidget(self.settings_desc_text, 1)

    settings_body_splitter.addWidget(desc_panel)

    settings_body_splitter.setStretchFactor(0, 3)
    settings_body_splitter.setStretchFactor(1, 1)
    settings_body_splitter.setSizes([700, 280])
    settings_body_splitter.setCollapsible(0, False)
    settings_body_splitter.setCollapsible(1, True)

    self.tab_frames = {}
    self.settings_tab_layout = _load_reclassify_settings_layout()
    self._settings_tabs_use_reclassify = bool(self.settings_tab_layout)
    self.settings_tab_title_keys = []

    if self._settings_tabs_use_reclassify:
        for tab in self.settings_tab_layout:
            tab_id = tab["id"]
            tab_title_key = str(tab.get("title", "")).strip() or "Group"
            tab_display_name = self._t(tab_title_key)

            tab_content_widget = QWidget()
            tab_layout = QVBoxLayout(tab_content_widget)
            tab_layout.setContentsMargins(0, 0, 0, 0)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setObjectName("settings_scroll_area")
            scroll_content = QWidget()
            scroll_content.setObjectName("settings_scroll_content")
            scroll.setWidget(scroll_content)

            form = QFormLayout(scroll_content)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(12)
            form.setContentsMargins(16, 14, 16, 14)

            tab_layout.addWidget(scroll)
            self.settings_tabs.addTab(tab_content_widget, tab_display_name)
            self.settings_tab_title_keys.append(tab_title_key)
            self.tab_frames[tab_id] = scroll_content
    else:
        tabs_config = [
            ("Application Settings", self._t("Application Settings")),
            ("Basic Settings", self._t("Basic Settings")),
            ("Advanced Settings", self._t("Advanced Settings")),
            ("Options", self._t("Options")),
        ]
        for tab_key, tab_display_name in tabs_config:
            tab_content_widget = QWidget()
            tab_layout = QVBoxLayout(tab_content_widget)
            tab_layout.setContentsMargins(0, 0, 0, 0)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll_content = QWidget()
            scroll.setWidget(scroll_content)

            form = QFormLayout(scroll_content)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)

            tab_layout.addWidget(scroll)
            self.settings_tabs.addTab(tab_content_widget, tab_display_name)
            self.settings_tab_title_keys.append(tab_key)
            self.tab_frames[tab_key] = scroll_content

    self._populate_theme_combo()
    self._populate_language_combo()
    return page


