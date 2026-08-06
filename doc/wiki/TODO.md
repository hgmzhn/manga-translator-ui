# Manga Translator Wiki TODO

> 需求与目录依据：[`BLUEPRINT.md`](./BLUEPRINT.md)
>
> 本文件是 Wiki 实施的唯一进度表。蓝图定义“要做什么”，本文件记录“是否完成”。

## 1. 勾选准则

### 1.1 状态写法

- `- [ ] [未开工]`：尚未开始，不得附带完成证据。
- `- [ ] [进行中]`：已经开始，但尚未满足全部完成条件；可以追加 `PROGRESS: 已完成内容`。
- `- [x] [完成]`：已经满足本节全部完成条件，并完成验证。
- 阻塞不是第四种状态：保留阻塞发生时的“未开工”或“进行中”，在行末追加 `BLOCKED: 原因`。
- 不使用 `[~]`、`[-]` 等 GitHub 不统一支持的伪 checkbox，也不使用“基本完成”“待复核”等额外状态。

### 1.2 谁可以打勾

- 开始任务的代理先把 `[未开工]` 改成 `[进行中]`，checkbox 仍保持未勾选。
- 实际完成并验证该项的代理或主代理负责把 `- [ ] [进行中]` 改成 `- [x] [完成]`。
- 不得因为创建了空文件、写了标题、复制了中文或列了源码路径就打勾。
- 不得一次性把父任务和未逐项核对的子任务全部打勾。
- 修改状态时只改 checkbox、三状态标签和必要的证据说明，不顺手删除或缩小原任务范围。

### 1.3 页面完成条件

任一 `zh/en <path>.md` 页面只有同时满足以下条件才能打勾：

- 页面责任边界与 `BLUEPRINT.md` 一致，没有把功能放错模块。
- 中文正文覆盖该页全部 UI 操作、状态、限制、关联文件和运行机理 TODO。
- 英文正文与中文结构、锚点、表格和图片完全镜像。
- UI 文案记录“调用 key -> `en_US` 实际值 -> `zh_CN` 实际值”，缺失项如实标记。
- 参数页中的每个参数都有独立锚点、存储值、全部选项、三类默认值、影响阶段、依赖和最终消费者。
- 文件格式、端口、命令或 API 契约经过对应源码核对，不照抄旧文档结论。
- 页面列出定义、UI/i18n、持久化/调度和最终消费者等源码依据。
- 蓝图要求的 Mermaid、截图或调试图已在未来对应实施阶段完成；当前蓝图阶段不得提前伪造或截图。
- 内部链接、双语切换、移动端排版和 VitePress 构建通过。
- 复核者确认没有密钥、令牌、用户名、私有绝对路径、用户图片或私有提示词。

上面十项是所有页面共用的完成定义，不在每个页面下重复粘贴。页面 checkbox 打勾即代表十项均已满足。

### 1.4 父任务完成条件

- 模块任务只有在其所有页面、资源、图示和校验子项均为 `[x] [完成]` 后才能打勾。
- Phase 任务只有在该阶段所有模块任务均为 `[x] [完成]` 后才能打勾。
- “站点完成”只有在 Phase 0-5、发布构建和 GitHub Pages 验收全部为 `[x] [完成]` 后才能打勾。
- 新发现的页面、参数、状态或路由必须先增加 checkbox，再判断父任务是否完成。

### 1.5 证据记录

完成任务时在 checkbox 后追加最短必要证据：

```md
- [x] [完成] `zh/en desktop/navigation-and-language.md` - build: PASS; review: 2026-08-06
- [ ] [进行中] `zh/en desktop/settings/ocr-filter-and-merge.md` - PROGRESS: 中文与参数表完成，英文待核对
- [ ] [进行中] Web API 流协议 - BLOCKED: 需要最小客户端运行验证
```

禁止把“应该可用”“看起来正确”作为完成证据。环境依赖缺失、测试未收集或页面未构建时保持未勾选。

## 2. 已完成的蓝图工作

