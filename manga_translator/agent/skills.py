"""Read the Agent's built-in skills from the application's skills directory."""

from pathlib import Path
from typing import Literal

from ..utils import BASE_PATH


SkillName = Literal["manga-translation", "manga-layout", "rich-text"]


def load_skill(name: SkillName) -> str:
    """Resolve a known skill independently of the host's working directory."""
    if name not in {"manga-translation", "manga-layout", "rich-text"}:
        raise ValueError(f"Unknown Agent skill: {name}")
    return (Path(BASE_PATH) / "skills" / name / "SKILL.md").read_text(encoding="utf-8").strip()
