import _bootstrap  # noqa: F401

import numpy as np
from PIL import Image

from editor.controller_document_service import EditorControllerDocumentService
from editor.controller_inpaint_service import EditorControllerInpaintService
from editor.core.resource_manager import ResourceManager
from editor.session import DocumentSnapshot


class _InpaintCacheController:
    CACHE_LAST_INPAINTED = "last_inpainted_image"
    CACHE_LAST_MASK = "last_processed_mask"
    WEAK_CACHE_BASE_IMAGE_RGB = "weak_base_image_rgb"

    def __init__(self):
        self.resource_manager = ResourceManager()


def test_clear_document_cache_removes_previous_page_state():
    controller = _InpaintCacheController()
    service = EditorControllerInpaintService(controller)
    inpainted = np.full((24, 32, 3), 200, dtype=np.uint8)
    mask = np.full((24, 32), 255, dtype=np.uint8)
    base_image = np.full((24, 32, 3), 20, dtype=np.uint8)

    controller.resource_manager.set_cache(controller.CACHE_LAST_INPAINTED, inpainted)
    controller.resource_manager.set_cache(controller.CACHE_LAST_MASK, mask)
    controller.resource_manager.set_weak_cache(controller.WEAK_CACHE_BASE_IMAGE_RGB, base_image)

    service.clear_document_cache()

    assert controller.resource_manager.get_cache(controller.CACHE_LAST_INPAINTED) is None
    assert controller.resource_manager.get_cache(controller.CACHE_LAST_MASK) is None
    assert controller.resource_manager.get_weak_cache(controller.WEAK_CACHE_BASE_IMAGE_RGB) is None


def test_inpaint_result_commit_rejects_stale_generation():
    class Model:
        image = None
        alpha = None

        def set_inpainted_image(self, image):
            self.image = image

        def set_original_image_alpha(self, alpha):
            self.alpha = alpha

    controller = _InpaintCacheController()
    controller._inpaint_request_generation = 2
    controller._user_adjusted_alpha = False
    controller.model = Model()
    service = EditorControllerInpaintService(controller)
    image = np.full((24, 32, 3), 180, dtype=np.uint8)
    mask = np.full((24, 32), 255, dtype=np.uint8)

    service.apply_inpaint_result((1, image, mask))
    assert controller.model.image is None

    service.apply_inpaint_result((2, image, mask))
    assert np.array_equal(controller.model.image, image)
    assert controller.model.alpha == 0.0
    assert np.array_equal(
        controller.resource_manager.get_cache(controller.CACHE_LAST_MASK), mask
    )


class _NoopAsyncService:
    @staticmethod
    def cancel_all_tasks():
        pass


class _NoopHistoryService:
    @staticmethod
    def clear():
        pass

    @staticmethod
    def mark_clean():
        pass


class _SnapshotModel:
    def __init__(self, inpaint_service):
        self.inpaint_service = inpaint_service
        self.snapshot = None
        self.original_image_alpha = None

    def set_original_image_alpha(self, alpha):
        self.original_image_alpha = alpha

    def apply_document_snapshot(self, snapshot):
        assert self.inpaint_service.cache_was_cleared
        self.snapshot = snapshot


class _RecordingInpaintService:
    def __init__(self):
        self.cache_was_cleared = False

    def invalidate_inpaint_requests(self):
        pass

    def clear_document_cache(self):
        self.cache_was_cleared = True


class _DocumentLoadController:
    def __init__(self):
        self._loading_toast = None
        self._user_adjusted_alpha = False
        self._pending_editor_prefetch_paths = []
        self.async_service = _NoopAsyncService()
        self.history_service = _NoopHistoryService()
        self.resource_manager = ResourceManager()
        self.inpaint_service = _RecordingInpaintService()
        self.model = _SnapshotModel(self.inpaint_service)

    @staticmethod
    def get_toolbar():
        return None

    @staticmethod
    def get_graphics_view():
        return None

    @staticmethod
    def _update_undo_redo_buttons():
        pass

    @staticmethod
    def _log_memory_snapshot(_stage):
        pass


def test_loading_snapshot_resets_inpaint_cache_before_applying_document():
    controller = _DocumentLoadController()
    service = EditorControllerDocumentService(controller)
    snapshot = DocumentSnapshot(
        source_path="next-page.png",
        image=Image.new("RGB", (32, 24)),
        inpainted_image=None,
    )

    try:
        service.apply_loaded_data_to_model(snapshot)
    finally:
        service.shutdown()

    assert controller.model.snapshot is snapshot
    assert controller.model.original_image_alpha == 1.0


def main() -> int:
    tests = [
        test_clear_document_cache_removes_previous_page_state,
        test_loading_snapshot_resets_inpaint_cache_before_applying_document,
    ]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
