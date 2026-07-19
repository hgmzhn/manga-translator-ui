from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from manga_translator.runtime_paths import get_config_dir

CUSTOM_API_PARAM_SECTIONS = ("common", "translator", "ocr", "render", "colorizer")
_SECTION_ALIASES = {
    "common": "common",
    "global": "common",
    "shared": "common",
    "all": "common",
    "translator": "translator",
    "translation": "translator",
    "ocr": "ocr",
    "render": "render",
    "renderer": "render",
    "rendering": "render",
    "colorizer": "colorizer",
    "colourizer": "colorizer",
    "colorization": "colorizer",
}

_GEMINI_TOP_LEVEL_ALIASES = {
    "safety_settings": "safetySettings",
    "system_instruction": "systemInstruction",
    "tool_config": "toolConfig",
    "cached_content": "cachedContent",
    "automatic_function_calling": "automaticFunctionCalling",
}
_GEMINI_TOP_LEVEL_KEYS = set(_GEMINI_TOP_LEVEL_ALIASES.values()) | {"tools"}

_GEMINI_GENERATION_CONFIG_ALIASES = {
    "top_p": "topP",
    "top_k": "topK",
    "max_output_tokens": "maxOutputTokens",
    "stop_sequences": "stopSequences",
    "candidate_count": "candidateCount",
    "response_modalities": "responseModalities",
    "response_mime_type": "responseMimeType",
    "response_schema": "responseSchema",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
    "thinking_budget": "thinkingBudget",
}

_DEFAULT_CUSTOM_API_PARAMS_DATA = {
    "translator": {
        "temperature": 0.3,
        "top_p": 0.95,
    },
    "ocr": {
        "temperature": 0.0,
    },
}


_CUSTOM_API_PARAMS_PATH = os.path.join(get_config_dir(), "custom_api_params.json")


def get_custom_api_params_path(path: str | None = None) -> str:
    return os.path.abspath(path or _CUSTOM_API_PARAMS_PATH)


def ensure_custom_api_params_file(path: str | None = None, logger=None) -> str:
    config_path = get_custom_api_params_path(path)
    if os.path.exists(config_path):
        return config_path

    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as file:
        json.dump(_DEFAULT_CUSTOM_API_PARAMS_DATA, file, indent=2, ensure_ascii=False)
        file.write("\n")

    if logger:
        logger.info(f"已创建自定义API参数配置文件: {config_path}")
    return config_path


def migrate_legacy_custom_api_params_config(data: Any) -> Any:
    if not isinstance(data, dict):
        return data

    translator = data.get("translator")
    if not isinstance(translator, dict) or "use_custom_api_params" not in translator:
        return data

    migrated = dict(data)
    migrated_translator = dict(translator)
    legacy_value = migrated_translator.pop("use_custom_api_params", None)
    migrated["translator"] = migrated_translator
    migrated.setdefault("use_custom_api_params", legacy_value)
    return migrated


def is_custom_api_params_enabled(config: Any) -> bool:
    direct_value = _get_value(config, "use_custom_api_params", default=None)
    if direct_value is not None:
        return bool(direct_value)

    translator_config = _get_value(config, "translator", default=None)
    legacy_value = _get_value(translator_config, "use_custom_api_params", default=None)
    return bool(legacy_value)


def load_custom_api_params_file(logger, path: str | None = None) -> dict[str, Any]:
    config_path = get_custom_api_params_path(path)
    try:
        ensure_custom_api_params_file(config_path, logger=logger)
        with open(config_path, "r", encoding="utf-8") as file:
            params = json.load(file)
        if not isinstance(params, dict):
            logger.error(f"自定义API参数配置必须是 JSON 对象: {config_path}")
            return {}
        logger.info(f"已加载自定义API参数配置: {params}")
        return params
    except Exception as exc:
        logger.error(f"加载自定义API参数配置失败: {exc}")
    return {}


def normalize_custom_api_params_payload(data: Any) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {
        section: {} for section in CUSTOM_API_PARAM_SECTIONS
    }
    if not isinstance(data, dict):
        return normalized

    for raw_key, value in data.items():
        section = _SECTION_ALIASES.get(str(raw_key or "").strip().lower())
        if section and isinstance(value, Mapping):
            normalized[section].update(dict(value))
        else:
            normalized["common"][str(raw_key)] = value

    return normalized


def build_custom_api_params_payload(section_data: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any]:
    normalized = normalize_custom_api_params_payload(dict(section_data or {}))
    payload: dict[str, Any] = {}
    for section in CUSTOM_API_PARAM_SECTIONS:
        values = normalized.get(section) or {}
        if values:
            payload[section] = dict(values)
    return payload


def resolve_custom_api_params_for_target(
    payload: Mapping[str, Any] | None,
    target: str,
) -> dict[str, Any]:
    normalized = normalize_custom_api_params_payload(dict(payload or {}))
    section = _SECTION_ALIASES.get(str(target or "").strip().lower())
    if section not in CUSTOM_API_PARAM_SECTIONS:
        raise ValueError(f"Unsupported custom API params target: {target}")

    resolved = dict(normalized["common"])
    if section != "common":
        resolved.update(normalized[section])
    return resolved


def load_enabled_custom_api_params(
    config: Any,
    logger,
    target: str,
    path: str | None = None,
) -> dict[str, Any]:
    if not is_custom_api_params_enabled(config):
        return {}

    params = load_custom_api_params_file(logger, path=path)
    resolved = resolve_custom_api_params_for_target(params, target)
    if resolved:
        logger.info(f"已启用自定义API参数[{target}]: {resolved}")
    return resolved


def load_custom_api_params_sections(
    config: Any,
    logger,
    path: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not is_custom_api_params_enabled(config):
        return {section: {} for section in CUSTOM_API_PARAM_SECTIONS}

    params = load_custom_api_params_file(logger, path=path)
    normalized = normalize_custom_api_params_payload(params)
    if any(normalized.values()):
        logger.info(f"已启用分类自定义API参数: {normalized}")
    return normalized


def merge_openai_request_params(
    base_params: dict[str, Any],
    custom_api_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(base_params)
    if custom_api_params:
        merged.update(dict(custom_api_params))
    return merged


def split_gemini_request_params(
    custom_api_params: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request_overrides: dict[str, Any] = {}
    generation_overrides: dict[str, Any] = {}

    if not custom_api_params:
        return request_overrides, generation_overrides

    for raw_key, value in dict(custom_api_params).items():
        key = str(raw_key or "").strip()
        if not key:
            continue

        if key in {"generationConfig", "generation_config"} and isinstance(value, Mapping):
            for nested_key, nested_value in dict(value).items():
                generation_overrides[_normalize_gemini_generation_key(str(nested_key))] = nested_value
            continue

        normalized_request_key = _GEMINI_TOP_LEVEL_ALIASES.get(key)
        if normalized_request_key or key in _GEMINI_TOP_LEVEL_KEYS:
            request_overrides[normalized_request_key or key] = value
            continue

        generation_overrides[_normalize_gemini_generation_key(key)] = value

    return request_overrides, generation_overrides


def _get_value(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _normalize_gemini_generation_key(key: str) -> str:
    if key in _GEMINI_GENERATION_CONFIG_ALIASES:
        return _GEMINI_GENERATION_CONFIG_ALIASES[key]
    if "_" in key:
        return _camelize(key)
    return key


def _camelize(value: str) -> str:
    parts = [part for part in value.split("_") if part]
    if not parts:
        return value
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
