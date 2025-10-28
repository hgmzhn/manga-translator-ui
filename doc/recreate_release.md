# Release/Tag Recreation Utility

Use the `scripts/recreate_release.py` helper to delete and recreate a GitHub release/tag pair (e.g. to retrigger workflows).

## Prerequisites

- A GitHub token with `repo` scope (`GITHUB_TOKEN` or `GH_TOKEN`).
- Network access to github.com.

## Usage

```bash
python scripts/recreate_release.py \
    --repo hgmzhn/manga-translator-ui \
    --tag v1.7.3 \
    --token "$GITHUB_TOKEN"
```

### Optional flags

- `--fallback-ref`: Ref used when the original tag cannot be read (defaults to `main`).
- `--changelog`: Override the fallback changelog description source. Defaults to `doc/CHANGELOG_<tag>.md` when present.
- `--timeout` / `--poll-interval`: Control how long the script waits for release-triggered workflows to appear.
- `--quiet`: Reduce log output while keeping the final summary.

The script will:

1. Capture the current release metadata and tag target/message.
2. Delete the existing release and `refs/tags/<tag>` reference when present.
3. Recreate an annotated tag pointing at the preserved commit SHA (or the provided fallback ref when absent).
4. Recreate the release, reusing the previous title/body flags or falling back to `doc/CHANGELOG_<tag>.md` if no prior body exists.
5. Poll GitHub Actions for a release-triggered workflow run and print the discovered run URL/status.

If your repository enforces signed tags or other protections, ensure the token/environment satisfies those requirements before running the script.
