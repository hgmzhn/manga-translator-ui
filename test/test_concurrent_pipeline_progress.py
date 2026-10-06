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



def test_concurrent_pipeline_emits_page_stage_progress_and_isolates_callback_errors():
    translator = _ProgressTranslator()
    events = []
    pipeline = ConcurrentPipeline(
        translator,
        batch_size=1,
        progress_callback=events.append,
    )

    pipeline._notify_progress("page-07.png", "recognize", "running", "ocr")
    pipeline._notify_progress("page-07.png", "translate", "skipped", "no-text")

    assert events == [
        {
            "image_name": "page-07.png",
            "stage": "recognize",
            "state": "running",
            "detail": "ocr",
        },
        {
            "image_name": "page-07.png",
            "stage": "translate",
            "state": "skipped",
            "detail": "no-text",
        },
    ]

    def failing_callback(_event):
        raise RuntimeError("stream disconnected")

    pipeline.progress_callback = failing_callback
    pipeline._notify_progress("page-08.png", "inpaint", "error", "inpainting", "failed")


if __name__ == "__main__":
    test_concurrent_pipeline_reports_render_progress()
    test_concurrent_pipeline_emits_page_stage_progress_and_isolates_callback_errors()
    print("concurrent pipeline progress test passed")
