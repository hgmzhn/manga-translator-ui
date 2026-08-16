import _bootstrap  # noqa: F401

import importlib.util
from types import SimpleNamespace


ROOT = _bootstrap.ROOT


def load_package_checker(name: str):
    path = ROOT / "packaging" / "build_utils" / "package_checker.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_missing_distribution_version_is_reported_for_reinstall(monkeypatch):
    checker = load_package_checker("package_checker_missing_version_test")
    distribution = SimpleNamespace(version=None)
    monkeypatch.setattr(
        checker.importlib_metadata,
        "distribution",
        lambda _name: distribution,
    )

    requirement = "broken-package>=1"

    assert checker.get_missing_packages([requirement]) == [requirement]
