---
title: 下载发布便携包
description: 下载已内置 Python、依赖和模型的 CPU、NVIDIA GPU 或 AMD Windows 发布便携包
pageId: install.release-download
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 下载发布便携包

当前 GitHub Release 提供的是**完整 Windows 便携目录**，不是旧版 `app.exe` / PyInstaller 单文件。下载对应硬件版本的全部分卷，解压后运行 `Win-Start.bat`；包内自带 Python、uv、PortableGit、对应依赖和模型，不需要预装这些工具。

## 当前发布包如何生成

版本标签触发 `.github/workflows/build-and-release.yml` 后，发布流程会分别构建 CPU、NVIDIA GPU 和 AMD 三种便携包：

1. CI 先从 `portable` 标签下载 `manga-translator-ui-portable.7z`；这个便携基础包已包含 Python 3.12、uv 和 `PortableGit`，随后再覆盖为当前标签的代码。`PortableGit` 会从基础包保留下来，不会在每次版本构建时重复下载。
2. 从已锁定的 `uv.lock` 导出对应依赖组，把依赖直接安装进包内的 `packaging/python`。
3. AMD 包单独安装 Windows Radeon ROCm 7.2.1 SDK 和配套 PyTorch；NVIDIA 包使用 CUDA 12.6 源。
4. 把模型文件放进包内，执行 PyQt6、PyTorch、ONNX Runtime 冒烟检查。
5. 使用 7-Zip 按约 1990 MiB 分卷，上传全部分卷到对应版本的 GitHub Release。

> 仓库中的 `packaging/build_packages.py` 和 PyInstaller spec 仍可用于本地构建，但当前 GitHub Release 的 CPU/GPU/AMD 下载附件走的是上述“便携 Python + 已安装依赖”流程，所以发布包里没有 `app.exe` 属于正常现象。

## 下载与选择版本

前往 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases) 打开最新版本，按硬件选择同一前缀的全部文件：

| 发布文件前缀 | 适用设备 | 运行时 |
| --- | --- | --- |
| `manga-translator-cpu-vX.Y.Z.7z.*` | 所有 Windows x64 电脑；无独显或不确定兼容性时选它 | CPU PyTorch / ONNX Runtime |
| `manga-translator-gpu-vX.Y.Z.7z.*` | NVIDIA 显卡 | CUDA 12.6；显卡驱动必须支持 CUDA 12.6 |
| `manga-translator-amd-vX.Y.Z.7z.*` | 受 Windows ROCm 支持的 AMD 显卡 | 实验性 Radeon ROCm 7.2.1；要求 AMD 26.2.2 驱动 |

AMD 包只适用于 Radeon ROCm 7.2.1 支持范围内的显卡。无法确认兼容性时先使用 CPU 包；不要把 CPU、NVIDIA 和 AMD 分卷混在一起。

## 下载和解压分卷

1. 下载所选版本的 `.7z.001`、`.7z.002` 等**全部分卷**，放在同一目录并保持原文件名。
2. 使用 7-Zip 或支持 7z 分卷的解压软件，只对 `.7z.001` 执行解压；后续分卷会自动读取。
3. 解压到可写的短路径，例如 `D:\manga-translator-ui\`。不要直接在压缩包内运行，也不要混用不同版本的分卷。

缺少任何一个分卷、下载不完整或文件被重命名，都会导致解压报错。每个普通分卷约 1990 MiB，最后一个分卷通常更小。

## 启动

1. 先安装 [Microsoft Visual C++ 2015–2022 运行库（x64）](https://aka.ms/vs/17/release/vc_redist.x64.exe)。
2. 进入解压后的目录，双击 `Win-Start.bat`。
3. 脚本会先把包内的 `PortableGit\cmd` 加入 `PATH`，再使用 `packaging\python\python.exe` 启动 `desktop_qt_ui\main.py`。首次加载模型可能较慢，但不会重新下载整套 Python 依赖和已随包提供的模型。
4. 需要检查依赖、同步代码、切换版本或分支时，运行 `Win-Install-or-Update.bat`；维护程序会优先使用包内的 `PortableGit\cmd\git.exe`。

## 更新或更换硬件版本

- **原目录更新**：运行 `Win-Install-or-Update.bat`，选择 `[2] 更新`；此方式需要网络，会同步代码并检查当前依赖。
- **下载新发布包**：适合修复损坏环境，或在 CPU、NVIDIA、AMD 之间切换。建议解压到新目录，再从旧目录迁移需要保留的 `config/`、`result/` 和 `logs/`。

切换硬件版本时不要把另一版本的 `packaging/python` 直接覆盖进当前目录；三个版本的 PyTorch 运行时互斥。

## 常见问题

- **目录里没有 `app.exe`**：当前发布包通过 `Win-Start.bat` 和包内 Python 启动，这是预期结构。
- **无法解压**：确认同一版本、同一硬件前缀的所有分卷都已下载完成，并从 `.001` 开始解压。
- **双击后闪退**：先安装 x64 Visual C++ 运行库，确认杀毒软件没有隔离脚本、DLL 或 Python 文件，再运行 `Win-Install-or-Update.bat` 检查环境。
- **NVIDIA 版无法加载 GPU**：更新显卡驱动并确认驱动支持 CUDA 12.6；仍失败时改用 CPU 包。
- **AMD 版无法加载 PyTorch**：确认显卡在 Windows ROCm 7.2.1 支持范围内并安装 AMD 26.2.2 驱动；不兼容时改用 CPU 包。

## 关联页面

- [Windows 便携版](./windows-portable.md)：便携基础包的首次安装、维护菜单和更新流程。
- [更新与版本切换](./update-and-version-switching.md)：分支、标签和依赖更新说明。
- [Linux 与 macOS 安装](./linux-and-macos.md)：Unix 安装脚本。
- [Docker 部署](./docker.md)：容器运行 Web 界面。
