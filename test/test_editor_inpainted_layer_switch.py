"""切图时上一张的修复图不得残留。

历史 bug：从有修复图的页切到没有修复图的页时，画布上仍显示上一张的修复图。
之前 `_load_inpainted_image` 用底图冒充修复图，靠"覆盖"压住了这个问题；现在
数据源如实返回 None，压制手段变成两条：

1. `EditorModel.apply_document_snapshot` 先发 image_changed 再发
   inpainted_image_changed —— 前者会走 `clear_all_state()`，把修复图层
   从 scene 移除并置 None；
2. `PixmapOverlayLayer.set_image(None)` 对已清空的图层是无害的空操作。

这两条是新方案的承重点，用例把它们钉死。

直接运行：uv run python test/test_editor_inpainted_layer_switch.py
"""

import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

try:  # Windows 上 torch 必须早于 Qt 加载，否则 c10.dll 初始化失败
    import torch  # noqa: F401
except Exception:  # noqa: BLE001 - 没装 torch 时按原顺序继续
    pass

from PyQt6.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from ui.editor.overlay_layer import OverlayLayerManager  # noqa: E402


class _FakeViewport:
    @staticmethod
    def update():
        pass


class _FakeView:
    """只提供 PixmapOverlayLayer 用到的那几个成员。

    scene 必须是真的 QGraphicsScene——图层的 clear() 依赖 Qt 自己的
    `item.scene()` 判断归属，假 scene 会让守卫恒假、测出假结论。
    """

    INPAINT_PREVIEW_MAX_PIXELS = 4_000_000

    def __init__(self):
        self.scene = QGraphicsScene()
        self._image_item = object()  # 非 None 即可，代表底图已就位

        class _Logger:
            @staticmethod
            def warning(*args, **kwargs):
                pass

        self.logger = _Logger()

    @staticmethod
    def _scale_mask_item(item):
        pass

    @staticmethod
    def viewport():
        return _FakeViewport()


def _inpainted_array(shade: int) -> np.ndarray:
    return np.full((24, 32, 3), shade, dtype=np.uint8)


def test_clear_removes_stale_inpainted_layer():
    """clear()（切图时由 clear_all_state 调用）必须把修复图层彻底摘掉。"""
    view = _FakeView()
    manager = OverlayLayerManager(view)

    manager.on_inpainted_image_changed(_inpainted_array(200))
    assert manager.inpainted.item is not None, "修复图层没建起来"
    assert manager.inpainted.item in view.scene.items()

    manager.clear()

    assert manager.inpainted.item is None, "切图后修复图层仍被持有"
    assert view.scene.items() == [], "切图后修复图层仍留在 scene 上"


def test_set_none_after_clear_is_noop():
    """clear() 之后再收到 inpainted_image_changed(None) 不得出错或复活图层。"""
    view = _FakeView()
    manager = OverlayLayerManager(view)

    manager.on_inpainted_image_changed(_inpainted_array(200))
    manager.clear()
    manager.on_inpainted_image_changed(None)

    assert manager.inpainted.item is None
    assert view.scene.items() == []


def test_set_none_hides_existing_layer():
    """没经过 clear 时，收到 None 也要把图层藏起来。"""
    view = _FakeView()
    manager = OverlayLayerManager(view)

    manager.on_inpainted_image_changed(_inpainted_array(200))
    item = manager.inpainted.item
    assert item.isVisible()

    manager.on_inpainted_image_changed(None)

    assert not item.isVisible(), "收到 None 后修复图层仍可见"


def test_snapshot_emits_image_before_inpainted():
    """承重顺序：image_changed 必须早于 inpainted_image_changed。

    前者触发 clear_all_state 摘掉旧修复图层；顺序反了就会先摘后建，
    上一张的修复图会残留到下一张上。
    """
    from PIL import Image

    from editor.core.resource_manager import ResourceManager
    from editor.editor_model import EditorModel
    from editor.session import DocumentSnapshot
    from services import ServiceManager

    class _StubContainer:
        """EditorModel 只从容器里取 resource_manager，不必起全套服务。"""

        def __init__(self):
            self.services = {"resource_manager": ResourceManager()}

        def get_service(self, name):
            return self.services.get(name)

        def register_service(self, name, instance):
            self.services[name] = instance

    previous = ServiceManager._container
    ServiceManager._container = _StubContainer()
    try:
        model = EditorModel()
        order = []
        model.image_changed.connect(lambda _: order.append("image"))
        model.inpainted_image_changed.connect(lambda _: order.append("inpainted"))

        model.apply_document_snapshot(
            DocumentSnapshot(
                source_path="page.png",
                image=Image.new("RGB", (32, 24), (10, 20, 30)),
                inpainted_image=None,
            )
        )

        assert "image" in order and "inpainted" in order, f"信号没发全: {order}"
        assert order.index("image") < order.index("inpainted"), f"信号顺序反了: {order}"
    finally:
        ServiceManager._container = previous


def main() -> int:
    failures = 0
    tests = [
        test_clear_removes_stale_inpainted_layer,
        test_set_none_after_clear_is_noop,
        test_set_none_hides_existing_layer,
        test_snapshot_emits_image_before_inpainted,
    ]
    for test in tests:
        try:
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
