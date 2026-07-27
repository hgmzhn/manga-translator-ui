"""Single source of truth for supported image formats."""

import os


# Dict order is the preferred UI/search order.
PIL_FORMAT_BY_EXTENSION = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".jfif": "JPEG",
    ".webp": "WEBP",
    ".avif": "AVIF",
    ".bmp": "BMP",
    ".tiff": "TIFF",
    ".tif": "TIFF",
    ".heic": "HEIF",
    ".heif": "HEIF",
}

SUPPORTED_IMAGE_EXTENSIONS = tuple(PIL_FORMAT_BY_EXTENSION)
OUTPUT_IMAGE_FORMATS = tuple(
    extension.removeprefix(".")
    for extension in SUPPORTED_IMAGE_EXTENSIONS
)
IMAGE_FILE_GLOB_PATTERNS = tuple(
    f"*{extension}" for extension in SUPPORTED_IMAGE_EXTENSIONS
)
IMAGE_FILE_DIALOG_PATTERNS = " ".join(IMAGE_FILE_GLOB_PATTERNS)
IMAGE_FILE_DIALOG_FILTER = f"Image Files ({IMAGE_FILE_DIALOG_PATTERNS})"
QUALITY_PIL_FORMATS = frozenset({"JPEG", "WEBP", "AVIF", "HEIF"})
RGB_PIL_FORMATS = frozenset({"JPEG", "BMP"})


def _resolve_extension(value: object) -> str | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    extension = token if token in PIL_FORMAT_BY_EXTENSION else os.path.splitext(token)[1] or f".{token}"
    return extension if extension in PIL_FORMAT_BY_EXTENSION else None


def resolve_pil_image_format(value: object) -> str:
    extension = _resolve_extension(value)
    if extension is None:
        raise ValueError(f"Unsupported image format: {value!r}")
    return PIL_FORMAT_BY_EXTENSION[extension]


def resolve_output_image_format(
    requested_format: object = None,
    *,
    original_path: os.PathLike[str] | str | None = None,
    default_format: str = "png",
) -> tuple[str, str]:
    """Return ``(Pillow encoder, filename extension)`` for an export."""
    token = str(requested_format or "").strip().lower()
    extension = None if token in {"", "none", "不指定"} else _resolve_extension(token)
    if token not in {"", "none", "不指定"} and extension is None:
        raise ValueError(f"Unsupported image format: {requested_format!r}")
    if extension is None and original_path is not None:
        extension = _resolve_extension(os.fspath(original_path))
    if extension is None:
        extension = _resolve_extension(default_format)
    if extension is None:
        raise ValueError(f"Unsupported default image format: {default_format!r}")
    return PIL_FORMAT_BY_EXTENSION[extension], extension
