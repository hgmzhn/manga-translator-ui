<div align="center">

<img src="doc/images/主页.png" width="500" alt="主页">

[![DeepWiki文档](https://img.shields.io/badge/DeepWiki-在线文档-blue)](https://deepwiki.com/hgmzhn/manga-translator-ui)
[![基于](https://img.shields.io/badge/基于-manga--image--translator-green)](https://github.com/zyddnys/manga-image-translator)
[![模型](https://img.shields.io/badge/模型-Real--CUGAN-orange)](https://github.com/bilibili/ailab)
[![模型](https://img.shields.io/badge/模型-YSG-orange)](https://github.com/lhj5426/YSG)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR-blue)](https://github.com/PaddlePaddle/PaddleOCR)
[![许可证](https://img.shields.io/badge/许可证-GPL--3.0-red)](LICENSE)

</div>

一键翻译漫画图片中的文字，支持日漫、韩漫、美漫，黑白漫和彩漫均可识别。自动检测、翻译、嵌字，支持日语、中文、英语等多种语言，内置可视化编辑器可调整文本框。

---

## 📚 文档导航

| 文档 | 说明 |
|------|------|
| [安装指南](doc/INSTALLATION.md) | 详细安装步骤、系统要求、分卷下载说明 |
| [使用教程](doc/USAGE.md) | 基础操作、翻译器选择、常用设置 |
| [命令行模式](doc/CLI_USAGE.md) | 命令行使用指南、参数说明、批量处理 |
| [API 配置](doc/API_CONFIG.md) | API Key 申请、配置教程 |
| [功能特性](doc/FEATURES.md) | 完整功能列表、可视化编辑器详解 |
| [工作流程](doc/WORKFLOWS.md) | 4 种工作流程、AI 断句、自定义模版 |
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

## 🚀 快速开始

### 📥 安装方式

#### 方式一：使用安装脚本（⭐ 推荐，支持更新）

> ⚠️ **无需预装 Python**：脚本会自动安装 Miniconda（轻量级 Python 环境）  
> 💡 **一键更新**：已安装用户运行 `步骤4-更新维护.bat` 即可更新到最新版本

1. **下载安装脚本**：
   - [点击下载 步骤1-首次安装.bat](https://github.com/hgmzhn/manga-translator-ui/raw/main/步骤1-首次安装.bat)
   - 保存到你想安装程序的目录（如 `D:\manga-translator-ui\`）

2. **运行安装**：
   - 双击 `步骤1-首次安装.bat`
   - 脚本会自动：
     - ✓ 检测并安装 Miniconda（如需要）
       - 提供下载源选择：清华大学镜像（国内推荐）或 Anaconda 官方
       - 自动下载安装（约 50MB）
       - 安装到项目目录，不占用C盘
     - ✓ 安装便携版 Git（如需要）
     - ✓ 克隆代码仓库
     - ✓ 创建 Conda 虚拟环境（Python 3.12）
     - ✓ 检测显卡类型（NVIDIA / AMD / 集显）
     - ✓ 自动选择对应的 PyTorch 版本
       - NVIDIA: CUDA 12.x 版本（需驱动 >= 525.60.13）
       - AMD: ROCm 版本（实验性支持，支持 RX 5000/6000/7000/9000 系列）
       - 其他: CPU 版本（通用，速度较慢）
     - ✓ 安装所有依赖

3. **启动程序**：
   - 双击 `步骤2-启动Qt界面.bat`

#### 方式二：下载打包版本

1. **下载程序**：
   - 前往 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases)
   - 选择版本：
     - **CPU 版本**：适用于所有电脑
     - **GPU 版本 (NVIDIA)**：需要支持 CUDA 12.x 的 NVIDIA 显卡
     - ⚠️ **AMD GPU 不支持打包版本**，请使用"方式一：安装脚本"安装

2. **解压运行**：
   - 解压压缩包到任意目录
   - 双击 `app.exe`

#### 方式三：手动部署（开发者）

1. **安装 Python 3.12**：[下载](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
2. **克隆仓库**：`git clone https://github.com/hgmzhn/manga-translator-ui.git`
3. **安装依赖**：
   - **NVIDIA GPU**：`py -3.12 -m pip install -r requirements_gpu.txt`
   - **AMD GPU**：`py -3.12 -m pip install -r requirements_amd.txt` 并参考文件注释安装 AMD PyTorch
   - **CPU 版本**：`py -3.12 -m pip install -r requirements_cpu.txt`
4. **运行程序**：`py -3.12 -m desktop_qt_ui.main`

**详细安装指南** → [doc/INSTALLATION.md](doc/INSTALLATION.md)

---

## 📖 使用教程

### 🖥️ Qt 界面模式

安装完成后，请查看使用教程了解如何翻译图片：

**使用教程** → [doc/USAGE.md](doc/USAGE.md)

基本步骤：
1. 填写 API（如使用在线翻译器）
2. 关闭 GPU（仅 CPU 版本）
3. 设置输出目录
4. 添加图片
5. 选择翻译器
6. 开始翻译

### ⌨️ 命令行模式

适合批量处理和自动化脚本：

**命令行指南** → [doc/CLI_USAGE.md](doc/CLI_USAGE.md)

快速开始：
```bash
# Local 模式（推荐，命令行翻译）
python -m manga_translator local -i manga.jpg

# 或简写（默认 Local 模式）
python -m manga_translator -i manga.jpg

# 翻译整个文件夹
python -m manga_translator local -i ./manga_folder/ -o ./output/

# Web API 服务器模式
python -m manga_translator web --host 127.0.0.1 --port 8000

# 查看所有参数
python -m manga_translator --help
```

---

## ✨ 核心功能

### 翻译功能

- 🔍 **智能文本检测** - 自动识别漫画中的文字区域
- 📝 **多语言 OCR** - 支持日语、中文、英语等多种语言
- 🌐 **30+ 翻译引擎** - 离线/在线翻译器任选
- 🎯 **高质量翻译** - 支持 GPT-4o、Gemini 多模态 AI 翻译
- 🎨 **智能嵌字** - 自动排版译文，支持多种字体
- 📦 **批量处理** - 一次处理整个文件夹

### 可视化编辑器

- ✏️ **区域编辑** - 移动、旋转、变形文本框
- 📐 **文本编辑** - 手动翻译、样式调整
- 🖌️ **蒙版编辑** - 画笔工具、橡皮擦
- ⏪ **撤销/重做** - 完整操作历史

**完整功能特性** → [doc/FEATURES.md](doc/FEATURES.md)

---

## 📋 工作流程

本程序支持多种工作流程：

1. **正常翻译流程** - 直接翻译图片 
2. **导出翻译** - 翻译后导出到 TXT 文件
3. **导出原文** - 仅检测识别，导出原文用于手动翻译
4. **导入翻译并渲染** - 从 TXT/JSON 导入翻译内容重新渲染

**工作流程详解** → [doc/WORKFLOWS.md](doc/WORKFLOWS.md)

---

## ⚙️ 常用翻译器

### 离线翻译器（无需网络）
- Sugoi、NLLB、M2M100、Qwen2 等

### 在线翻译器（需要 API Key）
- Google Gemini、OpenAI、DeepL、百度翻译等

### 高质量翻译器（推荐）
- **高质量翻译 OpenAI** - 使用 GPT-4o 多模态模型
- **高质量翻译 Gemini** - 使用 Gemini 多模态模型
- 📸 结合图片上下文，翻译更准确

**完整设置说明** → [doc/SETTINGS.md](doc/SETTINGS.md)

---

## 🔍 遇到问题？

### 翻译效果不理想

1. 在"基础设置"中勾选 **详细日志**
2. 查看 `result/` 目录中的调试文件
3. 调整检测器和 OCR 参数

**调试流程指南** → [doc/DEBUGGING.md](doc/DEBUGGING.md)

---

## 🙏 致谢

- [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) - 核心翻译引擎
- [bilibili/ailab](https://github.com/bilibili/ailab) - Real-CUGAN 超分辨率模型
- [lhj5426/YSG](https://github.com/lhj5426/YSG) - 提供模型支持
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - 提供 OCR 模型支持
- 所有贡献者和用户的支持

---

## 📝 许可证

本项目基于 GPL-3.0 许可证开源。
