"""Authoritative workspace state, page index, and shared command coordination."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from pathlib import PurePosixPath
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from ..domain.tool_models import (
    PageById,
    PageByName,
    PageRef,
    ResourceScope,
    ToolContext,
    ToolError,
    ToolScope,
)
from . import text
from .commands import WorkspaceCommands
from .common import _fingerprint
from .index import page_order
from .policies import WorkspacePolicies
from .search import WorkspaceSearch

_PAGE_REF = TypeAdapter(PageRef)


class Workspace(WorkspaceCommands, WorkspaceSearch, WorkspacePolicies):
    """Host registers immutable source snapshots; models only issue typed commands.

    Limits reject new work instead of discarding historical revisions or command
    identities. Search snapshots alone expire, with an explicit expired error.
    """

    def __init__(
        self,
        *,
        max_pages: int = 1000,
        max_history: int = 10_000,
        max_commands: int = 10_000,
        max_searches: int = 64,
        search_ttl: float = 1800,
    ):
        if min(max_pages, max_history, max_commands, max_searches, search_ttl) <= 0:
            raise ValueError("Workspace limits must be positive")
        self._lock = RLock()
        self._pages: dict[str, dict] = {}
        self._public_ids: dict[str, int] = {}
        self._indexed_pages: dict[int, str] = {}
        self._qualified_pages: dict[tuple[str, str], str] = {}
        self._index_sealed = False
        self._history: dict[tuple[str, int], dict] = {}
        self._commands: dict[tuple[str, str], dict] = {}
        self._transactions: dict[str, dict] = {}
        self._policies: dict[str, dict] = {}
        self._policy_history: dict[tuple[str, int], dict] = {}
        self._searches: OrderedDict[str, dict] = OrderedDict()
        self._occurrences: dict[str, tuple[str, dict]] = {}
        self.max_pages = max_pages
        self.max_history = max_history
        self.max_commands = max_commands
        self.max_searches = max_searches
        self.search_ttl = search_ttl

    def register_page(self, snapshot: dict) -> str:
        """Host-only page import. IDs and reading order are supplied by the host."""
        with self._lock:
            if self._index_sealed:
                raise ToolError("index_frozen", "Pages cannot be registered after indexing")
            page = deepcopy(snapshot)
            required = {"page_id", "work_id", "chapter_id", "order", "regions"}
            if not required <= page.keys():
                raise ToolError(
                    "invalid_page",
                    "Page is missing stable resource IDs, order, or regions",
                )
            pid = page["page_id"]
            if not isinstance(pid, str) or not pid or pid in self._pages:
                raise ToolError("invalid_page", "Page ID must be nonempty and unique")
            if (
                len(self._pages) >= self.max_pages
                or len(self._history) >= self.max_history
            ):
                raise ToolError(
                    "resource_limit", "Workspace page/history capacity reached"
                )
            if not isinstance(page["order"], (int, float)):
                raise ToolError("invalid_page", "Page reading order must be numeric")
            page.setdefault("revision", 1)
            page.setdefault("policy_version", 1)
            if any(
                type(page[key]) is not int or page[key] < 1
                for key in ("revision", "policy_version")
            ):
                raise ToolError(
                    "invalid_page",
                    "Revision and policy version must be positive integers",
                )
            page.setdefault("original_asset", None)
            if page["original_asset"]:
                source = PurePosixPath(str(page["original_asset"]).replace("\\", "/"))
                page.setdefault("name", source.name)
                page.setdefault("folder", ".")
            page.setdefault("base_asset", None)
            ids = set()
            if len(page["regions"]) > 5000:
                raise ToolError("resource_limit", "Page exceeds the region limit")
            for region in page["regions"]:
                rid = region.get("region_id")
                if not isinstance(rid, str) or not rid or rid in ids:
                    raise ToolError(
                        "invalid_region", "Region IDs must be nonempty unique strings"
                    )
                ids.add(rid)
                region.setdefault("version", 1)
                if type(region["version"]) is not int or region["version"] < 1:
                    raise ToolError(
                        "invalid_region", "Region version must be a positive integer"
                    )
                doc = text.document(region)
                text.boundaries(text.visible(doc))
                source = text.text_of(region, "source")
                if source is not None:
                    text.boundaries(source)
                if region.get("translation_rich") is not None:
                    text.store_document(region, doc)
            self._pages[pid] = page
            self._history[pid, page["revision"]] = deepcopy(page)
            return pid

    def seal_index(self) -> None:
        """Freeze workspace-global public IDs after all host pages are loaded."""
        with self._lock:
            if self._index_sealed:
                return
            qualified = {}
            identities = {}
            for pid, page in self._pages.items():
                try:
                    identity = PageByName.model_validate(
                        {"folder": page.get("folder"), "name": page.get("name")}
                    )
                except ValidationError as exc:
                    raise ToolError(
                        "invalid_page_identity",
                        "Each page requires a relative folder and full source filename",
                    ) from exc
                key = identity.folder, identity.name
                if key in qualified:
                    raise ToolError(
                        "duplicate_page_identity",
                        "More than one registered page has the same folder and filename",
                        {"folder": identity.folder, "name": identity.name},
                    )
                qualified[key] = pid
                identities[pid] = key
            ordered = sorted(identities, key=lambda pid: page_order(*identities[pid]))
            self._public_ids = {pid: index for index, pid in enumerate(ordered, 1)}
            self._indexed_pages = {index: pid for pid, index in self._public_ids.items()}
            self._qualified_pages = qualified
            self._index_sealed = True

    def public_identity(self, page_id: str) -> dict:
        with self._lock:
            self.seal_index()
            if page_id not in self._public_ids:
                raise ToolError("page_not_found", "Page is not registered")
            page = self._pages[page_id]
            return {"id": self._public_ids[page_id], "folder": page["folder"], "name": page["name"]}

    def resolve_page(self, ctx: ToolContext, ref: PageRef | dict) -> str:
        with self._lock:
            self._active(ctx)
            try:
                parsed = _PAGE_REF.validate_python(ref)
            except ValidationError as exc:
                raise ToolError(
                    "invalid_page_reference",
                    "Use exactly {id: positive integer} or {folder: relative directory, name: full filename}",
                ) from exc
            self.seal_index()
            pid = (
                self._indexed_pages.get(parsed.id)
                if isinstance(parsed, PageById)
                else self._qualified_pages.get((parsed.folder, parsed.name))
            )
            if pid is None:
                raise ToolError("page_not_found", "Page is not registered")
            return pid

    def resolve_scope(self, ctx: ToolContext, scope: ToolScope) -> ResourceScope:
        with self._lock:
            self._active(ctx)
            try:
                parsed = ToolScope.model_validate(scope)
            except ValidationError as exc:
                raise ToolError("invalid_scope", "Invalid page or folder scope") from exc
            self.seal_index()
            selected = None
            if parsed.pages is not None:
                if len(parsed.pages) > self.max_pages:
                    raise ToolError("invalid_scope", "Scope exceeds workspace page limit")
                selected = [self.resolve_page(ctx, ref) for ref in parsed.pages]
                if len(set(selected)) != len(selected):
                    raise ToolError("invalid_scope", "Scope must not contain duplicate pages")
                selected = set(selected)
            page_ids = []
            for pid in self._public_ids:
                if selected is not None and pid not in selected:
                    continue
                folder = self._pages[pid]["folder"]
                if parsed.folder is not None and folder != parsed.folder:
                    if not parsed.recursive or (
                        parsed.folder != "." and not folder.startswith(parsed.folder + "/")
                    ):
                        continue
                page_ids.append(pid)
            return ResourceScope(page_ids=page_ids)

    @staticmethod
    def _active(ctx: ToolContext) -> None:
        if ctx.cancelled.is_set():
            raise ToolError("cancelled", "Task has been cancelled")

    def _authorize(
        self,
        ctx: ToolContext,
        capability: str,
        resource: str,
        region_id: str | None = None,
    ) -> None:
        self._active(ctx)
        grant = ctx.grant
        seen = set()
        while grant is not None:
            if id(grant) in seen:
                raise ToolError(
                    "permission_denied", "Invalid cyclic permission ancestry"
                )
            seen.add(id(grant))
            if resource not in getattr(grant, capability):
                raise ToolError(
                    "permission_denied", f"Resource is outside the authorized {capability} scope"
                )
            if (
                region_id is not None
                and resource in grant.region_ids
                and region_id not in grant.region_ids[resource]
            ):
                raise ToolError(
                    "permission_denied",
                    f"Region {region_id} is outside the editable scope",
                )
            grant = grant.parent


    def page(self, ctx: ToolContext, page_id: str, revision: int | None = None) -> dict:
        with self._lock:
            self._active(ctx)
            current = self._pages.get(page_id)
            if current is None:
                raise ToolError("page_not_found", "Page is not registered")
            value = (
                current if revision is None else self._history.get((page_id, revision))
            )
            if value is None:
                raise ToolError(
                    "revision_unavailable",
                    "Requested historical revision is unavailable",
                )
            return deepcopy(value)

    def _scope_pages(self, ctx: ToolContext, scope: ResourceScope) -> list[dict]:
        self._active(ctx)
        self.seal_index()
        if scope.page_ids is not None:
            if len(scope.page_ids) > self.max_pages or len(set(scope.page_ids)) != len(
                scope.page_ids
            ):
                raise ToolError(
                    "invalid_scope",
                    "Page IDs must be unique and within workspace limits",
                )
            for pid in scope.page_ids:
                if pid not in self._pages:
                    raise ToolError("page_not_found", "Page is not registered")
        return sorted(
            (
                page
                for pid, page in self._pages.items()
                if (scope.page_ids is None or pid in scope.page_ids)
                and (scope.work_id is None or page["work_id"] == scope.work_id)
                and (scope.chapter_id is None or page["chapter_id"] == scope.chapter_id)
            ),
            key=lambda p: self._public_ids[p["page_id"]],
        )

    def pages(self, ctx: ToolContext, scope: ResourceScope) -> list[dict]:
        with self._lock:
            return deepcopy(self._scope_pages(ctx, scope))

    def _replay(self, ctx: ToolContext, command_id: str, payload: dict):
        self._active(ctx)
        if not command_id or len(command_id) > 200:
            raise ToolError(
                "invalid_command_id", "Command ID must contain 1–200 characters"
            )
        key = ctx.task_id, command_id
        signature = _fingerprint(payload)
        existing = self._commands.get(key)
        if existing is not None:
            for capability, pid, rid in existing["permissions"]:
                self._authorize(ctx, capability, pid, rid)
            if existing["fingerprint"] != signature:
                raise ToolError(
                    "idempotency_conflict",
                    "Command ID has already been used with different content",
                )
            return deepcopy(existing["result"])
        if len(self._commands) >= self.max_commands:
            raise ToolError("resource_limit", "Workspace command capacity reached")
        return None

    def _record(self, ctx, command_id, payload, permissions, result):
        self._commands[ctx.task_id, command_id] = {
            "fingerprint": _fingerprint(payload),
            "permissions": permissions,
            "result": deepcopy(result),
        }

    @staticmethod
    def _region(page, rid):
        for region in page["regions"]:
            if region["region_id"] == rid:
                return region
        raise ToolError("region_not_found", f"Unknown region {rid}")

    def _commit(self, ctx, page, candidate, before, permissions, payload, command_id):
        if len(self._history) >= self.max_history:
            raise ToolError("resource_limit", "Workspace history capacity reached")
        for capability, pid, rid in permissions:
            self._authorize(ctx, capability, pid, rid)
        versions = {}
        for rid in before:
            region = self._region(candidate, rid)
            region["version"] = before[rid]["version"] + 1
            versions[rid] = region["version"]
        candidate["revision"] = page["revision"] + 1
        transaction_id = uuid4().hex
        pid = page["page_id"]
        result = {
            "status": "accepted",
            "page_id": pid,
            "revision": candidate["revision"],
            "region_versions": versions,
            "policy_version": candidate["policy_version"],
            "transaction_id": transaction_id,
            "render_status": "not_rendered",
            "render_ticket": {"page_id": pid, "revision": candidate["revision"]},
        }
        self._transactions[transaction_id] = {
            "task_id": ctx.task_id,
            "page_id": pid,
            "before": before,
            "versions": versions,
            "permissions": permissions,
            "policy_version": page["policy_version"],
        }
        self._pages[pid] = candidate
        self._history[pid, candidate["revision"]] = deepcopy(candidate)
        if command_id is not None:
            self._record(ctx, command_id, payload, permissions, result)
        return result

