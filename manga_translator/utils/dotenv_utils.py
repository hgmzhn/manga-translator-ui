"""
Helpers for reading and writing .env files with python-dotenv-compatible syntax.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Mapping, Optional, Union

from dotenv import dotenv_values, load_dotenv
from dotenv.parser import parse_stream

PathLike = Union[str, os.PathLike[str]]

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
APP_DOTENV_PATH_ENV = "MANGA_TRANSLATOR_ENV_PATH"
_ENV_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*="
)


def validate_env_key(key: str) -> str:
    """Validate and return a standard environment variable key."""
    key = str(key)
    if not _ENV_KEY_RE.fullmatch(key):
        raise ValueError(f"Invalid environment variable key: {key!r}")
    return key


def normalize_env_value(value: object) -> str:
    """Normalize values before writing them to a .env file."""
    return "" if value is None else str(value)


def format_env_line(key: str, value: object) -> str:
    """Format one KEY=VALUE line that python-dotenv can parse reliably."""
    key = validate_env_key(key)
    value = normalize_env_value(value)
    escaped_value = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'{key}="{escaped_value}"\n'


def read_dotenv_file(path: PathLike) -> Dict[str, str]:
    """Read a .env file using python-dotenv's parser."""
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values = dotenv_values(env_path, encoding="utf-8")
    return {
        key: "" if value is None else value
        for key, value in values.items()
        if key is not None
    }


def load_app_dotenv(
    dotenv_path: Optional[PathLike] = None,
    *,
    override: bool = True,
) -> bool:
    """Load the app .env from the configured path, falling back to discovery."""
    selected_path = dotenv_path or os.environ.get(APP_DOTENV_PATH_ENV)
    if selected_path:
        return load_dotenv(selected_path, override=override, encoding="utf-8")
    return load_dotenv(override=override, encoding="utf-8")


def write_dotenv_file(path: PathLike, env_vars: Mapping[str, object]) -> None:
    """Rewrite a .env file using the canonical formatter."""
    env_path = Path(path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(format_env_line(key, value) for key, value in env_vars.items())
    env_path.write_text(content, encoding="utf-8")


def update_dotenv_file(
    path: PathLike,
    key: str,
    value: object,
    *,
    drop_invalid: bool = False,
) -> None:
    """
    Update one key in a .env file.

    Existing parseable lines and comments are preserved. If drop_invalid is true,
    statements that python-dotenv cannot parse are removed so future loads do not
    keep emitting parse warnings.
    """
    env_path = Path(path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    key = validate_env_key(key)
    line_out = format_env_line(key, value)

    if not env_path.exists():
        env_path.write_text(line_out, encoding="utf-8")
        return

    lines: list[str] = []
    replaced = False

    with env_path.open("r", encoding="utf-8") as source:
        for mapping in parse_stream(source):
            original = mapping.original.string
            mapping_key: Optional[str] = mapping.key

            if mapping.error:
                match = _ENV_ASSIGNMENT_RE.match(original)
                mapping_key = match.group("key") if match else None

            if mapping_key == key:
                if not replaced:
                    lines.append(line_out)
                    replaced = True
                continue

            if mapping.error and drop_invalid:
                continue

            lines.append(original)

    if not replaced:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines.append("\n")
        lines.append(line_out)

    env_path.write_text("".join(lines), encoding="utf-8")


def delete_dotenv_keys(
    path: PathLike,
    keys: set[str] | list[str] | tuple[str, ...],
    *,
    drop_invalid: bool = False,
) -> None:
    """Delete keys from a .env file while preserving other parseable lines."""
    env_path = Path(path)
    if not env_path.exists():
        return

    normalized_keys = {validate_env_key(key) for key in keys}
    if not normalized_keys:
        return

    lines: list[str] = []
    with env_path.open("r", encoding="utf-8") as source:
        for mapping in parse_stream(source):
            original = mapping.original.string
            mapping_key: Optional[str] = mapping.key

            if mapping.error:
                match = _ENV_ASSIGNMENT_RE.match(original)
                mapping_key = match.group("key") if match else None

            if mapping_key in normalized_keys:
                continue

            if mapping.error and drop_invalid:
                continue

            lines.append(original)

    env_path.write_text("".join(lines), encoding="utf-8")
