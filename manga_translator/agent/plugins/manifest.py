"""Strict manifest contract for local trusted plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_PLUGIN_ID = r"^[a-z][a-z0-9_]{0,23}$"


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: Annotated[str, Field(pattern=_PLUGIN_ID)]
    version: Annotated[str, Field(min_length=1)]
    api_version: Literal[1]
    entrypoint: str = "plugin.py"
    enabled: bool = True

    @field_validator("api_version", mode="before")
    @classmethod
    def integer_api_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("api_version must be the integer 1")
        return value

    @field_validator("entrypoint")
    @classmethod
    def safe_entrypoint(cls, value: str) -> str:
        # Keep imports package-relative and deterministic.  Nested Python modules
        # are allowed, but traversal, absolute paths and non-Python files are not.
        if (
            not value
            or not value.endswith(".py")
            or "\\" in value
            or ":" in value
            or "\x00" in value
        ):
            raise ValueError("entrypoint must be a relative .py path")
        parts = value.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("entrypoint must stay inside the plugin package")
        return value

    def entrypoint_path(self, package_root: Path | str) -> Path:
        root = Path(package_root)
        candidate = root / Path(self.entrypoint)
        # lexical validation above is the primary boundary; resolve catches a
        # symlinked package file escaping the trusted package directory.
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve()
        try:
            candidate_resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("entrypoint escapes plugin package") from exc
        return candidate_resolved


__all__ = ["PluginManifest"]
