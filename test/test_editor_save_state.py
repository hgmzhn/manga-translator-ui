import _bootstrap  # noqa: F401, I001

import concurrent.futures
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest

from editor.controller_export_service import EditorControllerExportService
from editor.document_state import ExportBase
from editor.inpaint_state import InpaintArtifact, InpaintKey


class _History:
    def __init__(self):
        self.clean = False

    def mark_clean(self):
        self.clean = True


class _Model:
    def __init__(
        self, source_path, source_image, *, mask=None, artifact=None, display_image=None
    ):
        self.source_path = str(source_path)
        self.source_image = source_image
        self.mask = mask
        self.artifact = artifact
        self.display_image = display_image
        self.ready_calls = 0

    def get_source_image_path(self):
        return self.source_path

    def get_image(self):
        return self.source_image

    def get_regions(self):
        return [{"translation": "saved text"}]

    def get_refined_mask(self):
        return self.mask

    def get_raw_mask(self):
        return None

    def get_ready_inpaint_artifact(self):
        self.ready_calls += 1
        return self.artifact

    def get_paint_overlay_image(self):
        return None

    def get_stamp_overlay_image(self):
        return None

    def get_paste_overlays(self):
        return []

    def get_display_layers(self):
        image = self.display_image
        if image is None:
            image = self.artifact.image if self.artifact is not None else self.source_image
        return SimpleNamespace(inpaint_display_image=image)

    def get_export_base(self):
        if self.mask is None or not np.any(self.mask):
            return ExportBase(
                "source", self.source_image, self.source_image, None, None
            )
        if self.artifact is not None:
            return ExportBase(
                "paired",
                self.source_image,
                self.artifact.image,
                self.artifact.mask,
                self.artifact.key,
            )
        return ExportBase(
            "backend_inpaint",
            self.source_image,
            self.source_image,
            self.mask,
            InpaintKey(7, 13),
        )


class _Controller:
    def __init__(self, source_path, *, mask=None, artifact=None, display_image=None):
        self.base_image = np.full((8, 8, 3), 23, dtype=np.uint8)
        self.model = _Model(
            source_path,
            self.base_image,
            mask=mask,
            artifact=artifact,
            display_image=display_image,
        )
        self.history_service = _History()
        self.logger = SimpleNamespace(
            error=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
        )
        self.config_service = SimpleNamespace(
            get_config=lambda: SimpleNamespace(
                model_dump=lambda: {"app": {}, "cli": {}}
            )
        )
        self.commits = 0

    def commit_pending_edits(self):
        self.commits += 1

    def get_toast_manager(self):
        return None


class _PersistenceService:
    calls: ClassVar[list] = []
    saved_images: ClassVar[list[np.ndarray]] = []
    sidecar_path = None

    def save_editor_project(self, source_path, regions, mask, config, **kwargs):
        self.calls.append((regions, None, source_path, mask, config, kwargs))

    def save_inpainted_image(self, _source_path, image, _config):
        self.saved_images.append(np.array(image, copy=True))

    def delete_inpainted_image(self, _source_path):
        if self.sidecar_path is None:
            return False
        self.sidecar_path.unlink(missing_ok=True)
        return True

    def build_output_path(self, _config, source_path):
        return str(source_path) + ".export.png"


class _RenderParameterService:
    def export_parameters_for_backend(self, _index, _region):
        return {}


def _ready_artifact(mask_value=255, image_value=173):
    return InpaintArtifact(
        key=InpaintKey(document_id=7, mask_revision=13),
        mask=np.full((8, 8), mask_value, dtype=np.uint8),
        image=np.full((8, 8, 3), image_value, dtype=np.uint8),
    )


def _completed_future():
    future = concurrent.futures.Future()
    future.set_result(None)
    return future


def test_inpaint_artifact_owns_immutable_arrays():
    source_mask = np.full((3, 4), 29, dtype=np.uint8)
    source_image = np.full((3, 4, 3), 47, dtype=np.uint8)
    artifact = InpaintArtifact(
        key=InpaintKey(1, 3),
        mask=source_mask,
        image=source_image,
    )

    source_mask[:] = 0
    source_image[:] = 0

    assert np.all(artifact.mask == 29)
    assert np.all(artifact.image == 47)
    assert not artifact.mask.flags.writeable
    assert not artifact.image.flags.writeable
    with pytest.raises(ValueError):
        artifact.mask[:] = 5
    with pytest.raises(ValueError):
        artifact.image[:] = 6


