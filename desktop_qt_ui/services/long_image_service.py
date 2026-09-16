"""Build a single vertically concatenated image from a completed image batch."""

from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps


SUPPORTED_IMAGE_EXTENSIONS = frozenset(
    {".avif", ".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)
_LONG_IMAGE_SUFFIX_RE = re.compile(r"(?:[_-](?:long|merged|combined))$", re.IGNORECASE)
_NATURAL_SORT_RE = re.compile(r"(\d+)")


@dataclass(frozen=True, slots=True)
class LongImageBuildResult:
    """Metadata for a successfully generated long image."""

    output_path: str
    source_paths: tuple[str, ...]
    width: int
    height: int
    resized_count: int


def _natural_sort_key(path: Path) -> tuple[object, ...]:
    parts: list[object] = []
    for part in _NATURAL_SORT_RE.split(path.name.lower()):
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)


def _is_generated_long_image(path: Path) -> bool:
    return bool(_LONG_IMAGE_SUFFIX_RE.search(path.stem))


def _resolve_image_paths(image_paths: Iterable[str | os.PathLike[str]]) -> list[Path]:
    """Return existing image files in natural filename order without duplicates."""
    resolved: dict[str, Path] = {}
    for raw_path in image_paths:
        path = Path(raw_path).expanduser()
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        if not path.is_file() or _is_generated_long_image(path):
            continue
        absolute = path.resolve()
        resolved[os.path.normcase(str(absolute))] = absolute
    return sorted(resolved.values(), key=_natural_sort_key)


def _default_output_path(source_paths: list[Path]) -> Path:
    """Derive a stable sibling PNG path from the common numbered page stem."""
    first_parent = source_paths[0].parent
    stripped_stems = [re.sub(r"(?:[_-])\d{1,6}$", "", path.stem) for path in source_paths]
    base_stem = stripped_stems[0] if stripped_stems and all(
        stem == stripped_stems[0] for stem in stripped_stems
    ) else "translated"
    return first_parent / f"{base_stem}_long.png"


def build_long_image(
    image_paths: Iterable[str | os.PathLike[str]],
    output_path: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
) -> LongImageBuildResult:
    """Concatenate result images from top to bottom into a PNG.

    The most common source width is used as the canvas width. Images with a
    different width are resized proportionally, which keeps a single manga
    chapter readable when a site includes a wider promotional page at the end.
    The output is replaced atomically, while all source files remain untouched.

    ``output_dir`` relocates the default output filename without changing its
    naming convention. It is useful when the source images live in a work
    subdirectory but the combined image belongs at the output root.
    """
    source_paths = _resolve_image_paths(image_paths)
    if not source_paths:
        raise ValueError("没有可拼接的图片结果")

    if output_path is not None:
        resolved_output = Path(output_path).expanduser().resolve()
    else:
        default_output = _default_output_path(source_paths)
        resolved_output = (
            Path(output_dir).expanduser().resolve() / default_output.name
            if output_dir is not None
            else default_output.resolve()
        )
    if resolved_output in source_paths:
        raise ValueError("长图输出路径不能覆盖源图片")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    loaded_images: list[Image.Image] = []
    prepared_images: list[Image.Image] = []
    canvas: Image.Image | None = None
    temporary_path: str | None = None
    try:
        for source_path in source_paths:
            with Image.open(source_path) as image:
                loaded_images.append(ImageOps.exif_transpose(image).convert("RGB"))

        target_width = Counter(image.width for image in loaded_images).most_common(1)[0][0]
        for image in loaded_images:
            if image.width == target_width:
                prepared_images.append(image)
                continue
            target_height = max(1, round(image.height * target_width / image.width))
            prepared_images.append(
                image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            )

        total_height = sum(image.height for image in prepared_images)
        canvas = Image.new("RGB", (target_width, total_height), "white")
        top = 0
        for image in prepared_images:
            canvas.paste(image, (0, top))
            top += image.height

        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{resolved_output.stem}.",
            suffix=".tmp",
            dir=str(resolved_output.parent),
        )
        os.close(fd)
        canvas.save(temporary_path, format="PNG")
        canvas.close()
        os.replace(temporary_path, resolved_output)
        temporary_path = None

        return LongImageBuildResult(
            output_path=str(resolved_output),
            source_paths=tuple(str(path) for path in source_paths),
            width=target_width,
            height=total_height,
            resized_count=sum(image.width != target_width for image in loaded_images),
        )
    finally:
        if temporary_path:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
        for image in prepared_images:
            if image not in loaded_images:
                image.close()
        if canvas is not None:
            canvas.close()
        for image in loaded_images:
            image.close()
