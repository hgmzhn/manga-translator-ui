"""Background Git release checks and launcher handoff for the desktop UI."""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from desktop_qt_ui.core.git_update_helpers import (
    commits_behind,
    current_commit,
    fetch_origin,
    git_executable,
    remote_commit,
    remote_file,
    update_branch,
)
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

REPOSITORY_URL = "https://github.com/hgmzhn/manga-translator-ui"


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    release_notes: str
    published_at: str
    current_commit: str = ""
    latest_commit: str = ""
    commits_behind: int = 0

    @property
    def is_update_available(self) -> bool:
        versions_are_valid = bool(
            _version_parts(self.latest_version) and _version_parts(self.current_version)
        )
        version_update = versions_are_valid and (
            compare_versions(self.latest_version, self.current_version) >= 0
        )
        commit_update = self.commits_behind > 0
        return version_update or commit_update


def _version_parts(version: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", str(version or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def compare_versions(left: str, right: str) -> int:
    """Compare release versions, returning -1, 0, or 1."""
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    if not left_parts or not right_parts:
        return 0
    width = max(len(left_parts), len(right_parts))
    left_parts += (0,) * (width - len(left_parts))
    right_parts += (0,) * (width - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def fetch_latest_release(
    current_version: str,
    timeout: float = 30.0,
    branch: str | None = None,
) -> UpdateInfo:
    """Check origin with the same Git comparison used by the maintenance tool."""
    root = Path(__file__).resolve().parents[2]
    executable = git_executable(root)
    branch = branch or update_branch(root, executable=executable)
    local_commit = current_commit(root, executable=executable)
    if local_commit == "unknown":
        raise RuntimeError("Unable to read the current Git commit")
    if not fetch_origin(root, branch, timeout=timeout, executable=executable):
        raise RuntimeError(f"Unable to fetch origin/{branch}")

    latest_commit = remote_commit(root, branch, executable=executable)
    if latest_commit == "unknown":
        raise RuntimeError(f"Unable to read origin/{branch}")
    latest_version = (
        remote_file(
            root,
            branch,
            "packaging/VERSION",
            executable=executable,
        )
        or "unknown"
    ).lstrip("vV")
    behind = commits_behind(root, branch, executable=executable)
    if behind is None:
        raise RuntimeError(f"Unable to compare HEAD with origin/{branch}")
    changelog_path = f"doc/CHANGELOG_v{latest_version}.md"
    release_notes = (
        remote_file(
            root,
            branch,
            changelog_path,
            executable=executable,
        )
        or ""
    )
    return UpdateInfo(
        current_version=str(current_version or "unknown"),
        latest_version=latest_version,
        release_url=f"{REPOSITORY_URL}/releases/tag/v{latest_version}",
        release_notes=release_notes,
        published_at="",
        current_commit=local_commit,
        latest_commit=latest_commit,
        commits_behind=behind,
    )

def launch_update_maintenance(branch: str | None = None) -> bool:
    """Start the maintenance tool's confirmed, non-interactive update mode."""
    root = Path(__file__).resolve().parents[2]
    update_args = ["--auto-update"]
    if branch:
        update_args.extend(["--branch", branch])
    try:
        if sys.platform == "win32":
            batch_file = root / "Win-Install-or-Update.bat"
            if batch_file.exists():
                subprocess.Popen(
                    ["cmd.exe", "/d", "/c", str(batch_file), *update_args],
                    cwd=root,
                )
                return True
        launcher = root / "packaging" / "launch.py"
        if launcher.exists():
            subprocess.Popen(
                [
                    sys.executable,
                    str(launcher),
                    "--maintenance",
                    *update_args,
                ],
                cwd=root,
            )
            return True
    except OSError:
        return False
    return False


class _UpdateWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, current_version: str, branch: str | None = None):
        super().__init__()
        self.current_version = current_version
        self.branch = branch

    @pyqtSlot()
    def run(self):
        try:
            self.finished.emit(
                fetch_latest_release(self.current_version, branch=self.branch)
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateChecker(QObject):
    """Run one Git update check without blocking the Qt event loop."""

    check_finished = pyqtSignal(object)
    check_failed = pyqtSignal(str)
    checking_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _UpdateWorker | None = None

    def check(self, current_version: str, branch: str | None = None):
        if self._thread is not None and self._thread.isRunning():
            return False
        self._worker = _UpdateWorker(current_version, branch)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self.checking_changed.emit(True)
        self._thread.start()
        return True

    def _on_finished(self, info: UpdateInfo):
        self.check_finished.emit(info)

    def _on_failed(self, message: str):
        self.check_failed.emit(message)

    def _on_thread_finished(self):
        self._thread = None
        self._worker = None
        self.checking_changed.emit(False)

    def stop(self):
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(1000)
        self._thread = None
        self._worker = None
