"""Discovery and immutable native tool snapshots for trusted local plugins."""

from __future__ import annotations

import functools
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import inspect
import json
import logging
import re
import sys
import threading
import types
import uuid
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..domain.tool_models import ToolContext, ToolError
from .api import PluginRegistrar, PluginRegistration
from .manifest import PluginManifest

_LOG = logging.getLogger(__name__)
_ID = re.compile(r"[a-z][a-z0-9_]{0,23}\Z")
_DIGITS = re.compile(r"(\d+)")


def _natural_key(path: Path) -> tuple:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in _DIGITS.split(path.name)
    ), path.name


def _require_sync_result(value: Any) -> None:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("plugin lifecycle callbacks must not return awaitables")


class _SourceLoader(importlib.abc.Loader):
    def __init__(self, source: bytes | None, filename: str) -> None:
        self.source = source
        self.filename = filename

    def create_module(self, spec):
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        if self.source is not None:
            exec(compile(self.source, self.filename, "exec"), module.__dict__)


class _SourceFinder(importlib.abc.MetaPathFinder):
    """Private imports read frozen bytes, including imports delayed until a call."""

    def __init__(self, prefix: str, root: Path, sources: dict[str, bytes]) -> None:
        self.prefix = prefix
        self.root = root
        self.sources = sources
        self.packages = {""}
        self.aliases = {"": ""}
        for name in sources:
            parent = Path(name).parent
            while parent != Path("."):
                self.packages.add(parent.as_posix())
                parent = parent.parent
            relative = name[:-3]
            self._add_alias(relative)
        for package in self.packages:
            self._add_alias(package)

    @staticmethod
    def _module_path(relative: str) -> str:
        return (
            ".".join(
                part
                if part.isidentifier()
                else "_plugin_path_" + part.encode("utf-8").hex()
                for part in relative.split("/")
            )
            if relative
            else ""
        )

    def _add_alias(self, relative: str) -> None:
        name = self._module_path(relative)
        if name in self.aliases and self.aliases[name] != relative:
            raise ValueError("plugin module path collision")
        self.aliases[name] = relative

    def entry_module(self, entrypoint: str) -> str:
        parts = entrypoint[:-3].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        relative = self._module_path("/".join(parts))
        return self.prefix + ("." + relative if relative else "")

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname != self.prefix and not fullname.startswith(self.prefix + "."):
            return None
        relative = self.aliases.get(fullname[len(self.prefix) :].lstrip("."))
        if relative is None:
            return None
        initializer = relative + "/__init__.py" if relative else "__init__.py"
        source_path = relative + ".py"
        if initializer in self.sources:
            source_path, is_package = initializer, True
        elif relative and source_path in self.sources:
            is_package = False
        elif relative in self.packages:
            # An empty search path intentionally prevents falling back to live disk.
            return importlib.machinery.ModuleSpec(
                fullname,
                _SourceLoader(None, str(self.root / relative)),
                is_package=True,
            )
        else:
            return None
        filename = str(self.root / source_path)
        spec = importlib.machinery.ModuleSpec(
            fullname,
            _SourceLoader(self.sources[source_path], filename),
            origin=filename,
            is_package=is_package,
        )
        spec.has_location = True
        return spec

    def remove(self) -> None:
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        for name in tuple(sys.modules):
            if name == self.prefix or name.startswith(self.prefix + "."):
                sys.modules.pop(name, None)


@dataclass
class _Generation:
    package: Path
    plugin_id: str
    finder: _SourceFinder
    registrations: tuple[PluginRegistration, ...]
    callbacks: tuple[Any, ...]
    revoked: bool = False
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = self.revoked = True
        try:
            for callback in reversed(self.callbacks):
                try:
                    _require_sync_result(callback())
                except Exception:
                    _LOG.warning("plugin %s: cleanup_failed", self.plugin_id)
        finally:
            self.finder.remove()


@dataclass
class _Candidate:
    package: Path
    manifest_bytes: bytes = b""
    plugin_id: str | None = None
    manifest: PluginManifest | None = None
    code: str | None = None


@dataclass
class _CachedLoad:
    fingerprint: str
    generation: _Generation | None
    code: str | None = None


