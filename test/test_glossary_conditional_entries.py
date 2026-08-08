import _bootstrap  # noqa: F401

import json
import re

from manga_translator.translators.common import (
    merge_glossary_to_file,
    parse_hq_response,
)
from manga_translator.translators.prompt_loader import (
    load_glossary_extraction_prompt,
    load_system_prompt_hq_format,
)
from ui.secondary_pages.glossary_entry_model import (
    aliases_from_rows,
    normalize_glossary_entry,
    serialize_glossary_entry,
)


def test_original_single_translation_format_migrates_to_canonical_alias():
    legacy = {
        "original": "Fire",
        "translation": "解雇",
        "condition": "非正式聊天",
        "overwrite": False,
        "custom": "preserved",
    }

    serialized = serialize_glossary_entry(normalize_glossary_entry(legacy))

    assert serialized == {
        "custom": "preserved",
        "original": "Fire",
        "aliases": [
            {
                "original": "Fire",
                "translations": [
                    {"text": "解雇", "condition": "非正式聊天"},
                ],
            }
        ],
        "overwrite": False,
    }


def test_unreleased_intermediate_glossary_shapes_are_not_migrated():
    entry = {
        "original": "Alice",
        "translations": [{"text": "爱丽丝"}],
        "nicknames": ["Ali"],
    }

    assert serialize_glossary_entry(normalize_glossary_entry(entry)) == {
        "original": "Alice",
        "aliases": [{"original": "Alice", "translations": []}],
    }


def test_unified_alias_structure_round_trips_for_any_category():
    entry = {
        "original": "アリス",
        "aliases": [
            {
                "original": "アリス",
                "translations": [
                    {"text": "爱丽丝", "condition": "正式称呼"},
                    {"text": "艾丽斯", "condition": "旧式译名"},
                ],
            },
            {
                "original": "アリちゃん",
                "translations": [
                    {"text": "小爱", "condition": "朋友间的称呼"},
                ],
            },
        ],
        "description": "主要角色",
        "overwrite": True,
    }

    assert serialize_glossary_entry(normalize_glossary_entry(entry)) == entry


def test_editor_rows_group_translations_and_seed_the_canonical_alias():
    aliases = aliases_from_rows(
        [
            {"original": "Fire", "text": "解雇", "condition": "非正式聊天"},
            {"original": "Fire", "text": "开火", "condition": "武器射击"},
            {"original": "Blaze", "text": "烈焰", "condition": "技能名"},
        ],
        "Fire",
    )

    assert serialize_glossary_entry(
        {
            **normalize_glossary_entry({"original": "Fire"}),
            "aliases": aliases,
        }
    )["aliases"] == [
        {
            "original": "Fire",
            "translations": [
                {"text": "解雇", "condition": "非正式聊天"},
                {"text": "开火", "condition": "武器射击"},
            ],
        },
        {
            "original": "Blaze",
            "translations": [{"text": "烈焰", "condition": "技能名"}],
        },
    ]

    seeded = serialize_glossary_entry(
        normalize_glossary_entry({"original": "Excalibur", "aliases": []})
    )
    assert seeded["aliases"] == [{"original": "Excalibur", "translations": []}]


def _alias(original, text):
    return {
        "original": original,
        "translations": [{"text": text}],
    }


