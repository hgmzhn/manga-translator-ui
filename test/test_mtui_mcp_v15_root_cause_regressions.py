"""Regression tests for all 8 real-world root causes discovered on Sword-Oratoria V15 (`图1` ~ `图8`)."""

import _bootstrap  # noqa: F401, I001

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image

import mtui_mcp.font_catalog as font_catalog_mod
from mtui_mcp.config import get_runtime_settings
from mtui_mcp.font_catalog import (
    check_missing_glyphs,
    check_region_missing_glyphs,
    check_region_missing_glyphs_by_font,
    has_font,
)
from mtui_mcp.font_detector import classify_current_mtui_font, region_bbox_signature
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import (
    apply_region_updates_to_page,
    encode_mask_base64,
    load_page_json,
    save_page_json_atomic,
    save_rgb_image_atomic,
)
from mtui_mcp.qa_auditor import audit_single_page, run_qa_audit
from mtui_mcp.richtext_builder import sync_region_text_update
from mtui_mcp.translation_auditor import (
    _translation_contains_number,
    audit_region_translation_accuracy,
    load_active_glossary_terms,
)


def _ensure_zero_contour_tankuin_font(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Always mount deterministic synthetic `Arphic Tankuin Bold GB` (0-contour quotes) + `FZYaSong-DB-GBK` fallback in `tmp_path`."""
    fake_root = tmp_path / "synthetic_mtui_root"
    fonts_dir = fake_root / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    ttf_path = fonts_dir / "ArphicTankuinSynthetic.ttf"
    fallback_ttf_path = fonts_dir / "FZYaSongSynthetic.ttf"

    pen = TTGlyphPen(None)
    pen.moveTo((100, 100))
    pen.lineTo((900, 100))
    pen.lineTo((900, 900))
    pen.lineTo((100, 900))
    pen.closePath()
    box_glyph = pen.glyph()
    empty_glyph = TTGlyphPen(None).glyph()

    cmap = {ord(ch): "box" for ch in "距离作品完成还差得远︙"}
    for zero_ch in "﹁﹂︻︼︴":
        cmap[ord(zero_ch)] = "empty"

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "box", "empty"])
    fb.setupCharacterMap(cmap)
    fb.setupGlyf({".notdef": box_glyph, "box": box_glyph, "empty": empty_glyph})
    fb.setupHorizontalMetrics({".notdef": (1000, 0), "box": (1000, 100), "empty": (1000, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Arphic Tankuin Bold GB", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(ttf_path))

    fb_fallback = FontBuilder(1000, isTTF=True)
    fb_fallback.setupGlyphOrder([".notdef", "box"])
    fb_fallback.setupCharacterMap({ord(ch): "box" for ch in "距离作品完成还差得远︙﹁﹂︻︼︴"})
    fb_fallback.setupGlyf({".notdef": box_glyph, "box": box_glyph})
    fb_fallback.setupHorizontalMetrics({".notdef": (1000, 0), "box": (1000, 100)})
    fb_fallback.setupHorizontalHeader(ascent=800, descent=-200)
    fb_fallback.setupNameTable({"familyName": "FZYaSong-DB-GBK", "styleName": "Regular"})
    fb_fallback.setupOS2()
    fb_fallback.setupPost()
    fb_fallback.save(str(fallback_ttf_path))

    monkeypatch.setattr(get_runtime_settings(), "mtui_root", fake_root)


def test_fig2_zero_contour_glyph_detection_and_auto_inline_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """图2 (`V15_006 #3`): `文鼎古印体.ttf` (`Arphic Tankuin Bold GB`) maps `﹁﹂︻︼︴` to 0-contour empty slots."""
    _ensure_zero_contour_tankuin_font(tmp_path, monkeypatch)

    missing = check_missing_glyphs("Arphic Tankuin Bold GB", "距离﹁作品﹂完成")
    assert "﹁" in missing and "﹂" in missing
    assert "作" not in missing

    raw_reg = {
        "font_family": "Arphic Tankuin Bold GB",
        "translation": "距离﹁作品﹂完成[BR]还差得远︙",
        "direction": "v",
    }
    assert set(check_region_missing_glyphs(raw_reg)) == {"﹁", "﹂"}

    folder = tmp_path / "guyin_ch"
    folder.mkdir(parents=True)
    img_path = folder / "006.png"
    Image.fromarray(np.full((400, 300, 3), 255, dtype=np.uint8)).save(img_path)
    paths = resolve_page_paths(img_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[80.0, 50.0], [180.0, 50.0], [180.0, 300.0], [80.0, 300.0]]],
                    "text": "『作品』の完成には未だ…",
                    "translation": "距离「作品」完成[BR]还差得远…",
                    "font_family": "Arphic Tankuin Bold GB",
                    "font_size": 28,
                    "direction": "v",
                }
            ]
        },
        auto_initial_snapshot=False,
    )
    _, saved = load_page_json(resolve_page_paths(img_path))
    saved_reg = saved["regions"][0]
    assert check_region_missing_glyphs(saved_reg) == []
    inlines_0 = saved_reg["translation_rich"]["blocks"][0]["inlines"]
    fb_runs = [inl for inl in inlines_0 if (inl.get("style") or {}).get("fontFamily")]
    assert len(fb_runs) == 2
    assert {r["text"] for r in fb_runs} == {"﹁", "﹂"}


def test_fig8_prevent_and_detect_double_rotated_vertical_wave_dash(tmp_path: Path) -> None:
    """图8 (`V15_010 #4`): `～` with `rotation: -90` updated to `︴` must NOT retain `rotation: -90`."""
    reg = {
        "direction": "v",
        "font_family": "FZZhunYuan-M02",
        "translation": "哈啊～[BR]那么一瞬间",
        "translation_rich": {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {"type": "text", "text": "哈啊", "style": {}},
                        {"type": "text", "text": "～", "style": {"transform": {"rotation": -90.0}}},
                    ],
                },
                {"type": "paragraph", "inlines": [{"type": "text", "text": "那么一瞬间", "style": {}}]},
            ],
        },
    }
    sync_region_text_update(reg, "哈啊︴\n那么一瞬间")
    assert '"rotation"' not in json.dumps(reg.get("translation_rich") or {}, ensure_ascii=False)

    folder = tmp_path / "dbl_rot_ch"
    folder.mkdir(parents=True)
    img_path = folder / "010.png"
    Image.fromarray(np.full((400, 300, 3), 255, dtype=np.uint8)).save(img_path)
    paths = resolve_page_paths(img_path)
    paths.json_path.parent.mkdir(parents=True, exist_ok=True)
    corrupted_payload = {
        str(img_path.resolve()): {
            "regions": [
                {
                    "lines": [[[80.0, 50.0], [180.0, 50.0], [180.0, 300.0], [80.0, 300.0]]],
                    "text": "はあ～",
                    "translation": "哈啊︴",
                    "direction": "v",
                    "translation_rich": {
                        "format": "richtext.v1",
                        "blocks": [
                            {
                                "type": "paragraph",
                                "inlines": [
                                    {"type": "text", "text": "哈啊", "style": {}},
                                    {"type": "text", "text": "︴", "style": {"transform": {"rotation": -90.0}}},
                                ],
                            }
                        ],
                    },
                }
            ]
        }
    }
    paths.json_path.write_text(json.dumps(corrupted_payload, ensure_ascii=False), encoding="utf-8")
    rep = audit_single_page(resolve_page_paths(img_path), check_inpaint_cv=False, check_font_glyphs=False)
    cats = {i["category"] for i in rep["issues"]}
    assert "double_rotated_vertical_symbol" in cats


def test_fig3_fig5_yuzumarker_bold_mismatch_and_regular_suffix_strip(tmp_path: Path) -> None:
    assert classify_current_mtui_font("FZHei-B01::Regular") == "ANTIQUE"

    folder = tmp_path / "bold_ym_ch"
    folder.mkdir(parents=True)
    img_path = folder / "010.png"
    page = np.full((500, 400, 3), 255, dtype=np.uint8)
    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[60.0, 60.0], [160.0, 60.0], [160.0, 260.0], [60.0, 260.0]]],
                    "text": "仮面の人物…間違いない",
                    "translation": "戴面具的那家伙︙[BR]绝对不会有错",
                    "font_family": "FZHei-B01::Regular",
                    "font_size": 26,
                    "direction": "v",
                },
                {
                    "lines": [[[200.0, 60.0], [300.0, 60.0], [300.0, 260.0], [200.0, 260.0]]],
                    "text": "私も一緒に行く",
                    "translation": "我要[BR]一起去",
                    "font_family": "FZHei-B01::Regular",
                    "font_size": 30,
                    "direction": "v",
                },
            ]
        },
        auto_initial_snapshot=False,
    )
    ym_cache = {
        "by_ridx": {
            "0": {
                "bbox_sig": region_bbox_signature([60.0, 60.0, 160.0, 260.0]),
                "center": [110.0, 160.0],
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 0.98,
                "bold_prob": 0.96,
                "heavy_prob": 0.0,
                "family_probs": {"KAKU_GOTHIC": 0.98},
                "top_fonts": [{"font": "A-OTF-KakuminPro-Bold.otf", "prob": 0.96}],
            },
            "1": {
                "bbox_sig": region_bbox_signature([200.0, 60.0, 300.0, 260.0]),
                "center": [250.0, 160.0],
                "pred_family": "KAKU_GOTHIC",
                "pred_conf": 1.0,
                "bold_prob": 1.0,
                "heavy_prob": 0.0,
                "family_probs": {"KAKU_GOTHIC": 1.0},
                "top_fonts": [{"font": "A-OTF-ShinGoPr6-Bold.otf", "prob": 0.52}],
            },
        }
    }
    rep = audit_single_page(
        resolve_page_paths(img_path), check_inpaint_cv=True, check_font_glyphs=False, yuzumarker_page_cache=ym_cache
    )
    bold_issues = [i for i in rep["issues"] if i["category"] == "yuzumarker_bold_mismatch"]
    assert {i["region_index"] for i in bold_issues} == {0, 1}

    apply_region_updates_to_page(
        img_path,
        [
            {"region_index": 0, "bold": True},
            {"region_index": 1, "font_family": "Source Han Sans SC::Bold", "bold": False},
        ],
    )
    _, fixed_data = load_page_json(resolve_page_paths(img_path))
    assert fixed_data["regions"][0]["font_family"] == "FZHei-B01"
    rep_after = audit_single_page(
        resolve_page_paths(img_path), check_inpaint_cv=True, check_font_glyphs=False, yuzumarker_page_cache=ym_cache
    )
    assert not [i for i in rep_after["issues"] if i["category"] == "yuzumarker_bold_mismatch"]


def test_fig1_fig4_fig6_fig7_toc_header_overflow_and_underfilled_bubble(tmp_path: Path) -> None:
    folder = tmp_path / "layout_ch"
    folder.mkdir(parents=True)

    p_toc = folder / "004.png"
    toc_raw = np.full((1000, 750, 3), (212, 218, 232), dtype=np.uint8)
    toc_raw[465:490, 140:320] = (35, 70, 95)
    cv2.rectangle(toc_raw, (540, 466), (554, 488), (30, 65, 90), 3)
    cv2.rectangle(toc_raw, (560, 466), (574, 488), (30, 65, 90), 3)
    cv2.rectangle(toc_raw, (580, 466), (594, 488), (30, 65, 90), 3)
    Image.fromarray(toc_raw).save(p_toc)
    toc_paths = resolve_page_paths(p_toc)
    toc_mask = np.zeros((1000, 750), dtype=np.uint8)
    toc_mask[460:495, 135:330] = 255
    toc_inp = toc_raw.copy()
    toc_inp[460:495, 135:330] = (212, 218, 232)
    save_rgb_image_atomic(toc_inp, toc_paths.inpainted_path)
    save_page_json_atomic(
        toc_paths,
        str(p_toc.resolve()),
        {
            "mask_raw": encode_mask_base64(toc_mask),
            "regions": [
                {
                    "lines": [[[140.0, 455.0], [520.0, 455.0], [520.0, 680.0], [140.0, 680.0]]],
                    "text": "quest61.<敵地>...003",
                    "translation": "CONTENTS[BR]quest61.〈敌地〉 …003[BR]quest62.〈致命〉 …033",
                    "direction": "h",
                    "alignment": "center",
                    "font_size": 22,
                }
            ],
        },
        auto_initial_snapshot=False,
    )

    p_main = folder / "017.png"
    main_raw = np.full((1200, 900, 3), 170, dtype=np.uint8)
    main_raw[0:95, :] = 255
    main_raw[40:68, 80:320] = 20
    cv2.ellipse(main_raw, (720, 285), (95, 150), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(main_raw, (720, 285), (95, 150), 0, 0, 360, (10, 10, 10), 4)
    cv2.ellipse(main_raw, (365, 675), (105, 145), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(main_raw, (235, 900), (115, 165), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(main_raw, (365, 675), (105, 145), 0, 0, 360, (10, 10, 10), 4)
    cv2.ellipse(main_raw, (235, 900), (115, 165), 0, 0, 360, (10, 10, 10), 4)
    cv2.circle(main_raw, (300, 785), 42, (255, 255, 255), -1)
    Image.fromarray(main_raw).save(p_main)

    main_paths = resolve_page_paths(p_main)
    main_inp = main_raw.copy()
    main_inp[38:70, 75:325] = 255
    save_rgb_image_atomic(main_inp, main_paths.inpainted_path)
    save_page_json_atomic(
        main_paths,
        str(p_main.resolve()),
        {
            "regions": [
                {
                    "lines": [[[80.0, 40.0], [320.0, 40.0], [320.0, 68.0], [80.0, 68.0]]],
                    "text": "ソード・オラトリア 15",
                    "translation": "剑姬神圣谭 15",
                    "direction": "h",
                    "font_size": 20,
                },
                {
                    "lines": [[[650.0, 150.0], [790.0, 150.0], [790.0, 420.0], [650.0, 420.0]]],
                    "text": "って どわぁ!?",
                    "translation": "等││[BR]哇啊!?",
                    "direction": "v",
                    "font_size": 119,
                },
                {
                    "lines": [[[310.0, 565.0], [420.0, 565.0], [420.0, 785.0], [310.0, 785.0]]],
                    "text": "城塞ってのは敵の進行に備えて",
                    "translation": "虽说城塞这种地方[BR]为了防备敌人的进攻[BR]往往…",
                    "direction": "v",
                    "font_size": 20,
                },
                {
                    "lines": [[[175.0, 775.0], [295.0, 775.0], [295.0, 1025.0], [175.0, 1025.0]]],
                    "text": "得てして容易には進めない造りになってるもんだが…",
                    "translation": "大多都会设计成[BR]让人难以[BR]轻易前进的构造[BR]但是…",
                    "direction": "v",
                    "font_size": 22,
                },
            ]
        },
        auto_initial_snapshot=False,
    )

    audit = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    cats = set(audit["issues_by_category"].keys())
    assert "toc_alignment_or_merge_error" in cats
    assert "toc_unmasked_raw_text" in cats
    assert "running_header_book_title" in cats
    assert "text_overflows_bubble" in cats
    assert "bubble_underfilled_font" in cats
    assert "cross_lobe_dangling_clause" in cats


def test_r2_font_catalog_aggregation_ruby_inherit_ttc_faces_and_dir_sig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os as _os

    from fontTools.ttLib import TTCollection, TTFont

    _ensure_zero_contour_tankuin_font(tmp_path, monkeypatch)

    # R2-F42: Aggregate runs per font so check_missing_glyphs runs once per unique font
    call_count = 0
    orig_check = font_catalog_mod.check_missing_glyphs

    def _counting_check(ff, txts, **kw):
        nonlocal call_count
        call_count += 1
        return orig_check(ff, txts, **kw)

    monkeypatch.setattr(font_catalog_mod, "check_missing_glyphs", _counting_check)
    many_runs_reg = {
        "font_family": "Arphic Tankuin Bold GB",
        "translation_rich": {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [{"type": "text", "text": ch, "style": {}} for ch in "距离﹁作品﹂完成还差得远︙"],
                }
            ],
        },
    }
    res_by_font = check_region_missing_glyphs_by_font(many_runs_reg)
    assert call_count == 1
    assert res_by_font == {"Arphic Tankuin Bold GB": ["﹁", "﹂"]}
    monkeypatch.setattr(font_catalog_mod, "check_missing_glyphs", orig_check)

    # R2-F70: Ruby inherits base[0].style.fontFamily by default (rubyInherit=True), or text[0].style.fontFamily when False
    ruby_inherit_reg = {
        "font_family": "Arphic Tankuin Bold GB",
        "translation_rich": {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "ruby",
                            "base": [{"type": "text", "text": "作品", "style": {"fontFamily": "FZYaSong-DB-GBK"}}],
                            "text": [{"type": "text", "text": "﹁﹂", "style": {}}],
                        }
                    ],
                }
            ],
        },
    }
    assert check_region_missing_glyphs_by_font(ruby_inherit_reg) == {}

    ruby_no_inherit_reg = {
        "font_family": "FZYaSong-DB-GBK",
        "translation_rich": {
            "format": "richtext.v1",
            "blocks": [
                {
                    "type": "paragraph",
                    "inlines": [
                        {
                            "type": "ruby",
                            "base": [
                                {
                                    "type": "text",
                                    "text": "作品",
                                    "style": {"fontFamily": "FZYaSong-DB-GBK", "rubyInherit": False},
                                }
                            ],
                            "text": [{"type": "text", "text": "﹁﹂", "style": {"fontFamily": "Arphic Tankuin Bold GB"}}],
                        }
                    ],
                }
            ],
        },
    }
    assert check_region_missing_glyphs_by_font(ruby_no_inherit_reg) == {"Arphic Tankuin Bold GB": ["﹁", "﹂"]}

    # R2-F69: Replacing a font file with a different path/family but identical mtime_ns invalidates directory signature
    fonts_dir = tmp_path / "sig_root" / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(get_runtime_settings(), "mtui_root", tmp_path / "sig_root")

    pen = TTGlyphPen(None)
    pen.moveTo((100, 100))
    pen.lineTo((800, 100))
    pen.lineTo((800, 800))
    pen.closePath()
    g_box = pen.glyph()

    def _save_simple_ttf(p: Path, fam: str, chars: str, mtime_ns: int) -> None:
        fb = FontBuilder(1000, isTTF=True)
        fb.setupGlyphOrder([".notdef", "box"])
        fb.setupCharacterMap({ord(c): "box" for c in chars})
        fb.setupGlyf({".notdef": g_box, "box": g_box})
        fb.setupHorizontalMetrics({".notdef": (1000, 0), "box": (1000, 100)})
        fb.setupHorizontalHeader(ascent=800, descent=-200)
        fb.setupNameTable({"familyName": fam, "styleName": "Regular"})
        fb.setupOS2()
        fb.setupPost()
        fb.save(str(p))
        _os.utime(p, ns=(mtime_ns, mtime_ns))

    fixed_ns = 1_700_000_000_000_000_000
    demo_a = fonts_dir / "DemoA.ttf"
    _save_simple_ttf(demo_a, "DemoAlpha", "甲乙", fixed_ns)
    assert has_font("DemoAlpha", include_system=False) is True
    assert check_missing_glyphs("DemoAlpha", "甲丙", include_system=False) == ["丙"]

    demo_a.unlink()
    demo_b = fonts_dir / "DemoB.ttf"
    _save_simple_ttf(demo_b, "DemoBeta", "丙丁", fixed_ns)
    assert has_font("DemoAlpha", include_system=False) is False
    assert has_font("DemoBeta", include_system=False) is True
    assert check_missing_glyphs("DemoBeta", "丙甲", include_system=False) == ["甲"]

    # R2-F68: TTC collection where face 0 fails name extraction still resolves face 1 at true face_idx == 1
    ttc_f0 = tmp_path / "f0.ttf"
    ttc_f1 = tmp_path / "f1.ttf"
    _save_simple_ttf(ttc_f0, "BrokenZero", "甲", fixed_ns)
    _save_simple_ttf(ttc_f1, "HealthyOne", "乙", fixed_ns)
    ttc_path = fonts_dir / "Pair.ttc"
    ttc_obj = TTCollection()
    ttc_obj.fonts = [TTFont(ttc_f0), TTFont(ttc_f1)]
    ttc_obj.save(str(ttc_path))
    ttc_obj.close()

    orig_extract_names = font_catalog_mod._extract_face_names_from_ttfont
    seen_idx = {"n": 0}

    def _fail_first_ttc_face(tt, fp):
        if fp.name == "Pair.ttc":
            seen_idx["n"] += 1
            if seen_idx["n"] in (1, 3):
                raise RuntimeError("corrupt face 0 name table")
        return orig_extract_names(tt, fp)

    font_catalog_mod._match_font_names_only.cache_clear()
    font_catalog_mod._inspect_font_file.cache_clear()
    font_catalog_mod._resolve_target_font_codepoints.cache_clear()
    monkeypatch.setattr(font_catalog_mod, "_extract_face_names_from_ttfont", _fail_first_ttc_face)
    assert has_font("HealthyOne", include_system=False) is True
    assert check_missing_glyphs("HealthyOne", "乙甲", include_system=False) == ["甲"]


def test_r2_translation_auditor_thousand_ten_multiplier_units_and_tab_glossary(tmp_path: Path) -> None:
    # R2-F75: 1010 accepts "一千零一十" / "一千〇一十", standalone 10 accepts "十", 1011 accepts "一千零一十一"
    assert _translation_contains_number("一共一千零一十人", 1010) is True
    assert _translation_contains_number("一共一千〇一十人", 1010) is True
    assert _translation_contains_number("一共一千零一十一人", 1011) is True
    assert _translation_contains_number("十个人", 10) is True
    assert audit_region_translation_accuracy(0, {"text": "敵は1010体", "translation": "敌人有一千零一十只"}) == []

    # R2-F76: "2万" normalizes to 20000 (must NOT satisfy n=2, and MUST satisfy n=20000)
    assert _translation_contains_number("有2人", 2) is True
    assert _translation_contains_number("有2万人", 2) is False
    assert _translation_contains_number("有2万人", 20000) is True
    assert _translation_contains_number("有两万人", 20000) is True
    iss_bad = audit_region_translation_accuracy(0, {"text": "相手は2人だ", "translation": "对手有2万人"})
    assert any(i["category"] == "translation_number_mismatch" and i["missing_number"] == 2 for i in iss_bad)
    assert audit_region_translation_accuracy(0, {"text": "賞金20000ヴァリス", "translation": "赏金2万法利"}) == []
    assert audit_region_translation_accuracy(0, {"text": "敵は2万いる", "translation": "敌人有两万"}) == []

    # Tab-first .txt glossary parsing preserves multi-word original keys
    txt_g = tmp_path / "tab_glossary.txt"
    txt_g.write_text("New York\t纽约\n", encoding="utf-8")
    terms = load_active_glossary_terms(txt_g)
    assert len(terms) == 1
    assert terms[0].original == "New York"
    assert terms[0].translations == ("纽约",)
