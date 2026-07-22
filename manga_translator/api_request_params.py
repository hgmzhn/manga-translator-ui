from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_GEMINI_REQUEST_KEY_MAP = {
    "safety_settings": "safetySettings",
    "system_instruction": "systemInstruction",
    "tool_config": "toolConfig",
    "cached_content": "cachedContent",
    "automatic_function_calling": "automaticFunctionCalling",
}
_GEMINI_REQUEST_KEYS = set(_GEMINI_REQUEST_KEY_MAP.values()) | {"tools"}
_GEMINI_GENERATION_KEY_MAP = {
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
_GEMINI_SDK_KEY_MAP = {
    api_key: sdk_key for sdk_key, api_key in _GEMINI_GENERATION_KEY_MAP.items()
}


def merge_openai_chat_request_params(
    base_params: Mapping[str, Any],
    custom_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(base_params)
    for key, value in _iter_params(custom_params):
        if key not in {"model", "messages", "stream"}:
            merged[key] = value
    return merged


def normalize_openai_image_request_params(
    custom_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    params = dict(custom_params or {})
    extra_body = params.pop("extra_body", None)
    if isinstance(extra_body, Mapping):
        for key, value in extra_body.items():
            params.setdefault(str(key), value)

    reserved = {"model", "prompt", "image", "images", "messages", "input"}
    return {key: value for key, value in _iter_params(params) if key not in reserved}


def split_gemini_request_params(
    custom_params: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request_overrides: dict[str, Any] = {}
    generation_overrides: dict[str, Any] = {}

    for key, value in _iter_params(custom_params):
        if key in {"model", "contents"}:
            continue
        if key in {"generationConfig", "generation_config"} and isinstance(value, Mapping):
            for nested_key, nested_value in _iter_params(value):
                generation_overrides[_gemini_api_generation_key(nested_key)] = nested_value
            continue

        request_key = _GEMINI_REQUEST_KEY_MAP.get(key)
        if request_key or key in _GEMINI_REQUEST_KEYS:
            request_overrides[request_key or key] = value
            continue
        generation_overrides[_gemini_api_generation_key(key)] = value

    return request_overrides, generation_overrides


def apply_gemini_sdk_generation_params(
    generation_config: Any,
    custom_params: Mapping[str, Any] | None,
) -> None:
    _, generation_params = split_gemini_request_params(custom_params)
    for api_key, value in generation_params.items():
        sdk_key = _GEMINI_SDK_KEY_MAP.get(api_key, _camel_to_snake(api_key))
        if hasattr(generation_config, sdk_key):
            setattr(generation_config, sdk_key, value)


def _iter_params(params: Mapping[str, Any] | None):
    for raw_key, value in dict(params or {}).items():
        key = str(raw_key).strip()
        if key:
            yield key, value


def _gemini_api_generation_key(key: str) -> str:
    if key in _GEMINI_GENERATION_KEY_MAP:
        return _GEMINI_GENERATION_KEY_MAP[key]
    if "_" not in key:
        return key
    parts = [part for part in key.split("_") if part]
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camel_to_snake(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char.isupper() and chars:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)