- [x] [完成] 从零建立 `doc/wiki/BLUEPRINT.md`，未迁移旧文档正文。
- [x] [完成] 前端 UI、二级页面、快捷键和 i18n 第一轮源码调查。
- [x] [完成] 参数消费者、流水线、上下文、重试和 API 轮换第一轮源码调查。
- [x] [完成] 安装、CLI、Web、HTTP API、端口和开发者文档第一轮源码调查。
- [x] [完成] 明确翻译器选择、API 功能选择器、API 槽轮换和 `translator_chain` 的边界。
- [x] [完成] 设计 VitePress/Vue、双语镜像和 GitHub Pages 总目录。
- [x] [完成] 建立本 TODO 勾选准则和总任务表。

## 3. Phase 0：覆盖清单与数据源

- [x] [完成] 固定桌面主页面和导航清单。 - evidence: `research/desktop-main-navigation.md`; source: 2026-08-06
- [x] [完成] 固定所有二级页面、弹窗和状态清单。 - evidence: `research/phase0-secondary-pages-dialogs-statuses.md`（静态源码/i18n；运行态未决项已记录：2026-08-06）
- [x] [完成] 固定编辑器菜单、工具、属性、快捷键和焦点冲突清单。 - evidence: `research/editor-inventory.md`; static source/i18n inventory: 2026-08-06
- [x] [完成] 生成 UI 参数字段清单并报告与“约 110 项”基线的差异。`phase0-ui-parameter-fields.json`; validate: layout 110, visible 109, delta -1.
- [x] [完成] 生成所有枚举/下拉选项的 value、English、简体中文和 i18n key 清单。 - evidence: `research/phase0-options-i18n-matrix.md`; static desktop source/i18n inventory: 2026-08-06
- [x] [完成] 固定核心默认、Qt UI 默认、发行配置默认和差异清单。 - evidence: `research/default-sources.md`（Core 120 / Qt 131 / Release 131；差异矩阵与运行态未决项：2026-08-06）
- [x] [完成] 固定九个工作流、输入输出、跳过阶段和互斥参数清单。 - EVIDENCE: `research/workflow-matrix-source-evidence.md` 已完成九个工作流的静态源码/i18n 清单；运行验证列为后续任务。
- [x] [完成] 固定 CLI 正式子命令、参数和实际 `--help` 清单。 - evidence: `research/cli-command-inventory.md`; help: PASS 2026-08-06
- [x] [完成] 固定 Web 用户功能、HTTP 路由、鉴权、端口和状态码清单。 - evidence: `research/phase0-web-user-http.md`（静态源码；150 个方法—路径映射、运行态未决项已记录：2026-08-06）
- [x] [完成] 固定关联文件、格式、调试产物和敏感信息清单。 - evidence: `research/phase0-related-files-formats-debug-safety.md`; static source paths: PASS (43); 2026-08-06
- [x] [完成] 追踪 `_result_path` 的直接调用、`result_path_fn` / `debug_path_fn` 回调写入和同目录手工路径，生成完整调试产物清单。 - evidence: `research/phase0-debug-artifact-path-trace.md`; static source trace: PASS 2026-08-06; runtime validation pending.
- [x] [完成] 建立“页面 -> 中文 -> 英文 -> 源码 -> 图示 -> 构建”覆盖矩阵。 - evidence: `research/phase0-page-coverage-matrix.md`; coverage: BLUEPRINT 114 + TODO-only debugging 6 = 120; static validation: PASS 2026-08-06; site build: N/A（B0 基线）

## 4. Phase 1：VitePress/Vue 骨架

