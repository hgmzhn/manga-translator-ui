import _bootstrap  # noqa: F401

from desktop_qt_ui.utils import app_version


def test_get_app_version_reads_only_packaging_version(monkeypatch):
    seen = []

    def fake_paths(relative_paths):
        seen.append(tuple(relative_paths))
        return iter(["/package/packaging/VERSION"])

    def fake_open(path, mode, encoding):
        assert path == "/package/packaging/VERSION"
        return _VersionFile("v3.0.2\n")

    class _VersionFile:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.value

    monkeypatch.setattr(app_version, "iter_existing_resource_paths", fake_paths)
    monkeypatch.setattr("builtins.open", fake_open)

    assert app_version.get_app_version() == "3.0.2"
    assert seen == [("packaging/VERSION",)]
