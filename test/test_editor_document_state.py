import _bootstrap  # noqa: F401, I001

import numpy as np

from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from editor.inpaint_state import InpaintArtifact, MaskDelta
from editor.session import EditorSession


def _source(value: int) -> np.ndarray:
    return np.full((5, 7, 3), value, dtype=np.uint8)


def _mask() -> np.ndarray:
    mask = np.zeros((5, 7), dtype=np.uint8)
    mask[1:4, 2:6] = 255
    return mask


def _session() -> EditorSession:
    return EditorSession()


def test_document_owns_one_immutable_source_rgb_projection():
    session = _session()
    source = _source(41)
    session.load_document(DocumentSnapshot(source_path="page-a.png", image=source))

    layers = session.get_display_layers()
    source_rgb = session.get_source_rgb()

    assert layers is not None
    assert layers.source_path == "page-a.png"
    assert layers.source_image is source
    assert layers.inpaint_display_image is source
    assert source_rgb is session.get_source_rgb()
    assert not source_rgb.flags.writeable
    assert not np.shares_memory(source_rgb, source)
    assert session.get_committed_inpaint_artifact() is None


def test_user_source_opacity_survives_inpaint_and_document_switches():
    session = _session()
    session.load_document(
        DocumentSnapshot(source_path="page-a.png", image=_source(31), raw_mask=_mask())
    )
    session.set_original_image_alpha_override(0.37)
    artifact = InpaintArtifact(
        session.get_inpaint_key(),
        _mask(),
        _source(173),
    )

    assert session.install_inpaint_artifact(artifact)
    assert session.get_display_layers().source_opacity == 0.37

    session.clear_document()
    session.load_document(DocumentSnapshot(source_path="page-b.png", image=_source(89)))

    assert session.get_original_image_alpha() == 0.37
    assert session.get_display_layers().source_opacity == 0.37


def test_mask_edit_keeps_last_committed_repair_visible_while_job_runs():
    session = _session()
    source = _source(31)
    mask = _mask()
    session.load_document(
        DocumentSnapshot(source_path="page.png", image=source, raw_mask=mask)
    )
    artifact = InpaintArtifact(
        session.get_inpaint_key(),
        mask,
        _source(177),
    )
    assert session.install_inpaint_artifact(artifact)

    extended = mask.copy()
    extended[0, 0] = 255
    mutation = session.replace_masks(refined=extended)

    assert mutation.delta is not None
    assert session.get_ready_inpaint_artifact() is None
    assert np.array_equal(
        session.get_display_layers().inpaint_display_image,
        artifact.image,
    )
    assert not np.array_equal(
        session.get_display_layers().inpaint_display_image,
        source,
    )


def test_document_switch_clears_old_identity_and_rejects_late_inpaint_result():
    session = _session()
    mask = _mask()
    first_source = _source(19)
    session.load_document(
        DocumentSnapshot(source_path="page-a.png", image=first_source, raw_mask=mask)
    )
    first_identity = session.get_document_identity()
    late_artifact = InpaintArtifact(
        session.get_inpaint_key(),
        mask,
        _source(211),
    )

    session.clear_document()
    cleared_identity = session.get_document_identity()
    session.load_document(
        DocumentSnapshot(source_path="page-b.png", image=_source(67), raw_mask=mask)
    )

    assert cleared_identity is None
    assert first_identity is not None
    assert session.get_document_identity() != first_identity
    assert not session.install_inpaint_artifact(late_artifact)
    layers = session.get_display_layers()
    assert layers.source_path == "page-b.png"
    assert np.all(layers.source_image == 67)
    assert np.all(layers.inpaint_display_image == 67)
    assert session.get_committed_inpaint_artifact() is None


def test_matching_generated_artifact_is_installed_only_for_current_identity():
    session = _session()
    mask = _mask()
    session.load_document(
        DocumentSnapshot(source_path="page.png", image=_source(23), raw_mask=mask)
    )
    artifact = InpaintArtifact(session.get_inpaint_key(), mask, _source(151))

    assert session.install_inpaint_artifact(artifact)
    layers = session.get_display_layers()
    assert np.array_equal(layers.inpaint_display_image, artifact.image)
    assert session.get_committed_inpaint_artifact() is not None

    session.clear_document()
    assert not session.install_inpaint_artifact(artifact)
    assert session.get_committed_inpaint_artifact() is None


