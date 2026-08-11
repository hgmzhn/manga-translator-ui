import _bootstrap  # noqa: F401

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = _bootstrap.ROOT


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_check_version_info_fetch_failure_reports_warning(tmp_path, monkeypatch, capsys):
    launch = load_module("launch_update_test", "packaging/launch.py")

    packaging_dir = tmp_path / "packaging"
    packaging_dir.mkdir()
    (packaging_dir / "VERSION").write_text("1.7.6", encoding="utf-8")

    monkeypatch.setattr(launch, "PATH_ROOT", tmp_path)
    monkeypatch.setattr(launch, "ensure_git_safe_directory", lambda: None)
    monkeypatch.setattr(launch, "get_current_branch", lambda: ("main", False))
    monkeypatch.setattr(launch, "get_update_branch", lambda: "main")
    monkeypatch.setattr(launch, "get_mirror_display_name", lambda *args, **kwargs: "GitHub")

    def fake_run(cmd, *args, **kwargs):
        if cmd[1:3] == ["fetch", "origin"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="network error")
        if cmd[1:3] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)

    current_version, remote_version = launch.check_version_info()
    out = capsys.readouterr().out

    assert current_version == "1.7.6"
    assert remote_version == "unknown"
    assert "[警告] 无法获取远程版本信息" in out
    assert "当前已是最新版本" not in out


def test_check_all_updates_fetch_failure_marks_code_update_needed(tmp_path, monkeypatch, capsys):
    launch = load_module("launch_update_all_test", "packaging/launch.py")

    packaging_dir = tmp_path / "packaging"
    packaging_dir.mkdir()
    (packaging_dir / "VERSION").write_text("1.7.6", encoding="utf-8")

    monkeypatch.setattr(launch, "PATH_ROOT", tmp_path)
    monkeypatch.setattr(launch, "ensure_git_safe_directory", lambda: None)
    monkeypatch.setattr(launch, "get_update_branch", lambda: "main")
    monkeypatch.setattr(launch, "get_mirror_display_name", lambda *args, **kwargs: "GitHub")
    monkeypatch.setattr(launch, "get_requirements_file_from_env", lambda: (None, None, None))

    def fake_run(cmd, *args, **kwargs):
        if cmd[1:3] == ["fetch", "origin"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="network error")
        if cmd[1:3] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
        if cmd[1:3] == ["show", "origin/main:packaging/VERSION"]:
            raise AssertionError("remote version should not be queried after fetch failure")
        if cmd[1:3] == ["rev-parse", "origin/main"]:
            raise AssertionError("remote commit should not be queried after fetch failure")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)

    code_needs_update, deps_needs_update, current_version, remote_version, req_file, missing_packages = launch.check_all_updates()
    out = capsys.readouterr().out

    assert code_needs_update is True
    assert remote_version == "unknown"
    assert current_version == "1.7.6"
    assert req_file is None
    assert missing_packages == []
    assert "[无法获取远程更新" in out
    assert "  状态: [已是最新]" not in out


def test_check_version_brief_fetch_failure_reports_warning(monkeypatch, capsys):
    check_version = load_module("check_version_update_test", "packaging/check_version.py")

    monkeypatch.setattr(check_version, "get_git_command", lambda: "git")

    def fake_run(cmd, *args, **kwargs):
        if cmd[1:3] == ["fetch", "origin"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="network error")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(check_version.subprocess, "run", fake_run)
    monkeypatch.setattr(check_version.sys, "argv", ["check_version.py", "--brief"])

    rc = check_version.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "[警告] 无法获取远程版本信息" in out
    assert "[信息] 已是最新版本" not in out
