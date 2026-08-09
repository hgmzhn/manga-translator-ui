import _bootstrap  # noqa: F401
from ui.main_page.env_management import (
    _build_api_rotation_reorder_updates,
    _reorder_api_rotation_slot,
    _resolve_api_slot_drop_target,
)

SLOT_KEYS = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_API_BASE")


def test_api_slot_reorder_moves_complete_credentials_as_one_unit() -> None:
    current = {
        "OPENAI_API_KEY": "key-1",
        "OPENAI_MODEL": "model-1",
        "OPENAI_API_BASE": "base-1",
        "OPENAI_API_KEY_2": "key-2",
        "OPENAI_MODEL_2": "model-2",
        "OPENAI_API_BASE_2": "base-2",
        "OPENAI_API_KEY_3": "key-3",
        "OPENAI_MODEL_3": "model-3",
        "OPENAI_API_BASE_3": "base-3",
        "OPENAI_API_ROTATION_STRATEGY": "failover",
        "GEMINI_API_KEY": "unrelated",
    }

    updates = _build_api_rotation_reorder_updates(current, SLOT_KEYS, 1, 3, 3)
    reordered = current | updates

    assert [reordered["OPENAI_API_KEY"], reordered["OPENAI_API_KEY_2"], reordered["OPENAI_API_KEY_3"]] == [
        "key-2",
        "key-3",
        "key-1",
    ]
    assert [reordered["OPENAI_MODEL"], reordered["OPENAI_MODEL_2"], reordered["OPENAI_MODEL_3"]] == [
        "model-2",
        "model-3",
        "model-1",
    ]
    assert [reordered["OPENAI_API_BASE"], reordered["OPENAI_API_BASE_2"], reordered["OPENAI_API_BASE_3"]] == [
        "base-2",
        "base-3",
        "base-1",
    ]
    assert reordered["OPENAI_API_ROTATION_STRATEGY"] == "failover"
    assert reordered["GEMINI_API_KEY"] == "unrelated"


def test_api_slot_reorder_preserves_empty_fields() -> None:
    current = {
        "OPENAI_API_KEY": "key-1",
        "OPENAI_MODEL": "model-1",
        "OPENAI_API_BASE": "base-1",
        "OPENAI_API_KEY_2": "key-2",
        "OPENAI_API_BASE_2": "base-2",
        "OPENAI_API_KEY_3": "key-3",
        "OPENAI_MODEL_3": "model-3",
        "OPENAI_API_BASE_3": "base-3",
    }

    updates = _build_api_rotation_reorder_updates(current, SLOT_KEYS, 2, 1, 3)

    assert updates["OPENAI_API_KEY"] == "key-2"
    assert updates["OPENAI_MODEL"] == ""
    assert updates["OPENAI_API_BASE"] == "base-2"
    assert updates["OPENAI_API_KEY_2"] == "key-1"
    assert updates["OPENAI_MODEL_2"] == "model-1"
    assert updates["OPENAI_API_BASE_2"] == "base-1"


def test_api_slot_drop_target_respects_before_and_after_positions() -> None:
    assert _resolve_api_slot_drop_target(1, 3, drop_after=False, slot_count=3) == 2
    assert _resolve_api_slot_drop_target(1, 3, drop_after=True, slot_count=3) == 3
    assert _resolve_api_slot_drop_target(3, 1, drop_after=False, slot_count=3) == 1
    assert _resolve_api_slot_drop_target(3, 1, drop_after=True, slot_count=3) == 2
    assert _resolve_api_slot_drop_target(2, 2, drop_after=True, slot_count=3) == 2


def test_api_slot_reorder_persists_one_batch_and_refreshes_page() -> None:
    class ConfigService:
        def __init__(self):
            self.values = {
                "OPENAI_API_KEY": "key-1",
                "OPENAI_MODEL": "model-1",
                "OPENAI_API_BASE": "base-1",
                "OPENAI_API_KEY_2": "key-2",
                "OPENAI_MODEL_2": "model-2",
                "OPENAI_API_BASE_2": "base-2",
            }
            self.saved_batches = []
            self.flush_count = 0

        def load_env_vars(self):
            return dict(self.values)

        def save_env_vars(self, updates):
            self.saved_batches.append(dict(updates))
            self.values.update(updates)
            return True

        def flush_pending_writes(self):
            self.flush_count += 1
            return True

    class Controller:
        def __init__(self, config_service):
            self.config_service = config_service

    class View:
        def __init__(self):
            self.config_service = ConfigService()
            self.controller = Controller(self.config_service)
            self._env_api_groups_signature = "old"
            self.refresh_calls = []
            self.selector_refresh_count = 0

        def _refresh_env_api_groups(self, *, force=False):
            self.refresh_calls.append(force)

        def _refresh_api_feature_selectors(self):
            self.selector_refresh_count += 1

    view = View()

    _reorder_api_rotation_slot(view, SLOT_KEYS, 1, 2)

    assert view.config_service.flush_count == 1
    assert len(view.config_service.saved_batches) == 1
    assert view.config_service.values["OPENAI_API_KEY"] == "key-2"
    assert view.config_service.values["OPENAI_MODEL"] == "model-2"
    assert view.config_service.values["OPENAI_API_BASE"] == "base-2"
    assert view.config_service.values["OPENAI_API_KEY_2"] == "key-1"
    assert view.refresh_calls == [True]
    assert view.selector_refresh_count == 1
    assert view._env_api_groups_signature is None
