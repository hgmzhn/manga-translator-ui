import _bootstrap  # noqa: F401, I001
import builtins
import importlib.util
from desktop_qt_ui.core import git_update_helpers
from services import update_service
from services.update_service import UpdateInfo, compare_versions

ROOT = _bootstrap.ROOT


def _load_maintenance_launcher():
    path = ROOT / "packaging" / "launch.py"
    spec = importlib.util.spec_from_file_location("maintenance_launch_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


maintenance_launch = _load_maintenance_launcher()



def test_compare_versions_handles_v_prefix_and_missing_patch_parts():
    assert compare_versions("v2.2.11", "2.2.10") == 1
    assert compare_versions("2.2", "v2.2.0") == 0
    assert compare_versions("2.1.99", "2.2") == -1


def test_git_commands_use_no_window_creation_flags_and_disable_prompts(monkeypatch, tmp_path):
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return git_update_helpers.subprocess.CompletedProcess(args, 0, "ok\n", "")

    monkeypatch.setattr(git_update_helpers.subprocess, "run", fake_run)
    monkeypatch.setattr(git_update_helpers, "_GIT_CREATION_FLAGS", 0x08000000)

    assert git_update_helpers.git_output(tmp_path, ["status"], executable="git") == "ok"
    assert captured["creationflags"] == 0x08000000
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GCM_INTERACTIVE"] == "Never"


def test_update_info_reports_only_newer_release_as_available():
    newer = UpdateInfo("2.2.10", "2.2.11", "https://example.test", "", "")
    same = UpdateInfo("2.2.10", "2.2.10", "https://example.test", "", "")
    unknown = UpdateInfo("unknown", "unknown", "https://example.test", "", "")
    assert newer.is_update_available
    assert not same.is_update_available
    assert not unknown.is_update_available


def test_update_info_reports_remote_commit_gap_without_version_change():
    info = UpdateInfo(
        "2.2.10",
        "2.2.10",
        "https://example.test/commit/remote",
        "",
        "",
        current_commit="local",
        latest_commit="remote",
        commits_behind=2,
    )
    assert info.is_update_available


def test_fetch_latest_release_reads_matching_remote_changelog(monkeypatch):
    requested_files = []

    def fake_remote_file(_root, _branch, relative_path, **_kwargs):
        requested_files.append(relative_path)
        return {
            "packaging/VERSION": "v2.2.10",
            "doc/CHANGELOG_v2.2.10.md": "# v2.2.10 更新日志\n\n- 修复更新流程",
        }[relative_path]

    monkeypatch.setattr(update_service, "git_executable", lambda _root: "git")
    monkeypatch.setattr(
        update_service, "current_commit", lambda _root, **_kwargs: "local-commit"
    )
    monkeypatch.setattr(update_service, "fetch_origin", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        update_service,
        "remote_commit",
        lambda _root, _branch, **_kwargs: "remote-commit",
    )
    monkeypatch.setattr(update_service, "remote_file", fake_remote_file)
    monkeypatch.setattr(update_service, "commits_behind", lambda *_args, **_kwargs: 1)

    info = update_service.fetch_latest_release("2.2.9", branch="main")

    assert requested_files == [
        "packaging/VERSION",
        "doc/CHANGELOG_v2.2.10.md",
    ]
    assert info.release_notes.startswith("# v2.2.10 更新日志")
    assert info.release_url == (
        "https://github.com/hgmzhn/manga-translator-ui/releases/tag/v2.2.10"
    )


def test_launch_update_maintenance_passes_confirmed_branch_without_environment_flags(
    monkeypatch,
):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(update_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(update_service.sys, "platform", "win32")

    assert update_service.launch_update_maintenance("beta")
    assert captured["args"][-3:] == ["--auto-update", "--branch", "beta"]
    assert "env" not in captured["kwargs"]


def test_automatic_full_update_skips_confirmation(monkeypatch):
    dependency_updates = []
    monkeypatch.setattr(
        maintenance_launch,
        "check_all_updates",
        lambda: (False, True, None, None, None, ["example-package"]),
    )
    monkeypatch.setattr(
        maintenance_launch,
        "update_runtime_dependencies",
        lambda args, req_file, missing: dependency_updates.append(missing) or True,
    )
    monkeypatch.setattr(
        builtins,
        "input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("automatic update prompted for confirmation")
        ),
    )

    args = type("Args", (), {"requirements": "auto"})()
    assert maintenance_launch.run_full_update(args, automatic=True)
    assert dependency_updates == [["example-package"]]


def test_restart_desktop_ui_uses_current_maintenance_interpreter(monkeypatch):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(maintenance_launch.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(maintenance_launch.sys, "platform", "win32")

    assert maintenance_launch.restart_desktop_ui()
    assert captured["args"] == [
        maintenance_launch.sys.executable,
        str(maintenance_launch.PATH_ROOT / "desktop_qt_ui" / "main.py"),
    ]
    assert captured["kwargs"]["cwd"] == maintenance_launch.PATH_ROOT
    assert captured["kwargs"]["creationflags"] & maintenance_launch.subprocess.DETACHED_PROCESS


def test_automatic_maintenance_restarts_after_update(monkeypatch):
    events = []
    monkeypatch.setattr(maintenance_launch, "init_language", lambda: None)
    monkeypatch.setattr(
        maintenance_launch,
        "run_full_update",
        lambda _args, automatic=False: events.append(("update", automatic)) or True,
    )
    monkeypatch.setattr(
        maintenance_launch,
        "restart_desktop_ui",
        lambda: events.append(("restart", True)) or True,
    )

    maintenance_launch.maintenance_menu(automatic=True)

    assert events == [("update", True), ("restart", True)]
