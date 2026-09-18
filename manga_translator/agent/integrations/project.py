"""Host-only import of existing project sidecars; never OCR or inpaint."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_PROJECT_BYTES = 64 * 1024 * 1024


def _asset_record(path: str) -> tuple[str, tuple[int, int]]:
    from manga_translator.utils import open_pil_image
    from ..domain.tool_models import ToolError

    if os.path.getsize(path) > MAX_ASSET_BYTES:
        raise ToolError("resource_limit", "Image asset exceeds the host byte limit")
    with open(path, "rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    with open_pil_image(path) as image:
        size = image.size
        if size[0] * size[1] > MAX_IMAGE_PIXELS:
            raise ToolError(
                "resource_limit", "Image asset exceeds the host pixel limit"
            )
    return digest, size


def load_project_page(
    source_path: str,
    *,
    page_id: str,
    work_id: str,
    chapter_id: str,
    order: int,
    root_path: str | None = None,
) -> dict:
    """Import one page using host identities and explicit reading order.

    Paths and underscore-prefixed fields are private host data. Asset digests bind
    historical snapshots to the imported bytes: overwritten files are rejected by
    the renderer, never presented as the old revision. Re-import is host-owned.
    """
    from manga_translator.utils.path_manager import (
        find_inpainted_path,
        find_json_path,
        find_paint_overlay_path,
        find_work_image_path,
        resolve_original_image_path,
    )
    from ..domain.tool_models import ToolError

    if not all(
        isinstance(value, str) and value for value in (page_id, work_id, chapter_id)
    ):
        raise ToolError(
            "invalid_resource", "Host page, work and chapter IDs are required"
        )
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        raise ToolError("invalid_order", "Reading order must be a nonnegative integer")
    source = os.path.realpath(resolve_original_image_path(source_path))
    root = Path(root_path).resolve() if root_path is not None else Path(source).parent
    try:
        relative = Path(source).relative_to(root)
    except ValueError as exc:
        raise ToolError("invalid_resource", "Source image must be inside the host root") from exc
    try:
        original_digest, original_size = _asset_record(source)
        json_path = find_json_path(source)
        image_data = {}
        if json_path:
            if os.path.getsize(json_path) > MAX_PROJECT_BYTES:
                raise ToolError(
                    "resource_limit", "Project JSON exceeds the host byte limit"
                )
            with open(json_path, encoding="utf-8-sig") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ToolError(
                    "invalid_project", "Project JSON must be an image-keyed object"
                )
            image_data = data.get(source)
            if image_data is None:
                matching = [
                    value
                    for key, value in data.items()
                    if os.path.normcase(os.path.abspath(key))
                    == os.path.normcase(source)
                ]
                if not matching:
                    matching = [
                        value
                        for key, value in data.items()
                        if Path(key.replace("\\", "/")).name == Path(source).name
                    ]
                if len(matching) == 1:
                    image_data = matching[0]
                elif not matching and len(data) == 1:
                    # Existing sidecars may retain the old absolute source path after moving.
                    image_data = next(iter(data.values()))
                else:
                    raise ToolError(
                        "ambiguous_project",
                        "Project JSON does not identify this page uniquely",
                    )
            if not isinstance(image_data, dict):
                raise ToolError("invalid_project", "Page JSON must be an object")
        raw_regions = image_data.get("regions", [])
        if not isinstance(raw_regions, list) or any(
            not isinstance(r, dict) for r in raw_regions
        ):
            raise ToolError("invalid_project", "Page regions must be a list of objects")
        regions = []
        seen = set()
        for index, raw in enumerate(raw_regions):
            region = dict(raw)
            region_id = str(
                region.get("region_id")
                or uuid5(NAMESPACE_URL, f"{page_id}/region/{index}")
            )
            if region_id in seen:
                raise ToolError("invalid_project", "Duplicate region identity in page")
            seen.add(region_id)
            region.update(region_id=region_id, version=1)
            region.setdefault("translation_raw", region.get("translation", ""))
            region["ocr_status"] = (
                "available" if region.get("texts") or region.get("text") else "missing"
            )
            regions.append(region)

        fingerprints = {"original_asset": original_digest}
        display = source
        display_size = original_size
        work_image = find_work_image_path(source)
        active_work_image = bool(image_data.get("upscale_ratio")) or (
            bool(image_data.get("colorizer"))
            and str(image_data["colorizer"]).lower() != "none"
        )
        if work_image and active_work_image:
            fingerprints["display_asset"], display_size = _asset_record(work_image)
            display = work_image
        else:
            fingerprints["display_asset"] = original_digest
        base = find_inpainted_path(source)
        base_status = "missing"
        if base:
            fingerprints["base_asset"], base_size = _asset_record(base)
            base_status = (
                "available" if base_size == display_size else "dimension_mismatch"
            )
        layers = {
            name: image_data[name]
            for name in ("paint_overlay", "stamp_overlay", "paste_overlays")
            if image_data.get(name)
        }
        paint_path = (
            None if layers.get("paint_overlay") else find_paint_overlay_path(source)
        )
        if paint_path:
            fingerprints["paint_asset"], _ = _asset_record(paint_path)
        return {
            "page_id": page_id,
            "work_id": work_id,
            "chapter_id": chapter_id,
            "order": order,
            "folder": relative.parent.as_posix(),
            "name": relative.name,
            "revision": 1,
            "policy_version": 1,
            "regions": regions,
            "original_asset": source,
            "display_asset": display,
            "base_asset": base,
            "paint_asset": paint_path,
            "width": display_size[0],
            "height": display_size[1],
            "original_width": original_size[0],
            "original_height": original_size[1],
            "ocr_status": "available"
            if regions and all(r["ocr_status"] == "available" for r in regions)
            else "partial"
            if any(r["ocr_status"] == "available" for r in regions)
            else "missing",
            "base_status": base_status,
            "project_status": "available" if json_path else "missing",
            "_asset_fingerprints": fingerprints,
            "_project_layers": layers,
            "_render_config": {},
        }
    except ToolError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ToolError(
            "project_load_failed",
            "Cannot read the project image or sidecar",
            {"error_type": type(exc).__name__},
        ) from exc


def load_project_folder(root_path: str) -> list[dict]:
    """Import source images recursively, excluding translator work sidecars.

    The root directory supplies the work name; relative source folders supply
    chapters. Internal page IDs are derived from full source URIs, not basenames.
    Register every returned snapshot before sealing the workspace index.
    """
    from manga_translator.image_formats import SUPPORTED_IMAGE_EXTENSIONS
    from manga_translator.utils.path_manager import WORK_DIR_NAME

    from ..domain.tool_models import ToolError
    from ..workspace.index import page_order

    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ToolError("invalid_resource", "Host root must be an existing directory")
    if WORK_DIR_NAME in root.parts:
        raise ToolError("invalid_resource", "Translator work sidecars are not source folders")
    sources = []

    def scan_error(error: OSError) -> None:
        raise ToolError("project_load_failed", "Cannot read a source directory") from error

    for directory, folders, names in os.walk(root, onerror=scan_error, followlinks=False):
        folders[:] = [name for name in folders if name.casefold() != WORK_DIR_NAME.casefold()]
        folder = Path(directory).relative_to(root).as_posix()
        for name in names:
            if Path(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                sources.append((folder, name, Path(directory) / name))
    sources.sort(key=lambda entry: page_order(entry[0], entry[1]))
    return [
        load_project_page(
            str(source),
            page_id=uuid5(NAMESPACE_URL, source.as_uri()).hex,
            work_id=root.name or ".",
            chapter_id=folder,
            order=order,
            root_path=str(root),
        )
        for order, (folder, _, source) in enumerate(sources)
    ]
