"""Floating rich-text body must run the same text-replacement rules as the raw path.

Run under pytest or directly: ``python test/test_editor_rich_text_replacements.py -v``.
"""

import _bootstrap  # noqa: F401, I001

import copy
import logging
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from PyQt6.QtCore import QCoreApplication, QEvent, QObject
from PyQt6.QtGui import QUndoStack
from PyQt6.QtWidgets import QApplication

from editor.document_state import DocumentSnapshot
from editor.editor_controller import EditorController
from editor.editor_model import EditorModel
from editor.render_text_value import render_text_value_from_region
from editor.rich_text_editing import document_from_region, plain_text_to_storage_text
from editor.rich_text_editor_state import RichTextEditorState
from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import (
    ensure_rich_text_document,
    normalize_rich_linebreaks,
    plain_text_of,
)
from manga_translator.rendering.rich_text_rules import _parse_rules
from manga_translator.rendering.rich_text_sync import apply_replacements_to_document
from manga_translator.rendering.text_replacements import apply_replacements, load_replacements
from ui.editor.view import EditorView
from ui.widgets.rich_text_floating_editor import RichTextFloatingEditor


class TranslationHarness(QObject):
    """Use real controller and undo paths while isolating document geometry."""

    update_translation_raw = EditorController.update_translation_raw
    update_translation_rich = EditorController.update_translation_rich
    _apply_translation_replacements = EditorController._apply_translation_replacements
    _sync_rich_for_plain_edit = EditorController._sync_rich_for_plain_edit
    _update_translation_pair = EditorController._update_translation_pair
    _replace_plain_translation = EditorController._replace_plain_translation
    _build_region_update_command = EditorController._build_region_update_command

    def __init__(self, region):
        """Keep an isolated region and real Qt undo stack for each test."""
        super().__init__()
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
        """Keep the QApplication alive for all controller and widget tests."""
        cls.app = QApplication.instance() or QApplication([])

    def test_floating_ellipsis_matches_raw_translation_input(self):
        """Match both rendering directions and undo/redo against raw input."""
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
                        """Render each path with identical layout parameters."""
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
        """Do not run a non-idempotent rule on a rebound style-only edit."""
        original = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [{"type": "text", "text": "aa", "style": {}}]}]}
        region = {"translation": "aa", "translation_raw": "a", "translation_rich": original, "direction": "h"}
        styled = copy.deepcopy(original)
        styled["blocks"][0]["inlines"][0]["style"] = {"bold": True}
        controller = TranslationHarness(region)
        with (
            patch("manga_translator.rendering.rich_text_sync.load_replacements", return_value={"common": [(re.compile("a"), "aa")]}),
            patch("editor.editor_controller._sync_white_frame_size_for_font_change"),
        ):
            controller.update_translation_rich(0, styled, "aa", False)
        self.assertEqual(controller.region["translation"], "aa")
        self.assertEqual(controller.region["translation_raw"], "a")
        self.assertEqual(controller.region["translation_rich"], styled)

    def test_live_input_matching_replaced_text_still_updates_raw_and_styles(self):
        """Distinguish body edits from output collisions and style changes."""
        region = {"translation": "", "direction": "h"}
        state = RichTextEditorState()
        state.bind_region(0, region)
        controller = TranslationHarness(region)
        with (
            patch("manga_translator.rendering.rich_text_sync.load_replacements", return_value={"common": [(re.compile("a"), "aa")]}),
            patch("editor.editor_controller._sync_white_frame_size_for_font_change"),
        ):
            for count in (1, 2):
                state.apply_qt_contents_change("a" * count, count - 1, 0, 1)
                controller.update_translation_rich(*state.mark_document_emitted())
                self.assertEqual(controller.region["translation"], "aa" * count)
                self.assertEqual(controller.region["translation_raw"], "a" * count)

            for rebind, style in ((False, "bold"), (True, "italic")):
                if rebind:
                    state.bind_region(0, controller.region)
                styled = copy.deepcopy(state.document)
                styled["blocks"][0]["inlines"][0]["style"][style] = True
                state.replace_document(styled)
                controller.update_translation_rich(*state.mark_document_emitted())
                self.assertEqual(controller.region["translation"], "aaaa")
                self.assertEqual(controller.region["translation_raw"], "aa")
                self.assertTrue(controller.region["translation_rich"]["blocks"][0]["inlines"][0]["style"][style])

    def test_replacements_preserve_ruby_tcy_styles_and_line_breaks(self):
        """Keep manual nodes and annotations while replacing their base text."""
        run = {"type": "text", "text": "...", "style": {"color": "#ff0000"}}
        document = {"format": "richtext.v1", "blocks": [
            {"type": "paragraph", "inlines": [{"type": "ruby", "base": [run], "text": [{"type": "text", "text": "...", "style": {}}]}]},
            {"type": "paragraph", "inlines": [{"type": "tcy", "content": [run]}]},
        ]}
        result = apply_replacements_to_document(document, "h", {"common": [(re.compile(r"\.{3}"), "…")]}).to_dict()
        self.assertEqual(plain_text_of(result), "…\n…")
        ruby = result["blocks"][0]["inlines"][0]
        self.assertEqual(ruby["type"], "ruby")
        self.assertEqual(ruby["base"][0]["style"]["color"], "#ff0000")
        self.assertEqual(ruby["text"][0]["text"], "...")
        self.assertEqual(result["blocks"][1]["inlines"][0]["type"], "tcy")

    def test_widget_signal_carries_body_changes_to_controller(self):
        """Own model notifications preserve input and Qt undo; external ones refresh."""
        config = SimpleNamespace(app=SimpleNamespace(saved_rich_text_presets=[]))
        service = SimpleNamespace(
            get_config=lambda: config, get_config_reference=lambda: config
        )
        with (
            patch("ui.widgets.rich_text_floating_editor.get_config_service", return_value=service),
            patch("ui.widgets.rich_text_floating_editor.get_i18n_manager", return_value=None),
            patch("manga_translator.rendering.rich_text_sync.load_replacements", return_value={"common": [(re.compile("a"), "aa")]}),
            patch("editor.editor_controller._sync_white_frame_size_for_font_change"),
        ):
            editor = RichTextFloatingEditor()
            try:
                region = {"translation": "", "direction": "h"}
                controller = TranslationHarness(region)
                model = EditorModel()
                model.apply_document_snapshot(DocumentSnapshot(
                    "synthetic.png", np.zeros((2, 2, 3), dtype=np.uint8), regions=[region]
                ))
                model.set_selection([0])
                controller.model = model
                view = SimpleNamespace(
                    rich_text_editor=editor, model=model,
                    _sync_rich_text_editor_region=lambda index, **kwargs: editor.refresh_region_if_changed(
                        index, model.get_region_by_index(index)
                    ),
                )
                notifications = []

                def notify(change):
                    """Exercise the actual view guard for every real model notification."""
                    notifications.append(change)
                    EditorView._on_regions_changed_for_rich_editor(view, change)

                model.regions_changed.connect(notify)
                editor.rich_text_changed.connect(controller.update_translation_rich)
                editor.set_region(0, region)
                editor.show()
                self.app.processEvents()
                for text in ("a", "aa"):
                    editor.text_box.insertPlainText("a")
                    editor.flush_pending_changes()
                    saved = model.get_region_by_index(0)
                    self.assertEqual(saved["translation"], text * 2)
                    self.assertEqual(saved["translation_raw"], text)
                    self.assertEqual(editor.text_box.toPlainText(), text)
                    self.assertTrue(editor.text_box.document().isUndoAvailable())
                self.assertGreaterEqual(len(notifications), 2)
                editor.text_box.undo()
                editor.flush_pending_changes()
                self.assertNotEqual(editor.text_box.toPlainText(), "aa")
                editor.text_box.redo()
                editor.flush_pending_changes()
                self.assertEqual(editor.text_box.toPlainText(), "aa")
                self.assertEqual(model.get_region_by_index(0)["translation"], "aaaa")
                external = model.get_region_by_index(0)
                external.update(translation="external", translation_raw="external")
                external.pop("translation_rich", None)
                model.update_region(0, external)
                self.assertEqual(editor.text_box.toPlainText(), "external")
            finally:
                editor.close()
                editor.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def _compare_paths(self, text, replacements, direction="h", rules=None):
        """Commit identical input through real raw and floating controller paths."""
        region = {"translation": "", "direction": direction}
        raw = TranslationHarness(region)
        floating = TranslationHarness(region)
        raw._auto_rich_text_rules_enabled = lambda: rules is not None
        floating._auto_rich_text_rules_enabled = lambda: rules is not None
        state = RichTextEditorState()
        state.auto_rules_provider = lambda: rules is not None
        state.bind_region(0, region)
        with (
            patch("manga_translator.rendering.text_replacements.load_replacements", return_value=replacements),
            patch("manga_translator.rendering.rich_text_sync.load_replacements", return_value=replacements),
            patch("manga_translator.rendering.rich_text_rules.load_rich_text_rules", return_value=rules),
            patch("editor.editor_controller._sync_white_frame_size_for_font_change"),
        ):
            raw.update_translation_raw(0, plain_text_to_storage_text(text))
            state.apply_qt_contents_change(text, 0, 0, len(text))
            payload = state.mark_document_emitted()
            if payload is not None:
                floating.update_translation_rich(*payload)
        return raw.region, floating.region

    def test_zero_width_prefix_and_suffix_match_raw_path(self):
        """Honor zero-width anchors and preserve the pure-text empty-input policy."""
        replacements = {"common": [(re.compile("^"), "Prefix: "), (re.compile("$"), "!")]}
        for text in ("hello", "A\nB", ""):
            with self.subTest(text=text):
                raw, floating = self._compare_paths(text, replacements)
                self.assertEqual(floating["translation"], raw["translation"])

    def test_default_vertical_whitespace_matches_raw_path(self):
        """Compress spaces adjacent to a protected paragraph break."""
        replacements = load_replacements(str(_bootstrap.ROOT / "config/text_replacements.yaml"))
        raw, floating = self._compare_paths("A  \nB", replacements, direction="v")
        self.assertEqual(floating["translation"], raw["translation"])

    def test_replacement_linebreak_markers_become_paragraphs(self):
        """Normalize generated BR spellings without losing surrounding manual nodes."""
        for marker in ("[BR]", "<br/>", "【BR】", "\n"):
            with self.subTest(marker=marker):
                replacements = {"common": [(re.compile(r"\|"), marker)]}
                raw, floating = self._compare_paths("A|B", replacements)
                self.assertEqual(
                    plain_text_of(render_text_value_from_region(floating)),
                    normalize_rich_linebreaks(raw["translation"]),
                )
        doc = document_from_region({"translation": "A|B"})
        doc["blocks"][0]["inlines"][0]["style"] = {"bold": True}
        replaced = apply_replacements_to_document(doc, "h", {"common": [(re.compile(r"\|"), "[BR]")]}).to_dict()
        self.assertEqual(plain_text_of(replaced), "A\nB")
        self.assertTrue(all(block["inlines"][0]["style"]["bold"] for block in replaced["blocks"]))

    def test_replacement_created_automatic_style_matches_raw_path(self):
        """Apply automatic rules to the new ellipsis produced by replacement."""
        replacements = {"common": [(re.compile(r"\.{3}"), "…")]}
        rules = _parse_rules({"common": [{"pattern": "…", "style": {"scale": 2.0}}]})
        raw, floating = self._compare_paths("...", replacements, rules=rules)
        self.assertEqual(floating["translation_rich"], raw["translation_rich"])
        self.assertEqual(ensure_rich_text_document(floating["translation_rich"]).paragraphs[0].spans[0].style.scale, 2.0)

    def test_replacement_styles_preserve_manual_and_cleared_styles(self):
        """Skip manual spans and unchanged matches whose style was cleared by the user."""
        rules = _parse_rules({"common": [{"pattern": "…", "style": {"scale": 2.0}}]})
        with (
            patch("manga_translator.rendering.rich_text_rules.load_rich_text_rules", return_value=rules),
            patch("manga_translator.rendering.rich_text_sync.load_replacements", return_value={"common": [(re.compile(r"\.{3}"), "…")]}),
            patch("editor.editor_controller._sync_white_frame_size_for_font_change"),
        ):
            for old_text, text, style in (("", "...", {"bold": True}), ("…", "…x", {})):
                with self.subTest(text=text):
                    controller = TranslationHarness({"translation": old_text, "direction": "h"})
                    controller._auto_rich_text_rules_enabled = lambda: True
                    document = document_from_region({"translation": text})
                    document["blocks"][0]["inlines"][0]["style"] = style
                    controller.update_translation_rich(0, document, text, True)
                    saved = ensure_rich_text_document(controller.region["translation_rich"])
                    self.assertEqual(saved.paragraphs[0].spans[0].style.scale, 1.0)
                    self.assertEqual(saved.paragraphs[0].spans[0].style.to_dict(), style)

    def test_zero_width_insertions_use_existing_node_and_boundary_policy(self):
        """Keep insertions inside ruby/TCY, and leave boundary insertions unstyled."""
        for node in (
            {"type": "ruby", "base": [{"type": "text", "text": "AB", "style": {"bold": True}}], "text": [{"type": "text", "text": "note", "style": {}}]},
            {"type": "tcy", "content": [{"type": "text", "text": "AB", "style": {"bold": True}}]},
        ):
            with self.subTest(node=node["type"]):
                document = {"format": "richtext.v1", "blocks": [{"type": "paragraph", "inlines": [node]}]}
                rules = {"common": [(re.compile("^"), "P"), (re.compile("(?=B)"), "X"), (re.compile("$"), "S")]}
                result = apply_replacements_to_document(document, "h", rules).to_dict()
                self.assertEqual(plain_text_of(result), "PAXBS")
                inlines = result["blocks"][0]["inlines"]
                self.assertEqual([inline["type"] for inline in inlines], ["text", node["type"], "text"])
                self.assertEqual(inlines[0]["style"], {})
                self.assertEqual(inlines[-1]["style"], {})
                runs = inlines[1]["base" if node["type"] == "ruby" else "content"]
                self.assertEqual(runs[0]["text"], "AXB")
                self.assertTrue(runs[0]["style"]["bold"])

    def test_rich_replacement_matches_plain_regex_semantics(self):
        """Match anchors, empty matches, backreferences and protected breaks."""
        for text in ("ab", "x", "A\nB", "A\n\nB", "A[BR]B", "A<br/>B"):
            for pattern, replacement in (("x*", "_"), ("(?=b)", "X"), ("(a)(b)", r"\2\1"), (r"\s+", " "), (".*", "X")):
                with self.subTest(text=text, pattern=pattern):
                    document = document_from_region({"translation": text})
                    storage = plain_text_to_storage_text(plain_text_of(document))
                    rules = {"common": [(re.compile(pattern), replacement)]}
                    expected = normalize_rich_linebreaks(apply_replacements(storage, 0, rules))
                    result = apply_replacements_to_document(document, "h", rules)
                    self.assertEqual(result.plain_text(), expected)


if __name__ == "__main__":
    unittest.main()
