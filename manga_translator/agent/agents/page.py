"""One native PydanticAI conversation for a delegated multi-page task."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..prompts import load_prompt

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from ..domain.tool_models import ToolContext
    from ..providers.request_debug import ContextTrace


class PageResult(BaseModel):
    """A visually checked candidate, not an export or acceptance decision."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0, strict=True)
    issues: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        max_length=100
    )


class TaskResult(BaseModel):
    """One visually checked result for every page in the delegated scope."""

    model_config = ConfigDict(extra="forbid")

    pages: list[PageResult] = Field(min_length=1, max_length=16)


def _validate_result(ctx: ToolContext, identities: dict, output: TaskResult) -> TaskResult:
    """Check task coverage and full-page observations both before and after completion."""
    from ..domain.tool_models import ToolError

    if ctx.cancelled.is_set():
        raise ToolError("cancelled", "页面任务已取消")
    result_ids = [page.id for page in output.pages]
    expected_ids = {identity["id"] for identity in identities.values()}
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != expected_ids:
        raise ToolError(
            "invalid_result", "任务结果必须恰好包含全部目标页，不能重复、遗漏或越界",
            {"expected_ids": sorted(expected_ids), "result_ids": result_ids},
        )
    unread = []
    for page_id, identity in identities.items():
        current = ctx.workspace.page(ctx, page_id)
        if ctx.observed_revisions.get(page_id) != current["revision"]:
            unread.append({"id": identity["id"]})
    if unread:
        raise ToolError(
            "unobserved_revision",
            "请调用 read_page 读取所列页面的完整 image_rendered，每次 1..16 页，不传 crop；"
            "检查全部所列页面的完整最新渲染图后重新提交结果",
            {"pages_to_observe": unread, "fields": ["image_rendered"]},
        )
    return output


async def run(
    ctx: ToolContext,
    requirements: str,
    model: Model,
    *,
    model_settings: dict | None = None,
    trace: ContextTrace | None = None,
) -> TaskResult:
    """Run the complete task scope in one host-owned model conversation."""
    from pydantic_ai import Agent, AgentRunResultEvent, ModelRetry
    from pydantic_ai.messages import (
        FunctionToolCallEvent, FunctionToolResultEvent,
        PartStartEvent, PartDeltaEvent, PartEndEvent, RetryPromptPart,
    )
    from pydantic_ai.models import Model
    from pydantic_ai.usage import UsageLimits

    from ..domain.tool_models import ToolContext, ToolError
    from ..context.request_budget import ModelImageBudget
    from ..tools import create_toolset
    from ..tools.validation import EditValidationFeedback

    if not isinstance(model, Model):
        raise ToolError("invalid_model", "宿主必须提供已配置的 PydanticAI Model 实例")
    if ctx.cancelled.is_set():
        raise ToolError("cancelled", "页面任务已取消")
    targets = list(ctx.task_page_ids or ctx.grant.region_ids)
    if not 1 <= len(targets) <= 16:
        raise ToolError("invalid_scope", "子代理任务必须绑定 1 到 16 个目标页")
    identities = {}
    pages = []
    for page_id in targets:
        snapshot = ctx.workspace.page(ctx, page_id)
        identity = ctx.workspace.public_identity(page_id)
        identities[page_id] = identity
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
                continue
            policies.append({key: value for key, value in policy.items()
                             if key not in {"version", "scope_id", "source_scope_id"}})
        pages.append({
            "page": identity,
            "style_policies": policies,
            "editable_regions": (
                [number for number, region in enumerate(snapshot["regions"], 1)
                 if region["region_id"] in ctx.grant.region_ids[page_id]]
                if page_id in ctx.grant.region_ids else "all"
            ),
            "layout_editing_authorized": page_id in ctx.grant.layout_pages,
            "translation_editing_authorized": page_id in ctx.grant.translation_pages,
            "geometry_editing_authorized": page_id in ctx.grant.geometry_pages,
        })
    prompt = json.dumps(
        {
            "requirements": requirements,
            "pages": pages,
        },
        ensure_ascii=False,
    )
    toolset = create_toolset("page")
    agent = Agent(
        model=model,
        model_settings=model_settings,
        deps_type=ToolContext,
        output_type=TaskResult,
        retries=1,
        instructions=load_prompt("page") + "\n\n" + load_prompt("skills"),
        toolsets=[toolset],
        capabilities=[ModelImageBudget(trace=trace), EditValidationFeedback([
            toolset.tools[name] for name in ("edit_regions", "create_regions", "delete_regions", "edit_rich_text")
        ])],
        name="manga_page",
    )

    @agent.output_validator
    def validate_output(output: TaskResult) -> TaskResult:
        try:
            return _validate_result(ctx, identities, output)
        except ToolError as error:
            if error.code not in {"invalid_result", "unobserved_revision"}:
                raise
            raise ModelRetry(json.dumps(error.as_dict(), ensure_ascii=False)) from error

    # No shared history or provider conversation identifiers: this run owns all
    # native messages, including every observed image and tool response.
    agent.instrument = False
    if trace is not None:
        trace_data = {"text": prompt, "pages": list(identities.values())}
        if len(targets) == 1:
            trace_data["page"] = identities[targets[0]]
        trace.add("turn_start", trace_data)
    result = None
    response_index = -1
    async with agent:
        async with agent.run_stream_events(
            prompt,
            deps=ctx,
            usage_limits=UsageLimits(
                request_limit=30 * len(targets), tool_calls_limit=100 * len(targets)
            ),
        ) as events:
            async for event in events:
                if isinstance(event, AgentRunResultEvent):
                    result = event.result
                    if trace is not None:
                        trace.finish_response()
                if trace is None:
                    continue
                if isinstance(event, PartStartEvent) and event.index == 0:
                    response_index += 1
                if isinstance(event, (PartStartEvent, PartDeltaEvent, PartEndEvent)):
                    trace.response_event(response_index, event)
                elif isinstance(event, FunctionToolCallEvent):
                    trace.finish_response()
                    trace.add("tool_call", {
                        "tool_name": event.part.tool_name,
                        "tool_call_id": event.part.tool_call_id,
                        "arguments": event.part.args, "args_valid": event.args_valid,
                    })
                elif isinstance(event, FunctionToolResultEvent):
                    part = event.part
                    trace.add("validation_error" if isinstance(part, RetryPromptPart) else "tool_result", {
                        "tool_name": part.tool_name, "tool_call_id": part.tool_call_id,
                        "result": part.content, "content": event.content,
                        "metadata": getattr(part, "metadata", None),
                    })
    if result is None:
        raise ToolError("invalid_result", "子代理结束时没有返回结构化任务结果")
    # Keep a final host check in case cancellation or a concurrent edit occurred
    # after the model's output validator ran.
    output = _validate_result(ctx, identities, result.output)
    if trace is not None:
        trace.add("turn_complete", {"messages": result.all_messages(), "usage": result.usage})
    return output
