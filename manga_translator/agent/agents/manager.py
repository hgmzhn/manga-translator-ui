"""Manager Agent assembly for read-only coordination and page delegation."""

from pydantic_ai import Agent

from ..domain.tool_models import AccessGrant, ToolContext
from ..context.request_budget import ModelImageBudget
from ..prompts import load_prompt
from ..tools.registry import create_toolset
from ..workspace import Workspace


def empty_context() -> ToolContext:
    """A manager without a loaded workspace can only explain its limitation."""
    return ToolContext(Workspace(), AccessGrant(set(), set(), set(), set()), "manager")


def create_agent(model, *, model_settings=None, trace=None):
    """Build the manager role with delegation and read-only workspace tools."""
    toolset = create_toolset("manager")
    return Agent(
        model,
        deps_type=ToolContext,
        output_type=str,
        retries=1,
        # The manager roster is intentionally read-only and does not include
        # the page-agent skill loader. Keeping the skill prompt here caused
        # the model to call an unavailable ``read_skill`` tool.
        instructions=load_prompt("manager"),
        toolsets=[toolset],
        capabilities=[ModelImageBudget(trace=trace)],
        model_settings=model_settings,
        name="manga_manager",
    )
