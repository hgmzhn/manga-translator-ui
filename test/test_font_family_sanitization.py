import io
import os
import unittest

from manga_translator.rendering.text_render import (
    _sanitized_font_bytes,
    qt_family_is_ambiguous,
    strip_qt_foundry_brackets,
)
from manga_translator.utils import BASE_PATH

BRACKETED_FONT = os.path.join(BASE_PATH, 'fonts', '[toolbox]书卷楷-简繁(v2.4).ttf')
CLEAN_FONT = os.path.join(BASE_PATH, 'fonts', 'Prompt-Regular.ttf')


class TestQtFamilyAmbiguity(unittest.TestCase):
    def test_leading_bracket_is_ambiguous(self):
        # Qt 的 parseFontName 会把 "[X]Y" 拆成空家族名 + 厂商 X
        self.assertTrue(qt_family_is_ambiguous('[工具箱]书卷楷-简繁'))
        self.assertTrue(qt_family_is_ambiguous('[toolbox]FangYuan-GBK W7'))
        self.assertTrue(qt_family_is_ambiguous('  [toolbox]QiangDiao-W'))

    def test_normal_names_are_not_ambiguous(self):
        self.assertFalse(qt_family_is_ambiguous('工具箱书卷楷-简繁'))
        self.assertFalse(qt_family_is_ambiguous('Microsoft YaHei UI'))
        # 尾部厂商写法家族名非空，Qt 能解析出 "Helvetica"
        self.assertFalse(qt_family_is_ambiguous('Helvetica [Cronyx]'))
        self.assertFalse(qt_family_is_ambiguous(''))
        self.assertFalse(qt_family_is_ambiguous('[未闭合'))
        self.assertFalse(qt_family_is_ambiguous('闭合]在前'))

    def test_strip_brackets(self):
        self.assertEqual(strip_qt_foundry_brackets('[工具箱]书卷楷-简繁'), '工具箱书卷楷-简繁')
        self.assertEqual(strip_qt_foundry_brackets(' [toolbox]X '), 'toolboxX')
        self.assertEqual(strip_qt_foundry_brackets(''), '')


@unittest.skipUnless(os.path.exists(BRACKETED_FONT), 'bracketed toolbox font not present')
class TestSanitizedFontBytes(unittest.TestCase):
    def test_bracketed_font_is_rewritten(self):
        from fontTools.ttLib import TTFont

        data, original_names = _sanitized_font_bytes(BRACKETED_FONT)
        self.assertIsNotNone(data)
        self.assertIn('[工具箱]书卷楷-简繁', original_names)

        source = TTFont(BRACKETED_FONT, lazy=True)
        rewritten = TTFont(io.BytesIO(data), lazy=True)
        try:
            for record in rewritten['name'].names:
                if record.nameID in (1, 4, 16):
                    value = record.toUnicode()
                    self.assertNotIn('[', value)
                    self.assertNotIn(']', value)
            # 缺失的英文首选家族名(nameID 16)被补全，offscreen freetype 选名依赖它
            self.assertIsNotNone(rewritten['name'].getName(16, 3, 1, 0x409))
            # 只动名字表，字形数据不变
            self.assertEqual(rewritten['maxp'].numGlyphs, source['maxp'].numGlyphs)
        finally:
            source.close()
            rewritten.close()

    @unittest.skipUnless(os.path.exists(CLEAN_FONT), 'clean control font not present')
    def test_clean_font_needs_no_rewrite(self):
        data, original_names = _sanitized_font_bytes(CLEAN_FONT)
        self.assertIsNone(data)
        self.assertEqual(original_names, [])


if __name__ == '__main__':
    unittest.main()
