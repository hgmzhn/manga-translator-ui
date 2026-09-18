"""Text search snapshots, occurrences, and paginated replacement operations."""

from __future__ import annotations

import time
from copy import deepcopy
from uuid import uuid4

from ..domain.tool_models import ResourceScope, ToolContext, ToolError
from . import text
from .common import _fingerprint


class WorkspaceSearch:
    """Use Workspace's shared lock, search state, and atomic commit helpers."""
    def _expire_searches(self):
        now = time.monotonic()
        for sid, search in list(self._searches.items()):
            if now - search["created"] > self.search_ttl:
                self._drop_search(sid)

    def _drop_search(self, sid):
        search = self._searches.pop(sid)
        for occurrence in search["occurrences"]:
            self._occurrences.pop(occurrence["occurrence_id"], None)

    def _occurrence(self, ctx, occurrence_id, page, region):
        self._expire_searches()
        entry = self._occurrences.get(occurrence_id)
        if entry is None:
            raise ToolError(
                "occurrence_expired", "Occurrence is unknown or expired; search again"
            )
        _, occurrence = entry
        self._active(ctx)
        if occurrence["field"] != "translation":
            raise ToolError(
                "source_read_only",
                "Source occurrences cannot address translation spans",
            )
        if (
            occurrence["page_id"] != page["page_id"]
            or occurrence["region_id"] != region["region_id"]
        ):
            raise ToolError(
                "occurrence_mismatch", "Occurrence belongs to another resource"
            )
        if occurrence["region_version"] != region["version"]:
            raise ToolError(
                "stale_occurrence", "Region changed since search; search again"
            )
        value = text.text_of(region, "translation")
        if _fingerprint(value) != occurrence["text_fingerprint"]:
            raise ToolError(
                "stale_occurrence", "Text changed since search; search again"
            )
        return occurrence

    @staticmethod
    def _limit(limit):
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 200
        ):
            raise ToolError("invalid_limit", "limit must be between 1 and 200")

    def find_text(
        self,
        ctx: ToolContext,
        scope: ResourceScope,
        query: str,
        field: str = "translation",
        mode: str = "literal",
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        with self._lock:
            self._limit(limit)
            self._expire_searches()
            signature = _fingerprint(
                {
                    "scope": scope.model_dump(),
                    "query": query,
                    "field": field,
                    "mode": mode,
                }
            )
            if cursor is not None:
                try:
                    sid, offset_text = cursor.split(":")
                    offset = int(offset_text)
                    search = self._searches[sid]
                except (ValueError, KeyError) as exc:
                    raise ToolError(
                        "invalid_cursor", "Search cursor is invalid or expired"
                    ) from exc
                if (
                    search["signature"] != signature
                    or search["task_id"] != ctx.task_id
                    or not 0 <= offset <= len(search["occurrences"])
                ):
                    raise ToolError(
                        "invalid_cursor", "Cursor does not belong to this query/task"
                    )
                self._active(ctx)
            else:
                compiled = text.matcher(query, mode)
                if field not in {"source", "translation"}:
                    raise ToolError(
                        "invalid_field", "field must be source or translation"
                    )
                pages = self._scope_pages(ctx, scope)
                sid = uuid4().hex
                occurrences = []
                missing = 0
                result_characters = 0
                deadline = time.monotonic() + 2
                for page in pages:
                    for region in page["regions"]:
                        value = text.text_of(region, field)
                        if value is None:
                            missing += 1
                            continue
                        fingerprint = _fingerprint(value)
                        for start, end, matched in text.matches(
                            value, compiled, deadline=deadline
                        ):
                            result_characters += len(matched) * 2 + 80
                            if result_characters > 1_000_000:
                                raise ToolError(
                                    "resource_limit",
                                    "Search snapshot is too large; narrow the query",
                                )
                            occurrences.append(
                                {
                                    "occurrence_id": uuid4().hex,
                                    "page_id": page["page_id"],
                                    "region_id": region["region_id"],
                                    "revision": page["revision"],
                                    "region_version": region["version"],
                                    "field": field,
                                    "start": start,
                                    "end": end,
                                    "text": matched,
                                    "context": value[max(0, start - 40) : end + 40],
                                    "text_fingerprint": fingerprint,
                                }
                            )
                            if len(occurrences) > text.MAX_MATCHES:
                                raise ToolError(
                                    "resource_limit",
                                    "Too many matches; narrow the scope",
                                )
                search = {
                    "signature": signature,
                    "task_id": ctx.task_id,
                    "created": time.monotonic(),
                    "page_ids": [p["page_id"] for p in pages],
                    "occurrences": occurrences,
                    "missing": missing,
                }
                while len(self._searches) >= self.max_searches:
                    self._drop_search(next(iter(self._searches)))
                self._searches[sid] = search
                for occurrence in occurrences:
                    self._occurrences[occurrence["occurrence_id"]] = sid, occurrence
                offset = 0
            total = len(search["occurrences"])
            return {
                "match_set_id": sid,
                "total": total,
                "coordinate_system": "unicode_codepoints_grapheme_boundaries",
                "matches": deepcopy(search["occurrences"][offset : offset + limit]),
                "next_cursor": f"{sid}:{offset + limit}"
                if offset + limit < total
                else None,
                "missing_source_count": search["missing"],
            }

    def replace_text(
        self,
        ctx: ToolContext,
        scope: ResourceScope,
        field: str,
        old: str,
        new: str,
        mode: str,
        command_id: str,
        expected_revisions: dict[str, int],
    ) -> dict:
        with self._lock:
            if field != "translation":
                raise ToolError(
                    "source_read_only", "Only translation text may be replaced"
                )
            payload = {
                "op": "replace_text",
                "scope": scope.model_dump(),
                "field": field,
                "old": old,
                "new": new,
                "mode": mode,
                "expected_revisions": expected_revisions,
            }
            replay = self._replay(ctx, command_id, payload)
            if replay is not None:
                return replay
            compiled = text.matcher(old, mode)
            if len(new) > text.MAX_TEXT:
                raise ToolError(
                    "resource_limit", "Replacement text exceeds character limit"
                )
            pages = self._scope_pages(ctx, scope)
            if len(pages) > 200:
                raise ToolError(
                    "resource_limit",
                    "Replacement accepts at most 200 pages; split the explicit scope",
                )
            if set(expected_revisions) != {page["page_id"] for page in pages}:
                raise ToolError(
                    "invalid_precondition",
                    "Expected revisions must exactly cover the selected pages",
                )
            permissions = []
            results = []
            counts = {
                "succeeded": 0,
                "conflicted": 0,
                "failed": 0,
                "cancelled": 0,
                "unchanged": 0,
            }
            hits = 0
            changed_regions = 0
            deadline = time.monotonic() + 5
            for page in pages:
                pid = page["page_id"]
                page_hits = 0
                try:
                    self._active(ctx)
                    if page["revision"] != expected_revisions[pid]:
                        raise ToolError(
                            "version_conflict", "Page changed before replacement"
                        )
                    candidate = deepcopy(page)
                    before = {}
                    page_permissions = []
                    for region in candidate["regions"]:
                        value = text.text_of(region, field)
                        ranges = text.matches(
                            value, compiled, new if mode == "regex" else None, deadline
                        )
                        if mode == "literal":
                            ranges = [(start, end, new) for start, end, _ in ranges]
                        if not ranges:
                            continue
                        rid = region["region_id"]
                        self._authorize(ctx, "translation_pages", pid, rid)
                        permission = ("translation_pages", pid, rid)
                        page_permissions.append(permission)
                        permissions.append(permission)
                        if region.get("locked") or region.get("is_locked"):
                            raise ToolError(
                                "region_locked", "Replacement targets a locked region"
                            )
                        page_hits += len(ranges)
                        if hits + page_hits > text.MAX_MATCHES:
                            raise ToolError(
                                "resource_limit",
                                "Too many matches; narrow the replacement scope",
                            )
                        doc = text.replace_ranges(text.document(region), ranges)
                        if text.visible(doc) != value:
                            before[rid] = deepcopy(self._region(page, rid))
                            text.store_document(region, doc)
                    hits += page_hits
                    if not before:
                        counts["unchanged"] += 1
                        results.append(
                            {
                                "page_id": pid,
                                "status": "unchanged",
                                "matched": page_hits,
                            }
                        )
                        continue
                    result = self._commit(
                        ctx,
                        page,
                        candidate,
                        before,
                        page_permissions,
                        {"batch": payload, "page_id": pid},
                        None,
                    )
                    result["matched"] = page_hits
                    result["changed_regions"] = len(before)
                    results.append(result)
                    changed_regions += len(before)
                    counts["succeeded"] += 1
                except ToolError as exc:
                    category = (
                        "cancelled"
                        if exc.code == "cancelled"
                        else "conflicted"
                        if exc.code in {"version_conflict", "policy_conflict"}
                        else "failed"
                    )
                    counts[category] += 1
                    results.append(
                        {"page_id": pid, "status": category, "error": exc.as_dict()}
                    )
            result = {
                "status": "completed"
                if not (counts["failed"] or counts["conflicted"] or counts["cancelled"])
                else "partial",
                "total_pages": len(pages),
                "matched": hits,
                "changed_regions": changed_regions,
                "match_count_complete": not (
                    counts["failed"] or counts["conflicted"] or counts["cancelled"]
                ),
                "counts": counts,
                "results": results,
                "transaction_ids": [
                    r["transaction_id"] for r in results if "transaction_id" in r
                ],
            }
            self._record(ctx, command_id, payload, permissions, result)
            return result