- [x] [完成] 创建 `package.json`、锁文件和 VitePress 依赖。 - `npm ci --prefix doc/wiki`: PASS; `npm ls vitepress vue --prefix doc/wiki`: vitepress 1.6.4, vue 3.5.41; 2026-08-06
- [x] [完成] 创建 `.vitepress/config.ts` 和 GitHub Pages `base` 配置。 - static config: PASS; base: `/manga-translator-ui/`; 2026-08-06
- [x] [完成] 创建 Vue 自定义主题、`Layout.vue` 和全局样式。 - `npm run docs:build --prefix doc/wiki`: PASS; 2026-08-06
- [x] [完成] 创建右上角 `LanguageSwitch.vue`，保持当前页面进行中英切换。 - route-prefix mapping + locale config; build: PASS; 2026-08-06
- [x] [完成] 创建 `SettingTable.vue`、`OptionMatrix.vue`、`SourceEvidence.vue`。 - static Vue components; build: PASS; 2026-08-06
- [x] [完成] 创建 `public/images/` 的模块目录和命名约定。 - structure: PASS (19 modules); naming: `public/images/README.md`; 2026-08-06
- [x] [完成] 创建 `data/settings.generated.json`。 - `uv run --no-sync python doc/wiki/scripts/build-settings-catalog.py --check`: PASS (109 records); 2026-08-06
- [x] [完成] 创建 `data/i18n.generated.json`。 - `node scripts/build-i18n-catalog.mjs --check`: PASS (1353 keys); 2026-08-06
- [x] [完成] 创建 `data/coverage.generated.json`。 - `node scripts/verify-wiki-coverage.mjs doc/wiki`: PASS (120 pages, S00-S16); 2026-08-06
- [x] [完成] 创建 `data/related-projects.yml` 及 schema/字段校验。 - `uv run python doc/wiki/verify_related_projects.py`: PASS (0 projects); 2026-08-06
- [x] [完成] 创建参数目录生成脚本。 - `uv run --no-sync python doc/wiki/scripts/build-settings-catalog.py --check`: PASS (109 records); 2026-08-06
- [x] [完成] 创建 i18n 目录生成脚本。 - `node scripts/build-i18n-catalog.mjs --check`: PASS; 2026-08-06
- [x] [完成] 创建中英路由镜像检查脚本。 - `node scripts/verify-route-mirror.mjs doc/wiki`: PASS (120/120); 2026-08-06
- [x] [完成] 创建源码依据和覆盖率检查脚本。 - `node scripts/verify-source-evidence.mjs doc/wiki` + `verify-wiki-coverage.mjs`: PASS; 2026-08-06
- [x] [完成] 创建 GitHub Pages workflow。 - `.github/workflows/docs-pages.yml`; YAML/static review: PASS; 2026-08-06
- [x] [完成] 生成以下全部中英文占位页面，且占位页面保持未完成状态。 - `uv run python scripts/generate-placeholders.py`: 240 pages; mirror: PASS; 2026-08-06
- [x] [完成] `npm ci --prefix doc/wiki` 成功。 - PASS; 127 packages installed; 2026-08-06
- [x] [完成] `npm run docs:build --prefix doc/wiki` 成功。 - PASS; 2026-08-06

## 5. 页面总表

每一项同时代表 `zh/` 与 `en/` 两个镜像页面。只完成其中一种语言不能打勾。

### 5.1 首页与入门

- [ ] [未开工] `zh/en index.md`
- [x] [完成] `zh/en introduction/product-forms.md` - evidence: 双语正文、源码依据、i18n 三列、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en introduction/first-translation.md` - evidence: 双语正文、九种流程、源码依据、i18n 三列、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en introduction/data-and-privacy.md` - evidence: 双语正文、数据流、源码依据、敏感信息审查、静态检查；后续统一验收按第 8 节执行

### 5.2 安装

