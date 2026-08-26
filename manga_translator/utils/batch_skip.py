"""Backend-owned input skipping, result ordering, and resume-context planning."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from ..config import Translator
from .generic import Context
from .path_manager import (
    find_json_path,
    get_original_txt_path,
    get_translated_txt_path,
)

logger = logging.getLogger("manga_translator")


@dataclass(frozen=True)
class SkipDecision:
    reason: str
    message: str
    output_path: str | None
    context_eligible: bool = False


@dataclass
class BatchInputPlan:
    source_items: list[tuple]
    pending_items: list[tuple]
    skipped_contexts: list[Context]
    source_order: dict[str, int]
    resume_pages: list[tuple[int, str, list[dict]]]
    resume_order: dict[str, int]

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_contexts)

    def merge_results(self, processed_contexts: list[Context]) -> list[Context]:
        combined = [*self.skipped_contexts, *processed_contexts]
        combined.sort(
            key=lambda ctx: self.source_order.get(
                normalize_path(getattr(ctx, "image_name", "")),
                len(self.source_order),
            )
        )
        return combined


def input_path(item: Any) -> str:
    image = item[0] if isinstance(item, tuple) else item
    return str(
        getattr(image, "name", None)
        or getattr(image, "image_name", None)
        or image
        or ""
    )


def normalize_path(path: str) -> str:
    return os.path.abspath(os.path.normpath(str(path)))


def _skip_decision(
    translator, image_path: str, config, save_info: dict | None
) -> SkipDecision | None:
    if not save_info or save_info.get("overwrite", True):
        return None

    if translator.translate_json_only:
        required_path = get_original_txt_path(image_path, create_dir=False)
        if not os.path.exists(required_path):
            return SkipDecision(
                reason="missing_required_original_text",
                message=f"原文文件不存在: {os.path.basename(required_path)}",
                output_path=None,
            )
        return None

    if translator.template and translator.save_text:
        output_path = get_original_txt_path(image_path, create_dir=False)
        if os.path.exists(output_path):
            return SkipDecision(
                reason="existing_original_text",
                message=f"原文文件已存在: {os.path.basename(output_path)}",
                output_path=output_path,
            )
        return None

    if translator.generate_and_export:
        output_path = get_translated_txt_path(image_path, create_dir=False)
        if os.path.exists(output_path):
            return SkipDecision(
                reason="existing_translated_text",
                message=f"翻译文件已存在: {os.path.basename(output_path)}",
                output_path=output_path,
            )
        return None

    output_path = translator._calculate_output_path(image_path, save_info)
    if not os.path.exists(output_path):
        return None

    translator_type = getattr(getattr(config, "translator", None), "translator", None)
    context_eligible = (
        not translator.load_text
        and not translator.colorize_only
        and not translator.upscale_only
        and not translator.inpaint_only
        and not translator.replace_translation
        and translator_type
        in {
            Translator.openai,
            Translator.gemini,
            Translator.openai_hq,
            Translator.gemini_hq,
        }
    )
    return SkipDecision(
        reason="existing_output",
        message=f"输出文件已存在: {os.path.basename(output_path)}",
        output_path=output_path,
        context_eligible=context_eligible,
    )


def _load_resume_pages(
    source_paths: list[str],
    eligible_skipped_paths: set[str],
    context_size: int,
) -> tuple[
    list[tuple[int, str, list[dict]]],
    dict[str, int],
]:
    if context_size <= 0 or not eligible_skipped_paths or not source_paths:
        return [], {}

    resume_order = {image_path: order for order, image_path in enumerate(source_paths)}
    resume_pages: list[tuple[int, str, list[dict]]] = []

    for order, image_path in enumerate(source_paths):
        if image_path not in eligible_skipped_paths:
            continue
        json_path = find_json_path(image_path)
        if not json_path:
            logger.warning("Resume context skipped: JSON not found for %s", image_path)
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            image_data = (
                next(iter(data.values())) if isinstance(data, dict) and data else None
            )
            regions = (
                image_data
                if isinstance(image_data, list)
                else (
                    image_data.get("regions", [])
                    if isinstance(image_data, dict)
                    else []
                )
            )
            entries = []
            for region in regions:
                if not isinstance(region, dict):
                    continue
                original_text = region.get("text")
                translated_text = region.get("translation")
                if not original_text or not translated_text:
                    continue
                lines = region.get("lines")
                region_count = len(lines) if isinstance(lines, list) else 1
                entries.append(
                    {
                        "text": original_text,
                        "translation": translated_text,
                        "original_region_count": max(region_count, 1),
                    }
                )
            if entries:
                resume_pages.append((order, image_path, entries))
                logger.info(
                    "[Resume Context] Loaded %s entries from skipped page %s",
                    len(entries),
                    os.path.basename(image_path),
                )
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Resume context failed for %s: %s", image_path, exc)

    return resume_pages, resume_order


def plan_batch_inputs(
    translator, items: list[tuple], save_info: dict | None
) -> BatchInputPlan:
    """Plan one complete ordered task before image materialization starts."""
    source_items = list(items)
    pending_items = []
    skipped_contexts = []
    source_paths = [normalize_path(input_path(item)) for item in source_items]
    source_order = {}
    for index, path in enumerate(source_paths):
        source_order.setdefault(path, index)

    eligible_skipped_paths = set()
    for item, normalized_source_path in zip(source_items, source_paths):
        image_path = input_path(item)
        try:
            decision = _skip_decision(translator, image_path, item[1], save_info)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to check existing output for %s: %s", image_path, exc
            )
            decision = None

        if decision is None:
            pending_items.append(item)
            continue

        ctx = Context(
            image_name=image_path,
            success=True,
            skipped=True,
            skip_reason=decision.reason,
            skip_message=decision.message,
            output_path=decision.output_path,
            context_eligible=decision.context_eligible,
        )
        skipped_contexts.append(ctx)
        if decision.context_eligible:
            eligible_skipped_paths.add(normalized_source_path)
        logger.info("Skipping %s: %s", os.path.basename(image_path), decision.message)

    resume_pages, resume_order = _load_resume_pages(
        source_paths,
        eligible_skipped_paths,
        int(getattr(translator, "context_size", 0) or 0),
    )
    return BatchInputPlan(
        source_items=source_items,
        pending_items=pending_items,
        skipped_contexts=skipped_contexts,
        source_order=source_order,
        resume_pages=resume_pages,
        resume_order=resume_order,
    )


def slice_batch_indices(
    items: list[tuple],
    max_batch_size: int,
    resume_pages: list[tuple[int, str, list[dict]]],
    resume_order: dict[str, int],
) -> list[tuple[int, int]]:
    """Split batches at size limits and skipped-page context boundaries."""
    if not items:
        return []

    skipped_orders = {order for order, _path, _entries in resume_pages}
    max_batch_size = max(1, int(max_batch_size or 1))
    batches = []
    batch_start = 0
    while batch_start < len(items):
        batch_end = batch_start + 1
        while batch_end < min(batch_start + max_batch_size, len(items)):
            previous_order = resume_order.get(
                normalize_path(input_path(items[batch_end - 1]))
            )
            current_order = resume_order.get(
                normalize_path(input_path(items[batch_end]))
            )
            if (
                previous_order is not None
                and current_order is not None
                and any(
                    previous_order < order < current_order for order in skipped_orders
                )
            ):
                break
            batch_end += 1
        batches.append((batch_start, batch_end))
        batch_start = batch_end
    return batches
