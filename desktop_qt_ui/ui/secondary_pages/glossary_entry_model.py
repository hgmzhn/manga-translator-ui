"""Pure data helpers for glossary entries used by the prompt editor."""

from __future__ import annotations

from typing import Any, Dict, List


_ENTRY_FIELDS = {
    "original",
    "translation",
    "condition",
    "aliases",
    # Unreleased intermediate glossary shapes are intentionally not migrated.
    "translations",
    "nicknames",
    "overwrite",
    "description",
    "note",
    "notes",
    "comment",
    "introduction",
    "intro",
}
_ALIAS_FIELDS = {"original", "translations", "condition", "translation"}
_TRANSLATION_FIELDS = {"text", "condition"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalized_translation(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        text = _text(value.get("text", ""))
        condition = _text(value.get("condition", ""))
        extras = dict(value.get("_extras", {})) if isinstance(value.get("_extras"), dict) else {}
        extras.update(
            {
                key: item
                for key, item in value.items()
                if key not in _TRANSLATION_FIELDS and key != "_extras"
            }
        )
    else:
        text = _text(value)
        condition = ""
        extras = {}
    return {"text": text, "condition": condition, "_extras": extras}


def _normalized_alias(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        value = {}

    raw_translations = value.get("translations", [])
    translations = (
        [_normalized_translation(item) for item in raw_translations]
        if isinstance(raw_translations, list)
        else []
    )
    extras = dict(value.get("_extras", {})) if isinstance(value.get("_extras"), dict) else {}
    extras.update(
        {
            key: item
            for key, item in value.items()
            if key not in _ALIAS_FIELDS and key != "_extras"
        }
    )
    return {
        "original": _text(value.get("original", "")),
        "translations": translations,
        "_extras": extras,
    }


def normalize_glossary_entry(entry: Any) -> Dict[str, Any]:
    """Return one entry in the canonical ``aliases[].translations[]`` shape.

    The only legacy format intentionally supported is the original simple
    ``original`` + ``translation`` mapping.
    """
    if not isinstance(entry, dict):
        entry = {}

    original = _text(entry.get("original", ""))
    raw_aliases = entry.get("aliases")
    aliases = (
        [_normalized_alias(item) for item in raw_aliases if isinstance(item, dict)]
        if isinstance(raw_aliases, list)
        else []
    )

    if not aliases and ("translation" in entry or "condition" in entry):
        legacy_translation = _normalized_translation(
            {
                "text": entry.get("translation", ""),
                "condition": entry.get("condition", ""),
            }
        )
        aliases = [
            {
                "original": original,
                "translations": [legacy_translation],
                "_extras": {},
            }
        ]

    description_key = next(
        (
            key
            for key in ("description", "note", "notes", "comment", "introduction", "intro")
            if key in entry
        ),
        "description",
    )
    overwrite = entry.get("overwrite")

    return {
        "original": original,
        "aliases": aliases,
        "description": _text(entry.get(description_key, "")),
        "overwrite": overwrite if isinstance(overwrite, bool) else None,
        "_has_overwrite": isinstance(overwrite, bool),
        "_description_key": description_key,
        "_extras": {key: value for key, value in entry.items() if key not in _ENTRY_FIELDS},
    }


def _translation_to_data(translation: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalized_translation(translation)
    data = dict(normalized.get("_extras", {}))
    data["text"] = normalized["text"]
    if normalized["condition"]:
        data["condition"] = normalized["condition"]
    return data


def _alias_to_data(alias: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalized_alias(alias)
    data = dict(normalized.get("_extras", {}))
    data["original"] = normalized["original"]
    data["translations"] = [
        _translation_to_data(item)
        for item in normalized["translations"]
        if item.get("text") or item.get("condition") or item.get("_extras")
    ]
    return data


def serialize_glossary_entry(entry: Any) -> Dict[str, Any]:
    """Serialize an entry in the unified glossary shape."""
    normalized = (
        entry
        if isinstance(entry, dict) and "_has_overwrite" in entry
        else normalize_glossary_entry(entry)
    )

    data = dict(normalized.get("_extras", {}))
    original = _text(normalized.get("original", ""))
    data["original"] = original

    aliases = [
        _normalized_alias(item)
        for item in normalized.get("aliases", [])
        if isinstance(item, dict)
    ]
    aliases = [
        item
        for item in aliases
        if item["original"] or item["translations"] or item.get("_extras")
    ]
    if not aliases and original:
        aliases = [{"original": original, "translations": [], "_extras": {}}]
    data["aliases"] = [_alias_to_data(item) for item in aliases]

    description = _text(normalized.get("description", ""))
    if description:
        data[normalized.get("_description_key", "description")] = description

    if normalized.get("_has_overwrite") and isinstance(normalized.get("overwrite"), bool):
        data["overwrite"] = normalized["overwrite"]

    return data


def alias_rows(entry: Any) -> List[Dict[str, str]]:
    """Flatten aliases for the editor table while retaining translation branches."""
    normalized = (
        entry
        if isinstance(entry, dict) and "_has_overwrite" in entry
        else normalize_glossary_entry(entry)
    )
    rows: List[Dict[str, str]] = []
    for raw_alias in normalized.get("aliases", []):
        alias = _normalized_alias(raw_alias)
        if alias["translations"]:
            for raw_translation in alias["translations"]:
                translation = _normalized_translation(raw_translation)
                rows.append(
                    {
                        "original": alias["original"],
                        "text": translation["text"],
                        "condition": translation["condition"],
                    }
                )
        elif alias["original"]:
            rows.append({"original": alias["original"], "text": "", "condition": ""})
    return rows


def aliases_from_rows(rows: List[Dict[str, Any]], canonical_original: str) -> List[Dict[str, Any]]:
    """Group editor rows by alias original, preserving first-seen order."""
    aliases: List[Dict[str, Any]] = []
    alias_indexes: Dict[str, int] = {}

    for row in rows:
        alias_original = _text(row.get("original", ""))
        translation_text = _text(row.get("text", ""))
        condition = _text(row.get("condition", ""))
        if not alias_original and not translation_text and not condition:
            continue
        if not alias_original:
            alias_original = _text(canonical_original)

        key = alias_original
        if key not in alias_indexes:
            alias_indexes[key] = len(aliases)
            aliases.append(
                {"original": alias_original, "translations": [], "_extras": {}}
            )

        if translation_text or condition:
            aliases[alias_indexes[key]]["translations"].append(
                {
                    "text": translation_text,
                    "condition": condition,
                    "_extras": {},
                }
            )

    canonical_original = _text(canonical_original)
    if not aliases and canonical_original:
        aliases.append(
            {"original": canonical_original, "translations": [], "_extras": {}}
        )
    return aliases


def alias_summary(entry: Any) -> str:
    parts = []
    for row in alias_rows(entry):
        alias = row["original"]
        translation = row["text"]
        condition = row["condition"]
        if translation and condition:
            parts.append(f"{alias} -> {translation} ({condition})")
        elif translation:
            parts.append(f"{alias} -> {translation}")
        elif alias:
            parts.append(alias)
    return " / ".join(parts)
