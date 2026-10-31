"""Server-side result directory export service.

Exports translated images directly to the server's ``result/`` directory
(typically bind-mounted to a host folder in Docker deployments) with
permission checks and strict relative-path sanitization.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from manga_translator.image_formats import (
    QUALITY_PIL_FORMATS,
    RGB_PIL_FORMATS,
    resolve_output_image_format,
)
from manga_translator.runtime_paths import get_application_dir
from manga_translator.server.core.download_ticket_service import resolve_path_within
from manga_translator.utils.image_modes import normalize_rgb_image

logger = logging.getLogger("manga_translator.server")

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_INVALID_SEGMENT_CHARS_RE = re.compile(r'[<>:"|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_MAX_SEGMENT_BYTES = 200


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Safely truncate text so its UTF-8 encoding does not exceed max_bytes."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def get_server_result_root(result_root: str | Path | None = None) -> Path:
    """Return the resolved server ``result/`` directory and ensure it exists."""
    target = Path(result_root) if result_root is not None else Path(get_application_dir()) / "result"
    target.mkdir(parents=True, exist_ok=True)
    return Path(os.path.realpath(os.fspath(target)))


def check_save_to_server_result_permission(
    username: Optional[str],
    admin_settings: Optional[dict] = None,
) -> bool:
    """Check whether *username* is allowed to write translated outputs to ``result/``."""
    settings = admin_settings or {}
    global_permissions = settings.get("permissions")
    default_allowed = bool(
        global_permissions.get("can_save_to_server_result", False)
        if isinstance(global_permissions, dict)
        else False
    )

    if not username or username in {"unknown", "anonymous"}:
        return default_allowed

    try:
        from manga_translator.server.core.group_management_service import (
            get_group_management_service,
        )
        from manga_translator.server.core.middleware import get_services

        account_service, _, _ = get_services()
        if account_service is None:
            return default_allowed

        account = account_service.get_user(username)
        if not account:
            return default_allowed

        if getattr(account, "role", None) == "admin":
            return True

        user_param_config = getattr(account, "parameter_config", None) or {}
        if isinstance(user_param_config, dict):
            user_permissions = user_param_config.get("permissions")
            if isinstance(user_permissions, dict) and "can_save_to_server_result" in user_permissions:
                return bool(user_permissions["can_save_to_server_result"])

        group = get_group_management_service().get_group(account.group)
        if isinstance(group, dict):
            group_param_config = group.get("parameter_config") or {}
            if isinstance(group_param_config, dict):
                group_permissions = group_param_config.get("permissions")
                if isinstance(group_permissions, dict) and "can_save_to_server_result" in group_permissions:
                    return bool(group_permissions["can_save_to_server_result"])

        if getattr(account, "group", None) == "admin":
            return True
    except Exception as exc:
        logger.warning("Failed to resolve server result export permission for %s: %s", username, exc)

    return default_allowed


def _sanitize_segment(raw_segment: str) -> str:
    cleaned = _INVALID_SEGMENT_CHARS_RE.sub("_", raw_segment).strip().rstrip(".")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("Invalid path segment")

    stem, ext = os.path.splitext(cleaned)
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
        cleaned = f"{stem}{ext}"

    # Linux / Docker ext4 restricts a single directory/file entry to 255 bytes (NAME_MAX).
    # Multi-byte UTF-8 sequences (such as Chinese characters) can easily exceed 255 bytes
    # even with fewer than 90 characters. We truncate by UTF-8 encoded bytes while
    # leaving adequate space for the file extension.
    ext_bytes = len(ext.encode("utf-8"))
    if ext_bytes >= _MAX_SEGMENT_BYTES:
        ext = _truncate_utf8(ext, 20)
        ext_bytes = len(ext.encode("utf-8"))

    max_stem_bytes = max(1, _MAX_SEGMENT_BYTES - ext_bytes)
    stem_bytes = len(stem.encode("utf-8"))
    if stem_bytes > max_stem_bytes:
        # Use a deterministic short hash suffix of the original stem to distinguish
        # different filenames/folders that would otherwise collide when truncated.
        suffix_hash = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:8]
        suffix = f"_{suffix_hash}"
        suffix_bytes = len(suffix.encode("utf-8"))

        if max_stem_bytes > suffix_bytes:
            budget = max_stem_bytes - suffix_bytes
            stem = f"{_truncate_utf8(stem, budget).rstrip('.')}{suffix}"
        else:
            stem = suffix[:max_stem_bytes]

        if not stem:
            stem = "_"
        cleaned = f"{stem}{ext}"
    else:
        cleaned = f"{stem}{ext}"

    return cleaned


def sanitize_relative_output_path(
    raw_path: Optional[str],
    *,
    allow_empty: bool = True,
) -> list[str]:
    """Validate and sanitize a user-supplied relative folder or file path.

    Rejects absolute paths, drive-qualified paths, null bytes, and ``..``
    traversal segments by raising :class:`ValueError`.
    """
    if raw_path is None:
        if allow_empty:
            return []
        raise ValueError("Output path cannot be empty")

    text = str(raw_path)
    if "\x00" in text:
        raise ValueError("Invalid null byte in output path")

    normalized = text.replace("\\", "/").strip()
    if not normalized:
        if allow_empty:
            return []
        raise ValueError("Output path cannot be empty")

    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized):
        raise ValueError("Absolute output paths are not allowed")

    segments: list[str] = []
    for part in normalized.split("/"):
        token = part.strip()
        if not token or token == ".":
            continue
        if token == "..":
            raise ValueError("Path traversal is not allowed in output path")
        segments.append(_sanitize_segment(token))

    if not segments and not allow_empty:
        raise ValueError("Output path cannot be empty")

    return segments


def build_archive_entry_name(
    original_path: Optional[str],
    output_format: Optional[str] = None,
    *,
    fallback_stem: str = "translated",
) -> str:
    """Build a safe relative archive entry path preserving subfolder hierarchy."""
    try:
        parts = sanitize_relative_output_path(original_path, allow_empty=True)
    except ValueError:
        parts = []

    if parts:
        dir_parts = parts[:-1]
        raw_filename = parts[-1]
        stem = os.path.splitext(raw_filename)[0] or fallback_stem
    else:
        dir_parts = []
        raw_filename = None
        stem = fallback_stem

    _, ext = resolve_output_image_format(output_format, original_path=raw_filename)
    return "/".join([*dir_parts, f"{stem}{ext}"])


def resolve_server_result_file_path(
    *,
    source_path: Optional[str] = None,
    output_folder: Optional[str] = None,
    config=None,
    result_root: str | Path | None = None,
    fallback_stem: Optional[str] = None,
    overwrite: bool = True,
) -> tuple[Path, Path, str]:
    """Resolve the destination file path inside ``result_root``.

    Returns ``(root_dir, target_file_path, pil_save_format)``.
    """
    root_dir = get_server_result_root(result_root)
    folder_parts = sanitize_relative_output_path(output_folder, allow_empty=True)
    source_parts = sanitize_relative_output_path(source_path, allow_empty=True)

    if source_parts:
        rel_dir_parts = source_parts[:-1]
        raw_filename = source_parts[-1]
        stem = os.path.splitext(raw_filename)[0] or (fallback_stem or "translated")
    else:
        rel_dir_parts = []
        raw_filename = None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        stem = fallback_stem or f"translated_{timestamp}"

    requested_format = None
    if config is not None and hasattr(config, "cli") and hasattr(config.cli, "format"):
        fmt = config.cli.format
        if fmt and fmt != "不指定":
            requested_format = str(fmt).lower()

    save_format, ext = resolve_output_image_format(requested_format, original_path=raw_filename)

    rel_dir = [*folder_parts, *rel_dir_parts]
    target_dir = root_dir.joinpath(*rel_dir) if rel_dir else root_dir
    if rel_dir:
        resolve_path_within(root_dir, target_dir)

    candidate = target_dir / f"{stem}{ext}"
    resolve_path_within(root_dir, candidate)

    if not overwrite and candidate.exists():
        suffix_index = 1
        ext_bytes = len(ext.encode("utf-8"))
        while True:
            num_suffix = f"_{suffix_index}"
            num_suffix_bytes = len(num_suffix.encode("utf-8"))
            max_num_stem_bytes = max(1, _MAX_SEGMENT_BYTES - ext_bytes - num_suffix_bytes)
            num_stem = _truncate_utf8(stem, max_num_stem_bytes).rstrip(".")
            if not num_stem:
                num_stem = "_"
            numbered_name = f"{num_stem}{num_suffix}{ext}"
            numbered = target_dir / numbered_name
            resolve_path_within(root_dir, numbered)
            if not numbered.exists():
                candidate = numbered
                break
            suffix_index += 1

    return root_dir, candidate, save_format


def save_image_to_server_result(
    image,
    *,
    username: Optional[str],
    admin_settings: Optional[dict] = None,
    config=None,
    source_path: Optional[str] = None,
    output_folder: Optional[str] = None,
    result_root: str | Path | None = None,
    fallback_stem: Optional[str] = None,
    overwrite: bool = True,
) -> str:
    """Save a PIL image into the server ``result/`` directory.

    Returns the saved path relative to ``result/`` using forward slashes
    (e.g. ``"Chapter01/001.png"``), avoiding leaking host absolute paths.
    """
    if not check_save_to_server_result_permission(username, admin_settings):
        raise PermissionError("User does not have permission to save to server result directory")

    if image is None or not hasattr(image, "save"):
        raise ValueError("Result image is not a valid PIL Image")

    root_dir, target_path, save_format = resolve_server_result_file_path(
        source_path=source_path,
        output_folder=output_folder,
        config=config,
        result_root=result_root,
        fallback_stem=fallback_stem,
        overwrite=overwrite,
    )

    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        img_to_save = image.copy()
    except Exception:
        img_to_save = image

    try:
        if save_format in RGB_PIL_FORMATS and img_to_save.mode not in ("RGB", "L"):
            converted = normalize_rgb_image(img_to_save)
            if converted is not img_to_save and img_to_save is not image:
                try:
                    img_to_save.close()
                except Exception:
                    pass
            img_to_save = converted

        save_kwargs = {}
        if save_format in QUALITY_PIL_FORMATS:
            quality = 100
            if config is not None and hasattr(config, "cli") and hasattr(config.cli, "save_quality"):
                raw_quality = getattr(config.cli, "save_quality", 100)
                if isinstance(raw_quality, (int, float)):
                    quality = max(1, min(100, int(raw_quality)))
            save_kwargs["quality"] = quality

        img_to_save.save(target_path, format=save_format, **save_kwargs)
    finally:
        if img_to_save is not image:
            try:
                img_to_save.close()
            except Exception:
                pass

    return target_path.relative_to(root_dir).as_posix()
