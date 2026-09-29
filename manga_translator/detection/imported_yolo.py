from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..utils import Quadrilateral
from ..utils.path_manager import find_yolo_label_path


def _coords_are_normalized(coords: np.ndarray) -> bool:
    if coords.size == 0:
        return False
    return float(np.min(coords)) >= 0.0 and float(np.max(coords)) <= 1.0


def _parse_bbox_line(values: np.ndarray, image_w: int, image_h: int) -> Tuple[np.ndarray, float]:
    cx, cy, box_w, box_h = values[1:5].astype(np.float32)
    prob = float(values[5]) if len(values) >= 6 else 1.0

    if _coords_are_normalized(values[1:5]):
        cx *= image_w
        cy *= image_h
        box_w *= image_w
        box_h *= image_h

    x1 = cx - box_w / 2.0
    y1 = cy - box_h / 2.0
    x2 = cx + box_w / 2.0
    y2 = cy + box_h / 2.0
    pts = np.array(
        [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        dtype=np.float32,
    )
    return pts, prob


def _parse_obb_line(values: np.ndarray, image_w: int, image_h: int) -> Tuple[np.ndarray, float]:
    coords = values[1:9].astype(np.float32)
    prob = float(values[9]) if len(values) >= 10 else 1.0
    pts = coords.reshape(4, 2)

    if _coords_are_normalized(coords):
        pts[:, 0] *= image_w
        pts[:, 1] *= image_h

    return pts.astype(np.float32), prob


def _parse_yolo_line(values: np.ndarray, image_w: int, image_h: int) -> Optional[Tuple[np.ndarray, float]]:
    if len(values) >= 9:
        return _parse_obb_line(values, image_w, image_h)
    if len(values) >= 5:
        return _parse_bbox_line(values, image_w, image_h)
    return None


def load_imported_yolo_textlines(
    image: np.ndarray,
    image_path: Optional[str],
    logger=None,
) -> List[Quadrilateral]:
    """
    读取固定目录中的 YOLO 标注文件，并转换为 Quadrilateral 列表。

    导入时忽略类别标签，所有框都进入前向流程。
    """
    if image is None or getattr(image, "size", 0) == 0 or not image_path:
        return []

    label_path = find_yolo_label_path(image_path)
    if not label_path:
        return []

    try:
        with open(label_path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except Exception as exc:
        if logger is not None:
            logger.warning(f"Failed to read YOLO annotations: {label_path} ({exc})")
        return []

    image_h, image_w = image.shape[:2]
    textlines: List[Quadrilateral] = []
    skipped_count = 0

    for line_no, raw_line in enumerate(raw_lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        try:
            values = np.array([float(part) for part in parts], dtype=np.float32)
        except ValueError:
            skipped_count += 1
            if logger is not None:
                logger.warning(f"Invalid YOLO annotation format; skipped: {label_path}:{line_no}")
            continue

        parsed = _parse_yolo_line(values, image_w=image_w, image_h=image_h)
        if parsed is None:
            skipped_count += 1
            continue

        pts, prob = parsed
        pts[:, 0] = np.clip(pts[:, 0], 0, image_w)
        pts[:, 1] = np.clip(pts[:, 1], 0, image_h)

        contour = np.round(pts).astype(np.int32)
        if cv2.contourArea(contour) <= 1.0:
            skipped_count += 1
            continue

        quad = Quadrilateral(contour, "", float(prob))
        quad.is_yolo_box = True
        quad.imported_yolo_box = True
        textlines.append(quad)

    if logger is not None:
        logger.info(
            f"Imported YOLO annotations: file={label_path}, boxes={len(textlines)}, skipped={skipped_count}"
        )

    return textlines


def build_mask_from_textlines(image_shape: Tuple[int, ...], textlines: List[Quadrilateral]) -> np.ndarray:
    """
    根据导入框生成兜底 mask_raw。
    """
    mask = np.zeros(image_shape[:2], dtype=np.uint8)
    polygons = []
    for textline in textlines:
        pts_raw = getattr(textline, "pts", None)
        if pts_raw is None:
            continue
        pts = np.asarray(pts_raw, dtype=np.int32)
        if pts.size == 0:
            continue
        polygons.append(pts.reshape((-1, 1, 2)))

    if polygons:
        cv2.fillPoly(mask, polygons, 255)
    return mask
