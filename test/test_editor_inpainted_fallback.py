import _bootstrap  # noqa: F401, I001
from types import SimpleNamespace


import numpy as np

from editor.document_state import DocumentSnapshot, LoadedInpaintSidecar
from editor.document_load_worker import DocumentLoadWorker
from editor.session import EditorSession


def _mask() -> np.ndarray:
    mask = np.zeros((6, 8), dtype=np.uint8)
    mask[1:5, 2:7] = 255
    return mask


def test_loaded_sidecar_installs_a_real_paired_artifact():
    source = np.full((6, 8, 3), 29, dtype=np.uint8)
    inpainted = np.full((6, 8, 3), 173, dtype=np.uint8)
    session = EditorSession()

    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=source,
            raw_mask=_mask(),
            inpaint_sidecar=LoadedInpaintSidecar(inpainted),
        )
    )

    artifact = session.get_committed_inpaint_artifact()
    assert artifact is not None
    assert np.array_equal(artifact.mask, _mask())
    assert np.array_equal(artifact.image, inpainted)
    assert artifact.key == session.get_inpaint_key()
    assert np.array_equal(
        session.get_display_layers().inpaint_display_image,
        inpainted,
    )

    session.load_document(
        DocumentSnapshot(source_path="page-next.png", image=source, raw_mask=_mask())
    )
    assert not session.install_inpaint_artifact(artifact)
    assert session.get_committed_inpaint_artifact() is None


def test_absent_sidecar_keeps_domain_artifact_empty():
    source = np.full((6, 8, 3), 47, dtype=np.uint8)
    session = EditorSession()

    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=source,
            raw_mask=_mask(),
        )
    )

    assert session.get_committed_inpaint_artifact() is None
    assert np.array_equal(
        session.get_display_layers().inpaint_display_image,
        source,
    )


def _worker_with_sidecar(image, warnings):
    logger = SimpleNamespace(
        error=lambda *_args, **_kwargs: None,
        warning=lambda *args, **_kwargs: warnings.append(args),
    )
    controller = SimpleNamespace(
        _load_detached_image_array=lambda *_args, **_kwargs: image,
    )
    service = SimpleNamespace(controller=controller, logger=logger)
    return DocumentLoadWorker(service, "page.png", None)


def test_same_page_legacy_sidecar_loads_without_manifest():
    warnings = []
    inpainted = np.full((6, 8, 3), 191, dtype=np.uint8)
    loaded = _worker_with_sidecar(inpainted, warnings)._load_inpaint_sidecar(
        "page_inpainted.jpg",
        _mask(),
        (8, 6),
    )

    assert loaded is not None
    assert np.array_equal(loaded.image, inpainted)
    assert warnings == []


def test_sidecar_with_incompatible_dimensions_is_ignored():
    warnings = []
    loaded = _worker_with_sidecar(
        np.zeros((5, 8, 3), dtype=np.uint8),
        warnings,
    )._load_inpaint_sidecar(
        "page_inpainted.jpg",
        _mask(),
        (8, 6),
    )

    assert loaded is None
    assert len(warnings) == 1
