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
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from ..domain.tool_models import ToolContext


_TERMINAL = frozenset({"completed", "conflict", "failed", "cancelled"})
_STATUSES = ("queued", "running", "completed", "conflict", "failed", "cancelled")


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
    page_id: str
    work_id: str
    chapter_id: str
    base_revision: int
    policy_version: int
    requirements: str
    status: str = "queued"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    future: asyncio.Task[None] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class PageTaskRuntime:
    """One asyncio-loop runtime; no credentials or model configuration discovery.

    Supply exactly one configured native Model or model_factory(child_context).
    A factory may be async; any returned Model/client remains host-owned.
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

    @staticmethod
    def _allowed(grant: Any, permission: str, page_id: str) -> bool:
        while grant is not None:
            if page_id not in getattr(grant, permission):
                return False
            grant = grant.parent
        return True

    @staticmethod
    def _regions_allowed(grant: Any, page_id: str, regions: set[str]) -> bool:
        while grant is not None:
            allowed = grant.region_ids.get(page_id)
            if allowed is not None and not regions <= allowed:
                return False
            grant = grant.parent
        return True

    @classmethod
    def _check_permissions(cls, record: _PageTask) -> None:
        from ..domain.tool_models import ToolError

        grant = record.context.grant
        regions = grant.region_ids[record.page_id]
        if not cls._regions_allowed(grant, record.page_id, regions):
            raise ToolError("permission_denied", "委派区域授权已撤销")
        for permission in ("layout_pages", "translation_pages", "geometry_pages"):
            if record.page_id in getattr(grant, permission) and not cls._allowed(
                grant, permission, record.page_id
            ):
                raise ToolError("permission_denied", "委派页面修改权限已撤销")

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
        editable_regions: dict[str, list[str]],
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
        if not page_ids or len(page_ids) != len(set(page_ids)):
            raise ToolError("invalid_scope", "页面列表不能为空且不能重复")
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
        command_key = (id(ctx.workspace), ctx.task_id, command_id)
        previous = self._commands.get(command_key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ToolError(
                    "idempotency_conflict", "同一命令 ID 不能用于不同的页面任务请求"
                )
            records = self._owned(ctx, previous[1])
            for record in records:
                self._check_permissions(record)
                if not self._regions_allowed(
                    ctx.grant, record.page_id, set(editable_regions[record.page_id])
                ):
                    raise ToolError("permission_denied", "可编辑区域授权已撤销")
            return {
                "task_ids": previous[1].copy(),
                "status": "accepted",
                "replayed": True,
            }
        active = sum(task.status not in _TERMINAL for task in self._tasks.values())
        if active + len(page_ids) > self._max_pending:
            raise ToolError(
                "capacity_exceeded",
                "页面任务队列已满",
                {"available": self._max_pending - active},
            )

        # Admission has no awaits: all pages are checked before any is scheduled.
        prepared: list[_PageTask] = []
        for page_id in page_ids:
            snapshot = ctx.workspace.page(ctx, page_id)
            self._check_versions(
                snapshot, base_revisions[page_id], policy_versions[page_id]
            )
            regions = set(editable_regions[page_id])
            actual = {region["region_id"] for region in snapshot["regions"]}
            if len(regions) != len(editable_regions[page_id]) or not regions <= actual:
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
            work_id = snapshot["work_id"]
            grant = AccessGrant(
                layout_pages={page_id}
                if regions and self._allowed(ctx.grant, "layout_pages", page_id)
                else set(),
                translation_pages={page_id}
                if regions and self._allowed(ctx.grant, "translation_pages", page_id)
                else set(),
                geometry_pages={page_id}
                if regions and self._allowed(ctx.grant, "geometry_pages", page_id)
                else set(),
                style_scopes=set(),
                region_ids={page_id: regions},
                parent=ctx.grant,
            )
            task_id = uuid.uuid4().hex
            child = replace(
                ctx,
                task_id=task_id,
                grant=grant,
                cancelled=_CancellationEvent(ctx.cancelled),
                observed_revisions={},
                read_snapshots={},
                read_policies={},
                command_payloads={},
                transaction_results={},
                runtime=self,
            )
            prepared.append(
                _PageTask(
                    task_id=task_id,
                    parent=ctx,
                    context=child,
                    page_id=page_id,
                    work_id=work_id,
                    chapter_id=snapshot["chapter_id"],
                    base_revision=base_revisions[page_id],
                    policy_version=policy_versions[page_id],
                    requirements=requirements,
                )
            )
        ids = [record.task_id for record in prepared]
        self._commands[command_key] = (fingerprint, ids)
        for record in prepared:
            self._tasks[record.task_id] = record
            record.future = asyncio.create_task(
                self._execute(record), name=f"manga-page-{record.task_id}"
            )
            record.future.add_done_callback(
                lambda future, item=record: self._settle(item, future)
            )
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.create_task(
                self._watch_cancellation(), name="manga-page-cancellation"
            )
        return {"task_ids": ids.copy(), "status": "accepted", "replayed": False}

    async def _execute(self, record: _PageTask) -> None:
        from ..agents.page import run
        from ..domain.tool_models import ToolError

        try:
            async with self._semaphore:
                if record.context.cancelled.is_set():
                    raise asyncio.CancelledError
                self._check_permissions(record)
                snapshot = record.context.workspace.page(record.context, record.page_id)
                self._check_versions(
                    snapshot, record.base_revision, record.policy_version
                )
                record.status = "running"
                record.started_at = time.time()
                model = self._model
                if self._model_factory is not None:
                    model = self._model_factory(record.context)
                    if inspect.isawaitable(model):
                        model = await model
                # A slow factory cannot silently move the initial version boundary.
                snapshot = record.context.workspace.page(record.context, record.page_id)
                self._check_versions(
                    snapshot, record.base_revision, record.policy_version
                )
                result = await run(record.context, record.requirements, model)
                if record.context.cancelled.is_set():
                    raise asyncio.CancelledError
                current = record.context.workspace.page(record.context, record.page_id)
                self._check_permissions(record)
                observed = record.context.observed_revisions.get(record.page_id)
                if observed is None:
                    raise ToolError("unobserved_revision", "最终页面未经本页面任务观察")
                self._check_versions(current, observed, record.policy_version)
                identity = record.context.workspace.public_identity(record.page_id)
                if result.id != identity["id"]:
                    raise ToolError("invalid_result", "页面结果引用了其他页面")
                record.result = {
                    **result.model_dump(),
                    "page_id": record.page_id,
                    "revision": current["revision"],
                }
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
        except Exception:
            # Provider exceptions can contain credentials, request bodies or host
            # paths. Only a stable safe error crosses the model tool boundary.
            record.status = "failed"
            record.error = {
                "code": "execution_failed",
                "message": "页面模型执行失败，未产生可接受结果",
            }
        finally:
            record.finished_at = time.time()
            record.done.set()

    @staticmethod
    def _settle(record: _PageTask, future: asyncio.Task[None]) -> None:
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

    @staticmethod
    def _item(record: _PageTask) -> dict:
        return {
            "task_id": record.task_id,
            "page_id": record.page_id,
            "work_id": record.work_id,
            "chapter_id": record.chapter_id,
            "status": record.status,
            "base_revision": record.base_revision,
            "policy_version": record.policy_version,
            "result": None
            if record.result is None
            else {**record.result, "issues": record.result["issues"].copy()},
            "error": None if record.error is None else record.error.copy(),
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }

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
