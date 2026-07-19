import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "manga_translator" / "runtime_paths.py"
_SPEC = importlib.util.spec_from_file_location("runtime_paths_under_test", _MODULE_PATH)
runtime_paths = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(runtime_paths)

_RESOURCE_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "desktop_qt_ui" / "utils" / "resource_helper.py"
)
_RESOURCE_HELPER_SPEC = importlib.util.spec_from_file_location(
    "resource_helper_under_test",
    _RESOURCE_HELPER_PATH,
)
resource_helper = importlib.util.module_from_spec(_RESOURCE_HELPER_SPEC)
assert _RESOURCE_HELPER_SPEC.loader is not None
_RESOURCE_HELPER_SPEC.loader.exec_module(resource_helper)


def test_development_resources_use_project_root(monkeypatch):
    monkeypatch.delattr(runtime_paths.sys, "frozen", raising=False)

    expected_root = Path(runtime_paths.__file__).resolve().parent.parent
    assert Path(runtime_paths.get_application_dir()) == expected_root
    assert Path(runtime_paths.get_config_dir()) == expected_root / "config"


def test_packaged_resources_are_next_to_executable(monkeypatch, tmp_path):
    executable = tmp_path / "app.exe"
    internal_dir = tmp_path / "_internal"

    monkeypatch.setattr(runtime_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime_paths.sys, "executable", str(executable))
    monkeypatch.setattr(runtime_paths.sys, "_MEIPASS", str(internal_dir), raising=False)

    assert Path(runtime_paths.get_application_dir()) == tmp_path
    assert Path(runtime_paths.get_config_path("config.json")) == tmp_path / "config" / "config.json"
    assert internal_dir not in Path(runtime_paths.get_config_dir()).parents


def test_packaged_ui_resources_do_not_fall_back_to_internal(monkeypatch, tmp_path):
    executable = tmp_path / "app.exe"

    monkeypatch.setattr(resource_helper.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resource_helper.sys, "executable", str(executable))
    monkeypatch.setattr(
        resource_helper.sys,
        "_MEIPASS",
        str(tmp_path / "_internal"),
        raising=False,
    )

    assert resource_helper._resource_base_candidates() == [str(tmp_path)]
    assert Path(resource_helper.resource_path("fonts")) == tmp_path / "fonts"
