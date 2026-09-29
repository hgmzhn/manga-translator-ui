"""PicLite-style measured compression for model-request image copies.

Policy adapted from amiaoapp/PicLite (GPL-3.0), revision
a346f0f805690a06350ee2c6f1f56095f565d9de: app/compression-policy.ts,
app/piclite-app.tsx and src-tauri/src/lib.rs. Encoders use Pillow, not Rust or
browser canvas, so output bytes differ. See IMAGE_COMPRESSION.md.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence

from PIL import Image, ImageOps, features

from ..domain.chat import ChatImage
from .limits import (
    MAX_IMAGE_BATCH_BYTES, MAX_IMAGE_BYTES, MAX_IMAGE_DIMENSION, MIN_IMAGE_DIMENSION,
)

# The selected Small preset, with the screenshot's 100% scale. Only a transport
# limit may reduce dimensions; never modify the host's original attachment.
IMAGE_QUALITY = 45
MIN_IMAGE_QUALITY = 38
_MAX_PASSES = 9


def _encode_png(image: Image.Image, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=9)
    lossless = output.getvalue()
    # PNG quality means palette size, following PicLite's small/balanced modes.
    # Also measure true-colour PNG: palette overhead can enlarge simple artwork.
    colors = round(64 + 192 * (quality / 100) ** 1.35)
    method = Image.Quantize.FASTOCTREE if image.mode == "RGBA" else Image.Quantize.MEDIANCUT
    palette = image.quantize(colors=colors, method=method, dither=Image.Dither.NONE)
    output = io.BytesIO()
    palette.save(output, format="PNG", optimize=True, compress_level=9)
    return min((lossless, output.getvalue()), key=len)


def _best_encoding(image: Image.Image, quality: int) -> ChatImage:
    """Measure real encodes; transparent pixels exclude JPEG, not opaque alpha."""
    options = [ChatImage(_encode_png(image, quality), "image/png")]
    if features.check("webp") and max(image.size) <= 16383:
        # Some platform Pillow builds lack WebP. PNG/JPEG remain available.
        output = io.BytesIO()
        try:
            image.save(output, format="WEBP", quality=quality, method=4)
        except OSError:
            pass
        else:
            options.append(ChatImage(output.getvalue(), "image/webp"))
    if image.mode != "RGBA":
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True,
                   progressive=True, subsampling=0)
        options.append(ChatImage(output.getvalue(), "image/jpeg"))
    return min(options, key=lambda value: len(value.data))


def _pixels(source: Image.Image) -> Image.Image:
    oriented = ImageOps.exif_transpose(source)
    rgba = oriented.convert("RGBA")
    transparent = rgba.getchannel("A").getextrema()[0] < 255
    pixels = rgba if transparent else rgba.convert("RGB")
    pixels.info.clear()
    return pixels


def _resize(image: Image.Image, dimension: int) -> Image.Image:
    longest = max(image.size)
    if longest <= dimension:
        return image
    size = (max(1, round(image.width * dimension / longest)),
            max(1, round(image.height * dimension / longest)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _prefer_original(original: ChatImage, candidate: ChatImage) -> ChatImage:
    # PicLite's small-mode guard avoids lossy changes for negligible savings.
    minimum_savings = max(128, math.ceil(len(original.data) * 0.02))
    if len(original.data) - len(candidate.data) < minimum_savings:
        return original
    return candidate


def compress_chat_image(image: ChatImage) -> ChatImage:
    """Choose the smallest useful 45%-quality encoding at the source dimensions.

    Animated images are kept intact, never silently replaced by their first
    frame. This helper is for standalone use; Agent calls fit_model_images at
    the request boundary, avoiding repeated lossy compression in UI/services.
    """
    try:
        with Image.open(io.BytesIO(image.data)) as source:
            if getattr(source, "is_animated", False):
                return image
            candidate = _best_encoding(_pixels(source), IMAGE_QUALITY)
    except (OSError, ValueError, Image.DecompressionBombError):
        return image
    return _prefer_original(image, candidate)


def fit_model_image(image: ChatImage, *, max_bytes: int,
                    max_dimension: int = MAX_IMAGE_DIMENSION,
                    min_dimension: int = MIN_IMAGE_DIMENSION,
                    optimize: bool = True) -> ChatImage:
    """Optimize a wire copy, lowering quality then dimensions to meet a hard cap.

    Every trial starts from decoded original pixels. The adaptive size search
    follows PicLite, with a longest-edge floor for manga and a strict failure
    when no candidate fits (PicLite itself can return an over-budget result).
    """
    if max_bytes <= 0 or min_dimension <= 0 or max_dimension < min_dimension:
        raise ValueError("invalid model image budget")
    try:
        with Image.open(io.BytesIO(image.data)) as source:
            within_budget = len(image.data) <= max_bytes and max(source.size) <= max_dimension
            if getattr(source, "is_animated", False):
                if within_budget:
                    return image
                raise ValueError("Animated image exceeds the model image budget; animation was preserved")
            if within_budget and not optimize:
                return image
            pixels = _pixels(source)
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("Cannot decode model image") from error

    dimension = min(max(pixels.size), max_dimension)
    minimum = min(max(pixels.size), min_dimension)
    quality = IMAGE_QUALITY
    candidate_pixels = _resize(pixels, dimension)
    for _ in range(_MAX_PASSES):
        candidate = _best_encoding(candidate_pixels, quality)
        if len(candidate.data) <= max_bytes:
            return _prefer_original(image, candidate) if within_budget else candidate
        if within_budget:
            return image
        ratio = max_bytes / len(candidate.data)
        if quality > MIN_IMAGE_QUALITY:
            quality = max(MIN_IMAGE_QUALITY, math.floor(quality * max(0.58, min(0.86, ratio * 1.15))))
        elif dimension > minimum:
            reduction = max(0.42, min(0.86, math.sqrt(ratio) * 0.96))
            dimension = max(minimum, math.floor(dimension * reduction))
            candidate_pixels = _resize(pixels, dimension)
            quality = IMAGE_QUALITY
        else:
            break
    else:
        # Always check the exact floor used by batch reservation, even when
        # the bounded adaptive search exhausted its passes before reaching it.
        candidate = _best_encoding(_resize(pixels, minimum), MIN_IMAGE_QUALITY)
        if len(candidate.data) <= max_bytes:
            return candidate
    raise ValueError(
        f"Image cannot fit {max_bytes} bytes without going below "
        f"{minimum}px longest edge or encoding quality {MIN_IMAGE_QUALITY}"
    )


def _minimum_model_image(image: ChatImage, min_dimension: int) -> ChatImage:
    """Measure the reservation at the same resolution/quality floor as fitting."""
    with Image.open(io.BytesIO(image.data)) as source:
        if getattr(source, "is_animated", False):
            return image
        pixels = _pixels(source)
    candidate = _best_encoding(_resize(pixels, min_dimension), MIN_IMAGE_QUALITY)
    if max(pixels.size) <= min_dimension and len(image.data) <= len(candidate.data):
        return image
    return candidate


def fit_model_images(images: Sequence[ChatImage], *, max_total_bytes: int = MAX_IMAGE_BATCH_BYTES,
                     max_image_bytes: int = MAX_IMAGE_BYTES,
                     max_dimension: int = MAX_IMAGE_DIMENSION,
                     min_dimension: int = MIN_IMAGE_DIMENSION,
                     optimize: bool = True) -> list[ChatImage]:
    """Fit all images by measured raw bytes without equal per-image quotas.

    Normal model requests optimize even below the byte cap. Tool preflight may
    use optimize=False to skip encodes when originals already fit; it discards
    the returned copies. Base64 and text are budgeted by ModelImageBudget.
    """
    if (max_total_bytes <= 0 or max_image_bytes <= 0 or min_dimension <= 0
            or max_dimension < min_dimension):
        raise ValueError("invalid model image batch budget")
    images = list(images)
    fitted = [fit_model_image(
        image, max_bytes=min(max_image_bytes, max_total_bytes),
        max_dimension=max_dimension, min_dimension=min_dimension, optimize=optimize,
    ) for image in images]
    if sum(len(image.data) for image in fitted) <= max_total_bytes:
        return fitted

    minimums = []
    for image, candidate in zip(images, fitted):
        minimum = _minimum_model_image(image, min_dimension)
        # Encoders are not strictly monotone in quality/size. An already valid
        # smaller candidate is a better reservation than the nominal floor.
        minimums.append(min((minimum, candidate), key=lambda value: len(value.data)))
    required = sum(len(image.data) for image in minimums)
    if required > max_total_bytes or any(len(image.data) > max_image_bytes for image in minimums):
        raise ValueError(
            f"{len(images)} images need {required} raw bytes at the supported "
            f"{min_dimension}px/quality {MIN_IMAGE_QUALITY} floor; batch budget is {max_total_bytes} bytes. "
            "Request one page and one image view, or a smaller crop."
        )
    result = []
    remaining = max_total_bytes
    minimum_rest = required
    for image, candidate, minimum in zip(images, fitted, minimums):
        minimum_rest -= len(minimum.data)
        allowance = min(max_image_bytes, remaining - minimum_rest)
        if len(candidate.data) > allowance:
            try:
                candidate = fit_model_image(image, max_bytes=allowance, max_dimension=max_dimension,
                                            min_dimension=min_dimension)
            except ValueError:
                candidate = minimum
        result.append(candidate)
        remaining -= len(candidate.data)
    return result