def test_auto_glossary_merge_uses_alias_deltas_and_preserves_authored_data(tmp_path):
    prompt_path = tmp_path / "prompt.json"
    prompt_path.write_text(
        json.dumps(
            {
                "system_prompt": "base",
                "glossary": {
                    "Item": [
                        {
                            "original": "Fire",
                            "aliases": [
                                {
                                    "original": "Fire",
                                    "translations": [
                                        {"text": "解雇", "condition": "非正式聊天"},
                                        {"text": "开火", "condition": "武器射击"},
                                    ],
                                }
                            ],
                            "overwrite": False,
                        },
                        {
                            "original": "Cell",
                            "aliases": [
                                _alias("Cell", "细胞"),
                                _alias("Cell Block", "牢房区"),
                            ],
                            "description": "人工注释",
                            "overwrite": True,
                        },
                        {
                            "original": "NoFlag",
                            "aliases": [_alias("NoFlag", "旧译名")],
                        },
                        {
                            "original": "Legacy",
                            "translation": "旧译名",
                            "condition": "人工条件",
                            "overwrite": True,
                        },
                    ],
                    "Person": [
                        {
                            "original": "アリス",
                            "aliases": [_alias("アリス", "爱丽丝")],
                            "description": "人工人物介绍",
                            "overwrite": True,
                        },
                        {
                            "original": "Shared",
                            "aliases": [_alias("Shared", "人物译名")],
                            "overwrite": True,
                        },
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    changed = merge_glossary_to_file(
        str(prompt_path),
        [
            {
                "original": "Fire",
                "category": "Item",
                "aliases": [_alias("Fire", "火灾")],
            },
            {
                "original": "Cell",
                "category": "Item",
                "aliases": [
                    _alias("Cell", "单元格"),
                    _alias("Cells", "细胞们"),
                ],
                "condition": "AI 不得写入",
                "description": "AI 不得修改",
                "overwrite": False,
                "update": True,
            },
            {
                "original": "NoFlag",
                "category": "Item",
                "aliases": [_alias("NoFlag Alias", "别名")],
            },
            {
                "original": "Legacy",
                "category": "Item",
                "aliases": [_alias("Old Name", "旧称")],
            },
            {
                "original": "アリス",
                "category": "Person",
                "aliases": [_alias("Alice", "艾丽斯")],
                "description": "AI 人物介绍",
                "overwrite": False,
            },
            {
                "original": "Excalibur",
                "category": "Item",
                "aliases": [
                    _alias("Excalibur", "誓约胜利之剑"),
                    _alias("Holy Sword", "圣剑"),
                ],
                "description": "AI 字段应忽略",
                "overwrite": True,
            },
            {
                "original": "Shared",
                "category": "Item",
                "aliases": [_alias("Shared", "物品译名")],
            },
            {
                "original": "InvalidOrder",
                "category": "Item",
                "aliases": [
                    _alias("Different", "不应创建"),
                    _alias("InvalidOrder", "顺序错误"),
                ],
            },
            {
                "original": "InvalidCategory",
                "category": "Unknown",
                "aliases": [_alias("InvalidCategory", "不应创建")],
            },
            {
                "original": "MissingCategory",
                "aliases": [_alias("MissingCategory", "不应创建")],
            },
            {
                "original": "TooManyTranslations",
                "category": "Item",
                "aliases": [
                    {
                        "original": "TooManyTranslations",
                        "translations": [
                            {"text": "重复"},
                            {"text": "重复"},
                        ],
                    }
                ],
            },
            {
                "original": "OldAiShape",
                "translation": "不再接受",
                "category": "Item",
            },
        ],
    )

    assert changed is True
    saved = json.loads(prompt_path.read_text(encoding="utf-8"))
    items = {entry["original"]: entry for entry in saved["glossary"]["Item"]}

    # overwrite=false discards the entire AI result, including a new alias.
    assert items["Fire"]["aliases"] == [
        {
            "original": "Fire",
            "translations": [
                {"text": "解雇", "condition": "非正式聊天"},
                {"text": "开火", "condition": "武器射击"},
            ],
        }
    ]

    # Existing aliases never receive a second AI translation; new aliases append.
    assert items["Cell"]["aliases"] == [
        {"original": "Cell", "translations": [{"text": "细胞"}]},
        {"original": "Cell Block", "translations": [{"text": "牢房区"}]},
        {"original": "Cells", "translations": [{"text": "细胞们"}]},
    ]
    assert items["Cell"]["description"] == "人工注释"
    assert items["Cell"]["overwrite"] is True

    # Missing overwrite is also a closed entry.
    assert items["NoFlag"] == {
        "original": "NoFlag",
        "aliases": [{"original": "NoFlag", "translations": [{"text": "旧译名"}]}],
    }

    # Legacy storage migrates only when a permitted new alias is appended.
    assert items["Legacy"] == {
        "original": "Legacy",
        "aliases": [
            {
                "original": "Legacy",
                "translations": [{"text": "旧译名", "condition": "人工条件"}],
            },
            {"original": "Old Name", "translations": [{"text": "旧称"}]},
        ],
        "overwrite": True,
    }

    assert saved["glossary"]["Person"][0] == {
        "original": "アリス",
        "aliases": [
            {"original": "アリス", "translations": [{"text": "爱丽丝"}]},
            {"original": "Alice", "translations": [{"text": "艾丽斯"}]},
        ],
        "description": "人工人物介绍",
        "overwrite": True,
    }
    assert items["Excalibur"] == {
        "original": "Excalibur",
        "aliases": [
            {"original": "Excalibur", "translations": [{"text": "誓约胜利之剑"}]},
            {"original": "Holy Sword", "translations": [{"text": "圣剑"}]},
        ],
        "overwrite": False,
    }
    assert items["Shared"] == {
        "original": "Shared",
        "aliases": [{"original": "Shared", "translations": [{"text": "物品译名"}]}],
        "overwrite": False,
    }
    assert saved["glossary"]["Person"][1] == {
        "original": "Shared",
        "aliases": [{"original": "Shared", "translations": [{"text": "人物译名"}]}],
        "overwrite": True,
    }
    assert "InvalidOrder" not in items
    assert "InvalidCategory" not in items
    assert "MissingCategory" not in items
    assert "TooManyTranslations" not in items
    assert "OldAiShape" not in items


def test_parse_hq_response_returns_alias_terms():
    response = json.dumps(
        {
            "translations": [{"id": 1, "translation": "译文"}],
            "new_terms": [
                {
                    "original": "Excalibur",
                    "category": "Item",
                    "aliases": [_alias("Excalibur", "誓约胜利之剑")],
                }
            ],
        },
        ensure_ascii=False,
    )

    translations, new_terms = parse_hq_response(response)

    assert translations == ["译文"]
    assert new_terms == [
        {
            "original": "Excalibur",
            "category": "Item",
            "aliases": [_alias("Excalibur", "誓约胜利之剑")],
        }
    ]


def test_glossary_prompts_require_the_alias_delta_shape():
    dict_dir = str(_bootstrap.ROOT / "dict")
    output_prompt = load_system_prompt_hq_format(
        dict_dir,
        "Chinese",
        extract_glossary=True,
    )
    extraction_prompt = load_glossary_extraction_prompt(dict_dir, "Chinese")

    assert 'Each item in "new_terms" MUST have "original", "category", and "aliases"' in output_prompt
    assert '{ "text": "<Chinese translation>" }' in output_prompt
    assert '"translation": "<Chinese translation>"' not in output_prompt
    output_examples = re.findall(r"```json\n(.*?)\n```", output_prompt, flags=re.DOTALL)
    parsed_example = json.loads(output_examples[-1])
    assert parsed_example["new_terms"][0]["aliases"][0]["translations"] == [
        {"text": "<Chinese translation>"}
    ]
    assert "Return new aliases only" in extraction_prompt
    assert "Never add a second AI translation" in extraction_prompt
    assert "No operation flag" not in extraction_prompt
    assert "No control or authored fields" not in extraction_prompt


def main() -> int:
    import pytest

    return pytest.main([__file__, "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
