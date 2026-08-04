"""富文本 underline 样式的端到端回归：协议 / 渲染 / 编辑器 / 控件。

导入顺序刻意固定：先设 QT_QPA_PLATFORM，再导入 manga_translator（会拉起
torch），最后才导入 PyQt6 与 desktop_qt_ui —— Windows 上先 PyQt6 后 torch
会在加载 c10.dll 时报 OSError WinError 1114。
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from manga_translator.rendering import text_render
from manga_translator.rendering.rich_text import (
    RICH_TEXT_FORMAT,
    TextStyle,
    ensure_rich_text_document,
)

from PyQt6.QtWidgets import QApplication

from desktop_qt_ui.editor.rich_text_editing import (
    apply_style_to_range,
    style_for_range,
    style_row_coverage,
    text_style_from_control_values,
    text_style_to_control_values,
)
from desktop_qt_ui.ui.secondary_pages.rich_text_rules_editor import RichTextStyleControls
from desktop_qt_ui.ui.widgets.rich_text_editor_components import (
    STYLE_SPECS,
    default_style_patch,
    style_keys_for_segment,
)
from desktop_qt_ui.ui.widgets.rich_text_floating_editor import RichTextFloatingEditor

# QApplication 必须留模块级引用：被回收后再建控件会直接崩进程。
_APP = QApplication.instance() or QApplication([])


def _document(items):
    return {
        "format": RICH_TEXT_FORMAT,
        "blocks": [{
            "type": "paragraph",
            "inlines": [
                {"type": "text", "text": text, "style": style}
                for text, style in items
            ],
        }],
    }


def _alpha(layer):
    return (layer[:, :, 3] > 0).astype(np.uint8)


class UnderlineProtocolTests(unittest.TestCase):
    def test_underline_survives_style_round_trip(self):
        style = TextStyle.from_dict({"underline": True, "bold": True})

        self.assertIs(style.underline, True)
        self.assertEqual(style.to_dict()["underline"], True)
        self.assertEqual(TextStyle.from_dict(style.to_dict()).underline, True)

    def test_false_underline_is_not_written_out(self):
        self.assertNotIn("underline", TextStyle.from_dict({"underline": False}).to_dict())
        self.assertNotIn("underline", TextStyle().to_dict())

    def test_underline_is_a_known_document_key(self):
        document = ensure_rich_text_document(_document([("下划线", {"underline": True})]))

        self.assertTrue(document.blocks[0].spans[0].style.underline)

    def test_unknown_style_keys_are_still_rejected(self):
        with self.assertRaises(ValueError):
            TextStyle.from_dict({"underlined": True})


class UnderlineControlValueTests(unittest.TestCase):
    def test_control_values_expose_underline(self):
        values = text_style_to_control_values({"underline": True})

        self.assertIs(values["underline"], True)

    def test_control_values_rebuild_underline(self):
        style = text_style_from_control_values({"underline": True}, {"underline"})

        self.assertEqual(style, {"underline": True})

    def test_disabled_underline_is_not_written(self):
        self.assertEqual(text_style_from_control_values({"underline": True}, set()), {})

    def test_round_trip_keeps_every_supported_field(self):
        original = {
            "bold": True,
            "underline": True,
            "emphasis": True,
            "italic": 15.0,
            "color": "#E53935",
        }
        values = text_style_to_control_values(original)
        rebuilt = text_style_from_control_values(values, set(original))

        self.assertEqual(rebuilt, original)


class UnderlineDocumentQueryTests(unittest.TestCase):
    def test_style_for_range_reports_underline(self):
        document = _document([("前", {}), ("线", {"underline": True})])

        self.assertNotIn("underline", style_for_range(document, 0, 1))
        self.assertIs(style_for_range(document, 1, 2)["underline"], True)

    def test_underline_row_key_coverage(self):
        document = _document([("前", {}), ("线", {"underline": True})])

        self.assertEqual(style_row_coverage(document, 0, 2, "U"), (True, False))
        self.assertEqual(style_row_coverage(document, 1, 2, "U"), (True, True))

    def test_apply_style_to_range_writes_underline(self):
        document = apply_style_to_range(_document([("下划线", {})]), 0, 1, {"underline": True})
        inlines = document["blocks"][0]["inlines"]

        self.assertEqual(inlines[0]["text"], "下")
        self.assertIs(inlines[0]["style"]["underline"], True)
        self.assertNotIn("underline", inlines[1]["style"])


class UnderlineRenderingTests(unittest.TestCase):
    def setUp(self):
        text_render.set_font("Arial-Unicode-Regular.ttf")
        text_render.set_bold(False)

    def test_horizontal_underline_draws_one_line_across_the_run(self):
        plain = text_render.put_text_horizontal(
            48, _document([("ABC", {})]), 400, 200, "center", False,
            (255, 255, 255), None, stroke_width=0.0,
        )
        underlined = text_render.put_text_horizontal(
            48, _document([("ABC", {"underline": True})]), 400, 200, "center", False,
            (255, 255, 255), None, stroke_width=0.0,
        )

        self.assertEqual(plain.shape[1], underlined.shape[1])
        # 无降部字形时下划线落在原墨迹框之外，渲染框只往下长
        self.assertGreater(underlined.shape[0], plain.shape[0])
        rows = _alpha(underlined).sum(axis=1)
        full_rows = np.where(rows >= underlined.shape[1] - 2)[0]
        self.assertGreaterEqual(len(full_rows), 1)
        # 线在基线下方，必须落在正文墨迹之下
        self.assertGreater(int(full_rows.min()), plain.shape[0] // 2)

    def test_vertical_underline_draws_one_line_along_the_column(self):
        plain = text_render.put_text_vertical(
            48, _document([("あい", {})]), 400, "center", (255, 255, 255), None, 0,
            stroke_width=0.0,
        )
        underlined = text_render.put_text_vertical(
            48, _document([("あい", {"underline": True})]), 400, "center",
            (255, 255, 255), None, 0, stroke_width=0.0,
        )

        self.assertEqual(plain.shape[0], underlined.shape[0])
        # 竖排线画在列的一侧，渲染框只往侧向长
        self.assertGreater(underlined.shape[1], plain.shape[1])
        columns = _alpha(underlined).sum(axis=0)
        full_columns = np.where(columns >= underlined.shape[0] - 2)[0]
        self.assertGreaterEqual(len(full_columns), 1)
        self.assertGreater(int(full_columns.min()), plain.shape[1] // 2)

    def test_measure_matches_rendered_surface(self):
        for horizontal in (True, False):
            with self.subTest(horizontal=horizontal):
                document = _document([("あA", {"underline": True})])
                metrics = text_render.measure_rich_text_metrics(48, document, horizontal, 1.0, stroke_width=0.0)
                surface = (
                    text_render.put_text_horizontal(
                        48, document, 400, 200, "center", False,
                        (255, 255, 255), None, stroke_width=0.0,
                    )
                    if horizontal else
                    text_render.put_text_vertical(
                        48, document, 400, "center", (255, 255, 255), None, 0,
                        stroke_width=0.0,
                    )
                )
                self.assertEqual((metrics["width"], metrics["height"]), (surface.shape[1], surface.shape[0]))

    def test_underline_follows_the_flow_direction_not_the_glyph(self):
        # 字形旋转 90° 时下划线仍是沿行方向的一条横线（排版方向优先）
        rotated = text_render.put_text_horizontal(
            48, _document([("ABC", {"underline": True, "transform": {"rotation": 90}})]),
            400, 200, "center", False, (255, 255, 255), None, stroke_width=0.0,
        )
        rows = _alpha(rotated).sum(axis=1)

        self.assertGreaterEqual(int(rows.max()), rotated.shape[1] - 2)

    def test_vertical_underline_ignores_per_glyph_rotation(self):
        # 竖排自动旋转的角引号旁边，线仍然是上下方向的一整条
        layer = text_render.put_text_vertical(
            48, _document([("「あ」", {"underline": True})]), 400, "center",
            (255, 255, 255), None, 0, stroke_width=0.0,
        )
        columns = _alpha(layer).sum(axis=0)

        self.assertGreaterEqual(int(columns.max()), layer.shape[0] - 2)

    def test_adjacent_underlined_runs_join_into_one_line(self):
        layer = text_render.put_text_horizontal(
            48, _document([("AB", {"underline": True}), ("CD", {"underline": True})]),
            400, 200, "center", False, (255, 255, 255), None, stroke_width=0.0,
        )
        rows = _alpha(layer).sum(axis=1)

        self.assertGreaterEqual(int(rows.max()), layer.shape[1] - 3)

    def test_underlined_text_with_stroke_and_ruby_renders(self):
        document = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [{
                "type": "paragraph",
                "inlines": [
                    {
                        "type": "ruby",
                        "base": [{"type": "text", "text": "漢字", "style": {"underline": True}}],
                        "text": [{"type": "text", "text": "かんじ", "style": {}}],
                    },
                    {"type": "text", "text": "12", "style": {"underline": True}},
                ],
            }],
        }
        for horizontal in (True, False):
            with self.subTest(horizontal=horizontal):
                surface = (
                    text_render.put_text_horizontal(
                        48, document, 400, 200, "center", False, (255, 255, 255), (0, 0, 0),
                    )
                    if horizontal else
                    text_render.put_text_vertical(
                        48, document, 400, "center", (255, 255, 255), (0, 0, 0), 0,
                    )
                )
                self.assertIsNotNone(surface)


class UnderlineWidgetTests(unittest.TestCase):
    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
        _APP.processEvents()

    def test_toolbar_exposes_the_u_row_key(self):
        editor = RichTextFloatingEditor()
        self.widgets.append(editor)

        self.assertIn("U", editor.toolbar.buttons)
        self.assertEqual(default_style_patch("U"), {"underline": True})
        self.assertEqual(STYLE_SPECS["U"].name, "Underline")

    def test_toolbar_toggle_applies_underline_to_the_selection(self):
        editor = RichTextFloatingEditor()
        self.widgets.append(editor)
        editor.set_region(0, {"translation": "下划线"})
        editor.show()
        _APP.processEvents()

        editor._select_python_range(0, 2)
        editor.toolbar.buttons["U"].click()
        _APP.processEvents()

        inlines = editor._state.document["blocks"][0]["inlines"]
        self.assertIs(inlines[0]["style"]["underline"], True)
        self.assertEqual(inlines[0]["text"], "下划")

    def test_underlined_segment_lists_the_u_row(self):
        from desktop_qt_ui.editor.rich_text_editing import StyledTextSegment

        segment = StyledTextSegment(0, 1, "线", {"underline": True})

        self.assertIn("U", style_keys_for_segment(segment))

    def test_rules_editor_round_trips_underline(self):
        rules = RichTextStyleControls(lambda text: text)
        self.widgets.append(rules)

        rules.load_style({"underline": True})
        self.assertTrue(rules.underline.isChecked())
        self.assertIs(rules.style()["underline"], True)

        rules.load_style({})
        self.assertFalse(rules.underline.isChecked())
        self.assertNotIn("underline", rules.style())


def main() -> int:
    result = unittest.main(exit=False, verbosity=2).result
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
