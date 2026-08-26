import _bootstrap  # noqa: F401, I001

from types import SimpleNamespace

from core.config_models import AppSection
from ui.editor.view import EditorView


def test_rich_text_popup_pin_config_default_and_serialization():
    section = AppSection()
    assert section.editor_rich_text_popup_pinned is False
    assert section.model_dump()["editor_rich_text_popup_pinned"] is False
    restored = AppSection.model_validate({"editor_rich_text_popup_pinned": True})
    assert restored.editor_rich_text_popup_pinned is True
    assert restored.model_dump()["editor_rich_text_popup_pinned"] is True


def test_rich_text_popup_pin_menu_change_is_saved():
    class FakeConfigService:
        def __init__(self):
            self.config = SimpleNamespace(app=AppSection())
            self.updates = []
            self.save_count = 0

        def get_config(self):
            return self.config

        def update_config(self, payload):
            self.updates.append(payload)

        def save_config_file(self):
            self.save_count += 1
            return True

    service = FakeConfigService()
    applied = []
    view = SimpleNamespace(
        config_service=service,
        _apply_editor_setting=lambda key, value: applied.append((key, value)),
        _read_editor_settings=lambda config=None: {
            "editor_rich_text_popup_pinned": bool(
                config.app.editor_rich_text_popup_pinned
            )
        },
    )

    EditorView._persist_editor_setting(view, "editor_rich_text_popup_pinned", True)

    assert applied == [("editor_rich_text_popup_pinned", True)]
    assert service.updates == [{"app": {"editor_rich_text_popup_pinned": True}}]
    assert service.save_count == 1
