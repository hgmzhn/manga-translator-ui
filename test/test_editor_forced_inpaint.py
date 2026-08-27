"""画笔强制重修（已修复区域再次手动修复）的回归测试。

只在 _dispatch_inpaint 和配置快照两处打桩，其余走真实的 EditorModel / EditorSession /
EditorDocument / InpaintState，以便覆盖真实的代数号与 artifact 编排。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging

import numpy as np
import pytest

from editor.commands import BrushStrokeCommand
from editor.controller_inpaint_service import EditorControllerInpaintService
from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from editor.inpaint_state import (
    INPAINT_BBOX_PADDING,
    InpaintArtifact,
    InpaintConfigSnapshot,
    MaskDelta,
)

H, W = 120, 160
SOURCE_FILL = 10
COMMITTED_FILL = 90
DISPATCH_FILL = 200


class _SyncAsyncService:
    """同步跑完协程，让 add_done_callback 立即回调，测试无需等异步。"""

    def submit_task(self, coro):
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            future.set_result(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001 - 转交给 _emit_inpaint_result
            future.set_exception(exc)
        return future


class _DirectSignal:
    def __init__(self):
        self._sink = None

    def connect(self, sink):
        self._sink = sink

    def emit(self, value):
        if self._sink is not None:
            self._sink(value)


class _FakeController:
    def __init__(self, model):
        self.model = model
        self.logger = logging.getLogger("test.forced_inpaint")
        self.async_service = _SyncAsyncService()
        self._inpaint_result_ready = _DirectSignal()


def _source_image() -> np.ndarray:
    return np.full((H, W, 3), SOURCE_FILL, dtype=np.uint8)


def _rect_mask(y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[y0:y1, x0:x1] = 255
    return mask


@pytest.fixture
def env(monkeypatch):
    """带一个已提交 artifact 的编辑器：蒙版覆盖 (20:60, 20:60)，修复图为纯色。"""
    model = EditorModel()
    mask = _rect_mask(20, 60, 20, 60)
    model.session.load_document(
        DocumentSnapshot(source_path="t.png", image=_source_image(), raw_mask=mask)
    )

    controller = _FakeController(model)
    service = EditorControllerInpaintService(controller)
    controller._inpaint_result_ready.connect(service.apply_inpaint_result)
    # 与 editor_controller.py:235 的真实接线一致；不连这条，抑制类断言会空跑通过。
    model.effective_mask_delta_changed.connect(service.on_effective_mask_delta_changed)

    monkeypatch.setattr(
        EditorControllerInpaintService,
        "_snapshot_inpaint_config",
        lambda self: InpaintConfigSnapshot("lama_large", "fp32", False, 2048, "cpu"),
    )

    calls: list[dict] = []

    async def fake_dispatch(request, image, mask_arg):
        calls.append(
            {
                "image": np.array(image, copy=True),
                "mask": np.array(mask_arg, copy=True),
                "shape": image.shape,
            }
        )
        return np.full(image.shape, DISPATCH_FILL, dtype=np.uint8)

    monkeypatch.setattr(
        EditorControllerInpaintService, "_dispatch_inpaint", staticmethod(fake_dispatch)
    )

    # 装入一份“已经修复过”的 artifact，模拟流水线或先前笔画的结果。
    committed = InpaintArtifact(
        model.get_inpaint_key(),
        mask,
        np.full((H, W, 3), COMMITTED_FILL, dtype=np.uint8),
    )
    assert model.install_inpaint_artifact(committed)

    return {
        "model": model,
        "service": service,
        "calls": calls,
        "mask": mask,
    }


# --- bump_inpaint_revision -------------------------------------------------


def test_bump_keeps_artifact_ready_and_export_paired(env):
    """推进代数号不能让导出退化成 backend_inpaint，否则会丢掉已有修复图。"""
    model = env["model"]
    document = model.session._document
    before = model.get_inpaint_key()

    key = model.bump_inpaint_revision()

    assert key.mask_revision == before.mask_revision + 1
    ready = document.ready_inpaint_artifact()
    assert ready is not None, "重贴 key 失败，异步窗口内 artifact 会失效"
    assert ready.key == key
    assert document.export_base().kind == "paired"
    # 只换 key，mask 必须保持真实，供后续 delta 相减使用。
    assert np.array_equal(ready.mask, env["mask"])
    assert int(ready.image[0, 0, 0]) == COMMITTED_FILL


def test_stale_result_rejected_after_bump(env):
    """代数号推进后，笔画之前在途的旧结果必须装不进去。"""
    model = env["model"]
    stale_key = model.get_inpaint_key()
    model.bump_inpaint_revision()

    stale = InpaintArtifact(
        stale_key, env["mask"], np.zeros((H, W, 3), dtype=np.uint8)
    )
    assert model.install_inpaint_artifact(stale) is False


# --- 核心回归：已修复区域重复涂抹 ------------------------------------------


def test_repeat_brush_inside_repaired_area_dispatches_again(env):
    """本次修复的主目标：整笔落在已修复蒙版内，仍要真正重跑一次修复器。"""
    model, service, calls = env["model"], env["service"], env["calls"]
    mask = env["mask"]
    stroke = _rect_mask(30, 40, 30, 40)  # 完全位于 (20:60, 20:60) 之内

    # 修复前的行为：涂在已有蒙版内不改变蒙版，常规 delta 路径整条丢弃这一笔。
    unchanged = mask.copy()
    unchanged[stroke > 0] = 255
    assert np.array_equal(unchanged, mask), "该笔本就不改变蒙版，才是这个 bug 的前提"
    delta = MaskDelta(
        added=unchanged,
        removed=np.zeros_like(unchanged),
        mask_revision=model.get_inpaint_key().mask_revision,
    )
    assert service.build_inpaint_request(unchanged, delta) is None

    # 修复后的行为：强制路径绕开上述判定，真正派发一次。
    service.force_inpaint_stroke(stroke)

    assert len(calls) == 1, "重复涂抹被当成 no-op 丢弃了"
    # 迭代精修：输入必须是当前修复图，而不是底图。
    assert int(calls[0]["image"][0, 0, 0]) == COMMITTED_FILL
    assert int(np.max(calls[0]["mask"])) == 255


def test_normal_delta_path_still_skips_noop(env):
    """对照组：常规 delta 路径仍应丢弃无变化的蒙版（未破坏既有行为）。"""
    service = env["service"]
    mask = env["mask"]
    delta = MaskDelta(
        added=mask,
        removed=np.zeros_like(mask),
        mask_revision=env["model"].get_inpaint_key().mask_revision,
    )
    assert service.build_inpaint_request(mask, delta) is None


def test_forced_region_is_clipped_to_current_mask(env):
    """笔画溢出蒙版的部分不能进修复器，否则会改写本该保持原样的像素。"""
    service, calls = env["service"], env["calls"]
    mask = env["mask"]
    stroke = _rect_mask(30, 40, 50, 100)  # 右半截伸到蒙版之外

    service.force_inpaint_stroke(stroke)

    assert len(calls) == 1
    call = calls[0]
    # 还原 bbox 偏移，把 dispatch 蒙版放回全图坐标系比对。
    ys, xs = np.where(stroke > 0)
    y0 = max(0, int(np.min(ys)) - INPAINT_BBOX_PADDING)
    x0 = max(0, int(np.min(xs)) - INPAINT_BBOX_PADDING)
    full = np.zeros((H, W), dtype=np.uint8)
    sub = call["mask"]
    full[y0 : y0 + sub.shape[0], x0 : x0 + sub.shape[1]] = sub
    assert not np.any((full > 0) & (mask == 0)), "强制区域越出了当前蒙版"
    assert np.array_equal(full > 0, (stroke > 0) & (mask > 0))


def test_forced_stroke_outside_mask_is_dropped(env):
    """完全在蒙版外的笔画求交后为空，不应触发修复。"""
    service, calls = env["service"], env["calls"]
    before = env["model"].get_inpaint_key()

    service.force_inpaint_stroke(_rect_mask(90, 100, 90, 100))

    assert calls == []
    assert env["model"].get_inpaint_key() == before, "空笔画不应推进代数号"


def test_forced_result_installs_and_becomes_display_image(env):
    """强制重修的结果要落到 committed / 显示图上。"""
    model, service = env["model"], env["service"]
    service.force_inpaint_stroke(_rect_mask(30, 40, 30, 40))

    committed = model.get_committed_inpaint_artifact()
    assert committed is not None
    assert committed.key == model.get_inpaint_key()
    # bbox 内被换成 dispatch 结果，bbox 外仍是原修复图。
    assert int(committed.image[35, 35, 0]) == DISPATCH_FILL
    assert int(committed.image[H - 1, W - 1, 0]) == COMMITTED_FILL
    assert model.session._document.inpaint_display_image is committed.image


def test_suspend_auto_inpaint_blocks_delta_path(env):
    """抑制期内常规 delta 修复不得启动，避免和强制重修抢同一份工作。"""
    model, service, calls = env["model"], env["service"], env["calls"]
    with service.suspend_auto_inpaint():
        model.set_refined_mask(_rect_mask(20, 60, 20, 80))
    assert calls == []


def test_delta_path_still_repairs_when_not_suspended(env):
    """守护既有行为：非抑制状态下扩大蒙版仍应触发常规增量修复。"""
    model, service, calls = env["model"], env["service"], env["calls"]
    model.set_refined_mask(_rect_mask(20, 60, 20, 80))
    assert len(calls) == 1, "常规 delta 修复被误伤"
    assert service is not None


def test_suspend_restores_counter_after_exception(env):
    """抑制是引用计数式的，异常路径也必须恢复，否则自动修复会永久失效。"""
    service, calls = env["service"], env["calls"]
    with pytest.raises(RuntimeError):
        with service.suspend_auto_inpaint():
            raise RuntimeError("boom")
    assert service._suspend_auto_inpaint == 0
    env["model"].set_refined_mask(_rect_mask(20, 60, 20, 80))
    assert len(calls) == 1


# --- BrushStrokeCommand 撤销/重做 ------------------------------------------


def _make_command(env, stroke, new_mask=None):
    model = env["model"]
    old_mask = model.get_refined_mask()
    return BrushStrokeCommand(
        model=model,
        service=env["service"],
        old_mask=old_mask,
        new_mask=env["mask"].copy() if new_mask is None else new_mask,
        stroke_mask=stroke,
    )


def test_command_redo_repairs_then_undo_restores_previous_image(env):
    model, calls = env["model"], env["calls"]
    stroke = _rect_mask(30, 40, 30, 40)
    command = _make_command(env, stroke)

    command.redo()
    assert len(calls) == 1
    assert int(model.get_committed_inpaint_artifact().image[35, 35, 0]) == DISPATCH_FILL

    command.undo()
    restored = model.get_committed_inpaint_artifact()
    assert restored is not None
    assert int(restored.image[35, 35, 0]) == COMMITTED_FILL, "撤销没有还原修复图"
    assert restored.key == model.get_inpaint_key()
    assert len(calls) == 1, "撤销不应再跑修复器"


def test_command_redo_reuses_after_patch_without_dispatch(env):
    """redo 复用首次结果的补丁，不重复跑修复器。"""
    model, calls = env["model"], env["calls"]
    command = _make_command(env, _rect_mask(30, 40, 30, 40))

    command.redo()
    command.undo()
    command.redo()

    assert len(calls) == 1, "redo 又跑了一次修复器"
    assert int(model.get_committed_inpaint_artifact().image[35, 35, 0]) == DISPATCH_FILL


def test_command_undo_restores_mask_when_stroke_grew_it(env):
    """笔画扩大蒙版时，撤销要同时还原蒙版和修复图。"""
    model = env["model"]
    grown = _rect_mask(20, 60, 20, 100)
    command = _make_command(env, _rect_mask(30, 40, 55, 95), new_mask=grown)

    command.redo()
    assert np.array_equal(model.get_refined_mask(), grown)

    command.undo()
    assert np.array_equal(model.get_refined_mask(), env["mask"])
    assert int(model.get_committed_inpaint_artifact().image[35, 70, 0]) == COMMITTED_FILL
