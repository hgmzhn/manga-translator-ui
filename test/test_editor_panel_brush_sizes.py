"""Brush size is remembered independently for each image-tool tab.

Run under pytest or directly: ``python test/test_editor_panel_brush_sizes.py -v``.
"""

import _bootstrap  # noqa: F401, I001

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication

from editor.editor_model import EditorModel
from editor.session import EditorSession
from ui.widgets.property_panel import PropertyPanel


class EditorPanelInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tool_switch_restores_each_brush_size(self):
        session = EditorSession()
        session.set_active_tool("brush")
        session.set_brush_size(17)
        session.set_active_tool("paint")
        session.set_brush_size(61)
        session.set_active_tool("clone")
        session.set_brush_size(93)
        for tool, expected in (
            ("brush", 17),
            ("eraser", 17),
            ("paint", 61),
            ("paint_erase", 61),
            ("clone", 93),
            ("stamp_erase", 93),
        ):
            with self.subTest(tool=tool):
                session.set_active_tool(tool)
                self.assertEqual(session.get_brush_size(), expected)

    def _create_panel(self):
        model = EditorModel()
        config = SimpleNamespace(
            app=SimpleNamespace(), render=SimpleNamespace(font_color="#000000")
        )
        config_service = SimpleNamespace(
            get_config=lambda: config, get_config_reference=lambda: config
        )
        with patch("ui.widgets.property_panel.get_config_service", return_value=config_service):
            panel = PropertyPanel(model, None)

        def cleanup_panel():
            panel.close()
            panel.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

        self.addCleanup(cleanup_panel)
        panel.mask_tool_changed.connect(model.set_active_tool)
        panel.brush_size_changed.connect(model.set_brush_size)
        model.brush_size_changed.connect(panel.sync_brush_size_from_model)
        model.active_tool_changed.connect(panel.sync_active_tool_from_model)
        return model, panel

    def test_tab_switch_keeps_sliders_and_active_brush_in_sync(self):
        model, panel = self._create_panel()

        panel.brush_size_slider.setValue(17)
        panel.activate_image_edit_tab(1)
        panel.paint_size_slider.setValue(61)
        panel.activate_image_edit_tab(2)
        panel.stamp_size_slider.setValue(93)
        for index, slider, expected in (
            (0, panel.brush_size_slider, 17),
            (1, panel.paint_size_slider, 61),
            (2, panel.stamp_size_slider, 93),
        ):
            with self.subTest(tab=index):
                panel.activate_image_edit_tab(index)
                self.assertEqual(slider.value(), expected)
                self.assertEqual(model.get_brush_size(), expected)
                panel.activate_image_edit_tool(1)
                self.assertEqual(model.get_active_tool(), ("brush", "paint", "clone")[index])
                model.set_brush_size(expected + 1)  # Shift+wheel uses this API.
                self.assertEqual(slider.value(), expected + 1)


if __name__ == "__main__":
    unittest.main()
