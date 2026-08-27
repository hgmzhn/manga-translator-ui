import _bootstrap  # noqa: F401, I001

import unittest
from types import SimpleNamespace


from desktop_qt_ui.editor.render_text_value import (
    has_renderable_text,
    render_text_value_from_text_block,
)
from desktop_qt_ui.editor.rich_text_editing import (
    apply_qt_text_change,
    apply_ruby_to_range,
    apply_style_to_range,
    apply_tcy_to_range,
    apply_text_change,
    is_rich_text_document,
    python_index_to_utf16_offset,
    remove_ruby_from_range,
    remove_tcy_from_range,
    storage_text_to_editor_text,
    style_for_range,
    style_row_coverage,
    styled_segments_for_range,
    styled_text_for_key,
    text_style_from_control_values,
    text_style_to_control_values,
    utf16_offset_to_python_index,
)
from manga_translator.rendering.rich_text import ensure_rich_text_document
from manga_translator.utils import TextBlock
from desktop_qt_ui.editor.rich_text_editor_state import RichTextEditorState
from desktop_qt_ui.ui.widgets.rich_text_editor_components import default_style_patch
from desktop_qt_ui.ui.widgets.rich_text_floating_editor import RichTextFloatingEditor



def _doc(text, style):
    return {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [{"type": "text", "text": text, "style": style}],
            }
        ],
    }


def _ruby_inline(base_text, ruby_text, style=None):
    return {
        "type": "ruby",
        "base": [{"type": "text", "text": base_text, "style": dict(style or {})}],
        "text": [{"type": "text", "text": ruby_text, "style": {}}],
    }


def _node_doc():
    # 可见文本 "普通漢字2026後"：text + ruby + tcy + text 混排
    return {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [
                    {"type": "text", "text": "普通", "style": {}},
                    _ruby_inline("漢字", "かんじ"),
                    {
                        "type": "tcy",
                        "content": [{"type": "text", "text": "2026", "style": {}}],
                    },
                    {"type": "text", "text": "後", "style": {}},
                ],
            }
        ],
    }


