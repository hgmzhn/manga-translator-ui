import asyncio
from contextlib import suppress

from manga_translator.mode.share import MangaShare


def _service_for_lock_test() -> MangaShare:
    service = MangaShare.__new__(MangaShare)
    service.progress_queue = asyncio.Queue()
    service.lock = asyncio.Lock()
    service._lock_waiters = 0
    service._active_job = None
    service._active_job_started_at = None
    return service


def test_shared_backend_requests_wait_instead_of_returning_429():
    async def scenario():
        service = _service_for_lock_test()
        await service._acquire_lock("first")

        second = asyncio.create_task(service._acquire_lock("second"))
        await asyncio.sleep(0)
        assert not second.done()
        assert service._lock_waiters == 1

        service._release_lock()
        await asyncio.wait_for(second, timeout=0.2)
        assert service._active_job == "second"
        service._release_lock()

    asyncio.run(scenario())


def test_disconnected_stream_cancels_worker_and_releases_lock():
    async def scenario():
        service = _service_for_lock_test()
        await service._acquire_lock("stream-job")
        worker_started = asyncio.Event()

        async def worker():
            worker_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                service._release_lock()

        worker_task = asyncio.create_task(worker())
        stream = service._stream_with_disconnect_cleanup(
            worker_task,
            service.progress_queue,
        )
        next_frame = asyncio.create_task(stream.__anext__())
        await asyncio.wait_for(worker_started.wait(), timeout=0.2)

        next_frame.cancel()
        with suppress(asyncio.CancelledError):
            await next_frame
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(worker_task, timeout=0.2)
        assert worker_task.done()
        assert not service.lock.locked()

    asyncio.run(scenario())
