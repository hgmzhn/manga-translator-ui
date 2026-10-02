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
    "You will receive a manga page and a list of entries containing translation and original "
    "(when available). Use each original text as a reference to locate its "
    "matching text region on the page; use the surrounding visual context and reading order "
    "to distinguish repeated texts. Erase the original text completely and render the provided "
    "translation exactly in its place. Do not retranslate, "
    "paraphrase, omit, or duplicate provided translations. The original field is only a "
    "matching reference: do not render it or the field labels. Fit the lettering to the "
    "region while preserving the artwork, bubble shapes, panel borders, and page layout. "
    "For remaining source-language text with no provided translation, including missed bubbles "
    "and sound effects, translate it into the same target language as the provided translations "
    "and render it in place. Remove associated furigana/ruby when replacing source text. "
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
    """Create the default prompt or upgrade an exact legacy default, preserving custom prompts."""
    resolved_path = resolve_ai_renderer_prompt_path(path)
    if not os.path.exists(resolved_path):
        save_ai_renderer_prompt_file(resolved_path, DEFAULT_AI_RENDERER_PROMPT)
        return resolved_path

    current_prompt = load_ai_renderer_prompt_file(resolved_path)
    if current_prompt in LEGACY_AI_RENDERER_PROMPTS:
        save_ai_renderer_prompt_file(resolved_path, DEFAULT_AI_RENDERER_PROMPT)
    return resolved_path
