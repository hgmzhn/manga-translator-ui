#!/usr/bin/env python3
"""Utility for deleting and recreating a GitHub release/tag pair."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

import requests

API_BASE_URL = "https://api.github.com"
API_ACCEPT_HEADER = "application/vnd.github+json"
API_VERSION_HEADER = "2022-11-28"


class GitHubAPIError(RuntimeError):
    """Raised when the GitHub API returns an unexpected response."""

    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[requests.Response] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


@dataclass
class ReleaseSnapshot:
    release_id: Optional[int]
    name: str
    body: Optional[str]
    draft: bool
    prerelease: bool


@dataclass
class TagSnapshot:
    commit_sha: str
    message: str
    tagger: Optional[Dict[str, Any]]


def parse_github_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHubReleaseManager:
    def __init__(self, repo: str, token: str, verbose: bool = True) -> None:
        if not token:
            raise ValueError("A GitHub token is required. Pass --token or set GITHUB_TOKEN/GH_TOKEN.")

        self.repo = repo
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": API_ACCEPT_HEADER,
                "User-Agent": "manga-translator-release-automation/1.0",
                "X-GitHub-Api-Version": API_VERSION_HEADER,
            }
        )

    def log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: Iterable[int],
        allow_404: bool = False,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        url = f"{API_BASE_URL}{path}"
        response = self.session.request(method, url, **kwargs)

        if response.status_code == 404 and allow_404:
            return None

        if response.status_code not in expected:
            details = response.text.strip()
            raise GitHubAPIError(
                f"GitHub API call to {method} {path} failed with status {response.status_code}: {details}",
                status_code=response.status_code,
                response=response,
            )

        if response.status_code == 204 or not response.content:
            return None

        return response.json()

    def get_release_by_tag(self, tag: str) -> Optional[Dict[str, Any]]:
        return self._request(
            "GET",
            f"/repos/{self.repo}/releases/tags/{tag}",
            expected=(200,),
            allow_404=True,
        )

    def delete_release(self, release_id: int) -> None:
        self._request(
            "DELETE",
            f"/repos/{self.repo}/releases/{release_id}",
            expected=(204,),
        )

    def get_tag_ref(self, tag: str) -> Optional[Dict[str, Any]]:
        return self._request(
            "GET",
            f"/repos/{self.repo}/git/refs/tags/{tag}",
            expected=(200,),
            allow_404=True,
        )

    def delete_tag_ref(self, tag: str) -> None:
        self._request(
            "DELETE",
            f"/repos/{self.repo}/git/refs/tags/{tag}",
            expected=(204,),
        )

    def get_git_tag(self, sha: str) -> Optional[Dict[str, Any]]:
        return self._request(
            "GET",
            f"/repos/{self.repo}/git/tags/{sha}",
            expected=(200,),
            allow_404=True,
        )

    def resolve_commit_sha(self, ref: str) -> str:
        data = self._request(
            "GET",
            f"/repos/{self.repo}/commits/{ref}",
            expected=(200,),
        )
        if not data or "sha" not in data:
            raise GitHubAPIError(f"Unable to resolve commit for ref '{ref}'.")
        return data["sha"]

    def create_annotated_tag(
        self,
        tag: str,
        message: str,
        commit_sha: str,
        tagger: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = {
            "tag": tag,
            "message": message,
            "object": commit_sha,
            "type": "commit",
            "tagger": tagger,
        }
        data = self._request(
            "POST",
            f"/repos/{self.repo}/git/tags",
            expected=(201,),
            json=payload,
        )
        if not data:
            raise GitHubAPIError("GitHub did not return tag data after creation.")
        return data

    def create_tag_ref(self, tag: str, tag_sha: str) -> None:
        payload = {"ref": f"refs/tags/{tag}", "sha": tag_sha}
        self._request(
            "POST",
            f"/repos/{self.repo}/git/refs",
            expected=(201,),
            json=payload,
        )

    def create_release(
        self,
        *,
        tag: str,
        name: str,
        body: str,
        draft: bool,
        prerelease: bool,
        target_commitish: str,
    ) -> Dict[str, Any]:
        payload = {
            "tag_name": tag,
            "name": name,
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
            "target_commitish": target_commitish,
        }
        data = self._request(
            "POST",
            f"/repos/{self.repo}/releases",
            expected=(201,),
            json=payload,
        )
        if not data:
            raise GitHubAPIError("GitHub did not return release data after creation.")
        return data

    def list_release_workflow_runs(self, per_page: int = 20) -> Dict[str, Any]:
        params = {"event": "release", "per_page": per_page}
        data = self._request(
            "GET",
            f"/repos/{self.repo}/actions/runs",
            expected=(200,),
            params=params,
        )
        return data or {"workflow_runs": []}


def load_changelog(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def default_tagger() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    name = (
        os.getenv("GIT_COMMITTER_NAME")
        or os.getenv("GIT_AUTHOR_NAME")
        or os.getenv("GITHUB_ACTOR")
        or "Release Automation"
    )
    email = (
        os.getenv("GIT_COMMITTER_EMAIL")
        or os.getenv("GIT_AUTHOR_EMAIL")
        or os.getenv("GITHUB_ACTOR_EMAIL")
        or os.getenv("GITHUB_ACTOR", "release-automation") + "@users.noreply.github.com"
    )
    return {"name": name, "email": email, "date": now}


def snapshot_release(manager: GitHubReleaseManager, tag: str, fallback_changelog: str) -> ReleaseSnapshot:
    release_data = manager.get_release_by_tag(tag)
    if release_data:
        return ReleaseSnapshot(
            release_id=release_data.get("id"),
            name=release_data.get("name") or tag,
            body=release_data.get("body"),
            draft=bool(release_data.get("draft", False)),
            prerelease=bool(release_data.get("prerelease", False)),
        )

    body = load_changelog(fallback_changelog) if os.path.exists(fallback_changelog) else ""
    return ReleaseSnapshot(
        release_id=None,
        name=tag,
        body=body,
        draft=False,
        prerelease=False,
    )


def snapshot_tag(manager: GitHubReleaseManager, tag: str, fallback_ref: str) -> TagSnapshot:
    tag_ref = manager.get_tag_ref(tag)
    if not tag_ref:
        commit_sha = manager.resolve_commit_sha(fallback_ref)
        return TagSnapshot(commit_sha=commit_sha, message=tag, tagger=None)

    ref_obj = tag_ref.get("object", {})
    ref_type = ref_obj.get("type")
    ref_sha = ref_obj.get("sha")

    if ref_type == "commit" or not ref_type:
        return TagSnapshot(commit_sha=ref_sha, message=tag, tagger=None)

    if ref_type == "tag":
        tag_data = manager.get_git_tag(ref_sha)
        if not tag_data:
            raise GitHubAPIError(f"Tag object {ref_sha} could not be retrieved.")
        object_data = tag_data.get("object") or {}
        if object_data.get("type") != "commit":
            raise GitHubAPIError(
                f"Annotated tag {tag} does not point to a commit (found {object_data.get('type')})."
            )
        message = tag_data.get("message") or tag
        tagger = tag_data.get("tagger")
        return TagSnapshot(commit_sha=object_data.get("sha"), message=message, tagger=tagger)

    raise GitHubAPIError(f"Unhandled tag reference type: {ref_type}")


def wait_for_release_workflow(
    manager: GitHubReleaseManager,
    *,
    tag: str,
    commit_sha: str,
    start_time: datetime,
    timeout: int,
    poll_interval: int,
) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    best_match: Optional[Dict[str, Any]] = None

    while time.monotonic() < deadline:
        runs_data = manager.list_release_workflow_runs(per_page=25)
        runs = runs_data.get("workflow_runs", [])
        for run in runs:
            if run.get("event") != "release":
                continue

            created_at = run.get("created_at")
            if not created_at:
                continue
            created_dt = parse_github_datetime(created_at)
            if created_dt + timedelta(seconds=5) < start_time:
                continue

            display = " ".join(
                filter(
                    None,
                    [
                        run.get("display_title"),
                        run.get("name"),
                        run.get("head_branch"),
                    ],
                )
            ).lower()
            if tag.lower() not in display and run.get("head_sha") not in (None, commit_sha):
                continue

            best_match = run
            if run.get("status") in {"queued", "in_progress", "completed"}:
                return run
        time.sleep(poll_interval)

    return best_match


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recreate a GitHub release/tag pair.")
    parser.add_argument("--repo", default="hgmzhn/manga-translator-ui", help="The owner/repo slug.")
    parser.add_argument("--tag", default="v1.7.3", help="The tag/release name to recreate.")
    parser.add_argument(
        "--token",
        default=os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"),
        help="GitHub token with repo permissions. Defaults to GITHUB_TOKEN/GH_TOKEN.",
    )
    parser.add_argument(
        "--fallback-ref",
        default="main",
        help="Ref to use if the existing tag cannot be read (default: main).",
    )
    parser.add_argument(
        "--changelog",
        default=None,
        help="Path to changelog fallback. Defaults to doc/CHANGELOG_<tag>.md if available.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Seconds to wait for release-triggered workflows (default: 180).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between workflow status polls (default: 5).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce log output (still prints the final summary).",
    )
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    changelog = args.changelog
    if not changelog:
        formatted_tag = args.tag.replace("/", "-")
        changelog_candidate = os.path.join("doc", f"CHANGELOG_{formatted_tag}.md")
        changelog = changelog_candidate

    manager = GitHubReleaseManager(repo=args.repo, token=args.token, verbose=not args.quiet)

    manager.log(f"Capturing existing release and tag information for {args.repo}@{args.tag}...")
    release_snapshot = snapshot_release(manager, args.tag, changelog)
    tag_snapshot = snapshot_tag(manager, args.tag, args.fallback_ref)

    release_body = release_snapshot.body or (load_changelog(changelog) if os.path.exists(changelog) else "")
    tagger = tag_snapshot.tagger or default_tagger()

    if release_snapshot.release_id:
        manager.log(f"Deleting existing release id {release_snapshot.release_id}...")
        manager.delete_release(release_snapshot.release_id)
    else:
        manager.log("No prior release found for this tag.")

    tag_ref_exists = manager.get_tag_ref(args.tag)
    if tag_ref_exists:
        manager.log("Deleting existing tag reference...")
        manager.delete_tag_ref(args.tag)
    else:
        manager.log("No existing tag reference to delete.")

    manager.log(f"Creating annotated tag {args.tag} at commit {tag_snapshot.commit_sha}...")
    tag_data = manager.create_annotated_tag(
        tag=args.tag,
        message=tag_snapshot.message,
        commit_sha=tag_snapshot.commit_sha,
        tagger=tagger,
    )
    tag_sha = tag_data.get("sha")
    if not tag_sha:
        raise GitHubAPIError("GitHub did not return the SHA for the new tag.")

    manager.log("Creating tag ref...")
    manager.create_tag_ref(args.tag, tag_sha)

    manager.log("Creating release...")
    release_data = manager.create_release(
        tag=args.tag,
        name=release_snapshot.name or args.tag,
        body=release_body,
        draft=release_snapshot.draft,
        prerelease=release_snapshot.prerelease,
        target_commitish=tag_snapshot.commit_sha,
    )

    release_url = release_data.get("html_url", "")
    start_time = datetime.now(timezone.utc)

    manager.log("Waiting for release-triggered workflow run...")
    run = wait_for_release_workflow(
        manager,
        tag=args.tag,
        commit_sha=tag_snapshot.commit_sha,
        start_time=start_time,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )

    actions_message = "No release workflow run detected within the timeout."
    if run:
        html_url = run.get("html_url")
        status = run.get("status")
        conclusion = run.get("conclusion")
        actions_message = (
            f"Release workflow status: {status or 'unknown'}"
            + (f", conclusion: {conclusion}" if conclusion else "")
        )
        if html_url:
            actions_message += f"\nWorkflow run: {html_url}"

    summary_lines = [
        "Release recreation completed:",
        f"  Repository: {args.repo}",
        f"  Tag: {args.tag}",
        f"  Commit: {tag_snapshot.commit_sha}",
        f"  Release URL: {release_url or 'N/A'}",
        f"  {actions_message}",
    ]
    print("\n".join(summary_lines))

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitHubAPIError as exc:
        message = str(exc)
        if exc.status_code is not None:
            message = f"[{exc.status_code}] {message}"
        print(f"ERROR: {message}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"ERROR: Request to GitHub failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - safety net
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
