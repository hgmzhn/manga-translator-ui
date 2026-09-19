"""Native, isolated PydanticAI execution for one delegated page."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..prompts import load_prompt

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from ..domain.tool_models import ToolContext


class PageResult(BaseModel):
    """A visually checked candidate, not an export or acceptance decision."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0, strict=True)
    issues: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        max_length=100
    )


async def run(ctx: ToolContext, requirements: str, model: Model) -> PageResult:
    """Run a fresh native conversation; model and its HTTP client are host-owned."""
    from pydantic_ai import Agent
    from pydantic_ai.models import Model
    from pydantic_ai.usage import UsageLimits

    from ..domain.tool_models import ToolContext, ToolError
    from ..tools import create_toolset
    from ..tools.validation import EditValidationFeedback

    if not isinstance(model, Model):
        raise ToolError("invalid_model", "宿主必须提供已配置的 PydanticAI Model 实例")
    if ctx.cancelled.is_set():
        raise ToolError("cancelled", "页面任务已取消")
    targets = list(ctx.grant.region_ids)
    if len(targets) != 1:
        raise ToolError("invalid_scope", "页面运行上下文必须且只能绑定一个目标页")
    page_id = targets[0]
    snapshot = ctx.workspace.page(ctx, page_id)
    identity = ctx.workspace.public_identity(page_id)
    policies = []
    for scope_id in dict.fromkeys((snapshot["work_id"], snapshot["chapter_id"])):
        if scope_id is None:
            continue
        try:
            policy = ctx.workspace.read_style_policy(
                ctx, scope_id, version=snapshot["policy_version"]
            )
        except ToolError as error:
            if error.code != "policy_not_found":
                raise
            policy = {
                "scope_id": scope_id,
                "status": "policy_not_found",
            }
        policies.append({key: value for key, value in policy.items() if key != "version"})
    prompt = json.dumps(
        {
            "requirements": requirements,
            "task_id": ctx.task_id,
            "work_id": snapshot["work_id"],
            "chapter_id": snapshot["chapter_id"],
            "page": identity,
            "style_policies": policies,
            "editable_region_ids": sorted(ctx.grant.region_ids[page_id]),
            "translation_editing_authorized": page_id in ctx.grant.translation_pages,
            "geometry_editing_authorized": page_id in ctx.grant.geometry_pages,
        },
        ensure_ascii=False,
    )
    toolset = create_toolset("page")
    agent = Agent(
        model=model,
        deps_type=ToolContext,
        output_type=PageResult,
        instructions=load_prompt("page") + "\n\n" + load_prompt("skills"),
        toolsets=[toolset],
        capabilities=[EditValidationFeedback([
            toolset.tools[name] for name in ("edit_regions", "create_regions", "delete_regions", "edit_rich_text")
        ])],
        name="manga_page",
    )
    # No shared history or provider conversation identifiers: this run owns all
    # native messages, including every observed image and tool response.
    async with agent:
        result = await agent.run(
            prompt,
            deps=ctx,
            usage_limits=UsageLimits(request_limit=30, tool_calls_limit=100),
        )
    if ctx.cancelled.is_set():
        raise ToolError("cancelled", "页面任务已取消")
    output = result.output
    if output.id != identity["id"]:
        raise ToolError("invalid_result", "页面结果引用了其他页面")
    current = ctx.workspace.page(ctx, page_id)
    observed = ctx.observed_revisions.get(page_id)
    if observed is None:
        raise ToolError("unobserved_revision", "最终页面未经本任务实际渲染观察")
    if observed != current["revision"]:
        raise ToolError(
            "revision_conflict",
            "页面结果已过期",
            {"current_revision": current["revision"]},
        )
    return output
