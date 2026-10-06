from pathlib import Path

import pytest

from manga_translator import server_paths
from manga_translator.config import TranslatorConfig


@pytest.fixture()
def prompt_layout(tmp_path, monkeypatch):
    shipped = tmp_path / "dict" / "prompt_example.yaml"
    shipped.parent.mkdir(parents=True)
    shipped.write_text("glossary:\n  Person: []\n", encoding="utf-8")
    user_dir = tmp_path / "manga_translator" / "server" / "data" / "user_resources" / "prompts"
    user_file = user_dir / "prompt_user.yaml"
    monkeypatch.setattr(server_paths, "get_application_dir", lambda: str(tmp_path))
    monkeypatch.setattr(server_paths, "SHIPPED_PROMPT_FILE", shipped)
    monkeypatch.setattr(server_paths, "USER_PROMPTS_DIR", user_dir)
    monkeypatch.setattr(server_paths, "USER_PROMPT_FILE", user_file)
    return shipped, user_file


def test_shipped_relative_prompt_is_redirected_to_user_copy(prompt_layout):
    shipped, user_file = prompt_layout
    resolved = server_paths.redirect_shipped_prompt_path("dict/prompt_example.yaml")
    assert resolved == user_file.resolve().as_posix()
    assert user_file.read_text(encoding="utf-8") == shipped.read_text(encoding="utf-8")


def test_shipped_absolute_prompt_is_redirected(prompt_layout):
    shipped, user_file = prompt_layout
    resolved = server_paths.redirect_shipped_prompt_path(str(shipped))
    assert resolved == user_file.resolve().as_posix()


def test_existing_user_copy_is_not_overwritten(prompt_layout):
    _, user_file = prompt_layout
    first = server_paths.redirect_shipped_prompt_path("dict/prompt_example.yaml")
    user_file.write_text("glossary:\n  Person:\n    - original: ひろし\n", encoding="utf-8")
    second = server_paths.redirect_shipped_prompt_path("dict/prompt_example.yaml")
    assert first == second
    assert "ひろし" in user_file.read_text(encoding="utf-8")


def test_custom_prompt_path_is_kept(prompt_layout):
    custom = "dict/my_manga_prompt.yaml"
    assert server_paths.redirect_shipped_prompt_path(custom) == custom


def test_empty_prompt_path_is_kept(prompt_layout):
    assert server_paths.redirect_shipped_prompt_path(None) is None
    assert server_paths.redirect_shipped_prompt_path("") == ""


def test_translator_config_applies_redirect(prompt_layout):
    _, user_file = prompt_layout
    config = TranslatorConfig(high_quality_prompt_path="dict/prompt_example.yaml")
    assert config.high_quality_prompt_path == user_file.resolve().as_posix()
