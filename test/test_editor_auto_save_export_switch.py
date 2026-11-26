import _bootstrap  # noqa: F401, I001

from types import SimpleNamespace

import pytest

from editor.controller_document_service import EditorControllerDocumentService


class _ExportState:
    def has_unsaved_changes(self):
        return True


class _Controller:
    def __init__(self, *, auto_save: bool, auto_export: bool, suppress_warning: bool):
        self.export_service = _ExportState()
        self.config_service = SimpleNamespace(
            get_config=lambda: SimpleNamespace(
                app=SimpleNamespace(
                    editor_auto_save_on_switch=auto_save,
                    editor_auto_export_on_switch=auto_export,
                    editor_suppress_unsaved_warning=suppress_warning,
                )
            )
        )
        self.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
        self.saved = 0
        self.exported = []
        self.committed = 0

    def commit_pending_edits(self):
        self.committed += 1

    def save_editor_state(self):
        self.saved += 1
        return True

    def export_image(self, automatic=False):
        self.exported.append(bool(automatic))
        return object()


@pytest.mark.parametrize(
    (
        "auto_save",
        "auto_export",
        "suppress_warning",
        "expected_saved",
        "expected_exported",
        "expected_prompts",
    ),
    [
        (True, False, False, 1, [], 0),
        (False, True, False, 0, [True], 0),
        (True, True, False, 1, [True], 0),
        (False, False, False, 0, [], 1),
        (False, False, True, 0, [], 0),
    ],
)
def test_image_switch_uses_distinct_auto_save_and_auto_export_modes(
    monkeypatch,
    auto_save,
    auto_export,
    suppress_warning,
    expected_saved,
    expected_exported,
    expected_prompts,
):
    controller = _Controller(
        auto_save=auto_save,
        auto_export=auto_export,
        suppress_warning=suppress_warning,
    )
    service = EditorControllerDocumentService(controller)
    loaded = []
    monkeypatch.setattr(service, "do_load_image", loaded.append)
    prompts = []
    monkeypatch.setattr(
        service,
        "_ask_unsaved_action",
        lambda: prompts.append(True) or "discard",
    )

    service.load_image_and_regions("next.png")

    assert controller.committed == 1
    assert controller.saved == expected_saved
    assert controller.exported == expected_exported
    assert loaded == ["next.png"]
    assert len(prompts) == expected_prompts
    service.shutdown()


def test_switch_runs_auto_save_before_auto_export_and_aborts_on_save_failure(
    monkeypatch,
):
    events = []

    class _OrderedController(_Controller):
        def __init__(self, *, save_ok: bool):
            super().__init__(auto_save=True, auto_export=True, suppress_warning=False)
            self._save_ok = save_ok

        def commit_pending_edits(self):
            events.append("commit")
            super().commit_pending_edits()

        def save_editor_state(self):
            events.append("save")
            super().save_editor_state()
            return self._save_ok

        def export_image(self, automatic=False):
            events.append(("export", bool(automatic)))
            return super().export_image(automatic=automatic)

    ok_controller = _OrderedController(save_ok=True)
    ok_service = EditorControllerDocumentService(ok_controller)
    monkeypatch.setattr(ok_service, "do_load_image", lambda p: events.append(("load", p)))
    ok_service.load_image_and_regions("next.png")
    ok_service.shutdown()
    assert events == ["commit", "save", ("export", True), ("load", "next.png")]

    events.clear()
    fail_controller = _OrderedController(save_ok=False)
    fail_service = EditorControllerDocumentService(fail_controller)
    monkeypatch.setattr(
        fail_service, "do_load_image", lambda p: events.append(("load", p))
    )
    fail_service.load_image_and_regions("next.png")
    fail_service.shutdown()
    assert events == ["commit", "save"]
