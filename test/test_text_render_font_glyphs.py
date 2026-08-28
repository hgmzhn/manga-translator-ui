import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import _bootstrap  # noqa: F401
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainterPath

from manga_translator.rendering.text_render import _fonts, _glyphs


class FontRuntimeSimulationTest(unittest.TestCase):
    def test_offscreen_font_directory_bootstrap_sets_default_once(self):
        with (
            patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}, clear=True),
            patch.object(_fonts, "BASE_PATH", "C:/project"),
            patch.object(_fonts.os.path, "isdir", return_value=True),
        ):
            _fonts._bootstrap_qt_fontdir_for_offscreen()
            self.assertEqual(
                os.environ["QT_QPA_FONTDIR"],
                os.path.join("C:/project", "fonts"),
            )

    def test_font_directory_bootstrap_preserves_existing_or_non_offscreen_environment(self):
        with patch.dict(
            os.environ,
            {"QT_QPA_PLATFORM": "offscreen", "QT_QPA_FONTDIR": "existing"},
            clear=True,
        ):
            _fonts._bootstrap_qt_fontdir_for_offscreen()
            self.assertEqual(os.environ["QT_QPA_FONTDIR"], "existing")

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "minimal"}, clear=True):
            _fonts._bootstrap_qt_fontdir_for_offscreen()
            self.assertNotIn("QT_QPA_FONTDIR", os.environ)

    def test_sanitizer_ignores_unsupported_font_extension(self):
        self.assertEqual(_fonts._sanitized_font_bytes("font.ttc"), (None, []))

    def test_register_font_file_skips_without_qt_application(self):
        _fonts._font_families_cache.clear()
        with patch.object(_fonts.QGuiApplication, "instance", return_value=None):
            self.assertEqual(_fonts.register_font_file("missing.ttf"), [])

    def test_raw_font_uses_exact_cache_entry(self):
        state = _fonts.FontState()
        cached = object()
        state.raw_fonts[("font.ttf", 12.0)] = cached
        with patch.object(_fonts._thread_state, "value", state, create=True):
            self.assertIs(_fonts._raw_font("font.ttf", 12), cached)

    def test_raw_font_resizes_cached_font_for_new_size(self):
        state = _fonts.FontState()
        state.raw_fonts[("font.ttf", 12.0)] = object()
        resized = object()
        with (
            patch.object(_fonts._thread_state, "value", state, create=True),
            patch.object(_fonts, "QRawFont", return_value=Mock()) as raw_font,
            patch.object(_fonts, "_cache_put", return_value=resized),
        ):
            self.assertIs(_fonts._raw_font("font.ttf", 24), resized)
        raw_font.return_value.setPixelSize.assert_called_once_with(24.0)

    def test_set_font_uses_qt_default_when_requested_family_is_missing(self):
        state = _fonts.FontState()
        with (
            patch.object(_fonts._thread_state, "value", state, create=True),
            patch.object(_fonts, "_resolve_existing_font_path", return_value=""),
            patch.object(_fonts, "_ensure_qt_runtime"),
            patch.object(_fonts, "_register_project_fonts"),
            patch.object(_fonts, "_match_family", return_value=None),
            patch.object(_fonts, "_register_system_fonts", return_value=False),
            patch.object(_fonts, "_set_family") as set_family,
        ):
            _fonts.set_font("missing-family")
        set_family.assert_called_once_with(state, _fonts.DEFAULT_FONT_FAMILY, "")

    def test_load_font_file_reports_missing_file(self):
        with (
            patch.object(_fonts, "_resolve_existing_font_path", return_value=""),
            self.assertRaises(FileNotFoundError),
        ):
            _fonts.load_font_file("missing.ttf")

    def test_create_text_layout_returns_empty_layout_for_empty_text(self):
        qfont = object()
        with patch.object(_fonts, "_layout_font", return_value=qfont):
            self.assertEqual(
                _fonts._create_text_layout("", 32),
                ("", qfont, None, None),
            )

    def test_select_hyphenator_returns_none_without_supported_language(self):
        with patch.object(_fonts, "HYPHENATOR_LANGUAGES", []):
            self.assertIsNone(_fonts.select_hyphenator("xx_XX"))

    def test_select_hyphenator_caches_initialization_failure(self):
        _fonts._hyphenator_cache.pop("en_US", None)
        with (
            patch.object(_fonts, "HYPHENATOR_LANGUAGES", ["en_US"]),
            patch.object(_fonts, "standardize_tag", return_value="en_US"),
            patch.object(_fonts, "Hyphenator", side_effect=RuntimeError("bad dict")),
        ):
            self.assertIsNone(_fonts.select_hyphenator("en_US"))
        self.assertIsNone(_fonts._hyphenator_cache["en_US"])
        _fonts._hyphenator_cache.pop("en_US", None)


