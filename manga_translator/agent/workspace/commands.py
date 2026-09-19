"""Atomic region edit and compensating revert operations for Workspace."""

from __future__ import annotations

from copy import deepcopy

from pydantic import TypeAdapter, ValidationError

from ..domain.tool_models import (
    CreateRegion,
    DeleteRegion,
    Edit,
    ReplaceRichText,
    SetGeometry,
    SetRegionStyle,
    SetSpanStyle,
    SetTranslation,
    ToolContext,
    ToolError,
)
from . import text
from .common import _patch
from .regions import create_region

_EDIT = TypeAdapter(Edit)


class WorkspaceCommands:
    """Use the authoritative state and command coordination owned by Workspace."""
    def _permissions(self, ctx, page, edits):
        pid = page["page_id"]
        permissions = []
        for edit in edits:
            rid = edit.region_id
            if isinstance(edit, (CreateRegion, DeleteRegion)):
                permissions.extend((cap, pid, rid) for cap in (
                    "layout_pages", "geometry_pages", "translation_pages"
                ))
                continue
            capability = (
                "translation_pages"
                if isinstance(edit, SetTranslation)
                else "geometry_pages"
                if isinstance(edit, SetGeometry)
                else "layout_pages"
            )
            permissions.append((capability, pid, rid))
            if isinstance(edit, ReplaceRichText):
                region = self._region(page, rid)
                new_doc = edit.document.model_dump(mode="json", exclude_none=True)
                if text.content_signature(new_doc) != text.content_signature(
                    text.document(region)
                ):
                    permissions.append(("translation_pages", pid, rid))
        for capability, pid, rid in permissions:
            self._authorize(ctx, capability, pid, rid)
        return permissions

    def _mutate(self, ctx, page, region, edit):
        if region.get("locked") or region.get("is_locked"):
            raise ToolError("region_locked", f"Region {region['region_id']} is locked")
        if isinstance(edit, DeleteRegion):
            page["regions"].remove(region)
        elif isinstance(edit, SetRegionStyle):
            patch = _patch(edit.style)
            if not patch or any(v is None for v in patch.values()):
                raise ToolError(
                    "invalid_patch", "Region style patch must contain non-null values"
                )
            region.update(patch)
        elif isinstance(edit, SetGeometry):
            patch = _patch(edit.geometry)
            if (
                not patch
                or any(v is None for v in patch.values())
                or patch.get("lines") == []
            ):
                raise ToolError(
                    "invalid_patch", "Geometry patch must contain non-null geometry"
                )
            region.update(patch)
        elif isinstance(edit, ReplaceRichText):
            doc = edit.document.model_dump(mode="json", exclude_none=True)
            text.boundaries(text.visible(doc))
            text.store_document(region, doc)
        elif isinstance(edit, SetTranslation):
            doc = text.document(region)
            current = text.visible(doc)
            text.boundaries(edit.text)
            if current != edit.text:
                if current:
                    doc = text.replace_ranges(doc, [(0, len(current), edit.text)])
                else:
                    from manga_translator.rendering.rich_text import (
                        legacy_line_breaks_to_document,
                    )

                    doc = legacy_line_breaks_to_document(edit.text).to_dict()
                text.store_document(region, doc)
        elif isinstance(edit, SetSpanStyle):
            occurrence = self._occurrence(ctx, edit.occurrence_id, page, region)
            patch = _patch(edit.style)
            if not patch:
                raise ToolError("invalid_patch", "Span style patch cannot be empty")
            text.store_document(
                region,
                text.style_range(
                    text.document(region), occurrence["start"], occurrence["end"], patch
                ),
            )

    def apply_edits(
        self,
        ctx: ToolContext,
        page_id: str,
        expected_versions: dict[str, int],
        policy_version: int,
        edits: list[Edit],
        command_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict:
        with self._lock:
            try:
                parsed = [_EDIT.validate_python(edit) for edit in edits]
            except ValidationError as exc:
                raise ToolError("invalid_edit", str(exc)) from exc
            if not parsed or len(parsed) > 1000:
                raise ToolError("resource_limit", "Commands require 1–1000 edits")
            payload = {
                "op": "apply_edits",
                "page_id": page_id,
                "expected_versions": expected_versions,
                "policy_version": policy_version,
                "edits": [
                    edit.model_dump(mode="json", exclude_unset=True) for edit in parsed
                ],
            }
            if expected_revision is not None:
                payload["expected_revision"] = expected_revision
            self._active(ctx)
            replay = self._replay(ctx, command_id, payload)
            if replay is not None:
                return replay
            page = self._pages.get(page_id)
            if page is None:
                raise ToolError("page_not_found", "Page is not registered")
            permissions = self._permissions(ctx, page, parsed)
            touched = list(dict.fromkeys(edit.region_id for edit in parsed))
            created = {edit.region_id for edit in parsed if isinstance(edit, CreateRegion)}
            structural = [edit.region_id for edit in parsed
                          if isinstance(edit, (CreateRegion, DeleteRegion))]
            if any(sum(edit.region_id == rid for edit in parsed) != 1 for rid in structural):
                raise ToolError("invalid_edit", "新建或删除的区域不能在同一事务中重复操作")
            if created:
                grant = ctx.grant
                while grant is not None:
                    if page_id in grant.region_ids:
                        raise ToolError("permission_denied", "新建文本框需要整页编辑授权")
                    grant = grant.parent
                if expected_revision != page["revision"]:
                    raise ToolError("revision_conflict", "新建文本框的页面基准已变化，请重新读取")
            if set(expected_versions) != set(touched):
                raise ToolError(
                    "invalid_precondition",
                    "Expected versions must exactly cover edited regions",
                )
            if page["policy_version"] != policy_version:
                raise ToolError(
                    "policy_conflict",
                    "Page policy version differs from command precondition",
                )
            for rid in touched:
                if rid in created:
                    if expected_versions[rid] != 0 or (page_id, rid) in self._region_versions:
                        raise ToolError("version_conflict", "新区域 ID 已使用或新建基准无效")
                    continue
                if self._region(page, rid)["version"] != expected_versions[rid]:
                    raise ToolError(
                        "version_conflict",
                        f"Region {rid} changed",
                        {
                            "region_id": rid,
                            "actual_version": self._region(page, rid)["version"],
                        },
                    )
            candidate = deepcopy(page)
            before = {rid: None if rid in created else deepcopy(self._region(page, rid))
                      for rid in touched}
            for edit in parsed:
                if isinstance(edit, CreateRegion):
                    candidate["regions"].append(create_region(candidate, edit))
                    continue
                self._mutate(
                    ctx, candidate, self._region(candidate, edit.region_id), edit
                )
            return self._commit(
                ctx, page, candidate, before, permissions, payload, command_id
            )

    def revert_edits(
        self,
        ctx: ToolContext,
        transaction_id: str,
        expected_versions: dict[str, int],
        command_id: str,
    ) -> dict:
        with self._lock:
            payload = {
                "op": "revert_edits",
                "transaction_id": transaction_id,
                "expected_versions": expected_versions,
            }
            transaction = self._transactions.get(transaction_id)
            if transaction is None or transaction["task_id"] != ctx.task_id:
                raise ToolError(
                    "transaction_not_owned",
                    "Only this task's own transactions may be reverted",
                )
            for cap, pid, rid in transaction["permissions"]:
                self._authorize(ctx, cap, pid, rid)
            replay = self._replay(ctx, command_id, payload)
            if replay is not None:
                return replay
            page = self._pages[transaction["page_id"]]
            if (
                expected_versions != transaction["versions"]
                or page["policy_version"] != transaction["policy_version"]
            ):
                raise ToolError(
                    "version_conflict",
                    "Revert preconditions differ from the original transaction",
                )
            candidate = deepcopy(page)
            before = {}
            for rid, version in expected_versions.items():
                region = next((r for r in candidate["regions"] if r["region_id"] == rid), None)
                if self._region_versions.get((page["page_id"], rid)) != version:
                    raise ToolError(
                        "version_conflict",
                        "Later region edits prevent compensating revert",
                    )
                if region is not None and (region.get("locked") or region.get("is_locked")):
                    raise ToolError("region_locked", "Locked region cannot be reverted")
                before[rid] = deepcopy(region)
                prior = transaction["before"][rid]
                if prior is None:
                    candidate["regions"].remove(region)
                elif region is not None:
                    region.clear()
                    region.update(deepcopy(prior))
            # Restore deleted regions in their original relative order, preserving
            # unrelated regions and edits made since this transaction.
            order = transaction["before_order"]
            for position, rid in enumerate(order):
                if rid not in before or before[rid] is not None:
                    continue
                prior = transaction["before"][rid]
                successors = set(order[position + 1:])
                index = next((i for i, r in enumerate(candidate["regions"])
                              if r["region_id"] in successors), None)
                if index is None:
                    predecessors = set(order[:position])
                    index = max((i + 1 for i, r in enumerate(candidate["regions"])
                                 if r["region_id"] in predecessors), default=0)
                candidate["regions"].insert(index, deepcopy(prior))
            return self._commit(
                ctx,
                page,
                candidate,
                before,
                transaction["permissions"],
                payload,
                command_id,
            )
