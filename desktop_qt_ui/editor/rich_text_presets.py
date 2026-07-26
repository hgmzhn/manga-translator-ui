"""Persistence for saved rich-text style presets.

The floating editor owns only the dialogs; loading, validation, and the
config-file round trip (with rollback on failure) live here.
"""

from __future__ import annotations

import copy

from .rich_text_editing import normalize_text_style


def normalize_rich_text_preset(payload: object) -> dict | None:
    """Validate one preset payload; ``None`` when empty or malformed."""
    if not isinstance(payload, dict):
        return None
    try:
        style = normalize_text_style(payload.get("style") or {})
    except (TypeError, ValueError):
        return None
    ruby = payload.get("ruby", "")
    if not isinstance(ruby, str):
        return None
    tcy = bool(payload.get("tcy", False))
    if not style and not ruby and not tcy:
        return None
    return {
        "style": style,
        "ruby": ruby,
        "tcy": tcy,
    }


class RichTextPresetStore:
    """Load/save ``app.saved_rich_text_presets`` (in-memory when no config)."""

    def __init__(self, config_service):
        self._config_service = config_service
        self._memory: dict[str, dict] = {}

    def load(self) -> dict[str, dict]:
        if self._config_service is None:
            return copy.deepcopy(self._memory)
        config_ref = self._config_service.get_config_reference()
        raw = getattr(getattr(config_ref, "app", None), "saved_rich_text_presets", None)
        if not isinstance(raw, dict):
            return {}
        presets: dict[str, dict] = {}
        for name, payload in raw.items():
            normalized = normalize_rich_text_preset(payload)
            clean_name = str(name).strip()
            if clean_name and normalized is not None:
                presets[clean_name] = normalized
        return presets

    def save_all(self, presets: dict[str, dict], previous: dict[str, dict]) -> bool:
        """Persist the full preset dict; roll back to ``previous`` on failure."""
        if self._config_service is None:
            self._memory = copy.deepcopy(presets)
            return True
        config_ref = self._config_service.get_config_reference()
        config_ref.app.saved_rich_text_presets = copy.deepcopy(presets) or None
        if self._config_service.save_config_file():
            return True
        config_ref.app.saved_rich_text_presets = copy.deepcopy(previous) or None
        return False
