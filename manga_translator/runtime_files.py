"""Create user-editable runtime tables for every application entry point."""

from __future__ import annotations

from typing import Any


def ensure_runtime_files(logger: Any = None) -> dict[str, str]:
    """Ensure every code-backed runtime table exists without overwriting users."""
    from manga_translator.colorization.prompt_loader import ensure_ai_colorizer_prompt_file
    from manga_translator.custom_api_params import ensure_custom_api_params_file
    from manga_translator.ocr.prompt_loader import ensure_ai_ocr_prompt_file
    from manga_translator.rendering.prompt_loader import ensure_ai_renderer_prompt_file
    from manga_translator.rendering.rich_text_rules import ensure_rich_text_rules_exists
    from manga_translator.rendering.text_replacements import ensure_text_replacements_exists
    from manga_translator.utils.text_filter import ensure_filter_list_exists
    from manga_translator.utils.translation_template import ensure_translation_template_exists

    factories = (
        ("custom_api_params", lambda: ensure_custom_api_params_file(logger=logger)),
        ("ocr_prompt", ensure_ai_ocr_prompt_file),
        ("renderer_prompt", ensure_ai_renderer_prompt_file),
        ("colorizer_prompt", ensure_ai_colorizer_prompt_file),
        ("filter_list", ensure_filter_list_exists),
        ("text_replacements", ensure_text_replacements_exists),
        ("rich_text_rules", ensure_rich_text_rules_exists),
        ("translation_template", ensure_translation_template_exists),
    )
    paths: dict[str, str] = {}
    for label, factory in factories:
        try:
            path = factory()
            paths[label] = path
        except Exception as exc:
            if logger:
                logger.warning(f"Failed to prepare runtime table [{label}]: {exc}")
    return paths
