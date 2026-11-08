"""Brush size is remembered independently for each image-tool tab.

Run under pytest or directly: ``python test/test_editor_panel_brush_sizes.py -v``.
"""

import _bootstrap  # noqa: F401, I001

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QGraphicsView

from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from editor.session import EditorSession
from ui.editor.shortcut_manager import EditorShortcutManager
from ui.widgets.property_panel import PropertyPanel


class EditorPanelInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Keep one QApplication alive for the complete widget test run."""
        cls.app = QApplication.instance() or QApplication([])

    def test_tool_switch_restores_each_brush_size(self):
        """Retain each group's size across document loads and drawing/eraser switches."""
        session = EditorSession()
        session.set_active_tool("brush")
        session.set_brush_size(17)
        session.set_active_tool("paint")
        session.set_brush_size(61)
        session.set_active_tool("clone")
        session.set_brush_size(93)
        for name in ("first.png", "second.png"):
            session.load_document(DocumentSnapshot(name, np.zeros((2, 2, 3), dtype=np.uint8)))
        for tool, expected in (
            ("pen", 17),
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
        """Connect a real model and panel using isolated configuration and cleanup."""
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
            """Delete the panel and its queued Qt events before the next test."""
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
        """Restore tab sizes and consume Shift+wheel without touching other groups."""
        model, panel = self._create_panel()
        view = QGraphicsView()
        self.addCleanup(view.deleteLater)
        shortcuts = SimpleNamespace(editor_view=SimpleNamespace(model=model, graphics_view=view))

        panel.brush_size_slider.setValue(17)
        panel.activate_image_edit_tab(1)
        panel.paint_size_slider.setValue(61)
        panel.activate_image_edit_tab(2)
        panel.stamp_size_slider.setValue(93)
        expected_sizes = [17, 61, 93]
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
                event = QWheelEvent(
                    QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.ShiftModifier,
                    Qt.ScrollPhase.NoScrollPhase, False,
                )
                self.assertTrue(EditorShortcutManager.eventFilter(shortcuts, view.viewport(), event))
                self.assertEqual(slider.value(), expected + 1)
                expected_sizes[index] = expected + 1
                self.assertEqual(
                    [model.get_brush_size(group) for group in ("mask", "paint", "stamp")],
                    expected_sizes,
                )

    def test_pen_alias_returns_to_mask_without_changing_other_groups(self):
        """A model-driven pen alias must select Mask before the next slider edit."""
        model, panel = self._create_panel()
        panel.brush_size_slider.setValue(17)
        panel.activate_image_edit_tab(1)
        panel.paint_size_slider.setValue(61)
        panel.activate_image_edit_tab(2)
        panel.stamp_size_slider.setValue(93)
        for index in (1, 2):
            with self.subTest(previous_tab=index):
                panel.activate_image_edit_tab(index)
                panel.activate_image_edit_tool(1)
                model.set_active_tool("pen")
                self.assertEqual(panel._paint_current_index(), 0)
                self.assertTrue(panel.brush_button.isChecked())
                self.assertEqual(model.get_active_tool(), "pen")
                self.assertEqual(panel.brush_size_slider.value(), model.get_brush_size("mask"))
                panel.brush_size_slider.setValue(17 + index)
                self.assertEqual(model.get_brush_size(), 17 + index)
                self.assertEqual(model.get_brush_size("paint"), 61)
                self.assertEqual(model.get_brush_size("stamp"), 93)
                self.assertEqual(panel.paint_size_slider.value(), 61)
                self.assertEqual(panel.stamp_size_slider.value(), 93)


if __name__ == "__main__":
    unittest.main()
