---
title: 参与贡献
description: 说明如何通过 Issue 提出功能建议与 Bug 反馈、通过 PR 贡献代码，以及社区交流渠道
pageId: community.contributing
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 参与贡献

这里说明如何参与 Manga Translator 项目：通过 Issue 提出功能建议与 Bug 反馈、通过 PR 贡献代码，以及加入社区交流渠道。

## 参与方式 {#feature-boundary}

- 下面列出提 Issue、提 PR 和加入社区渠道的方式。
- 功能开发链路见[新增或修改功能](../developer/adding-or-changing-a-feature.md)，测试与代码质量见[测试与代码质量](../developer/tests-and-code-quality.md)，打包与发布见[打包与发布](../developer/packaging-and-release.md)，架构与代码边界见[架构与代码边界](../developer/architecture-and-code-boundaries.md)。
- 友情链接申请见[相关项目与链接](../developer/related-projects-and-links.md)。

## 提出 Issue {#opening-an-issue}

所有建议与反馈都通过 GitHub Issues 提交：[https://github.com/hgmzhn/manga-translator-ui/issues](https://github.com/hgmzhn/manga-translator-ui/issues)。

**功能建议**（新功能、交互优化、流程改进）使用「功能建议」模板，填写使用场景、期望功能、价值与收益，可附原型、截图或示例。提交前先搜索现有 Issues，确认不是重复建议；如果“已有功能行为不对”，请改用「Bug 反馈」模板。

**Bug 反馈**使用「Bug 反馈」模板，填写问题类型、问题概述、复现步骤、期望 vs 实际、运行环境。必须提供翻译前的原图或输入文件（不要只给结果图），尽量提供配置、日志与相关 JSON；日志默认在 `result/log_*.txt`。

**使用问题 / 求助**使用「使用问题 / 求助」模板，适用于安装启动、配置模型、翻译效果、编辑器操作、性能兼容性，以及 PR 或功能实现咨询。请说明具体问题、已经尝试的方法和运行环境；可补充复现步骤、日志、截图、配置片段或相关 Issue/PR。若不确定改动是否符合项目方向，也可以先使用此模板讨论。

**隐私与脱敏**：不要上传 `.env` 原文、账号、API Key、Token、Cookie 或含密钥的完整预设文件（`presets/*.json`）；路径中的用户名与密钥先脱敏。

## 提交 PR {#opening-a-pr}

代码贡献走 Pull Request，流程为：Fork 仓库 → 新建分支 → 按规范修改 → 本地测试 → 提交 PR → 维护者审核合并。

### 提交前要求 {#pr-requirements}

提交 PR 前请逐项确认：

1. **基于最新仓库修改**：从上游仓库的最新默认分支创建或更新开发分支。提交 PR 前再次同步上游改动，处理冲突，并确认你的修改没有意外覆盖仓库中的新代码。
2. **只提交与本次改动有关的文件**：不要混入编辑器配置、临时文件、运行产物、个人配置、无关格式化结果，或顺手修改但与当前 PR 无关的内容。一个 PR 应聚焦一个明确问题，便于审核和回退。
3. **保持代码整洁**：遵循仓库现有结构、命名和编码风格；删除调试输出、注释掉的旧代码、未使用的导入、死代码和临时兼容逻辑。不要为了完成局部需求复制一套已有实现。
4. **完整更新关联内容**：行为、配置或接口发生变化时，同步更新所有调用方以及必要的测试、文档和中英文内容，不保留失效说明或过时代码路径。
5. **自行验证改动**：按项目文档运行与改动直接相关的检查，并在 PR 描述中写明实际执行的命令与结果。界面改动请附截图或录屏；Bug 修复请说明复现步骤和修复后的结果。
6. **清楚填写 PR 描述**：说明问题背景、改动范围、实现方式、验证结果和可能影响。若改动较大或不确定是否会被接受，请先提交 Issue 讨论方向，避免投入与项目目标不一致。

维护者会重点检查改动是否聚焦、是否与最新仓库对齐、实现是否清晰可维护，以及验证证据是否覆盖实际行为。不符合上述要求的 PR 可能会被要求整理后再审。

- 功能开发链路见[新增或修改功能](../developer/adding-or-changing-a-feature.md)。
- 测试与代码质量见[测试与代码质量](../developer/tests-and-code-quality.md)。
- 打包与发布见[打包与发布](../developer/packaging-and-release.md)。
- 架构与代码边界见[架构与代码边界](../developer/architecture-and-code-boundaries.md)。
- 友情链接申请不需要走 PR，直接在 Issues 提申请 Issue 即可，见[相关项目与链接](../developer/related-projects-and-links.md)。

## 社区渠道 {#community-channels}

- 交流群等社区渠道与文档导航见仓库 [README](https://github.com/hgmzhn/manga-translator-ui/blob/main/README.md)（英文版见 [README_EN.md](https://github.com/hgmzhn/manga-translator-ui/blob/main/README_EN.md)）。
- 在线文档可参考 README 中的 DeepWiki 链接。

## 关联页面 {#related-pages}

- [新增或修改功能](../developer/adding-or-changing-a-feature.md)：功能开发链路与修改步骤。
- [测试与代码质量](../developer/tests-and-code-quality.md)：测试目录、uv 命令与格式检查。
- [打包与发布](../developer/packaging-and-release.md)：版本打包与发布流程。
- [架构与代码边界](../developer/architecture-and-code-boundaries.md)：代码模块边界与调用关系。
- [相关项目与链接](../developer/related-projects-and-links.md)：友情链接申请方式（提 Issue）。
