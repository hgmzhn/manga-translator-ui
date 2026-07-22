from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

from manga_translator.runtime_paths import get_config_dir


DEFAULT_CUSTOM_API_PARAMS_PRESET = "通用"
CUSTOM_API_PARAM_SECTIONS = (
    "common",
    "translator",
    "ocr",
    "colorizer",
    "render",
)

_DEFAULT_CUSTOM_API_PARAMS_DATA = {
    DEFAULT_CUSTOM_API_PARAMS_PRESET: {
        "common": {},
        "translator": {
            "temperature": 0.3,
            "top_p": 0.95,
        },
        "ocr": {
            "temperature": 0.0,
        },
        "colorizer": {},
        "render": {},
    }
}

_LEGACY_DEFAULT_CUSTOM_API_PARAMS_MD5 = "078a7568301d5d22db0d5b53b286115c"
_CUSTOM_API_PARAMS_PATH = os.path.join(get_config_dir(), "custom_api_params.json")


def get_custom_api_params_path(path: str | None = None) -> str:
    return os.path.abspath(path or _CUSTOM_API_PARAMS_PATH)


def ensure_custom_api_params_file(path: str | None = None, logger=None) -> str:
    config_path = get_custom_api_params_path(path)
    if not os.path.exists(config_path):
        _write_custom_api_params_file(config_path, _DEFAULT_CUSTOM_API_PARAMS_DATA)
        _log(logger, "info", f"已创建自定义API参数配置文件: {config_path}")
        return config_path

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            content = file.read()
    except Exception as exc:
        _log(logger, "warning", f"读取自定义API参数配置失败: {exc}")
        return config_path

    if _normalized_md5(content) == _LEGACY_DEFAULT_CUSTOM_API_PARAMS_MD5:
        try:
            os.remove(config_path)
            _write_custom_api_params_file(config_path, _DEFAULT_CUSTOM_API_PARAMS_DATA)
            _log(logger, "info", f"已重建旧版默认自定义API参数配置: {config_path}")
        except Exception as exc:
            _log(logger, "warning", f"重建旧版默认自定义API参数配置失败: {exc}")
        return config_path

    try:
        data = json.loads(content)
    except Exception:
        return config_path

    migrated, was_legacy = migrate_legacy_custom_api_params_payload(data)
    if was_legacy:
        try:
            _write_custom_api_params_file(config_path, migrated)
            _log(logger, "info", f"已迁移旧版自定义API参数配置: {config_path}")
        except Exception as exc:
            _log(logger, "warning", f"迁移旧版自定义API参数配置失败: {exc}")
    return config_path


def migrate_legacy_custom_api_params_config(data: Any) -> Any:
    """Migrate the legacy main-config location of ``use_custom_api_params``."""
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


def migrate_legacy_custom_api_params_payload(
    data: Any,
) -> tuple[dict[str, dict[str, dict[str, Any]]], bool]:
    """Return the canonical model-preset payload and whether legacy migration ran."""
    if not isinstance(data, dict):
        return _empty_presets(), False

    preset_entries = {
        str(raw_name): raw_preset
        for raw_name, raw_preset in data.items()
        if _is_model_preset_entry(str(raw_name), raw_preset)
    }
    if preset_entries:
        normalized = normalize_custom_api_params_presets(preset_entries)
        legacy_entries = [
            (str(raw_key), value)
            for raw_key, value in data.items()
            if str(raw_key) not in preset_entries
        ]
        general = normalized[DEFAULT_CUSTOM_API_PARAMS_PRESET]
        for key, value in legacy_entries:
            if key in CUSTOM_API_PARAM_SECTIONS and isinstance(value, Mapping):
                general[key].update(dict(value))
            else:
                general["common"][key] = value
        was_migrated = bool(legacy_entries) or (
            DEFAULT_CUSTOM_API_PARAMS_PRESET not in preset_entries
        )
        return normalized, was_migrated

    migrated_preset = _empty_preset()
    for raw_key, value in data.items():
        key = str(raw_key)
        if key in CUSTOM_API_PARAM_SECTIONS and isinstance(value, Mapping):
            migrated_preset[key].update(dict(value))
        else:
            migrated_preset["common"][key] = value

    return {DEFAULT_CUSTOM_API_PARAMS_PRESET: migrated_preset}, True


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
            _log(logger, "error", f"自定义API参数配置必须是 JSON 对象: {config_path}")
            return _empty_presets()
        normalized, _ = migrate_legacy_custom_api_params_payload(params)
        return normalized
    except Exception as exc:
        _log(logger, "error", f"加载自定义API参数配置失败: {exc}")
        return _empty_presets()


