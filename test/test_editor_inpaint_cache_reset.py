import _bootstrap  # noqa: F401, I001

import asyncio
import concurrent.futures
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
from PIL import Image

from editor.controller_inpaint_service import EditorControllerInpaintService
from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from editor.inpaint_state import (
    InpaintArtifact,
    InpaintConfigSnapshot,
    InpaintKey,
    InpaintRequest,
    InpaintState,
    MaskDelta,
)
from editor.session import EditorSession
from ui.editor.graphics_view_input import GraphicsViewInputMixin


class _KeyedModel:
    def __init__(self, key, mask):
        self.key = key
        self.mask = np.array(mask, copy=True)
        self.image = None
        self.state = InpaintState()
        self.source_rgb = np.zeros((*self.mask.shape, 3), dtype=np.uint8)

    def get_document_identity(self):
        return self.key.document_id, "page.png"

    def get_document_id(self):
        return self.key.document_id

    def get_mask_revision(self):
        return self.key.mask_revision

    def get_inpaint_key(self):
        return self.key

    def get_source_rgb(self):
        return self.source_rgb

    def get_effective_mask(self):
        return self.mask

    def get_committed_inpaint_artifact(self):
        return self.state.committed

    def get_ready_inpaint_artifact(self):
        return self.state.ready_artifact(self.key, self.mask)

    def install_inpaint_artifact(self, artifact):
        if artifact.key != self.key or not np.array_equal(artifact.mask, self.mask):
            return False
        self.state.install_ready(artifact)
        self.image = artifact.image
        return True

    def fail_inpaint(self, key):
        return self.state.fail(key, self.key)

    def begin_inpaint(self, key, future):
        if key != self.key:
            future.cancel()
            return False
        return self.state.begin(key, future)


class _InpaintController:
    def __init__(self, model):
        self.model = model
        self.logger = SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        )


def _make_keyed_service():
    key = InpaintKey(document_id=2, mask_revision=5)
    mask = np.full((4, 5), 255, dtype=np.uint8)
    model = _KeyedModel(key, mask)
    assert model.begin_inpaint(key, concurrent.futures.Future())
    return EditorControllerInpaintService(_InpaintController(model)), model, key, mask


def test_inpaint_key_rejects_stale_document_and_mask_results():
    image = np.full((4, 5, 3), 181, dtype=np.uint8)

    for field in ("document_id", "mask_revision"):
        service, model, request_key, mask = _make_keyed_service()
        model.key = replace(
            request_key,
            **{field: getattr(request_key, field) + 1},
        )

        service.apply_inpaint_result(InpaintArtifact(request_key, mask, image))

        assert model.image is None, field
        assert model.get_committed_inpaint_artifact() is None, field
        assert model.get_ready_inpaint_artifact() is None, field


def test_region_change_does_not_reject_same_inpaint_key():
    model = _make_editor_model()
    mask = np.full((4, 5), 255, dtype=np.uint8)
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=Image.new("RGB", (5, 4)),
            regions=[{"translation": "before", "font_size": 18}],
            raw_mask=mask,
        )
    )
    service = EditorControllerInpaintService(_InpaintController(model))
    key = model.get_inpaint_key()
    assert model.begin_inpaint(key, concurrent.futures.Future())

    model.replace_regions([{"translation": "after", "font_size": 24}])
    assert model.get_inpaint_key() == key

    image = np.full((4, 5, 3), 137, dtype=np.uint8)
    service.apply_inpaint_result(InpaintArtifact(key, mask, image))
    artifact = model.get_ready_inpaint_artifact()

    assert artifact is not None
    assert artifact.key == key
    assert np.array_equal(artifact.mask, mask)
    assert np.array_equal(artifact.image, image)


def test_session_identity_changes_only_for_document_or_effective_mask():
    session = EditorSession()
    initial_mask = np.zeros((4, 5), dtype=np.uint8)
    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=Image.new("RGB", (5, 4)),
            regions=[{"translation": "before"}],
            raw_mask=initial_mask,
        )
    )
    initial_identity = session.get_document_identity()
    initial_mask_revision = session.get_mask_revision()

    session.set_regions([{"translation": "after", "font_size": 24}])
    assert session.get_document_identity() == initial_identity
    assert session.get_mask_revision() == initial_mask_revision

    changed_mask = initial_mask.copy()
    changed_mask[0, 0] = 255
    session.replace_masks(refined=changed_mask)
    assert session.get_document_identity() == initial_identity
    assert session.get_mask_revision() == initial_mask_revision + 1

    session.clear_document()
    assert session.get_document_identity() is None


def test_ready_artifact_is_rejected_after_document_or_mask_change():
    image = np.full((4, 5, 3), 113, dtype=np.uint8)

    for field in ("document_id", "mask_revision"):
        service, model, key, mask = _make_keyed_service()
        service.apply_inpaint_result(InpaintArtifact(key, mask, image))
        assert model.get_ready_inpaint_artifact() is not None

        model.key = replace(key, **{field: getattr(key, field) + 1})

        assert model.get_ready_inpaint_artifact() is None, field


def test_undo_to_committed_mask_rekeys_and_reuses_its_artifact():
    mask = np.full((4, 5), 255, dtype=np.uint8)
    current_key = InpaintKey(2, 6)
    committed = InpaintArtifact(
        InpaintKey(2, 5),
        mask,
        np.full((4, 5, 3), 113, dtype=np.uint8),
    )
    model = _KeyedModel(current_key, mask)
    model.state.install_ready(committed)
    service = EditorControllerInpaintService(_InpaintController(model))
    delta = MaskDelta(np.zeros_like(mask), mask, current_key.mask_revision)

    service.on_effective_mask_delta_changed(mask, delta)

    ready = model.get_ready_inpaint_artifact()
    assert ready is not None
    assert ready.key == current_key
    assert ready.image is committed.image


