"""Read-only access to the built-in Agent skill instructions."""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from ...domain.tool_models import ToolContext, ToolError
from ...skills import SkillName, load_skill
from .shared import _guard


@_guard
async def read_skill(ctx: RunContext[ToolContext], name: SkillName) -> str | ToolReturn:
    """读取操作说明；rich-text 同时加载富文本编辑工具，不改变页面或编辑权限。"""
    try:
        instructions = load_skill(name)
    except OSError as error:
        raise ToolError("skill_unavailable", f"无法读取 skill：{name}") from error
    if name == "rich-text":
        return ToolReturn(return_value=instructions, tools=["edit_rich_text"])
    return instructions
