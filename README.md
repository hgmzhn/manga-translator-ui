<div align="center">

<img src="doc/images/icon.png" width="112" alt="Manga Translator UI 图标">

# Manga Translator UI

**从文字识别、翻译到修复与嵌字，一站式处理漫画图片。**

支持日漫、韩漫、美漫、黑白漫和彩漫，提供桌面端、Web UI 和命令行三种使用方式。

<p>
  <a href="https://hgmzhn.github.io/manga-translator-ui/"><strong>📖 在线 Wiki</strong></a>
  ·
  <a href="https://github.com/hgmzhn/manga-translator-ui/releases"><strong>📦 下载发布版</strong></a>
  ·
  <a href="https://github.com/hgmzhn/manga-translator-ui/issues"><strong>🐛 提交 Issue</strong></a>
</p>

[![Wiki](https://img.shields.io/badge/Wiki-在线文档-2f80ed?logo=readthedocs&logoColor=white)](https://hgmzhn.github.io/manga-translator-ui/)
[![Release](https://img.shields.io/github/v/release/hgmzhn/manga-translator-ui?display_name=tag&sort=semver)](https://github.com/hgmzhn/manga-translator-ui/releases)
[![Python](https://img.shields.io/badge/Python-3.12-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-GPL--3.0-red)](LICENSE.txt)

<br>
<img src="doc/images/主页.png" width="760" alt="Manga Translator UI 主界面">

</div>

**语言 / Language**：简体中文 | [English](README_EN.md)

> **重要提示**：如果公开分享本工具生成的机翻结果，且没有经验丰富的译者完整翻译或校对，请在显眼位置注明“机器翻译”。

**💬 QQ 交流群：1079089991（密码：`kP9#mB2!vR5*sL1`）**

## 项目简介

Manga Translator UI 基于 [manga-image-translator](https://github.com/zyddnys/manga-image-translator) 构建，并整合了 OCR、翻译、图像修复、超分辨率和排版能力。它既可以一键批量处理漫画，也可以在可视化编辑器中逐个调整文本框、蒙版和译文。

安装、配置、使用、排障和开发说明已集中维护在 **[在线 Wiki](https://hgmzhn.github.io/manga-translator-ui/)**，README 只保留项目概览和启动入口。

## 效果展示

<div align="center">

| 翻译前 | 翻译后 |
| :---: | :---: |
| <img src="doc/images/0012.png" width="360" alt="翻译前示例"> | <img src="doc/images/110012.png" width="360" alt="翻译后示例"> |

</div>

## 功能亮点

| 能力 | 说明 |
| --- | --- |
| **自动翻译** | 自动检测文字、OCR、翻译、擦除原文、修复背景并完成嵌字 |
| **多语言与多引擎** | 支持日语、中文、英语等语言，以及 OpenAI、Gemini、Vertex、Sakura 等翻译服务 |
| **高质量翻译** | 使用多模态模型结合整页图片上下文，提升长句、气泡和专有名词的翻译质量 |
| **可视化编辑器** | 移动、旋转、变形文本框，编辑富文本，调整字体与排版，绘制或修改蒙版 |
| **批量与工作流** | 支持文件夹批处理、导入/导出翻译文本、仅 OCR、仅上色、仅修复和仅渲染 |
| **工程化能力** | 支持 PSD 分层导出、CLI、HTTP API、WebSocket 和 Web 管理界面 |

## 快速开始

Windows 首次运行前，请先安装 [Microsoft Visual C++ 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

### Windows 便携版（推荐）

便携包自带 Python 3.12 和 `uv`，不需要单独配置 Python 环境：

1. 从 [便携整合包发布页](https://github.com/hgmzhn/manga-translator-ui/releases/tag/portable) 下载最新版本并解压。
2. 双击 `Win-Install-or-Update.bat`，选择 `[1] 安装`。
3. 安装完成后双击 `Win-Start.bat` 启动。

安装脚本会根据显卡自动选择 NVIDIA、AMD 或 CPU 依赖，也支持更新、切换版本、切换镜像源和中英文界面。

### 其他运行方式

<details>
<summary><strong>Windows 打包版</strong></summary>

从 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases) 下载 CPU 或 NVIDIA GPU 版本，解压后双击 `app.exe`。AMD 用户请使用上面的便携安装脚本。

</details>

<details>
<summary><strong>Linux / macOS</strong></summary>

```bash
mkdir -p ~/manga-translator-ui
cd ~/manga-translator-ui
curl -L -O https://raw.githubusercontent.com/hgmzhn/manga-translator-ui/main/Unix-Install-or-Update.sh
chmod +x Unix-Install-or-Update.sh
./Unix-Install-or-Update.sh
```

安装完成后运行 `./Unix-Start.sh` 启动。脚本会根据平台选择 NVIDIA、AMD ROCm、Apple Metal 或 CPU 依赖。

</details>

<details>
<summary><strong>Docker（实验性）</strong></summary>

```bash
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

浏览器访问 `http://localhost:8000`；管理界面为 `http://localhost:8000/admin`。正式部署时请按照 [Wiki](https://hgmzhn.github.io/manga-translator-ui/) 挂载数据目录和配置文件。

</details>

<details>
<summary><strong>从源码运行</strong></summary>

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/hgmzhn/manga-translator-ui.git
cd manga-translator-ui

# NVIDIA GPU（默认）
uv sync

# CPU / AMD / Apple Silicon：按需选择一个依赖组
# uv sync --no-default-groups --group cpu
# uv sync --no-default-groups --group amd
# uv sync --no-default-groups --group metal

# 启动桌面端
uv run --no-sync python -m desktop_qt_ui.main

# 启动 Web UI
uv run --no-sync python -m manga_translator web
```

</details>

## 基本使用

1. 打开设置，选择源语言、目标语言和翻译器，并配置所需的 API Key。
2. 设置输出目录，导入一张图片或一个漫画文件夹。
3. 选择翻译模式后开始处理，等待检测、翻译、修复和嵌字完成。
4. 在编辑器中检查文本框、字体、颜色和排版，必要时手动调整。
5. 导出图片、文本或 PSD 分层文件。

首次使用、API 配置、快捷键、工作流、Web 管理和排障请查看 **[在线 Wiki](https://hgmzhn.github.io/manga-translator-ui/)**。

### 命令行示例

```bash
# 翻译单张图片
uv run --no-sync python -m manga_translator local -i manga.jpg

# 批量翻译文件夹
uv run --no-sync python -m manga_translator local -i ./manga_folder/ -o ./output/

# 启动 Web 服务器
uv run --no-sync python -m manga_translator web --host 127.0.0.1 --port 8000 --use-gpu

# 查看全部参数
uv run --no-sync python -m manga_translator --help
```

## 支持的模型与服务

- **翻译**：OpenAI、Gemini、Vertex、Sakura，以及兼容 OpenAI API 的服务
- **高质量翻译**：OpenAI 和 Gemini 多模态翻译
- **OCR**：PaddleOCR、MangaOCR、PaddleOCR-VL，以及 OpenAI/Gemini OCR
- **图像处理**：AI 上色、AI 渲染、文本擦除、图像修复和超分辨率

## Star 趋势

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=hgmzhn/manga-translator-ui&type=Date)](https://star-history.com/#hgmzhn/manga-translator-ui&Date)

</div>

## 致谢

本项目的实现离不开以下开源项目和模型：

- [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) - 核心翻译引擎
- [charlespfan/mtu-json-gui](https://github.com/charlespfan/mtu-json-gui) - 富文本编辑参考
- [dmMaze/BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) - 文本渲染参考
- [bilibili/ailab](https://github.com/bilibili/ailab) - Real-CUGAN 超分辨率模型
- [the-database/MangaJaNai](https://github.com/the-database/MangaJaNai) - MangaJaNai / IllustrationJaNai 超分辨率模型
- [lhj5426/YSG](https://github.com/lhj5426/YSG) - 模型支持
- [MangaLens Bubble Segmentation](https://huggingface.co/huyvux3005/manga109-segmentation-bubble) - 气泡分割模型
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - OCR 模型支持
- [manga-ocr](https://github.com/kha-white/manga-ocr) - 日文 OCR 模型支持
- [PaddleOCR-VL-1.5](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) - 多模态 OCR 模型
- 所有贡献者和用户的支持

## 支持作者

如果这个项目对你有帮助，欢迎请作者喝杯奶茶。

<div align="center">

| 微信赞赏 | 支付宝赞助 |
| :---: | :---: |
| <img src="doc/images/mm_reward_qrcode_1765200960689.png" width="180" alt="微信赞赏码"> | <img src="doc/images/IMG_20251223_173711.jpg" width="180" alt="支付宝赞助码"> |

</div>

## 许可证

本项目代码基于 [GPL-3.0](LICENSE.txt) 许可证开源。

项目支持使用 [MangaJaNai / IllustrationJaNai](https://github.com/the-database/MangaJaNai) 模型进行图像超分辨率处理；相关模型权重采用 **CC BY-NC 4.0** 许可证，仅限非商业用途。使用模型时请同时遵守其上游许可证和使用限制。

## 免责声明

本项目仅供技术演示、个人学习和交流使用，不构成法律、商业或合规建议。使用者应自行确认输入内容、输出内容和数据来源具备合法授权，并持续遵守所在地法律法规、平台规则、内容许可及第三方服务条款。

- 不得将本项目用于盗版传播、未授权批量抓取或搬运、绕过平台限制、诈骗、诽谤等违法违规用途。
- 本项目依赖第三方模型、API、数据集和库，其可用性、准确性、稳定性、费用与合规要求由对应服务方负责。
- 因使用或无法使用本项目造成的直接或间接损失，由使用者自行承担；团队或组织使用时还应自行完成权限管理、日志审计、内容审核和合规评估。

继续使用本项目即表示你已阅读、理解并同意上述声明。
