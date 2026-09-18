import _bootstrap  # noqa: F401

import asyncio
import base64
import copy
import hashlib
import io
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from manga_translator.agent.domain.tool_models import ToolError
from manga_translator.agent.integrations import rendering as adapter


@pytest.fixture(scope="module", autouse=True)
def font_runtime():
    adapter._initialize_worker(())


@pytest.fixture
def page(tmp_path):
    adapter._render_cache.clear()
    adapter._render_cache_bytes = 0
    yy, xx = np.indices((600, 800))
    rgba = np.stack((xx % 256, yy % 256, (xx + yy) % 256, xx % 150 + 100), axis=-1).astype(np.uint8)
    path = tmp_path / "base.png"
    Image.fromarray(rgba).save(path)
    regions = []
    for i, (x, y) in enumerate(((150, 150), (620, 150), (150, 470))):
        regions.append({
            "region_id": f"r{i}", "version": 1,
            "lines": [[[x - 80, y - 50], [x + 80, y - 50], [x + 80, y + 50], [x - 80, y + 50]]],
            "center": [x + 0.5, y + 0.5], "angle": 0,
            "texts": ["source"], "translation": "测试文字", "font_size": 28,
            "font_family": "Microsoft YaHei UI", "font_color": "#111111",
            "direction": "h", "target_lang": "CHS", "alignment": "center",
            "disable_font_border": True, "opacity": 1,
        })
    return {
        "page_id": "page", "work_id": "work", "chapter_id": "chapter", "order": 0,
        "folder": ".", "name": "base.png", "revision": 1, "policy_version": 1,
        "width": 800, "height": 600, "base_status": "available",
        "base_asset": str(path), "original_asset": str(path), "regions": regions,
        "_asset_fingerprints": {"base_asset": hashlib.sha256(path.read_bytes()).hexdigest()},
    }


def observe(page, **kwargs):
    return adapter._observe_snapshot(page, "rendered", kwargs.pop("crop", None),
                                     kwargs.pop("dimension", 1600), **kwargs)


def pixels(payload):
    with Image.open(io.BytesIO(payload["image"])) as image:
        return np.array(image)


@pytest.mark.parametrize("patch", [
    {"font_size": 33}, {"font_color": "#ff3300"}, {"opacity": 0.4},
    {"translation": ""}, {"translation": "改动后的文字"}, {"direction": "v"},
    {"center": [220.5, 190.5]}, {"center": [-5, 20]},
])
def test_local_update_matches_full_backend_pixels(page, patch):
    original = observe(page)
    page["revision"] += 1
    page["regions"][0].update(patch, version=2)
    fast = observe(page)
    full = observe(page, force_full=True)
    assert fast["status"] == "fast", fast
    assert fast["rendered_regions"] == ["r0"]
    assert np.array_equal(pixels(fast), pixels(full))
    assert not np.array_equal(pixels(original), pixels(fast))
    assert fast["requested_revision"] == fast["rendered_revision"] == 2


def test_observation_cache_preserves_native_size_and_historical_revision(page):
    first = observe(page, dimension=200)
    cached = observe(page, dimension=200)
    assert cached["status"] == "cached" and cached["image"] == first["image"]
    assert pixels(observe(page)).shape == (600, 800, 4)
    old = copy.deepcopy(page)
    page["revision"] = 2
    page["regions"][0]["font_size"] = 32
    current = observe(page)
    historical = observe(old)
    assert historical["rendered_revision"] == 1
    assert np.array_equal(pixels(historical), pixels(observe(old, force_full=True)))
    again = observe(page)
    assert again["status"] == "cached" and again["image"] == current["image"]


@pytest.mark.parametrize("kind", ["rotation", "stroke", "rich", "overlap", "order"])
def test_complex_changes_use_full_backend_without_stale_pixels(page, kind):
    observe(page)
    page["revision"] = 2
    region = page["regions"][0]
    if kind == "rotation":
        region["angle"] = 20
    elif kind == "stroke":
        region.update(disable_font_border=False, stroke_width=0.1, stroke_color="#ffffff")
    elif kind == "rich":
        region["translation_rich"] = {"format": "richtext.v1", "blocks": [
            {"type": "paragraph", "inlines": [
                {"type": "text", "text": "测试文字", "style": {"bold": True, "color": "#dd2200"}}
            ]}
        ]}
    elif kind == "overlap":
        region["center"] = page["regions"][1]["center"]
    else:
        page["regions"].reverse()
    result = observe(page)
    assert result["status"] == "full_fallback"
    assert result["fallback_reason"]
    assert np.array_equal(pixels(result), pixels(observe(page, force_full=True)))


def test_cached_assets_cannot_silently_change(page):
    observe(page)
    Image.new("RGBA", (800, 600), "red").save(page["base_asset"])
    with pytest.raises(ToolError, match="Imported image bytes changed"):
        observe(page)


def test_identical_page_ids_are_isolated_between_works(page):
    first = observe(page)
    other = copy.deepcopy(page)
    other["work_id"] = "other-work"
    other["regions"][0]["font_color"] = "#ff0000"
    assert observe(other)["status"] == "full_fallback"
    assert observe(page)["image"] == first["image"]


def test_swapping_regions_never_leaves_old_text(page):
    observe(page)
    page["revision"] = 2
    a, b = page["regions"][:2]
    a["center"], b["center"] = b["center"], a["center"]
    b["translation"] = "另一段文字"
    result = observe(page)
    assert result["status"] == "full_fallback"
    assert np.array_equal(pixels(result), pixels(observe(page, force_full=True)))


