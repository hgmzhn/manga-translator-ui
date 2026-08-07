import _bootstrap  # noqa: F401

import importlib.util
import subprocess


ROOT = _bootstrap.ROOT


def load_launch(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pytorch_detection_allows_slow_portable_import(monkeypatch):
    launch = load_launch("launch_pytorch_timeout_test")
    seen = {}

    def fake_run(*args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(args[0], 0, stdout="CPU|CPU-only\n", stderr="")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)

    assert launch.detect_installed_pytorch_version() == ("CPU", "CPU-only")
    assert seen["timeout"] == 60


def test_pytorch_detection_reports_timeout(monkeypatch):
    launch = load_launch("launch_pytorch_timeout_error_test")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(launch.subprocess, "run", fake_run)

    pytorch_type, detail = launch.detect_installed_pytorch_version()
    assert pytorch_type is None
    assert "60 秒" in detail
