import os
from typing import Optional

from ..translators.prompt_loader import load_prompt_file
from ..utils import BASE_PATH

LEGACY_AI_RENDERER_PROMPTS = (
    "You are a manga typesetting renderer. You will receive a cleaned manga page image with "
    "numbered boxes marking text regions, plus a numbered translation list. Render each provided "
    "translation into the matching numbered region. Remove the numbered boxes, their numeric "
    "labels, outlines, and any helper marks from the final image. Preserve artwork, panel "
    "borders, perspective, and reading order. Translate and render every provided item, "
    "including sound effects and onomatopoeia. Do not invent extra text. Return only the final "
    "rendered image.",
)

DEFAULT_AI_RENDERER_PROMPT = (
    "For each provided translation, locate the matching original text on the manga page, "
    "erase the original text completely, and render the translation in its place. For any "
    "remaining untranslated text (such as missed bubbles, sound effects, or furigana/rubi characters), "
    "erase it, translate it, and render the translation. Preserve all artwork and page layout. "
    "Return only the rendered image."
)

DEFAULT_AI_RENDERER_PROMPT_PATH = os.path.join("dict", "ai_renderer_prompt.yaml").replace("\\", "/")
AI_RENDERER_PROMPT_KEYS = ("ai_renderer_prompt", "renderer_prompt", "prompt")


def resolve_ai_renderer_prompt_path(path: Optional[str]) -> str:
    rel_path = (path or DEFAULT_AI_RENDERER_PROMPT_PATH).replace("\\", "/")
    if os.path.isabs(rel_path):
        return os.path.normpath(rel_path)
    return os.path.normpath(os.path.join(BASE_PATH, rel_path))


def load_ai_renderer_prompt_file(path: Optional[str]) -> str:
    resolved_path = resolve_ai_renderer_prompt_path(path)
    if not os.path.exists(resolved_path):
        return ""

    data = load_prompt_file(resolved_path)
    if not isinstance(data, dict):
        return ""

    for key in AI_RENDERER_PROMPT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def save_ai_renderer_prompt_file(path: Optional[str], prompt_text: str) -> str:
    resolved_path = resolve_ai_renderer_prompt_path(path)
    os.makedirs(os.path.dirname(resolved_path), exist_ok=True)

    lines = prompt_text.splitlines() or [""]
    content = ["ai_renderer_prompt: |"]
    content.extend(f"  {line}" if line else "  " for line in lines)
    with open(resolved_path, "w", encoding="utf-8") as f:
        f.write("\n".join(content) + "\n")
    return resolved_path


def ensure_ai_renderer_prompt_file(path: Optional[str] = None) -> str:
    resolved_path = resolve_ai_renderer_prompt_path(path)
    if not os.path.exists(resolved_path):
        save_ai_renderer_prompt_file(resolved_path, DEFAULT_AI_RENDERER_PROMPT)
        return resolved_path

    current_prompt = load_ai_renderer_prompt_file(resolved_path)
    if current_prompt in LEGACY_AI_RENDERER_PROMPTS:
        save_ai_renderer_prompt_file(resolved_path, DEFAULT_AI_RENDERER_PROMPT)
    return resolved_path
