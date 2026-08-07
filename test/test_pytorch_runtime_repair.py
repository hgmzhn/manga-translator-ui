import _bootstrap  # noqa: F401

import importlib.util


ROOT = _bootstrap.ROOT


def load_launch(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_missing_torch_does_not_prompt_for_vc_runtime(monkeypatch):
    launch = load_launch("launch_missing_torch_repair_test")
    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("prompted")))

    assert launch.repair_broken_pytorch_runtime(None, "未安装") == (None, "未安装", False)


def test_broken_torch_rechecks_after_vc_runtime_install(monkeypatch):
    launch = load_launch("launch_successful_torch_repair_test")
    opened = []
    answers = iter(["", ""])
    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(launch.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(launch, "detect_installed_pytorch_version", lambda: ("CPU", "CPU-only"))

    result = launch.repair_broken_pytorch_runtime(None, "安装损坏: WinError 1114 DLL")

    assert result == ("CPU", "CPU-only", False)
    assert opened == [launch.VC_REDIST_X64_URL]


def test_broken_torch_is_marked_for_removal_when_recheck_fails(monkeypatch):
    launch = load_launch("launch_failed_torch_repair_test")
    answers = iter(["n", ""])
    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(
        launch, "detect_installed_pytorch_version", lambda: (None, "安装损坏: WinError 1114")
    )

    assert launch.repair_broken_pytorch_runtime(
        None, "安装损坏: WinError 1114"
    ) == (None, "安装损坏: WinError 1114", True)


def test_broken_torch_prompt_uses_english_language(monkeypatch, capsys):
    launch = load_launch("launch_english_torch_repair_test")
    answers = iter(["n", ""])
    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr(launch, "LANG", "en")
    monkeypatch.setattr("builtins.input", lambda prompt: (print(prompt, end=""), next(answers))[1])
    monkeypatch.setattr(
        launch, "detect_installed_pytorch_version", lambda: (None, "broken DLL: WinError 1114")
    )

    launch.repair_broken_pytorch_runtime(None, "broken DLL: WinError 1114")
    output = capsys.readouterr().out

    assert "PyTorch DLL could not be loaded" in output
    assert "Open the official Microsoft download URL?" in output
    assert "broken PyTorch installation will be removed" in output