def test_save_editor_state_with_empty_mask_deletes_sidecar_without_exporting(
    monkeypatch, tmp_path
):
    from services import export_service

    _PersistenceService.calls.clear()
    _PersistenceService.saved_images.clear()
    monkeypatch.setattr(export_service, "ExportService", _PersistenceService)
    source_path = tmp_path / "page.png"
    source_path.touch()
    stale_sidecar = tmp_path / "stale_inpainted.png"
    stale_sidecar.write_bytes(b"stale")
    _PersistenceService.sidecar_path = stale_sidecar
    empty_mask = np.zeros((8, 8), dtype=np.uint8)
    controller = _Controller(source_path, mask=empty_mask)
    service = EditorControllerExportService(controller)
    submitted = []
    monkeypatch.setattr(service, "_submit_job", lambda job: submitted.append(job))

    assert service.save_editor_state() is True
    assert controller.commits == 1
    assert controller.history_service.clean is True
    assert controller.model.ready_calls == 0
    assert submitted == []
    assert len(_PersistenceService.calls) == 1
    regions, _json_path, saved_source_path, mask, _config, _kwargs = (
        _PersistenceService.calls[0]
    )
    assert regions == [{"translation": "saved text"}]
    assert saved_source_path == str(source_path)
    assert np.array_equal(mask, empty_mask)
    assert not stale_sidecar.exists()

    service.shutdown()


def test_save_uses_current_in_memory_inpaint_image(monkeypatch, tmp_path):
    from services import export_service

    _PersistenceService.calls.clear()
    _PersistenceService.saved_images.clear()
    monkeypatch.setattr(export_service, "ExportService", _PersistenceService)
    source_path = tmp_path / "page.png"
    source_path.touch()
    artifact = _ready_artifact()
    controller = _Controller(
        source_path,
        mask=artifact.mask.copy(),
        display_image=artifact.image,
    )
    service = EditorControllerExportService(controller)

    assert service.save_editor_state() is True
    assert controller.model.ready_calls == 0
    assert np.array_equal(_PersistenceService.calls[0][3], artifact.mask)
    assert np.array_equal(_PersistenceService.saved_images[0], artifact.image)
    assert service.unfinished_count() == 0

    service.shutdown()


def test_export_uses_mask_and_image_from_one_ready_artifact(monkeypatch, tmp_path):
    from editor import controller_export_service as export_module

    monkeypatch.setattr(
        export_module,
        "get_render_parameter_service",
        lambda: _RenderParameterService(),
    )
    source_path = tmp_path / "page.png"
    source_path.touch()
    artifact = _ready_artifact(mask_value=255, image_value=149)
    model_mask = artifact.mask.copy()
    controller = _Controller(source_path, mask=model_mask, artifact=artifact)
    service = EditorControllerExportService(controller)
    submitted = []
    monkeypatch.setattr(
        service,
        "_submit_job",
        lambda job: submitted.append(job) or _completed_future(),
    )

    assert service.export_image() is not None
    assert controller.model.ready_calls == 0
    assert len(submitted) == 1
    controller.base_image[:] = 99
    assert np.all(np.asarray(submitted[0].export_base.source_image) == 23)
    assert np.array_equal(submitted[0].export_base.mask, artifact.mask)
    assert np.array_equal(submitted[0].export_base.mask, model_mask)
    assert np.array_equal(submitted[0].export_base.render_image, artifact.image)
    assert submitted[0].export_base.render_image.flags.writeable
    assert not np.shares_memory(
        submitted[0].export_base.render_image,
        artifact.image,
    )
    submitted[0].release_resources()
    service.shutdown()


def test_save_does_not_wait_for_inpaint_and_export_still_queues_backend(
    monkeypatch, tmp_path
):
    from editor import controller_export_service as export_module
    from services import export_service

    monkeypatch.setattr(
        export_module,
        "get_render_parameter_service",
        lambda: _RenderParameterService(),
    )

    _PersistenceService.calls.clear()
    _PersistenceService.saved_images.clear()
    monkeypatch.setattr(export_service, "ExportService", _PersistenceService)
    source_path = tmp_path / "page.png"
    source_path.touch()
    mask = np.full((8, 8), 255, dtype=np.uint8)
    display_image = np.full((8, 8, 3), 77, dtype=np.uint8)
    controller = _Controller(
        source_path,
        mask=mask,
        display_image=display_image,
    )
    service = EditorControllerExportService(controller)
    submitted = []
    monkeypatch.setattr(
        service,
        "_submit_job",
        lambda job: submitted.append(job) or _completed_future(),
    )

    assert service.save_editor_state() is True
    assert controller.model.ready_calls == 0
    assert np.array_equal(_PersistenceService.calls[0][3], mask)
    assert np.array_equal(_PersistenceService.saved_images[0], display_image)
    assert controller.history_service.clean is True

    assert service.export_image() is not None
    assert controller.model.ready_calls == 0
    assert len(submitted) == 1
    assert submitted[0].export_base.kind == "backend_inpaint"
    assert np.array_equal(submitted[0].export_base.mask, mask)

    submitted[0].release_resources()
    service.shutdown()
