"""Helpers for mapping an editor text region back to image-mask pixels."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .document_state import normalize_binary_mask


def normalize_mask_for_shape(mask: Any, shape: tuple[int, int]) -> np.ndarray:
    """Return a binary mask at ``shape``, reusing canonical model data when possible."""
    normalized = normalize_binary_mask(mask)
    if normalized is None:
        return np.zeros(shape, dtype=np.uint8)
    if normalized.shape == shape:
        return normalized
    resized = cv2.resize(
        normalized,
        (shape[1], shape[0]),
        interpolation=cv2.INTER_NEAREST,
    )
    return np.where(resized > 0, 255, 0).astype(np.uint8)


def build_region_mask(
    region_data: dict[str, Any], shape: tuple[int, int], expand_px: int = 0
) -> np.ndarray:
    """Build a binary image mask for the source polygons in one text region."""
    height, width = (int(shape[0]), int(shape[1]))
    result = np.zeros((max(0, height), max(0, width)), dtype=np.uint8)
    if result.size == 0 or not isinstance(region_data, dict):
        return result

    lines = region_data.get("lines", [])
    try:
        lines_array = np.asarray(lines, dtype=np.float32)
    except (TypeError, ValueError):
        return result

    if lines_array.ndim == 2:
        lines_array = lines_array[None, ...]
    if lines_array.ndim != 3 or lines_array.shape[2] != 2:
        return result

    polygons = []
    for line in lines_array:
        if line.shape[0] < 3 or not np.all(np.isfinite(line)):
            continue
        points = np.rint(line).astype(np.int32)
        points[:, 0] = np.clip(points[:, 0], 0, max(width - 1, 0))
        points[:, 1] = np.clip(points[:, 1], 0, max(height - 1, 0))
        if len(np.unique(points, axis=0)) >= 3:
            polygons.append(points)

    if not polygons:
        return result
    cv2.fillPoly(result, polygons, 255)

    expand_px = max(0, int(expand_px))
    if expand_px:
        kernel_size = expand_px * 2 + 1
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (kernel_size, kernel_size),
        )
        result = cv2.dilate(result, kernel)
    return result


def remove_region_from_mask(
    mask: Any, region_data: dict[str, Any], expand_px: int = 0
) -> np.ndarray | None:
    """Return ``mask`` with pixels belonging to ``region_data`` cleared."""
    if mask is None:
        return None
    try:
        normalized = normalize_binary_mask(mask)
    except ValueError:
        return None
    region_mask = build_region_mask(region_data, normalized.shape, expand_px)
    return cv2.bitwise_and(normalized, cv2.bitwise_not(region_mask))
