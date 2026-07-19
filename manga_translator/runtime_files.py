"""Create user-editable runtime tables for every application entry point."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from manga_translator.runtime_paths import get_config_path


def _normalized_md5(content: str) -> str:
    normalized = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _upgrade_default_file(
    path: str,
    *,
    legacy_md5: set[str],
    label: str,
    logger: Any = None,
) -> bool:
    """旧默认文件命中哈希时删除，后续 ensure 流程会重新创建。"""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if _normalized_md5(content) not in legacy_md5:
            return False
        os.remove(path)
        if logger:
            logger.info(f"Removed legacy runtime default [{label}]: {path}")
        return True
    except Exception as exc:
        if logger:
            logger.warning(f"Failed to upgrade runtime default [{label}]: {exc}")
        return False


def _upgrade_runtime_defaults(logger: Any = None) -> None:
    """统一升级仍保持历史内置内容的运行时配置文件。"""
    migrations = (
        (
            "translation_template",
            get_config_path("translation_template.json"),
            {"5c7b585099251410a1ad82e14d0028d8"},
        ),
        (
            "text_replacements",
            get_config_path("text_replacements.yaml"),
            {
                # commit 52942b1：旧内置默认模板
                "5b8fbc89492ff2a1d5c064f5e85a458b",
                # commit 2b70f6f：经旧表格编辑器保存后的默认模板
                "94b2787940afdde800db3aba0742ad98",
            },
        ),
    )
    for label, path, legacy_md5 in migrations:
        _upgrade_default_file(
            path,
            legacy_md5=legacy_md5,
            label=label,
            logger=logger,
        )


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

    _upgrade_runtime_defaults(logger)

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
