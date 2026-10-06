import _bootstrap  # noqa: F401

import asyncio
import threading
from types import SimpleNamespace

from PIL import Image

from manga_translator.utils.concurrent_pipeline import ConcurrentPipeline


class _StubTranslator:
    def __init__(self):
        self._cancel_check_callback = None

    def _check_cancelled(self):
        return None

    def set_cancel_check_callback(self, callback):
        self._cancel_check_callback = callback


class _NoTextPipelineTranslator:
    """Minimal translator for the real four-stage threads (no-text pages)."""

    verbose = False
    save_quality = 100
    save_text = False
    text_output_file = None
    ignore_errors = False

    def __init__(self):
        self._cancel_check_callback = None
        self._resume_context_pages = []
        self._resume_context_order = {}
        self._current_save_info = {"output_folder": ""}
        self._stream_result_callback = None
        self._stream_progress_callback = None
        self.saved = []

    def _check_cancelled(self):
        return None

    def set_cancel_check_callback(self, callback):
        self._cancel_check_callback = callback

    async def _report_progress(self, state):
        return None

    def _save_editor_base_if_needed(self, ctx, config):
        return None

    async def _run_detection(self, config, ctx):
        await asyncio.sleep(0.05)
        return [], None, None

    async def _run_ocr(self, config, ctx):
        await asyncio.sleep(0.05)
        ctx.text_regions = []
        return []

    def _append_resume_context_before(self, image_name):
        return None

    async def _batch_translate_contexts(self, batch, batch_size):
        return batch

    async def _run_text_rendering(self, config, ctx):
        raise AssertionError("no-text pages must not render text")

    def _save_and_cleanup_context(self, ctx, save_info, config, tag):
        self.saved.append(ctx.image_name)

    def _cleanup_context_memory(self, ctx, keep_result=True):
        return None

    def _mark_context_failure(self, ctx, error, stage=""):
        return ctx


def _no_text_config():
    return SimpleNamespace(
        colorizer=SimpleNamespace(colorizer=SimpleNamespace(value="none")),
        upscale=SimpleNamespace(upscale_ratio=0),
    )


def test_max_workers_is_clamped_and_applied_to_stage_pools():
    pipeline = ConcurrentPipeline(_StubTranslator(), batch_size=1, max_workers=99)
    assert pipeline.max_workers == ConcurrentPipeline.MAX_WORKERS_LIMIT
    assert pipeline._detection_executor._max_workers == pipeline.max_workers
    assert pipeline._translation_executor._max_workers == pipeline.max_workers
    assert pipeline._inpaint_executor._max_workers == pipeline.max_workers
    assert pipeline._render_executor._max_workers == pipeline.max_workers

    for value, expected in ((0, 1), (-3, 1), (None, 1), ("bad", 1), (2, 2)):
        assert ConcurrentPipeline(_StubTranslator(), batch_size=1, max_workers=value).max_workers == expected


def test_process_batch_spawns_configured_workers_per_stage():
    pipeline = ConcurrentPipeline(_StubTranslator(), batch_size=1, max_workers=2)
    calls = []
    calls_lock = threading.Lock()

    def record(stage, worker_index, payload):
        with calls_lock:
            calls.append((stage, worker_index, payload))

    def detection(paths, configs, worker_index=0, worker_count=1):
        record("detection", worker_index, len(paths))

    def translation():
        record("translation", None, 1)

    def inpaint():
        record("inpaint", None, 1)

    def render():
        record("render", None, 1)

    pipeline._detection_ocr_thread = detection
    pipeline._translation_thread = translation
    pipeline._inpaint_thread = inpaint
    pipeline._render_thread = render

    asyncio.run(pipeline.process_batch(["a.png", "b.png", "c.png"], [None, None, None]))

    detection_calls = [call for call in calls if call[0] == "detection"]
    assert len(detection_calls) == 2
    assert sorted(call[2] for call in detection_calls) == [1, 2]
    assert len([call for call in calls if call[0] == "translation"]) == 2
    assert len([call for call in calls if call[0] == "inpaint"]) == 2
    assert len([call for call in calls if call[0] == "render"]) == 2


def test_concurrent_pipeline_runs_with_multiple_workers_without_deadlock(tmp_path):
    translator = _NoTextPipelineTranslator()
    translator._current_save_info = {"output_folder": str(tmp_path)}
    pipeline = ConcurrentPipeline(translator, batch_size=2, max_workers=2)

    paths = []
    for index in range(3):
        path = tmp_path / f"page-{index}.png"
        Image.new("RGB", (8, 16), (index * 40, 10, 10)).save(path)
        paths.append(str(path))

    results = asyncio.run(
        asyncio.wait_for(pipeline.process_batch(paths, [_no_text_config() for _ in paths]), timeout=30)
    )

    assert len(results) == 3
    assert pipeline.stats["detection_ocr"] == 3
    assert pipeline.stats["rendering"] == 3
    assert sorted(translator.saved) == sorted(paths)


if __name__ == "__main__":
    test_max_workers_is_clamped_and_applied_to_stage_pools()
    test_process_batch_spawns_configured_workers_per_stage()
    test_concurrent_pipeline_runs_with_multiple_workers_without_deadlock()
    print("concurrent pipeline worker scale test passed")
