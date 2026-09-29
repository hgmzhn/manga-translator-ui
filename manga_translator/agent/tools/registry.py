"""Fixed native tool rosters for page and manager agents."""

from __future__ import annotations

from typing import Literal

from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.tools import Tool

from ..domain.tool_models import ToolContext
from ..plugins.loader import PluginManager

from .builtin.auxiliary import list_fonts, preview_font
from .builtin.manager import (
    browse_workspace,
    get_task_results,
    review_page_results,
    revise_pages,
    view_page_overview,
)
from .builtin.page import (
    compare_revisions,
    read_page,
    revert_edits,
)
from .builtin.skills import read_skill
from .builtin.text import find_reference_pages, find_text, replace_text
from .edit_tools import create_edit_tools


_PAGE_TOOLS = (
    read_skill,
    read_page,
    find_reference_pages,
    find_text,
    replace_text,
    list_fonts,
    preview_font,
    compare_revisions,
    revert_edits,
)
_MANAGER_TOOLS = (
    browse_workspace,
    view_page_overview,
    read_page,
    list_fonts,
    preview_font,
    revise_pages,
    get_task_results,
    review_page_results,
    find_reference_pages,
    find_text,
)


class WorkspaceToolset(FunctionToolset[ToolContext]):
    """Native toolset with append-only history, including images and snapshots."""


def create_toolset(
    role: Literal["page", "manager"] = "page",
    *,
    plugin_manager: PluginManager | None = None,
) -> FunctionToolset[ToolContext]:
    """Build native tools plus the current role-filtered plugin snapshot."""
    if role not in ("page", "manager"):
        raise ValueError("role must be page or manager")
    functions = list(_PAGE_TOOLS if role == "page" else _MANAGER_TOOLS)
    toolset = WorkspaceToolset(
        tools=[Tool(function, sequential=True) if function is revert_edits
               else function for function in functions],
        id=f"workspace-{role}",
    )
    if role == "page":
        for tool in create_edit_tools():
            toolset.add_tool(tool)
    if plugin_manager is None:
        from ..plugins import get_default_manager

        plugin_manager = get_default_manager()
    for tool in plugin_manager.snapshot(role):
        toolset.add_tool(tool)
    return toolset
