from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from git_update_helpers import (
    GIT_MIRRORS,
    current_commit,
    git_executable,
    mirror_index,
    remote_url,
    set_origin_url,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from theme_registry import THEME_OPTIONS
from ui.secondary_pages.fluent_dialog import normalize_dialog_parent
from ui.secondary_pages.themed_message_box import _apply_flexible_size
from ui.widgets.toggle_switch import ToggleSwitch
from utils.app_version import format_version_label
from utils.resource_helper import resource_path

PROJECT_ROOT = Path(resource_path("."))
GITHUB_REPOSITORY_URL = "https://github.com/hgmzhn/manga-translator-ui"
GITHUB_COMMIT_HASH = os.environ.get("GITHUB_SHA") or current_commit(PROJECT_ROOT)
WIKI_URL = "https://hgmzhn.github.io/manga-translator-ui/zh/"


def _open_about_directory(self, relative_path: str):
    directory = resource_path(relative_path)
    os.makedirs(directory, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(directory))


MIRROR_LABEL_KEYS = ("GitHub Official", "Gitee Mirror", "GitCode Mirror")


def _populate_about_mirror_combo(self):
    self.about_update_mirror_combo.blockSignals(True)
    try:
        self.about_update_mirror_combo.clear()
        for label_key, (_, _, mirror_url) in zip(MIRROR_LABEL_KEYS, GIT_MIRRORS):
            self.about_update_mirror_combo.addItem(self._t(label_key), mirror_url)
        current = remote_url(PROJECT_ROOT, executable=git_executable(PROJECT_ROOT))
        self.about_update_mirror_combo.setCurrentIndex(mirror_index(current))
    finally:
        self.about_update_mirror_combo.blockSignals(False)


def _on_about_mirror_changed(self, index: int):
    if index < 0:
        return
    mirror_url = self.about_update_mirror_combo.itemData(index)
    if not mirror_url:
        return
    executable = git_executable(PROJECT_ROOT)
    if set_origin_url(PROJECT_ROOT, mirror_url, executable=executable):
        self._set_about_update_status(
            "Update Source Changed",
            name=self.about_update_mirror_combo.currentText(),
        )
    else:
        self._set_about_update_status("Update Source Switch Failed")

BRANCH_LABEL_KEYS = ("Main Branch", "Beta Branch")
BRANCH_VALUES = ("main", "beta")


def _populate_about_branch_combo(self):
    self.about_update_branch_combo.blockSignals(True)
    try:
        self.about_update_branch_combo.clear()
        for label_key, branch in zip(BRANCH_LABEL_KEYS, BRANCH_VALUES):
            self.about_update_branch_combo.addItem(self._t(label_key), branch)
        selected = getattr(self, "_update_branch", "main")
        index = BRANCH_VALUES.index(selected) if selected in BRANCH_VALUES else 0
        self.about_update_branch_combo.setCurrentIndex(index)
    finally:
        self.about_update_branch_combo.blockSignals(False)


def _on_about_branch_changed(self, index: int):
    if 0 <= index < len(BRANCH_VALUES):
        self._update_branch = BRANCH_VALUES[index]
        self._set_about_update_status(
            "Update Branch Changed",
            name=self.about_update_branch_combo.currentText(),
        )


def _open_about_url(self, url: str):
    QDesktopServices.openUrl(QUrl(url))


def _populate_about_theme_combo(self):
    config = self.config_service.get_config()
    self.about_theme_combo.blockSignals(True)
    try:
        self.about_theme_combo.clear()
        selected_index = 0
        for index, (theme_key, theme_label) in enumerate(THEME_OPTIONS):
            self.about_theme_combo.addItem(self._t(theme_label), theme_key)
            if config.app.theme == theme_key:
                selected_index = index
        self.about_theme_combo.setCurrentIndex(selected_index)
    finally:
        self.about_theme_combo.blockSignals(False)


def _populate_about_language_combo(self):
    current_language = self.config_service.get_config().app.ui_language
    self.about_language_combo.blockSignals(True)
    try:
        self.about_language_combo.clear()
        selected_index = 0
        if self.i18n:
            for index, (locale_code, locale_info) in enumerate(
                self.i18n.get_available_locales().items()
            ):
                self.about_language_combo.addItem(locale_info.name, locale_code)
                if locale_code == current_language:
                    selected_index = index
        if self.about_language_combo.count():
            self.about_language_combo.setCurrentIndex(selected_index)
    finally:
        self.about_language_combo.blockSignals(False)


def _create_section_card(title: str, subtitle: str, icon) -> tuple[CardWidget, QVBoxLayout]:
    card = CardWidget()
    card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(16)

    header = QHBoxLayout()
    header.setSpacing(12)
    icon_widget = IconWidget(icon, card)
    icon_widget.setFixedSize(22, 22)
    header.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignTop)

    text_layout = QVBoxLayout()
    text_layout.setSpacing(3)
    title_label = StrongBodyLabel(title, card)
    subtitle_label = CaptionLabel(subtitle, card)
    subtitle_label.setWordWrap(True)
    card._about_title_label = title_label
    card._about_subtitle_label = subtitle_label
    text_layout.addWidget(title_label)
    text_layout.addWidget(subtitle_label)
    header.addLayout(text_layout, 1)
    layout.addLayout(header)
    return card, layout


