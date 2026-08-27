import _bootstrap  # noqa: F401

import asyncio
import time

from manga_translator.utils.concurrent_pipeline import ConcurrentPipeline


class _ProgressTranslator:
    def __init__(self):
        self._cancel_check_callback = None
        self.progress_events = []

    def _check_cancelled(self):
        return None

    def set_cancel_check_callback(self, callback):
        self._cancel_check_callback = callback

    async def _report_progress(self, state):
        self.progress_events.append(state)


def test_concurrent_pipeline_reports_render_progress():
    translator = _ProgressTranslator()
    pipeline = ConcurrentPipeline(translator, batch_size=1)
    pipeline._detection_ocr_thread = lambda *_args: None
    pipeline._translation_thread = lambda: None
    pipeline._inpaint_thread = lambda: None

    def render_one():
        time.sleep(0.7)
        pipeline.stats["rendering"] = 1
    pipeline._render_thread = render_one
    asyncio.run(pipeline.process_batch(["image.png"], [None]))
    assert translator.progress_events == ["batch:1:1:1:0:0"]



if __name__ == "__main__":
    test_concurrent_pipeline_reports_render_progress()
    print("concurrent pipeline progress test passed")
