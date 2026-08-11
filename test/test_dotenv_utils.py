import _bootstrap  # noqa: F401
import pytest

from manga_translator.utils import dotenv_utils
from manga_translator.utils.dotenv_utils import (
    read_dotenv_file,
    remove_invalid_dotenv_lines,
)


def test_remove_invalid_dotenv_lines_preserves_valid_entries_and_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'GOOD="one"\nBROKEN VALUE\n# keep this comment\nexport NEXT="two"\n',
        encoding="utf-8",
    )

    assert remove_invalid_dotenv_lines(env_path) == 1
    assert env_path.read_text(encoding="utf-8") == (
        'GOOD="one"\n# keep this comment\nexport NEXT="two"\n'
    )
    assert read_dotenv_file(env_path) == {"GOOD": "one", "NEXT": "two"}
    assert remove_invalid_dotenv_lines(env_path) == 0



def test_remove_invalid_dotenv_lines_keeps_original_when_replace_is_blocked(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    original = 'GOOD="one"\nBROKEN VALUE\n'
    env_path.write_text(original, encoding="utf-8")

    def reject_replace(_source, _target):
        raise PermissionError("blocked by security software")

    monkeypatch.setattr(dotenv_utils.os, "replace", reject_replace)

    with pytest.raises(PermissionError, match="blocked by security software"):
        remove_invalid_dotenv_lines(env_path)

    assert env_path.read_text(encoding="utf-8") == original