def test_cache_budget_evicts_images_and_encoding_together(page, monkeypatch):
    one_page_bytes = page["width"] * page["height"] * 8
    monkeypatch.setattr(adapter, "MAX_RENDER_CACHE_BYTES", one_page_bytes + 100_000)
    first = observe(page)
    other = copy.deepcopy(page)
    other["page_id"] = "other"
    observe(other)
    assert adapter._render_cache_bytes <= adapter.MAX_RENDER_CACHE_BYTES
    again = observe(page)
    assert again["status"] == "full_fallback"
    assert again["image"] == first["image"]


def test_backend_starts_without_any_editor_or_widgets(page):
    code = r'''
import _bootstrap
import importlib.abc
import json
import sys
class NoEditor(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'editor', 'desktop_qt_ui'} or fullname == 'PyQt6.QtWidgets':
            raise AssertionError('Forbidden GUI dependency: ' + fullname)
sys.meta_path.insert(0, NoEditor())
from manga_translator.agent.integrations.rendering import _initialize_worker, _observe_snapshot
_initialize_worker(())
from PyQt6.QtGui import QGuiApplication
assert type(QGuiApplication.instance()) is QGuiApplication
page = json.loads(sys.stdin.read())
result = _observe_snapshot(page, 'rendered', None, 1600)
assert result['image'].startswith(b'\x89PNG')
assert not any(m.split('.')[0] in {'editor', 'desktop_qt_ui'} for m in sys.modules)
print('headless render passed')
'''
    import json
    result = subprocess.run([sys.executable, "-c", code], input=json.dumps(page),
                            cwd=_bootstrap.ROOT / "test", capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_paste_layers_and_crops_survive_local_update(page):
    image = Image.new("RGBA", (20, 30), (255, 0, 0, 160))
    output = io.BytesIO()
    image.save(output, format="PNG")
    page["_project_layers"] = {"paste_overlays": [{
        "image": base64.b64encode(output.getvalue()).decode(), "center_x": 400,
        "center_y": 300, "width": 140, "height": 100, "rotation": 27,
        "opacity": 0.6, "flip_h": True,
    }]}
    first = pixels(observe(page))
    page["revision"] = 2
    page["regions"][0]["font_size"] = 32
    fast = observe(page)
    assert fast["status"] == "fast"
    assert np.array_equal(pixels(fast), pixels(observe(page, force_full=True)))
    assert np.array_equal(pixels(fast)[250:350, 350:450], first[250:350, 350:450])
    crop = observe(page, crop=(300, 200, 500, 400))
    assert np.array_equal(pixels(crop), pixels(fast)[200:400, 300:500])


def test_headless_worker_schedule_cancel_and_close(page):
    async def run():
        renderer = adapter.BackendRenderer(max_pending=2)
        try:
            await renderer.warmup()
            assert renderer.schedule(page) == "queued"
            captured = copy.deepcopy(page)
            page["regions"][0]["font_size"] = 90
            result = await renderer.observe(captured)
            page.update(captured)
            assert result["rendered_revision"] == 1
            assert np.array_equal(pixels(result), pixels(observe(page, force_full=True)))
            # Rapid commits replace unobserved, unsubmitted previews of this page.
            page["revision"] = 2
            assert renderer.schedule(page) == "queued"
            old = copy.deepcopy(page)
            page["revision"] = 3
            page["regions"][0]["font_size"] = 33
            assert renderer.schedule(page) == "queued"
            assert len(renderer._scheduled) == 1
            assert (await renderer.observe(page))["rendered_revision"] == 3
            assert (await renderer.observe(old))["rendered_revision"] == 2
            cancelled = threading.Event()
            cancelled.set()
            page["revision"] = 4
            assert renderer.schedule(page, cancelled=cancelled) == "cancelled"
            # Cancelling a waiter cannot free native resources still in use.
            pending = asyncio.create_task(renderer.observe(page))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            await renderer.close()
            await renderer.close()
        with pytest.raises(ToolError, match="Renderer is closed"):
            await renderer.observe(page)
    asyncio.run(run())


def test_committed_edits_schedule_an_exact_snapshot(page):
    from types import SimpleNamespace
    from manga_translator.agent.domain.tool_models import AccessGrant, ToolContext
    from manga_translator.agent.tools.builtin.shared import _transactions
    from manga_translator.agent.workspace import Workspace

    async def run():
        workspace = Workspace()
        workspace.register_page(page)
        renderer = adapter.BackendRenderer()
        deps = ToolContext(workspace, AccessGrant({"page"}, set(), set(), set()), "task", renderer=renderer)
        try:
            result = workspace.apply_edits(deps, "page", {"r0": 1}, 1, [
                {"op": "set_region_style", "region_id": "r0", "style": {"font_size": 35}}
            ], "edit")
            result = _transactions(SimpleNamespace(deps=deps), result)
            assert result["render_status"] == "queued"
            current = workspace.page(deps, "page", 2)
            rendered = await renderer.observe(current)
            assert rendered["rendered_revision"] == 2
            assert deps.observed_revisions == {}  # Prefetch is not a model observation.
        finally:
            await renderer.close()
    asyncio.run(run())


def main():
    return pytest.main([str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
