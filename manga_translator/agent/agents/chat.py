"""Main conversational Agent assembly, shared by every application host."""

import json
from copy import deepcopy
from dataclasses import replace

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.tools import Tool

from ..context.images import EDIT_IMAGE_METADATA
from ..domain.chat import ChatCanvas, ChatImage
from ..domain.tool_models import AccessGrant, ToolContext
from ..prompts import load_prompt
from ..tools.registry import WorkspaceToolset
from ..tools.builtin.page import apply_edits
from ..tools.validation import EditValidationFeedback
from ..workspace import Workspace


def _compact_edit_schema(schema):
    """Keep typed fields and shared definitions; omit redundant schema metadata.

    Optional patches are omitted rather than sent as null, as the prompt requires.
    The original Pydantic validator remains authoritative at execution time.
    """
    result = {key: deepcopy(value) for key, value in schema.items()
              if key not in {"title", "default", "discriminator"}}
    for key in ("properties", "$defs"):
        if key in result:
            result[key] = {name: _compact_edit_schema(value) for name, value in result[key].items()}
    for key in ("items", "additionalProperties"):
        if isinstance(result.get(key), dict):
            result[key] = _compact_edit_schema(result[key])
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if key in result:
            result[key] = [_compact_edit_schema(value) for value in result[key]]
    if schema.get("default", False) is None and "anyOf" in result:
        choices = [value for value in result["anyOf"] if value != {"type": "null"}]
        if len(choices) == 1:
            result.pop("anyOf")
            result = {**choices[0], **result}
        else:
            result["anyOf"] = choices
    # Tagged unions require their discriminator even when the model field has a default.
    for name in ("op", "type"):
        if "const" in result.get("properties", {}).get(name, {}):
            required = result.setdefault("required", [])
            if name not in required:
                required.append(name)
    return result


def empty_context() -> ToolContext:
    """A conversation without a loaded document has no editing permissions."""
    return ToolContext(Workspace(), AccessGrant(set(), set(), set(), set()), "chat")


async def _prepare_edit(ctx, definition):
    pages = ctx.deps.grant.layout_pages | ctx.deps.grant.translation_pages | ctx.deps.grant.geometry_pages
    identities = [ctx.deps.workspace.public_identity(pid) for pid in sorted(pages)]
    location = ("当前加载的可编辑页面（无需另行开通权限）：" + json.dumps(identities, ensure_ascii=False)
                if identities else "当前尚未加载页面，请先在渲染界面加载图片。")
    return replace(
        definition, description=(definition.description or "") + "\n" + location,
        parameters_json_schema=_compact_edit_schema(definition.parameters_json_schema), strict=False,
    )


def create_agent(model, *, model_settings=None):
    # Deliberately register only editing in the main flow at this stage.
    edit_tool = Tool(apply_edits, sequential=True, prepare=_prepare_edit, max_retries=2)
    toolset = WorkspaceToolset(
        tools=[edit_tool], id="chat-editing"
    )
    return Agent(
        model, deps_type=ToolContext, output_type=str, retries=0,
        instructions=load_prompt("chat") + "\n\n" + load_prompt("rich_text"), toolsets=[toolset],
        model_settings=model_settings, name="manga_chat",
        capabilities=[EditValidationFeedback(edit_tool.function_schema.validator)],
    )


def canvas_from_tool_result(event, context: ToolContext) -> ChatCanvas | None:
    """Deliver the exact tool image to hosts, without rendering a second copy."""
    part = event.part
    if not isinstance(part, ToolReturnPart) or not isinstance(part.metadata, dict):
        return None
    identifiers = part.metadata.get(EDIT_IMAGE_METADATA)
    if not identifiers or not isinstance(part.content, dict) or context.cancelled.is_set():
        return None
    data = part.content
    transaction = context.transaction_results.get(data.get("transaction_id"))
    if transaction is None or data.get("render_status") != "rendered":
        return None
    current = context.workspace.page(context, transaction["page_id"])
    if current["revision"] != transaction["revision"]:
        return None
    for item in event.content or ():
        if isinstance(item, BinaryContent) and item.identifier in identifiers:
            return ChatCanvas(
                context_id=context.task_id,
                image=ChatImage(item.data, item.media_type),
                page=deepcopy(data.get("page", {})),
                canvas=deepcopy(data.get("canvas", {})),
            )
    return None
