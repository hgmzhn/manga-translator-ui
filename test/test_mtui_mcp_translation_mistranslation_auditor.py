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
