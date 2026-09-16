import json
import logging

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    HorizontalSeparator,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    SimpleCardWidget,
    StrongBodyLabel,
    TitleLabel,
)

from utils.resource_helper import resource_path
from ui.widgets.wheel_filter import NoWheelComboBox


_MANAGE_PARAMETER_PROFILES_ITEM = "__manage_parameter_profiles__"


def _show_parameter_profile_error(self, fallback_key: str) -> None:
    detail = ""
    getter = getattr(self.controller, "get_parameter_profile_error", None)
    if callable(getter):
        detail = str(getter() or "").strip()
    QMessageBox.warning(
        self._dialog_parent(),
        self._t("Error"),
        detail or self._t(fallback_key),
    )


def refresh_parameter_profiles(self, *_args) -> None:
    """Refresh the named parameter-profile selector without firing a load."""
    combo = getattr(self, "parameter_profile_combo", None)
    if combo is None:
        return

    try:
        profiles = list(self.controller.get_parameter_profiles())
        current_name = self.controller.get_current_parameter_profile()
    except Exception as exc:
        logging.getLogger(__name__).warning("刷新参数配置列表失败: %s", exc)
        return

    blocker = QSignalBlocker(combo)
    try:
        combo.clear()
        for profile_name in profiles:
            combo.addItem(profile_name, profile_name)
        combo.addItem(
            self._t("Manage Parameter Profiles"),
            _MANAGE_PARAMETER_PROFILES_ITEM,
        )
        current_index = combo.findData(current_name)
        if current_index < 0 and profiles:
            current_index = combo.findData("default")
        if current_index >= 0:
            combo.setCurrentIndex(current_index)
    finally:
        del blocker


def on_parameter_profile_selected(self, index: int) -> None:
    if getattr(self, "_refreshing_parameter_profiles", False):
        return
    combo = getattr(self, "parameter_profile_combo", None)
    if combo is None or index < 0:
        return

    profile_name = combo.itemData(index)
    if profile_name == _MANAGE_PARAMETER_PROFILES_ITEM:
        self.refresh_parameter_profiles()
        self._open_parameter_profile_manager()
        return
    if not profile_name:
        return
    if profile_name == self.controller.get_current_parameter_profile():
        return

    if not self.controller.load_parameter_profile(str(profile_name)):
        _show_parameter_profile_error(self, "Failed to load parameter profile")
    self.refresh_parameter_profiles()


