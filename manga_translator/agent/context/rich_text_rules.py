"""Agent-owned automatic styling configuration, separate from the desktop rules."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import yaml

from ..domain.tool_models import ToolError

RICH_TEXT_RULES_PATH = Path(__file__).with_name("rich_text_rules.yaml")


@lru_cache(maxsize=8)
def _compile_rules(source: bytes) -> dict:
    from manga_translator.rendering.rich_text_rules import _parse_rules

    data = yaml.safe_load(source) or {}
    if not isinstance(data, dict):
        raise ValueError("Agent rich-text rules must contain common/horizontal/vertical groups")
    return _parse_rules(data)


def load_agent_rich_text_rules() -> dict:
    """Load only the Agent file; content changes invalidate the compiled snapshot."""
    try:
        return deepcopy(_compile_rules(RICH_TEXT_RULES_PATH.read_bytes()))
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise ToolError(
            "invalid_agent_rich_text_rules", "Agent 独立自动富文本规则无法读取或格式错误",
            {"file": RICH_TEXT_RULES_PATH.name, "error_type": type(error).__name__},
        ) from error