def test_raw_and_refined_ui_changes_emit_one_effective_mask_delta():
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(source_path="page.png", image=_source(29))
    )
    raw_events = []
    refined_events = []
    effective_events = []
    model.raw_mask_changed.connect(raw_events.append)
    model.refined_mask_changed.connect(refined_events.append)
    model.effective_mask_delta_changed.connect(
        lambda mask, delta: effective_events.append((mask, delta))
    )
    mask = _mask()

    model.set_masks(raw=mask, refined=mask)

    assert len(raw_events) == 1
    assert len(refined_events) == 1
    assert len(effective_events) == 1
    effective_mask, delta = effective_events[0]
    assert isinstance(delta, MaskDelta)
    assert np.array_equal(effective_mask, mask)
    assert np.array_equal(delta.added, mask)
    assert not np.any(delta.removed)


def test_repair_delta_reprocesses_an_unchanged_mask():
    model = EditorModel()
    mask = _mask()
    model.apply_document_snapshot(
        DocumentSnapshot(source_path="page.png", image=_source(29), raw_mask=mask)
    )
    original_key = model.get_inpaint_key()
    artifact = InpaintArtifact(original_key, mask, _source(173))
    assert model.install_inpaint_artifact(artifact)

    refined_events = []
    effective_events = []
    model.refined_mask_changed.connect(refined_events.append)
    model.effective_mask_delta_changed.connect(
        lambda effective, delta: effective_events.append((effective, delta))
    )
    stroke = np.zeros_like(mask)
    stroke[2, 3:5] = 255

    mutation = model.set_refined_mask(mask.copy(), repair=stroke)

    assert not mutation.refined_changed
    assert refined_events == []
    assert len(effective_events) == 1
    effective, delta = effective_events[0]
    assert np.array_equal(effective, mask)
    assert np.array_equal(delta.added, mask)
    assert not np.any(delta.removed)
    assert model.get_inpaint_key().mask_revision == original_key.mask_revision + 1
    assert model.get_ready_inpaint_artifact() is None
    assert np.array_equal(
        model.get_display_layers().inpaint_display_image,
        artifact.image,
    )


def test_repair_delta_expands_only_to_touched_connected_components():
    model = EditorModel()
    mask = np.zeros((5, 9), dtype=np.uint8)
    mask[1:4, 1:4] = 255
    mask[1:4, 6:8] = 255
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.zeros((5, 9, 3), dtype=np.uint8),
            raw_mask=mask,
        )
    )
    stroke = np.zeros_like(mask)
    stroke[2, 2] = 255
    events = []
    model.effective_mask_delta_changed.connect(lambda _, delta: events.append(delta))

    model.set_refined_mask(mask.copy(), repair=stroke)

    expected = np.zeros_like(mask)
    expected[1:4, 1:4] = 255
    assert len(events) == 1
    assert np.array_equal(events[0].added, expected)
    assert not np.any(events[0].removed)


def test_new_bridge_repairs_joined_component_but_undo_removes_only_bridge():
    model = EditorModel()
    mask = np.zeros((3, 7), dtype=np.uint8)
    mask[:, 1:3] = 255
    mask[:, 4:6] = 255
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=np.zeros((3, 7, 3), dtype=np.uint8),
            raw_mask=mask,
        )
    )
    bridge = np.zeros_like(mask)
    bridge[1, 3] = 255
    joined = np.maximum(mask, bridge)

    added = model.set_refined_mask(joined, repair=bridge).delta
    removed = model.set_refined_mask(mask).delta

    assert np.array_equal(added.added, joined)
    assert not np.any(added.removed)
    assert not np.any(removed.added)
    assert np.array_equal(removed.removed, bridge)


def test_empty_mask_export_uses_the_current_source_image():
    session = _session()
    source = _source(53)
    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=source,
            raw_mask=np.zeros((5, 7), dtype=np.uint8),
        )
    )

    export_base = session.get_export_base()

    assert export_base.kind == "source"
    assert export_base.source_image is source
    assert export_base.render_image is source
    assert export_base.mask is None
    assert export_base.inpaint_key is None


def test_paired_artifact_export_uses_only_its_strictly_paired_image_and_mask():
    session = _session()
    source = _source(61)
    mask = _mask()
    session.load_document(
        DocumentSnapshot(source_path="page.png", image=source, raw_mask=mask)
    )
    artifact = InpaintArtifact(session.get_inpaint_key(), mask, _source(197))
    assert session.install_inpaint_artifact(artifact)

    export_base = session.get_export_base()

    assert export_base.kind == "paired"
    assert export_base.source_image is source
    assert np.array_equal(export_base.render_image, artifact.image)
    assert np.array_equal(export_base.mask, artifact.mask)
    assert export_base.inpaint_key == artifact.key


