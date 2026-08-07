---
title: Contributing
description: How to open issues for feature requests and bug reports, submit PRs, and join community channels
pageId: community.contributing
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Contributing

This page explains how to contribute to the Manga Translator project: opening issues for feature requests and bug reports, submitting code via pull requests, and joining community channels.

## Feature boundary {#feature-boundary}

- This page covers only how to participate: opening issues, opening PRs, and community channels.
- For feature development flows, see [Adding or Changing a Feature](../developer/adding-or-changing-a-feature.md); for tests and code quality, see [Tests and Code Quality](../developer/tests-and-code-quality.md); for packaging and release, see [Packaging and Release](../developer/packaging-and-release.md); for architecture and code boundaries, see [Architecture and Code Boundaries](../developer/architecture-and-code-boundaries.md).
- For friendly-link applications, see [Related Projects and Links](../developer/related-projects-and-links.md).

## Opening an issue {#opening-an-issue}

All suggestions and feedback go through GitHub Issues: [https://github.com/hgmzhn/manga-translator-ui/issues](https://github.com/hgmzhn/manga-translator-ui/issues).

**Feature requests** (new features, interaction improvements, workflow improvements) use the "Feature Request" template: usage scenario, expected feature, value and benefit; you may attach prototypes, screenshots, or examples. Search the existing issues first to confirm it is not a duplicate; if an existing feature does not behave as expected, use the "Bug Report" template instead.

**Bug reports** use the "Bug Report" template: issue type, summary, reproduction steps, expected vs. actual, and environment. You must provide the original image or input file used before translation (not just the result image), and include configuration, logs, and relevant JSON when possible; logs are at `result/log_*.txt` by default.

**Privacy and redaction**: do not upload `.env` contents, accounts, API keys, tokens, cookies, or complete preset files that contain secrets (`presets/*.json`); redact usernames and secrets in paths first.

## Opening a PR {#opening-a-pr}

Code contributions go through pull requests: fork the repository, create a branch, modify per the guidelines, test locally, open a PR, and wait for maintainer review and merge.

- See [Adding or Changing a Feature](../developer/adding-or-changing-a-feature.md) for feature development flows.
- See [Tests and Code Quality](../developer/tests-and-code-quality.md) for tests and code quality.
- See [Packaging and Release](../developer/packaging-and-release.md) for packaging and release.
- See [Architecture and Code Boundaries](../developer/architecture-and-code-boundaries.md) for architecture and code boundaries.
- Friendly-link applications do not require a PR; just open an application issue, see [Related Projects and Links](../developer/related-projects-and-links.md).

## Community channels {#community-channels}

- Community channels such as chat groups and documentation navigation are listed in the repository [README](https://github.com/hgmzhn/manga-translator-ui/blob/main/README.md) (English version: [README_EN.md](https://github.com/hgmzhn/manga-translator-ui/blob/main/README_EN.md)).
- For online documentation, see the DeepWiki link in the README.

## Related pages {#related-pages}

- [Adding or Changing a Feature](../developer/adding-or-changing-a-feature.md): feature development flow and change steps.
- [Tests and Code Quality](../developer/tests-and-code-quality.md): test directories, uv commands, and format checks.
- [Packaging and Release](../developer/packaging-and-release.md): packaging and release flow.
- [Architecture and Code Boundaries](../developer/architecture-and-code-boundaries.md): module boundaries and call relationships.
- [Related Projects and Links](../developer/related-projects-and-links.md): how to apply for a friendly link (open an issue).
