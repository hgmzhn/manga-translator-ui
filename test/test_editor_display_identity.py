import _bootstrap  # noqa: F401, I001

from types import SimpleNamespace

import numpy as np

from editor.document_state import DisplayLayers
from ui.editor.graphics_view_layers import GraphicsViewLayersMixin


def _layers(document_id: int, source_path: str, value: int) -> DisplayLayers:
    source = np.full((4, 6, 3), value, dtype=np.uint8)
    return DisplayLayers(
        document_id=document_id,
        source_path=source_path,
        source_image=source,
        inpaint_display_image=source.copy(),
        source_opacity=0.25,
    )


def test_graphics_layers_reject_a_late_payload_from_the_previous_document():
    current_identity = (2, "page-b.png")
    view = SimpleNamespace(
        model=SimpleNamespace(get_document_identity=lambda: current_identity),
        _document_identity=current_identity,
    )

    GraphicsViewLayersMixin.on_display_layers_changed(
        view,
        _layers(1, "page-a.png", 19),
    )

    assert view._document_identity == current_identity


def test_queued_clear_cannot_erase_a_newly_installed_document():
    current_identity = (3, "page-c.png")
    view = SimpleNamespace(
        model=SimpleNamespace(get_document_identity=lambda: current_identity),
        _document_identity=current_identity,
    )

    GraphicsViewLayersMixin.on_display_layers_changed(view, None)

    assert view._document_identity == current_identity
