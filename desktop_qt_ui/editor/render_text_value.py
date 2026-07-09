"""Shared text selection helpers for editor render and measurement paths."""

from __future__ import annotations

from typing import Any

from manga_translator.rendering.rich_text import has_content, is_rich_text_document


def has_renderable_text(value: Any) -> bool:
    # 薄委托：富文本/纯文本"是否有可渲染内容"的唯一实现在 rich_text.py（F12）。
    return has_content(value)


def render_text_value_from_region(region_data: dict) -> Any:
    rich = region_data.get("translation_rich") if isinstance(region_data, dict) else None
    if is_rich_text_document(rich):
        return rich
    return region_data.get("translation", "") if isinstance(region_data, dict) else ""


def render_text_value_from_text_block(text_block) -> Any:
    if hasattr(text_block, "get_translation_for_rendering"):
        value = text_block.get_translation_for_rendering()
        if has_renderable_text(value):
            return value
        # 未翻译（仅检测/OCR）的区域回退显示原文预览，而不是画布空白（F29）
        return getattr(text_block, "text", "")
    return getattr(text_block, "translation", "") or getattr(text_block, "text", "")