def normalize_custom_api_params_presets(
    data: Any,
) -> dict[str, dict[str, dict[str, Any]]]:
    normalized: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(data, Mapping):
        return _empty_presets()

    for raw_name, raw_preset in data.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_preset, Mapping):
            continue

        preset = _empty_preset()
        for section in CUSTOM_API_PARAM_SECTIONS:
            values = raw_preset.get(section)
            if isinstance(values, Mapping):
                preset[section].update(dict(values))
        normalized[name] = preset

    if DEFAULT_CUSTOM_API_PARAMS_PRESET not in normalized:
        normalized = {
            DEFAULT_CUSTOM_API_PARAMS_PRESET: _empty_preset(),
            **normalized,
        }
    elif next(iter(normalized), None) != DEFAULT_CUSTOM_API_PARAMS_PRESET:
        general = normalized.pop(DEFAULT_CUSTOM_API_PARAMS_PRESET)
        normalized = {DEFAULT_CUSTOM_API_PARAMS_PRESET: general, **normalized}
    return normalized


def build_custom_api_params_payload(
    presets: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> dict[str, dict[str, dict[str, Any]]]:
    return normalize_custom_api_params_presets(dict(presets or {}))


def create_empty_custom_api_params_preset() -> dict[str, dict[str, Any]]:
    return _empty_preset()


def resolve_custom_api_params_for_model(
    payload: Mapping[str, Any] | None,
    model_name: str | None,
    section: str,
) -> dict[str, Any]:
    if section not in CUSTOM_API_PARAM_SECTIONS:
        raise ValueError(f"Unsupported custom API params section: {section}")

    presets, _ = migrate_legacy_custom_api_params_payload(dict(payload or {}))
    requested_model = str(model_name or "").strip()
    preset_name = (
        requested_model
        if requested_model and requested_model in presets
        else DEFAULT_CUSTOM_API_PARAMS_PRESET
    )
    preset = presets.get(preset_name) or _empty_preset()

    resolved = dict(preset.get("common") or {})
    if section != "common":
        resolved.update(preset.get(section) or {})
    return resolved


def resolve_custom_api_params(
    config: Any,
    logger,
    *,
    model_name: str | None,
    section: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Load, match and merge one model preset without applying API-specific rules."""
    if not is_custom_api_params_enabled(config):
        return {}

    presets = load_custom_api_params_file(logger, path=path)
    resolved = resolve_custom_api_params_for_model(presets, model_name, section)
    if resolved:
        requested_model = str(model_name or "").strip()
        preset_name = (
            requested_model
            if requested_model and requested_model in presets
            else DEFAULT_CUSTOM_API_PARAMS_PRESET
        )
        _log(
            logger,
            "info",
            f"已启用自定义API参数[{section}]，模型={requested_model or '(empty)'}，预设={preset_name}",
        )
    return resolved


def _empty_preset() -> dict[str, dict[str, Any]]:
    return {section: {} for section in CUSTOM_API_PARAM_SECTIONS}


def _is_model_preset_entry(name: str, value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if name == DEFAULT_CUSTOM_API_PARAMS_PRESET:
        return True
    return any(
        section in value and isinstance(value[section], Mapping)
        for section in CUSTOM_API_PARAM_SECTIONS
    )


def _empty_presets() -> dict[str, dict[str, dict[str, Any]]]:
    return {DEFAULT_CUSTOM_API_PARAMS_PRESET: _empty_preset()}


def _write_custom_api_params_file(path: str, data: Mapping[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary_path = f"{path}.tmp"
    with open(temporary_path, "w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")
    os.replace(temporary_path, path)


def _normalized_md5(content: str) -> str:
    normalized = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _get_value(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _log(logger, level: str, message: str) -> None:
    callback = getattr(logger, level, None)
    if callable(callback):
        callback(message)
