"""Shared Git state helpers for the maintenance launcher and desktop updater."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SUPPORTED_BRANCHES = ("main", "beta")
GIT_MIRRORS = (
    ("GitHub 官方", "GitHub official", "https://github.com/hgmzhn/manga-translator-ui.git"),
    (
        "Gitee 镜像",
        "Gitee mirror",
        "https://gitee.com/hgmzhn/manga-translator-ui.git",
    ),
    (
        "GitCode 镜像",
        "GitCode mirror",
        "https://gitcode.com/hgmzhn/manga-translator-ui",
    ),
)


def git_executable(root: Path) -> str:
    """Resolve the bundled Git first, then the configured/system Git."""
    portable_name = "git.exe" if os.name == "nt" else "git"
    portable_git = root / "PortableGit" / "cmd" / portable_name
    if portable_git.exists():
        return str(portable_git)
    return os.environ.get("GIT") or shutil.which("git") or "git"

_GIT_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def non_interactive_git_env() -> dict[str, str]:
    """Return an environment that prevents Git credential UI and terminal prompts."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    return env


def _run_git(
    root: Path,
    args: list[str],
    *,
    timeout: float,
    executable: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run Git without creating a console window in the desktop application."""
    try:
        return subprocess.run(
            [executable or git_executable(root), *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
            creationflags=_GIT_CREATION_FLAGS,
            env=non_interactive_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_output(
    root: Path,
    args: list[str],
    *,
    timeout: float = 15,
    executable: str | None = None,
) -> str | None:
    """Run a Git read command and return trimmed stdout on success."""
    result = _run_git(root, args, timeout=timeout, executable=executable)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()

def remote_url(root: Path, *, executable: str | None = None) -> str:
    """Return the configured origin URL."""
    return (
        git_output(
            root,
            ["config", "--get", "remote.origin.url"],
            executable=executable,
        )
        or ""
    )


def set_origin_url(root: Path, url: str, *, executable: str | None = None) -> bool:
    """Persist a new origin URL for both the UI and maintenance launcher."""
    result = _run_git(
        root,
        ["remote", "set-url", "origin", url],
        timeout=15,
        executable=executable,
    )
    return result is not None and result.returncode == 0


def mirror_index(url: str) -> int:
    """Return the matching configured mirror index, defaulting to GitHub."""
    normalized = (url or "").strip().removesuffix(".git")
    for index, (_, _, mirror_url) in enumerate(GIT_MIRRORS):
        if normalized == mirror_url.removesuffix(".git"):
            return index
    return 0


def current_branch(
    root: Path,
    *,
    executable: str | None = None,
) -> tuple[str, bool]:
    """Return the current branch/tag-or-commit name and detached state."""
    branch = git_output(root, ["rev-parse", "--abbrev-ref", "HEAD"], executable=executable)
    if branch and branch != "HEAD":
        return branch, False
    tag = git_output(root, ["describe", "--tags", "--exact-match"], executable=executable)
    if tag:
        return tag, True
    commit = git_output(root, ["rev-parse", "--short", "HEAD"], executable=executable) or "unknown"
    return commit, True


def update_branch(
    root: Path,
    *,
    executable: str | None = None,
    supported_branches: tuple[str, ...] = SUPPORTED_BRANCHES,
) -> str:
    """Select the branch to compare, falling back to main when detached."""
    branch, detached = current_branch(root, executable=executable)
    if not detached and branch in supported_branches:
        return branch
    return "main"


def fetch_origin(
    root: Path,
    branch: str,
    *,
    timeout: float = 30,
    executable: str | None = None,
) -> bool:
    """Fetch one origin branch, returning whether the remote ref is current."""
    result = _run_git(
        root,
        ["fetch", "origin", branch],
        timeout=timeout,
        executable=executable,
    )
    return result is not None and result.returncode == 0


def current_commit(root: Path, *, short: bool = False, executable: str | None = None) -> str:
    """Return the local HEAD hash, or ``unknown`` when Git is unavailable."""
    args = ["rev-parse", "--short" if short else "HEAD"]
    return git_output(root, args, executable=executable) or "unknown"


def remote_commit(root: Path, branch: str, *, executable: str | None = None) -> str:
    """Return the fetched origin branch hash, or ``unknown``."""
    return (
        git_output(root, ["rev-parse", f"origin/{branch}"], executable=executable)
        or "unknown"
    )


def remote_file(
    root: Path,
    branch: str,
    relative_path: str,
    *,
    executable: str | None = None,
) -> str | None:
    """Read a text file from a fetched origin branch."""
    return git_output(
        root,
        ["show", f"origin/{branch}:{relative_path}"],
        executable=executable,
    )


def commits_behind(
    root: Path,
    branch: str,
    *,
    executable: str | None = None,
) -> int | None:
    """Return the number of commits by which HEAD trails origin/branch."""
    value = git_output(
        root,
        ["rev-list", "--count", f"HEAD..origin/{branch}"],
        executable=executable,
    )
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
