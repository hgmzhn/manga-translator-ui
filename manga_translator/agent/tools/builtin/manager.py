"""Workspace browsing, policy management, and delegated page task tools."""

from __future__ import annotations

import io
from copy import deepcopy
from typing import Annotated, Literal

from PIL import Image, ImageDraw
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from ...domain.tool_models import StylePolicyPatch, ToolContext, ToolError, ToolScope
from .shared import (
    CommandId,
    Limit,
    PageAssignment,
    Pages,
    TaskIds,
    Text,
    _PAGE_FIELDS,
    _baseline,
    _command,
    _guard,
    _image_return,
    _observe,
    _remember,
    _region_id,
    _service,
    _window,
)


@_guard
async def browse_workspace(
    ctx: RunContext[ToolContext],
    scope: ToolScope,
    resource_type: Literal["folder", "page", "task"] = "page",
    status: Literal["queued", "running", "completed", "conflict", "failed", "cancelled"]
    | None = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> dict:
    """按全局自然顺序浏览页面或相对文件夹；文件夹计数含子目录，任务仅返回本任务的子任务。"""
    resolved = ctx.deps.workspace.resolve_scope(ctx.deps, scope)
    pages = ctx.deps.workspace.pages(ctx.deps, resolved)
    if resource_type == "task":
        result = await _service(ctx, "runtime").get_task_results(
            ctx.deps, [], wait=False, limit=100
        )
        all_items = list(result["items"])
        while result.get("next_cursor"):
            result = await ctx.deps.runtime.get_task_results(
                ctx.deps, [], wait=False, cursor=result["next_cursor"], limit=100
            )
            all_items.extend(result["items"])
        page_ids = {ctx.deps.workspace.public_identity(p["page_id"])["id"] for p in pages}
        return _window(
            [
                item
                for item in all_items
                if page_ids.intersection(page["id"] for page in item.get("pages", []))
                and (status is None or item["status"] == status)
            ],
            cursor,
            limit,
        )
    if status is not None:
        raise ToolError("invalid_input", "status 筛选只适用于 resource_type=task")
    if resource_type == "page":
        items = [
            {key: page[key] for key in _PAGE_FIELDS if key in page} for page in pages
        ]
    else:
        counts = {}
        for page in pages:
            folder = ctx.deps.workspace.public_identity(page["page_id"])["folder"]
            ancestors = ["."]
            if folder != ".":
                parts = folder.split("/")
                ancestors.extend("/".join(parts[:end]) for end in range(1, len(parts) + 1))
            for ancestor in ancestors:
                counts[ancestor] = counts.get(ancestor, 0) + 1
        items = [{"folder": folder, "page_count": count} for folder, count in counts.items()]
    result = _window(items, cursor, limit)
    if resource_type == "page":
        selected = {item["page_id"] for item in result["items"]}
        for page in pages:
            if page["page_id"] in selected:
                _remember(ctx, page, [])
    return result


async def _contact_sheet(ctx, page_ids, revisions, view):
    if not 1 <= len(page_ids) <= 16 or len(set(page_ids)) != len(page_ids):
        raise ToolError("invalid_input", "联系表需要 1..16 个不同页面")
    tiles, records, observation_payloads = [], [], []
    for page_id in page_ids:
        payload = await _observe(
            ctx, page_id, revisions[page_id], view, max_dimension=640
        )
        observation_payloads.append(payload)
        with Image.open(io.BytesIO(payload["image"])) as image:
            tile = Image.new("RGB", (640, 680), "white")
            image.thumbnail((640, 640))
            tile.paste(image.convert("RGB"), ((640 - image.width) // 2, 40))
        identity = ctx.deps.workspace.public_identity(page_id)
        ImageDraw.Draw(tile).text(
            (8, 8), f"ID {identity['id']} | {identity['folder']}/{identity['name']}", fill="black"
        )
        tiles.append(tile)
        records.append(identity)
    columns = min(4, len(tiles))
    sheet = Image.new(
        "RGB", (columns * 640, ((len(tiles) + columns - 1) // columns) * 680), "#dddddd"
    )
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % columns) * 640, (index // columns) * 680))
    for page_id in page_ids:
        if ctx.deps.workspace.page(ctx.deps, page_id)["revision"] != revisions[page_id]:
            raise ToolError("conflict", "联系表生成期间页面已变化")
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return await _image_return(
        ctx,
        {
            "image": buffer.getvalue(),
            "mime_type": "image/png",
            "pages": records,
            "view": view,
            "notice": "联系表用于跨页概览；细微溢出请用 read_page 的 image_rendered 配合 crop 查看。",
        },
        observations=observation_payloads,
    )


@_guard
async def view_page_overview(
    ctx: RunContext[ToolContext],
    pages: Pages,
    view: Literal["original", "rendered"] = "rendered",
) -> ToolReturn | dict:
    """仅供概览的真实图片联系表，标注页面 ID 和文件路径；每次 1..16 页。

    超过 16 页须分批调用，例如 20 页拆成 16+4；细节需 read_page 查看。
    """
    workspace = ctx.deps.workspace
    page_ids = [workspace.resolve_page(ctx.deps, page) for page in pages]
    revisions = {pid: workspace.page(ctx.deps, pid)["revision"] for pid in page_ids}
    return await _contact_sheet(ctx, page_ids, revisions, view)


@_guard
async def read_style_policy(ctx: RunContext[ToolContext], scope_id: str) -> dict:
    """读取作品或章节当前规则；宿主记录更新所需基准。"""
    policy = ctx.deps.workspace.read_style_policy(ctx.deps, scope_id)
    ctx.deps.read_policies[scope_id] = deepcopy(policy)
    return policy


@_guard
async def update_style_policy(
    ctx: RunContext[ToolContext],
    scope_id: str,
    patch: StylePolicyPatch,
    command_id: CommandId,
) -> dict:
    """更新已读取的规则；宿主检测读取后冲突，已有运行继续固定原规则。"""
    def capture():
        policy = ctx.deps.read_policies.get(scope_id)
        if policy is None:
            raise ToolError("read_required", "请先 read_style_policy 读取目标规则")
        return policy["version"]

    expected = _command(ctx, command_id, {
        "op": "update_style_policy", "scope_id": scope_id,
        "patch": patch.model_dump(mode="json", exclude_unset=True),
    }, capture)
    return ctx.deps.workspace.update_style_policy(
        ctx.deps, scope_id, expected, patch, command_id
    )


@_guard
async def revise_pages(
    ctx: RunContext[ToolContext],
    assignments: Annotated[list[PageAssignment], Field(min_length=1, max_length=16)],
    requirements: Text,
    command_id: CommandId,
) -> dict:
    """每次为 1..16 页创建一个子 Agent，处理该批 assignments 的全部页面。

    超过 16 页须按任务分批，例如 20 页拆为 16+4。editable_regions 为编号列表、
    []（只读）或显式 all（整页），仍受父级授权约束；同一任务不按页拆代理。
    """
    page_ids = []
    for assignment in assignments:
        pid = ctx.deps.workspace.resolve_page(ctx.deps, assignment.page)
        if pid in page_ids:
            raise ToolError("invalid_input", "委派页面不能重复")
        page_ids.append(pid)

    def capture():
        snapshots = {pid: _baseline(ctx, pid) for pid in page_ids}
        editable_regions = {}
        for pid, assignment in zip(page_ids, assignments):
            if assignment.editable_regions == "all":
                editable_regions[pid] = "all"
                continue
            regions = [
                _region_id(snapshots[pid], region)
                for region in assignment.editable_regions
            ]
            if len(regions) != len(set(regions)):
                raise ToolError("invalid_scope", "可编辑区域不能重复")
            editable_regions[pid] = regions
        return {
            "editable_regions": editable_regions,
            "policy_versions": {pid: item["policy_version"] for pid, item in snapshots.items()},
            "base_revisions": {pid: item["revision"] for pid, item in snapshots.items()},
        }

    expected = _command(ctx, command_id, {
        "op": "revise_pages", "page_ids": page_ids,
        "assignments": [item.model_dump(mode="json") for item in assignments],
        "requirements": requirements,
    }, capture)
    return await _service(ctx, "runtime").revise_pages(
        ctx.deps, page_ids, requirements, expected["editable_regions"],
        expected["policy_versions"], expected["base_revisions"], command_id,
    )


@_guard
async def get_task_results(
    ctx: RunContext[ToolContext],
    task_ids: list[str],
    wait: bool = False,
    cursor: str | None = None,
    limit: Limit = 50,
) -> dict:
    """查询本任务子任务完整统计和分页结果；wait 等待状态通知而非忙轮询。"""
    return await _service(ctx, "runtime").get_task_results(
        ctx.deps, task_ids, wait, cursor, limit
    )


@_guard
async def review_page_results(
    ctx: RunContext[ToolContext], task_ids: TaskIds
) -> ToolReturn | dict:
    """查看已完成子任务候选联系表；若候选已不是当前版本则报告冲突。"""
    result = await _service(ctx, "runtime").get_task_results(
        ctx.deps, task_ids, limit=100
    )
    revisions = {}
    for item in result["items"]:
        if item["status"] != "completed" or not item.get("result"):
            raise ToolError(
                "not_ready", "只能复核已完成的子任务", {"task_id": item["task_id"]}
            )
        candidates = item["result"].get("pages") or [item["result"]]
        for candidate in candidates:
            current = ctx.deps.workspace.page(ctx.deps, candidate["page_id"])
            if current["revision"] != candidate["revision"]:
                raise ToolError("conflict", "候选页面在任务完成后已变化")
            revisions[candidate["page_id"]] = candidate["revision"]
    page_ids = list(revisions)
    if len(page_ids) <= 16:
        return await _contact_sheet(ctx, page_ids, revisions, "rendered")
    # Multiple task groups may exceed one contact sheet's 16-page limit.
    sheets = [
        await _contact_sheet(ctx, page_ids[start:start + 16], revisions, "rendered")
        for start in range(0, len(page_ids), 16)
    ]
    # A later render may await long enough for an earlier candidate to change.
    for page_id, revision in revisions.items():
        if ctx.deps.workspace.page(ctx.deps, page_id)["revision"] != revision:
            raise ToolError("conflict", "候选页面在复核期间已变化")
    return ToolReturn(
        return_value={
            "pages": [ctx.deps.workspace.public_identity(pid) for pid in page_ids],
            "view": "rendered", "sheet_count": len(sheets),
        },
        content=[part for sheet in sheets for part in (sheet.content or [])],
    )


