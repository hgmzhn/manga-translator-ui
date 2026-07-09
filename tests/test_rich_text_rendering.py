import unittest

import numpy as np

from manga_translator.config import Config
from manga_translator.rendering import calc_box_from_font, render, text_render
from manga_translator.rendering.rich_text import (
    RICH_TEXT_FORMAT,
    ensure_rich_text_document,
    legacy_line_breaks_to_document,
)
from manga_translator.rendering.text_replacement_layout import (
    ReplacementLayoutRecord,
    sync_translation_raw_from_layout,
)
from manga_translator.utils import TextBlock


def _sample_document():
    return {
        "format": RICH_TEXT_FORMAT,
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [
                    {"type": "text", "text": "普通", "style": {}},
                    {
                        "type": "text",
                        "text": "红字",
                        "style": {
                            "color": "#ff0000",
                            "stroke": {"color": "#000000", "width": 2},
                        },
                    },
                    {
                        "type": "ruby",
                        "base": [
                            {"type": "text", "text": "漢字", "style": {}},
                        ],
                        "text": [
                            {"type": "text", "text": "かんじ", "style": {}},
                        ],
                    },
                    {
                        "type": "text",
                        "text": "点",
                        "style": {"emphasis": True},
                    },
                ],
            }
        ],
    }


class RichTextRenderingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text_render.set_font(text_render.DEFAULT_FONT)

    def test_legacy_break_markers_become_paragraph_blocks(self):
        document = legacy_line_breaks_to_document("红[BR]蓝<br>绿【BR】紫\n黑").to_dict()

        self.assertEqual(document["format"], RICH_TEXT_FORMAT)
        self.assertEqual(
            [
                block["inlines"][0]["text"] if block["inlines"] else ""
                for block in document["blocks"]
            ],
            ["红", "蓝", "绿", "紫", "黑"],
        )

    def test_rich_text_schema_rejects_noncanonical_fields(self):
        with self.assertRaises(ValueError):
            ensure_rich_text_document(
                {
                    "format": RICH_TEXT_FORMAT,
                    "document": {"blocks": []},
                }
            )

        with self.assertRaises(ValueError):
            ensure_rich_text_document(
                {
                    "format": RICH_TEXT_FORMAT,
                    "source": "红字",
                    "blocks": [],
                }
            )

        with self.assertRaises(ValueError):
            ensure_rich_text_document(
                {
                    "format": RICH_TEXT_FORMAT,
                    "blocks": [
                        {
                            "type": "paragraph",
                            "spans": [
                                {"type": "text", "text": "红", "style": {}},
                            ],
                        }
                    ],
                }
            )

        with self.assertRaises(ValueError):
            ensure_rich_text_document(
                {
                    "format": RICH_TEXT_FORMAT,
                    "blocks": [
                        {
                            "type": "paragraph",
                            "inlines": [
                                {
                                    "type": "text",
                                    "text": "红",
                                    "style": {"fontFamily": "Arial"},
                                },
                            ],
                        }
                    ],
                }
            )

        with self.assertRaises(ValueError):
            ensure_rich_text_document(
                {
                    "format": RICH_TEXT_FORMAT,
                    "blocks": [
                        {
                            "type": "paragraph",
                            "inlines": [
                                {
                                    "type": "text",
                                    "text": "红",
                                    "style": {"font_size": 32},
                                },
                            ],
                        }
                    ],
                }
            )

    def test_textblock_stores_rich_text_as_canonical_dict(self):
        document = _sample_document()
        region = TextBlock(
            lines=[[[0, 0], [100, 0], [100, 80], [0, 80]]],
            texts=["原文"],
            translation_rich=ensure_rich_text_document(document),
        )

        self.assertEqual(region.translation, "普通红字漢字点")
        self.assertIsInstance(region.translation_rich, dict)
        self.assertEqual(region.translation_rich, document)
        self.assertEqual(region.translation_raw, "普通红字漢字点")
        self.assertEqual(region.get_translation_for_rendering(), document)
        self.assertEqual(region.to_dict()["translation"], "普通红字漢字点")
        self.assertEqual(region.to_dict()["translation_rich"], document)

    def test_textblock_normalizes_rich_text_assignment(self):
        region = TextBlock(
            lines=[[[0, 0], [100, 0], [100, 80], [0, 80]]],
            texts=["原文"],
            translation="旧文本",
            translation_raw="原始文本",
        )
        document = ensure_rich_text_document(_sample_document())

        region.set_translation_rich(document, sync_plain=True)

        self.assertEqual(region.translation, "普通红字漢字点")
        self.assertEqual(region.translation_rich, document.to_dict())
        self.assertEqual(region.translation_raw, "原始文本")

    def test_structured_document_measures_as_multiple_lines(self):
        document = legacy_line_breaks_to_document("第一行[BR]第二行").to_dict()

        width, height, line_count, _ = calc_box_from_font(
            32,
            document,
            True,
            line_spacing=1.0,
        )

        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertEqual(line_count, 2)

    def test_low_level_rich_text_metrics_return_one_entry_per_paragraph(self):
        document = legacy_line_breaks_to_document("第一行[BR]第二行").to_dict()

        horizontal_lines, horizontal_widths = text_render.calc_horizontal(
            32,
            document,
            max_width=9999,
            max_height=9999,
        )
        vertical_lines, vertical_heights, vertical_widths = text_render.calc_vertical_metrics(
            32,
            document,
            max_height=9999,
        )

        self.assertEqual(len(horizontal_lines), 2)
        self.assertEqual(len(horizontal_widths), 2)
        self.assertTrue(all(width > 0 for width in horizontal_widths))
        self.assertEqual(len(vertical_lines), 2)
        self.assertEqual(len(vertical_heights), 2)
        self.assertEqual(len(vertical_widths), 2)
        self.assertTrue(all(height > 0 for height in vertical_heights))

    def test_structured_document_renders_horizontal_and_vertical(self):
        document = _sample_document()

        horizontal = text_render.put_text_horizontal(
            32,
            document,
            420,
            180,
            "left",
            False,
            (255, 255, 255),
            (0, 0, 0),
            line_spacing=1.0,
        )
        vertical = text_render.put_text_vertical(
            32,
            document,
            260,
            "left",
            (255, 255, 255),
            (0, 0, 0),
            line_spacing=1.0,
        )

        self.assertIsNotNone(horizontal)
        self.assertIsNotNone(vertical)
        self.assertEqual(horizontal.shape[2], 4)
        self.assertEqual(vertical.shape[2], 4)
        self.assertGreater(int(horizontal[:, :, 3].max()), 0)
        self.assertGreater(int(vertical[:, :, 3].max()), 0)

    def test_vertical_rich_layout_keeps_body_width_separate_from_side_marks(self):
        document = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "前", "style": {}},
                            {
                                "type": "ruby",
                                "base": [{"type": "text", "text": "中", "style": {"emphasis": True}}],
                                "text": [{"type": "text", "text": "なか", "style": {}}],
                            },
                            {"type": "text", "text": "後", "style": {}},
                        ],
                    }
                ],
            }
        )

        layout = text_render._build_rich_vertical_layout(
            document,
            32,
            0.07,
            (255, 255, 255),
            (0, 0, 0),
            1.0,
        )[0]

        self.assertEqual(layout["width"], layout["body_width"])
        self.assertGreater(layout["ruby_extra"], 0)
        self.assertGreater(layout["dot_extra"], 0)

    def test_vertical_bold_does_not_change_body_column_width(self):
        plain = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "能", "style": {}},
                            {"type": "text", "text": "写", "style": {}},
                            {"type": "text", "text": "出", "style": {}},
                        ],
                    }
                ],
            }
        )
        bold = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "能", "style": {}},
                            {"type": "text", "text": "写", "style": {"bold": True}},
                            {"type": "text", "text": "出", "style": {}},
                        ],
                    }
                ],
            }
        )

        plain_layout = text_render._build_rich_vertical_layout(plain, 32, 0.07, (255, 255, 255), (0, 0, 0), 1.0)[0]
        bold_layout = text_render._build_rich_vertical_layout(bold, 32, 0.07, (255, 255, 255), (0, 0, 0), 1.0)[0]
        plain_geometry = text_render._rich_vertical_layout_geometry([plain_layout], 32, 1.0)
        bold_geometry = text_render._rich_vertical_layout_geometry([bold_layout], 32, 1.0)

        self.assertEqual(bold_layout["body_width"], plain_layout["body_width"])
        self.assertEqual(bold_geometry["layout_width"], plain_geometry["layout_width"])
        self.assertGreater(bold_geometry["paint_width"], plain_geometry["paint_width"])

    def test_vertical_rich_layout_uses_fixed_column_thickness(self):
        plain = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "能写出", "style": {}}],
                    },
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "好歌词", "style": {}}],
                    },
                ],
            }
        )
        large = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "能", "style": {}},
                            {"type": "text", "text": "写", "style": {"fontSize": 64}},
                            {"type": "text", "text": "出", "style": {}},
                        ],
                    },
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "好歌词", "style": {}}],
                    },
                ],
            }
        )
        tcy = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "年", "style": {}},
                            {
                                "type": "tcy",
                                "content": [{"type": "text", "text": "2026", "style": {}}],
                            },
                            {"type": "text", "text": "版", "style": {}},
                        ],
                    },
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "好歌词", "style": {}}],
                    },
                ],
            }
        )

        plain_layouts = text_render._build_rich_vertical_layout(plain, 32, 0.07, (255, 255, 255), (0, 0, 0), 1.0)
        large_layouts = text_render._build_rich_vertical_layout(large, 32, 0.07, (255, 255, 255), (0, 0, 0), 1.0)
        tcy_layouts = text_render._build_rich_vertical_layout(tcy, 32, 0.07, (255, 255, 255), (0, 0, 0), 1.0)
        plain_geometry = text_render._rich_vertical_layout_geometry(plain_layouts, 32, 1.0)
        large_geometry = text_render._rich_vertical_layout_geometry(large_layouts, 32, 1.0)
        tcy_geometry = text_render._rich_vertical_layout_geometry(tcy_layouts, 32, 1.0)

        self.assertTrue(all(layout["thickness"] == 32 for layout in large_layouts))
        self.assertEqual(large_layouts[0]["body_width"], plain_layouts[0]["body_width"])
        self.assertEqual(tcy_layouts[0]["body_width"], plain_layouts[0]["body_width"])
        self.assertEqual(large_geometry["layout_width"], plain_geometry["layout_width"])
        self.assertEqual(tcy_geometry["layout_width"], plain_geometry["layout_width"])
        self.assertGreater(large_geometry["paint_width"], plain_geometry["paint_width"])
        self.assertGreater(tcy_geometry["paint_width"], plain_geometry["paint_width"])

        columns = text_render._rich_vertical_column_positions(large_layouts, large_geometry)
        self.assertAlmostEqual(columns[0][2] - columns[1][2], 32 + large_geometry["spacing_x"])

    def test_rich_text_ruby_expands_measured_box(self):
        plain = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": "漢字", "style": {}}],
                }
            ],
        }
        ruby = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "ruby",
                            "base": [{"type": "text", "text": "漢字", "style": {}}],
                            "text": [{"type": "text", "text": "かんじ", "style": {}}],
                        }
                    ],
                }
            ],
        }

        plain_v_w, plain_v_h, _, _ = calc_box_from_font(32, plain, False, line_spacing=1.0)
        ruby_v_w, ruby_v_h, _, _ = calc_box_from_font(32, ruby, False, line_spacing=1.0)
        plain_h_w, plain_h_h, _, _ = calc_box_from_font(32, plain, True, line_spacing=1.0)
        ruby_h_w, ruby_h_h, _, _ = calc_box_from_font(32, ruby, True, line_spacing=1.0)

        self.assertGreater(ruby_v_w, plain_v_w)
        self.assertEqual(ruby_v_h, plain_v_h)
        self.assertEqual(ruby_h_w, plain_h_w)
        self.assertGreater(ruby_h_h, plain_h_h)

    def test_empty_ruby_does_not_expand_measured_box(self):
        plain = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": "漢字", "style": {}}],
                }
            ],
        }
        empty_ruby = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "ruby",
                            "base": [{"type": "text", "text": "漢字", "style": {}}],
                            "text": [{"type": "text", "text": "", "style": {}}],
                        }
                    ],
                }
            ],
        }

        self.assertEqual(
            calc_box_from_font(32, empty_ruby, False, line_spacing=1.0),
            calc_box_from_font(32, plain, False, line_spacing=1.0),
        )
        self.assertEqual(
            calc_box_from_font(32, empty_ruby, True, line_spacing=1.0),
            calc_box_from_font(32, plain, True, line_spacing=1.0),
        )

    def test_vertical_ruby_compact_box_reports_body_center_for_anchoring(self):
        plain = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": "漢字", "style": {}}],
                }
            ],
        }
        ruby = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "ruby",
                            "base": [{"type": "text", "text": "漢字", "style": {}}],
                            "text": [{"type": "text", "text": "かんじ", "style": {}}],
                        }
                    ],
                }
            ],
        }

        # 纯文本：正文中心 == 渲染框正中心
        plain_w, plain_h, _, plain_body = calc_box_from_font(32, plain, False, line_spacing=1.0)
        self.assertAlmostEqual(plain_body[0], plain_w / 2.0, places=3)
        self.assertAlmostEqual(plain_body[1], plain_h / 2.0, places=3)

        # 紧凑框：注音只向右侧扩张，正文中心位于渲染框中心左侧
        ruby_w, ruby_h, _, ruby_body = calc_box_from_font(32, ruby, False, line_spacing=1.0)
        self.assertLess(ruby_body[0], ruby_w / 2.0)
        self.assertAlmostEqual(ruby_body[1], ruby_h / 2.0, places=3)

        # 调用方按正文锚定平移整框后（正文中心对齐同一锚点 (0,0)）：
        # 正文列左边缘与纯文本重合，注音空间全部扩在右侧。
        plain_points, plain_body_world = calc_box_from_font(32, plain, False, line_spacing=1.0, center=(0, 0))
        ruby_points, ruby_body_world = calc_box_from_font(32, ruby, False, line_spacing=1.0, center=(0, 0))
        self.assertAlmostEqual(plain_body_world[0], 0.0, places=3)
        self.assertAlmostEqual(plain_body_world[1], 0.0, places=3)

        plain_left = float(plain_points[0, :, 0].min())
        plain_right = float(plain_points[0, :, 0].max())
        ruby_left = float(ruby_points[0, :, 0].min()) - ruby_body_world[0]
        ruby_right = float(ruby_points[0, :, 0].max()) - ruby_body_world[0]
        self.assertAlmostEqual(ruby_left, plain_left, delta=1.0)
        self.assertGreater(ruby_right, plain_right)

    def test_horizontal_body_center_reflects_ruby_and_emphasis(self):
        ruby = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "ruby",
                            "base": [{"type": "text", "text": "漢字", "style": {}}],
                            "text": [{"type": "text", "text": "かんじ", "style": {}}],
                        }
                    ],
                }
            ],
        }
        emphasis = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": "着重", "style": {"emphasis": True}}],
                }
            ],
        }

        # 首行注音占据框顶部 → 正文中心低于框正中心
        ruby_w, ruby_h, _, ruby_body = calc_box_from_font(32, ruby, True, line_spacing=1.0)
        self.assertAlmostEqual(ruby_body[0], ruby_w / 2.0, places=3)
        self.assertGreater(ruby_body[1], ruby_h / 2.0)

        # 末行着重号占据框底部 → 正文中心高于框正中心
        emp_w, emp_h, _, emp_body = calc_box_from_font(32, emphasis, True, line_spacing=1.0)
        self.assertAlmostEqual(emp_body[0], emp_w / 2.0, places=3)
        self.assertLess(emp_body[1], emp_h / 2.0)

    def test_plain_inputs_report_box_center_as_body_center(self):
        # 纯字符串与无装饰文档的正文中心恒为框正中心
        for value, horizontal in (
            ("第一行[BR]第二行", True),
            ("第一行[BR]第二行", False),
            (legacy_line_breaks_to_document("第一行[BR]第二行").to_dict(), True),
            (legacy_line_breaks_to_document("第一行[BR]第二行").to_dict(), False),
        ):
            w, h, _, body = calc_box_from_font(32, value, horizontal, line_spacing=1.0)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)
            self.assertAlmostEqual(body[0], w / 2.0, places=3)
            self.assertAlmostEqual(body[1], h / 2.0, places=3)

    def test_vertical_rich_text_applies_local_layer_effects(self):
        plain = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": "文字", "style": {}}],
                }
            ],
        }
        styled = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "text",
                            "text": "文",
                            "style": {
                                "bold": True,
                                "italic": True,
                                "transform": {"rotation": 18, "mirrorX": True, "offsetX": 12},
                            },
                        },
                        {"type": "text", "text": "字", "style": {}},
                    ],
                }
            ],
        }

        plain_render = text_render.put_text_vertical(
            36,
            plain,
            180,
            "left",
            (255, 255, 255),
            (0, 0, 0),
            line_spacing=1.0,
        )
        styled_render = text_render.put_text_vertical(
            36,
            styled,
            180,
            "left",
            (255, 255, 255),
            (0, 0, 0),
            line_spacing=1.0,
        )

        self.assertIsNotNone(plain_render)
        self.assertIsNotNone(styled_render)
        self.assertNotEqual(plain_render.shape, styled_render.shape)
        self.assertGreater(int(styled_render[:, :, 3].sum()), int(plain_render[:, :, 3].sum()))

    def test_bold_is_applied_before_stroke_colorization(self):
        normal = text_render._line_surface("太", 48, 3, 0.07, False, 1.0, False)
        bold = text_render._line_surface("太", 48, 3, 0.07, False, 1.0, True)

        self.assertIsNotNone(normal)
        self.assertIsNotNone(bold)
        self.assertGreater(int(bold["text"].sum()), int(normal["text"].sum()))
        self.assertTrue(np.all(bold["border"] >= bold["text"]))

        layer = text_render.add_color(bold["text"], (0, 0, 0), bold["border"], (255, 255, 255))
        body_pixels = layer[bold["text"] >= 240]
        self.assertGreater(len(body_pixels), 0)
        self.assertLess(int(body_pixels[:, :3].max()), 16)

    def test_tcy_node_uses_horizontal_block_in_vertical_rendering(self):
        tcy_document = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "年", "style": {}},
                        {
                            "type": "tcy",
                            "content": [
                                {"type": "text", "text": "2026", "style": {}},
                            ],
                        },
                        {"type": "text", "text": "版", "style": {}},
                    ],
                }
            ],
        }
        plain_document = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "年2026版", "style": {}},
                    ],
                }
            ],
        }

        tcy_height = text_render.get_string_height(32, tcy_document)
        plain_height = text_render.get_string_height(32, plain_document)
        rendered = text_render.put_text_vertical(
            32,
            tcy_document,
            260,
            "left",
            (255, 255, 255),
            (0, 0, 0),
            line_spacing=1.0,
        )

        self.assertLess(tcy_height, plain_height)
        self.assertIsNotNone(rendered)
        self.assertGreater(int(rendered[:, :, 3].max()), 0)

    def test_add_color_keeps_text_alpha_when_border_layer_is_blank(self):
        # F05 回归：描边色非 None 而描边层全零时，输出 alpha 必须仍含正文
        # （输出 alpha = max(描边alpha, 文字alpha)），不得整段透明。
        text_alpha = np.zeros((8, 8), dtype=np.uint8)
        text_alpha[2:6, 2:6] = 255
        blank_border = np.zeros_like(text_alpha)

        layer = text_render.add_color(text_alpha, (255, 0, 0), blank_border, (0, 0, 0))

        self.assertTrue(
            np.array_equal(layer[:, :, 3], np.maximum(blank_border, text_alpha))
        )
        self.assertEqual(int(layer[:, :, 3].max()), 255)

    def test_horizontal_ruby_visible_when_region_stroke_enabled(self):
        # F05 场景①回归：区域描边开启（bg 非 None）时，横排注音层不得全透明。
        base_document = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": "漢字", "style": {}}],
                }
            ],
        }
        ruby_document = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "ruby",
                            "base": [{"type": "text", "text": "漢字", "style": {}}],
                            "text": [{"type": "text", "text": "かんじ", "style": {}}],
                        }
                    ],
                }
            ],
        }

        base_render = text_render.put_text_horizontal(
            32, base_document, 200, 100, "left", False,
            (255, 255, 255), (0, 0, 0), line_spacing=1.0,
        )
        ruby_render = text_render.put_text_horizontal(
            32, ruby_document, 200, 100, "left", False,
            (255, 255, 255), (0, 0, 0), line_spacing=1.0,
        )

        self.assertIsNotNone(base_render)
        self.assertIsNotNone(ruby_render)
        # 注音层有墨：整体墨量大于无注音渲染，且裁剪框因注音行而更高
        self.assertGreater(int(ruby_render[:, :, 3].sum()), int(base_render[:, :, 3].sum()))
        self.assertGreater(ruby_render.shape[0], base_render.shape[0])

    def test_zero_width_span_stroke_keeps_span_visible(self):
        # F05 场景③回归：span 级 stroke.width=0 不能让整段透明消失。
        document = {
            "format": RICH_TEXT_FORMAT,
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "text",
                            "text": "零描边",
                            "style": {"stroke": {"color": "#000000", "width": 0}},
                        }
                    ],
                }
            ],
        }

        rendered = text_render.put_text_horizontal(
            32, document, 200, 100, "left", False,
            (255, 255, 255), (0, 0, 0), line_spacing=1.0,
        )

        self.assertIsNotNone(rendered)
        self.assertGreater(int(rendered[:, :, 3].max()), 0)

    def test_measure_horizontal_span_metrics_match_render_path(self):
        # F21：横排度量改用 _line_metrics（无光栅化）后，logical_width/ascent/
        # descent 必须与渲染路径（_rich_span_surface）产出的数值完全一致。
        document = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "漢字Abc", "style": {}},
                            {
                                "type": "text",
                                "text": "大字",
                                "style": {"fontSize": 48, "bold": True},
                            },
                        ],
                    }
                ],
            }
        )

        for span in document.blocks[0].spans:
            span_font = text_render._style_font_size(32, span.style)
            run = text_render._rich_span_surface(span, 32, 0.07, (0, 0, 0), False, 1.0)
            metrics = text_render._line_metrics(span.text, span_font, 1.0)

            self.assertIsNotNone(run)
            self.assertEqual(float(metrics["logical_width"]), float(run["logical_width"]))
            self.assertEqual(float(metrics["ascent"]), float(run["ascent"]))
            self.assertEqual(float(metrics["descent"]), float(run["descent"]))

    def test_vertical_measure_only_layout_matches_full_geometry(self):
        # F21：measure_only 跳过描边距离变换/RGBA 合成/特效 warp，
        # 但所有几何输出（含 paint extras 与 item 级偏移）必须与完整布局一致。
        document = ensure_rich_text_document(
            {
                "format": RICH_TEXT_FORMAT,
                "blocks": [
                    {
                        "type": "paragraph",
                        "inlines": [
                            {"type": "text", "text": "粗", "style": {"bold": True}},
                            {
                                "type": "text",
                                "text": "斜",
                                "style": {"italic": True, "transform": {"rotation": 18}},
                            },
                            {
                                "type": "text",
                                "text": "描",
                                "style": {"stroke": {"color": "#000000", "width": 0.12}},
                            },
                            {
                                "type": "ruby",
                                "base": [{"type": "text", "text": "漢", "style": {"emphasis": True}}],
                                "text": [{"type": "text", "text": "かん", "style": {}}],
                            },
                            {
                                "type": "tcy",
                                "content": [{"type": "text", "text": "2026", "style": {}}],
                            },
                        ],
                    },
                    {
                        "type": "paragraph",
                        "inlines": [{"type": "text", "text": "第二列", "style": {}}],
                    },
                ],
            }
        )

        full = text_render._build_rich_vertical_layout(
            document, 32, 0.07, (255, 255, 255), (0, 0, 0), 1.0
        )
        measured = text_render._build_rich_vertical_layout(
            document, 32, 0.07, (255, 255, 255), (0, 0, 0), 1.0, measure_only=True
        )

        self.assertEqual(len(full), len(measured))
        for full_layout, measured_layout in zip(full, measured):
            for key in (
                "width",
                "body_width",
                "thickness",
                "paint_left_extra",
                "paint_right_extra",
                "ruby_extra",
                "dot_extra",
                "height",
            ):
                self.assertEqual(measured_layout[key], full_layout[key], key)
            self.assertEqual(len(measured_layout["items"]), len(full_layout["items"]))
            for full_item, measured_item in zip(full_layout["items"], measured_layout["items"]):
                self.assertEqual(measured_item["kind"], full_item["kind"])
                self.assertEqual(measured_item["cursor_y"], full_item["cursor_y"])
                self.assertEqual(measured_item.get("advance_y"), full_item.get("advance_y"))
                self.assertEqual(measured_item.get("layer_width"), full_item.get("layer_width"))
                self.assertAlmostEqual(
                    measured_item.get("paint_offset_x", 0.0),
                    full_item.get("paint_offset_x", 0.0),
                    places=4,
                )
                self.assertAlmostEqual(
                    measured_item.get("paint_offset_y", 0.0),
                    full_item.get("paint_offset_y", 0.0),
                    places=4,
                )
                if full_item["kind"] == "block":
                    self.assertEqual(measured_item["width"], full_item["width"])
                    self.assertEqual(measured_item["height"], full_item["height"])
                    self.assertAlmostEqual(measured_item["offset_x"], full_item["offset_x"], places=4)
                    self.assertAlmostEqual(measured_item["offset_y"], full_item["offset_y"], places=4)
                    self.assertIsNone(measured_item["layer"])

        full_geometry = text_render._rich_vertical_layout_geometry(full, 32, 1.0)
        measured_geometry = text_render._rich_vertical_layout_geometry(measured, 32, 1.0)
        self.assertEqual(measured_geometry, full_geometry)

    def test_paragraph_spans_are_lazily_cached(self):
        # F24：解析后的文档不可变，spans 首次计算后缓存（不再每次访问全量 deepcopy）；
        # 缓存的 span.style 与 inline.style 脱钩。
        document = ensure_rich_text_document(_sample_document())
        paragraph = document.blocks[0]

        first = paragraph.spans
        second = paragraph.spans

        self.assertIs(first, second)
        self.assertIsNot(first[0].style, paragraph.inlines[0].style)

    def test_high_level_render_uses_legacy_breaks_collected_after_layout(self):
        image = np.zeros((220, 220, 3), dtype=np.uint8)
        region = TextBlock(
            lines=[[[40, 40], [180, 40], [180, 180], [40, 180]]],
            texts=["原文"],
            translation="红[BR]蓝",
            fg_color=(255, 255, 255),
            bg_color=(0, 0, 0),
            direction="h",
            target_lang="CHS",
        )
        region.font_size = 32
        region.font_path = "Arial-Unicode-Regular.ttf"
        dst_points = np.array(
            [[[40, 40], [180, 40], [180, 180], [40, 180]]],
            dtype=np.float32,
        )
        sync_translation_raw_from_layout([region], Config())

        rendered = render(
            image,
            region,
            dst_points,
            hyphenate=True,
            line_spacing=1.0,
            disable_font_border=False,
            config=Config(),
        )

        self.assertEqual(rendered.shape, image.shape)
        self.assertGreater(int(rendered.max()), 0)
        self.assertEqual(region.translation, "红[BR]蓝")
        self.assertIsInstance(region.translation_rich, dict)
        self.assertEqual(region.translation_rich["format"], RICH_TEXT_FORMAT)
        self.assertEqual(len(region.translation_rich["blocks"]), 2)

    def test_multiline_plain_document_falls_back_to_hq_string_path(self):
        # F28 回归：仅由 BR 转换产生的无样式多行文档应回退纯字符串渲染路径，
        # 保住 HQ 超采样；带注音的文档保持结构化路径（不走 HQ）。
        from unittest import mock

        from manga_translator.rendering import text_render_hq

        def _make_region(translation):
            region = TextBlock(
                lines=[[[40, 40], [180, 40], [180, 180], [40, 180]]],
                texts=["原文"],
                translation=translation,
                fg_color=(255, 255, 255),
                bg_color=(0, 0, 0),
                direction="h",
                target_lang="CHS",
            )
            region.font_size = 32
            region.font_path = "Arial-Unicode-Regular.ttf"
            return region

        dst_points = np.array(
            [[[40, 40], [180, 40], [180, 180], [40, 180]]], dtype=np.float32
        )
        image = np.zeros((220, 220, 3), dtype=np.uint8)

        plain_region = _make_region("红[BR]蓝")
        sync_translation_raw_from_layout([plain_region], Config())
        self.assertEqual(plain_region.translation_rich["format"], RICH_TEXT_FORMAT)

        calls = []
        original_upscale = text_render_hq.render_text_with_upscale

        def spy(**kwargs):
            calls.append(kwargs.get("text"))
            return original_upscale(**kwargs)

        with mock.patch.object(text_render_hq, "render_text_with_upscale", side_effect=spy):
            rendered = render(
                image.copy(),
                plain_region,
                dst_points,
                hyphenate=True,
                line_spacing=1.0,
                disable_font_border=False,
                config=Config(),
            )
        self.assertEqual(calls, ["红\n蓝"])
        self.assertGreater(int(rendered.max()), 0)

        ruby_region = _make_region("")
        ruby_region.set_translation_rich(
            ensure_rich_text_document(
                {
                    "format": RICH_TEXT_FORMAT,
                    "blocks": [
                        {
                            "type": "paragraph",
                            "inlines": [
                                {
                                    "type": "ruby",
                                    "base": [{"type": "text", "text": "漢字", "style": {}}],
                                    "text": [{"type": "text", "text": "かんじ", "style": {}}],
                                }
                            ],
                        }
                    ],
                }
            ),
            sync_plain=True,
        )

        calls.clear()
        with mock.patch.object(text_render_hq, "render_text_with_upscale", side_effect=spy):
            render(
                image.copy(),
                ruby_region,
                dst_points,
                hyphenate=True,
                line_spacing=1.0,
                disable_font_border=False,
                config=Config(),
            )
        self.assertEqual(calls, [])

    def test_rich_document_font_size_shrinks_to_fit_region_box(self):
        # F07 回归：非 skip_font_scaling 时富文本区域不再直接用估算字号，
        # 而是收缩到区域未旋转外接框能容纳的最大字号（不做自动断行）。
        from manga_translator.rendering import resize_regions_to_font_size

        region = TextBlock(
            lines=[[[0, 0], [120, 0], [120, 60], [0, 60]]],
            texts=["原文"],
            direction="h",
            target_lang="CHS",
        )
        region.set_translation_rich(
            ensure_rich_text_document(
                legacy_line_breaks_to_document("很长的一段译文需要收缩字号[BR]第二行同样很长").to_dict()
            ),
            sync_plain=True,
        )
        region.font_size = 80  # 估算字号远大于框
        image = np.zeros((400, 400, 3), dtype=np.uint8)
        config = Config()

        dst_points_list = resize_regions_to_font_size(image, [region], config, original_img=None)

        self.assertEqual(len(dst_points_list), 1)
        self.assertIsNotNone(dst_points_list[0])
        self.assertLess(region.font_size, 80)
        # 收缩后的字号按同一测量应能放进原框
        req_w, req_h, _, _ = calc_box_from_font(
            region.font_size,
            region.get_translation_for_rendering(),
            True,
            line_spacing=1.0,
            config=config,
        )
        self.assertLessEqual(req_w, 120)
        self.assertLessEqual(req_h, 60)

    def test_h_tag_is_not_a_supported_rendering_protocol(self):
        raw_text = "<H>ABC</H>"

        lines, heights, _ = text_render.calc_vertical_metrics(
            32,
            raw_text,
            max_height=99999,
        )
        expected_height = sum(text_render.get_char_offset_y(32, char) for char in raw_text)

        self.assertEqual(lines, [raw_text])
        self.assertEqual(heights, [expected_height])

    def test_replacement_sync_collects_legacy_breaks_after_layout(self):
        region = TextBlock(
            lines=[[[0, 0], [100, 0], [100, 80], [0, 80]]],
            texts=["原文"],
            translation="红[BR]蓝",
        )
        region._replacement_layout_record = ReplacementLayoutRecord(
            raw_text="红[BR]蓝",
            replaced_text="红[BR]蓝",
        )

        sync_translation_raw_from_layout([region], Config())

        self.assertEqual(region.translation, "红[BR]蓝")
        self.assertEqual(region.translation_raw, "红[BR]蓝")
        self.assertEqual(region.translation_rich["format"], RICH_TEXT_FORMAT)
        self.assertEqual(len(region.translation_rich["blocks"]), 2)
        first_style = region.translation_rich["blocks"][0]["inlines"][0]["style"]
        self.assertEqual(first_style, {})
        self.assertFalse(hasattr(region, "_replacement_layout_record"))


if __name__ == "__main__":
    unittest.main()