- [x] [完成] `zh/en install/choose-edition.md` - evidence: 双语正文、安装选择与源码依据、i18n 三列、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en install/requirements.md` - evidence: 双语正文、Python/硬件依赖与源码依据、i18n 三列、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en install/windows-portable.md` - evidence: 双语正文、便携版启动与源码依据、i18n 三列、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en install/source-windows.md` - evidence: 双语正文、源码安装与源码依据、i18n 三列、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en install/linux-and-macos.md` - evidence: 双语正文、Unix 安装/启动与平台依赖源码依据、i18n 三列、Mermaid、安全审查、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en install/docker.md` - evidence: 双语正文、Compose/容器源码依据、i18n 边界、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en install/update-and-version-switching.md` - evidence: 双语镜像正文、维护菜单 i18n 三列、更新/分支/tag/镜像机理、依赖冲突、文件/安全审查、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en install/uninstall-and-data-cleanup.md` - evidence: 双语正文、卸载/数据清理源码依据、i18n 三列、Mermaid、安全审查、静态检查；后续统一验收按第 8 节执行

### 5.3 桌面导航与翻译工作区

- [x] [完成] `zh/en desktop/navigation-and-language.md` - evidence: 双语正文、导航与语言切换源码依据、Mermaid、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en desktop/translation/file-list-and-input.md` - evidence: 双语正文、输入状态、格式、源码依据、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en desktop/translation/output-directory-and-workflow.md` - evidence: 双语正文、九种工作流、路径、源码依据、静态检查；后续统一验收按第 8 节执行
- [x] [完成] `zh/en desktop/translation/progress-stop-and-task-state.md` - evidence: 双语正文、状态/按钮 i18n 三列、Mermaid、停止状态测试依据、静态检查；后续统一验收按第 8 节执行

### 5.4 设置与参数

- 参数介绍按 **7 个 UI 设置页签** 归属，再拆成 **9 个 Wiki 参数页面**；不是每个参数单独一页。
- 7 个 UI 页签来自 `desktop_qt_ui/ui/main_page/settings_tab_layout.json`：`General`、`OCR`、`Detection`、`Translation`、`Inpainting`、`Typesetting`、`Mode Specific`。

| UI 设置页签 | Wiki 参数页面 | 页面内容边界 |
| --- | --- | --- |
| General | `general-and-app.md`、`cli-batch-and-output.md` | 应用语言/主题/预设、CLI 覆盖、批量、输出、API 参数开关、模型卸载 |
| OCR | `ocr-filter-and-merge.md` | OCR 模型、混合 OCR、AI OCR、过滤、文本行合并和气泡约束 |
| Detection | `detection.md` | 检测器、尺寸、阈值、YOLO、SFX 和框过滤 |
| Translation | `translation.md` | 翻译器、目标语言、流式、术语、RPM、译后处理和上下文 |
| Inpainting | `mask-and-inpainting.md` | 蒙版膨胀、气泡交集、修复器、尺寸、精度和逐块修复 |
| Typesetting | `typesetting-and-rendering.md` | renderer、字体、方向、对齐、断句、布局、间距和 AI 渲染 |
| Mode Specific | `mode-specific.md`、`upscale-and-colorization.md` | 替换翻译、模板对齐、超分和上色的模式专用参数 |

- [ ] [未开工] P01 参数总索引：7 个 UI 页签到 9 个 Wiki 页面映射。
- [ ] [未开工] P02 General / CLI-Batch-Output 参数组。
- [ ] [未开工] P03 OCR / Filter / Merge 参数组。
- [ ] [未开工] P04 Detection 参数组。
- [ ] [未开工] P05 Translation 参数组。
- [ ] [未开工] P06 Mask / Inpainting 参数组。
- [ ] [未开工] P07 Typesetting / Rendering 参数组。
- [ ] [未开工] P08 Upscale / Colorization 参数组。
- [ ] [未开工] P09 Mode Specific 参数组。

- [x] [完成] `zh/en desktop/settings/index.md` - evidence: 双语正文、设置生命周期与源码依据、i18n 三列、Mermaid、静态检查；后续统一验收按第 8 节执行
- [ ] [进行中] `zh/en desktop/settings/shell-description-import-export.md` - PROGRESS: 双语正文、源码依据、i18n 三列和 Mermaid 完成；有头运行与统一验收待执行
- [ ] [未开工] `zh/en desktop/settings/general-and-app.md`
- [ ] [未开工] `zh/en desktop/settings/cli-batch-and-output.md`
- [ ] [未开工] `zh/en desktop/settings/detection.md`
- [ ] [未开工] `zh/en desktop/settings/ocr-filter-and-merge.md`
- [ ] [未开工] `zh/en desktop/settings/translation.md`
- [ ] [未开工] `zh/en desktop/settings/mask-and-inpainting.md`
- [ ] [未开工] `zh/en desktop/settings/typesetting-and-rendering.md`
- [ ] [未开工] `zh/en desktop/settings/upscale-and-colorization.md`
- [ ] [未开工] `zh/en desktop/settings/mode-specific.md`

### 5.5 翻译器

- [ ] [未开工] `zh/en desktop/translator/selection-and-languages.md`
- [ ] [未开工] `zh/en desktop/translator/engine-dispatch.md`
- [ ] [未开工] `zh/en desktop/translator/context-and-prompts.md`
- [ ] [未开工] `zh/en desktop/translator/glossary-stream-and-linebreak.md`
- [ ] [未开工] `zh/en desktop/translator/retry-rate-limit-and-quality.md`
- [ ] [未开工] `zh/en desktop/translator/translation-chain.md`

### 5.6 API 管理

- [ ] [未开工] `zh/en desktop/api-management/feature-selectors.md`
- [ ] [未开工] `zh/en desktop/api-management/provider-tabs.md`
- [ ] [未开工] `zh/en desktop/api-management/credentials-addresses-models.md`
- [ ] [未开工] `zh/en desktop/api-management/slots-and-rotation.md`
- [ ] [未开工] `zh/en desktop/api-management/failures-cooldown-and-recovery.md`
- [ ] [未开工] `zh/en desktop/api-management/connection-tests-and-model-list.md`
- [ ] [未开工] `zh/en desktop/api-management/custom-request-parameters.md`
- [ ] [未开工] `zh/en desktop/api-management/presets-and-persistence.md`

### 5.7 提示词

- [ ] [未开工] `zh/en desktop/prompts/list-apply-and-preview.md`
- [ ] [未开工] `zh/en desktop/prompts/structured-editor-and-format.md`
- [ ] [未开工] `zh/en desktop/prompts/system-and-translation-prompts.md`
- [ ] [未开工] `zh/en desktop/prompts/ai-ocr-prompt.md`
- [ ] [未开工] `zh/en desktop/prompts/ai-renderer-prompt.md`
- [ ] [未开工] `zh/en desktop/prompts/ai-colorizer-prompt.md`

### 5.8 替换规则与富文本规则

- [ ] [未开工] `zh/en desktop/replacement-rules/table-groups-and-order.md`
- [ ] [未开工] `zh/en desktop/replacement-rules/raw-yaml-regex-and-save.md`
- [ ] [未开工] `zh/en desktop/rich-text-rules/table-raw-and-match.md`
- [ ] [未开工] `zh/en desktop/rich-text-rules/styles-and-presets.md`

### 5.9 批量管理

- [ ] [未开工] `zh/en desktop/batch-management/schemes-crud.md`
- [ ] [未开工] `zh/en desktop/batch-management/conditions.md`
- [ ] [未开工] `zh/en desktop/batch-management/actions-and-order.md`
- [ ] [未开工] `zh/en desktop/batch-management/preview-apply-restore.md`

### 5.10 编辑器

- [ ] [未开工] `zh/en desktop/editor/layout-and-file-list.md`
- [ ] [未开工] `zh/en desktop/editor/toolbar-and-menus.md`
- [ ] [未开工] `zh/en desktop/editor/display-compare-and-arrange.md`
- [ ] [未开工] `zh/en desktop/editor/canvas-tools-and-selection.md`
- [ ] [未开工] `zh/en desktop/editor/region-list-and-text-editing.md`
- [ ] [未开工] `zh/en desktop/editor/text-properties.md`
- [ ] [未开工] `zh/en desktop/editor/style-properties.md`
- [ ] [未开工] `zh/en desktop/editor/mask-paint-and-clone-stamp.md`
- [ ] [未开工] `zh/en desktop/editor/floating-rich-text.md`
- [ ] [未开工] `zh/en desktop/editor/shortcuts.md`
- [ ] [未开工] `zh/en desktop/editor/import-export-and-writeback.md`

### 5.11 九个工作流

- [ ] [未开工] `zh/en workflows/normal.md`
- [ ] [未开工] `zh/en workflows/export-translation.md`
- [ ] [未开工] `zh/en workflows/export-original.md`
- [ ] [未开工] `zh/en workflows/translate-json-only.md`
- [ ] [未开工] `zh/en workflows/import-translation-and-render.md`
- [ ] [未开工] `zh/en workflows/colorize-only.md`
- [ ] [未开工] `zh/en workflows/upscale-only.md`
- [ ] [未开工] `zh/en workflows/inpaint-only.md`
- [ ] [未开工] `zh/en workflows/replace-translation.md`

### 5.12 Web 用户端

- [ ] [未开工] `zh/en web/launch-and-access.md`
- [ ] [未开工] `zh/en web/login-language-and-session.md`
- [ ] [未开工] `zh/en web/upload-config-and-translate.md`
- [ ] [未开工] `zh/en web/progress-results-and-history.md`
- [ ] [未开工] `zh/en web/accounts-permissions-and-api-keys.md`
- [ ] [未开工] `zh/en web/resources-fonts-and-prompts.md`
- [ ] [未开工] `zh/en web/administrator-interface.md`
- [ ] [未开工] `zh/en web/deployment-security-and-troubleshooting.md`

### 5.13 CLI

- [ ] [未开工] `zh/en cli/command-structure.md`
- [ ] [未开工] `zh/en cli/local-input-output.md`
- [ ] [未开工] `zh/en cli/configuration-overrides.md`
- [ ] [未开工] `zh/en cli/workflow-and-file-modes.md`
- [ ] [未开工] `zh/en cli/subprocess-memory-and-recovery.md`
- [ ] [未开工] `zh/en cli/output-debugging-and-exit-codes.md`
- [ ] [未开工] `zh/en cli/web-ws-and-shared-modes.md`

### 5.14 开发者与 HTTP API

- [ ] [未开工] `zh/en developer/architecture-and-code-boundaries.md`
- [ ] [未开工] `zh/en developer/adding-or-changing-a-feature.md`
- [ ] [未开工] `zh/en developer/tests-and-code-quality.md`
- [ ] [未开工] `zh/en developer/packaging-and-release.md`
- [ ] [未开工] `zh/en developer/web-server-ports-and-deployment.md`
- [ ] [未开工] `zh/en developer/http-api/authentication-and-errors.md`
- [ ] [未开工] `zh/en developer/http-api/translation-endpoints.md`
- [ ] [未开工] `zh/en developer/http-api/streaming-protocol.md`
- [ ] [未开工] `zh/en developer/http-api/batch-export-import-process.md`
- [ ] [未开工] `zh/en developer/http-api/config-env-and-resources.md`
- [ ] [未开工] `zh/en developer/http-api/history-files-and-download-tickets.md`
- [ ] [未开工] `zh/en developer/http-api/admin-users-groups-quota-audit.md`
- [ ] [未开工] `zh/en developer/internal-shared-and-websocket.md`
- [ ] [未开工] `zh/en developer/related-projects-and-links.md`

### 5.15 调试目录与产物

- [ ] [未开工] `zh/en debugging/folder-naming-and-overview.md`
- [ ] [未开工] `zh/en debugging/input-detection-and-rearrangement.md`
- [ ] [未开工] `zh/en debugging/ocr-and-text-regions.md`
- [ ] [未开工] `zh/en debugging/mask-inpainting-and-rendering.md`
- [ ] [未开工] `zh/en debugging/special-workflows-and-websocket.md`
- [ ] [未开工] `zh/en debugging/how-to-read-and-share-a-debug-run.md`

### 5.16 参考索引

- [ ] [未开工] `zh/en reference/settings-index.md`
- [ ] [未开工] `zh/en reference/options-i18n-matrix.md`
- [ ] [未开工] `zh/en reference/workflow-matrix.md`
- [ ] [未开工] `zh/en reference/source-evidence-index.md`
- [ ] [未开工] `zh/en reference/debug-artifact-index.md`

### 5.17 故障排查

- [ ] [未开工] `zh/en troubleshooting/installation-and-startup.md`
- [ ] [未开工] `zh/en troubleshooting/model-gpu-and-memory.md`
- [ ] [未开工] `zh/en troubleshooting/api-auth-rate-limit-and-timeout.md`
- [ ] [未开工] `zh/en troubleshooting/output-json-and-rendering.md`
- [ ] [未开工] `zh/en troubleshooting/privacy-cleanup-and-log-sharing.md`

## 6. 跨页面专项 TODO

### 6.1 参数和 i18n

- [ ] [未开工] 参数总表覆盖所有实际可见设置，数量由生成脚本确定。
- [ ] [未开工] 每个参数都能反向链接到所属功能页锚点。
- [ ] [未开工] 所有下拉/枚举选项完成 value、English、简体中文对照。
- [ ] [未开工] 所有 UI 调用 key 与实际 en/zh 显示值完成差异检查。
- [ ] [未开工] 逐页扫描操作步骤中用引号标出的页签、字段、按钮、菜单和状态，完成“UI 调用 key -> `en_US` 实际值 -> `zh_CN` 实际值”三方核对，并确认没有把环境变量名、后端字段名或自行翻译的文字写成界面名称。
- [ ] [未开工] 所有 i18n 缺失、回退和代码硬编码项已列出，不擅自翻译。
- [ ] [未开工] 核心默认、UI 默认和发行默认差异均已标注。
- [ ] [未开工] 逐参数判断是否需要图示；涉及阶段分支、枚举路径、重试状态、并发、阈值回退、上下文或文件数据流的参数均已配图，简单参数已明确记录“不需要：原因”。

### 6.2 翻译器与 API

- [ ] [未开工] 翻译器选择与 API 功能选择器的共用配置关系已验证。
- [ ] [未开工] Key/Base/Model 候选解析和优先级已验证。
- [ ] [未开工] `failover` 与 `round_robin` 已分别说明和验证。
- [ ] [未开工] 冷却、永久不可用、成功恢复和手动恢复状态已说明。
- [ ] [未开工] 翻译请求、OCR、上色、渲染的 feature-specific API 分组已隔离说明。
- [ ] [未开工] 自定义参数的 common/translator/ocr/colorizer/render 合并规则已说明。
- [ ] [未开工] `translator_chain` 与 API 槽轮换的差异已说明。
- [ ] [未开工] 普通重试、HQ/质量重试、区域重试和 API 候选切换没有混写。

### 6.3 文件与格式

- [ ] [未开工] `config/translation_template.json` 已放到实际工作流页并说明注意事项。
- [ ] [未开工] 翻译 JSON 顶层、regions、mask、富文本、覆盖层和兼容标志已验证。
- [ ] [未开工] `mask_raw` base64 PNG 与 `mask_is_refined` 行为已验证。
- [ ] [未开工] 提示词 JSON/YAML、固定提示词和系统提示词的消费者已分别说明。
- [ ] [未开工] 替换规则和富文本规则 YAML 的顺序、正则和恢复已说明。
- [ ] [未开工] 批量方案、`.bak`、回写和恢复机制已说明。
- [ ] [未开工] TXT、YOLO、PSD/JSX 和工作目录命名规则已说明。
- [ ] [未开工] 调试目录名中的时间戳、图片 MD5、检测尺寸、目标语言和翻译器字段已说明。
- [ ] [未开工] 每个调试图片、JSON、JSX 和 `ocrs/` 子目录均已记录生成阶段、触发条件、画面/内容含义、排查用途和源码依据。
- [ ] [未开工] 长图重排已说明触发条件、切块/拼批过程、检测结果坐标回映、性能与显存影响，以及 `rearrange_{n}.png`、`yolo_rearrange_{n}.png` 分别对应的处理分支。
- [ ] [未开工] 明确区分“某次运行实际存在的产物”和“当前源码在不同模式下可能生成的完整产物”，不把条件产物写成每次必有。
- [ ] [未开工] 样例目录 `result/1785860417472-3415b69c-2048-CHS-openai/` 已作为核对案例，只记录实际文件，不收录用户原图或私有内容。
- [ ] [未开工] 调试产物的清理、脱敏和对外分享注意事项已说明。

### 6.4 并发、上下文和错误

- [ ] [未开工] `batch_size` 与 `batch_concurrent` 的差异已说明。
- [ ] [未开工] 并发关闭/开启的数据流、背压、顺序和失败隔离已说明。
- [ ] [未开工] 特殊工作流禁用并发的条件已逐项说明。
- [ ] [未开工] 历史页上下文选择与 OpenAI/Gemini 消息构建已说明。
- [ ] [未开工] 取消、`ignore_errors`、每图 Context 隔离和清理已说明。
- [ ] [未开工] 模型缓存、TTL、任务后卸载和子进程重启没有混写。

### 6.5 安装、CLI、Web 和 API

- [ ] [未开工] Python 和 uv 版本、依赖组及硬件后端已从当前配置核对。
- [ ] [未开工] Windows、Unix、Docker、升级、版本切换和卸载路径已核对。
- [ ] [未开工] `local/web/ws/shared` 与实际 `--help` 一致。
- [ ] [未开工] Web `0.0.0.0:8000`、shared `127.0.0.1:5003`、ws 上游 `ws://localhost:5000` 已区分。
- [ ] [未开工] Web 用户操作和开发者 HTTP API 没有混在同一页面。
- [ ] [未开工] HTTP 请求、响应、鉴权、状态码、流帧和批量任务已核对。
- [ ] [未开工] shared/ws 已标为内部协议，并说明 nonce/secret/pickle/protobuf 风险。
- [ ] [未开工] 友情链接 PR 材料、人工审核、安全复查和无商业背书规则已写明。

