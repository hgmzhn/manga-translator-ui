import os

import _bootstrap  # noqa: F401
from core.config_models import AppSection
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFileSystemModel
from ui.secondary_pages.folder_dialog import (
    CaseInsensitiveSortProxyModel,
    _folder_sort_spec,
    _folder_sort_state,
    _normalize_folder_sort_state,
)


def test_folder_dialog_sort_state_round_trip():
    states = {
        "name_ascending": (0, Qt.SortOrder.AscendingOrder),
        "name_descending": (0, Qt.SortOrder.DescendingOrder),
        "size_ascending": (1, Qt.SortOrder.AscendingOrder),
        "size_descending": (1, Qt.SortOrder.DescendingOrder),
        "type_ascending": (2, Qt.SortOrder.AscendingOrder),
        "type_descending": (2, Qt.SortOrder.DescendingOrder),
        "modified_ascending": (3, Qt.SortOrder.AscendingOrder),
        "modified_descending": (3, Qt.SortOrder.DescendingOrder),
    }

    for state, spec in states.items():
        assert _folder_sort_spec(state) == spec
        assert _folder_sort_state(*spec) == state


def test_folder_dialog_sort_state_falls_back_to_name_ascending():
    assert _normalize_folder_sort_state(None) == "name_ascending"
    assert _normalize_folder_sort_state("unknown") == "name_ascending"
    assert _folder_sort_spec("unknown") == (0, Qt.SortOrder.AscendingOrder)


def test_folder_dialog_sort_config_default_and_serialization():
    assert AppSection().folder_dialog_sort == "name_ascending"
    section = AppSection(folder_dialog_sort="modified_descending")
    assert section.model_dump()["folder_dialog_sort"] == "modified_descending"


def test_folder_dialog_modified_time_sort_uses_timestamp(tmp_path):
    older = tmp_path / "older"
    newer = tmp_path / "newer"
    older.mkdir()
    newer.mkdir()
    older_timestamp = 1_754_107_200  # 2025-08-02 00:00:00 UTC
    newer_timestamp = 1_755_057_600  # 2025-08-13 00:00:00 UTC

    os.utime(older, (older_timestamp, older_timestamp))
    os.utime(newer, (newer_timestamp, newer_timestamp))

    source_model = QFileSystemModel()
    source_model.setRootPath(str(tmp_path))
    proxy_model = CaseInsensitiveSortProxyModel()
    proxy_model.setSourceModel(source_model)

    older_index = source_model.index(str(older), 3)
    newer_index = source_model.index(str(newer), 3)

    assert older_index.isValid()
    assert newer_index.isValid()
    assert proxy_model.lessThan(older_index, newer_index)
    assert not proxy_model.lessThan(newer_index, older_index)
