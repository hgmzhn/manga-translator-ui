import _bootstrap  # noqa: F401
import json
from string import Formatter
from types import SimpleNamespace

import services
from services import i18n_service
from services.i18n_service import I18nManager
from ui.main_page import env_management
from ui.main_page.view import MainView
from utils.font_list import _font_search_placeholder

LOCALE_DIR = _bootstrap.ROOT / "desktop_qt_ui" / "locales"


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(text) if field}


def test_dynamic_locale_discovery_and_resolution(tmp_path):
    (tmp_path / "zh_CN.json").write_text(
        json.dumps({"Hello": "你好"}, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "en_US.json").write_text(
        json.dumps({"Hello": "Hello"}, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "fr_FR.json").write_text(
        json.dumps(
            {"Hello": "Bonjour", "Search fonts…": "Rechercher des polices…"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "ca_ES.json").write_text(
        json.dumps({"Hello": "Hola"}, ensure_ascii=False), encoding="utf-8"
    )

    manager = I18nManager(
        locale_dir=str(tmp_path),
        fallback_locale="zh_CN",
        config_language="fr_CA",
    )

    locales = manager.get_available_locales()
    assert set(locales.keys()) == {"ca_ES", "en_US", "fr_FR", "zh_CN"}
    assert locales["fr_FR"].name == "Français"
    assert manager.get_current_locale() == "fr_FR"
    assert manager.translate("Hello") == "Bonjour"

    # Language-only and hyphenated codes resolve cleanly
    assert manager.set_locale("ca-ES") is True
    assert manager.get_current_locale() == "ca_ES"
    assert manager.set_locale("fr") is True
    assert manager.get_current_locale() == "fr_FR"
    assert manager.set_locale("xx_XX") is False


def test_auto_detect_and_rtl_metadata(tmp_path, monkeypatch):
    (tmp_path / "zh_CN.json").write_text("{}", encoding="utf-8")
    (tmp_path / "fr_FR.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ar_SA.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        I18nManager, "_detect_system_locale", lambda self: "fr_BE.UTF-8"
    )
    manager = I18nManager(
        locale_dir=str(tmp_path),
        fallback_locale="zh_CN",
        config_language="auto",
    )
    assert manager.get_current_locale() == "fr_FR"
    assert manager.is_rtl_language("ar_SA") is True
    assert manager.is_rtl_language("fr_FR") is False
    assert manager.set_locale("ar_SA") is True
    assert manager.set_locale("auto") is True
    assert manager.get_current_locale() == "fr_FR"

    # Unknown system locale falls back to zh_CN
    monkeypatch.setattr(I18nManager, "_detect_system_locale", lambda self: "pt_BR")
    fallback_mgr = I18nManager(
        locale_dir=str(tmp_path),
        fallback_locale="zh_CN",
        config_language="auto",
    )
    assert fallback_mgr.get_current_locale() == "zh_CN"


def test_translate_formats_placeholders_even_when_equal_to_key(tmp_path):
    (tmp_path / "zh_CN.json").write_text(
        json.dumps({"Found {count} prompt files.": "找到 {count} 个提示词文件。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "en_US.json").write_text(
        json.dumps({"Found {count} prompt files.": "Found {count} prompt files."}, ensure_ascii=False),
        encoding="utf-8",
    )

    manager = I18nManager(
        locale_dir=str(tmp_path),
        fallback_locale="zh_CN",
        config_language="en_US",
    )
    assert manager.translate("Found {count} prompt files.", count=4) == "Found 4 prompt files."
    assert manager.translate("Missing {count} item(s)", count=2) == "Missing 2 item(s)"


def test_font_search_placeholder_uses_i18n(tmp_path, monkeypatch):
    (tmp_path / "zh_CN.json").write_text(
        json.dumps({"Search fonts…": "搜索字体…"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "fr_FR.json").write_text(
        json.dumps({"Search fonts…": "Rechercher des polices…"}, ensure_ascii=False),
        encoding="utf-8",
    )

    manager = I18nManager(
        locale_dir=str(tmp_path),
        fallback_locale="zh_CN",
        config_language="fr_FR",
    )
    monkeypatch.setattr(i18n_service, "_i18n_manager", manager)

    assert _font_search_placeholder("fr_FR") == "Rechercher des polices…"
    assert _font_search_placeholder("fr_CA") == "Rechercher des polices…"
    assert _font_search_placeholder("zh_CN") == "搜索字体…"
    # Missing key in another locale falls back to fallback_locale (zh_CN)
    assert _font_search_placeholder("es_ES") == "搜索字体…"


def test_setup_i18n_shares_singleton_with_service_container(tmp_path, monkeypatch):
    (tmp_path / "desktop_qt_ui" / "locales").mkdir(parents=True)
    (tmp_path / "desktop_qt_ui" / "locales" / "zh_CN.json").write_text("{}", encoding="utf-8")
    (tmp_path / "desktop_qt_ui" / "locales" / "fr_FR.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(i18n_service, "_i18n_manager", i18n_service._i18n_manager)
    monkeypatch.setattr(services.ServiceManager, "_container", None)

    fake_config = SimpleNamespace(
        app=SimpleNamespace(ui_language="fr_FR"),
        cli=SimpleNamespace(verbose=False),
    )
    monkeypatch.setattr(services, "LogService", lambda: SimpleNamespace(set_console_log_level=lambda *_: None))
    monkeypatch.setattr(services, "StateManager", lambda: SimpleNamespace(set_app_ready=lambda *_: None))
    monkeypatch.setattr(services, "ConfigService", lambda root: SimpleNamespace(get_config=lambda: fake_config))
    monkeypatch.setattr(services, "PresetService", lambda config_service=None: SimpleNamespace())

    container = services.ServiceContainer(str(tmp_path))
    container._init_essential_services()
    services.ServiceManager._container = container

    assert services.get_i18n_manager() is i18n_service.get_i18n_manager()
    assert services.get_i18n_manager().get_current_locale() == "fr_FR"
    assert services.get_i18n_manager().set_locale("zh_CN") is True
    assert i18n_service.get_i18n_manager().get_current_locale() == "zh_CN"


def test_main_view_refresh_ui_texts_reloads_dynamic_settings_and_combos():
    calls = []
    fake_view = SimpleNamespace(
        _t=lambda key, **kw: key.format(**kw) if kw else key,
        _settings_ui_ready=True,
        _env_api_groups_signature="sig",
        refresh_tab_titles=lambda: calls.append("refresh_tab_titles"),
        populate_theme_combo=lambda: calls.append("populate_theme_combo"),
        populate_language_combo=lambda: calls.append("populate_language_combo"),
        update_start_button_text=lambda: calls.append("update_start_button_text"),
        _clear_dynamic_settings=lambda: calls.append("_clear_dynamic_settings"),
        _create_dynamic_settings=lambda: calls.append("_create_dynamic_settings"),
        _refresh_dynamic_setting_texts=lambda: calls.append("_refresh_dynamic_setting_texts"),
        _refresh_prompt_manager=lambda: calls.append("_refresh_prompt_manager"),
        _update_workflow_mode_description=lambda idx: calls.append("_update_workflow_mode_description"),
    )

    MainView.refresh_ui_texts(fake_view)

    assert "populate_theme_combo" in calls
    assert "populate_language_combo" in calls
    assert "_refresh_prompt_manager" in calls
    assert "_clear_dynamic_settings" in calls
    assert "_create_dynamic_settings" in calls
    assert calls.index("_clear_dynamic_settings") < calls.index("_create_dynamic_settings")
    assert "_refresh_dynamic_setting_texts" not in calls


def test_refresh_preset_list_preserves_saved_preset_without_emitting():
    class _FakeCombo:
        def __init__(self):
            self._items = []
            self._idx = -1

        def blockSignals(self, _):
            pass

        def clear(self):
            self._items.clear()
            self._idx = -1

        def addItems(self, items):
            self._items.extend(items)
            if self._idx < 0 and self._items:
                self._idx = 0

        def setCurrentText(self, text):
            self._idx = self._items.index(text)

        def setCurrentIndex(self, idx):
            self._idx = idx

        def currentText(self):
            return self._items[self._idx] if 0 <= self._idx < len(self._items) else ""

        def currentIndex(self):
            return self._idx

    emitted = []
    fake_view = SimpleNamespace(
        preset_combo=_FakeCombo(),
        controller=SimpleNamespace(
            get_presets_list=lambda: ["Personal", "Work"],
            config_service=SimpleNamespace(get_current_preset=lambda: "Work"),
        ),
        _on_preset_changed=lambda name: emitted.append(name),
    )

    env_management.refresh_preset_list(fake_view, emit_change=False)

    assert fake_view.preset_combo.currentText() == "Work"
    assert emitted == []


def test_all_locale_files_complete_no_duplicates_and_valid_placeholders():
    def load_no_dups(path):
        dups = []

        def hook(pairs):
            obj = {}
            for k, v in pairs:
                if k in obj:
                    dups.append(k)
                obj[k] = v
            return obj

        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=hook)
        return data, dups

    files = {p.name: load_no_dups(p) for p in sorted(LOCALE_DIR.glob("*.json"))}
    assert "zh_CN.json" in files
    assert "fr_FR.json" in files

    zh_data, zh_dups = files["zh_CN.json"]
    assert not zh_dups

    for name, (data, dups) in files.items():
        assert not dups, f"{name} has duplicate keys: {dups}"
        assert set(data.keys()) == set(zh_data.keys()), name
        for key, value in data.items():
            assert isinstance(value, str) and value.strip(), (name, key)
            assert _placeholders(value) == _placeholders(zh_data[key]), (name, key)