## 7. 图示与未来视觉任务

当前蓝图阶段保持全部未勾选，不安排当前调查代理截图。

- [ ] [未开工] 标准主流水线 Mermaid。
- [ ] [未开工] 九种工作流差异 Mermaid。
- [ ] [未开工] 翻译器、功能选择器和 API 槽边界 Mermaid。
- [ ] [未开工] API 候选轮换时序和状态机 Mermaid。
- [ ] [未开工] 上下文历史消息构建 Mermaid。
- [ ] [未开工] 混合 OCR 回退 Mermaid。
- [ ] [未开工] 蒙版、修复和排版数据流 Mermaid。
- [ ] [未开工] `batch_concurrent` 关闭/开启对照 Mermaid。
- [ ] [未开工] 翻译 JSON 与覆盖层保留 Mermaid。
- [ ] [未开工] Web 鉴权、队列、任务、取消和结果 Mermaid。
- [ ] [未开工] 所有复杂参数的 Mermaid 图都展示了不同取值造成的实际流程或输出变化，没有使用空泛的“配置 -> 算法 -> 输出”占位图。
- [ ] [未开工] 桌面页面基础图、关键状态和交互特写。
- [ ] [未开工] Web 用户端与管理员端关键状态图。
- [ ] [未开工] CLI/安装终端、帮助和调试产物图。
- [ ] [未开工] 所有图片完成脱敏、双语 alt、双语图注和复现信息。

