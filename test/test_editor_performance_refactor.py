from __future__ import annotations

import _bootstrap  # noqa: F401  —— sys.path / offscreen / torch 先于 PyQt6

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = _bootstrap.ROOT

from PyQt6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

_TEST_APP = QApplication.instance() or QApplication([])

from desktop_qt_ui.editor.controller_document_service import EditorControllerDocumentService
from desktop_qt_ui.editor.document_load_worker import DocumentLoadWorker
from desktop_qt_ui.editor.editor_controller import EditorController
from desktop_qt_ui.editor.region_change import RegionChange
from desktop_qt_ui.ui.main_page import dynamic_settings
from desktop_qt_ui.ui.widgets.region_list_view import RegionListView
from desktop_qt_ui.ui.widgets.widget_cleanup import clear_layout


def _app() -> QApplication:
    return _TEST_APP


class _RegionListModel:
    def __init__(self):
        self.regions = [
            {"text": "one", "translation": "uno"},
            {"text": "two", "translation": "dos"},
        ]
        self.ids = [101, 102]

    def get_regions(self):
        return self.regions

    def get_region_id(self, index):
        return self.ids[index]


def test_region_list_applies_region_change_without_rebuilding_unaffected_rows():
    app = _app()
    model = _RegionListModel()
    view = RegionListView(model)
    view.resize(420, 360)
    view.show()
    app.processEvents()
    try:
        view.update_regions(model.get_regions())
        app.processEvents()
        first_widget = view.itemWidget(view.item(0))
        second_widget = view.itemWidget(view.item(1))

        model.regions[1] = {"text": "two updated", "translation": "dos"}
        view.on_regions_changed(RegionChange.updated([1], fields=["text"]))
        assert view.itemWidget(view.item(0)) is first_widget
        assert view.itemWidget(view.item(1)) is second_widget
        assert second_widget.original_label.text() == "2: two updated"

        model.regions.insert(1, {"text": "new", "translation": "nuevo"})
        model.ids.insert(1, 103)
        view.on_regions_changed(RegionChange.inserted([1]))
        assert view.itemWidget(view.item(0)) is first_widget
        assert view.itemWidget(view.item(2)) is second_widget

        model.regions.pop(1)
        model.ids.pop(1)
        view.on_regions_changed(RegionChange.removed([1]))
        assert view.itemWidget(view.item(0)) is first_widget
        assert view.itemWidget(view.item(1)) is second_widget
    finally:
        view.close()
        app.processEvents()


def test_settings_structure_signature_ignores_values_but_tracks_control_kind():
    dummy = SimpleNamespace(
        controller=SimpleNamespace(
            get_options_for_key=lambda _key: [],
            get_display_mapping=lambda _key: {},
        ),
        settings_tab_layout=[],
        _settings_tabs_use_reclassify=False,
    )
    first = dynamic_settings._settings_structure_signature(dummy, {"app": {"name": "a", "count": 1}})
    second = dynamic_settings._settings_structure_signature(dummy, {"app": {"name": "b", "count": 2}})
    changed_kind = dynamic_settings._settings_structure_signature(dummy, {"app": {"name": True, "count": 2}})
    assert first == second
    assert first != changed_kind


def test_recursive_layout_cleanup_hides_nested_widgets_without_reparenting():
    app = _app()
    parent = QWidget()
    root_layout = QVBoxLayout(parent)
    nested_layout = QHBoxLayout()
    child = QWidget(parent)
    nested_layout.addWidget(child)
    root_layout.addLayout(nested_layout)
    parent.show()
    app.processEvents()
    try:
        clear_layout(root_layout)
        assert root_layout.count() == 0
        assert not child.isVisible()
        assert child.parentWidget() is parent
    finally:
        parent.close()
        app.processEvents()


def test_settings_value_sync_reuses_existing_widget():
    app = _app()
    line_edit = dynamic_settings.QLineEdit("old")
    try:
        dummy = SimpleNamespace(
            _settings_value_bindings={"app.name": (line_edit, {})},
        )
        assert dynamic_settings._sync_setting_widget_values(dummy, {"app": {"name": "new"}})
        assert line_edit.text() == "new"
    finally:
        line_edit.close()
        app.processEvents()


class _BatchModel:
    def __init__(self):
        self.regions = [{"font_color": "#000000"}, {"font_color": "#111111"}]
        self.notifications = []

    def get_regions(self):
        return self.regions

    def get_region_by_index(self, index):
        return self.regions[index]

    def update_regions(self, updates, *, fields=None, source=""):
        self.notifications.append((tuple(sorted(updates)), tuple(fields or ()), source))
        for index, region in updates.items():
            self.regions[index] = region


def test_style_patch_updates_selection_with_one_model_notification():
    model = _BatchModel()
    controller = SimpleNamespace(
        model=model,
        config_service=SimpleNamespace(
            get_config=lambda: SimpleNamespace(render=SimpleNamespace(line_spacing=1.0, letter_spacing=1.0))
        ),
        _normalize_alignment_value=EditorController._normalize_alignment_value,
        _normalize_direction_value=EditorController._normalize_direction_value,
        _merge_live_geometry_state=lambda index, region: dict(region),
        _build_rotated_region_data=EditorController._build_rotated_region_data,
    )
    executed_commands = []

    def execute_command(command):
        executed_commands.append(command)
        command.redo()

    controller.execute_command = execute_command

    EditorController.update_region_style_patch(
        controller,
        [0, 1],
        {"font_color": "#abcdef", "stroke_color": "#010203"},
    )

    assert len(executed_commands) == 1
    assert len(model.notifications) == 1
    assert model.notifications[0][0] == (0, 1)
    assert model.notifications[0][2] == "property-panel"
    assert [region["font_color"] for region in model.regions] == ["#abcdef", "#abcdef"]
    assert [region["bg_colors"] for region in model.regions] == [[1, 2, 3], [1, 2, 3]]

    executed_commands[0].undo()
    assert [region["font_color"] for region in model.regions] == ["#000000", "#111111"]
    assert all("bg_colors" not in region for region in model.regions)


def test_document_load_worker_reuses_service_aux_executor():
    service = EditorControllerDocumentService(SimpleNamespace())
    try:
        worker = DocumentLoadWorker(service, "unused.png", service._aux_load_executor)
        assert worker.aux_executor is service._aux_load_executor
    finally:
        service.shutdown()


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__]))
