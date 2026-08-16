import _bootstrap  # noqa: F401

from manga_translator import runtime_files


def test_prior_dash_rule_defaults_are_recreated(monkeypatch, tmp_path):
    paths = {
        "translation_template.json": tmp_path / "translation_template.json",
        "text_replacements.yaml": tmp_path / "text_replacements.yaml",
        "rich_text_rules.yaml": tmp_path / "rich_text_rules.yaml",
    }
    paths["text_replacements.yaml"].write_text("legacy replacements", encoding="utf-8")
    paths["rich_text_rules.yaml"].write_text("legacy rich rules", encoding="utf-8")
    legacy_hashes = {
        "legacy replacements": "5ab345740c972146a561b682753fe07e",
        "legacy rich rules": "3119fa189c04c4077e64853aa4e6beaf",
    }

    monkeypatch.setattr(runtime_files, "get_config_path", lambda name: str(paths[name]))
    monkeypatch.setattr(runtime_files, "_normalized_md5", legacy_hashes.get)

    runtime_files._upgrade_runtime_defaults()

    assert not paths["text_replacements.yaml"].exists()
    assert not paths["rich_text_rules.yaml"].exists()
