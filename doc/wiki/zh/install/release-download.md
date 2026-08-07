---
title: 下载打包版本
description: 从 GitHub Releases 下载 CPU/GPU 打包版本，解压后直接运行，无需自行安装 Python
pageId: install.release-download
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 下载打包版本

适合不想用安装脚本、也不想自己管理 Python 环境的用户：直接从 GitHub Releases 下载已经打包好的程序，解压后双击即可运行，无需预装 Python。

## 下载与选择版本

前往 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases) 下载最新版本：

| 版本 | 适用 | 说明 |
| --- | --- | --- |
| CPU 版本 | 所有电脑 | 无需 GPU，兼容性最好 |
| GPU 版本（NVIDIA） | 拥有 NVIDIA 显卡的电脑 | 需要支持 CUDA 13.x 的显卡与驱动 |

AMD GPU 不支持打包版本，请改用[Windows 便携版](./windows-portable.md)安装。

## 解压与运行

1. 下载对应压缩包；分卷文件需全部下载到同一目录，解压第一个分卷即可。
2. 解压到任意目录（如 `D:\manga-translator-ui\`），不要直接在压缩包内运行。
3. 双击 `app.exe` 启动程序。首次运行会加载 AI 模型，可能需要几分钟；加载完成后自动打开主界面。
4. CPU 版本请到「设置 → 通用」取消勾选「使用 GPU」，否则可能崩溃。

## 更新

下载新版本压缩包并替换整个目录即可；也可以改用[Windows 便携版](./windows-portable.md)的更新菜单。

## 常见问题

- **无法启动或闪退**：确认已完整解压；检查杀毒软件是否拦截；可尝试以管理员身份运行。
- **缺少 DLL（如 VCRUNTIME140.dll）**：安装 [Microsoft Visual C++ 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe) 后重试。
- **GPU 版崩溃**：确认显卡支持 CUDA 13.x 并已安装或更新 NVIDIA 驱动；仍失败请换用 CPU 版本。

## 关联页面

- [Windows 便携版](./windows-portable.md)：带安装/更新维护菜单的便携安装包。
- [Linux 与 macOS 安装](./linux-and-macos.md)：Unix 安装脚本。
- [Docker 部署](./docker.md)：容器运行 Web 界面。