class GlyphSimulationTest(unittest.TestCase):
    def test_glyph_advance_and_renderability_handle_missing_qt_data(self):
        raw = Mock()
        raw.advancesForGlyphIndexes.side_effect = RuntimeError("invalid glyph")
        self.assertFalse(_glyphs._glyph_has_advance(raw, 0))
        self.assertFalse(_glyphs._glyph_has_advance(raw, 7))

        raw.pathForGlyph.side_effect = RuntimeError("invalid path")
        raw.alphaMapForGlyph.side_effect = RuntimeError("invalid alpha")
        self.assertFalse(_glyphs._glyph_renderable(raw, 7, "字"))

    def test_whitespace_glyph_is_renderable_when_it_has_advance(self):
        raw = Mock()
        raw.advancesForGlyphIndexes.return_value = [QPointF(8, 0)]
        self.assertTrue(_glyphs._glyph_renderable(raw, 7, " "))
        raw.pathForGlyph.assert_not_called()

    def test_raw_font_key_falls_back_when_qt_properties_raise(self):
        raw = Mock()
        raw.familyName.side_effect = RuntimeError("family")
        raw.styleName.side_effect = RuntimeError("style")
        raw.weight.side_effect = RuntimeError("weight")
        self.assertEqual(_glyphs._raw_font_key(raw), ("", "", ""))

    def test_glyph_renderable_rejects_zero_id_and_empty_alpha_map(self):
        raw = Mock()
        self.assertFalse(_glyphs._glyph_renderable(raw, 0, "字"))
        raw.pathForGlyph.return_value = QPainterPath()
        alpha = Mock(isNull=lambda: False, width=lambda: 0, height=lambda: 0)
        raw.alphaMapForGlyph.return_value = alpha
        self.assertFalse(_glyphs._glyph_renderable(raw, 7, "字"))

    def test_glyph_spec_uses_first_available_placeholder(self):
        state = _fonts.FontState()
        placeholder = object()
        with (
            patch.object(_glyphs, "_state", return_value=state),
            patch.object(_glyphs, "_cache_get", return_value=None),
            patch.object(
                _glyphs,
                "_glyph_spec_via_layout",
                side_effect=[None, placeholder],
            ),
            patch.object(_glyphs, "_cache_put", return_value=placeholder),
        ):
            self.assertIs(_glyphs._glyph_spec("字", 32), placeholder)

    def test_rasterize_path_handles_zero_height_path(self):
        path = QPainterPath()
        path.moveTo(3, 3)
        path.lineTo(4, 3)
        alpha, left, top = _glyphs._rasterize_path(path)
        self.assertEqual(alpha.shape, (0, 0))
        self.assertEqual((left, top), (3, 3))

    def test_glyph_spec_via_layout_returns_none_without_layout(self):
        with patch.object(
            _glyphs, "_create_text_layout", return_value=("字", None, None, None)
        ):
            self.assertIsNone(_glyphs._glyph_spec_via_layout("字", 32))

    def test_glyph_spec_via_layout_keeps_whitespace_advance_fallback(self):
        raw = Mock()
        run = SimpleNamespace(rawFont=lambda: raw, glyphIndexes=lambda: [7])
        layout = SimpleNamespace(glyphRuns=lambda: [run])
        with (
            patch.object(_glyphs, "_create_text_layout", return_value=(None, None, layout, None)),
            patch.object(_glyphs, "_glyph_renderable", return_value=False),
            patch.object(_glyphs, "_glyph_has_advance", return_value=True),
            patch.object(_glyphs, "_raw_font_key", return_value=("family", "", "50")),
        ):
            spec = _glyphs._glyph_spec_via_layout(" ", 32)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.glyph_id, 7)

    def test_glyph_spec_reports_missing_placeholder(self):
        state = _fonts.FontState()
        with (
            patch.object(_glyphs, "_state", return_value=state),
            patch.object(_glyphs, "_cache_get", return_value=None),
            patch.object(_glyphs, "_glyph_spec_via_layout", return_value=None),
            self.assertRaisesRegex(RuntimeError, "not found"),
        ):
            _glyphs._glyph_spec("?", 32)

    def test_glyph_spec_reports_when_all_placeholders_are_missing(self):
        state = _fonts.FontState()
        with (
            patch.object(_glyphs, "_state", return_value=state),
            patch.object(_glyphs, "_cache_get", return_value=None),
            patch.object(_glyphs, "_glyph_spec_via_layout", return_value=None),
            self.assertRaisesRegex(RuntimeError, "No placeholder"),
        ):
            _glyphs._glyph_spec("字", 32)

    def test_rasterize_path_handles_empty_and_zero_area_paths(self):
        empty = QPainterPath()
        alpha, left, top = _glyphs._rasterize_path(empty)
        self.assertEqual(alpha.shape, (0, 0))
        self.assertEqual((left, top), (0, 0))

        point = QPainterPath()
        point.moveTo(3, 3)
        alpha, left, top = _glyphs._rasterize_path(point)
        self.assertEqual(alpha.shape, (0, 0))
        self.assertEqual((left, top), (0, 0))

    def test_path_mapping_skips_empty_or_identity_transform(self):
        path = QPainterPath()
        self.assertIs(_glyphs._map_path_about_center(path, _glyphs.QTransform()), path)


def main() -> int:
    result = unittest.main(exit=False)
    return 0 if result.result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
