"""Import a workspace and navigate its latest page previews."""

import asyncio
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..domain.tool_models import AccessGrant, ToolContext, ToolError
from ..integrations.project import load_project_folder, load_project_page
from ..integrations.rendering import BackendRenderer
from ..workspace import Workspace


class RenderPreviewSession:
    """Own a warm renderer on the host's asyncio loop and reject stale loads."""

    def __init__(self, *, include_system_fonts: bool = True):
        self.renderer = None
        self._include_system_fonts = bool(include_system_fonts)
        self._generation = 0
        self._navigation_generation = 0
        self._closed = False
        self._context = None
        self._context_closures: set[asyncio.Task] = set()

    def set_system_fonts_enabled(self, enabled: bool) -> None:
        """Keep Agent font discovery aligned with the desktop font policy."""
        self._include_system_fonts = bool(enabled)
        if self.renderer is not None:
            self.renderer.set_system_fonts_enabled(self._include_system_fonts)

    async def warmup(self):
        if self._closed:
            raise ToolError("renderer_closed", "Render preview is closed")
        if self.renderer is None:
            self.renderer = BackendRenderer(
                include_system_fonts=self._include_system_fonts
            )
        await self.renderer.warmup()

    async def load(self, source_path: str):
        if self._closed:
            raise ToolError("renderer_closed", "Render preview is closed")
        started = perf_counter()
        self._generation += 1
        generation = self._generation
        self._navigation_generation += 1
        await self._release_context()
        if self._closed or generation != self._generation:
            raise asyncio.CancelledError
        path = Path(source_path).resolve()
        if path.is_dir():
            snapshots = await asyncio.to_thread(load_project_folder, str(path))
            if not snapshots:
                raise ToolError("missing_asset", "No supported images were found in the folder")
        else:
            snapshot = await asyncio.to_thread(
                load_project_page, str(path), page_id=uuid5(NAMESPACE_URL, path.as_uri()).hex,
                work_id=str(path.parent), chapter_id="preview", order=0,
            )
            if snapshot["project_status"] != "available":
                raise ToolError("missing_project", "No corresponding translations JSON was found")
            snapshots = [snapshot]
        if self._closed or generation != self._generation:
            raise asyncio.CancelledError
        for snapshot in snapshots:
            snapshot["revision"] = generation
        if self.renderer is None:
            self.renderer = BackendRenderer(
                include_system_fonts=self._include_system_fonts
            )
        workspace = Workspace()
        for snapshot in snapshots:
            workspace.register_page(snapshot)
        workspace.seal_index()
        snapshot = min(snapshots, key=lambda page: workspace.public_identity(page["page_id"])["id"])
        payload = await self.renderer.observe(snapshot, max_dimension=4096)
        if self._closed or generation != self._generation:
            raise asyncio.CancelledError
        page_ids = {page["page_id"] for page in snapshots}
        context = ToolContext(
            workspace, AccessGrant(page_ids, page_ids, page_ids, set()),
            uuid4().hex, renderer=self.renderer,
        )
        # This render is a local preview, not a model observation. The Agent
        # establishes its read baseline through read_page.
        self._context = context
        result = self._preview_result(context, snapshot, payload, started)
        result["pages"] = sorted(
            (workspace.public_identity(page["page_id"]) for page in snapshots),
            key=lambda page: page["id"],
        )
        return result

    async def select_page(self, page_ref):
        """Render a current snapshot without replacing the workspace or its tasks."""
        if self._closed:
            raise ToolError("renderer_closed", "Render preview is closed")
        context = self._context
        if context is None:
            raise ToolError("missing_workspace", "Load a workspace before selecting a page")
        generation = self._generation
        self._navigation_generation += 1
        navigation = self._navigation_generation
        started = perf_counter()
        # The UI carries the full public identity; tool references deliberately
        # accept either an id or a folder/name pair, never all three fields.
        if isinstance(page_ref, dict):
            reference = (
                {"id": page_ref["id"]} if "id" in page_ref
                else {"folder": page_ref.get("folder"), "name": page_ref.get("name")}
            )
        elif type(page_ref) is int:
            reference = {"id": page_ref}
        else:
            reference = page_ref
        page_id = context.workspace.resolve_page(context, reference)
        identity = context.workspace.public_identity(page_id)
        if isinstance(page_ref, dict) and any(
            key in page_ref and page_ref[key] != identity[key]
            for key in ("folder", "name")
        ):
            raise ToolError("page_not_found", "Page identity no longer matches the loaded workspace")
        snapshot = context.workspace.page(context, page_id)
        payload = await self.renderer.observe(snapshot, max_dimension=4096)
        if (self._closed or generation != self._generation
                or navigation != self._navigation_generation or context is not self._context):
            raise asyncio.CancelledError
        return self._preview_result(context, snapshot, payload, started)

    @staticmethod
    def _preview_result(context, snapshot, payload, started):
        return {
            "payload": payload,
            "regions": snapshot["regions"],
            "current_page": context.workspace.public_identity(snapshot["page_id"]),
            "page_revision": snapshot["revision"],
            "source_path": snapshot["original_asset"],
            "base_path": snapshot["base_asset"],
            "load_ms": (perf_counter() - started) * 1000,
            "context": context,
        }

    async def _release_context(self):
        context, self._context = self._context, None
        if context is not None:
            context.cancelled.set()
            if context.runtime is not None:
                self._context_closures.add(asyncio.create_task(context.runtime.close()))
        pending = tuple(self._context_closures)
        if pending:
            # Cancelling an obsolete folder import must not interrupt cleanup
            # of its child agents. A later load/close joins these tasks too.
            outcomes = await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))
            self._context_closures.difference_update(pending)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome

    async def close(self):
        self._closed = True
        self._generation += 1
        self._navigation_generation += 1
        try:
            await self._release_context()
        finally:
            if self.renderer is not None:
                await self.renderer.close()
