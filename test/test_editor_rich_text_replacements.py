"""Floating rich-text body must run the same text-replacement rules as the raw path.

Run under pytest or directly: ``python test/test_editor_rich_text_replacements.py -v``.
"""

import _bootstrap  # noqa: F401, I001

import copy
import logging
import re
import unittest
from unittest.mock import patch

import numpy as np

from PyQt6.QtGui import QUndoStack
from PyQt6.QtWidgets import QApplication

from editor.editor_controller import EditorController
from editor.rich_text_editor_state import RichTextEditorState
from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import plain_text_of
from manga_translator.rendering.rich_text_sync import apply_replacements_to_document


class TranslationHarness:
    """Use real controller and undo paths while isolating document geometry."""

    update_translation_raw = EditorController.update_translation_raw
    update_translation_rich = EditorController.update_translation_rich
    _apply_translation_replacements = EditorController._apply_translation_replacements
    _sync_rich_for_plain_edit = EditorController._sync_rich_for_plain_edit
    _update_translation_pair = EditorController._update_translation_pair
    _replace_plain_translation = EditorController._replace_plain_translation
    _build_region_update_command = EditorController._build_region_update_command

    def __init__(self, region):
        self.region = copy.deepcopy(region)
        self.model = self
        self.logger = logging.getLogger(__name__)
        self.commands = []
        self.undo_stack = QUndoStack()

    def get_region_by_index(self, _index):
        return copy.deepcopy(self.region)

    def _auto_rich_text_rules_enabled(self):
        return False

    def _merge_live_geometry_state(self, _index, region):
        return region

    def _resolve_region_render_params(self, _index, _region):
        return {}

    def update_region(self, _index, region):
        self.region = copy.deepcopy(region)

    def execute_command(self, command):
        self.commands.append(command)
        self.undo_stack.push(command)


class RichTextReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_floating_ellipsis_matches_raw_translation_input(self):
        replacements = {
            "common": [(re.compile(r"\.{3}"), "…")],
            "horizontal": [],
            "vertical": [(re.compile("…"), "︙")],
        }
        with (
            patch("manga_translator.rendering.text_replacements.load_replacements", return_value=replacements),
            patch("manga_translator.rendering.rich_text_sync.load_replacements", return_value=replacements),
            patch("editor.editor_controller._sync_white_frame_size_for_font_change"),
        ):
            for direction in ("h", "v"):
                with self.subTest(direction=direction):
                    region = {"translation": "", "direction": direction}
                    raw = TranslationHarness(region)
                    state = RichTextEditorState()
                    state.bind_region(0, region)
                    floating = TranslationHarness(region)
                    for count in range(1, 4):
                        raw.update_translation_raw(0, "." * count)
                        state.apply_qt_contents_change("." * count, count - 1, 0, 1)
                        floating.update_translation_rich(*state.mark_document_emitted())
                        self.assertEqual(floating.region["translation"], raw.region["translation"])

                    self.assertEqual(plain_text_of(floating.region["translation_rich"]), raw.region["translation"])
                    self.assertEqual(floating.region["translation_raw"], "...")
                    text_render.set_font("Microsoft YaHei")

                    def render(value):
                        if direction == "v":
                            return text_render.put_text_vertical(40, value, 240, "center", (0, 0, 0), None, 1.0)
                        return text_render.put_text_horizontal(40, value, 240, 240, "center", False, (0, 0, 0), None, line_spacing=1.0)

                    np.testing.assert_array_equal(render(raw.region["translation"]), render(floating.region["translation_rich"]))
                    self.assertEqual(floating.undo_stack.count(), 1)
                    saved_region = copy.deepcopy(floating.region)
                    floating.undo_stack.undo()
                    self.assertEqual(floating.region, region)
                    floating.undo_stack.redo()
                    self.assertEqual(floating.region, saved_region)

    def test_style_only_edit_preserves_raw_without_repeating_replacements(self):
        original = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [{"type": "text", "text": "aa", "style": {}}]}]}
        region = {"translation": "aa", "translation_raw": "a", "translation_rich": original, "direction": "h"}
        styled = copy.deepcopy(original)
        styled["blocks"][0]["inlines"][0]["style"] = {"bold": True}
        controller = TranslationHarness(region)
        with (
            patch("manga_translator.rendering.rich_text_sync.load_replacements", return_value={"common": [(re.compile("a"), "aa")]}),
            patch("editor.editor_controller._sync_white_frame_size_for_font_change"),
        ):
            controller.update_translation_rich(0, styled, "aa")
        self.assertEqual(controller.region["translation"], "aa")
        self.assertEqual(controller.region["translation_raw"], "a")
        self.assertEqual(controller.region["translation_rich"], styled)

    def test_replacements_preserve_ruby_tcy_styles_and_line_breaks(self):
        run = {"type": "text", "text": "...", "style": {"color": "#ff0000"}}
        document = {"format": "richtext.v1", "blocks": [
            {"type": "paragraph", "inlines": [{"type": "ruby", "base": [run], "text": [{"type": "text", "text": "...", "style": {}}]}]},
            {"type": "paragraph", "inlines": [{"type": "tcy", "content": [run]}]},
        ]}
        result = apply_replacements_to_document(document, 0, {"common": [(re.compile(r"\.{3}"), "…")]}).to_dict()
        self.assertEqual(plain_text_of(result), "…\n…")
        ruby = result["blocks"][0]["inlines"][0]
        self.assertEqual(ruby["type"], "ruby")
        self.assertEqual(ruby["base"][0]["style"]["color"], "#ff0000")
        self.assertEqual(ruby["text"][0]["text"], "...")
        self.assertEqual(result["blocks"][1]["inlines"][0]["type"], "tcy")


if __name__ == "__main__":
    unittest.main()