def create_parameter_profile_from_current(self, parent=None) -> bool:
    # QPushButton.clicked emits a boolean when this function is connected
    # directly; keep that signal payload from being mistaken for a QWidget.
    if isinstance(parent, bool):
        parent = None
    parent = parent or self._dialog_parent()
    name, accepted = QInputDialog.getText(
        parent,
        self._t("Create Parameter Profile"),
        self._t("Enter parameter profile name:"),
    )
    if not accepted:
        return False

    name = str(name or "").strip()
    if not name:
        QMessageBox.warning(
            parent,
            self._t("Warning"),
            self._t("Parameter profile name cannot be empty"),
        )
        return False

    overwrite = False
    if name in self.controller.get_parameter_profiles():
        reply = QMessageBox.question(
            parent,
            self._t("Confirm"),
            self._t(
                "Parameter profile '{name}' already exists. Overwrite?",
                name=name,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        overwrite = True

    if not self.controller.create_parameter_profile(name, overwrite=overwrite):
        _show_parameter_profile_error(self, "Failed to create parameter profile")
        return False

    self.refresh_parameter_profiles()
    return True


class _ParameterProfileManagerDialog(QDialog):
    """Small manager for creating, renaming, and deleting parameter profiles."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.setWindowTitle(self.view._t("Parameter Profile Management"))
        self.setMinimumSize(480, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        hint = QLabel(self.view._t("Parameter Profile Management Hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.profile_list = QListWidget(self)
        layout.addWidget(self.profile_list, 1)

        action_layout = QHBoxLayout()
        self.create_button = PushButton(self.view._t("Create from Current"), self)
        self.rename_button = PushButton(self.view._t("Rename Parameter Profile"), self)
        self.delete_button = PushButton(self.view._t("Delete Parameter Profile"), self)
        action_layout.addWidget(self.create_button)
        action_layout.addWidget(self.rename_button)
        action_layout.addWidget(self.delete_button)
        layout.addLayout(action_layout)

        self.close_button = PushButton(self.view._t("Close"), self)
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignRight)

        self.create_button.clicked.connect(self._create_profile)
        self.rename_button.clicked.connect(self._rename_profile)
        self.delete_button.clicked.connect(self._delete_profile)
        self.refresh()

    def refresh(self):
        profiles = list(self.view.controller.get_parameter_profiles())
        current_name = self.view.controller.get_current_parameter_profile()
        self.profile_list.clear()
        self.profile_list.addItems(profiles)
        current_items = self.profile_list.findItems(
            current_name, Qt.MatchFlag.MatchExactly
        )
        if current_items:
            self.profile_list.setCurrentRow(self.profile_list.row(current_items[0]))

    def _selected_name(self) -> str:
        item = self.profile_list.currentItem()
        return item.text().strip() if item else ""

    def _create_profile(self):
        if create_parameter_profile_from_current(self.view, self):
            self.refresh()

    def _rename_profile(self):
        old_name = self._selected_name()
        if not old_name:
            QMessageBox.warning(
                self,
                self.view._t("Warning"),
                self.view._t("Please select a parameter profile to rename"),
            )
            return
        if old_name == "default":
            QMessageBox.warning(
                self,
                self.view._t("Warning"),
                self.view._t("Default parameter profile cannot be renamed"),
            )
            return

        new_name, accepted = QInputDialog.getText(
            self,
            self.view._t("Rename Parameter Profile"),
            self.view._t("Enter parameter profile name:"),
            text=old_name,
        )
        if not accepted:
            return
        new_name = str(new_name or "").strip()
        if not new_name:
            QMessageBox.warning(
                self,
                self.view._t("Warning"),
                self.view._t("Parameter profile name cannot be empty"),
            )
            return
        if new_name in self.view.controller.get_parameter_profiles():
            QMessageBox.warning(
                self,
                self.view._t("Warning"),
                self.view._t(
                    "Parameter profile '{name}' already exists.", name=new_name
                ),
            )
            return
        if not self.view.controller.rename_parameter_profile(old_name, new_name):
            _show_parameter_profile_error(
                self.view, "Failed to rename parameter profile"
            )
            return
        self.view.refresh_parameter_profiles()
        self.refresh()

    def _delete_profile(self):
        profile_name = self._selected_name()
        if not profile_name:
            QMessageBox.warning(
                self,
                self.view._t("Warning"),
                self.view._t("Please select a parameter profile to delete"),
            )
            return
        if profile_name == "default":
            QMessageBox.warning(
                self,
                self.view._t("Warning"),
                self.view._t("Default parameter profile cannot be deleted"),
            )
            return

        reply = QMessageBox.question(
            self,
            self.view._t("Confirm"),
            self.view._t(
                "Are you sure you want to delete parameter profile '{name}'?",
                name=profile_name,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self.view.controller.delete_parameter_profile(profile_name):
            _show_parameter_profile_error(
                self.view, "Failed to delete parameter profile"
            )
            return
        self.view.refresh_parameter_profiles()
        self.refresh()


def open_parameter_profile_manager(self) -> None:
    dialog = _ParameterProfileManagerDialog(self, self._dialog_parent())
    dialog.exec()


def _resolve_settings_tab_layout_file() -> str:
    """打包/开发环境通用地定位 settings_tab_layout.json。"""
    return resource_path("desktop_qt_ui/ui/main_page/settings_tab_layout.json")


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

    scroll = ScrollArea(tab_content_widget)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tab_layout.addWidget(scroll)

    settings_rows_widget = QWidget(scroll)
    settings_rows_layout = QVBoxLayout(settings_rows_widget)
    settings_rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    settings_rows_layout.setSpacing(8)
    settings_rows_layout.setContentsMargins(0, 8, 10, 8)
    settings_rows_layout.addStretch(1)

    scroll.setWidget(settings_rows_widget)
    scroll.enableTransparentBackground()
    return tab_content_widget, settings_rows_widget


def create_settings_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(14)

    header = QWidget(page)
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(10)

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
    page_layout.addWidget(header)

    self.export_config_button.clicked.connect(self.controller.export_config)
    self.import_config_button.clicked.connect(self.controller.import_config)

    profile_bar = QWidget(page)
    profile_layout = QHBoxLayout(profile_bar)
    profile_layout.setContentsMargins(0, 0, 0, 0)
    profile_layout.setSpacing(8)
    self.parameter_profile_label = StrongBodyLabel(
        self._t("Parameter Profile:")
    )
    self.parameter_profile_combo = NoWheelComboBox(profile_bar)
    self.parameter_profile_combo.setMinimumWidth(220)
    self.parameter_profile_create_button = PushButton(
        self._t("Create from Current"), profile_bar
    )
    self.parameter_profile_manage_button = PushButton(
        self._t("Manage Parameter Profiles"), profile_bar
    )
    profile_layout.addWidget(self.parameter_profile_label)
    profile_layout.addWidget(self.parameter_profile_combo)
    profile_layout.addWidget(self.parameter_profile_create_button)
    profile_layout.addWidget(self.parameter_profile_manage_button)
    profile_layout.addStretch(1)
    page_layout.addWidget(profile_bar)

    self.parameter_profile_combo.currentIndexChanged.connect(
        self._on_parameter_profile_selected
    )
    self.parameter_profile_create_button.clicked.connect(
        self._create_parameter_profile_from_current
    )
    self.parameter_profile_manage_button.clicked.connect(
        self._open_parameter_profile_manager
    )
    self.refresh_parameter_profiles()

    settings_body = QWidget(page)
    settings_body_layout = QHBoxLayout(settings_body)
    settings_body_layout.setContentsMargins(0, 0, 0, 0)
    settings_body_layout.setSpacing(14)
    page_layout.addWidget(settings_body, 1)

    settings_tabs_panel = QWidget(settings_body)
    settings_tabs_layout = QVBoxLayout(settings_tabs_panel)
    settings_tabs_layout.setContentsMargins(0, 0, 0, 0)
    settings_tabs_layout.setSpacing(10)

    self.settings_tabs = SegmentedWidget(settings_tabs_panel)
    self.settings_pages_panel = QWidget(settings_tabs_panel)
    self.settings_pages_layout = QVBoxLayout(self.settings_pages_panel)
    self.settings_pages_layout.setContentsMargins(0, 0, 0, 0)
    self.settings_pages_layout.setSpacing(0)
    self.settings_tab_routes = []
    self.settings_tab_route_indexes = {}
    self.settings_tab_route_widgets = {}
    settings_tabs_layout.addWidget(self.settings_tabs)
    settings_tabs_layout.addWidget(self.settings_pages_panel, 1)

    def switch_settings_tab(route_key: str):
        for current_route, widget in self.settings_tab_route_widgets.items():
            widget.setVisible(current_route == route_key)

    self.settings_tabs.currentItemChanged.connect(switch_settings_tab)
    settings_body_layout.addWidget(settings_tabs_panel, 3)

    desc_panel = QWidget(settings_body)
    desc_panel_layout = QVBoxLayout(desc_panel)
    desc_panel_layout.setContentsMargins(10, 8, 0, 8)
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
        index = len(self.settings_tab_routes)
        self.settings_pages_layout.addWidget(widget)
        widget.setVisible(index == 0)
        self.settings_tab_routes.append(route_key)
        self.settings_tab_route_indexes[route_key] = index
        self.settings_tab_route_widgets[route_key] = widget
        self.settings_tabs.addItem(route_key, text)
        if index == 0:
            self.settings_tabs.setCurrentItem(route_key)

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

    return page
