---
title: Download a Portable Release
description: Download a Windows CPU, NVIDIA GPU, or AMD portable release with Python, dependencies, and models included
pageId: install.release-download
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Download a Portable Release

Current GitHub Releases contain a **complete Windows portable directory**, not the old standalone `app.exe` / PyInstaller package. Download every volume for the hardware variant you need, extract it, and run `Win-Start.bat`. Python, uv, PortableGit, the matching dependencies, and the models are included.

## How current release packages are built

When a version tag triggers `.github/workflows/build-and-release.yml`, the release workflow creates CPU, CUDA 13.0, CUDA 12.6, and ROCm 7.2.1 portable packages separately:

1. CI first downloads `manga-translator-ui-portable.7z` from the `portable` release. This base already contains Python 3.12, uv, and `PortableGit`; the tagged source tree is then overlaid. PortableGit is retained from the base instead of being downloaded again for every version build.
2. It exports the matching dependency group from the locked `uv.lock` and installs those packages directly into `packaging/python` inside the archive.
3. The ROCm 7.2.1 variant separately installs the Windows Radeon ROCm 7.2.1 SDK and matching PyTorch build; the `cuda13.0` dependency group uses PyTorch cu130, while `cuda12.6` uses PyTorch cu126.
4. It adds the model files and smoke-tests PyQt6, PyTorch, and ONNX Runtime.
5. It creates approximately 1990 MiB 7-Zip volumes and attaches every volume to the versioned GitHub Release.

> `packaging/build_packages.py` and the PyInstaller specs remain available for local builds, but current CPU, CUDA 13.0, CUDA 12.6, and ROCm 7.2.1 GitHub Release assets use the portable-Python workflow above. It is therefore normal for the release directory not to contain `app.exe`.

## Download and choose a variant

Open the latest version on [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases), then download every file with the prefix for your hardware:

| Release filename prefix | Best for | Runtime |
| --- | --- | --- |
| `manga-translator-cpu-vX.Y.Z.7z.*` | Any Windows x64 computer; use it without a discrete GPU or when compatibility is uncertain | CPU PyTorch / ONNX Runtime |
| `manga-translator-cuda13.0-vX.Y.Z.7z.*` | NVIDIA GPUs supporting CUDA 13.0 or newer; RTX 50-series cards must use this build | CUDA 13.0 / PyTorch cu130 |
| `manga-translator-cuda12.6-vX.Y.Z.7z.*` | NVIDIA GPUs needing the CUDA 12.6 compatibility build; drivers supporting CUDA 13.0 or newer can also run it | CUDA 12.6 / PyTorch cu126 |
| `manga-translator-rocm7.2.1-vX.Y.Z.7z.*` | AMD GPUs supported by Windows ROCm | Experimental Radeon ROCm 7.2.1; AMD driver 26.2.2 is required |

The ROCm 7.2.1 package works only with supported AMD GPUs. Use the CPU package when uncertain, and never mix volumes from different runtimes.

## Download and extract the volumes

1. Download **all** `.7z.001`, `.7z.002`, and later volumes for the selected variant into one directory without renaming them.
2. Use 7-Zip or another extractor that supports split 7z archives, and extract only `.7z.001`; the remaining volumes are read automatically.
3. Extract to a writable, short path such as `D:\manga-translator-ui\`. Do not run inside the archive or combine volumes from different versions.

Extraction fails when any volume is missing, incomplete, or renamed. Normal volumes are approximately 1990 MiB; the final volume is usually smaller.

## Start the application

1. Install the [Microsoft Visual C++ 2015–2022 Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe).
2. Open the extracted directory and double-click `Win-Start.bat`.
3. The launcher adds the bundled `PortableGit\cmd` to `PATH`, then uses `packaging\python\python.exe` to run `desktop_qt_ui\main.py`. Initial model loading can take time, but it does not reinstall the bundled Python dependencies or models.
4. Run `Win-Install-or-Update.bat` to check dependencies, synchronize source, or switch versions or branches. The maintenance program prefers the bundled `PortableGit\cmd\git.exe`.

## Update or change hardware variants

- **Update in place**: run `Win-Install-or-Update.bat` and select `[2] Update`. This requires network access and synchronizes the source and current dependencies.
- **Download a new release package**: use this to repair a damaged environment or switch between CPU, NVIDIA, and AMD. Extract to a new directory, then migrate any `config/`, `result/`, and `logs/` data you want to keep.

Do not copy another variant's `packaging/python` over the current one. The three PyTorch runtimes are mutually exclusive.

## Troubleshooting

- **There is no `app.exe`**: current releases start through `Win-Start.bat` and the bundled Python runtime; this is expected.
- **Extraction fails**: verify that every volume has the same version and hardware prefix, finished downloading, and is being extracted from `.001`.
- **The launcher exits immediately**: install the x64 Visual C++ runtime, check whether antivirus quarantined scripts, DLLs, or Python files, then run `Win-Install-or-Update.bat` to inspect the environment.
- **The NVIDIA build cannot use the GPU**: update the driver and confirm that it supports the CUDA version selected by the package; use the CPU package if the problem remains.
- **The AMD build cannot load PyTorch**: confirm that the GPU is supported by Windows ROCm 7.2.1 and install AMD driver 26.2.2; use the CPU package when unsupported.

## Related pages

- [Windows Portable](./windows-portable.md): initial installation, maintenance-menu, and update behavior of the portable base.
- [Updates and version switching](./update-and-version-switching.md): branches, tags, and dependency updates.
- [Linux and macOS](./linux-and-macos.md): Unix installation scripts.
- [Docker](./docker.md): run the Web UI in a container.
