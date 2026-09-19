"""Main conversational Agent assembly, shared by every application host."""

from copy import deepcopy

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.tools import Tool

from ..context.images import EDIT_IMAGE_METADATA
from ..domain.chat import ChatCanvas, ChatImage
from ..domain.tool_models import AccessGrant, ToolContext
from ..prompts import load_prompt
from ..tools.registry import WorkspaceToolset
from ..tools.edit_tools import create_edit_tools
from ..tools.builtin.skills import read_skill
from ..tools.validation import EditValidationFeedback
from ..workspace import Workspace


def empty_context() -> ToolContext:
    """A conversation without a loaded document has no editing permissions."""
    return ToolContext(Workspace(), AccessGrant(set(), set(), set(), set()), "chat")


def create_agent(model, *, model_settings=None):
    edit_tools = create_edit_tools()
    toolset = WorkspaceToolset(
        tools=[*edit_tools, Tool(read_skill, max_retries=2)], id="chat-editing"
    )
    return Agent(
        model, deps_type=ToolContext, output_type=str, retries=0,
        instructions=load_prompt("chat") + "\n\n" + load_prompt("skills"), toolsets=[toolset],
        model_settings=model_settings, name="manga_chat",
        capabilities=[EditValidationFeedback(edit_tools)],
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
