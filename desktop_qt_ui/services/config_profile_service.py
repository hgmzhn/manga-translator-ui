"""Named parameter-profile storage.

Parameter profiles intentionally live separately from ``PresetService``.  The
latter manages API values in ``.env``; this service stores the settings that
control the translation pipeline and never copies API credentials into a
profile.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List


PROFILE_SCHEMA_VERSION = 1
DEFAULT_PROFILE_NAME = "default"
PROFILE_DIRNAME = "parameter_profiles"

PROFILE_SECTIONS = (
    "filter_text_enabled",
    "kernel_size",
    "mask_dilation_offset",
    "use_custom_api_params",
    "translator",
    "ocr",
    "detector",
    "inpainter",
    "render",
    "upscale",
    "colorizer",
    "cli",
)
PROFILE_APP_FIELDS = ("unload_models_after_translation",)
SENSITIVE_TRANSLATOR_FIELDS = frozenset(
    {
        "user_api_key",
        "aliyun_access_key_id",
        "aliyun_access_key_secret",
    }
)
_INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ConfigProfileError(ValueError):
    """Expected, user-correctable profile operation error."""


class ConfigProfileProtectedError(ConfigProfileError):
    """Raised when an operation is not allowed for the default profile."""


class ConfigProfileService:
    """Persist named, non-secret translation parameter profiles as JSON."""

    def __init__(self, root_dir: str):
        self.logger = logging.getLogger(__name__)
        self.root_dir = os.path.abspath(str(root_dir))
        self.profiles_dir = os.path.join(
            self.root_dir, "config", PROFILE_DIRNAME
        )
        os.makedirs(self.profiles_dir, exist_ok=True)

    def normalize_profile_name(self, name: str) -> str:
        """Validate and normalize a user-visible profile name."""
        normalized = str(name or "").strip()
        if not normalized:
            raise ConfigProfileError("参数配置名称不能为空")
        if normalized in {".", ".."}:
            raise ConfigProfileError("参数配置名称无效")
        if _INVALID_NAME_RE.search(normalized):
            raise ConfigProfileError("参数配置名称不能包含文件名非法字符")
        if normalized.endswith("."):
            raise ConfigProfileError("参数配置名称不能以句点结尾")
        if len(normalized) > 80:
            raise ConfigProfileError("参数配置名称不能超过 80 个字符")
        return normalized

    def _profile_path(self, name: str) -> str:
        normalized = self.normalize_profile_name(name)
        return os.path.join(self.profiles_dir, f"{normalized}.json")

    @staticmethod
    def _as_dict(config: Any) -> Dict[str, Any]:
        if hasattr(config, "model_dump"):
            config = config.model_dump()
        if not isinstance(config, dict):
            raise ConfigProfileError("配置数据格式无效")
        return config

    def extract_profile(self, config: Any) -> Dict[str, Any]:
        """Extract profile-owned settings and remove secrets."""
        source = self._as_dict(config)
        profile: Dict[str, Any] = {}

        for key in PROFILE_SECTIONS:
            if key not in source:
                continue
            value = deepcopy(source[key])
            if key == "translator" and isinstance(value, dict):
                for sensitive_key in SENSITIVE_TRANSLATOR_FIELDS:
                    value.pop(sensitive_key, None)
            profile[key] = value

        app = source.get("app")
        if isinstance(app, dict):
            app_profile = {
                key: deepcopy(app[key])
                for key in PROFILE_APP_FIELDS
                if key in app
            }
            if app_profile:
                profile["app"] = app_profile

        return profile

    def merge_profile(
        self, current_config: Any, profile_settings: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge profile-owned settings into a config, keeping app state/secrets."""
        merged = deepcopy(self._as_dict(current_config))
        source = self._as_dict(profile_settings)

        def deep_update(target: Dict[str, Any], values: Dict[str, Any]) -> None:
            for key, value in values.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    deep_update(target[key], value)
                else:
                    target[key] = deepcopy(value)

        for key in PROFILE_SECTIONS:
            value = source.get(key)
            if key not in source:
                continue
            if key == "translator" and isinstance(value, dict):
                safe_value = {
                    field: field_value
                    for field, field_value in value.items()
                    if field not in SENSITIVE_TRANSLATOR_FIELDS
                }
                deep_update(merged.setdefault(key, {}), safe_value)
            elif key in source:
                merged[key] = deepcopy(value)

        app_profile = source.get("app")
        if isinstance(app_profile, dict):
            deep_update(merged.setdefault("app", {}), {
                key: value
                for key, value in app_profile.items()
                if key in PROFILE_APP_FIELDS
            })
        return merged

    def _read_profile_document(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as profile_file:
                document = json.load(profile_file)
        except FileNotFoundError as exc:
            raise ConfigProfileError("参数配置不存在") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigProfileError("参数配置文件无法读取") from exc

        if not isinstance(document, dict):
            raise ConfigProfileError("参数配置文件格式无效")
        settings = document.get("settings", document)
        if not isinstance(settings, dict):
            raise ConfigProfileError("参数配置文件格式无效")
        return settings

    def list_profiles(self) -> List[str]:
        """Return valid profile names, with ``default`` first."""
        try:
            entries = list(Path(self.profiles_dir).glob("*.json"))
        except OSError as exc:
            self.logger.warning("读取参数配置列表失败: %s", exc)
            return []

        names: List[str] = []
        for entry in entries:
            try:
                self.normalize_profile_name(entry.stem)
                self._read_profile_document(str(entry))
            except ConfigProfileError:
                self.logger.warning("忽略无效参数配置文件: %s", entry)
                continue
            names.append(entry.stem)

        return sorted(
            set(names),
            key=lambda value: (value != DEFAULT_PROFILE_NAME, value.casefold()),
        )

    def _write_profile(self, name: str, config: Any) -> str:
        normalized = self.normalize_profile_name(name)
        path = self._profile_path(normalized)
        payload = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "name": normalized,
            "settings": self.extract_profile(config),
        }

        temporary_path = None
        try:
            fd, temporary_path = tempfile.mkstemp(
                prefix=".parameter-profile-",
                suffix=".tmp",
                dir=self.profiles_dir,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as profile_file:
                json.dump(payload, profile_file, indent=2, ensure_ascii=False)
                profile_file.write("\n")
            os.replace(temporary_path, path)
            temporary_path = None
            return normalized
        except OSError as exc:
            raise ConfigProfileError(f"保存参数配置失败: {exc}") from exc
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def ensure_default_profile(self, config: Any) -> bool:
        """Create the initial default profile without overwriting user data."""
        path = self._profile_path(DEFAULT_PROFILE_NAME)
        if os.path.exists(path):
            return False
        self._write_profile(DEFAULT_PROFILE_NAME, config)
        return True

    def create_profile(
        self, name: str, config: Any, *, overwrite: bool = False
    ) -> str:
        normalized = self.normalize_profile_name(name)
        path = self._profile_path(normalized)
        if os.path.exists(path) and not overwrite:
            raise ConfigProfileError(f"参数配置已存在: {normalized}")
        return self._write_profile(normalized, config)

    def save_profile(self, name: str, config: Any) -> str:
        """Save the current settings into the active profile."""
        return self._write_profile(name, config)

    def load_profile(self, name: str) -> Dict[str, Any]:
        return self._read_profile_document(self._profile_path(name))

    def delete_profile(self, name: str) -> bool:
        normalized = self.normalize_profile_name(name)
        if normalized == DEFAULT_PROFILE_NAME:
            raise ConfigProfileProtectedError("默认参数配置不能删除")
        path = self._profile_path(normalized)
        if not os.path.exists(path):
            raise ConfigProfileError(f"参数配置不存在: {normalized}")
        try:
            os.remove(path)
        except OSError as exc:
            raise ConfigProfileError(f"删除参数配置失败: {exc}") from exc
        return True

    def rename_profile(self, old_name: str, new_name: str) -> str:
        old_normalized = self.normalize_profile_name(old_name)
        new_normalized = self.normalize_profile_name(new_name)
        if old_normalized == DEFAULT_PROFILE_NAME:
            raise ConfigProfileProtectedError("默认参数配置不能重命名")
        if new_normalized == DEFAULT_PROFILE_NAME:
            raise ConfigProfileProtectedError("不能使用 default 作为新参数配置名称")

        old_path = self._profile_path(old_normalized)
        new_path = self._profile_path(new_normalized)
        if not os.path.exists(old_path):
            raise ConfigProfileError(f"参数配置不存在: {old_normalized}")
        if os.path.exists(new_path):
            raise ConfigProfileError(f"参数配置已存在: {new_normalized}")

        try:
            document = self._read_profile_document(old_path)
            payload = {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "name": new_normalized,
                "settings": document,
            }
            self._write_document(new_path, payload)
            os.remove(old_path)
        except ConfigProfileError:
            raise
        except OSError as exc:
            raise ConfigProfileError(f"重命名参数配置失败: {exc}") from exc
        return new_normalized

    def _write_document(self, path: str, payload: Dict[str, Any]) -> None:
        temporary_path = None
        try:
            fd, temporary_path = tempfile.mkstemp(
                prefix=".parameter-profile-",
                suffix=".tmp",
                dir=self.profiles_dir,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as profile_file:
                json.dump(payload, profile_file, indent=2, ensure_ascii=False)
                profile_file.write("\n")
            os.replace(temporary_path, path)
            temporary_path = None
        except OSError as exc:
            raise ConfigProfileError(f"保存参数配置失败: {exc}") from exc
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
