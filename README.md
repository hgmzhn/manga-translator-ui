# 漫画图片翻译器

一键翻译漫画图片中的文字，支持日语、中文、英语等多种语言，提供完整的可视化编辑功能。

基于 [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) 核心引擎开发。

---

## 📚 文档导航

| 文档 | 说明 |
|------|------|
| [安装指南](doc/INSTALLATION.md) | 详细安装步骤、系统要求、分卷下载说明 |
| [使用教程](doc/USAGE.md) | 基础操作、翻译器选择、常用设置 |
| [API 配置](doc/API_CONFIG.md) | API Key 申请、配置教程 |
| [功能特性](doc/FEATURES.md) | 完整功能列表、可视化编辑器详解 |
| [工作流程](doc/WORKFLOWS.md) | 4 种工作流程、AI 断句、自定义模版 |
| [设置说明](doc/SETTINGS.md) | 翻译器配置、OCR 模型、参数详解 |
| [调试指南](doc/DEBUGGING.md) | 调试流程、可调节参数、问题排查 |
| [开发者指南](doc/DEVELOPMENT.md) | 项目结构、环境配置、构建打包 |

---

## 🚀 快速开始

### 📥 安装方式

#### 方式一：使用安装脚本（⭐ 推荐，支持自动更新）

1. **安装 Python 3.12**：
   - [点击下载 Python 3.12.10](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
   - ⚠️ 安装时**必须勾选** "Add Python to PATH"

2. **下载安装脚本**：
   - [点击下载 步骤1-首次安装.bat](https://github.com/hgmzhn/manga-translator-ui/raw/main/步骤1-首次安装.bat)
   - 保存到你想安装程序的目录（如 `D:\manga-translator-ui\`）

3. **运行安装**：
   - 双击 `步骤1-首次安装.bat`
   - 脚本会自动：
     - ✓ 安装便携版 Git（可选）
     - ✓ 克隆代码仓库
     - ✓ 创建虚拟环境
     - ✓ 安装所有依赖
     - ✓ 检测并配置 GPU/CPU

4. **启动程序**：
   - 双击 `步骤2-启动Qt界面.bat`

#### 方式二：下载打包版本

1. **下载程序**：
   - 前往 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases)
   - 选择版本：
     - **CPU 版本**：适用于所有电脑
     - **NVIDIA GPU 版本**：需要支持 CUDA 12.x 的 NVIDIA 显卡
     - **AMD ROCm 版本**：需要 AMD Radeon RX 9000 系列（gfx1201）或更新显卡

2. **解压运行**：
   - 解压压缩包到任意目录
   - 双击 `app.exe`

#### 方式三：手动部署（开发者）

1. **安装 Python 3.12**：[下载](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
2. **克隆仓库**：`git clone https://github.com/hgmzhn/manga-translator-ui.git`
3. **安装依赖**：
   - **NVIDIA GPU 版本**：`py -3.12 -m pip install -r requirements.txt`
   - **AMD ROCm 版本**：`py -3.12 -m pip install -r requirements_rocm.txt`
   - **CPU 版本**：`py -3.12 -m pip install -r requirements_cpu.txt`
4. **运行程序**：
   - **NVIDIA/CPU**：`py -3.12 -m desktop_qt_ui.main`
   - **AMD ROCm**：运行 `start-rocm.bat` 或参考 [ROCm 部署指南](部署指南-gfx1201.md)

**详细安装指南** → [doc/INSTALLATION.md](doc/INSTALLATION.md)

---

## 📖 使用教程

安装完成后，请查看使用教程了解如何翻译图片：

**使用教程** → [doc/USAGE.md](doc/USAGE.md)

基本步骤：
1. 填写 API（如使用在线翻译器）
2. 关闭 GPU（仅 CPU 版本）
3. 设置输出目录
4. 添加图片
5. 选择翻译器
6. 开始翻译

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
- [lhj5426/YSG](https://github.com/lhj5426/YSG) - 提供模型支持
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - 提供 OCR 模型支持
- 所有贡献者和用户的支持

---

## 📝 许可证

本项目基于 GPL-3.0 许可证开源。
