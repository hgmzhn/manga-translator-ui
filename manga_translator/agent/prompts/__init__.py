"""UTF-8 Markdown prompts bundled alongside the Agent package."""

from importlib.resources import files
from typing import Literal


def load_prompt(name: Literal["chat", "page", "editing", "skills", "validation"]) -> str:
    if name not in {"chat", "page", "editing", "skills", "validation"}:
        raise ValueError(f"Unknown Agent prompt: {name}")
    return files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8").strip()
