from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))


def test_async_region_update_uses_region_id_on_main_thread() -> None:
    from desktop_qt_ui.editor.editor_controller import EditorController, _AsyncRegionUpdateRequest

    class FakeModel:
        def __init__(self):
            self.ids = [99, 1]
            self.regions = [{"text": "new"}, {"text": "old"}]
            self.calls = []

        def find_region_index(self, region_id):
            try:
                return self.ids.index(region_id)
            except ValueError:
                return None

        def get_region_by_index(self, index):
            if index is None or not 0 <= index < len(self.regions):
                return None
            return dict(self.regions[index])

        def get_regions(self):
            return [dict(region) for region in self.regions]

        def update_regions(self, updates, *, fields=None, source=""):
            self.calls.append((updates, tuple(fields or ()), source))
            for index, region in updates.items():
                self.regions[index] = region

    model = FakeModel()
    finish_calls = []
    executed_commands = []

    def execute_command(command):
        executed_commands.append(command)
        command.redo()

    controller = SimpleNamespace(
        model=model,
        logger=SimpleNamespace(error=lambda *args, **kwargs: None),
        execute_command=execute_command,
        _finish_async_region_update=lambda *args, **kwargs: finish_calls.append((args, kwargs)),
    )

    request = _AsyncRegionUpdateRequest("text", [(1, "ocr result")], "ocr")
    EditorController.on_regions_update_finished(controller, request)

    assert model.regions[0]["text"] == "new"
    assert model.regions[1]["text"] == "ocr result"
    assert model.calls[0][1] == ("text",)
    assert model.calls[0][2] == "async"

    # 写回必须走撤销栈里的命令，且 undo 能还原
    assert len(executed_commands) == 1
    executed_commands[0].undo()
    assert model.regions[1]["text"] == "old"


def test_region_list_preserves_dirty_translation_by_region_id() -> None:
    from PyQt6.QtWidgets import QApplication
    from qfluentwidgets import TextEdit

    from desktop_qt_ui.editor.region_change import RegionChange
    from desktop_qt_ui.ui.widgets.region_list_view import RegionListView

    class FakeModel:
        def __init__(self):
            self.ids = [1]
            self.regions = [{"text": "A", "translation": "model A"}]

        def get_regions(self):
            return list(self.regions)

        def get_region_id(self, index):
            return self.ids[index] if 0 <= index < len(self.ids) else None

    app = QApplication.instance() or QApplication([])
    model = FakeModel()
    view = RegionListView(model)
    view.show()
    try:
        view.update_regions(model.get_regions())
        app.processEvents()

        edit = view.itemWidget(view.item(0)).findChild(TextEdit)
        edit.setPlainText("draft A")

        model.ids = [2, 1]
        model.regions = [
            {"text": "B", "translation": "model B"},
            {"text": "A", "translation": "model A updated"},
        ]
        view.on_regions_changed(RegionChange.inserted([0]))
        app.processEvents()

        new_edit = view.itemWidget(view.item(0)).findChild(TextEdit)
        old_edit = view.itemWidget(view.item(1)).findChild(TextEdit)
        assert new_edit.toPlainText() == "model B"
        assert old_edit.toPlainText() == "draft A"
    finally:
        view.close()


def test_legacy_white_frame_is_normalized_to_render_box() -> None:
    from desktop_qt_ui.editor.region_geometry_state import (
        RegionGeometryState,
        normalize_region_geometry_data,
    )
    from desktop_qt_ui.editor.region_render_snapshot import RegionRenderSnapshot

    legacy = {
        "center": [50, 50],
        "angle": 0,
        "has_custom_white_frame": False,
        "white_frame_rect_local": [10, 20, 30, 40],
    }

    normalized = normalize_region_geometry_data(legacy)
    state = RegionGeometryState.from_region_data(legacy)
    snapshot = RegionRenderSnapshot.from_sources(0, legacy)

    assert normalized.get("render_box_rect_local") == [10, 20, 30, 40]
    assert "white_frame_rect_local" not in normalized
    assert list(state.render_box_local) == [10, 20, 30, 40]
    assert tuple(state.white_frame_local) == snapshot.white_frame_local


def main() -> int:
    test_async_region_update_uses_region_id_on_main_thread()
    test_region_list_preserves_dirty_translation_by_region_id()
    test_legacy_white_frame_is_normalized_to_render_box()
    print("EDITOR_REGION_REGRESSIONS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
