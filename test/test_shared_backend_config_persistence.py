import asyncio
import json

from manga_translator.mode import share as share_module
from manga_translator.mode.share import MangaShare


def _service(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"keep": "desktop"}\n', encoding="utf-8")
    monkeypatch.setattr(share_module, "DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.setattr(share_module, "_read_desktop_config", lambda: {"keep": "desktop"})
    monkeypatch.setattr(
        share_module,
        "_load_desktop_config",
        lambda raw: ({"validated": True}, {"translator": raw}, {"save": True}),
    )
    monkeypatch.setattr(share_module, "MangaTranslator", lambda params: {"params": params})

    service = MangaShare.__new__(MangaShare)
    service.nonce = ""
    service.config_revision = share_module._config_revision({"before": True})
    service.progress_queue = asyncio.Queue()
    service._register_progress_hook = lambda: None
    return service, config_path


def test_session_config_does_not_change_desktop_config(tmp_path, monkeypatch):
    service, config_path = _service(tmp_path, monkeypatch)
    original = config_path.read_bytes()
    plugin_config = {"translator": {"target_lang": "JPN"}}

    changed = asyncio.run(service.apply_runtime_config(plugin_config, persist=False))

    assert changed is True
    assert config_path.read_bytes() == original
    assert service.config_document == plugin_config
    assert service.config_revision == share_module._config_revision(plugin_config)


def test_legacy_persistent_config_apply_still_writes_by_default(tmp_path, monkeypatch):
    service, config_path = _service(tmp_path, monkeypatch)
    plugin_config = {"translator": {"target_lang": "JPN"}}

    changed = asyncio.run(service.apply_runtime_config(plugin_config))

    assert changed is True
    assert json.loads(config_path.read_text(encoding="utf-8")) == plugin_config