def test_repair_request_reprocesses_pixels_already_in_committed_mask():
    mask = np.full((4, 5), 255, dtype=np.uint8)
    current_key = InpaintKey(2, 6)
    previous = InpaintArtifact(
        InpaintKey(2, 5),
        mask,
        np.full((4, 5, 3), 113, dtype=np.uint8),
    )
    model = _KeyedModel(current_key, mask)
    model.state.install_ready(previous)
    service = EditorControllerInpaintService(_InpaintController(model))
    service._snapshot_inpaint_config = lambda: InpaintConfigSnapshot(
        "none", "fp32", False, 64, "cpu"
    )
    stroke = np.zeros_like(mask)
    stroke[1, 2:4] = 255
    delta = MaskDelta(
        added=stroke,
        removed=np.zeros_like(mask),
        mask_revision=current_key.mask_revision,
    )

    request = service.build_inpaint_request(mask, delta)

    assert request is not None
    assert request.key == current_key
    assert request.previous_artifact is previous
    assert np.array_equal(request.delta.added, stroke)
    assert not np.any(request.delta.removed)


def test_mask_brush_submits_repair_when_binary_mask_is_unchanged():
    current_mask = np.full((2, 3), 255, dtype=np.uint8)
    stroke = np.zeros_like(current_mask)
    stroke[0, 1] = 255
    calls = []
    model = SimpleNamespace(
        get_refined_mask=lambda: current_mask,
        set_refined_mask=lambda mask, **kwargs: calls.append((mask, kwargs)),
    )
    view = SimpleNamespace(
        _current_draw_mask_points=[(1, 0)],
        _current_draw_mask_shape=current_mask.shape,
        _active_tool="brush",
        model=model,
        controller=SimpleNamespace(
            execute_command=lambda command: calls.append(command)
        ),
        _build_stroke_mask=lambda points, shape: stroke,
    )

    GraphicsViewInputMixin._commit_drawing(view)

    assert len(calls) == 1
    submitted_mask, kwargs = calls[0]
    assert np.array_equal(submitted_mask, current_mask)
    assert np.array_equal(kwargs["repair"], stroke)


def test_mask_delta_repeated_coverage_partial_addition_and_erasure():
    session = EditorSession()
    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=np.zeros((2, 3, 3), dtype=np.uint8),
        )
    )
    initial = np.array(
        [
            [255, 0, 0],
            [0, 0, 0],
        ],
        dtype=np.uint8,
    )
    initial_delta = session.replace_masks(refined=initial).delta
    assert initial_delta is not None
    initial_revision = session.get_mask_revision()

    repeated_delta = session.replace_masks(refined=initial.copy()).delta
    assert repeated_delta is None
    assert session.get_mask_revision() == initial_revision

    extended = initial.copy()
    extended[0, 1] = 255
    added_delta = session.replace_masks(refined=extended).delta
    assert added_delta is not None
    assert added_delta.mask_revision == initial_revision + 1
    assert np.array_equal(
        added_delta.added,
        np.array([[0, 255, 0], [0, 0, 0]], dtype=np.uint8),
    )
    assert not np.any(added_delta.removed)

    erased = extended.copy()
    erased[0, 0] = 0
    erased_delta = session.replace_masks(refined=erased).delta
    assert erased_delta is not None
    assert not np.any(erased_delta.added)
    assert np.array_equal(
        erased_delta.removed,
        np.array([[255, 0, 0], [0, 0, 0]], dtype=np.uint8),
    )


def test_erasure_restores_base_pixels_from_immutable_request_snapshot():
    previous_key = InpaintKey(1, 1)
    request_key = InpaintKey(1, 2)
    base = np.full((2, 3, 3), 19, dtype=np.uint8)
    previous_mask = np.array(
        [
            [255, 255, 0],
            [0, 0, 0],
        ],
        dtype=np.uint8,
    )
    current_mask = np.array(
        [
            [0, 255, 0],
            [0, 0, 0],
        ],
        dtype=np.uint8,
    )
    previous_image = np.full((2, 3, 3), 211, dtype=np.uint8)
    removed = np.array(
        [
            [255, 0, 0],
            [0, 0, 0],
        ],
        dtype=np.uint8,
    )
    delta = MaskDelta(
        added=np.zeros_like(removed),
        removed=removed,
        mask_revision=request_key.mask_revision,
    )
    removed[:] = 0
    previous_artifact = InpaintArtifact(
        previous_key,
        previous_mask,
        previous_image,
    )
    request = InpaintRequest(
        key=request_key,
        image=base,
        mask=current_mask,
        delta=delta,
        previous_artifact=previous_artifact,
        config=InpaintConfigSnapshot("none", "fp32", False, 64, "cpu"),
    )

    base[:] = 0
    current_mask[:] = 0
    previous_image[:] = 0
    assert request.delta is delta
    assert request.previous_artifact is previous_artifact
    assert not np.shares_memory(request.image, base)
    assert not np.shares_memory(request.mask, current_mask)

    result = asyncio.run(EditorControllerInpaintService.async_inpaint(request))

    assert result is not None
    assert result.key == request_key
    assert np.array_equal(result.mask, request.mask)
    unmasked = request.mask == 0
    assert np.array_equal(result.image[unmasked], request.image[unmasked])
    assert np.all(result.image[0, 1] == 211)


def _make_editor_model():
    return EditorModel()