def test_nonempty_mask_without_artifact_requires_backend_inpaint():
    session = _session()
    source = _source(71)
    mask = _mask()
    session.load_document(
        DocumentSnapshot(source_path="page.png", image=source, raw_mask=mask)
    )

    export_base = session.get_export_base()

    assert export_base.kind == "backend_inpaint"
    assert export_base.source_image is source
    assert export_base.render_image is source
    assert np.array_equal(export_base.mask, mask)
    assert export_base.inpaint_key == session.get_inpaint_key()
    assert session.get_committed_inpaint_artifact() is None


def test_brush_and_eraser_sizes_are_independently_stored_and_switched():
    """测试画笔与橡皮擦尺寸以及各工具组（蒙版/画笔/印章）在 EditorSession 中独立存储与切换。"""
    session = _session()
    assert session.get_brush_size() == 30
    assert session.get_eraser_size() == 30

    # 1. 蒙版组（mask）：画笔与橡皮擦独立设置
    assert session.set_brush_size(15, group="mask")
    assert session.get_brush_size("mask") == 15
    assert session.get_eraser_size("mask") == 30

    assert session.set_eraser_size(60, group="mask")
    assert session.get_eraser_size("mask") == 60
    assert session.get_brush_size("mask") == 15

    session.set_active_tool("brush")
    assert not session.is_eraser_tool()
    assert session.get_active_group() == "mask"
    assert session.get_active_brush_size() == 15

    session.set_active_tool("eraser")
    assert session.is_eraser_tool()
    assert session.get_active_group() == "mask"
    assert session.get_active_brush_size() == 60

    # 2. 画笔组（paint）：画笔与橡皮擦独立，且与蒙版组隔离
    session.set_active_tool("paint")
    assert not session.is_eraser_tool()
    assert session.get_active_group() == "paint"
    assert session.get_active_brush_size() == 30  # 默认值
    assert session.set_active_brush_size(61)
    assert session.get_brush_size("paint") == 61
    assert session.get_brush_size("mask") == 15

    session.set_active_tool("paint_erase")
    assert session.is_eraser_tool()
    assert session.get_active_group() == "paint"
    assert session.get_active_brush_size() == 30  # 默认值
    assert session.set_active_brush_size(22)
    assert session.get_eraser_size("paint") == 22
    assert session.get_eraser_size("mask") == 60

    # 3. 印章组（stamp）：印章与橡皮擦独立，且与其他组隔离
    session.set_active_tool("clone")
    assert not session.is_eraser_tool()
    assert session.get_active_group() == "stamp"
    assert session.get_active_brush_size() == 30
    assert session.set_active_brush_size(93)
    assert session.get_brush_size("stamp") == 93

    session.set_active_tool("stamp_erase")
    assert session.is_eraser_tool()
    assert session.get_active_group() == "stamp"
    assert session.get_active_brush_size() == 30
    assert session.set_active_brush_size(55)
    assert session.get_eraser_size("stamp") == 55

    # 4. 再次遍历所有工具，验证 6 种尺寸全部完整保留互不覆盖
    for tool, expected in (
        ("brush", 15),
        ("pen", 15),
        ("eraser", 60),
        ("paint", 61),
        ("paint_erase", 22),
        ("clone", 93),
        ("stamp_erase", 55),
    ):
        session.set_active_tool(tool)
        assert session.get_active_brush_size() == expected


def test_editor_model_emits_brush_and_eraser_size_signals_independently():
    """测试 EditorModel 针对画笔与橡皮擦独立发射尺寸变更信号。"""
    model = EditorModel()
    brush_events = []
    eraser_events = []
    model.brush_size_changed.connect(brush_events.append)
    model.eraser_size_changed.connect(eraser_events.append)

    model.set_brush_size(18)
    assert brush_events == [18]
    assert eraser_events == []

    model.set_eraser_size(55)
    assert brush_events == [18]
    assert eraser_events == [55]

    model.set_active_tool("eraser")
    model.set_active_brush_size(70)
    assert brush_events == [18]
    assert eraser_events == [55, 70]

    model.set_active_tool("brush")
    model.set_active_brush_size(22)
    assert brush_events == [18, 22]
    assert eraser_events == [55, 70]


