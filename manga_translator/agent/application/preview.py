"""File-backed, read-only render preview; no Qt or editor objects."""

import asyncio
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from ..domain.tool_models import ToolError
from ..integrations.project import load_project_page
from ..integrations.rendering import BackendRenderer


class RenderPreviewSession:
    """Own a warm renderer on the host's asyncio loop and reject stale loads."""

    def __init__(self):
        self.renderer = None
        self._generation = 0
        self._closed = False

    async def warmup(self):
        if self._closed:
            raise ToolError("renderer_closed", "Render preview is closed")
        if self.renderer is None:
            self.renderer = BackendRenderer()
        await self.renderer.warmup()

    async def load(self, source_path: str):
        if self._closed:
            raise ToolError("renderer_closed", "Render preview is closed")
        started = perf_counter()
        self._generation += 1
        generation = self._generation
        path = Path(source_path).resolve()
        snapshot = await asyncio.to_thread(
            load_project_page, str(path), page_id=uuid5(NAMESPACE_URL, path.as_uri()).hex,
            work_id=str(path.parent), chapter_id="preview", order=0,
        )
        if snapshot["project_status"] != "available":
            raise ToolError("missing_project", "No corresponding translations JSON was found")
        if self._closed or generation != self._generation:
            raise asyncio.CancelledError
        snapshot["revision"] = generation
        if self.renderer is None:
            self.renderer = BackendRenderer()
        payload = await self.renderer.observe(snapshot, max_dimension=4096)
        if self._closed or generation != self._generation:
            raise asyncio.CancelledError
        return {
            "payload": payload,
            "regions": snapshot["regions"],
            "source_path": snapshot["original_asset"],
            "base_path": snapshot["base_asset"],
            "load_ms": (perf_counter() - started) * 1000,
        }

    async def close(self):
        self._closed = True
        self._generation += 1
        if self.renderer is not None:
            await self.renderer.close()
