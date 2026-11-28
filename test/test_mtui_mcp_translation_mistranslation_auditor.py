"""Unit tests for semantic, glossary, OCR-corruption, number-drop, and polarity mistranslation detectors."""

import _bootstrap  # noqa: F401, I001

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import encode_mask_base64, save_page_json_atomic, save_rgb_image_atomic
from mtui_mcp.qa_auditor import run_qa_audit


def test_glossary_mismatch_and_ocr_confusable_proper_noun_typo(tmp_path: Path) -> None:
    glossary_yaml = tmp_path / "test_glossary.yaml"
    glossary_yaml.write_text(
        """
system_prompt: ""
glossary:
  Person:
    - original: アイシャ
      aliases:
        - original: アイシャ
          translations:
            - text: 阿伊莎
    - original: フィン
      aliases:
        - original: フィン
          translations:
            - text: 芬恩
    - original: シル
      aliases:
        - original: シル
          translations:
            - text: 希儿
""",
        encoding="utf-8",
    )

    folder = tmp_path / "mistrans_ch"
    folder.mkdir(parents=True)
    img_path = folder / "001.png"
    Image.fromarray(np.full((600, 500, 3), 255, dtype=np.uint8)).save(img_path)
    paths = resolve_page_paths(img_path)

    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "regions": [
                {
                    "lines": [[[40.0, 40.0], [120.0, 40.0], [120.0, 220.0], [40.0, 220.0]]],
                    "text": "それならアイシャ達だって群がってくるじゃない！",
                    "translation": "那艾夏她们[BR]也会围过来啊！",
                    "direction": "v",
                },
                {
                    "lines": [[[140.0, 40.0], [220.0, 40.0], [220.0, 220.0], [140.0, 220.0]]],
                    "text": "……すまねぇフィシ……てめえの苦労を舐めてた",
                    "translation": "︙抱歉[BR]菲西︙[BR]我小看你的辛苦了",
                    "direction": "v",
                },
                {
                    "lines": [[[240.0, 40.0], [320.0, 40.0], [320.0, 220.0], [240.0, 220.0]]],
                    "text": "黒いシルエットが見えた",
                    "translation": "看到了黑色的剪影",
                    "direction": "v",
                },
            ]
        },
        auto_initial_snapshot=False,
    )

    audit = run_qa_audit(folder, check_inpaint_cv=False, check_font_glyphs=False, glossary_file=glossary_yaml)
    issues = audit["pages"][0]["issues"]
    by_idx = {(i["region_index"], i["category"]) for i in issues}
    assert (0, "glossary_term_mismatch") in by_idx
    assert (1, "suspect_ocr_glossary_typo") in by_idx
    assert not [i for i in issues if i["region_index"] == 2 and i["category"] == "glossary_term_mismatch"]


