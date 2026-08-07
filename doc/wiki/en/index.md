---
title: Welcome to Manga Translator Wiki
description: "The usage and development documentation site for this software: install, usage, and developer guides for the desktop app, Web, CLI, Docker, and developer interfaces"
pageId: index
lang: en-US
outline: [2, 4]
lastUpdated: true
---

<div align="center">

![Manga Translator Wiki](/home.png){width=300}

</div>

# Welcome to Manga Translator Wiki

This is the usage and development documentation site for this software. Its contents are based on the current repository source, desktop i18n, and public server code, covering the desktop app, Web, CLI, Docker, and developer interfaces. Choose a runtime form first, then follow the relevant operation page; capabilities that have not been checked are not presented as product claims.

## Quick Start {#quick-start}

The project shares the MangaTranslator processing chain but exposes different interaction boundaries:

| Form | Best for | Entry points |
| --- | --- | --- |
| Qt desktop application | Selecting files locally, adjusting parameters, monitoring progress, and revising regions in the visual editor | [Product Forms](./introduction/product-forms.md) · [First Translation](./introduction/first-translation.md) |
| CLI | Headless, scriptable, and batch processing | [Command Structure](./cli/command-structure.md) · [Local Input and Output](./cli/local-input-output.md) |
| Web UI | Uploading, configuring tasks, and viewing results and history in a browser | [Launch and Access](./web/launch-and-access.md) · [Upload, Configure, and Translate](./web/upload-config-and-translate.md) |
| Docker | Running the Web form in a container while persisting resources and server data through volumes | [Docker](./install/docker.md) |
| Internal services | Internal protocols (`ws` / `shared`) required by an existing local integration | [Web, WS, and Shared Modes](./cli/web-ws-and-shared-modes.md) · [Internal Protocols](./developer/internal-shared-and-websocket.md) |

Where to start:

- First time: walk through a complete translation in [First Translation](./introduction/first-translation.md), or compare the runtime forms in [Product Forms](./introduction/product-forms.md).
- Installing: pick the desktop, CLI, or container edition for your platform in [Windows Portable](./install/windows-portable.md), [Linux and macOS](./install/linux-and-macos.md), or [Docker](./install/docker.md).
- Developing or integrating: see [Contributing](#contributing) and [Related Projects and Links](./developer/related-projects-and-links.md), which collect the source, protocols, and surrounding projects.

## Contributing {#contributing}

Contributions are welcome via Pull Request to fix wiki content — typos, outdated information, missing pages — or to adjust the documentation structure. Issues are also welcome for questions and suggestions. See the [Contributing Guide](./community/contributing.md) for the contribution process, and [Related Projects and Links](./developer/related-projects-and-links.md) for related projects.