def test_editor_ui_interaction_brush_and_eraser_size_switching():
    """测试界面交互：画笔与橡皮擦切换、多工具页独立记忆、滑块与标题响应、画布 Shift+滚轮事件联动。"""
    from types import SimpleNamespace
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtWidgets import QApplication, QWidget
    from services import ServiceManager
    from ui.editor.graphics_view import GraphicsView
    from ui.editor.shortcut_manager import EditorShortcutManager
    from ui.widgets.property_panel import PropertyPanel

    ServiceManager.initialize(str(_bootstrap.ROOT))
    _app = QApplication.instance() or QApplication([])

    class _DummyView(QWidget):
        def __init__(self, m, gv, p):
            super().__init__()
            self.model = m
            self.graphics_view = gv
            self.property_panel = p
            self.controller = None
            self.file_list = SimpleNamespace(
                select_prev_image=lambda: None,
                select_next_image=lambda: None,
            )

    model = EditorModel()
    graphics_view = GraphicsView(model)
    panel = PropertyPanel(model, None)

    # 接线：与 EditorView 的实际信号槽连接完全一致
    panel.mask_tool_changed.connect(model.set_active_tool)
    panel.brush_size_changed.connect(model.set_brush_size)
    panel.eraser_size_changed.connect(model.set_eraser_size)
    model.brush_size_changed.connect(panel.on_model_brush_size_changed)
    model.eraser_size_changed.connect(panel.on_model_eraser_size_changed)
    model.active_tool_changed.connect(panel.sync_active_tool_from_model)

    dummy_view = _DummyView(model, graphics_view, panel)
    shortcut_mgr = EditorShortcutManager(dummy_view)

    # 1. 蒙版页：设置画笔尺寸为 18
    model.set_active_tool("brush")
    panel.brush_size_slider.setValue(18)
    assert model.get_brush_size("mask") == 18
    assert model.get_eraser_size("mask") == 30
    assert graphics_view._brush_size == 18
    assert panel.brush_size_title_label.text() == panel._t("Brush Size:")

    # 2. 蒙版页：切换至橡皮擦工具，设置橡皮擦尺寸为 72
    model.set_active_tool("eraser")
    assert panel.brush_size_title_label.text() == panel._t("Eraser Size:")
    assert panel.brush_size_slider.value() == 30
    assert graphics_view._brush_size == 30

    panel.brush_size_slider.setValue(72)
    assert model.get_eraser_size("mask") == 72
    assert model.get_brush_size("mask") == 18
    assert graphics_view._brush_size == 72

    # 3. 在橡皮擦状态下测试画布 Shift+滚轮事件
    wheel_up = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ShiftModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    shortcut_mgr.eventFilter(graphics_view.viewport(), wheel_up)
    assert model.get_eraser_size("mask") == 73
    assert model.get_brush_size("mask") == 18
    assert panel.brush_size_slider.value() == 73
    assert graphics_view._brush_size == 73

    # 4. 切换到 draw_textbox 模式，验证标题和滑块切回画笔且不修改橡皮擦
    model.set_active_tool("draw_textbox")
    assert panel.brush_size_title_label.text() == panel._t("Brush Size:")
    assert panel.brush_size_slider.value() == 18
    assert model.get_brush_size("mask") == 18
    assert model.get_eraser_size("mask") == 73

    # 5. 切换到画笔页（Tab 1）并激活画笔，调整画笔尺寸为 61
    panel.activate_image_edit_tab(1)
    panel.activate_image_edit_tool(1)  # paint brush
    assert model.get_active_tool() == "paint"
    panel.paint_size_slider.setValue(61)
    assert model.get_brush_size("paint") == 61
    assert model.get_brush_size("mask") == 18  # 蒙版画笔未被污染
    assert panel.brush_size_slider.value() == 18

    # 6. 切换到印章页（Tab 2）并激活印章，调整印章尺寸为 93
    panel.activate_image_edit_tab(2)
    panel.activate_image_edit_tool(1)  # clone stamp
    assert model.get_active_tool() == "clone"
    panel.stamp_size_slider.setValue(93)
    assert model.get_brush_size("stamp") == 93
    assert model.get_brush_size("paint") == 61  # 画笔页未被污染
    assert model.get_brush_size("mask") == 18  # 蒙版页未被污染

    # 7. 在印章工具下触发 Shift+滚轮，验证只影响印章组
    shortcut_mgr.eventFilter(graphics_view.viewport(), wheel_up)
    assert model.get_brush_size("stamp") == 94
    assert model.get_brush_size("paint") == 61
    assert model.get_brush_size("mask") == 18
    assert panel.stamp_size_slider.value() == 94

    # 8. 切回蒙版页橡皮擦，验证蒙版橡皮擦尺寸 73 完整保留
    panel.activate_image_edit_tab(0)
    panel.activate_image_edit_tool(2)  # eraser
    assert model.get_active_tool() == "eraser"
    assert panel.brush_size_title_label.text() == panel._t("Eraser Size:")
    assert panel.brush_size_slider.value() == 73
    assert graphics_view._brush_size == 73



