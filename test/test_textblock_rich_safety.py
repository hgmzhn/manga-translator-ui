"""TextBlock 富文本安全性测试（F04a/F19/F02/F30）。

覆盖：
- 非法 translation_rich 不炸构造函数：丢样式、保区域（F04a）
- 合法富文本正常入库
- translation setter 等值赋值不清 translation_rich（F02）
- 'r' 方向 BR→rich 转换保留字符串路径的 LTR 块反转（F30）

运行：PYTHONIOENCODING=utf-8 PYTHONPATH=. python tests/test_textblock_rich_safety.py
"""

import unittest

from manga_translator.rendering.rich_text import RICH_TEXT_FORMAT
from manga_translator.utils import TextBlock

_LINES = [[[0, 0], [100, 0], [100, 80], [0, 80]]]


def _make_block(**kwargs):
    return TextBlock(lines=_LINES, texts=["原文"], **kwargs)


def _valid_document():
    return {
        "format": RICH_TEXT_FORMAT,
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [
                    {"type": "text", "text": "普通", "style": {}},
                    {"type": "text", "text": "红字", "style": {"color": "#ff0000"}},
                ],
            }
        ],
    }


# 旧实验格式形状：'spans'/'source' 等未知键会被严格解析拒绝（ValueError）
_MALFORMED_DOCUMENTS = [
    {"format": RICH_TEXT_FORMAT, "source": "红字", "blocks": []},
    {
        "format": RICH_TEXT_FORMAT,
        "blocks": [
            {"type": "paragraph", "spans": [{"type": "text", "text": "红", "style": {}}]}
        ],
    },
    {"format": RICH_TEXT_FORMAT},  # 缺 blocks
    {"format": RICH_TEXT_FORMAT, "blocks": "oops"},  # blocks 非 list（TypeError）
    {
        "format": RICH_TEXT_FORMAT,
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [
                    # fontPath 是已移除的旧字段（协议现在只认 fontFamily）
                    {"type": "text", "text": "红", "style": {"fontPath": "C:/x.ttf"}}
                ],
            }
        ],
    },
]


class MalformedRichTranslationTest(unittest.TestCase):
    """F04a：非法 translation_rich 只丢样式，绝不丢区域。"""

    def test_malformed_translation_rich_keeps_region(self):
        for malformed in _MALFORMED_DOCUMENTS:
            with self.subTest(document=malformed):
                with self.assertLogs("manga-translator.textblock", level="WARNING"):
                    region = _make_block(
                        translation="保留的译文",
                        translation_raw="原始译文",
                        translation_rich=malformed,
                    )
                self.assertIsNone(region.translation_rich)
                self.assertEqual(region.translation, "保留的译文")
                self.assertEqual(region.translation_raw, "原始译文")
                self.assertEqual(region.text, "原文")
                self.assertNotIn("translation_rich", region.to_dict())

    def test_malformed_rich_passed_as_translation_keeps_region(self):
        with self.assertLogs("manga-translator.textblock", level="WARNING"):
            region = _make_block(translation=_MALFORMED_DOCUMENTS[0])
        self.assertIsNone(region.translation_rich)
        self.assertEqual(region.translation, "")
        self.assertEqual(region.text, "原文")

    def test_load_style_kwargs_survive_malformed_rich(self):
        """load_text 场景：rich 解析失败后其余字段照常构造。"""
        with self.assertLogs("manga-translator.textblock", level="WARNING"):
            region = _make_block(
                translation="译文",
                translation_rich=_MALFORMED_DOCUMENTS[1],
                font_size=32,
                direction="v",
                target_lang="CHS",
            )
        self.assertEqual(region.font_size, 32)
        self.assertEqual(region.direction, "v")
        self.assertEqual(region.target_lang, "CHS")

    def test_removed_apis_are_gone(self):
        """F19：零调用 API 已删除。"""
        self.assertFalse(hasattr(TextBlock, "clear_translation_rich"))
        region = _make_block(translation="x")
        with self.assertRaises(TypeError):
            region.set_translation_rich(_valid_document(), sync_plain_when_empty=True)