def _add_setting_row(layout: QGridLayout, row: int, title: str, subtitle: str, control: QWidget):
    labels = QVBoxLayout()
    labels.setSpacing(2)
    title_label = BodyLabel(title)
    subtitle_label = CaptionLabel(subtitle)
    subtitle_label.setWordWrap(True)
    labels.addWidget(title_label)
    labels.addWidget(subtitle_label)
    layout.addLayout(labels, row, 0)
    layout.addWidget(control, row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

def _add_resource_row(self, layout: QGridLayout, row: int, title_key: str, path: str):
    title_label = BodyLabel(self._t(title_key))
    path_label = CaptionLabel(resource_path(path))
    labels = QVBoxLayout()
    labels.setSpacing(2)
    labels.addWidget(title_label)
    labels.addWidget(path_label)
    layout.addLayout(labels, row, 0)

    open_button = PushButton(self._t("Open Directory"))
    open_button.setIcon(FIF.FOLDER)
    open_button.clicked.connect(
        lambda checked=False, relative_path=path: self._open_about_directory(relative_path)
    )
    layout.addWidget(
        open_button,
        row,
        1,
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
    )
    self.about_resource_rows.append((title_label, path_label, open_button, title_key))


def _add_about_link(self, layout: QHBoxLayout, title_key: str, url: str):
    link_button = PushButton(self._t(title_key))
    link_button.setIcon(FIF.LINK)
    link_button.clicked.connect(
        lambda checked=False, target_url=url: self._open_about_url(target_url)
    )
    self.about_link_buttons.append((link_button, title_key))
    layout.addWidget(link_button)


def create_about_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(28, 26, 28, 24)
    page_layout.setSpacing(18)


    hero = CardWidget(page)
    hero.setObjectName("aboutHeroCard")
    hero_layout = QHBoxLayout(hero)
    hero_layout.setContentsMargins(24, 22, 24, 22)
    hero_layout.setSpacing(18)

    application = QApplication.instance()
    application_icon = application.windowIcon() if application is not None else FIF.INFO
    app_icon = IconWidget(application_icon, hero)
    app_icon.setFixedSize(64, 64)
    hero_layout.addWidget(app_icon, 0, Qt.AlignmentFlag.AlignVCenter)

    app_text = QVBoxLayout()
    app_text.setSpacing(5)
    self.about_app_name = SubtitleLabel(self._t("Manga Translator"), hero)
    self.about_app_description = BodyLabel(
        self._t("About Application Description"),
        hero,
    )
    self.about_app_description.setWordWrap(True)
    app_text.addWidget(self.about_app_name)
    app_text.addWidget(self.about_app_description)
    hero_layout.addLayout(app_text, 1)

    self.about_version_label = StrongBodyLabel(
        self._t(
            "Current Version: {version}",
            version=format_version_label(self.app_version) or self._t("Unknown"),
        ),
    )
    self.about_version_label.setAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    hero_layout.addWidget(self.about_version_label)
    page_layout.addWidget(hero)
    content = QHBoxLayout()
    content.setSpacing(18)
    self.about_resource_rows = []
    self.about_link_buttons = []

    preferences_card, preferences_layout = _create_section_card(
        self._t("Application Preferences"),
        self._t("Settings Page Subtitle"),
        FIF.SETTING,
    )
    self.about_preferences_card = preferences_card
    preferences_grid = QGridLayout()
    preferences_grid.setHorizontalSpacing(20)
    preferences_grid.setVerticalSpacing(18)
    preferences_grid.setColumnStretch(0, 1)

    self.about_theme_combo = ComboBox()
    self.about_theme_combo.setMinimumWidth(210)
    self.about_theme_combo.currentIndexChanged.connect(
        lambda index: self.theme_change_requested.emit(
            self.about_theme_combo.itemData(index)
        )
        if index >= 0 and self.about_theme_combo.itemData(index)
        else None
    )
    _add_setting_row(
        preferences_grid,
        0,
        self._t("Theme:").rstrip(":："),
        self._t("desc_app_theme"),
        self.about_theme_combo,
    )

    self.about_language_combo = ComboBox()
    self.about_language_combo.setMinimumWidth(210)
    self.about_language_combo.currentIndexChanged.connect(
        lambda index: self.language_change_requested.emit(
            self.about_language_combo.itemData(index)
        )
        if index >= 0 and self.about_language_combo.itemData(index)
        else None
    )
    _add_setting_row(
        preferences_grid,
        1,
        self._t("Language:").rstrip(":："),
        self._t("desc_app_ui_language"),
        self.about_language_combo,
    )

    self.about_auto_check_checkbox = ToggleSwitch(
        checked=bool(self.config_service.get_config().app.auto_check_updates)
    )
    self.about_auto_check_checkbox.setOnText(self._t("Yes"))
    self.about_auto_check_checkbox.setOffText(self._t("No"))
    self.about_auto_check_checkbox.checkedChanged.connect(
        lambda checked: self.controller.update_single_config(
            "app.auto_check_updates", bool(checked)
        )
    )
    _add_setting_row(
        preferences_grid,
        2,
        self._t("Automatically Check for Updates"),
        self._t("desc_app_auto_check_updates"),
        self.about_auto_check_checkbox,
    )
    preferences_layout.addLayout(preferences_grid)
    preferences_layout.addStretch(1)
    content.addWidget(preferences_card, 3)

    update_card, update_layout = _create_section_card(
        self._t("Software Updates"),
        self._t("Software Updates Subtitle"),
        FIF.SYNC,
    )
    self.about_update_card = update_card
    self.about_update_status = BodyLabel(self._t("Update Check Not Run"))
    self.about_update_status.setWordWrap(True)
    update_layout.addWidget(self.about_update_status)
    self.about_commit_label = CaptionLabel(
        self._t("GitHub Commit Hash: {hash}", hash=GITHUB_COMMIT_HASH)
    )
    self.about_commit_label.setWordWrap(True)
    update_layout.addWidget(self.about_commit_label)

    mirror_row = QHBoxLayout()
    mirror_row.setSpacing(8)
    self.about_update_source_label = BodyLabel(self._t("Update Source"))
    self.about_update_mirror_combo = ComboBox()
    self.about_update_mirror_combo.setMinimumWidth(210)
    self.about_update_mirror_combo.currentIndexChanged.connect(
        self._on_about_mirror_changed
    )
    mirror_row.addWidget(self.about_update_source_label)
    mirror_row.addStretch(1)
    mirror_row.addWidget(self.about_update_mirror_combo)
    update_layout.addLayout(mirror_row)

    branch_row = QHBoxLayout()
    branch_row.setSpacing(8)
    self.about_update_branch_label = BodyLabel(self._t("Update Branch"))
    self.about_update_branch_combo = ComboBox()
    self.about_update_branch_combo.setMinimumWidth(210)
    self.about_update_branch_combo.currentIndexChanged.connect(
        self._on_about_branch_changed
    )
    branch_row.addWidget(self.about_update_branch_label)
    branch_row.addStretch(1)
    branch_row.addWidget(self.about_update_branch_combo)
    update_layout.addLayout(branch_row)
    update_layout.addStretch(1)

    update_buttons = QHBoxLayout()
    update_buttons.setSpacing(8)
    self.about_check_updates_button = PrimaryPushButton(self._t("Check for Updates"))
    self.about_check_updates_button.setIcon(FIF.SYNC)
    self.about_open_release_button = PushButton(self._t("Open Release Page"))
    self.about_open_release_button.setIcon(FIF.LINK)
    self.about_open_release_button.setVisible(False)
    self.about_check_updates_button.clicked.connect(self.check_for_updates)
    self.about_open_release_button.clicked.connect(self.open_latest_release)
    update_buttons.addWidget(self.about_check_updates_button)
    update_buttons.addWidget(self.about_open_release_button)
    update_buttons.addStretch(1)
    update_layout.addLayout(update_buttons)
    content.addWidget(update_card, 2)
    resources = QHBoxLayout()
    resources.setSpacing(18)
    directories_card, directories_layout = _create_section_card(
        self._t("Application Directories"),
        self._t("Application Directories Subtitle"),
        FIF.FOLDER,
    )
    self.about_directories_card = directories_card
    directory_grid = QGridLayout()
    directory_grid.setHorizontalSpacing(20)
    directory_grid.setVerticalSpacing(12)
    directory_grid.setColumnStretch(0, 1)
    _add_resource_row(self, directory_grid, 0, "Logs Directory", "result")
    _add_resource_row(self, directory_grid, 1, "Configuration Directory", "config")
    _add_resource_row(self, directory_grid, 2, "Prompts Directory", "dict")
    directories_layout.addLayout(directory_grid)
    directories_layout.addStretch(1)
    resources.addWidget(directories_card, 3)

    links_card, links_layout = _create_section_card(
        self._t("Web Resources"),
        self._t("Web Resources Subtitle"),
        FIF.LINK,
    )
    self.about_links_card = links_card
    link_buttons = QHBoxLayout()
    link_buttons.setSpacing(8)
    _add_about_link(self, link_buttons, "GitHub Repository", GITHUB_REPOSITORY_URL)
    _add_about_link(self, link_buttons, "Wiki", WIKI_URL)
    link_buttons.addStretch(1)
    links_layout.addLayout(link_buttons)
    links_layout.addStretch(1)
    resources.addWidget(links_card, 2)

    page_layout.addLayout(content, 1)
    page_layout.addLayout(resources)
    page_layout.addStretch(1)
    _populate_about_mirror_combo(self)
    _populate_about_branch_combo(self)
    return page

def refresh_about_page_texts(self):
    self.about_app_name.setText(self._t("Manga Translator"))
    self.about_app_description.setText(self._t("About Application Description"))
    self.about_version_label.setText(
        self._t(
            "Current Version: {version}",
            version=format_version_label(self.app_version) or self._t("Unknown"),
        )
    )
    self.about_check_updates_button.setText(
        self._t("Checking for Updates")
        if getattr(self, "_update_check_in_progress", False)
        else self._t("Check for Updates")
    )
    self.about_open_release_button.setText(self._t("Open Release Page"))
    self.about_update_source_label.setText(self._t("Update Source"))
    self.about_update_branch_label.setText(self._t("Update Branch"))
    self.about_commit_label.setText(
        self._t("GitHub Commit Hash: {hash}", hash=GITHUB_COMMIT_HASH)
    )
    for card, title_key, subtitle_key in (
        (
            self.about_preferences_card,
            "Application Preferences",
            "Settings Page Subtitle",
        ),
        (self.about_update_card, "Software Updates", "Software Updates Subtitle"),
        (
            self.about_directories_card,
            "Application Directories",
            "Application Directories Subtitle",
        ),
        (self.about_links_card, "Web Resources", "Web Resources Subtitle"),
    ):
        card._about_title_label.setText(self._t(title_key))
        card._about_subtitle_label.setText(self._t(subtitle_key))
    for title_label, path_label, button, title_key in self.about_resource_rows:
        title_label.setText(self._t(title_key))
        button.setText(self._t("Open Directory"))
    for button, title_key in self.about_link_buttons:
        button.setText(self._t(title_key))
    _populate_about_theme_combo(self)
    _populate_about_language_combo(self)
    _populate_about_mirror_combo(self)
    _populate_about_branch_combo(self)


def set_about_update_status(self, key: str, **kwargs):
    if hasattr(self, "about_update_status"):
        self.about_update_status.setText(self._t(key, **kwargs))


def show_update_dialog(self, info):
    dialog_parent = normalize_dialog_parent(self._dialog_parent())
    dialog = UpdateDialog(
        info,
        self._t,
        dialog_parent,
        on_update=self.start_update_maintenance,
    )
    dialog.exec()


class UpdateDialog:
    """Modal release summary shown when a newer version is available."""

    def __new__(
        cls,
        info,
        translate: Callable[..., str],
        parent=None,
        on_update: Callable[[], bool] | None = None,
    ):
        from PyQt6.QtWidgets import QTextBrowser
        from qfluentwidgets import Dialog

        dialog = Dialog("", "", parent)
        dialog.setWindowTitle(translate("Update Available"))
        dialog.titleLabel.hide()
        dialog.contentLabel.hide()
        dialog.textLayout.setSpacing(8)

        heading = SubtitleLabel(translate("New Version Available"), dialog)
        heading.setWordWrap(True)
        summary = BodyLabel(
            translate(
                "Update Version Summary",
                current=info.current_version,
                latest=info.latest_version,
            ),
            dialog,
        )
        summary.setWordWrap(True)
        dialog.textLayout.addWidget(heading)
        dialog.textLayout.addWidget(summary)

        commits_differ = info.current_commit != info.latest_commit
        if (
            info.current_commit
            and info.latest_commit
            and (commits_differ or info.commits_behind > 0)
        ):
            commit_summary = CaptionLabel(
                translate(
                    "Update Commit Summary",
                    current=info.current_commit[:12],
                    latest=info.latest_commit[:12],
                    count=info.commits_behind,
                ),
                dialog,
            )
            commit_summary.setWordWrap(True)
            dialog.textLayout.addWidget(commit_summary)

        log_heading = StrongBodyLabel(translate("Update Log"), dialog)
        dialog.textLayout.addWidget(log_heading)

        markdown = info.release_notes or translate("No Release Notes")
        markdown_lines = markdown.splitlines()
        if markdown_lines and markdown_lines[0].lstrip().startswith("# "):
            markdown_lines = markdown_lines[1:]
            while markdown_lines and not markdown_lines[0].strip():
                markdown_lines.pop(0)

        notes = QTextBrowser(dialog)
        notes.setOpenExternalLinks(True)
        notes.document().setDefaultStyleSheet(
            "body { font-size: 14px; } "
            "h1 { font-size: 20px; margin: 8px 0 6px; } "
            "h2 { font-size: 17px; margin: 10px 0 5px; } "
            "h3 { font-size: 15px; margin: 8px 0 4px; } "
            "p { margin: 4px 0; } li { margin: 2px 0; }"
        )
        notes.setMarkdown("\n".join(markdown_lines))
        notes.setMinimumHeight(240)
        dialog.textLayout.addWidget(notes, 1)

        dialog.yesButton.hide()
        dialog.cancelButton.setText(translate("Later"))
        if on_update is not None:
            update_button = PrimaryPushButton(translate("Update Now"), dialog.buttonGroup)
            update_button.clicked.connect(
                lambda: dialog.accept() if on_update() else None
            )
            dialog.buttonLayout.insertWidget(0, update_button, 1)

        release_button = PushButton(translate("Open Release Page"), dialog.buttonGroup)
        release_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(info.release_url))
        )
        dialog.buttonLayout.insertWidget(1, release_button, 1)

        _apply_flexible_size(dialog, 640, 460)
        dialog.setContentCopyable(True)
        dialog._update_info = info
        return dialog
