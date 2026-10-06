import _bootstrap  # noqa: F401

import asyncio
import base64
import io
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from manga_translator.mode import share as share_module
from manga_translator.mode.share import MangaShare, PAGE_PROGRESS_VERSION


def test_backend_info_advertises_page_progress_protocol(monkeypatch):
    service = MangaShare.__new__(MangaShare)
    service.host = "127.0.0.1"
    service.port = 5003
    service.started_at = time.time()

    captured = {}

    async def capture_app(self):
        captured["app"] = self.config.app

    monkeypatch.setattr(share_module.uvicorn.Server, "serve", capture_app)

    async def scenario():
        await service.listen()
        route = next(route for route in captured["app"].routes if route.path == "/backend_info")
        return await route.endpoint()

    info = asyncio.run(scenario())
    assert info["service"] == "manga-translator-ui"
    assert info["protocol"] == share_module.SHARED_BACKEND_PROTOCOL
    assert info["pageProgressVersion"] == PAGE_PROGRESS_VERSION == 1


def test_batch_progress_events_keep_original_page_indices_and_monotonic_sequences(tmp_path):
    service = MangaShare.__new__(MangaShare)
    service.save_info = {"output_folder": str(tmp_path)}
    service.desktop_config = {}
    service.progress_queue = asyncio.Queue()
    service._release_lock = lambda: None

    class FakeManga:
        def __init__(self):
            self.verbose = False
            self.batch_concurrent = False
            self._runtime_result_root = None
            self._stream_result_callback = None
            self._stream_progress_callback = None

        async def translate_batch(self, images_with_configs, save_info):
            async def process(image, _config):
                await asyncio.sleep(0)
                self._stream_progress_callback({
                    "image_name": image.name,
                    "stage": "recognize",
                    "state": "running",
                    "detail": "ocr",
                })
                self._stream_progress_callback({
                    "image_name": image.name,
                    "stage": "recognize",
                    "state": "done",
                    "detail": "ocr",
                })
                filename = Path(image.name).name
                result = Image.new("RGB", (2, 2), (30, 60, 90))
                output_path = Path(save_info["output_folder"]) / filename
                result.save(output_path, format="PNG")
                context = SimpleNamespace(
                    image_name=image.name,
                    result=result,
                    output_path=str(output_path),
                    translation_error=None,
                )
                self._stream_result_callback(context)
                return context

            return await asyncio.gather(*(process(image, config) for image, config in images_with_configs))

    service.manga = FakeManga()

    image_buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (0, 0, 0)).save(image_buffer, format="PNG")
    encoded_image = base64.b64encode(image_buffer.getvalue()).decode("ascii")
    payload = {
        "taskId": "task_12345678",
        "runId": "run-test-01",
        "sourceUrl": "https://manga18.club/manhwa/example/8",
        "outputFolder": str(tmp_path),
        "images": [
            {"image": encoded_image, "filename": "page-a.png", "pageIndex": 4, "imageUrl": "https://cdn.test/a.png"},
            {"image": encoded_image, "filename": "page-b.png", "pageIndex": 9, "imageUrl": "https://cdn.test/b.png"},
        ],
    }

    async def scenario():
        await service.run_batch_image_method(payload)
        frames = []
        while not service.progress_queue.empty():
            frame = await service.progress_queue.get()
            status = frame[0]
            length = int.from_bytes(frame[1:5], "big")
            data = frame[5:5 + length]
            if status in (0, 3):
                frames.append((status, share_module.json.loads(data)))
        return frames

    frames = asyncio.run(scenario())
    progress = [event for status, event in frames if status == 0 and event.get("type") == "page-progress"]
    assert {event["pageIndex"] for event in progress} == {4, 9}
    assert all(event["taskId"] == payload["taskId"] for event in progress)
    assert all(event["runId"] == payload["runId"] for event in progress)
    assert [event["sequence"] for event in progress] == list(range(1, len(progress) + 1))
    assert all(event["stage"] in {"prepare", "recognize", "result"} for event in progress)
    assert len([event for status, event in frames if status == 0 and event.get("type") == "result"]) == 2
    assert any(status == 3 and event.get("type") == "done" for status, event in frames)
