import _bootstrap  # noqa: F401  —— sys.path / offscreen / torch 先于 PyQt6

import copy
import logging
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = _bootstrap.ROOT

from PyQt6.QtGui import QColor, QTextCursor
from PyQt6.QtWidgets import QApplication, QToolButton
from qfluentwidgets import CompactDoubleSpinBox, SimpleCardWidget

from desktop_qt_ui.editor.editor_controller import EditorController
from desktop_qt_ui.editor.rich_text_editing import visible_text_from_document
from desktop_qt_ui.ui.editor.view import EditorView
from desktop_qt_ui.ui.secondary_pages.rich_text_rules_editor import RichTextStyleControls
from desktop_qt_ui.ui.widgets.rich_text_editor_components import RubyEditBar
from desktop_qt_ui.ui.widgets.rich_text_floating_editor import RichTextFloatingEditor


def _styled_run_document():
    return {
        "format": "richtext.v1",
        "blocks": [{"type": "paragraph", "inlines": [
            {
                "type": "text",
                "text": "连",
                "style": {
                    "bold": True,
                    "outerStroke": {"color": "#000000", "width": 0.2},
                },
            },
            {"type": "text", "text": "游戏也", "style": {}},
            {
                "type": "text",
                "text": "戏",
                "style": {
                    "bold": True,
                    "outerStroke": {"color": "#000000", "width": 0.2},
                },
            },
            {"type": "text", "text": "不玩了", "style": {}},
        ]}],
    }


def _ruby_text(document):
    for inline in document["blocks"][0]["inlines"]:
        if inline.get("type") == "ruby":
            return "".join(run.get("text", "") for run in inline.get("text", []))
    return None


class RichTextFloatingEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
        self.app.processEvents()

    def _editor(self, region_data=None):
        editor = RichTextFloatingEditor()
        self.widgets.append(editor)
        editor.set_region(0, region_data or {"translation": "漢字"})
        editor.show()
        self.app.processEvents()
        return editor

    def test_editor_starts_hidden_without_a_bound_region(self):
        editor = RichTextFloatingEditor()
        self.widgets.append(editor)
        self.app.processEvents()

        self.assertFalse(editor.isVisible())
        self.assertFalse(editor._state.has_region)

    def test_editor_surface_follows_fluent_theme(self):
        editor = RichTextFloatingEditor()
        self.widgets.append(editor)
        self.assertIsInstance(editor.preset_sidebar, SimpleCardWidget)
        self.assertEqual(editor.preset_sidebar.getBorderRadius(), 0)

        with patch(
            "desktop_qt_ui.ui.widgets.rich_text_floating_editor.isDarkTheme",
            return_value=True,
        ):
            editor.refresh_theme()
            self.assertEqual(editor.backgroundColor, QColor(32, 32, 32))

        with patch(
            "desktop_qt_ui.ui.widgets.rich_text_floating_editor.isDarkTheme",
            return_value=False,
        ):
            editor.refresh_theme()
            self.assertEqual(editor.backgroundColor, QColor(250, 250, 250))

    def _open_new_ruby(self, editor):
        editor._select_python_range(0, 2)
        editor._on_toolbar_toggled("R", True)
        self.app.processEvents()
        card = editor.run_list.run_cards[0]
        ruby_bar = card.controls["R"]
        self.assertIsInstance(ruby_bar, RubyEditBar)
        return ruby_bar

    def test_ruby_apply_button_saves(self):
        editor = self._editor()
        changes = []
        editor.rich_text_changed.connect(lambda index, document, text: changes.append(copy.deepcopy(document)))
        ruby_bar = self._open_new_ruby(editor)

        ruby_bar.input.setText("かんじ")
        ruby_bar.apply_button.click()
        self.app.processEvents()

        self.assertEqual(_ruby_text(editor._state.document), "かんじ")
        self.assertEqual(len(changes), 1)

    def test_ruby_focus_loss_saves(self):
        editor = self._editor()
        changes = []
        editor.rich_text_changed.connect(lambda *_args: changes.append(1))
        ruby_bar = self._open_new_ruby(editor)

        ruby_bar.input.setText("かんじ")
        ruby_bar.input.setFocus()
        editor.text_box.setFocus()
        self.app.processEvents()

        self.assertEqual(_ruby_text(editor._state.document), "かんじ")
        self.assertEqual(len(changes), 1)

    def test_ruby_region_switch_saves_old_region(self):
        editor = self._editor()
        changes = []
        editor.rich_text_changed.connect(
            lambda index, document, text: changes.append((index, copy.deepcopy(document)))
        )
        ruby_bar = self._open_new_ruby(editor)
        ruby_bar.input.setText("かんじ")

        editor.set_region(1, {"translation": "漢字"})

        self.assertEqual(changes[0][0], 0)
        self.assertEqual(_ruby_text(changes[0][1]), "かんじ")

    def test_ruby_selection_switch_and_hide_both_flush(self):
        editor = self._editor({"translation": "漢字かな"})
        changes = []
        editor.rich_text_changed.connect(lambda _index, document, _text: changes.append(copy.deepcopy(document)))
        ruby_bar = self._open_new_ruby(editor)
        ruby_bar.input.setText("かんじ")

        editor._select_python_range(2, 4)
        self.assertEqual(_ruby_text(editor._state.document), "かんじ")
        self.assertEqual(len(changes), 1)

        editor._select_python_range(0, 2)
        editor._on_toolbar_toggled("R", True)
        self.app.processEvents()
        ruby_bar = editor.run_list.run_cards[0].controls["R"]
        ruby_bar.input.setText("カンジ")
        editor.hide()
        self.app.processEvents()

        self.assertEqual(_ruby_text(editor._state.document), "カンジ")
        self.assertEqual(len(changes), 2)

    def test_ruby_enter_and_editing_finished_do_not_double_submit(self):
        editor = self._editor()
        changes = []
        editor.rich_text_changed.connect(lambda *_args: changes.append(1))
        ruby_bar = self._open_new_ruby(editor)
        ruby_bar.input.setText("かんじ")

        ruby_bar.input.returnPressed.emit()
        ruby_bar.input.editingFinished.emit()
        self.app.processEvents()

        self.assertEqual(len(changes), 1)

    def test_real_runs_are_separate_cards_with_separate_property_rows(self):
        document = _styled_run_document()
        editor = self._editor({
            "translation": "连游戏也戏不玩了",
            "translation_rich": document,
        })

        self.assertEqual([card.header.text() for card in editor.run_list.run_cards], ["连", "戏"])
        self.assertEqual([card.keys for card in editor.run_list.run_cards], [["B", "OS"], ["B", "OS"]])
        for card in editor.run_list.run_cards:
            self.assertEqual(card.name_labels["B"].text(), editor._t("Bold"))
            self.assertEqual(card.name_labels["OS"].text(), editor._t("Outer Stroke"))
        visible_text = " ".join(
            child.text()
            for card in editor.run_list.run_cards
            for child in card.findChildren(type(card.header))
            if hasattr(child, "text")
        )
        self.assertNotIn("默认", visible_text)
        self.assertNotIn("混合", visible_text)
        self.assertNotIn("连 / 戏", visible_text)

    def test_clicking_run_selects_its_exact_text_range(self):
        document = _styled_run_document()
        editor = self._editor({
            "translation": "连游戏也戏不玩了",
            "translation_rich": document,
        })

        editor.run_list.run_cards[1].header.click()
        self.app.processEvents()

        self.assertEqual(editor._state.selected_range, (4, 5))
        self.assertEqual(editor.text_box.textCursor().selectedText(), "戏")

    def test_rotation_writes_90_immediately_and_offset_after_value_input(self):
        editor = self._editor({"translation": "偏移"})
        changes = []
        editor.rich_text_changed.connect(lambda *_args: changes.append(1))
        editor._select_python_range(0, 2)

        editor.toolbar.buttons["Rot"].click()
        self.app.processEvents()

        style = editor._state.document["blocks"][0]["inlines"][0]["style"]
        self.assertEqual(style["transform"]["rotation"], 90.0)
        self.assertEqual(len(changes), 1)
        self.assertEqual(editor.run_list.run_cards[0].keys, ["Rot"])

        rotation = editor.run_list.run_cards[0].controls["Rot"]
        self.assertEqual(rotation.value(), 90.0)
        rotation.setValue(15)
        self.app.processEvents()

        style = editor._state.document["blocks"][0]["inlines"][0]["style"]
        self.assertEqual(style["transform"]["rotation"], 15.0)
        self.assertEqual(len(changes), 2)

        editor.toolbar.buttons["XY"].click()
        self.app.processEvents()

        style = editor._state.document["blocks"][0]["inlines"][0]["style"]
        self.assertNotIn("offsetX", style["transform"])
        self.assertNotIn("offsetY", style["transform"])
        self.assertEqual(len(changes), 2)
        self.assertEqual(editor.run_list.run_cards[0].keys, ["Rot", "XY"])

        offset_editor = editor.run_list.run_cards[0].controls["XY"]
        offsets = offset_editor.findChildren(CompactDoubleSpinBox)
        self.assertEqual(len(offsets), 2)
        offsets[0].setValue(8)
        self.app.processEvents()

        style = editor._state.document["blocks"][0]["inlines"][0]["style"]
        self.assertEqual(style["transform"]["offsetX"], 8.0)
        self.assertEqual(len(changes), 3)

    def test_style_only_edit_preserves_pre_replacement_translation(self):
        editor = self._editor({
            "translation": "AB",
            "translation_raw": "A_B",
            "translation_rich": {
                "format": "richtext.v1",
                "blocks": [{"type": "paragraph", "inlines": [
                    {"type": "text", "text": "AB", "style": {"bold": True}},
                ]}],
            },
        })
        editor._select_python_range(0, 2)

        editor.toolbar.buttons["I"].click()
        self.app.processEvents()

        self.assertEqual(editor._state.region_data["translation"], "AB")
        self.assertEqual(editor._state.region_data["translation_raw"], "A_B")

    def test_toolbar_without_selection_targets_whole_text(self):
        """未选中文字时点工具栏 = 样式作用于全文（选区随之扩到全文）。"""
        editor = self._editor({"translation": "漢字かな"})
        changes = []
        editor.rich_text_changed.connect(lambda *_args: changes.append(1))

        editor.toolbar.buttons["B"].click()
        self.app.processEvents()

        self.assertEqual(
            editor._state.document["blocks"][0]["inlines"],
            [{"type": "text", "text": "漢字かな", "style": {"bold": True}}],
        )
        self.assertEqual(editor._state.selected_range, (0, 4))
        self.assertEqual(len(changes), 1)

    def test_spin_edit_keeps_control_alive_for_auto_repeat(self):
        """点步进箭头后卡片不得整体重建，否则悬停态与连发都会丢失。"""
        document = {
            "format": "richtext.v1",
            "blocks": [{"type": "paragraph", "inlines": [
                {"type": "text", "text": "连", "style": {"fontSize": 24}},
                {"type": "text", "text": "游", "style": {}},
            ]}],
        }
        editor = self._editor({"translation": "连游", "translation_rich": document})
        spin = editor.run_list.run_cards[0].controls["S"]

        spin.stepUp()
        self.app.processEvents()

        style = editor._state.document["blocks"][0]["inlines"][0]["style"]
        self.assertEqual(style["fontSize"], 25)
        self.assertTrue(editor.run_list.run_cards)
        self.assertIs(editor.run_list.run_cards[0].controls.get("S"), spin)

    def test_two_decimal_spin_steps_by_fraction(self):
        editor = self._editor({"translation": "字距"})
        editor._select_python_range(0, 2)
        editor.toolbar.buttons["K"].click()
        self.app.processEvents()

        spin = editor.run_list.run_cards[0].controls["K"]
        self.assertEqual(spin.decimals(), 2)
        self.assertAlmostEqual(spin.singleStep(), 0.05)

    def test_every_toolbar_style_opens_a_property_row(self):
        for key in (
            "B", "I", "C", "S", "%", "F", "O", "G", "OS", "D", "FA", "T", "R",
            "Rot", "K", "PK", "LK", "NK", "XY", "M", "MV",
        ):
            with self.subTest(key=key):
                editor = self._editor({"translation": "样式"})
                editor._select_python_range(0, 2)
                editor.toolbar.buttons[key].click()
                self.app.processEvents()

                self.assertTrue(editor.run_list.run_cards)
                self.assertTrue(
                    any(key in card.keys for card in editor.run_list.run_cards),
                    f"{key} did not open a property row",
                )
                editor.close()

    def test_force_advance_controls_write_half_and_full(self):
        editor = self._editor({"translation": "推进"})
        editor._select_python_range(0, 2)
        editor.toolbar.buttons["FA"].click()
        self.app.processEvents()

        control = editor.run_list.run_cards[0].controls["FA"]
        style = editor._state.document["blocks"][0]["inlines"][0]["style"]
        self.assertEqual((control.currentData(), style["verticalAdvance"]), ("half", "half"))

        control.setCurrentIndex(control.findData("full"))
        self.app.processEvents()
        self.assertEqual(
            editor._state.document["blocks"][0]["inlines"][0]["style"]["verticalAdvance"],
            "full",
        )

        rules = RichTextStyleControls(lambda text: text)
        self.widgets.append(rules)
        rules.load_style({"verticalAdvance": "full"})
        self.assertTrue(rules.vertical_advance.enabled.isChecked())
        self.assertEqual(rules.style()["verticalAdvance"], "full")

    def test_unedited_pending_style_is_discarded_when_hidden(self):
        # Rot 现在立即写入 90°，待定路径改用默认仍为 0 的 XY（偏移）覆盖。
        editor = self._editor({"translation": "旋转"})
        changes = []
        editor.rich_text_changed.connect(lambda *_args: changes.append(1))
        editor._select_python_range(0, 2)
        editor.toolbar.buttons["XY"].click()
        self.app.processEvents()

        editor.hide()
        editor.show()
        self.app.processEvents()

        self.assertEqual(changes, [])
        self.assertIsNone(editor._state.pending_style_edit)
        self.assertEqual(editor.run_list.run_cards, [])

    def test_remove_style_button_works_with_one_click(self):
        document = _styled_run_document()
        editor = self._editor({
            "translation": "连游戏也戏不玩了",
            "translation_rich": document,
        })
        first_card = editor.run_list.run_cards[0]
        remove_buttons = [
            button
            for button in first_card.findChildren(QToolButton)
            if button.objectName() == "removeStyle"
        ]

        remove_buttons[0].click()
        self.app.processEvents()

        first_inline = editor._state.document["blocks"][0]["inlines"][0]
        self.assertNotIn("bold", first_inline["style"])
        self.assertIn("outerStroke", first_inline["style"])

    def test_equal_text_region_resets_selection_and_local_undo(self):
        editor = self._editor({"translation": "相同"})
        cursor = editor.text_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("X")
        self.app.processEvents()
        self.assertTrue(editor.text_box.document().isUndoAvailable())

        editor.set_region(1, {"translation": "相同"})

        cursor = editor.text_box.textCursor()
        self.assertEqual(cursor.position(), 0)
        self.assertFalse(cursor.hasSelection())
        self.assertFalse(editor.text_box.document().isUndoAvailable())

    def test_emoji_qt_selection_only_styles_the_emoji(self):
        editor = self._editor({"translation": "A😀B"})
        cursor = QTextCursor(editor.text_box.document())
        cursor.setPosition(1)
        cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        editor.text_box.setTextCursor(cursor)
        self.app.processEvents()

        editor.toolbar.buttons["B"].click()
        self.app.processEvents()

        self.assertEqual(
            editor._state.document["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "A", "style": {}},
                {"type": "text", "text": "😀", "style": {"bold": True}},
                {"type": "text", "text": "B", "style": {}},
            ],
        )

    def test_toolbar_export_flushes_before_controller_reads_model(self):
        events = []

        class RichEditor:
            def flush_pending_changes(self):
                events.append("flush")

        class Controller:
            def export_image(self):
                events.append("export")
                return "ok"

        view = SimpleNamespace(rich_text_editor=RichEditor(), controller=Controller())

        self.assertEqual(EditorView.export_image(view), "ok")
        self.assertEqual(events, ["flush", "export"])

    def test_batch_translation_does_not_keep_old_rich_document(self):
        old_document = {
            "format": "richtext.v1",
            "blocks": [{"type": "paragraph", "inlines": [
                {"type": "text", "text": "旧", "style": {"bold": True}},
            ]}],
        }

        class Model:
            def __init__(self):
                self.regions = [{"translation": "旧", "translation_rich": old_document}]

            def get_regions(self):
                return self.regions

            def get_region_by_index(self, index):
                return self.regions[index]

            def update_regions(self, updates, **_kwargs):
                for index, value in updates.items():
                    self.regions[index] = value

        class ControllerHarness:
            _replace_plain_translation = EditorController._replace_plain_translation
            update_multiple_translations = EditorController.update_multiple_translations

            def __init__(self):
                self.model = Model()
                self.logger = logging.getLogger(__name__)

            def _merge_live_geometry_state(self, _index, region_data):
                return region_data

            def _rules_rich_for_full_replacement(self, _region_data, _translation):
                # 本用例只关注旧富文本被丢弃；自动规则开关视为关闭
                return None

            def execute_command(self, command):
                command.redo()

        controller = ControllerHarness()
        controller.update_multiple_translations({0: "新"})
        region = controller.model.regions[0]

        self.assertEqual(region["translation"], "新")
        self.assertNotEqual(region.get("translation_rich"), old_document)
        if region.get("translation_rich") is not None:
            self.assertEqual(visible_text_from_document(region["translation_rich"]), "新")

    def test_controller_style_only_rich_edit_preserves_translation_raw(self):
        old_document = {
            "format": "richtext.v1",
            "blocks": [{"type": "paragraph", "inlines": [
                {"type": "text", "text": "AB", "style": {"bold": True}},
            ]}],
        }
        new_document = copy.deepcopy(old_document)
        new_document["blocks"][0]["inlines"][0]["style"]["italic"] = 10

        class ControllerHarness:
            update_translation_rich = EditorController.update_translation_rich

            def __init__(self):
                self.region = {
                    "translation": "AB",
                    "translation_raw": "A_B",
                    "translation_rich": old_document,
                    "font_size": 0,
                }
                self.updated = None

            def _get_region_by_index(self, _index):
                return self.region

            def _merge_live_geometry_state(self, _index, region_data):
                return region_data

            def _resolve_region_render_params(self, _index, _region_data):
                return SimpleNamespace(font_size=0)

            def _build_region_update_command(self, **kwargs):
                self.updated = kwargs["new_data"]
                return object()

            def execute_command(self, _command):
                pass

        controller = ControllerHarness()
        controller.update_translation_rich(0, new_document, "AB")

        self.assertIsNotNone(controller.updated)
        self.assertEqual(controller.updated["translation_raw"], "A_B")


if __name__ == "__main__":
    unittest.main()