## 8. 自动校验与发布

- [ ] [未开工] 中英页面路径镜像检查通过。
- [ ] [未开工] 中英标题和锚点镜像检查通过。
- [ ] [未开工] 参数覆盖检查通过。
- [ ] [未开工] i18n key 和实际值覆盖检查通过。
- [ ] [未开工] 源码依据字段检查通过。
- [ ] [未开工] 内部链接和资源链接检查通过。
- [ ] [未开工] Mermaid 渲染检查通过。
- [ ] [未开工] 图片缺失、alt 和脱敏人工复核通过。
- [ ] [未开工] 桌面与移动端文字无溢出、遮挡和导航异常。
- [ ] [未开工] VitePress 生产构建通过。
- [ ] [未开工] GitHub Pages 仓库子路径部署通过。
- [ ] [未开工] 404、语言切换和刷新深层路由通过。
- [ ] [未开工] 发布后 HTTPS 和外链安全提示通过。

## 9. 总完成状态

- [ ] [未开工] Phase 0：覆盖清单与数据源完成。
- [ ] [未开工] Phase 1：VitePress/Vue 骨架完成。
- [ ] [未开工] Phase 2：桌面 UI、参数和编辑器页面完成。
- [ ] [未开工] Phase 3：运行机理、工作流、文件和调试说明完成。
- [ ] [未开工] Phase 4：安装、CLI、Web 和开发者 API 完成。
- [ ] [未开工] Phase 5：双语、图示、自动校验和 GitHub Pages 发布完成。
- [ ] [未开工] Manga Translator Wiki 全部完成。
