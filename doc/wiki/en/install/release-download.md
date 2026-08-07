---
title: Download Packed Release
description: Download the CPU/GPU packed release from GitHub Releases, extract it, and run directly without installing Python
pageId: install.release-download
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Download Packed Release

For users who do not want to use the installer scripts or manage a Python environment: download a prebuilt program from GitHub Releases, extract it, and run it directly — no Python installation required.

## Download and choose a version

Go to [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases) and download the latest version:

| Version | Best for | Notes |
| --- | --- | --- |
| CPU build | Any computer | No GPU required; best compatibility |
| GPU build (NVIDIA) | Computers with an NVIDIA GPU | Requires a GPU and driver supporting CUDA 13.x |

AMD GPUs are not supported in the packed release; use [Windows Portable](./windows-portable.md) instead.

## Extract and run

1. Download the archive; if it is split into volumes, download all parts into the same folder and extract the first volume.
2. Extract to any directory (e.g. `D:\manga-translator-ui\`); do not run from inside the archive.
3. Double-click `app.exe` to launch. The first launch loads the AI models and may take a few minutes; the main window opens after loading.
4. On the CPU build, uncheck "Use GPU" under Settings → General to avoid crashes.

## Updating

Download the new version and replace the whole directory; alternatively use the update menu of [Windows Portable](./windows-portable.md).

## Troubleshooting

- **Won't start or crashes**: confirm the archive is fully extracted; check whether antivirus blocked it; try running as administrator.
- **Missing DLL (e.g. VCRUNTIME140.dll)**: install the [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) and retry.
- **GPU build crashes**: confirm the GPU supports CUDA 13.x and the NVIDIA driver is installed or updated; switch to the CPU build if it still fails.

## Related pages

- [Windows Portable](./windows-portable.md): portable package with an install/update maintenance menu.
- [Linux and macOS](./linux-and-macos.md): Unix install script.
- [Docker](./docker.md): run the Web UI in a container.