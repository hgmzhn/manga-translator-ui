---
title: 第一次翻译
description: 从添加图片、选择输出目录到运行首个翻译任务的桌面操作与工作流边界
pageId: introduction.first-translation
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 第一次翻译

> 还没安装？先看[安装文档](../install/windows-portable.md)（Windows 便携版/源码/Linux/macOS/Docker 任选）。
> 在线翻译需要 API Key：看[API 配置教程](../desktop/api-management/api-key-guide.md)与[API 功能选择器](../desktop/api-management/feature-selectors.md)。

下面按「安装 → API 配置 → 操作步骤」介绍桌面端第一次完整翻译的流程。

## 先了解这些 {#feature-boundary}

这里按桌面端第一次完整翻译的最小路径：安装、配置 API（在线翻译需要）、添加图片、设置输出目录、保持“正常翻译流程”并启动任务。检测器、OCR、翻译器、排版、API 凭据等各模块的参数细节见对应模块页（[设置](../reference/settings-index.md)、[翻译器](../desktop/translator/selection-and-languages.md)、[API 管理](../desktop/api-management/feature-selectors.md)），不在本页展开。

第一次运行建议使用一张可公开、无敏感内容的图片。“正常翻译流程”不是跳过配置的演示模式：使用在线翻译时仍需在 API 管理中配好凭据。

## 安装 {#installation}

先安装桌面端，任选一种方式：

- **Windows 便携版**：解压即用，见[Windows 便携版](../install/windows-portable.md)。
- **Windows 源码安装**：从源码运行，见[Windows 源码安装](../install/source-windows.md)。
- **Linux/macOS**：在 Linux 或 macOS 上运行，见[Linux 与 macOS 安装](../install/linux-and-macos.md)。
- **Docker 部署**：以容器方式运行，见[Docker 部署](../install/docker.md)。

安装并启动后，打开侧栏的“翻译界面”，再按下面的[操作步骤](#steps)继续。

## API 配置 {#api-configuration}

在线翻译需要 API Key。使用在线翻译时，先在“API 管理”中完成配置：

- 申请并填入 API Key，见[API 配置教程](../desktop/api-management/api-key-guide.md)。
- 选择要启用的翻译功能与模型，见[API 功能选择器](../desktop/api-management/feature-selectors.md)。

配好后回到本页，按[操作步骤](#steps)添加文件并开始翻译；不使用在线翻译时可以跳过本节。

## 操作步骤 {#steps}

1. 打开侧栏的“翻译界面”。这是桌面端执行翻译的页面，页面标题默认显示“正常翻译流程”。
2. 添加输入：点击“添加文件”选择图片，或点击“添加文件夹”加入文件夹中的图片；也可以直接把文件或文件夹拖入输入列表。列表会显示已加入的文件，每个文件都可单独移除。
3. 确认输入列表不为空；列表为空时无法开始翻译。“清空列表”只清空当前输入列表，不会删除磁盘上的原图或已生成的结果。
4. 设置输出目录：在“输出目录”输入框中填写目录，点击“浏览...”选择目录，或直接把文件夹拖入输入框。译后图片会写入该目录。
5. 保持“翻译流程模式”为“正常翻译流程”，点击“开始翻译”。

启动前会检查输出目录、输入列表和 API 要求，检查不通过时任务不会启动。

## 任务中与任务后 {#during-and-after}

- 点击“开始翻译”后，按钮先显示 `Starting...`，随后变为“停止翻译”。任务期间输入列表、添加/清空按钮等会禁用，进度区显示当前数、总数和状态消息。
- 点击“停止翻译”后按钮变为“停止中...”，停止完成后任务回到已停止状态。停止只取消当前任务，不会删除已经保存的文件。
- 任务的成功、部分失败或跳过结果会记录在任务状态和日志中。译后图片写入你设置的输出目录；“打开”只打开输出目录，不等于打开编辑器。如需继续编辑结果，见[编辑器导入、导出与回写](../desktop/editor/import-export-and-writeback.md)。
- “导出翻译”“导出原文”“仅翻译（JSON）”和“导入翻译并渲染”等其它工作流需要理解工程副文件；第一次只想得到译后图片时，用“正常翻译流程”即可，不要误选这些模式。

“正常翻译流程”按配置依次执行：上色 → 超分 → 检测 → OCR → 文本行合并 → 翻译 → 图像修复 → 排版渲染；每一步都可选启用，未配置的步骤会跳过。

## 选择时要注意 {#dependencies-and-conflicts}

- 需要可读的输入图片，以及一个存在且可写的输出目录；条件不满足时任务不会启动。
- 支持常见图片扩展名（如 png、jpg、webp 等）；压缩包（如 .zip、.cbz）的支持情况取决于所用入口和解包依赖。
- 使用在线翻译时，需要先在 API 管理中配好凭据、地址和模型；这里不展示任何真实密钥。
- `save_text` 决定结果是否附带文本内容，`overwrite` 关闭时已存在的同名结果可能被跳过；两者都在设置中调整。

存储值与默认值见[设置参数索引](../reference/settings-index.md)。
