import _bootstrap  # noqa: F401, I001

import asyncio
import concurrent.futures
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
from PIL import Image

from editor.controller_inpaint_service import EditorControllerInpaintService
from editor.core.resource_manager import ResourceManager
from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from editor.inpaint_state import (
    InpaintArtifact,
    InpaintConfigSnapshot,
    InpaintKey,
    InpaintRequest,
    InpaintResult,
    InpaintState,
    MaskDelta,
    MaskRefineResult,
)
from editor.session import EditorSession




class _KeyedModel:
    def __init__(self, key, mask):
        self.key = key
        self.mask = np.array(mask, copy=True)
        self.image = None
        self.refined_writes = []
        self.document_revision = 1
        self.state = InpaintState()

    def get_document_revision(self):
        return self.document_revision

    def get_document_identity(self):
        return self.key.document_id, "page.png"

    def get_document_id(self):
        return self.key.document_id

    def get_base_revision(self):
        return self.key.base_revision

    def get_mask_revision(self):
        return self.key.mask_revision

    def get_inpaint_key(self):
        return self.key


    def get_effective_mask(self):
        return self.mask

    def get_committed_inpaint_artifact(self):
        artifact = self.state.committed
        return None if artifact is None else artifact.snapshot()

    def get_ready_inpaint_artifact(self):
        return self.state.ready_artifact(self.key, self.mask)

    def install_inpaint_artifact(self, artifact):
        if artifact.key != self.key or not np.array_equal(artifact.mask, self.mask):
            return False
        self.state.install_ready(artifact)
        self.image = np.array(artifact.image, copy=True)
        return True

    def fail_inpaint(self, key):
        return self.state.fail(key, self.key)

    def begin_inpaint(self, key, future):
        if key != self.key:
            future.cancel()
            return False
        return self.state.begin(key, future)

    def get_raw_mask(self):
        return self.mask

    def get_refined_mask(self):
        return self.mask

    def set_refined_mask(self, mask):
        self.refined_writes.append(np.array(mask, copy=True))

    def invalidate_inpaint(self, *, clear_committed):
        self.state.invalidate(clear_committed=clear_committed)
        return True


class _InpaintController:
    def __init__(self, model):
        self.model = model
        self.resource_manager = ResourceManager()
        self.logger = SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        )


def _make_keyed_service():
    key = InpaintKey(
        document_id=2,
        base_revision=3,
        mask_revision=5,
        generation=7,
    )
    mask = np.full((4, 5), 255, dtype=np.uint8)
    model = _KeyedModel(key, mask)
    assert model.begin_inpaint(key, concurrent.futures.Future())
    return EditorControllerInpaintService(_InpaintController(model)), model, key, mask


def test_inpaint_key_rejects_each_stale_identity_component():
    image = np.full((4, 5, 3), 181, dtype=np.uint8)

    for field in ("document_id", "base_revision", "mask_revision", "generation"):
        service, model, request_key, mask = _make_keyed_service()
        model.key = replace(
            request_key,
            **{field: getattr(request_key, field) + 1},
        )

        service.apply_inpaint_result(InpaintResult(request_key, mask, image))

        assert model.image is None, field
        assert model.get_committed_inpaint_artifact() is None, field
        assert model.get_ready_inpaint_artifact() is None, field


def test_refined_mask_result_rejects_each_stale_revision_component():
    for field in ("document_id", "base_revision", "mask_revision"):
        service, model, request_key, mask = _make_keyed_service()
        result = MaskRefineResult(
            request_key.document_id,
            request_key.base_revision,
            request_key.mask_revision,
            mask,
        )
        model.key = replace(
            request_key,
            **{field: getattr(request_key, field) + 1},
        )

        service.apply_refined_mask_result(result)

        assert model.refined_writes == [], field

    service, model, request_key, mask = _make_keyed_service()
    refined_mask = mask.copy()
    service.apply_refined_mask_result(
        MaskRefineResult(
            request_key.document_id,
            request_key.base_revision,
            request_key.mask_revision,
            refined_mask,
        )
    )
    assert len(model.refined_writes) == 1
    assert np.array_equal(model.refined_writes[0], refined_mask)


def test_document_revision_change_does_not_reject_same_inpaint_key():
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
    previous_document_revision = model.get_document_revision()

    model.replace_regions([{"translation": "after", "font_size": 24}])

    assert model.get_document_revision() > previous_document_revision
    assert model.get_inpaint_key() == key

    image = np.full((4, 5, 3), 137, dtype=np.uint8)
    service.apply_inpaint_result(InpaintResult(key, mask, image))
    artifact = model.get_ready_inpaint_artifact()

    assert artifact is not None
    assert artifact.key == key
    assert np.array_equal(artifact.mask, mask)
    assert np.array_equal(artifact.image, image)


def test_session_keeps_content_and_mask_revision_domains_separate():
    session = EditorSession()
    initial_image = Image.new("RGB", (5, 4))
    initial_mask = np.zeros((4, 5), dtype=np.uint8)
    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=initial_image,
            regions=[{"translation": "before"}],
            raw_mask=initial_mask,
        )
    )
    initial_identity = (
        session.get_document_id(),
        session.get_base_revision(),
        session.get_mask_revision(),
    )

    session.set_regions([{"translation": "after", "font_size": 24}])
    assert (
        session.get_document_id(),
        session.get_base_revision(),
        session.get_mask_revision(),
    ) == initial_identity


    changed_mask = initial_mask.copy()
    changed_mask[0, 0] = 255
    session.replace_masks(refined=changed_mask)
    after_mask_change = (
        session.get_document_id(),
        session.get_base_revision(),
        session.get_mask_revision(),
    )
    assert after_mask_change == (
        initial_identity[0],
        initial_identity[1],
        initial_identity[2] + 1,
    )

    previous_document_id = session.get_document_id()
    session.clear_document()
    assert session.get_document_identity() is None
    assert session.get_document_id() > previous_document_id


def test_ready_artifact_is_rejected_after_document_base_or_mask_change():
    image = np.full((4, 5, 3), 113, dtype=np.uint8)

    for field in ("document_id", "base_revision", "mask_revision"):
        service, model, key, mask = _make_keyed_service()
        service.apply_inpaint_result(InpaintResult(key, mask, image))
        assert model.get_ready_inpaint_artifact() is not None

        model.key = replace(key, **{field: getattr(key, field) + 1})

        assert model.get_ready_inpaint_artifact() is None, field


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
    previous_key = InpaintKey(1, 1, 1, 1)
    request_key = InpaintKey(1, 1, 2, 2)
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
    assert not np.shares_memory(request.delta.removed, delta.removed)
    assert not np.shares_memory(
        request.previous_artifact.image,
        previous_artifact.image,
    )

    result = asyncio.run(EditorControllerInpaintService.async_inpaint(request))

    assert result is not None
    assert result.key == request_key
    assert np.array_equal(result.mask, request.mask)
    unmasked = request.mask == 0
    assert np.array_equal(result.image[unmasked], request.image[unmasked])
    assert np.all(result.image[0, 1] == 211)


def _make_editor_model():
    return EditorModel()


