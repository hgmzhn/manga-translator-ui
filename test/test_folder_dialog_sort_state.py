import _bootstrap  # noqa: F401

from PyQt6.QtCore import Qt

from core.config_models import AppSection
from ui.secondary_pages.folder_dialog import (
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
