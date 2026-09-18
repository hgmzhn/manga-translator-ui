"""Fixed native tool rosters for page and manager agents."""

from __future__ import annotations

from typing import Literal

from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.tools import Tool

from ..context.images import prune_images_after_edit
from ..domain.tool_models import ToolContext
from ..plugins.loader import PluginManager

from .builtin.auxiliary import (
    check_font_coverage,
    check_text_changes,
    fit_text,
    list_fonts,
    measure_layout,
)
from .builtin.manager import (
    browse_workspace,
    get_task_results,
    read_style_policy,
    review_page_results,
    revise_pages,
    update_style_policy,
    view_page_overview,
)
from .builtin.page import (
    apply_edits,
    compare_revisions,
    observe_canvas,
    read_page,
    revert_edits,
)
from .builtin.text import find_reference_pages, find_text, replace_text


_PAGE_TOOLS = (
    read_page,
    observe_canvas,
    find_reference_pages,
    find_text,
    replace_text,
    list_fonts,
    apply_edits,
    compare_revisions,
    revert_edits,
)
_MANAGER_TOOLS = (
    browse_workspace,
    view_page_overview,
    read_style_policy,
    update_style_policy,
    revise_pages,
    get_task_results,
    review_page_results,
    find_text,
    replace_text,
)
_AUXILIARY_TOOLS = (measure_layout, fit_text, check_text_changes, check_font_coverage)


class WorkspaceToolset(FunctionToolset[ToolContext]):
    async def for_run_step(self, ctx):
        # Runs after the previous tool batch is assembled, including image parts.
        # No edit marker means the host's initial context is left unchanged.
        prune_images_after_edit(ctx.messages)
        return self


def create_toolset(
    role: Literal["page", "manager"] = "page",
    *,
    include_auxiliary: bool = False,
    plugin_manager: PluginManager | None = None,
) -> FunctionToolset[ToolContext]:
    """Build native tools plus the current role-filtered plugin snapshot."""
    if role not in ("page", "manager"):
        raise ValueError("role must be page or manager")
    functions = list(_PAGE_TOOLS if role == "page" else _MANAGER_TOOLS)
    if include_auxiliary:
        functions.extend(_AUXILIARY_TOOLS)
    toolset = WorkspaceToolset(
        tools=[Tool(function, sequential=True) if function in (apply_edits, revert_edits)
               else function for function in functions],
        id=f"workspace-{role}",
    )
    if plugin_manager is None:
        from ..plugins import get_default_manager

        plugin_manager = get_default_manager()
    for tool in plugin_manager.snapshot(role):
        toolset.add_tool(tool)
    return toolset
