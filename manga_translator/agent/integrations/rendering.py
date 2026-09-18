"""Headless snapshot adapter for manga_translator.rendering, with dirty-region reuse."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import multiprocessing
import os
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache, partial
from time import perf_counter

from .project import MAX_ASSET_BYTES, MAX_IMAGE_PIXELS

MAX_OUTPUT_BYTES = 12 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_TEXT_LENGTH = 100_000
MAX_RENDER_CACHE_ENTRIES = 4
MAX_RENDER_CACHE_BYTES = 64 * 1024 * 1024
_worker_app = None
_render_cache = OrderedDict()
_render_cache_bytes = 0
RENDER_PROTOCOL = 2


def _error(code: str, message: str, details=None):
    from ..domain.tool_models import ToolError

    return ToolError(code, message, details)


def _fingerprint(value) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=repr
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _render_identity(snapshot: dict) -> str:
    return _fingerprint(
        {
            "protocol": RENDER_PROTOCOL,
            "width": snapshot.get("width"),
            "height": snapshot.get("height"),
            "base_status": snapshot.get("base_status"),
            "asset_fingerprints": snapshot.get("_asset_fingerprints", {}),
            "project_layers": snapshot.get("_project_layers", {}),
            "render_config": snapshot.get("_render_config", {}),
            "assets": {key: snapshot.get(key) for key in ("base_asset", "paint_asset")},
        }
    )


def _region_fingerprint(region: dict) -> str:
    return _fingerprint({key: value for key, value in region.items() if key != "version"})


def _points_bounds(points, width: int, height: int):
    import numpy as np

    if points is None:
        return None
    vertices = np.asarray(points).reshape(-1, 2)
    if not len(vertices) or not np.isfinite(vertices).all():
        return None
    left, top = np.maximum(np.floor(vertices.min(axis=0)).astype(int) - 2, 0)
    right, bottom = np.minimum(
        np.ceil(vertices.max(axis=0)).astype(int) + 2, (width, height)
    )
    if right <= left or bottom <= top:
        return None
    return int(left), int(top), int(right), int(bottom)


def _bounds_overlap(left, right) -> bool:
    return bool(
        left is not None
        and right is not None
        and left[0] < right[2]
        and right[0] < left[2]
        and left[1] < right[3]
        and right[1] < left[3]
    )


def _page_key(snapshot):
    return snapshot.get("work_id"), snapshot.get("chapter_id"), snapshot["page_id"]


def _asset_stamps(snapshot):
    """Detect replacement/deletion before reusing decoded, fingerprint-checked assets."""
    result = []
    for key in ("base_asset", "paint_asset"):
        path = snapshot.get(key)
        if path:
            try:
                stat = os.stat(path)
                result.append((key, path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
            except OSError as exc:
                raise _error("asset_unavailable", "Imported image cannot be read") from exc
    return tuple(result)


def _cache_put(snapshot, image, base, bounds):
    global _render_cache_bytes
    page_id = _page_key(snapshot)
    previous = _render_cache.get(page_id)
    # Historical observations must not evict the latest canvas for this page.
    if previous is not None and previous["snapshot"]["revision"] > snapshot["revision"]:
        return
    cost = image.width * image.height * 8
    if cost > MAX_RENDER_CACHE_BYTES:
        return
    previous = _render_cache.pop(page_id, None)
    if previous is not None:
        _render_cache_bytes -= previous["bytes"]
    _render_cache[page_id] = {
        "bytes": cost,
        "identity": _render_identity(snapshot),
        "snapshot": copy.deepcopy(snapshot),
        "image": image,
        "base": base,
        "bounds": dict(bounds),
        "asset_stamps": _asset_stamps(snapshot),
        "encoded": {},
    }
    _render_cache_bytes += cost
    while (
        len(_render_cache) > MAX_RENDER_CACHE_ENTRIES
        or _render_cache_bytes > MAX_RENDER_CACHE_BYTES
    ):
        _, evicted = _render_cache.popitem(last=False)
        _render_cache_bytes -= evicted["bytes"]


def _cache_get(snapshot):
    page_id = _page_key(snapshot)
    entry = _render_cache.get(page_id)
    if entry is not None:
        _render_cache.move_to_end(page_id)
    return entry


def _initialize_worker(font_files: tuple[str, ...]):
    global _worker_app
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from manga_translator.rendering.text_render import _fonts

    _worker_app = _fonts._ensure_qt_runtime()
    _fonts._register_project_fonts()
    _fonts._register_system_fonts()
    for path in font_files:
        if not _fonts.register_font_file(path):
            raise RuntimeError("Host font registration failed")
    _font_face.cache_clear()


def _load_asset(snapshot: dict, key: str):
    from manga_translator.utils import open_pil_image

    path = snapshot.get(key)
    if not path:
        raise _error("missing_asset", f"Page has no {key.removesuffix('_asset')} image")
    try:
        if os.path.getsize(path) > MAX_ASSET_BYTES:
            raise _error("resource_limit", "Image asset exceeds byte limit")
        with open(path, "rb") as handle:
            data = handle.read(MAX_ASSET_BYTES + 1)
        expected = snapshot.get("_asset_fingerprints", {}).get(key)
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise _error(
                "asset_changed",
                "Imported image bytes changed; this revision is no longer renderable",
            )
        with open_pil_image(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise _error("resource_limit", "Image asset exceeds pixel limit")
            return image.convert("RGBA")
    except OSError as exc:
        raise _error(
            "asset_unavailable",
            "Imported image cannot be read",
            {"error_type": type(exc).__name__},
        ) from exc


def _encode(image, max_dimension: int):
    from PIL import Image

    if (
        isinstance(max_dimension, bool)
        or not isinstance(max_dimension, int)
        or not 1 <= max_dimension <= MAX_DIMENSION
    ):
        raise _error(
            "invalid_resolution", f"max_dimension must be between 1 and {MAX_DIMENSION}"
        )
    if max(image.size) > max_dimension:
        image = image.copy()
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    while True:
        output = io.BytesIO()
        image.save(output, format="PNG", compress_level=1)
        if output.tell() <= MAX_OUTPUT_BYTES:
            return output.getvalue(), image.size
        if max(image.size) <= 64:
            raise _error("resource_limit", "PNG cannot fit the output byte limit")
        image = image.resize(
            (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
            Image.Resampling.LANCZOS,
        )


@lru_cache(maxsize=256)
def _font_face(font_family: str, size=32):
    from PyQt6.QtGui import QFont, QFontDatabase, QRawFont
    from manga_translator.rendering.text_render import _fonts

    if not isinstance(font_family, str) or not font_family or len(font_family) > 256:
        raise _error("invalid_font", "A registered font family is required")
    family, _, style = font_family.partition("::")
    available = {item.casefold(): item for item in QFontDatabase.families()}
    resolved = available.get(family.casefold())
    if resolved is None:
        raise _error(
            "font_not_found",
            "Font family is not installed or imported",
            {"font_family": font_family},
        )
    if style and style not in QFontDatabase.styles(resolved):
        raise _error(
            "font_not_found",
            "Font style is not available",
            {"font_family": font_family},
        )
    font = QFont(resolved)
    font.setPixelSize(size)
    if style:
        font.setStyleName(style)
    font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
    raw = QRawFont.fromFont(font)
    if not raw.isValid():
        raise _error("font_unavailable", "Qt could not load this font face")
    return font, raw


def _font(font_family: str, size=32):
    from manga_translator.rendering.text_render import _fonts

    face = _font_face(font_family, size)
    _fonts.set_font(font_family)
    return face


def _coverage(font_family: str, text: str):
    if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
        raise _error("resource_limit", "Coverage text exceeds character limit")
    _, raw = _font(font_family)
    missing = [
        char
        for char in dict.fromkeys(text)
        if not char.isspace() and not raw.supportsCharacter(ord(char))
    ]
    return {
        "font_family": font_family,
        "resolved_family": raw.familyName(),
        "style": raw.styleName(),
        "covered": not missing,
        "missing_characters": missing,
        "checked_characters": len(set(text)),
    }


def _prepare(snapshot: dict, region_ids=None):
    import numpy as np
    from .snapshot import layout_box
    from manga_translator.config import Config
    from manga_translator.rendering import (
        _native_render_rect_points,
        calc_box_from_font,
    )
    from manga_translator.rendering.rich_text import (
        ensure_rich_text_document,
        iter_render_spans,
        plain_text_of,
    )
    from manga_translator.rendering.text_render import _fonts
    from manga_translator.utils import TextBlock

    config = Config()
    # Only render configuration is host-injected; no model API renderer or rule execution.
    for name, value in snapshot.get("_render_config", {}).items():
        if name in (
            "disable_font_border",
            "no_hyphenation",
            "line_spacing",
            "letter_spacing",
            "font_family",
        ):
            setattr(config.render, name, value)
    selected = set(region_ids) if region_ids is not None else None
    available = {region["region_id"] for region in snapshot["regions"]}
    if selected is not None and not selected <= available:
        raise _error("region_not_found", "Requested region does not exist on this page")
    prepared = []
    for region in snapshot["regions"]:
        if selected is not None and region["region_id"] not in selected:
            continue
        args = dict(region)
        lines = np.asarray(args.get("lines"), dtype=np.float64)
        if (
            lines.ndim != 3
            or lines.shape[1:] != (4, 2)
            or not len(lines)
            or not np.isfinite(lines).all()
        ):
            raise _error(
                "invalid_geometry",
                "Region requires finite quadrilateral lines",
                {"region_id": region["region_id"]},
            )
        args["texts"] = args.get("texts") or [args.get("text") or ""]
        if "fg_colors" in args:
            args["fg_color"] = args.pop("fg_colors")
        if "bg_colors" in args:
            args["bg_color"] = args.pop("bg_colors")
        if args.get("stroke_color"):
            color = args["stroke_color"].removeprefix("#")
            args["bg_color"] = tuple(
                int(color[index : index + 2], 16) for index in (0, 2, 4)
            )
            args["adjust_bg_color"] = False
        if "stroke_width" in args:
            args["default_stroke_width"] = args.pop("stroke_width")
        if args.get("disable_font_border", config.render.disable_font_border):
            args["default_stroke_width"] = 0
        family = (
            args.get("font_family")
            or config.render.font_family
            or _fonts.DEFAULT_FONT_FAMILY
        )
        _font(family)
        args["font_family"] = family
        if args.get("translation_rich") is not None:
            document = ensure_rich_text_document(args["translation_rich"])
            for span in iter_render_spans(document):
                if span.style.font_family:
                    _font(span.style.font_family)
            args["translation_rich"] = document
        args["center"], target = layout_box(args, lines)
        block = TextBlock(**args)
        value = block.get_translation_for_rendering()
        if len(plain_text_of(value)) > MAX_TEXT_LENGTH:
            raise _error("resource_limit", "Region text exceeds character limit")
        if not plain_text_of(value).strip():
            prepared.append((region, block, None, target, (0, 0, 0, (0, 0))))
            continue
        if not 1 <= block.font_size <= 8192:
            raise _error(
                "invalid_font_size", "Region must have an explicit positive font size"
            )
        _font(family)
        config._current_region = block
        horizontal = block.direction in ("h", "hr", "hl")
        kwargs = dict(
            line_spacing=block.line_spacing or 1,
            config=config,
            target_lang=block.target_lang,
            letter_spacing=block.letter_spacing or 1,
            stroke_width=block.stroke_width,
        )
        metrics = calc_box_from_font(block.font_size, value, horizontal, **kwargs)
        if min(metrics[:2]) <= 0:
            raise _error(
                "text_layout_failed", "Qt produced no drawable layout for nonempty text"
            )
        if metrics[0] * metrics[1] > MAX_IMAGE_PIXELS or max(metrics[:2]) > 16000:
            raise _error(
                "resource_limit", "Text layer exceeds the rendering surface limit"
            )
        points = _native_render_rect_points(
            block.center, metrics[0], metrics[1], block.angle
        )
        prepared.append((region, block, points, target, metrics))
    return config, prepared


def _base(snapshot):
    from PIL import Image
    from .snapshot import composite_paste_layers

    if snapshot.get("base_status") == "dimension_mismatch":
        raise _error(
            "base_dimension_mismatch",
            "Inpaint sidecar dimensions do not match the working page",
        )
    image = _load_asset(snapshot, "base_asset")
    size = (snapshot.get("width", image.width), snapshot.get("height", image.height))
    if image.size != size:
        raise _error(
            "base_dimension_mismatch",
            "Base image dimensions do not match this snapshot",
        )
    layers = snapshot.get("_project_layers", {})
    for name in ("paint_overlay", "stamp_overlay"):
        encoded = layers.get(name)
        if encoded:
            if len(encoded) > MAX_ASSET_BYTES * 4 // 3:
                raise _error("resource_limit", "Overlay exceeds byte limit")
            with Image.open(
                io.BytesIO(base64.b64decode(encoded, validate=True))
            ) as overlay:
                if (
                    overlay.size[0] * overlay.size[1] > MAX_IMAGE_PIXELS
                    or overlay.mode != "RGBA"
                ):
                    raise _error(
                        "invalid_overlay", "Overlay must be a bounded RGBA image"
                    )
                image.alpha_composite(overlay.resize(size, Image.Resampling.NEAREST))
        elif name == "paint_overlay" and snapshot.get("paint_asset"):
            overlay = _load_asset(snapshot, "paint_asset")
            image.alpha_composite(overlay.resize(size, Image.Resampling.NEAREST))
    return composite_paste_layers(image, layers.get("paste_overlays", []))


def _render_prepared(image, config, prepared, selected=None):
    import numpy as np
    from manga_translator.rendering import render
    from PIL import Image

    selected = set(selected) if selected is not None else None
    rgb = np.array(image.convert("RGB"))
    alpha = np.array(image.getchannel("A"))
    bounds = {}
    for region, _block, points, _target, _metrics in prepared:
        bounds[region["region_id"]] = _points_bounds(
            points, image.width, image.height
        )
    for paint_part in ("effects", "stroke", "fill"):
        for region, block, points, _, _ in prepared:
            if selected is not None and region["region_id"] not in selected:
                continue
            if points is None or block.opacity == 0:
                continue
            opacity = float(block.opacity)
            if not np.isfinite(opacity) or not 0 <= opacity <= 1:
                raise _error(
                    "invalid_opacity", "Region opacity must be between zero and one"
                )
            region_bounds = bounds[region["region_id"]]
            if opacity < 1 and region_bounds is not None:
                left, top, right, bottom = region_bounds
                array_bounds = np.s_[top:bottom, left:right]
                before_rgb = rgb[array_bounds].copy()
                before_alpha = alpha[array_bounds].copy()
            else:
                array_bounds = None
            rgb = render(
                rgb,
                block,
                points,
                not config.render.no_hyphenation,
                block.line_spacing or 1,
                region.get("disable_font_border", config.render.disable_font_border),
                config,
                render_alpha=alpha,
                paint_part=paint_part,
            )
            if opacity < 1 and array_bounds is not None:
                rgb[array_bounds] = np.rint(
                    before_rgb * (1 - opacity) + rgb[array_bounds] * opacity
                ).astype(np.uint8)
                alpha[array_bounds] = np.rint(
                    before_alpha * (1 - opacity) + alpha[array_bounds] * opacity
                ).astype(np.uint8)
    result = Image.fromarray(rgb).convert("RGBA")
    result.putalpha(Image.fromarray(alpha))
    return result, bounds


def _render_full(snapshot):
    entry = _cache_get(snapshot)
    base = (
        entry["base"] if entry is not None
        and entry["identity"] == _render_identity(snapshot)
        and entry["asset_stamps"] == _asset_stamps(snapshot)
        else _base(snapshot)
    )
    config, prepared = _prepare(snapshot)
    result, bounds = _render_prepared(base, config, prepared)
    _cache_put(snapshot, result, base, bounds)
    return result


def _render_snapshot(snapshot, region_ids=None, *, force_full=False):
    started = perf_counter()
    result = ({"status": "fallback", "reason": "full_render_requested"}
              if force_full else _fast_render(snapshot, region_ids))
    if result["status"] == "fallback":
        result = {
            "status": "full_fallback", "fallback_reason": result["reason"],
            "image": _render_full(snapshot),
            "rendered_regions": [r["region_id"] for r in snapshot["regions"]],
        }
    result["render_ms"] = (perf_counter() - started) * 1000
    return result


def _render(snapshot):
    # The cached image belongs to the worker. Callers may freely modify their copy.
    return _render_snapshot(snapshot)["image"].copy()


def _stroke_signature(region):
    return tuple(
        (key, region.get(key))
        for key in (
            "stroke_width",
            "default_stroke_width",
            "stroke_color",
            "disable_font_border",
        )
    )


def _fast_render(snapshot, region_ids=None):
    """Patch an isolated text region onto the last full render, or explain fallback."""
    if region_ids is not None and (not isinstance(region_ids, (list, tuple))
            or any(not isinstance(item, str) or not item for item in region_ids)):
        return {"status": "fallback", "reason": "invalid_region_set"}
    selected = None if region_ids is None else set(region_ids)
    entry = _cache_get(snapshot)
    if entry is None:
        return {"status": "fallback", "reason": "full_render_cache_miss"}
    if entry["identity"] != _render_identity(snapshot):
        return {"status": "fallback", "reason": "render_identity_changed"}
    if entry["asset_stamps"] != _asset_stamps(snapshot):
        return {"status": "fallback", "reason": "asset_changed"}
    if snapshot["revision"] < entry["snapshot"]["revision"]:
        return {"status": "fallback", "reason": "revision_not_newer"}

    old_regions = {item["region_id"]: item for item in entry["snapshot"]["regions"]}
    new_regions = {item["region_id"]: item for item in snapshot["regions"]}
    # Order is part of compositing, even when all region payloads are unchanged.
    if list(old_regions) != list(new_regions) or (selected is not None and not selected <= new_regions.keys()):
        return {"status": "fallback", "reason": "region_set_changed"}
    changed = {
        region_id
        for region_id in new_regions
        if _region_fingerprint(old_regions[region_id])
        != _region_fingerprint(new_regions[region_id])
    }
    if not changed:
        entry["snapshot"] = copy.deepcopy(snapshot)
        return {"status": "cached", "image": entry["image"], "rendered_regions": []}
    if snapshot["revision"] == entry["snapshot"]["revision"]:
        return {"status": "fallback", "reason": "revision_content_changed"}
    if selected is not None and not changed <= selected:
        return {"status": "fallback", "reason": "unrequested_regions_changed"}
    for region_id in changed:
        old_region, new_region = old_regions[region_id], new_regions[region_id]
        if float(old_region.get("angle", 0) or 0) != 0 or float(
            new_region.get("angle", 0) or 0
        ) != 0:
            return {"status": "fallback", "reason": "rotation_requires_full_render"}
        if _stroke_signature(old_region) != _stroke_signature(new_region):
            return {"status": "fallback", "reason": "stroke_change_requires_full_render"}
        if old_region.get("translation_rich") is not None or new_region.get(
            "translation_rich"
        ) is not None:
            return {"status": "fallback", "reason": "rich_text_requires_full_render"}

    old_bounds = entry["bounds"]
    new_config, new_prepared = _prepare(snapshot, list(changed))
    new_bounds = {
        item[0]["region_id"]: _points_bounds(
            item[2], snapshot["width"], snapshot["height"]
        )
        for item in new_prepared
    }
    # Only these small rectangles will be converted to numpy and drawn. Keep an
    # even origin to preserve the backend's round-to-even placement at half pixels.
    dirty = {}
    for rid in changed:
        boxes = [box for box in (old_bounds.get(rid), new_bounds.get(rid)) if box]
        if boxes:
            dirty[rid] = (min(b[0] for b in boxes) // 2 * 2,
                          min(b[1] for b in boxes) // 2 * 2,
                          max(b[2] for b in boxes), max(b[3] for b in boxes))
    unchanged = set(new_regions) - changed
    for region_id in changed:
        for other_id in unchanged:
            if _bounds_overlap(dirty.get(region_id), old_bounds.get(other_id)):
                return {"status": "fallback", "reason": "region_overlap_requires_full_render"}
        for other_id in changed:
            if other_id <= region_id:
                continue
            if _bounds_overlap(dirty.get(region_id), dirty.get(other_id)):
                return {"status": "fallback", "reason": "region_overlap_requires_full_render"}

    image = entry["image"].copy()
    base = entry["base"]
    for region, block, points, target, metrics in new_prepared:
        box = dirty.get(region["region_id"])
        if box is None:
            continue
        shifted = None if points is None else points - (box[0], box[1])
        tile, _ = _render_prepared(
            base.crop(box), new_config, [(region, block, shifted, target, metrics)]
        )
        image.paste(tile, box[:2])
    _cache_put(snapshot, image, base, {**old_bounds, **new_bounds})
    return {"status": "fast", "image": image, "rendered_regions": sorted(changed)}


def _measure(snapshot, region_ids=None):
    import numpy as np

    _, prepared = _prepare(snapshot, region_ids)
    rows = []
    for region, block, points, target, metrics in prepared:
        width, height, lines, body_center = metrics
        page_overflow = False
        if points is not None:
            vertices = np.asarray(points).reshape(-1, 2)
            page_overflow = bool(
                (vertices < 0).any()
                or (vertices[:, 0] > snapshot["width"]).any()
                or (vertices[:, 1] > snapshot["height"]).any()
            )
        rows.append(
            {
                "region_id": region["region_id"],
                "version": region["version"],
                "font_size": block.font_size,
                "width": width,
                "height": height,
                "line_count": lines,
                "body_center": list(body_center),
                "render_polygon": points.reshape(-1, 2).tolist()
                if points is not None
                else [],
                "target_box_local": list(target),
                "overflow_x": max(0, width - (target[2] - target[0])),
                "overflow_y": max(0, height - (target[3] - target[1])),
                "outside_page": page_overflow,
            }
        )
    return {
        "page_id": snapshot["page_id"],
        "revision": snapshot["revision"],
        "regions": rows,
        "measurement": "qt_native_layout",
    }


def _fit(snapshot, region_id, min_size, max_size):
    from manga_translator.rendering import calc_font_from_box
    from manga_translator.rendering.text_render import _fonts

    if (
        any(isinstance(v, bool) or not isinstance(v, int) for v in (min_size, max_size))
        or not 1 <= min_size <= max_size <= 8192
    ):
        raise _error(
            "invalid_font_size",
            "Fit range must satisfy 1 <= min_size <= max_size <= 8192",
        )
    config, prepared = _prepare(snapshot, [region_id])
    region, block, _, target, _ = prepared[0]
    _fonts.set_font(block.font_family)
    config._current_region = block
    size = calc_font_from_box(
        target[2] - target[0],
        target[3] - target[1],
        block.get_translation_for_rendering(),
        block.direction in ("h", "hr", "hl"),
        block.line_spacing or 1,
        config,
        block.target_lang,
        block.letter_spacing or 1,
        block.stroke_width,
    )
    chosen = max(min_size, min(max_size, size))
    candidate = copy.deepcopy(snapshot)
    next(r for r in candidate["regions"] if r["region_id"] == region_id)[
        "font_size"
    ] = chosen
    measured = _measure(candidate, [region_id])["regions"][0]
    return {
        "page_id": snapshot["page_id"],
        "revision": snapshot["revision"],
        "expected_versions": {region_id: region["version"]},
        "policy_version": snapshot["policy_version"],
        "fits": measured["overflow_x"] == 0 and measured["overflow_y"] == 0,
        "proposed_edits": [
            {
                "op": "set_region_style",
                "region_id": region_id,
                "style": {"font_size": chosen},
            }
        ],
        "measurement": measured,
        "applied": False,
        "limitations": [
            "Bounding-box fitting does not prove balloon-mask containment or visual acceptance"
        ],
    }


def _fonts(query, characters, sample_text, limit, cursor):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QFontDatabase, QImage, QPainter
    from manga_translator.rendering.text_render import _fonts as font_engine
    from PIL import Image

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _error("invalid_limit", "Font limit must be between 1 and 100")
    if (
        len(query) > 256
        or len(characters) > 4096
        or (sample_text is not None and len(sample_text) > 500)
    ):
        raise _error("resource_limit", "Font query or sample exceeds character limits")
    signature = hashlib.sha256((query + "\0" + characters).encode()).hexdigest()[:16]
    offset = 0
    if cursor:
        try:
            key, position = cursor.split(":")
            offset = int(position)
        except (ValueError, AttributeError) as exc:
            raise _error("invalid_cursor", "Invalid font cursor") from exc
        if key != signature or offset < 0:
            raise _error("invalid_cursor", "Font cursor does not match query")
    files_by_family = {}
    for path, families in font_engine._font_families_cache.items():
        for family in families:
            files_by_family.setdefault(family, []).append(path)
    matches = []
    for family in sorted(QFontDatabase.families(), key=str.casefold):
        if query.casefold() not in family.casefold():
            continue
        if characters and not _coverage(family, characters)["covered"]:
            continue
        matches.append(family)
    if offset > len(matches):
        raise _error("invalid_cursor", "Font cursor is past the final result")
    rows = [
        {
            "family": family,
            "styles": list(QFontDatabase.styles(family)),
            "files": files_by_family.get(family, []),
            "fixed_pitch": QFontDatabase.isFixedPitch(family),
        }
        for family in matches[offset : offset + limit]
    ]
    result = {
        "fonts": rows,
        "total": len(matches),
        "next_cursor": f"{signature}:{offset + limit}"
        if offset + limit < len(matches)
        else None,
    }
    if sample_text and rows:
        # One bounded specimen per listed family, in inventory order.
        height = 90 * len(rows)
        canvas = QImage(1200, height, QImage.Format.Format_RGBA8888)
        canvas.fill(Qt.GlobalColor.white)
        painter = QPainter(canvas)
        try:
            painter.setPen(Qt.GlobalColor.black)
            for index, row in enumerate(rows):
                font, _ = _font(row["family"], 24)
                painter.setFont(font)
                painter.drawText(12, index * 90 + 28, row["family"])
                painter.drawText(12, index * 90 + 65, sample_text)
        finally:
            painter.end()
        pointer = canvas.bits()
        pointer.setsize(canvas.sizeInBytes())
        image = Image.frombytes(
            "RGBA", (canvas.width(), canvas.height()), bytes(pointer)
        )
        result["image"], size = _encode(image, MAX_DIMENSION)
        result.update(mime_type="image/png", width=size[0], height=size[1])
    return result


def _observe_snapshot(snapshot, view, crop, dimension, *, region_ids=None, force_full=False):
    global _render_cache_bytes
    started = perf_counter()
    if view not in ("original", "base", "rendered"):
        raise _error("invalid_view", "view must be original, base or rendered")
    if (isinstance(dimension, bool) or not isinstance(dimension, int)
            or not 1 <= dimension <= MAX_DIMENSION):
        raise _error("invalid_resolution", f"max_dimension must be between 1 and {MAX_DIMENSION}")
    details = {}
    if view == "rendered":
        details = _render_snapshot(snapshot, region_ids, force_full=force_full)
        image = details.pop("image")
    else:
        image = _base(snapshot) if view == "base" else _load_asset(snapshot, "original_asset")
    source_size = image.size
    if crop is not None:
        if (not isinstance(crop, (tuple, list)) or len(crop) != 4
                or any(isinstance(v, bool) or not isinstance(v, int) for v in crop)
                or not 0 <= crop[0] < crop[2] <= image.width
                or not 0 <= crop[1] < crop[3] <= image.height):
            raise _error("invalid_crop", "Crop must be integer (left, top, right, bottom) inside the requested image")
    entry = _cache_get(snapshot) if view == "rendered" else None
    if entry is not None and entry["image"] is not image:
        entry = None  # Historical / oversized renders are not in the latest-page cache.
    key = (tuple(crop) if crop else None, dimension)
    encoded = entry["encoded"].get(key) if entry is not None else None
    encode_started = perf_counter()
    if encoded is None:
        encoded = _encode(image.crop(crop) if crop else image, dimension)
        if entry is not None:
            # At most two observation sizes/crops per page, within the shared byte cap.
            if len(entry["encoded"]) >= 2:
                old = entry["encoded"].pop(next(iter(entry["encoded"])))
                entry["bytes"] -= len(old[0])
                _render_cache_bytes -= len(old[0])
            if _render_cache_bytes + len(encoded[0]) <= MAX_RENDER_CACHE_BYTES:
                entry["encoded"][key] = encoded
                entry["bytes"] += len(encoded[0])
                _render_cache_bytes += len(encoded[0])
    data, size = encoded
    return {
        **details, "image": data, "mime_type": "image/png",
        "page_id": snapshot["page_id"], "requested_revision": snapshot["revision"],
        "rendered_revision": snapshot["revision"], "width": size[0], "height": size[1],
        "source_width": source_size[0], "source_height": source_size[1],
        "view": view, "crop": crop,
        "encode_ms": (perf_counter() - encode_started) * 1000,
        "worker_ms": (perf_counter() - started) * 1000,
    }


def _execute(operation, args):
    from ..domain.tool_models import ToolError

    try:
        if operation == "render":
            value = _render(*args)
        elif operation == "fast_render":
            snapshot, region_ids, dimension = args
            value = _observe_snapshot(snapshot, "rendered", None, dimension, region_ids=region_ids)
        elif operation == "observe":
            snapshot, view, crop, dimension, force_full = args
            value = _observe_snapshot(snapshot, view, crop, dimension, force_full=force_full)
        elif operation == "warmup":
            value = {"status": "ready"}
        elif operation == "measure":
            value = _measure(*args)
        elif operation == "fit":
            value = _fit(*args)
        elif operation == "fonts":
            value = _fonts(*args)
        elif operation == "coverage":
            value = _coverage(*args)
        else:
            raise _error("invalid_operation", "Unknown host rendering operation")
        return {"value": value}
    except ToolError as exc:
        return {
            "error": {"code": exc.code, "message": str(exc), "details": exc.details}
        }
    except Exception as exc:
        # Avoid serializing exception objects or exposing host paths in model output.
        return {
            "error": {
                "code": "render_failed",
                "message": "Qt rendering operation failed",
                "details": {"error_type": type(exc).__name__},
            }
        }


class BackendRenderer:
    """One persistent spawned Qt worker, with bounded submission and no GUI state.

    Call from a guarded host entrypoint on Windows (``if __name__ == '__main__'``).
    Imported font files are host-only constructor values, never tool parameters.
    Shutdown waits for already running native rendering rather than freeing Qt
    while native code is still using it.
    """

    def __init__(self, *, font_files=(), max_pending: int = 4):
        if (
            isinstance(max_pending, bool)
            or not isinstance(max_pending, int)
            or not 1 <= max_pending <= 32
        ):
            raise ValueError("max_pending must be between 1 and 32")
        self._pool = ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize_worker,
            initargs=(tuple(os.path.abspath(path) for path in font_files),),
        )
        self._slots = asyncio.Semaphore(max_pending)
        self._closed = False
        self._closing = None
        self._scheduled = OrderedDict()
        self._max_pending = max_pending

    async def _call(self, operation, *args, cancelled=None, submitted=None):
        started = perf_counter()
        if self._closed:
            raise _error("renderer_closed", "Renderer is closed")
        captured = copy.deepcopy(args)
        await self._slots.acquire()
        if self._closed:
            self._slots.release()
            raise _error("renderer_closed", "Renderer is closed")
        if cancelled is not None and cancelled.is_set():
            self._slots.release()
            raise _error("cancelled", "Task was cancelled before rendering")
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(
                self._pool, _execute, operation, captured
            )
        except Exception:
            self._slots.release()
            raise
        # Cancellation does not release admission until the native worker really finishes.
        future.add_done_callback(lambda _: self._slots.release())
        if submitted is not None:
            submitted()
        try:
            result = await asyncio.shield(future)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _error(
                "renderer_unavailable",
                "Persistent Qt worker is unavailable",
                {"error_type": type(exc).__name__},
            ) from exc
        if "error" in result:
            error = result["error"]
            raise _error(error["code"], error["message"], error["details"])
        value = result["value"]
        if isinstance(value, dict):
            value["elapsed_ms"] = (perf_counter() - started) * 1000
        return value

    async def warmup(self):
        """Start the worker and register fonts before the first interactive edit."""
        return await self._call("warmup")

    @staticmethod
    def _request_key(snapshot):
        return _page_key(snapshot), snapshot["revision"], _fingerprint(snapshot)

    def schedule(self, snapshot, *, cancelled=None):
        """Queue an immutable preview after a commit without blocking the tool.

        Pending previews are bounded; explicit observe requests always remain
        available, including historical revisions coalesced out of this queue.
        """
        if self._closed:
            return "unavailable"
        if cancelled is not None and cancelled.is_set():
            return "cancelled"
        key = self._request_key(snapshot)
        if key in self._scheduled:
            return "queued"
        for old_key in list(self._scheduled):
            old = self._scheduled[old_key]
            task = old["task"]
            if task.done():
                del self._scheduled[old_key]
            elif old_key[0] == key[0] and not old["submitted"] and not old["claimed"]:
                # Coalesce only previews nobody has requested to observe yet.
                task.cancel()
                del self._scheduled[old_key]
        if len(self._scheduled) >= self._max_pending:
            # No unbounded task queue or dropped observation barrier.
            return "deferred"
        captured = copy.deepcopy(snapshot)
        state = {"submitted": False, "claimed": False, "cancelled": cancelled}

        async def preview():
            if cancelled is not None and cancelled.is_set():
                raise _error("cancelled", "Task was cancelled before rendering")
            result = await self._call(
                "observe", captured, "rendered", None, 1600, False,
                cancelled=cancelled, submitted=lambda: state.update(submitted=True),
            )
            if cancelled is not None and cancelled.is_set():
                raise _error("cancelled", "Task was cancelled during rendering")
            return result

        task = asyncio.create_task(preview())
        # Failures are retained for observe(), never reported as a successful image.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        state["task"] = task
        self._scheduled[key] = state
        return "queued"

    async def render(self, snapshot: dict):
        return await self._call("render", snapshot)
    async def fast_render(
        self, snapshot: dict, region_ids=None, max_dimension: int = 1600
    ) -> dict:
        """Render isolated changed regions; return fallback status when unsafe."""
        return await self._call("fast_render", snapshot, region_ids, max_dimension)

    async def observe(
        self,
        snapshot: dict,
        view: str = "rendered",
        crop=None,
        max_dimension: int = 1600,
        *,
        force_full: bool = False,
    ) -> dict:
        if self._closed:
            raise _error("renderer_closed", "Renderer is closed")
        pending = self._scheduled.get(self._request_key(snapshot)) if view == "rendered" and not force_full else None
        # A cancelled task's speculative preview must not poison another task's read.
        if pending is not None and not (pending["cancelled"] is not None and pending["cancelled"].is_set()):
            pending["claimed"] = True
            payload = await asyncio.shield(pending["task"])
            if crop is None and max_dimension == 1600:
                return copy.deepcopy(payload)
        return await self._call("observe", snapshot, view, crop, max_dimension, force_full)

    async def fonts(
        self,
        query: str = "",
        characters: str = "",
        sample_text=None,
        limit: int = 50,
        cursor=None,
    ) -> dict:
        return await self._call("fonts", query, characters, sample_text, limit, cursor)

    async def measure(self, snapshot: dict, region_ids=None) -> dict:
        return await self._call("measure", snapshot, region_ids)

    async def fit(
        self, snapshot: dict, region_id: str, min_size: int = 1, max_size: int = 300
    ) -> dict:
        return await self._call("fit", snapshot, region_id, min_size, max_size)

    async def coverage(self, font_family: str, text: str) -> dict:
        return await self._call("coverage", font_family, text)

    async def close(self):
        if self._closing is None:
            self._closed = True
            for pending in self._scheduled.values():
                pending["task"].cancel()
            self._closing = asyncio.create_task(
                self._shutdown()
            )
        await asyncio.shield(self._closing)

    async def _shutdown(self):
        await asyncio.gather(*(p["task"] for p in self._scheduled.values()), return_exceptions=True)
        self._scheduled.clear()
        await asyncio.to_thread(partial(self._pool.shutdown, wait=True, cancel_futures=True))