def test_ocr_lv_trap_number_drop_invented_ellipsis_and_polarity_reversal(tmp_path: Path) -> None:
    folder = tmp_path / "polarity_ch"
    folder.mkdir(parents=True)
    img_path = folder / "002.png"
    page = np.full((800, 700, 3), 175, dtype=np.uint8)
    cv2.ellipse(page, (530, 310), (105, 200), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(page, (530, 310), (105, 200), 0, 0, 360, (10, 10, 10), 4)
    Image.fromarray(page).save(img_path)
    paths = resolve_page_paths(img_path)
    save_rgb_image_atomic(page, paths.inpainted_path)

    save_page_json_atomic(
        paths,
        str(img_path.resolve()),
        {
            "mask_raw": encode_mask_base64(np.zeros((800, 700), dtype=np.uint8)),
            "regions": [
                {
                    "lines": [[[30.0, 40.0], [90.0, 40.0], [90.0, 220.0], [30.0, 220.0]]],
                    "text": "てめぇ……プチのめされてえのか？",
                    "translation": "你︙[BR]想被小地玩弄吗？",
                    "direction": "v",
                },
                {
                    "lines": [[[110.0, 40.0], [170.0, 40.0], [170.0, 220.0], [110.0, 220.0]]],
                    "text": "大半かい２",
                    "translation": "大半都会︙",
                    "direction": "v",
                },
                {
                    "lines": [[[190.0, 40.0], [250.0, 40.0], [250.0, 220.0], [190.0, 220.0]]],
                    "text": "言ってるそばから仕留めるんじゃねえ",
                    "translation": "话音未落[BR]就下杀手啊",
                    "direction": "v",
                },
                {
                    "lines": [[[270.0, 40.0], [330.0, 40.0], [330.0, 220.0], [270.0, 220.0]]],
                    "text": "ううんきっと知らないだろ",
                    "translation": "嗯[BR]你一定不知道吧",
                    "direction": "v",
                },
                {
                    "lines": [[[505.0, 145.0], [555.0, 145.0], [555.0, 265.0], [505.0, 265.0]]],
                    "text": "こさえるよい",
                    "translation": "来得好",
                    "direction": "v",
                },
                {
                    "lines": [[[500.0, 290.0], [560.0, 290.0], [560.0, 435.0], [500.0, 435.0]]],
                    "text": "傷を死ぬ方が",
                    "translation": "受伤[BR]总比死",
                    "direction": "v",
                },
            ],
        },
        auto_initial_snapshot=False,
    )

    audit = run_qa_audit(folder, check_inpaint_cv=True, check_font_glyphs=False)
    issues = audit["pages"][0]["issues"]
    by_idx = {(i["region_index"], i["category"]) for i in issues}
    assert (0, "suspect_ocr_corruption_mistranslation") in by_idx
    assert (1, "suspect_ocr_corruption_mistranslation") in by_idx
    assert (1, "translation_number_mismatch") in by_idx
    assert (1, "invented_ellipsis_truncated_translation") in by_idx
    assert (2, "negation_or_prohibition_drop_suspect") in by_idx
    assert (3, "negation_or_prohibition_drop_suspect") in by_idx
    assert (4, "same_bubble_split_regions") in by_idx


def test_r3_compound_units_mixed_quantities_and_exact_decimal_units() -> None:
    from mtui_mcp.translation_auditor import (
        _parse_decimal_with_units_exact,
        _parse_num_span,
        _translation_contains_number,
        audit_region_translation_accuracy,
    )

    # R3-F55: Consecutive section multipliers (`一万亿` == `1万亿` == 10^12), preserving additive `一亿二千万`
    assert _parse_num_span("一万亿") == 1_000_000_000_000
    assert _parse_num_span("1万亿") == 1_000_000_000_000
    assert _parse_num_span("一万二千亿") == 1_200_000_000_000
    assert _parse_num_span("一亿二千万") == 120_000_000
    assert _translation_contains_number("预算高达一万亿法利", 1_000_000_000_000) is True
    assert audit_region_translation_accuracy(0, {"text": "予算は1万億だ", "translation": "预算是一万亿"}) == []
    assert audit_region_translation_accuracy(0, {"text": "被害1億2000万", "translation": "损失一亿二千万"}) == []

    # R3-F56 / R4-F49: Mixed compound `2万3千` / `2万3500` parsed as single quantity (with negative control on `2万3千`)
    assert audit_region_translation_accuracy(0, {"text": "敵は2万3千人", "translation": "敌人有23000人"}) == []
    assert audit_region_translation_accuracy(0, {"text": "敵は2万3千人", "translation": "敌人有两万三千人"}) == []
    assert audit_region_translation_accuracy(0, {"text": "敵は2万3500人", "translation": "敌人有23500人"}) == []
    wrong_2man3sen = audit_region_translation_accuracy(0, {"text": "敵は2万3千人", "translation": "敌人有2人"})
    assert any(
        i["category"] == "translation_number_mismatch" and i["missing_number"] == 23000 for i in wrong_2man3sen
    )
    wrong_2man = audit_region_translation_accuracy(0, {"text": "敵は2万人", "translation": "敌人有2人"})
    assert any(i["category"] == "translation_number_mismatch" and i["missing_number"] == 20000 for i in wrong_2man)

    # R3-F57: Exact base-10 decimal-with-unit conversion (no float tolerance false integers or OverflowError)
    assert _parse_decimal_with_units_exact("2.5", "万") == 25000
    assert _parse_decimal_with_units_exact("0.000000019999999999", "亿") is None
    assert _parse_decimal_with_units_exact("9" * 400 + ".5", "万") is None
    assert audit_region_translation_accuracy(0, {"text": "賞金2.5万", "translation": "赏金25000"}) == []
    assert audit_region_translation_accuracy(0, {"text": "確率0.000000019999999999億", "translation": "概率极低"}) == []
    non_int_tr = audit_region_translation_accuracy(
        0, {"text": "相手は2人だ", "translation": "对手有0.000000019999999999亿人"}
    )
    assert any(i["category"] == "translation_number_mismatch" and i["missing_number"] == 2 for i in non_int_tr)


def test_r4_multi_comma_thousand_groups_and_high_precision_decimal_units() -> None:
    from mtui_mcp.translation_auditor import (
        _parse_decimal_with_units_exact,
        _strip_thousand_commas,
        _translation_contains_number,
        audit_region_translation_accuracy,
    )

    # R4-F21: Full thousand-group token normalization strips all internal commas on valid groups (`234,567`, `1,234,567`)
    # without splitting `1,234,567` into `1` and `234567` or mutating malformed groupings (`1234,567`, `12,34,567`).
    assert _strip_thousand_commas("234,567") == "234567"
    assert _strip_thousand_commas("1,234,567") == "1234567"
    assert _strip_thousand_commas("1,234.5万") == "1234.5万"
    assert _strip_thousand_commas("1234,567") == "1234,567"
    assert _strip_thousand_commas("12,34,567") == "12,34,567"
    assert _strip_thousand_commas("1,234,56") == "1,234,56"

    dropped_million = audit_region_translation_accuracy(
        0, {"text": "賞金は1,234,567ヴァリスだ", "translation": "赏金非常丰厚"}
    )
    assert any(
        i["category"] == "translation_number_mismatch" and i["missing_number"] == 1_234_567 for i in dropped_million
    )
    assert (
        audit_region_translation_accuracy(
            0, {"text": "賞金は1,234,567ヴァリスだ", "translation": "赏金是1,234,567法利"}
        )
        == []
    )
    assert (
        audit_region_translation_accuracy(
            0, {"text": "賞金は1,234,567ヴァリスだ", "translation": "赏金是1234567法利"}
        )
        == []
    )
    assert (
        audit_region_translation_accuracy(0, {"text": "賞金は234,567ヴァリスだ", "translation": "赏金是234567法利"})
        == []
    )
    assert _translation_contains_number("对手有1,234,567人", 234_567) is False

    # R4-F42: Preserve exact rational value beyond Decimal's default 28-digit arithmetic precision
    almost_2_dec = "2." + "0" * 28 + "1"
    assert _parse_decimal_with_units_exact(almost_2_dec, "万") is None
    assert _parse_decimal_with_units_exact("2." + "0" * 35, "万") == 20_000
    assert _parse_decimal_with_units_exact("2.5", "万") == 25_000
    non_int_28prec = audit_region_translation_accuracy(
        0, {"text": "賞金20000ヴァリス", "translation": f"赏金{almost_2_dec}万法利"}
    )
    assert any(
        i["category"] == "translation_number_mismatch" and i["missing_number"] == 20_000 for i in non_int_28prec
    )


def test_r5_malformed_comma_grouped_numbers_skipped_without_fragment_extraction() -> None:
    from mtui_mcp.translation_auditor import (
        _parse_source_number_token,
        _translation_contains_number,
        audit_region_translation_accuracy,
    )

    # R5-F33: Malformed contiguous comma-grouped tokens (`12,34,567`, `1234,567`, `1,234,56`, `1,,234`) MUST be
    # skipped as whole invalid tokens so sub-fragments (`12`, `34`, `567`) never satisfy number-drop checks!
    for bad_text in (
        "赏金是12,34,567法利",
        "赏金是1234,567法利",
        "赏金是1,234,56法利",
        "赏金是1,,567法利",
        "赏金是12,34,567万法利",
    ):
        for probe_n in (12, 34, 56, 234, 567, 1234, 10_000, 34_567, 1_234_567):
            assert _translation_contains_number(bad_text, probe_n) is False, (bad_text, probe_n)

    # Valid 3-digit thousand-grouped numbers STILL parse as a single quantity
    assert _translation_contains_number("赏金是1,234,567法利", 1_234_567) is True
    assert _translation_contains_number("赏金是1,234.5万法利", 12_345_000) is True

    # Comma + space separated number lists (`12, 34, 567`) and Chinese `、` lists STILL extract each separate item
    for n_item in (12, 34, 567):
        assert _translation_contains_number("编号是12, 34, 567", n_item) is True
        assert _translation_contains_number("编号是12、34、567", n_item) is True

    # End-to-end: source `567` vs broken translation `12,34,567` MUST flag `translation_number_mismatch` (`567`)
    mism_567 = audit_region_translation_accuracy(
        0, {"text": "賞金は567ヴァリスだ", "translation": "赏金是12,34,567法利"}
    )
    assert any(i["category"] == "translation_number_mismatch" and i["missing_number"] == 567 for i in mism_567)

    # Malformed comma-grouped token on the source side is also rejected cleanly without extracting `567`
    assert _parse_source_number_token("12,34,567") is None
    assert audit_region_translation_accuracy(0, {"text": "コード12,34,567", "translation": "代码异常"}) == []
