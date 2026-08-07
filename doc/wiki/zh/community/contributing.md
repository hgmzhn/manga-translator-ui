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

**隐私与脱敏**：不要上传 `.env` 原文、账号、API Key、Token、Cookie 或含密钥的完整预设文件（`presets/*.json`）；路径中的用户名与密钥先脱敏。

## 提交 PR {#opening-a-pr}

代码贡献走 Pull Request，流程为：Fork 仓库 → 新建分支 → 按规范修改 → 本地测试 → 提交 PR → 维护者审核合并。

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