class ValidRichTranslationTest(unittest.TestCase):
    def test_valid_rich_document_is_stored(self):
        document = _valid_document()
        region = _make_block(translation_rich=document)

        self.assertEqual(region.translation_rich, document)
        self.assertEqual(region.translation, "普通红字")
        self.assertEqual(region.translation_raw, "普通红字")
        self.assertEqual(region.get_translation_for_rendering(), document)
        self.assertEqual(region.to_dict()["translation_rich"], document)

    def test_valid_rich_with_plain_translation(self):
        document = _valid_document()
        region = _make_block(translation="纯文本译文", translation_rich=document)

        self.assertEqual(region.translation, "纯文本译文")
        self.assertEqual(region.translation_rich, document)

    def test_rich_document_passed_as_translation(self):
        document = _valid_document()
        region = _make_block(translation=document)

        self.assertEqual(region.translation, "普通红字")
        self.assertEqual(region.translation_rich, document)


class TranslationSetterRichInvalidationTest(unittest.TestCase):
    """F02：等值赋值不视为编辑，不清 translation_rich。"""

    def test_equal_assignment_keeps_rich(self):
        region = _make_block(translation_rich=_valid_document())
        plain = region.translation

        region.translation = plain  # OpenCC 未命中 / 后字典无替换等原样回写

        self.assertIsNotNone(region.translation_rich)
        self.assertEqual(region.translation, plain)

    def test_changed_assignment_clears_rich(self):
        region = _make_block(translation_rich=_valid_document())
        plain = region.translation

        region.translation = plain + "！"

        self.assertIsNone(region.translation_rich)
        self.assertEqual(region.translation, plain + "！")

    def test_raw_semantics_unchanged(self):
        region = _make_block(translation="旧", translation_raw="原始")
        region.translation = "新"
        self.assertEqual(region.translation_raw, "原始")


class RtlLegacyBreakConversionTest(unittest.TestCase):
    """F30：'r' 方向 BR→rich 转换与字符串渲染路径的 LTR 反转行为一致。"""

    @staticmethod
    def _paragraph_texts(document):
        return [
            "".join(inline["text"] for inline in block["inlines"])
            for block in document["blocks"]
        ]

    def test_hr_conversion_matches_string_path_reversal(self):
        source_lines = ["abc123", "مرحبا abc", "123", "ab"]
        region = _make_block(
            translation="[BR]".join(source_lines),
            direction="hr",
            target_lang="ARA",
        )

        self.assertTrue(region.ensure_translation_rich_from_legacy_breaks())
        rich_lines = self._paragraph_texts(region.translation_rich)

        # 与单行字符串路径逐行对照：get_translation_for_rendering 是行为基准
        expected = []
        for line in source_lines:
            single = _make_block(translation=line, direction="hr", target_lang="ARA")
            self.assertIsNone(single.translation_rich)
            expected.append(single.get_translation_for_rendering())
        self.assertEqual(rich_lines, expected)

        # 显式断言 LTR 拉丁块确实被反转（非恒等对照）
        self.assertEqual(rich_lines[0], "cba123")
        self.assertEqual(rich_lines[1], "مرحبا cba")

    def test_h_direction_conversion_keeps_order(self):
        region = _make_block(translation="abc123[BR]def", direction="h")

        self.assertTrue(region.ensure_translation_rich_from_legacy_breaks())

        self.assertEqual(
            self._paragraph_texts(region.translation_rich),
            ["abc123", "def"],
        )

    def test_conversion_skipped_when_rich_exists(self):
        document = _valid_document()
        region = _make_block(
            translation="abc[BR]def",
            translation_rich=document,
            direction="hr",
            target_lang="ARA",
        )
        self.assertFalse(region.ensure_translation_rich_from_legacy_breaks())
        self.assertEqual(region.translation_rich, document)


if __name__ == "__main__":
    unittest.main()