class RichTextEditingTests(unittest.TestCase):
    def test_insert_inside_run_inherits_style(self):
        document = _doc("红字", {"color": "#ff0000"})
        updated = apply_text_change(document, "红色字", 1, 0, 1)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [{"type": "text", "text": "红色字", "style": {"color": "#ff0000"}}],
        )

    def test_insert_at_run_start_does_not_inherit_style(self):
        document = _doc("红字", {"color": "#ff0000"})
        updated = apply_text_change(document, "新红字", 0, 0, 1)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "新", "style": {}},
                {"type": "text", "text": "红字", "style": {"color": "#ff0000"}},
            ],
        )

    def test_insert_at_run_end_does_not_inherit_style(self):
        document = _doc("红字", {"color": "#ff0000"})
        updated = apply_text_change(document, "红字新", 2, 0, 1)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "红字", "style": {"color": "#ff0000"}},
                {"type": "text", "text": "新", "style": {}},
            ],
        )

    def test_style_range_uses_position_not_text_content(self):
        document = {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "红字", "style": {"color": "#ff0000"}},
                        {"type": "text", "text": "红字", "style": {"color": "#0000ff"}},
                    ],
                }
            ],
        }
        updated = apply_style_to_range(document, 2, 3, {"stroke": {"width": 0.2}})

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "红字", "style": {"color": "#ff0000"}},
                {
                    "type": "text",
                    "text": "红",
                    "style": {"color": "#0000ff", "stroke": {"width": 0.2}},
                },
                {"type": "text", "text": "字", "style": {"color": "#0000ff"}},
            ],
        )

    def test_storage_text_uses_real_newlines_in_editor(self):
        self.assertEqual(
            storage_text_to_editor_text("第一行[BR]第二行"), "第一行\n第二行"
        )

    def test_storage_text_preserves_spaces_around_line_breaks(self):
        self.assertEqual(
            storage_text_to_editor_text(
                "第一行  [BR]  第二行\t<br />\t第三行 【BR】 第四行"
            ),
            "第一行  \n  第二行\t\n\t第三行 \n 第四行",
        )


    def test_tcy_wraps_selected_range_by_position(self):
        document = _doc("ABAB", {})
        updated = apply_tcy_to_range(document, 2, 4)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "AB", "style": {}},
                {
                    "type": "tcy",
                    "content": [{"type": "text", "text": "AB", "style": {}}],
                },
            ],
        )

    def test_remove_tcy_unwraps_selected_range(self):
        document = apply_tcy_to_range(_doc("甲乙", {"color": "#ff0000"}), 0, 1)
        updated = remove_tcy_from_range(document, 0, 1)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [{"type": "text", "text": "甲乙", "style": {"color": "#ff0000"}}],
        )

    def test_ruby_wraps_selected_range_by_position(self):
        document = _doc("漢字漢字", {"color": "#ff0000"})
        updated = apply_ruby_to_range(document, 2, 4, "かんじ")

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "漢字", "style": {"color": "#ff0000"}},
                {
                    "type": "ruby",
                    "base": [
                        {"type": "text", "text": "漢字", "style": {"color": "#ff0000"}}
                    ],
                    "text": [{"type": "text", "text": "かんじ", "style": {}}],
                },
            ],
        )

    def test_remove_ruby_unwraps_selected_range_by_position(self):
        document = apply_ruby_to_range(
            _doc("漢字漢字", {"color": "#ff0000"}), 2, 4, "かんじ"
        )
        updated = remove_ruby_from_range(document, 2, 4)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [{"type": "text", "text": "漢字漢字", "style": {"color": "#ff0000"}}],
        )

    def test_empty_ruby_text_unwraps_instead_of_creating_empty_ruby_node(self):
        document = apply_ruby_to_range(_doc("漢字", {"color": "#ff0000"}), 0, 2, "")

        self.assertEqual(
            document["blocks"][0]["inlines"],
            [{"type": "text", "text": "漢字", "style": {"color": "#ff0000"}}],
        )

    def test_style_for_range_reports_ruby_text(self):
        document = apply_ruby_to_range(
            _doc("漢字", {"color": "#ff0000"}), 0, 2, "かんじ"
        )

        self.assertEqual(
            style_for_range(document, 0, 2),
            {"ruby": True, "rubyText": "かんじ", "color": "#ff0000"},
        )

    def test_style_row_coverage_distinguishes_partial_from_full(self):
        document = {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "甲", "style": {"bold": True}},
                        {"type": "text", "text": "乙", "style": {}},
                    ],
                }
            ],
        }

        self.assertEqual(style_row_coverage(document, 0, 0, "B"), (True, False))
        self.assertEqual(style_row_coverage(document, 0, 1, "B"), (True, True))

    def test_stretch_style_targets_selected_text_and_round_trips_controls(self):
        document = apply_style_to_range(
            _doc("甲乙丙", {}),
            1,
            2,
            {"transform": {"scaleX": 1.6, "scaleY": 0.8}},
        )

        self.assertEqual(
            document["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "甲", "style": {}},
                {
                    "type": "text",
                    "text": "乙",
                    "style": {"transform": {"scaleX": 1.6, "scaleY": 0.8}},
                },
                {"type": "text", "text": "丙", "style": {}},
            ],
        )
        self.assertEqual(style_row_coverage(document, 1, 2, "WH"), (True, True))
        values = text_style_to_control_values(
            {"transform": {"scaleX": 1.6, "scaleY": 0.8}}
        )
        self.assertEqual((values["scaleX"], values["scaleY"]), (1.6, 0.8))
        self.assertEqual(
            text_style_from_control_values(values, {"scaleX", "scaleY"}),
            {"transform": {"scaleX": 1.6, "scaleY": 0.8}},
        )

    def test_styled_text_for_key_reports_matching_run_text(self):
        document = {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "红字", "style": {}},
                        {"type": "text", "text": "红字", "style": {"fontSize": 29}},
                    ],
                }
            ],
        }

        self.assertEqual(styled_text_for_key(document, 0, 0, "S"), "红字")

    def test_styled_text_for_key_prefers_selected_text(self):
        document = _doc("这次是要_戏和唱~卡拉OK对吧!", {})

        # 无局部样式时不再把选区伪装成“默认样式”。
        self.assertEqual(styled_text_for_key(document, 5, 7, "S"), "")

    def test_non_adjacent_equal_styles_remain_separate_segments(self):
        document = {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "黑", "style": {"color": "#000000"}},
                        {"type": "text", "text": "红红", "style": {"color": "#ff0000"}},
                        {"type": "text", "text": "黑", "style": {"color": "#000000"}},
                    ],
                }
            ],
        }

        segments = styled_segments_for_range(document, 0, 4)

        self.assertEqual([segment.text for segment in segments], ["黑", "红红", "黑"])
        self.assertEqual(
            [(segment.start, segment.end) for segment in segments],
            [(0, 1), (1, 3), (3, 4)],
        )

    def test_unstyled_text_is_not_exposed_as_default_segment(self):
        document = {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "连", "style": {"bold": True}},
                        {"type": "text", "text": "游", "style": {}},
                        {"type": "text", "text": "戏", "style": {"bold": True}},
                        {"type": "text", "text": "也不玩了", "style": {}},
                    ],
                }
            ],
        }

        self.assertEqual(
            [segment.text for segment in styled_segments_for_range(document, 0, 0)],
            ["连", "戏"],
        )
        self.assertEqual(styled_text_for_key(document, 0, 0, "B"), "连 / 戏")

    def test_utf16_offsets_do_not_split_emoji(self):
        text = "A😀B"
        self.assertEqual(python_index_to_utf16_offset(text, 2), 3)
        self.assertEqual(utf16_offset_to_python_index(text, 3), 2)

        document = _doc(text, {})
        updated = apply_qt_text_change(document, text, "A😀XB", 3, 0, 1)
        styled = apply_style_to_range(updated, 1, 2, {"bold": True})

        self.assertEqual(
            styled["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "A", "style": {}},
                {"type": "text", "text": "😀", "style": {"bold": True}},
                {"type": "text", "text": "XB", "style": {}},
            ],
        )

    def test_font_size_patch_starts_from_selected_region_font_size(self):
        self.assertEqual(
            default_style_patch("S", region_font_size=37), {"fontSize": 37}
        )

    def test_font_size_patch_falls_back_when_region_size_is_invalid(self):
        self.assertEqual(
            default_style_patch("S", region_font_size=None), {"fontSize": 24}
        )
        self.assertEqual(
            default_style_patch("S", region_font_size="invalid"), {"fontSize": 24}
        )

    def test_font_size_toolbar_uses_selected_region_font_size(self):
        state = RichTextEditorState()
        state.bind_region(5, {"translation": "字", "font_size": 37})
        state.set_selection(0, 1)
        committed = []
        editor = SimpleNamespace(
            _updating=False,
            _state=state,
            _commit_document=committed.append,
        )

        RichTextFloatingEditor._on_toolbar_toggled(editor, "S", True)

        self.assertEqual(
            committed[0]["blocks"][0]["inlines"][0]["style"],
            {"fontSize": 37.0},
        )

    def test_render_text_value_prefers_rich_text_document(self):
        document = _doc("大", {"fontSize": 60})
        region = TextBlock(
            [[[0, 0], [100, 0], [100, 100], [0, 100]]],
            texts=[""],
            translation="大",
            translation_rich=document,
            font_size=20,
        )

        self.assertEqual(render_text_value_from_text_block(region), document)

    # ------------------------------------------------------------------
    # F01：编辑操作必须保留 ruby/tcy 节点
    # ------------------------------------------------------------------

    def test_typing_elsewhere_preserves_ruby_and_tcy_nodes(self):
        document = _node_doc()
        updated = apply_text_change(document, "新普通漢字2026後", 0, 0, 1)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "新普通", "style": {}},
                _ruby_inline("漢字", "かんじ"),
                {
                    "type": "tcy",
                    "content": [{"type": "text", "text": "2026", "style": {}}],
                },
                {"type": "text", "text": "後", "style": {}},
            ],
        )

    def test_wrap_other_range_preserves_existing_nodes(self):
        document = _node_doc()
        updated = apply_tcy_to_range(document, 0, 2)  # 只包 "普通"

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {
                    "type": "tcy",
                    "content": [{"type": "text", "text": "普通", "style": {}}],
                },
                _ruby_inline("漢字", "かんじ"),
                {
                    "type": "tcy",
                    "content": [{"type": "text", "text": "2026", "style": {}}],
                },
                {"type": "text", "text": "後", "style": {}},
            ],
        )

    def test_style_other_range_preserves_existing_nodes(self):
        document = _node_doc()
        updated = apply_style_to_range(
            document, 8, 9, {"color": "#ff0000"}
        )  # 只染 "後"

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "普通", "style": {}},
                _ruby_inline("漢字", "かんじ"),
                {
                    "type": "tcy",
                    "content": [{"type": "text", "text": "2026", "style": {}}],
                },
                {"type": "text", "text": "後", "style": {"color": "#ff0000"}},
            ],
        )

    def test_typing_inside_ruby_base_joins_base(self):
        document = _node_doc()
        updated = apply_text_change(document, "普通漢X字2026後", 3, 0, 1)

        self.assertEqual(
            updated["blocks"][0]["inlines"][1],
            _ruby_inline("漢X字", "かんじ"),
        )

    def test_typing_inside_tcy_content_joins_content(self):
        document = _node_doc()
        updated = apply_text_change(document, "普通漢字20X26後", 6, 0, 1)

        self.assertEqual(
            updated["blocks"][0]["inlines"][2],
            {
                "type": "tcy",
                "content": [{"type": "text", "text": "20X26", "style": {}}],
            },
        )

    def test_typing_at_node_edges_stays_plain(self):
        document = _node_doc()

        before = apply_text_change(document, "普通X漢字2026後", 2, 0, 1)
        self.assertEqual(
            before["blocks"][0]["inlines"][:2],
            [
                {"type": "text", "text": "普通X", "style": {}},
                _ruby_inline("漢字", "かんじ"),
            ],
        )

        after = apply_text_change(document, "普通漢字X2026後", 4, 0, 1)
        self.assertEqual(
            after["blocks"][0]["inlines"][1:3],
            [
                _ruby_inline("漢字", "かんじ"),
                {"type": "text", "text": "X", "style": {}},
            ],
        )

    def test_deleting_entire_base_removes_node(self):
        document = _node_doc()
        updated = apply_text_change(document, "普通2026後", 2, 2, 0)

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "普通", "style": {}},
                {
                    "type": "tcy",
                    "content": [{"type": "text", "text": "2026", "style": {}}],
                },
                {"type": "text", "text": "後", "style": {}},
            ],
        )

    def test_wrap_overlapping_existing_node_dissolves_it(self):
        document = {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "AB", "style": {}},
                        {
                            "type": "tcy",
                            "content": [{"type": "text", "text": "CD", "style": {}}],
                        },
                    ],
                }
            ],
        }
        updated = apply_ruby_to_range(document, 1, 3, "x")

        self.assertEqual(
            updated["blocks"][0]["inlines"],
            [
                {"type": "text", "text": "A", "style": {}},
                _ruby_inline("BC", "x"),
                {"type": "text", "text": "D", "style": {}},
            ],
        )

    # ------------------------------------------------------------------
    # F11：协议判定/文本提取兼容 RichTextDocument 实例
    # ------------------------------------------------------------------

    def test_rich_text_document_instances_are_recognized(self):
        instance = ensure_rich_text_document(_doc("漢字", {}))

        self.assertTrue(is_rich_text_document(instance))
        self.assertEqual(storage_text_to_editor_text(instance), "漢字")

    # ------------------------------------------------------------------
    # F29：未翻译区域回退显示 OCR 原文
    # ------------------------------------------------------------------

    def test_render_text_value_falls_back_to_source_text_when_untranslated(self):
        region = TextBlock(
            [[[0, 0], [100, 0], [100, 100], [0, 100]]],
            texts=["原文プレビュー"],
            translation="",
        )

        self.assertEqual(render_text_value_from_text_block(region), "原文プレビュー")
        self.assertFalse(has_renderable_text(""))


if __name__ == "__main__":
    unittest.main()
