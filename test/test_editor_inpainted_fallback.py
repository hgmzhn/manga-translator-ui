"""无修复图时的 inpainted 兜底行为回归。

历史上 `_load_inpainted_image` 在没有 `_inpainted.jpg` 时返回底图冒充修复图，
导致 `controller_document_service` 里
`default_alpha = 0.0 if snapshot.inpainted_image is not None else 1.0`
的条件恒真、`else 1.0` 成为死分支，同时把 PIL 底图混进了只该放 ndarray 的
temp_cache。现在数据源如实返回 None，需要"修复图否则底图"的地方各自显式兜底。

注意：画布层的耦合（无修复图时 z=2 原图层靠 alpha=1.0 显示）属于 UI 行为，
由手动验证覆盖，这里只锁数据层的契约。

直接运行：uv run python test/test_editor_inpainted_fallback.py
"""

import concurrent.futures
import logging
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from editor.controller_export_service import EditorControllerExportService  # noqa: E402
from editor.core.resource_manager import ResourceManager  # noqa: E402
from editor.document_load_worker import DocumentLoadWorker  # noqa: E402
from editor.session import DocumentSnapshot, EditorSession  # noqa: E402

LOGGER = logging.getLogger(__name__)


class _StubLoadService:
    logger = LOGGER
    controller = None


def test_missing_inpainted_file_yields_none():
    """没有修复图路径时不再拿底图冒充。"""
    worker = DocumentLoadWorker(_StubLoadService(), "unused.png", None)
    assert worker._load_inpainted_image(None, (32, 24)) == (None, None)


def test_failed_inpainted_load_yields_none(tmp_path):
    """修复图读取失败时同样返回 None，而不是退回底图。"""

    class _FailingController:
        @staticmethod
        def _load_detached_image_array(path, size):
            raise OSError("broken file")

    service = _StubLoadService()
    service.controller = _FailingController()
    worker = DocumentLoadWorker(service, "unused.png", None)

    assert worker._load_inpainted_image(str(tmp_path / "missing.png"), (32, 24)) == (
        None,
        None,
    )


def test_session_keeps_inpainted_none():
    """None 一路传到 session，不被中途替换成底图。"""
    manager = ResourceManager()
    session = EditorSession(manager)
    base = Image.new("RGB", (32, 24), (10, 20, 30))

    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=base,
            inpainted_image=None,
        )
    )

    assert session.get_inpainted_image() is None
    # 对照图缺省回退到底图，这条老行为不变
    assert session.get_compare_image() is base


class _StubHistoryService:
    def __init__(self):
        self.cleaned = False

    def mark_clean(self):
        self.cleaned = True


class _StubModel:
    def __init__(self, source_path, mask):
        self._source_path = source_path
        self._mask = mask

    def get_source_image_path(self):
        return self._source_path

    def get_refined_mask(self):
        return self._mask

    def get_raw_mask(self):
        return self._mask

    def get_paint_overlay_image(self):
        return None

    def get_stamp_overlay_image(self):
        return None

    def get_inpainted_image(self):
        return None  # 关键：没有修复图


class _StubController:
    logger = LOGGER

    def __init__(self, image, model):
        self._image = image
        self.model = model
        self.config_service = _StubConfigService()
        self.history_service = _StubHistoryService()

    def _get_current_image(self):
        return self._image

    def _get_regions(self):
        return []

    def _snapshot_image_for_export(self, image_obj, label):
        return None if image_obj is None else image_obj.copy()

    def get_toast_manager(self):
        return None


class _StubConfigService:
    @staticmethod
    def get_config():
        return {}


class _RecordingExportService(EditorControllerExportService):
    """把排队重活换成记录，只观察导出快照和落盘边界。"""

    def __init__(self, controller):
        super().__init__(controller)
        self.saved_json = False
        self.saved_inpainted = None
        self.submitted_job = None

    def _build_config_dict(self, config):
        return {"cli": {}}

    def _prepare_render_config(self, config_dict):
        return None

    def _build_output_path(self, config, source_path):
        return str(Path(source_path).with_name("out.png"))

    def save_editor_json(self, **kwargs):
        self.saved_json = True

    def save_inpainted_image(self, source_path, config_dict, image):
        self.saved_inpainted = image
        return "inpainted.png"

    def _submit_job(self, job):
        self.submitted_job = job
        future = concurrent.futures.Future()
        future.set_result(None)
        return future


def test_export_falls_back_to_base_image_without_saving_project(tmp_path):
    """没有修复图时导出快照兜底到底图，但不写工程数据。"""
    source_path = str(tmp_path / "page.png")
    base = Image.new("RGB", (32, 24), (200, 100, 50))
    base.save(source_path)

    mask = np.zeros((24, 32), dtype=np.uint8)
    controller = _StubController(base, _StubModel(source_path, mask))
    service = _RecordingExportService(controller)
    try:
        future = service.export_image(automatic=True)

        assert future is not None, "导出被拒绝了"
        assert service.saved_json is False
        assert service.saved_inpainted is None
        assert controller.history_service.cleaned is False
        assert service.submitted_job is not None
        assert service.submitted_job.inpainted_image is not None, "后端应仍收到修复图"
    finally:
        service.shutdown()


def main() -> int:
    import inspect
    import tempfile

    failures = 0
    tests = [
        test_missing_inpainted_file_yields_none,
        test_failed_inpainted_load_yields_none,
        test_session_keeps_inpainted_none,
        test_export_falls_back_to_base_image_without_saving_project,
    ]
    for test in tests:
        try:
            if "tmp_path" in inspect.signature(test).parameters:
                with tempfile.TemporaryDirectory() as directory:
                    test(Path(directory))
            else:
                test()
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {error}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
