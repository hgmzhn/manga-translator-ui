<div align="center">

<img src="doc/images/主页.png" width="500" alt="主页">

[![DeepWiki文档](https://img.shields.io/badge/DeepWiki-%E5%9C%A8%E7%BA%BF%E6%96%87%E6%A1%A3-blue)](https://deepwiki.com/hgmzhn/manga-translator-ui)
[![基于](https://img.shields.io/badge/%E5%9F%BA%E4%BA%8E-manga--image--translator-green)](https://github.com/zyddnys/manga-image-translator)
[![模型](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B-Real--CUGAN-orange)](https://github.com/bilibili/ailab)
[![模型](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B-MangaJaNai-orange)](https://github.com/the-database/MangaJaNai)
[![模型](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B-YSG-orange)](https://github.com/lhj5426/YSG)
[![模型](https://img.shields.io/badge/Model-MangaLens%20Bubble%20Segmentation-orange?logo=huggingface)](https://huggingface.co/huyvux3005/manga109-segmentation-bubble)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR-blue)](https://github.com/PaddlePaddle/PaddleOCR)
[![OCR](https://img.shields.io/badge/OCR-MangaOCR-blue)](https://github.com/kha-white/manga-ocr)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR--VL--1.5-blue)](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5)
[![许可证](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF%E8%AF%81-GPL--3.0-red)](LICENSE)

</div>


**语言 / Language**: 简体中文 | [English](README_EN.md)

一键翻译漫画图片中的文字，支持日漫、韩漫、美漫，黑白漫和彩漫均可识别。自动检测、翻译、嵌字，支持日语、中文、英语等多种语言，内置可视化编辑器可调整文本框。

**💬 QQ 交流群：1079089991（密码：kP9#mB2!vR5*sL1）** | **🐛 [提交 Issue](https://github.com/hgmzhn/manga-translator-ui/issues)**

---

## 📚 文档导航

| 文档 | 说明 |
|------|------|
| [安装指南](doc/INSTALLATION.md) | 详细安装步骤、系统要求、分卷下载说明 |
| [使用教程](doc/USAGE.md) | 基础操作、翻译器选择、常用设置 |
| [命令行模式](doc/CLI_USAGE.md) | 命令行使用指南、参数说明、批量处理 |
| [API 配置](doc/API_CONFIG.md) | API Key 申请、配置教程 |
| [功能特性](doc/FEATURES.md) | 完整功能列表、可视化编辑器详解 |
| [工作流程](doc/WORKFLOWS.md) | 7 种工作流程、AI 断句、自定义模版 |
| [设置说明](doc/SETTINGS.md) | 翻译器配置、OCR 模型、参数详解 |
| [调试指南](doc/DEBUGGING.md) | 调试流程、可调节参数、问题排查 |
| [开发者指南](doc/DEVELOPMENT.md) | 项目结构、环境配置、构建打包 |

---

## 📸 效果展示

<div align="center">

<table>
<tr>
<td align="center"><b>翻译前</b></td>
<td align="center"><b>翻译后</b></td>
</tr>
<tr>
<td><img src="doc/images/0012.png" width="400" alt="翻译前"></td>
<td><img src="doc/images/110012.png" width="400" alt="翻译后"></td>
</tr>
</table>

</div>

---

## ✨ 核心功能

### 翻译功能

- 🔍 **智能文本检测** - 自动识别漫画中的文字区域
- 📝 **多语言 OCR** - 支持日语、中文、英语等多种语言
- 🌐 **多种翻译引擎** - OpenAI、Gemini、Vertex、Sakura（含高质量模式）
- 🎯 **高质量翻译** - 支持 GPT-4o、Gemini 多模态 AI 翻译
- 📚 **自动提取术语** - AI 自动识别并积累专有名词，保持翻译一致性
- 🤖 **AI 智能断句** - 提升文本可读性，自动优化换行
- 🎨 **智能嵌字** - 自动排版译文，支持多种字体
- 📥 **PSD 导出** - 导出可编辑的 PSD 文件（原图/修复图/文本分层）
- 📦 **批量处理** - 一次处理整个文件夹

### 可视化编辑器

- ✏️ **区域编辑** - 移动、旋转、变形文本框
- 📐 **文本编辑** - 手动翻译、样式调整
- 🖌️ **蒙版编辑** - 画笔工具、橡皮擦
- ⏪ **撤销/重做** - 完整操作历史
- ⌨️ **快捷键支持** - 支持 A/D 切换图片，Q/W/E 切换工具，Ctrl+A 全选文本框，Ctrl+Q 导出图片
- 🖱️ **鼠标滚轮快捷键** - Ctrl+滚轮缩放文本框，Shift+滚轮调整画笔大小

**完整功能特性** → [doc/FEATURES.md](doc/FEATURES.md)

---

## 🚀 快速开始

### 📥 安装方式

#### 方式一：便携安装包（⭐ 推荐，支持更新）

> ⚠️ **无需预装 Python**：安装包内自带便携版 Python 3.12 和 uv 包管理器，完全绿色，不写注册表  
> 💡 **一键更新**：运行 `Win-Install-or-Update.bat` 选择 [2] 更新，即可更新到最新版本

1. **下载便携安装包**：
   - 前往 [便携整合包发布页](https://github.com/hgmzhn/manga-translator-ui/releases/tag/portable) 下载最新版本
   - 解压到任意目录（如 `D:\manga-translator-ui\`）

2. **运行安装**：
   - 双击 `Win-Install-or-Update.bat`，选择 `[1] 安装`
   - 脚本会自动：
     - ✓ 选择下载线路（GitHub 官方 / Gitee 国内镜像），同步失败自动提示换线路
     - ✓ 同步最新代码
     - ✓ 检测显卡类型（NVIDIA / AMD / 集显）
     - ✓ 自动选择对应的 PyTorch 版本
       - NVIDIA: CUDA 13.x 版本（驱动需支持 CUDA 13.0）
       - AMD: ROCm 版本（实验性支持，**仅支持 RX 7000/9000 系列**，RX 5000/6000 请使用 CPU 版本）
       - 其他: CPU 版本（通用，速度较慢）
     - ✓ 使用 uv 高速批量安装所有依赖（支持多镜像源回退）
     - ✓ 安装完成后自动清理下载缓存
   - 维护菜单还支持：切换分支（main/beta）、按 tag 切换版本、切换镜像源、中英文界面切换

3. **启动程序**：
   - 双击 `Win-Start.bat`

4. **卸载**：
   - 新版：直接删除整个文件夹即可
   - 旧版：参考 [卸载指南](doc/UNINSTALL.md)

#### 方式二：下载打包版本

1. **下载程序**：
   - 前往 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases)
   - 选择版本：
     - **CPU 版本**：适用于所有电脑
     - **GPU 版本 (NVIDIA)**：需要支持 CUDA 13.x 的 NVIDIA 显卡
     - ⚠️ **AMD GPU 不支持打包版本**，请使用"方式一：安装脚本"安装

2. **解压运行**：
   - 解压压缩包到任意目录
   - 双击 `app.exe`

#### 方式三：Docker 部署（实验性）

> 💡 **说明**：下面的命令适合临时体验。正式部署 Web UI 时，建议按下方“Web UI 持久化”说明挂载数据目录和配置文件。

**快速启动**：
```bash
# Windows CMD / PowerShell
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu

# Linux / macOS
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

**镜像仓库**：

本项目的 Docker 镜像同时发布在两个镜像仓库，选择下载速度更快的即可：

- **Docker Hub**（推荐）：
  - CPU 版本：`hgmzhn/manga-translator:latest-cpu`
  - GPU 版本：`hgmzhn/manga-translator:latest-gpu`

- **GitHub Container Registry**（备用，国内可能更快）：
  - CPU 版本：`ghcr.io/hgmzhn/manga-translator:latest-cpu`
  - GPU 版本：`ghcr.io/hgmzhn/manga-translator:latest-gpu`

**访问地址**（默认端口 8000）：
- 🌐 用户界面：`http://localhost:8000`
- 🔧 管理界面：`http://localhost:8000/admin`

**Web UI 持久化（推荐）**：

- 必挂目录：`/app/manga_translator/server/data`、`/app/config`、`/app/dict`、`/app/fonts`、`/app/models`
- 可选目录：`/app/logs`、`/app/result`
- 可选文件：`/app/.env`。如果你准备在 Web 管理界面里保存服务器 API Keys，必须额外挂这个文件
- `server/data` 现在会统一保存 `admin_config.json`、`user_resources/`、账号、会话、用户组、权限、配额、API Key 预设、用户配置和翻译历史元数据

> 📖 **详细安装教程**：[Docker 部署文档](doc/INSTALLATION.md#安装方式四docker部署)  
> 📖 **使用教程**：[命令行使用指南](doc/CLI_USAGE.md)

#### 方式四：从源码运行（开发者）

适合开发者或想要自定义的用户。

1. **安装 Python 3.12**：[下载](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
2. **克隆仓库**：
   ```bash
   git clone https://github.com/hgmzhn/manga-translator-ui.git
   cd manga-translator-ui
   ```
3. **安装依赖**（按硬件只执行对应的一组命令）：
   ```bash
   python -m pip install uv

   # NVIDIA GPU（CUDA 13.0，默认）
   uv sync

   # CPU 版本（与上面的 GPU 命令二选一）
   uv sync --no-default-groups --group cpu

   # AMD GPU（实验性；Linux 使用 ROCm 7.2 索引）
   uv sync --no-default-groups --group amd

   # Apple Silicon / Metal
   uv sync --no-default-groups --group metal
   ```
4. **运行程序**：
   ```bash
   # 桌面 UI
   uv run --no-sync python -m desktop_qt_ui.main
   
   # Web UI（可选）
   uv run --no-sync python -m manga_translator web
   ```

> 📖 **详细安装教程**：[安装指南](doc/INSTALLATION.md)  
> 📖 **使用教程**：[命令行使用指南](doc/CLI_USAGE.md)

#### 方式五：Linux/macOS

Linux 和 macOS 共用同一套安装脚本。Apple Silicon 会使用 Metal/MPS；Linux 会自动选择 NVIDIA、AMD ROCm 或 CPU 依赖组。

**快速安装**：

```bash
mkdir -p ~/manga-translator-ui
cd ~/manga-translator-ui

curl -L -O https://raw.githubusercontent.com/hgmzhn/manga-translator-ui/main/Unix-Install-or-Update.sh
chmod +x Unix-Install-or-Update.sh
./Unix-Install-or-Update.sh
```

> 安装脚本启动时只询问一次 `Start installation now? [Y/n]`。确认后会检查 Git，使用 `uv` 安装 Python 3.12，在项目目录创建 `.venv`，然后直接进入双语 Python 安装/更新菜单。没有项目代码时会自动克隆仓库。

**已有仓库时安装依赖**：

```bash
cd manga-translator-ui
chmod +x Unix-*.sh
./Unix-Install-or-Update.sh
```

**启动程序**：

```bash
./Unix-Start.sh
```

**更新代码和依赖**：

```bash
./Unix-Install-or-Update.sh
# 在 Python 菜单中选择 [2] Update
```



---

## 📖 使用教程

### 🖥️ Qt 界面模式

安装完成后，请查看使用教程了解如何翻译图片：

**使用教程** → [doc/USAGE.md](doc/USAGE.md)

基本步骤：
1. 填写 API（如使用在线翻译器）→ [API 配置教程](doc/API_CONFIG.md)
2. 关闭 GPU（仅 CPU 版本）
3. 设置输出目录
4. 添加图片
5. 选择翻译器
   - 首次使用推荐：**高质量翻译 OpenAI** 或 **高质量翻译 Gemini**
   - 如需单独管理 Google 官方 Key，也可使用 **高质量翻译 Vertex**
   - 需要配置 API Key，参考 [API 配置教程](doc/API_CONFIG.md)
6. 开始翻译

### ⌨️ 命令行模式

适合批量处理和自动化脚本：

**命令行指南** → [doc/CLI_USAGE.md](doc/CLI_USAGE.md)

> ⚠️ **重要提示**：使用命令行前，请先在项目目录激活虚拟环境：
> ```bash
> # Windows
> conda activate manga-env
> 
> # Linux/macOS
> source .venv/bin/activate
> ```

快速开始：
```bash
# Local 模式（推荐，命令行翻译）
python -m manga_translator local -i manga.jpg

# 或简写（默认 Local 模式）
python -m manga_translator -i manga.jpg

# 翻译整个文件夹
python -m manga_translator local -i ./manga_folder/ -o ./output/

# Web 服务器模式（带管理界面和 API）
python -m manga_translator web --host 127.0.0.1 --port 8000 --use-gpu

# 查看所有参数
python -m manga_translator --help
```

---

## 📋 工作流程

本程序支持多种工作流程：

1. **正常翻译流程** - 直接翻译图片 
2. **导出翻译** - 翻译后按模板配置的扩展名导出文本（默认 JSON）
3. **导出原文** - 仅检测识别，导出原文用于手动翻译
4. **导入翻译并渲染** - 从 TXT/JSON 导入翻译内容重新渲染

**工作流程详解** → [doc/WORKFLOWS.md](doc/WORKFLOWS.md)

---

## ⚙️ 常用翻译器

### 在线翻译器（需要 API Key）
- **OpenAI** - 使用 GPT 系列模型
- **Gemini** - 使用 Google Gemini 模型
- **Sakura** - 专门针对日语优化的翻译模型
- Google Cloud / Vertex 相关 API Key 也直接填写到 `Gemini` 配置即可；`Base URL` 保持默认官方地址，无需修改

### 高质量翻译器（推荐）
- **高质量翻译 OpenAI** - 使用 GPT-4o 多模态模型
- **高质量翻译 Gemini** - 使用 Gemini 多模态模型
- 📸 结合图片上下文，翻译更准确

### AI 识别 / 上色 / 渲染（可选）
- **AI OCR** - 支持 `openai_ocr`、`gemini_ocr`
- **AI 上色** - 支持 `openai_colorizer`、`gemini_colorizer`
- **AI 渲染** - 支持 `openai_renderer`、`gemini_renderer`
- 桌面端可在 `API 管理` 的 `OCR` / `上色` / `渲染` 标签分别配置
- Web 管理端也同步提供对应的 Gemini / OpenAI API Key 分组；Google Cloud / Vertex 相关 API Key 同样直接填 Gemini 分组即可，`Base URL` 无需修改，详情见 [API 配置教程](doc/API_CONFIG.md) 与 [设置说明](doc/SETTINGS.md)

**完整设置说明** → [doc/SETTINGS.md](doc/SETTINGS.md)

---

## 🔍 遇到问题？

### 翻译效果不理想

1. 在"设置" -> "通用"中勾选 **详细日志**
2. 查看 `result/` 目录中的调试文件
3. 调整检测器和 OCR 参数
4. 排查完成后按文档清理旧日志（避免 `result/` 目录过大）

**调试流程指南** → [doc/DEBUGGING.md](doc/DEBUGGING.md)

---

## ⭐ Star 趋势

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=hgmzhn/manga-translator-ui&type=Date)](https://star-history.com/#hgmzhn/manga-translator-ui&Date)

</div>

---

## 🙏 致谢

- [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) - 核心翻译引擎
- [bilibili/ailab](https://github.com/bilibili/ailab) - Real-CUGAN 超分辨率模型
- [the-database/MangaJaNai](https://github.com/the-database/MangaJaNai) - MangaJaNai/IllustrationJaNai 超分辨率模型
- [lhj5426/YSG](https://github.com/lhj5426/YSG) - 提供模型支持
- [huyvux3005/manga109-segmentation-bubble](https://huggingface.co/huyvux3005/manga109-segmentation-bubble) - MangaLens Bubble Segmentation 气泡分割模型
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - 提供 OCR 模型支持
- [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) - MangaOCR 模型支持
- [PaddlePaddle/PaddleOCR-VL-1.5](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) - 官方 PaddleOCR-VL-1.5 模型页
- 所有贡献者和用户的支持

---

## ❤️ 支持作者

如果这个项目对你有帮助，欢迎请作者喝杯奶茶 🧋

<div align="center">

<table style="border: none;">
<tr>
<td align="center" style="padding: 10px;">
<div style="border: 2px solid #e0e0e0; border-radius: 10px; padding: 15px; background: #f9f9f9; display: inline-block;">
  <div style="background: white; padding: 10px; border-radius: 8px;">
    <img src="doc/images/mm_reward_qrcode_1765200960689.png" width="180" alt="微信赞赏码">
  </div>
  <p style="margin: 8px 0 0 0; font-size: 14px; color: #666;">💚 微信赞赏</p>
</div>
</td>
<td width="30"></td>
<td align="center" style="padding: 10px;">
<div style="border: 2px solid #e0e0e0; border-radius: 10px; padding: 15px; background: #f9f9f9; display: inline-block;">
  <div style="background: white; padding: 10px; border-radius: 8px;">
    <img src="doc/images/IMG_20251223_173711.jpg" width="180" alt="支付宝赞助码">
  </div>
  <p style="margin: 8px 0 0 0; font-size: 14px; color: #666;">💙 支付宝赞助</p>
</div>
</td>
</tr>
</table>

<p style="font-size: 13px; color: #888; margin-top: 10px;">感谢你的支持 ✨</p>

</div>

---

## 📝 许可证

本项目基于 GPL-3.0 许可证开源。

### 模型协议声明

本项目代码采用 **GPL-3.0 协议**。

本项目支持使用 MangaJaNai/IllustrationJaNai 模型进行图像超分辨率处理。这些模型权重文件采用 **CC BY-NC 4.0 协议**（署名-非商业性使用 4.0 国际），仅供非商业用途使用。

- **模型来源**：[MangaJaNai](https://github.com/the-database/MangaJaNai)
- **模型协议**：CC BY-NC 4.0
- **使用限制**：仅限非商业用途

---

## ⚠️ 特别声明

本项目仅提供技术演示与个人学习交流用途，不构成任何法律、商业或合规建议。  
你在安装、配置、调用和分发本项目相关功能时，应自行确认并持续遵守所在地法律法规、平台规则、内容来源许可及第三方服务条款。

### 免责与责任限制

- 使用本项目产生的一切行为与后果（包括但不限于内容处理、发布、传播、二次分发、商业化使用），均由使用者独立承担责任。
- 你应自行确保输入内容、输出内容及数据来源具备合法授权，不得用于侵犯著作权、商标权、隐私权、肖像权等合法权益的场景。
- 严禁将本项目用于任何违法违规用途，包括但不限于盗版传播、未授权批量抓取与搬运、绕过平台限制、诈骗、诽谤、侵害他人合法权益等行为。
- 本项目依赖第三方模型、API、数据与库（含 OCR、翻译、超分模型等）；相关可用性、准确性、稳定性、费用、风控与合规要求由对应服务方负责，使用者需自行承担相应风险与成本。
- 对于因使用或无法使用本项目导致的任何直接或间接损失（包括但不限于数据损失、业务中断、收益损失、账户风险、第三方索赔等），项目作者与贡献者在适用法律允许范围内不承担责任。
- 若你将本项目用于团队或组织环境，应自行完成权限管理、日志审计、内容审核与合规评估，并建立必要的人工复核流程。

请在使用前审慎评估风险；继续使用即视为你已阅读、理解并同意上述声明。

---
