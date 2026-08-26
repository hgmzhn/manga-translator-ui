import _bootstrap  # noqa: I001
import json
from types import SimpleNamespace

import numpy as np

from manga_translator import rendering
from manga_translator.config import Config
from manga_translator.utils import TextBlock

from core.config_models import RenderSettings
from services.i18n_service import I18nManager

ROOT = _bootstrap.ROOT
LOCALE_DIR = ROOT / "desktop_qt_ui" / "locales"
LOCALES = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")
SETTING_KEY = "balloon_fill_mask_layout"
LABEL_KEY = f"label_{SETTING_KEY}"
DESCRIPTION_KEY = f"desc_render_{SETTING_KEY}"


def test_balloon_fill_mask_layout_defaults_off_and_is_visible_once():
    assert RenderSettings().balloon_fill_mask_layout is False

    release_config = json.loads(
        (ROOT / "config" / "config-example.json").read_text(encoding="utf-8")
    )
    assert release_config["render"][SETTING_KEY] is False

    layout = json.loads(
        (
            ROOT / "desktop_qt_ui" / "ui" / "main_page" / "settings_tab_layout.json"
        ).read_text(encoding="utf-8")
    )
    dotted_key = f"render.{SETTING_KEY}"
    assert sum(tab["items"].count(dotted_key) for tab in layout["tabs"]) == 1


def test_balloon_fill_mask_layout_translations_do_not_fall_back_to_chinese():
    catalogs = {
        locale: json.loads((LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in LOCALES
    }

    for locale, catalog in catalogs.items():
        manager = I18nManager(
            locale_dir=str(LOCALE_DIR),
            fallback_locale="zh_CN",
            config_language=locale,
        )
        for key in (LABEL_KEY, DESCRIPTION_KEY):
            assert catalog[key].strip(), (locale, key)
            assert manager.translate(key) == catalog[key], (locale, key)

    chinese_description = catalogs["zh_CN"][DESCRIPTION_KEY]
    for locale in ("en_US", "ja_JP", "ko_KR", "es_ES"):
        assert catalogs[locale][DESCRIPTION_KEY] != chinese_description, locale


def test_balloon_fill_mask_layout_preserves_translator_line_breaks(monkeypatch):
    image = np.zeros((400, 400, 3), dtype=np.uint8)
    bubble_mask = np.zeros((400, 400), dtype=np.uint8)
    bubble_mask[90:271, 90:331] = 1
    monkeypatch.setattr(
        rendering,
        "get_cached_bubbles_with_mangalens",
        lambda *args, **kwargs: SimpleNamespace(detections=[]),
    )
    monkeypatch.setattr(
        rendering,
        "build_bubble_mask_from_mangalens_result",
        lambda *args, **kwargs: bubble_mask.copy(),
    )

    translation = "第一行文字[BR]第二行更长的文字[BR]第三行"
    region = TextBlock(
        lines=[
            [[100, 100], [320, 100], [320, 180], [100, 180]],
            [[100, 180], [320, 180], [320, 260], [100, 260]],
        ],
        texts=["原文", "原文"],
        font_size=48,
        translation=translation,
        direction="h",
        target_lang="CHS",
    )
    config = Config()
    config.render.layout_mode = "balloon_fill"
    config.render.balloon_fill_mask_layout = True

    points = rendering.resize_regions_to_font_size(
        image,
        [region],
        config,
        original_img=image.copy(),
        skip_text_replacements=True,
    )[0]

    assert region.translation == translation
    assert rendering._polygon_fully_inside_mask(np.asarray(points[0]), bubble_mask)
    assert (
        rendering._resolve_balloon_fill_search_font_size(
            preferred_font_size=16,
            target_font_size=16,
            line_box_width=25,
            line_box_height=12,
            bubble_width=241,
            bubble_height=181,
        )
        == 241
    )


def test_balloon_fill_mask_layout_uses_mask_range_for_text_without_breaks(monkeypatch):
    image = np.zeros((400, 400, 3), dtype=np.uint8)
    bubble_mask = np.zeros((400, 400), dtype=np.uint8)
    bubble_mask[90:271, 90:331] = 1
    monkeypatch.setattr(
        rendering,
        "get_cached_bubbles_with_mangalens",
        lambda *args, **kwargs: SimpleNamespace(detections=[]),
    )
    monkeypatch.setattr(
        rendering,
        "build_bubble_mask_from_mangalens_result",
        lambda *args, **kwargs: bubble_mask.copy(),
    )

    source_text = "这是一段没有显式断行并且需要根据气泡范围重新安排断点的测试文字"

    def run_layout(safety_enabled: bool) -> str:
        region = TextBlock(
            lines=[
                [[190, 100], [210, 100], [210, 180], [190, 180]],
                [[190, 180], [210, 180], [210, 260], [190, 260]],
            ],
            texts=["原文", "原文"],
            font_size=24,
            translation=source_text,
            direction="h",
            target_lang="CHS",
        )
        config = Config()
        config.render.layout_mode = "balloon_fill"
        config.render.balloon_fill_mask_layout = safety_enabled
        rendering.resize_regions_to_font_size(
            image,
            [region],
            config,
            original_img=image.copy(),
            skip_text_replacements=True,
        )
        return region.translation

    legacy_text = run_layout(False)
    mask_layout_text = run_layout(True)

    assert mask_layout_text.replace("[BR]", "") == source_text
    assert mask_layout_text.count("[BR]") < legacy_text.count("[BR]")
