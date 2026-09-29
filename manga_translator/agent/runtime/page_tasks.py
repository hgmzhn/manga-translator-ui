"""Bounded, host-configured page delegation with owned, versioned outcomes."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from ..domain.tool_models import ToolContext


_TERMINAL = frozenset({"completed", "conflict", "failed", "cancelled"})
_STATUSES = ("queued", "running", "completed", "conflict", "failed", "cancelled")
# HTTP hooks on the host-owned child client resolve the currently executing
# child, never the manager turn that happened to create that client.
current_page_trace: ContextVar[Any] = ContextVar("current_page_trace", default=None)


class _CancellationEvent(threading.Event):
    """Child-local cancellation also observes its parent's live signal."""

    def __init__(self, parent: threading.Event) -> None:
        super().__init__()
        self._parent = parent

    def is_set(self) -> bool:
        return super().is_set() or self._parent.is_set()


@dataclass(slots=True)
class _PageTask:
    task_id: str
    parent: ToolContext
    context: ToolContext
    page_ids: list[str]
    pages: list[dict[str, Any]]
    work_ids: dict[str, str]
    chapter_ids: dict[str, str | None]
    base_revisions: dict[str, int]
    policy_versions: dict[str, int]
    editable_regions: dict[str, list[str] | Literal["all"]]
    editable_region_numbers: dict[str, list[int] | Literal["all"]]
    requirements: str
    trace: Any = None
    status: str = "queued"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    future: asyncio.Task[None] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class PageTaskRuntime:
    """Schedule one child Agent per assignment group on one asyncio loop.

    Supply exactly one configured native Model or model_factory(child_context).
    A factory may be async; any returned Model/client remains host-owned.
    One revise_pages call creates one task spanning all supplied pages;
    concurrency and pending limits count tasks, never individual pages.
    close() cancels and joins all executions, but does not close shared models,
    the renderer, or the workspace. Host cancellation is available through
    cancel_tasks(); a small monitor bridges inherited threading.Event signals
    to asyncio Task.cancel even while a provider request is in flight.
    """

    def __init__(
        self,
        *,
        model: Model | None = None,
        model_factory: Callable[[ToolContext], Model | Awaitable[Model]] | None = None,
        max_concurrency: int = 4,
        max_pending: int = 128,
        model_settings: dict | None = None,
        on_debug_event: Callable[[dict], None] | None = None,
        debug_secret: str = "",
    ) -> None:
        if (model is None) == (model_factory is None):
            raise ValueError("Supply exactly one model or model_factory")
        if isinstance(model, str):
            raise TypeError("Supply a configured native Model, not a model name")
        if model_factory is not None and not callable(model_factory):
            raise TypeError("model_factory must be callable")
        if max_concurrency < 1 or max_pending < max_concurrency:
            raise ValueError("Require 1 <= max_concurrency <= max_pending")
        self._model = model
        self._model_factory = model_factory
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_pending = max_pending
        self._tasks: dict[str, _PageTask] = {}
        self._commands: dict[tuple[int, str, str], tuple[str, list[str]]] = {}
        self._monitor: asyncio.Task[None] | None = None
        self._closed = False
        self._model_settings = dict(model_settings or {})
        self._on_debug_event = on_debug_event
        self._debug_secret = debug_secret

    @property
    def closed(self) -> bool:
        return self._closed

    def _make_trace(self, record: _PageTask):
        from ..providers.request_debug import ContextTrace

        observer = self._on_debug_event

        def publish(event):
            observer({**event, "data": {
                **record.trace.snapshot(self._scope(record)),
                **event["data"], "agent_role": "page",
                "task_id": record.task_id, "parent_task_id": record.parent.task_id,
            }})

        return ContextTrace(publish if observer is not None else None, secret=self._debug_secret)

    def _emit_status(self, record: _PageTask) -> None:
        from ..tools.builtin.shared import public_payload

        record.trace.add("task_status", public_payload(record.context, self._item(record)))

    @staticmethod
    def _scope(record: _PageTask) -> dict:
        scope = {
            "pages": record.pages,
            "assignments": [
                {
                    "page": page,
                    "editable_regions": record.editable_region_numbers[page_id],
                }
                for page_id, page in zip(record.page_ids, record.pages)
            ],
        }
        # A multi-page task has no single current/primary page. These aliases
        # remain only for consumers displaying genuinely single-page tasks.
        if len(record.page_ids) == 1:
            page_id = record.page_ids[0]
            scope.update(
                page=record.pages[0],
                editable_regions=record.editable_region_numbers[page_id],
            )
        return scope

    @staticmethod
    def _allowed(grant: Any, permission: str, page_id: str) -> bool:
        while grant is not None:
            if page_id not in getattr(grant, permission):
                return False
            grant = grant.parent
        return True

    @staticmethod
    def _regions_allowed(grant: Any, page_id: str, regions: set[str] | Literal["all"]) -> bool:
        while grant is not None:
            allowed = grant.region_ids.get(page_id)
            if allowed is not None and (regions == "all" or not regions <= allowed):
                return False
            grant = grant.parent
        return True

    @classmethod
    def _check_permissions(cls, record: _PageTask) -> None:
        from ..domain.tool_models import ToolError

        grant = record.context.grant
        for page_id in record.page_ids:
            assignment = record.editable_regions[page_id]
            regions = "all" if assignment == "all" else set(assignment)
            if not cls._regions_allowed(grant, page_id, regions):
                raise ToolError("permission_denied", "委派区域授权已撤销")
            for permission in ("layout_pages", "translation_pages", "geometry_pages"):
                if page_id in getattr(grant, permission) and not cls._allowed(
                    grant, permission, page_id
                ):
                    raise ToolError("permission_denied", "委派页面修改权限已撤销")

    @classmethod
    def _check_initial_versions(cls, record: _PageTask) -> None:
        for page_id in record.page_ids:
            snapshot = record.context.workspace.page(record.context, page_id)
            cls._check_versions(
                snapshot, record.base_revisions[page_id], record.policy_versions[page_id]
            )

    @staticmethod
    def _check_versions(snapshot: dict, revision: int, policy_version: int) -> None:
        from ..domain.tool_models import ToolError

        if snapshot["revision"] != revision:
            raise ToolError(
                "revision_conflict",
                "委派页面基准版本已变化",
                {"current_revision": snapshot["revision"]},
            )
        if snapshot["policy_version"] != policy_version:
            raise ToolError(
                "policy_conflict",
                "委派页面规则版本已变化",
                {"current_policy_version": snapshot["policy_version"]},
            )

    async def revise_pages(
        self,
        ctx: ToolContext,
        page_ids: list[str],
        requirements: str,
        editable_regions: dict[str, list[str] | Literal["all"]],
        policy_versions: dict[str, int],
        base_revisions: dict[str, int],
        command_id: str,
    ) -> dict:
        from ..domain.tool_models import AccessGrant, ToolError

        if self._closed:
            raise ToolError("runtime_closed", "页面任务运行时已关闭")
        if ctx.cancelled.is_set():
            raise ToolError("cancelled", "父任务已取消")
        if (
            not command_id
            or not isinstance(requirements, str)
            or not requirements.strip()
        ):
            raise ToolError("invalid_request", "要求和命令 ID 不能为空")
        if not 1 <= len(page_ids) <= 16 or len(page_ids) != len(set(page_ids)):
            raise ToolError("invalid_scope", "每个任务必须指定 1 至 16 个不重复页面")
        targets = set(page_ids)
        if any(
            set(mapping) != targets
            for mapping in (editable_regions, policy_versions, base_revisions)
        ):
            raise ToolError(
                "invalid_request", "每页必须显式提供可编辑区域、基准版本和规则版本"
            )
        if any(
            type(version) is not int or version < 0
            for mapping in (policy_versions, base_revisions)
            for version in mapping.values()
        ):
            raise ToolError("invalid_request", "版本必须是非负整数")
        fingerprint = json.dumps(
            [page_ids, requirements, editable_regions, policy_versions, base_revisions],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        command_key = (id(ctx.workspace), ctx.command_scope_id, command_id)
        previous = self._commands.get(command_key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ToolError(
                    "idempotency_conflict", "同一命令 ID 不能用于不同的页面任务请求"
                )
            records = self._owned(ctx, previous[1])
            for record in records:
                self._check_permissions(record)
                for page_id in record.page_ids:
                    if not self._regions_allowed(
                        ctx.grant, page_id,
                        "all" if editable_regions[page_id] == "all" else set(editable_regions[page_id])
                    ):
                        raise ToolError("permission_denied", "可编辑区域授权已撤销")
            return {
                "task_ids": previous[1].copy(),
                "status": "accepted",
                "replayed": True,
            }
        active = sum(task.status not in _TERMINAL for task in self._tasks.values())
        if active + 1 > self._max_pending:
            raise ToolError(
                "capacity_exceeded",
                "页面任务队列已满",
                {"available": self._max_pending - active},
            )

        # Admission has no awaits: all pages are checked before any is scheduled.
        snapshots = {}
        pages = []
        region_ids = {}
        for page_id in page_ids:
            snapshot = ctx.workspace.page(ctx, page_id)
            self._check_versions(
                snapshot, base_revisions[page_id], policy_versions[page_id]
            )
            assignment = editable_regions[page_id]
            if assignment != "all" and not isinstance(assignment, list):
                raise ToolError("invalid_scope", "可编辑范围必须是区域列表或 all")
            regions = "all" if assignment == "all" else set(assignment)
            actual = {region["region_id"] for region in snapshot["regions"]}
            if regions != "all" and (len(regions) != len(assignment) or not regions <= actual):
                raise ToolError("invalid_scope", "可编辑区域不存在或重复")
            if not self._regions_allowed(ctx.grant, page_id, regions):
                raise ToolError("permission_denied", "委派区域超出父任务授权")
            if regions and not any(
                self._allowed(ctx.grant, permission, page_id)
                for permission in (
                    "layout_pages",
                    "translation_pages",
                    "geometry_pages",
                )
            ):
                raise ToolError("permission_denied", "父任务没有目标页修改权限")
            snapshots[page_id] = snapshot
            pages.append(dict(ctx.workspace.public_identity(page_id)))
            if regions != "all":
                region_ids[page_id] = regions

        def granted_pages(permission: str) -> set[str]:
            return {
                page_id for page_id in page_ids
                if (editable_regions[page_id] == "all" or region_ids[page_id])
                and self._allowed(ctx.grant, permission, page_id)
            }

        grant = AccessGrant(
            layout_pages=granted_pages("layout_pages"),
            translation_pages=granted_pages("translation_pages"),
            geometry_pages=granted_pages("geometry_pages"),
            style_scopes=set(), region_ids=region_ids, parent=ctx.grant,
        )
        task_id = uuid.uuid4().hex
        child = replace(
            ctx, task_id=task_id, grant=grant,
            task_page_ids=list(page_ids), command_generation="",
            cancelled=_CancellationEvent(ctx.cancelled),
            observed_revisions={}, read_snapshots={}, read_policies={},
            command_payloads={}, resolved_edit_commands={}, transaction_results={}, runtime=self,
        )
        record = _PageTask(
            task_id=task_id, parent=ctx, context=child,
            page_ids=list(page_ids), pages=pages,
            work_ids={pid: snapshots[pid]["work_id"] for pid in page_ids},
            chapter_ids={pid: snapshots[pid]["chapter_id"] for pid in page_ids},
            base_revisions=dict(base_revisions), policy_versions=dict(policy_versions),
            editable_regions={
                pid: "all" if editable_regions[pid] == "all" else sorted(region_ids[pid])
                for pid in page_ids
            },
            editable_region_numbers={
                pid: "all" if editable_regions[pid] == "all" else [
                    number for number, region in enumerate(snapshots[pid]["regions"], 1)
                    if region["region_id"] in region_ids[pid]
                ]
                for pid in page_ids
            },
            requirements=requirements,
        )
        ids = [task_id]
        self._commands[command_key] = (fingerprint, ids)
        self._tasks[task_id] = record
        record.trace = self._make_trace(record)
        self._emit_status(record)
        record.future = asyncio.create_task(self._execute(record), name=f"manga-page-{task_id}")
        record.future.add_done_callback(lambda future: self._settle(record, future))
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.create_task(
                self._watch_cancellation(), name="manga-page-cancellation"
            )
        return {"task_ids": ids.copy(), "status": "accepted", "replayed": False}

    async def _execute(self, record: _PageTask) -> None:
        from ..agents.page import run
        from ..domain.tool_models import ToolError

        trace_token = current_page_trace.set(record.trace)
        try:
            async with self._semaphore:
                if record.context.cancelled.is_set():
                    raise asyncio.CancelledError
                self._check_permissions(record)
                self._check_initial_versions(record)
                record.status = "running"
                record.started_at = time.time()
                self._emit_status(record)
                model = self._model
                if self._model_factory is not None:
                    model = self._model_factory(record.context)
                    if inspect.isawaitable(model):
                        model = await model
                # A slow factory cannot silently move the initial version boundary.
                self._check_permissions(record)
                self._check_initial_versions(record)
                result = await run(
                    record.context, record.requirements, model,
                    model_settings=self._model_settings,
                    trace=record.trace,
                )
                if record.context.cancelled.is_set():
                    raise asyncio.CancelledError
                self._check_permissions(record)
                outputs = {page.id: page for page in result.pages}
                if (len(outputs) != len(result.pages)
                        or set(outputs) != {page["id"] for page in record.pages}):
                    raise ToolError("invalid_result", "任务结果必须完整且不重复地包含全部委派页面")
                accepted = []
                # Accept the task only after every page passes. Earlier draft
                # writes remain in the workspace even when a later page fails.
                for page_id, identity in zip(record.page_ids, record.pages):
                    current = record.context.workspace.page(record.context, page_id)
                    observed = record.context.observed_revisions.get(page_id)
                    if observed is None:
                        raise ToolError(
                            "unobserved_revision", "最终页面未经本任务观察", {"page": identity}
                        )
                    self._check_versions(current, observed, record.policy_versions[page_id])
                    accepted.append({
                        **outputs[identity["id"]].model_dump(),
                        "page_id": page_id, "revision": current["revision"],
                    })
                record.result = {"pages": accepted}
                record.status = "completed"
        except asyncio.CancelledError:
            record.context.cancelled.set()
            record.status = "cancelled"
            record.error = {
                "code": "cancelled",
                "message": "页面任务已取消；已提交草稿不会自动撤销",
            }
        except ToolError as error:
            code = error.code
            record.status = (
                "cancelled"
                if code == "cancelled"
                else "conflict"
                if "conflict" in code or code == "stale_revision"
                else "failed"
            )
            record.error = {
                "code": code,
                "message": error.message,
                "details": error.details,
            }
        except Exception as error:
            record.trace.add("error", {
                "exception_type": type(error).__name__, "message": str(error),
            })
            # Provider exceptions can contain credentials, request bodies or host
            # paths. Only a stable safe error crosses the model tool boundary.
            record.status = "failed"
            record.error = {
                "code": "execution_failed",
                "message": "页面模型执行失败，未产生可接受结果",
            }
        finally:
            record.trace.finish_response("interrupted" if record.status != "completed" else "complete")
            record.finished_at = time.time()
            self._emit_status(record)
            record.done.set()
            current_page_trace.reset(trace_token)

    def _settle(self, record: _PageTask, future: asyncio.Task[None]) -> None:
        # Cancelling before the coroutine's first instruction skips its finally.
        if not record.done.is_set():
            if future.cancelled():
                record.status = "cancelled"
                record.error = {
                    "code": "cancelled",
                    "message": "页面任务在开始执行前已取消",
                }
            else:
                future.exception()
                record.status = "failed"
                record.error = {
                    "code": "execution_failed",
                    "message": "页面执行初始化失败",
                }
            record.finished_at = time.time()
            self._emit_status(record)
            record.done.set()

    async def _watch_cancellation(self) -> None:
        # threading.Event has no asyncio notification interface. One bounded
        # monitor bridges it; result waiters below use actual completion events.
        while not self._closed:
            active = [
                record
                for record in self._tasks.values()
                if record.status not in _TERMINAL
            ]
            if not active:
                return
            for record in active:
                if (
                    record.context.cancelled.is_set()
                    and record.future is not None
                    and record.future.cancelling() == 0
                ):
                    record.future.cancel()
            await asyncio.sleep(0.1)

    def _owned(self, ctx: ToolContext, task_ids: list[str]) -> list[_PageTask]:
        from ..domain.tool_models import ToolError

        if len(task_ids) != len(set(task_ids)):
            raise ToolError("invalid_request", "任务 ID 不能重复")
        if task_ids:
            records = []
            for task_id in task_ids:
                record = self._tasks.get(task_id)
                if (
                    record is None
                    or record.parent.workspace is not ctx.workspace
                    or record.parent.task_id != ctx.task_id
                ):
                    raise ToolError("task_not_found", "找不到当前父任务拥有的子任务")
                records.append(record)
        else:
            records = [
                record
                for record in self._tasks.values()
                if record.parent.workspace is ctx.workspace
                and record.parent.task_id == ctx.task_id
            ]
        return records

    async def get_task_results(
        self,
        ctx: ToolContext,
        task_ids: list[str],
        wait: bool = False,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        from ..domain.tool_models import ToolError

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ToolError("invalid_request", "分页大小必须介于 1 和 100")
        records = self._owned(ctx, task_ids)
        selection = hashlib.sha256(
            json.dumps(
                [id(ctx.workspace), ctx.task_id, task_ids], separators=(",", ":")
            ).encode()
        ).hexdigest()[:16]
        offset = 0
        if cursor is not None:
            try:
                prefix, position = cursor.split(":", 1)
                offset = int(position)
                if prefix != selection or offset < 0 or offset > len(records):
                    raise ValueError
            except (AttributeError, TypeError, ValueError):
                raise ToolError(
                    "invalid_cursor", "结果游标无效或不属于当前查询"
                ) from None
        if wait and records:
            await asyncio.gather(*(record.done.wait() for record in records))
            self._owned(ctx, [record.task_id for record in records])
        counts = {status: 0 for status in _STATUSES}
        for record in records:
            counts[record.status] += 1
        items = [self._item(record) for record in records[offset : offset + limit]]
        end = offset + len(items)
        return {
            "items": items,
            "total": len(records),
            "counts": counts,
            "next_cursor": f"{selection}:{end}" if end < len(records) else None,
        }

    @classmethod
    def _item(cls, record: _PageTask) -> dict:
        return deepcopy({
            **cls._scope(record),
            "task_id": record.task_id,
            "parent_task_id": record.parent.task_id,
            "status": record.status,
            "requirements": record.requirements,
            "result": record.result,
            "error": record.error,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        })

    async def cancel_tasks(
        self, ctx: ToolContext, task_ids: list[str] | None = None
    ) -> dict:
        """Host cancellation; [] or None cancels all this parent's active tasks."""
        records = self._owned(ctx, task_ids or [])
        futures = []
        for record in records:
            if record.status not in _TERMINAL and record.future is not None:
                record.context.cancelled.set()
                if record.future.cancelling() == 0:
                    record.future.cancel()
                futures.append(record.future)
        if futures:
            await asyncio.gather(*futures, return_exceptions=True)
        return await self.get_task_results(ctx, [record.task_id for record in records])

    async def close(self) -> None:
        """Cancel/join owned work; retain outcomes for subsequent host queries."""
        self._closed = True
        futures = []
        for record in self._tasks.values():
            if record.future is not None and not record.future.done():
                record.context.cancelled.set()
                if record.future.cancelling() == 0:
                    record.future.cancel()
                futures.append(record.future)
        if self._monitor is not None:
            self._monitor.cancel()
            futures.append(self._monitor)
        if futures:
            await asyncio.gather(*futures, return_exceptions=True)