class PluginManager:
    """Host-owned local plugin lifetime; close only after its tasks have stopped.

    All published generations and their sources/resources are retained until
    close. A new snapshot refreshes discovery; an existing snapshot never gains
    replacement functions. Disabling/removing a package revokes old calls too.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self._lock = threading.RLock()
        self._cache: dict[Path, _CachedLoad] = {}
        self._active: tuple[_Generation, ...] = ()
        self._generations: list[_Generation] = []
        self._statuses: list[dict[str, Any]] = []
        self._closed = False
        self._namespace = uuid.uuid4().hex
        self._serial = 0

    @property
    def statuses(self) -> list[dict[str, Any]]:
        """Last refresh diagnostics; never contains plugin exception messages."""
        with self._lock:
            return [status.copy() for status in self._statuses]

    def _revoke(self, package: Path) -> None:
        self._cache.pop(package, None)
        for generation in self._generations:
            if generation.package == package:
                generation.revoked = True

    def _scan(self) -> list[_Candidate]:
        candidates = []
        for package in sorted(self.root.iterdir(), key=_natural_key):
            try:
                if not package.is_dir() or not (package / "plugin.json").exists():
                    continue
            except OSError:
                candidates.append(_Candidate(package, code="package_unreadable"))
                continue
            item = _Candidate(package)
            candidates.append(item)
            try:
                package.resolve().relative_to(self.root)
                (package / "plugin.json").resolve().relative_to(package.resolve())
                item.manifest_bytes = (package / "plugin.json").read_bytes()
                raw = json.loads(item.manifest_bytes)
                if isinstance(raw, dict):
                    plugin_id = raw.get("id")
                    if isinstance(plugin_id, str) and _ID.fullmatch(plugin_id):
                        item.plugin_id = plugin_id
                    if "api_version" in raw and (
                        type(raw["api_version"]) is not int or raw["api_version"] != 1
                    ):
                        item.code = "unsupported_api_version"
                item.manifest = PluginManifest.model_validate_json(item.manifest_bytes)
            except Exception:
                item.code = item.code or "invalid_manifest"
        return candidates

    @staticmethod
    def _sources(item: _Candidate) -> tuple[dict[str, bytes], str]:
        package = item.package
        root = package.resolve()
        sources: dict[str, bytes] = {}
        digest = hashlib.sha256()
        digest.update(len(item.manifest_bytes).to_bytes(8, "big"))
        digest.update(item.manifest_bytes)
        for path in sorted(package.rglob("*.py")):
            path.resolve().relative_to(root)
            name = path.relative_to(package).as_posix()
            source = path.read_bytes()
            sources[name] = source
            for value in (name.encode("utf-8"), source):
                digest.update(len(value).to_bytes(8, "big"))
                digest.update(value)
        return sources, digest.hexdigest()

    def _load(self, item: _Candidate, sources: dict[str, bytes]) -> _Generation:
        manifest = item.manifest
        assert manifest is not None
        entrypoint = manifest.entrypoint_path(item.package)
        if not entrypoint.is_file() or manifest.entrypoint not in sources:
            raise FileNotFoundError("plugin entrypoint is missing")
        self._serial += 1
        prefix = f"_manga_agent_plugin_{self._namespace}_{self._serial}"
        finder = _SourceFinder(prefix, item.package, sources)
        registrar = PluginRegistrar(manifest.id)
        sys.meta_path.insert(0, finder)
        try:
            module = importlib.import_module(finder.entry_module(manifest.entrypoint))
            register = getattr(module, "register", None)
            if (
                not callable(register)
                or inspect.iscoroutinefunction(register)
                or inspect.iscoroutinefunction(getattr(register, "__call__", None))
            ):
                raise TypeError("register must be a synchronous callable")
            _require_sync_result(register(registrar))
            registrar.freeze()
            generation = _Generation(
                item.package,
                manifest.id,
                finder,
                registrar.registrations,
                registrar.close_callbacks,
            )
            self._generations.append(generation)
            return generation
        except BaseException:
            registrar.freeze()
            _Generation(
                item.package,
                manifest.id,
                finder,
                (),
                registrar.close_callbacks,
            ).close()
            raise

    def _publish_statuses(self, statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for status in statuses:
            if status["status"] == "error" and status not in self._statuses:
                _LOG.warning(
                    "plugin %s: %s", status["id"] or "<unknown>", status["code"]
                )
        self._statuses = statuses
        return [status.copy() for status in statuses]

    def refresh(self) -> list[dict[str, Any]]:
        """Discover immediate child packages, isolating every package's failure."""
        with self._lock:
            if self._closed:
                raise RuntimeError("plugin manager is closed")
            if not self.root.exists():
                for package in {generation.package for generation in self._generations}:
                    self._revoke(package)
                self._active = ()
                return self._publish_statuses([])
            try:
                candidates = self._scan()
            except OSError:
                self._active = ()
                return self._publish_statuses(
                    [
                        {
                            "id": None,
                            "package": None,
                            "status": "error",
                            "code": "root_unreadable",
                        }
                    ]
                )
            counts: dict[str, int] = {}
            for item in candidates:
                if item.plugin_id is not None:
                    counts[item.plugin_id] = counts.get(item.plugin_id, 0) + 1
            present = {item.package for item in candidates}
            for package in {
                generation.package for generation in self._generations
            } - present:
                self._revoke(package)
            active = []
            statuses = []
            for item in candidates:
                manifest = item.manifest
                status = {
                    "id": item.plugin_id,
                    "package": item.package.name,
                    "status": "error",
                }
                if item.plugin_id and counts[item.plugin_id] > 1:
                    self._revoke(item.package)
                    status["code"] = "duplicate_id"
                elif manifest is None:
                    self._cache.pop(item.package, None)
                    status["code"] = item.code or "invalid_manifest"
                elif not manifest.enabled:
                    self._revoke(item.package)
                    status.update(status="disabled", version=manifest.version)
                else:
                    status["version"] = manifest.version
                    try:
                        sources, fingerprint = self._sources(item)
                        cached = self._cache.get(item.package)
                        if cached is None or cached.fingerprint != fingerprint:
                            try:
                                generation = self._load(item, sources)
                                cached = _CachedLoad(fingerprint, generation)
                            except Exception:
                                cached = _CachedLoad(fingerprint, None, "load_failed")
                            self._cache[item.package] = cached
                        if cached.generation is None:
                            status["code"] = cached.code or "load_failed"
                        else:
                            active.append(cached.generation)
                            status.update(
                                status="loaded",
                                tools=len(cached.generation.registrations),
                            )
                    except Exception:
                        self._cache.pop(item.package, None)
                        status["code"] = "source_unreadable"
                statuses.append(status)
            self._active = tuple(active)
            return self._publish_statuses(statuses)

    def _check_call(
        self, generation: _Generation, ctx: RunContext[ToolContext]
    ) -> None:
        if ctx.deps.cancelled.is_set():
            raise ToolError("cancelled", "任务已取消")
        with self._lock:
            if self._closed or generation.revoked:
                raise ToolError("plugin_unavailable", "插件已关闭、禁用或移除")
            try:
                raw = json.loads((generation.package / "plugin.json").read_bytes())
                enabled = (
                    isinstance(raw, dict)
                    and raw.get("id") == generation.plugin_id
                    and raw.get("enabled", True) is True
                )
            except (OSError, ValueError):
                enabled = False
            if not enabled:
                self._revoke(generation.package)
                raise ToolError("plugin_unavailable", "插件已禁用或移除")

    @staticmethod
    def _public_result(ctx: RunContext[ToolContext], value: Any) -> Any:
        # Imported after registry initialization to avoid a plugins/tools import cycle.
        from ..tools.builtin.shared import public_payload

        if ctx.deps.cancelled.is_set():
            raise ToolError("cancelled", "任务已取消")
        return public_payload(ctx.deps, value)

    def _failure(
        self, generation: _Generation, ctx: RunContext[ToolContext], error: Exception
    ) -> Any:
        from ..tools.builtin.shared import public_payload

        if isinstance(error, ToolError):
            detail = {
                "code": error.code,
                "message": str(error),
                "details": error.details,
            }
        else:
            _LOG.warning("plugin %s: execution_failed", generation.plugin_id)
            detail = {"code": "plugin_execution_failed", "message": "插件工具执行失败"}
        return public_payload(ctx.deps, {"status": "error", "error": detail})

    def _bind(
        self, generation: _Generation, registration: PluginRegistration
    ) -> Tool[ToolContext]:
        original = registration.tool
        schema = original.function_schema
        function = schema.function

        async def finish_awaitable(ctx, value):
            try:
                return self._public_result(ctx, await value)
            except Exception as error:
                return self._failure(generation, ctx, error)

        if schema.is_async:

            @functools.wraps(function)
            async def guarded(ctx, *args, **kwargs):
                try:
                    self._check_call(generation, ctx)
                    value = (
                        function(ctx, *args, **kwargs)
                        if schema.takes_ctx
                        else function(*args, **kwargs)
                    )
                    return await finish_awaitable(ctx, value)
                except Exception as error:
                    return self._failure(generation, ctx, error)
        else:

            @functools.wraps(function)
            def guarded(ctx, *args, **kwargs):
                try:
                    self._check_call(generation, ctx)
                    value = (
                        function(ctx, *args, **kwargs)
                        if schema.takes_ctx
                        else function(*args, **kwargs)
                    )
                    if inspect.isawaitable(value):
                        return finish_awaitable(ctx, value)
                    return self._public_result(ctx, value)
                except Exception as error:
                    return self._failure(generation, ctx, error)

        bound = copy(original)
        bound.function = guarded
        bound.takes_ctx = True
        bound.function_schema = copy(schema)
        bound.function_schema.function = guarded
        bound.function_schema.takes_ctx = True
        return bound

    def snapshot(self, role: str) -> list[Tool[ToolContext]]:
        if role not in ("page", "manager"):
            raise ValueError("role must be page or manager")
        with self._lock:
            self.refresh()
            return [
                self._bind(generation, registration)
                for generation in self._active
                for registration in generation.registrations
                if role in registration.roles
            ]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for generation in reversed(self._generations):
                generation.close()
            self._generations.clear()
            self._cache.clear()
            self._active = ()

    def __enter__(self) -> PluginManager:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
