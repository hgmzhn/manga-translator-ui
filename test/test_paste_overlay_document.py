import _bootstrap  # noqa: F401, I001

import numpy as np

from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from editor.session import EditorSession


def _source(value: int) -> np.ndarray:
    return np.full((5, 7, 3), value, dtype=np.uint8)


def _overlay(**overrides):
    data = {
        "id": "ovl-1",
        "name": "特效字",
        "z": 1,
        "visible": True,
        "opacity": 0.8,
        "center_x": 10.0,
        "center_y": 20.0,
        "width": 64.0,
        "height": 32.0,
        "rotation": 0.0,
        "flip_h": False,
        "flip_v": False,
        "image": "",
    }
    data.update(overrides)
    return data


def _session_with_page(paste_overlays=None) -> EditorSession:
    session = EditorSession()
    session.load_document(
        DocumentSnapshot(
            source_path="page.png",
            image=_source(31),
            paste_overlays=paste_overlays or [],
        )
    )
    return session


def test_snapshot_paste_overlays_survive_document_load():
    overlays = [_overlay(), _overlay(id="ovl-2", name="背景补块", z=0)]
    session = _session_with_page(overlays)
    assert session.get_paste_overlays() == overlays


def test_get_returns_detached_copy():
    session = _session_with_page([_overlay()])
    fetched = session.get_paste_overlays()
    fetched[0]["center_x"] = 9999.0
    assert session.get_paste_overlays()[0]["center_x"] == 10.0


def test_set_paste_overlays_normalizes_and_dedupes_ids():
    session = _session_with_page()
    changed = session.set_paste_overlays(
        [
            {"width": "80", "height": "40", "z": "2"},
            {"width": "80", "height": "40", "z": "2"},
        ]
    )
    assert changed is True
    overlays = session.get_paste_overlays()
    assert len(overlays) == 2
    assert overlays[0]["width"] == 80.0
    assert overlays[0]["height"] == 40.0
    assert overlays[0]["z"] == 2
    ids = [item["id"] for item in overlays]
    assert len(ids) == len(set(ids))


def test_set_noop_returns_false_when_unchanged():
    session = _session_with_page([_overlay()])
    assert session.set_paste_overlays([_overlay()]) is False


def test_clear_document_resets_paste_overlays():
    session = _session_with_page([_overlay()])
    session.clear_document()
    assert session.get_paste_overlays() == []


def test_model_emits_signal_on_replace():
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(source_path="page.png", image=_source(5))
    )
    emitted = []
    model.paste_overlays_changed.connect(emitted.append)
    model.set_paste_overlays([_overlay()])
    assert len(emitted) == 1
    assert emitted[0][0]["id"] == "ovl-1"


def test_model_noop_does_not_emit():
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path="page.png",
            image=_source(5),
            paste_overlays=[_overlay()],
        )
    )
    emitted = []
    model.paste_overlays_changed.connect(emitted.append)
    model.set_paste_overlays([_overlay()])
    assert emitted == []
