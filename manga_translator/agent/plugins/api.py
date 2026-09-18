"""Public API exposed to trusted local tool plugins."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai.tools import Tool

from ..domain.tool_models import ToolContext

_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_ROLES = frozenset(("page", "manager"))


@dataclass(frozen=True, slots=True)
class PluginRegistration:
    """An immutable, validated plugin tool registration."""

    function: Callable[..., Any]
    name: str
    roles: tuple[str, ...]
    description: str | None
    tool: Tool[ToolContext]


class PluginRegistrar:
    """Synchronous registration surface given to a trusted plugin."""

    def __init__(self, plugin_id: str):
        if not isinstance(plugin_id, str) or not _PLUGIN_ID.fullmatch(plugin_id):
            raise ValueError("plugin id must match ^[a-z][a-z0-9_]{0,23}$")
        self.plugin_id = plugin_id
        self._registrations: list[PluginRegistration] = []
        self._close_callbacks: list[Callable[[], None]] = []
        self._frozen = False

    @property
    def registrations(self) -> tuple[PluginRegistration, ...]:
        return tuple(self._registrations)

    @property
    def close_callbacks(self) -> tuple[Callable[[], None], ...]:
        return tuple(self._close_callbacks)

    def _check_open(self) -> None:
        if self._frozen:
            raise RuntimeError("plugin registrar is frozen")

    def tool(
        self,
        function: Callable[..., Any],
        *,
        name: str | None = None,
        roles: Iterable[Literal["page", "manager"]] = ("page",),
        description: str | None = None,
    ) -> Callable[..., Any]:
        """Validate and register a synchronous or asynchronous native tool."""
        self._check_open()
        if not callable(function) or inspect.isasyncgenfunction(function):
            raise TypeError("tool must be a regular callable, not an async generator")
        local_name = function.__name__ if name is None else name
        if not isinstance(local_name, str) or not _PLUGIN_ID.fullmatch(local_name):
            raise ValueError("tool name must match ^[a-z][a-z0-9_]{0,23}$")
        try:
            role_tuple = tuple(roles)
        except TypeError as exc:
            raise TypeError("roles must be an iterable of page and manager") from exc
        if not role_tuple or any(role not in _ROLES for role in role_tuple):
            raise ValueError("roles must contain page and/or manager")
        if len(set(role_tuple)) != len(role_tuple):
            raise ValueError("roles must not contain duplicates")
        final_name = f"plugin__{self.plugin_id}__{local_name}"
        if len(final_name) > 64:
            raise ValueError("final plugin tool name exceeds 64 characters")
        if any(item.name == final_name for item in self._registrations):
            raise ValueError(f"duplicate plugin tool name: {local_name!r}")
        native = Tool(function, name=final_name, description=description)
        self._registrations.append(
            PluginRegistration(
                function, final_name, role_tuple, native.description, native
            )
        )
        return function

    def on_close(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a synchronous cleanup callback, returned for decorator use."""
        self._check_open()
        if (
            not callable(callback)
            or inspect.iscoroutinefunction(callback)
            or inspect.iscoroutinefunction(getattr(callback, "__call__", None))
        ):
            raise TypeError("close callback must be synchronous")
        try:
            inspect.signature(callback).bind()
        except (TypeError, ValueError) as exc:
            raise TypeError("close callback must accept no arguments") from exc
        self._close_callbacks.append(callback)
        return callback

    def freeze(self) -> None:
        self._frozen = True


__all__ = ["PluginRegistrar", "PluginRegistration"]
