import _bootstrap  # noqa: F401, I001

from types import SimpleNamespace
from typing import ClassVar

from editor.controller_export_service import EditorControllerExportService


class _History:
    def __init__(self):
        self.clean = False

    def mark_clean(self):
        self.clean = True


class _Model:
    def __init__(self, source_path):
        self.source_path = str(source_path)

    def get_source_image_path(self):
        return self.source_path

    def get_refined_mask(self):
        return None

    def get_raw_mask(self):
        return None

    def get_paint_overlay_image(self):
        return None

    def get_stamp_overlay_image(self):
        return None

    def get_inpainted_image(self):
        return None


class _Controller:
    def __init__(self, source_path):
        self.model = _Model(source_path)
        self.history_service = _History()
        self.logger = SimpleNamespace(
            error=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
        )
        self.config_service = SimpleNamespace(
            get_config=lambda: SimpleNamespace(
                model_dump=lambda: {"app": {}, "cli": {}}
            )
        )
        self.commits = 0

    def commit_pending_edits(self):
        self.commits += 1

    def _get_current_image(self):
        return object()

    def _get_regions(self):
        return [{"translation": "saved text"}]

    def get_toast_manager(self):
        return None


class _PersistenceService:
    calls: ClassVar[list] = []

    def _save_regions_data_with_path(
        self, regions, json_path, source_path, mask, config, **kwargs
    ):
        self.calls.append((regions, json_path, source_path, mask, config, kwargs))


def test_save_editor_state_persists_project_without_exporting(monkeypatch, tmp_path):
    from services import export_service

    _PersistenceService.calls.clear()
    monkeypatch.setattr(export_service, "ExportService", _PersistenceService)
    source_path = tmp_path / "page.png"
    source_path.touch()
    controller = _Controller(source_path)
    service = EditorControllerExportService(controller)
    submitted = []
    monkeypatch.setattr(service, "_submit_job", lambda job: submitted.append(job))

    assert service.save_editor_state() is True
    assert controller.commits == 1
    assert controller.history_service.clean is True
    assert submitted == []
    assert len(_PersistenceService.calls) == 1
    regions, _json_path, saved_source_path, mask, _config, _kwargs = (
        _PersistenceService.calls[0]
    )
    assert regions == [{"translation": "saved text"}]
    assert saved_source_path == str(source_path)
    assert mask is None

    service.shutdown()
