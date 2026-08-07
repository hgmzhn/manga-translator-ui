---
title: 相关项目与链接
description: 说明友情链接（相关项目）的申请方式、收录规则与审核口径
pageId: developer.related-projects-and-links
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 相关项目与链接

## 涉及的代码 {#feature-boundary}

- 相关项目列表是 Wiki 站点功能，不是桌面端、CLI 或 Web 服务器的运行时功能。
- 收录由维护者人工审核；只有审核通过的条目才会展示。
- 这里按友情链接申请；普通代码贡献（PR）、测试、打包发布见[参与贡献](../community/contributing.md)与对应开发者页面。

## 申请方式 {#how-to-apply}

想申请友情链接，直接在 GitHub Issues 提一个申请 Issue 即可：https://github.com/hgmzhn/manga-translator-ui/issues

在 Issue 里说明：

- 项目名称与公开链接（必须是 HTTPS）；
- 项目与 Manga Translator 的关系（例如基于本项目的二次开发、相关工具、社区等）；
- 期望分类：翻译 / OCR / 排版 / 图像处理 / 社区 / 工具；
- 可选：项目 Logo（请确认有使用授权）。

维护者审核后会把项目加入列表；不通过也会在 Issue 里反馈原因。提 Issue 的一般方式见[参与贡献](../community/contributing.md)。

## 收录规则 {#inclusion-rules}

- 只收录与 Manga Translator 相关的项目，领域可以是翻译、OCR、排版、图像处理、社区或工具。
- 只接受公开的 HTTPS 链接；拒绝冒充、恶意下载、隐私追踪与未授权 Logo。
- 列表不构成商业背书，链接可随时下架。
- 访问者自行承担外链风险，Wiki 不保证第三方站点内容或安全性。

## 关联页面 {#related-pages}

- [参与贡献](../community/contributing.md)：提 Issue（功能建议、Bug 反馈）与提交 PR。
- [新增或修改功能](./adding-or-changing-a-feature.md)、[测试与代码质量](./tests-and-code-quality.md)、[打包与发布](./packaging-and-release.md)：代码贡献与发布流程。

## 开发指南 {#developer-guide}

### 选项中英对照 {#option-matrix}

#### 分类枚举 {#category-enum}

`category` 只能是以下六个值之一，对应申请方填写的“期望分类”：

| 存储值 | English | 简体中文 |
| --- | --- | --- |
| `translation` | Translation | 翻译 |
| `ocr` | OCR | OCR / 文字识别 |
| `typesetting` | Typesetting | 排版 |
| `image-processing` | Image processing | 图像处理 |
| `community` | Community | 社区 |
| `tooling` | Tooling | 工具 |

### 数据文件与格式

以下数据文件由维护者在收录时维护，申请方不需要直接修改它们：

| 文件 | 本页实际作用 | 注意 |
| --- | --- | --- |
| `doc/wiki/data/related-projects.yml` | 相关项目列表的唯一审核数据源 | 当前为 `projects: []`（空列表）；只通过 PR 修改 |
| `doc/wiki/data/related-projects.schema.json` | JSON Schema，定义全部字段与格式 | 由 `verify_related_projects.py --write-schema` 生成，手改会导致校验失败 |
| `doc/wiki/verify_related_projects.py` | Pydantic 模型校验脚本 | 从仓库根目录运行 `uv run python doc/wiki/verify_related_projects.py` |
| `doc/wiki/data/README.md` | 数据治理说明 | 记录“提交不等于自动发布”与人工审核规则 |
| `desktop_qt_ui/locales/en_US.json` / `zh_CN.json` | 不参与本页 | 已核对无相关 key；双语文案来自数据文件 |

### 代码位置 {#source-evidence}
| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 数据源 | `doc/wiki/data/related-projects.yml` | 当前空列表、`schema_version`、字段结构与 `LocalizedText` 双语约定 |
| Schema | `doc/wiki/data/related-projects.schema.json` | 全部字段、正则、枚举、必填项与日期格式 |
| 校验脚本 | `doc/wiki/verify_related_projects.py` | Pydantic 模型、HTTPS URL 校验、`--write-schema` 与 PASS 输出 |
| 数据治理 | `doc/wiki/data/README.md` | 提交不等于自动发布、人工审核、无商业背书 |
| 契约 | `doc/wiki/BLUEPRINT.md` 第 9.6 节 | 申请材料清单、官方反馈渠道、收录与下架规则 |
| 覆盖矩阵 | `doc/wiki/research/phase0-page-coverage-matrix.md` | W104 行与 S00 证据族 |
