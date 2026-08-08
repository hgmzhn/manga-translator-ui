# Phase 0 页面覆盖矩阵

> 基线日期：2026-08-06
>
> 范围：`BLUEPRINT.md` 第 3 节目录树中的 114 个语言无关页面路径，以及 `TODO.md` 第 5 节额外列出的 6 个 `debugging/` 页面；并集共 120 页。
>
> 本表只记录覆盖状态，不代表任何页面已满足 `TODO.md` 的十项完成条件。

路径差异：`W105` 至 `W110` 的六个 `debugging/` 页面存在于 `TODO.md` 页面总表，且承接 `BLUEPRINT.md` 第 10 节调试图要求，但没有出现在蓝图第 3 节目录树中。本矩阵保留这些 TODO-only 页面，不在本任务中改写蓝图。

## 1. 状态口径

| 维度 | 状态 | 含义 |
| --- | --- | --- |
| 中文 | `C0 未创建` | `zh/<path>` 不存在 |
| 中文 | `C1 占位` / `C2 正文中` / `C3 已验收` | 后续分别表示占位、正文未验收、已满足页面完成条件 |
| 英文 | `E0 未创建` | `en/<path>` 不存在 |
| 英文 | `E1 占位` / `E2 正文中` / `E3 已验收` | 后续分别表示占位、镜像正文未验收、已满足页面完成条件 |
| 源码依据 | `S0 未定位` | 蓝图给出责任边界，但尚未定位该页源码证据族 |
| 源码依据 | `S1 已定位，待逐页核对` | 已有候选源码/调查产物，不代表页面结论已经逐条核对 |
| 源码依据 | `S2 已核对` | 页面中的定义、UI/i18n、持久化/调度、最终消费者已逐项留证 |
| 图示 | `G0 待判断` | 尚未逐项判断截图、Mermaid 或“不需要：原因” |
| 图示 | `G1 截图未创建` | 蓝图要求真实有头模式截图，尚未采集 |
| 图示 | `G2 Mermaid 未创建` | 蓝图明确要求流程/状态/数据图，尚未绘制 |
| 图示 | `G3 截图与 Mermaid 未创建` | 两类图示均需要，均未完成 |
| 图示 | `G4 调试图/目录树未创建` | 需要真实脱敏调试产物或 verbose 目录树，尚未完成 |
| 构建 | `B0 不可验证` | 页面或 VitePress 工程尚不存在，不能声称构建通过 |
| 构建 | `B1 未通过` / `B2 已通过` | 已实际执行生产构建并分别记录失败或通过证据 |

图示状态只表达当前已知要求。参数页后续仍须按 `PAGE_GUIDELINES.md` 逐参数判断，简单参数只有写明“不需要：原因”后才能结项。

## 2. 源码证据族

矩阵中的 `S1/<编号>` 表示“候选依据已定位，待成文时逐页核对”，不是源码验收完成。

| 编号 | 当前候选依据 |
| --- | --- |
| `S00` | `BLUEPRINT.md`、`TODO.md`；尚无更具体的逐页源码映射 |
| `S01` | `desktop_qt_ui/ui/main_window.py`、`desktop_qt_ui/locales/en_US.json`、`desktop_qt_ui/locales/zh_CN.json`、`research/desktop-main-navigation.md` |
| `S02` | `desktop_qt_ui/ui/main_page/pages/translation_page.py`、`desktop_qt_ui/ui/widgets/file_list_view.py`、`desktop_qt_ui/services/file_service.py`、`desktop_qt_ui/services/workflow_service.py`、`desktop_qt_ui/ui/main_page/runtime.py`、`desktop_qt_ui/ui/main_page/view.py`、`desktop_qt_ui/services/translation_service.py` |
| `S03` | `desktop_qt_ui/ui/main_page/pages/settings_page.py`、`desktop_qt_ui/ui/main_page/dynamic_settings.py`、`desktop_qt_ui/ui/main_page/settings_tab_layout.json`、`desktop_qt_ui/core/config_models.py`、`desktop_qt_ui/app_logic.py`、`phase0-ui-parameter-fields.json`、`research/phase0-options-i18n-matrix.md` |
| `S04` | `manga_translator/config.py`、`manga_translator/translators/`、`manga_translator/manga_translator.py`、`manga_translator/utils/retry.py` |
| `S05` | `desktop_qt_ui/ui/main_page/pages/env_page.py`、`desktop_qt_ui/ui/main_page/env_management.py`、`desktop_qt_ui/ui/main_page/dynamic_settings.py`、`desktop_qt_ui/ui/secondary_pages/model_selector_dialog.py`、`desktop_qt_ui/ui/secondary_pages/custom_api_params_editor.py`、`desktop_qt_ui/services/config_service.py`、`manga_translator/runtime_api_resolver.py`、`manga_translator/api_key_rotation.py` |
| `S06` | `desktop_qt_ui/ui/secondary_pages/simple_prompt_editor_dialog.py`、`prompt_preview.py`、`ai_colorizer_prompt_editor.py`、`replacements_editor.py`、`rich_text_rules_editor.py`、`batch_edit_panel.py`、`batch_edit_condition_widgets.py`，以及 `desktop_qt_ui/services/batch_edit_*.py`、`dict/ai_ocr_prompt.yaml`、`dict/ai_renderer_prompt.yaml`、`dict/ai_colorizer_prompt.yaml`；具体消费者待逐页收窄 |
| `S07` | `desktop_qt_ui/ui/editor/`、`desktop_qt_ui/editor/`、`desktop_qt_ui/ui/widgets/editor_toolbar.py`、`desktop_qt_ui/ui/widgets/property_panel.py`、`desktop_qt_ui/ui/widgets/region_list_view.py`、`desktop_qt_ui/ui/widgets/rich_text_floating_editor.py`、`research/editor-inventory.md` |
| `S08` | `desktop_qt_ui/services/workflow_service.py`、`desktop_qt_ui/runtime.py`、`manga_translator/manga_translator.py`、`research/workflow-matrix-source-evidence.md` |
| `S09` | `manga_translator/server/static/index.html`、`manga_translator/server/static/admin-new.html`、`manga_translator/server/static/script.js`、`manga_translator/server/static/js/history-gallery.js`、`manga_translator/server/routes/web.py`、`manga_translator/server/routes/auth.py`、`research/phase0-web-user-http.md` |
| `S10` | `manga_translator/args.py`、`research/cli-command-inventory.md`；实际 `--help` 已在调查产物记录 |
| `S11` | `pyproject.toml`、`uv.lock`、`packaging/launch.py`、`Win-*.bat`、`Unix-*.sh`、`packaging/Dockerfile`、`packaging/docker-compose.yml`、`packaging/docker-entrypoint.sh` |
| `S12` | 仓库模块边界、测试目录、打包脚本与发布配置；具体文件待相应开发者页面逐项收窄 |
| `S13` | `manga_translator/server/main.py`、`manga_translator/server/routes/`、`manga_translator/server/request_extraction.py`、`manga_translator/server/to_json.py`、`manga_translator/server/core/task_manager.py`、`manga_translator/server/core/middleware.py`、`manga_translator/server/routes/translation_auth.py`、`research/phase0-web-user-http.md` |
| `S14` | `manga_translator/mode/share.py`、`manga_translator/mode/ws.py`；蓝图列出的 `manga_translator/server/ws_pb2.py` 当前仓库不存在，协议生成来源待后续逐页核对 |
| `S15` | `manga_translator/manga_translator.py`、各阶段模块、`research/phase0-related-files-formats-debug-safety.md`；完整回调/手工路径追踪仍是独立进行中 TODO |
| `S16` | Phase 0 已产出的参数、选项、默认值、工作流、CLI、Web、UI、编辑器、文件与调试调查产物 |

## 3. 页面矩阵

### 3.1 首页与入门

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W001 | `index.md` | C0 未创建 | E0 未创建 | S0/S00 | G0 待判断 | B0 不可验证 |
| W002 | `introduction/product-forms.md` | C0 未创建 | E0 未创建 | S0/S00 | G0 待判断 | B0 不可验证 |
| W003 | `introduction/first-translation.md` | C0 未创建 | E0 未创建 | S1/S01+S02+S08 | G0 待判断 | B0 不可验证 |
| W004 | `introduction/data-and-privacy.md` | C0 未创建 | E0 未创建 | S1/S02+S09+S15 | G0 待判断 | B0 不可验证 |

### 3.2 安装

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W005 | `install/choose-edition.md` | C0 未创建 | E0 未创建 | S1/S11 | G0 待判断 | B0 不可验证 |
| W006 | `install/requirements.md` | C0 未创建 | E0 未创建 | S1/S11 | G0 待判断 | B0 不可验证 |
| W007 | `install/windows-portable.md` | C0 未创建 | E0 未创建 | S1/S11 | G1 截图未创建 | B0 不可验证 |
| W008 | `install/source-windows.md` | C0 未创建 | E0 未创建 | S1/S11 | G0 待判断 | B0 不可验证 |
| W009 | `install/linux-and-macos.md` | C0 未创建 | E0 未创建 | S1/S11 | G0 待判断 | B0 不可验证 |
| W010 | `install/docker.md` | C0 未创建 | E0 未创建 | S1/S11 | G0 待判断 | B0 不可验证 |
| W011 | `install/update-and-version-switching.md` | C0 未创建 | E0 未创建 | S1/S11 | G0 待判断 | B0 不可验证 |
| W012 | `install/uninstall-and-data-cleanup.md` | C0 未创建 | E0 未创建 | S1/S11 | G0 待判断 | B0 不可验证 |

### 3.3 桌面导航与翻译工作区

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W013 | `desktop/navigation-and-language.md` | C0 未创建 | E0 未创建 | S1/S01 | G1 截图未创建 | B0 不可验证 |
| W014 | `desktop/translation/file-list-and-input.md` | C0 未创建 | E0 未创建 | S1/S02 | G1 截图未创建 | B0 不可验证 |
| W015 | `desktop/translation/output-directory-and-workflow.md` | C0 未创建 | E0 未创建 | S1/S02+S08 | G1 截图未创建 | B0 不可验证 |
| W016 | `desktop/translation/progress-stop-and-task-state.md` | C0 未创建 | E0 未创建 | S1/S02 | G3 截图与 Mermaid 未创建 | B0 不可验证 |

### 3.4 设置与参数

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W017 | `desktop/settings/index.md` | C0 未创建 | E0 未创建 | S1/S03 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W018 | `desktop/settings/shell-description-import-export.md` | C0 未创建 | E0 未创建 | S1/S03 | G1 截图未创建 | B0 不可验证 |
| W019 | `desktop/settings/general-and-app.md` | C0 未创建 | E0 未创建 | S1/S03 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W020 | `desktop/settings/cli-batch-and-output.md` | C0 未创建 | E0 未创建 | S1/S03+S10 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W021 | `desktop/settings/detection.md` | C0 未创建 | E0 未创建 | S1/S03+S15 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W022 | `desktop/settings/ocr-filter-and-merge.md` | C0 未创建 | E0 未创建 | S1/S03+S04 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W023 | `desktop/settings/translation.md` | C0 未创建 | E0 未创建 | S1/S03+S04 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W024 | `desktop/settings/mask-and-inpainting.md` | C0 未创建 | E0 未创建 | S1/S03+S15 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W025 | `desktop/settings/typesetting-and-rendering.md` | C0 未创建 | E0 未创建 | S1/S03+S15 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W026 | `desktop/settings/upscale-and-colorization.md` | C0 未创建 | E0 未创建 | S1/S03+S15 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W027 | `desktop/settings/mode-specific.md` | C0 未创建 | E0 未创建 | S1/S03+S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |

### 3.5 翻译器

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W028 | `desktop/translator/selection-and-languages.md` | C0 未创建 | E0 未创建 | S1/S03+S04+S05 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W029 | `desktop/translator/engine-dispatch.md` | C0 未创建 | E0 未创建 | S1/S04 | G2 Mermaid 未创建 | B0 不可验证 |
| W030 | `desktop/translator/context-and-prompts.md` | C0 未创建 | E0 未创建 | S1/S04+S06 | G2 Mermaid 未创建 | B0 不可验证 |
| W031 | `desktop/translator/glossary-stream-and-linebreak.md` | C0 未创建 | E0 未创建 | S1/S04+S06 | G2 Mermaid 未创建 | B0 不可验证 |
| W032 | `desktop/translator/retry-rate-limit-and-quality.md` | C0 未创建 | E0 未创建 | S1/S04+S05 | G2 Mermaid 未创建 | B0 不可验证 |
| W033 | `desktop/translator/translation-chain.md` | C0 未创建 | E0 未创建 | S1/S04+S05 | G2 Mermaid 未创建 | B0 不可验证 |

### 3.6 API 管理

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W034 | `desktop/api-management/feature-selectors.md` | C0 未创建 | E0 未创建 | S1/S05 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W035 | `desktop/api-management/provider-tabs.md` | C0 未创建 | E0 未创建 | S1/S05 | G1 截图未创建 | B0 不可验证 |
| W036 | `desktop/api-management/credentials-addresses-models.md` | C0 未创建 | E0 未创建 | S1/S05 | G1 截图未创建 | B0 不可验证 |
| W037 | `desktop/api-management/slots-and-rotation.md` | C0 未创建 | E0 未创建 | S1/S05 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W038 | `desktop/api-management/failures-cooldown-and-recovery.md` | C0 未创建 | E0 未创建 | S1/S05 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W039 | `desktop/api-management/connection-tests-and-model-list.md` | C0 未创建 | E0 未创建 | S1/S05 | G1 截图未创建 | B0 不可验证 |
| W040 | `desktop/api-management/custom-request-parameters.md` | C0 未创建 | E0 未创建 | S1/S05 | G1 截图未创建 | B0 不可验证 |
| W041 | `desktop/api-management/presets-and-persistence.md` | C0 未创建 | E0 未创建 | S1/S05 | G1 截图未创建 | B0 不可验证 |

### 3.7 提示词

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W042 | `desktop/prompts/list-apply-and-preview.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W043 | `desktop/prompts/structured-editor-and-format.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W044 | `desktop/prompts/system-and-translation-prompts.md` | C0 未创建 | E0 未创建 | S1/S04+S06 | G1 截图未创建 | B0 不可验证 |
| W045 | `desktop/prompts/ai-ocr-prompt.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W046 | `desktop/prompts/ai-renderer-prompt.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W047 | `desktop/prompts/ai-colorizer-prompt.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |

### 3.8 替换规则与富文本规则

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W048 | `desktop/replacement-rules/table-groups-and-order.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W049 | `desktop/replacement-rules/raw-yaml-regex-and-save.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W050 | `desktop/rich-text-rules/table-raw-and-match.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W051 | `desktop/rich-text-rules/styles-and-presets.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |

### 3.9 批量管理

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W052 | `desktop/batch-management/schemes-crud.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W053 | `desktop/batch-management/conditions.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W054 | `desktop/batch-management/actions-and-order.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |
| W055 | `desktop/batch-management/preview-apply-restore.md` | C0 未创建 | E0 未创建 | S1/S06 | G1 截图未创建 | B0 不可验证 |

### 3.10 编辑器

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W056 | `desktop/editor/layout-and-file-list.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W057 | `desktop/editor/toolbar-and-menus.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W058 | `desktop/editor/display-compare-and-arrange.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W059 | `desktop/editor/canvas-tools-and-selection.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W060 | `desktop/editor/region-list-and-text-editing.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W061 | `desktop/editor/text-properties.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W062 | `desktop/editor/style-properties.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W063 | `desktop/editor/mask-paint-and-clone-stamp.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W064 | `desktop/editor/floating-rich-text.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W065 | `desktop/editor/shortcuts.md` | C0 未创建 | E0 未创建 | S1/S07 | G1 截图未创建 | B0 不可验证 |
| W066 | `desktop/editor/import-export-and-writeback.md` | C0 未创建 | E0 未创建 | S1/S07+S15 | G3 截图与 Mermaid 未创建 | B0 不可验证 |

### 3.11 九个工作流

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W067 | `workflows/normal.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W068 | `workflows/export-translation.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W069 | `workflows/export-original.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W070 | `workflows/translate-json-only.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W071 | `workflows/import-translation-and-render.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W072 | `workflows/colorize-only.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W073 | `workflows/upscale-only.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W074 | `workflows/inpaint-only.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W075 | `workflows/replace-translation.md` | C0 未创建 | E0 未创建 | S1/S08 | G3 截图与 Mermaid 未创建 | B0 不可验证 |

### 3.12 Web 用户端

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W076 | `web/launch-and-access.md` | C0 未创建 | E0 未创建 | S1/S09+S10 | G1 截图未创建 | B0 不可验证 |
| W077 | `web/login-language-and-session.md` | C0 未创建 | E0 未创建 | S1/S09+S13 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W078 | `web/upload-config-and-translate.md` | C0 未创建 | E0 未创建 | S1/S09+S13 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W079 | `web/progress-results-and-history.md` | C0 未创建 | E0 未创建 | S1/S09+S13 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W080 | `web/accounts-permissions-and-api-keys.md` | C0 未创建 | E0 未创建 | S1/S09+S13 | G3 截图与 Mermaid 未创建 | B0 不可验证 |
| W081 | `web/resources-fonts-and-prompts.md` | C0 未创建 | E0 未创建 | S1/S09+S13 | G1 截图未创建 | B0 不可验证 |
| W082 | `web/administrator-interface.md` | C0 未创建 | E0 未创建 | S1/S09+S13 | G1 截图未创建 | B0 不可验证 |
| W083 | `web/deployment-security-and-troubleshooting.md` | C0 未创建 | E0 未创建 | S1/S09+S11+S13 | G0 待判断 | B0 不可验证 |

### 3.13 CLI

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W084 | `cli/command-structure.md` | C0 未创建 | E0 未创建 | S1/S10 | G1 截图未创建 | B0 不可验证 |
| W085 | `cli/local-input-output.md` | C0 未创建 | E0 未创建 | S1/S10 | G1 截图未创建 | B0 不可验证 |
| W086 | `cli/configuration-overrides.md` | C0 未创建 | E0 未创建 | S1/S03+S10 | G0 待判断 | B0 不可验证 |
| W087 | `cli/workflow-and-file-modes.md` | C0 未创建 | E0 未创建 | S1/S08+S10 | G0 待判断 | B0 不可验证 |
| W088 | `cli/subprocess-memory-and-recovery.md` | C0 未创建 | E0 未创建 | S1/S10 | G0 待判断 | B0 不可验证 |
| W089 | `cli/output-debugging-and-exit-codes.md` | C0 未创建 | E0 未创建 | S1/S10+S15 | G4 调试图/目录树未创建 | B0 不可验证 |
| W090 | `cli/web-ws-and-shared-modes.md` | C0 未创建 | E0 未创建 | S1/S10+S14 | G0 待判断 | B0 不可验证 |

### 3.14 开发者与 HTTP API

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W091 | `developer/architecture-and-code-boundaries.md` | C0 未创建 | E0 未创建 | S1/S12 | G2 Mermaid 未创建 | B0 不可验证 |
| W092 | `developer/adding-or-changing-a-feature.md` | C0 未创建 | E0 未创建 | S1/S12 | G0 待判断 | B0 不可验证 |
| W093 | `developer/tests-and-code-quality.md` | C0 未创建 | E0 未创建 | S1/S12 | G0 待判断 | B0 不可验证 |
| W094 | `developer/packaging-and-release.md` | C0 未创建 | E0 未创建 | S1/S11+S12 | G0 待判断 | B0 不可验证 |
| W095 | `developer/web-server-ports-and-deployment.md` | C0 未创建 | E0 未创建 | S1/S11+S13 | G2 Mermaid 未创建 | B0 不可验证 |
| W096 | `developer/http-api/authentication-and-errors.md` | C0 未创建 | E0 未创建 | S1/S13 | G2 Mermaid 未创建 | B0 不可验证 |
| W097 | `developer/http-api/translation-endpoints.md` | C0 未创建 | E0 未创建 | S1/S13 | G2 Mermaid 未创建 | B0 不可验证 |
| W098 | `developer/http-api/streaming-protocol.md` | C0 未创建 | E0 未创建 | S1/S13 | G2 Mermaid 未创建 | B0 不可验证 |
| W099 | `developer/http-api/batch-export-import-process.md` | C0 未创建 | E0 未创建 | S1/S13 | G2 Mermaid 未创建 | B0 不可验证 |
| W100 | `developer/http-api/config-env-and-resources.md` | C0 未创建 | E0 未创建 | S1/S13 | G0 待判断 | B0 不可验证 |
| W101 | `developer/http-api/history-files-and-download-tickets.md` | C0 未创建 | E0 未创建 | S1/S13 | G2 Mermaid 未创建 | B0 不可验证 |
| W102 | `developer/http-api/admin-users-groups-quota-audit.md` | C0 未创建 | E0 未创建 | S1/S13 | G2 Mermaid 未创建 | B0 不可验证 |
| W103 | `developer/internal-shared-and-websocket.md` | C0 未创建 | E0 未创建 | S1/S14 | G2 Mermaid 未创建 | B0 不可验证 |
| W104 | `developer/related-projects-and-links.md` | C0 未创建 | E0 未创建 | S0/S00 | G0 待判断 | B0 不可验证 |

### 3.15 调试目录与产物

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W105 | `debugging/folder-naming-and-overview.md` | C0 未创建 | E0 未创建 | S1/S15 | G4 调试图/目录树未创建 | B0 不可验证 |
| W106 | `debugging/input-detection-and-rearrangement.md` | C0 未创建 | E0 未创建 | S1/S15 | G4 调试图/目录树未创建 | B0 不可验证 |
| W107 | `debugging/ocr-and-text-regions.md` | C0 未创建 | E0 未创建 | S1/S15 | G4 调试图/目录树未创建 | B0 不可验证 |
| W108 | `debugging/mask-inpainting-and-rendering.md` | C0 未创建 | E0 未创建 | S1/S15 | G4 调试图/目录树未创建 | B0 不可验证 |
| W109 | `debugging/special-workflows-and-websocket.md` | C0 未创建 | E0 未创建 | S1/S08+S14+S15 | G4 调试图/目录树未创建 | B0 不可验证 |
| W110 | `debugging/how-to-read-and-share-a-debug-run.md` | C0 未创建 | E0 未创建 | S1/S15 | G4 调试图/目录树未创建 | B0 不可验证 |

### 3.16 参考索引

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W111 | `reference/settings-index.md` | C0 未创建 | E0 未创建 | S1/S03+S16 | G0 待判断 | B0 不可验证 |
| W112 | `reference/options-i18n-matrix.md` | C0 未创建 | E0 未创建 | S1/S03+S16 | G0 待判断 | B0 不可验证 |
| W113 | `reference/workflow-matrix.md` | C0 未创建 | E0 未创建 | S1/S08+S16 | G2 Mermaid 未创建 | B0 不可验证 |
| W114 | `reference/source-evidence-index.md` | C0 未创建 | E0 未创建 | S1/S16 | G0 待判断 | B0 不可验证 |
| W115 | `reference/debug-artifact-index.md` | C0 未创建 | E0 未创建 | S1/S15+S16 | G4 调试图/目录树未创建 | B0 不可验证 |

### 3.17 故障排查

| ID | 页面路径 | 中文 | 英文 | 源码依据 | 图示 | 构建 |
| --- | --- | --- | --- | --- | --- | --- |
| W116 | `troubleshooting/installation-and-startup.md` | C0 未创建 | E0 未创建 | S1/S10+S11 | G0 待判断 | B0 不可验证 |
| W117 | `troubleshooting/model-gpu-and-memory.md` | C0 未创建 | E0 未创建 | S1/S03+S04+S11 | G0 待判断 | B0 不可验证 |
| W118 | `troubleshooting/api-auth-rate-limit-and-timeout.md` | C0 未创建 | E0 未创建 | S1/S05+S09+S13 | G0 待判断 | B0 不可验证 |
| W119 | `troubleshooting/output-json-and-rendering.md` | C0 未创建 | E0 未创建 | S1/S07+S08+S15 | G0 待判断 | B0 不可验证 |
| W120 | `troubleshooting/privacy-cleanup-and-log-sharing.md` | C0 未创建 | E0 未创建 | S1/S09+S11+S15 | G0 待判断 | B0 不可验证 |

## 4. 基线汇总与维护规则

| 项目 | 2026-08-06 基线 |
| --- | --- |
| 蓝图第 3 节页面路径 | 114 |
| TODO 第 5 节页面路径 | 120；比蓝图树多 6 个 `debugging/` 页面 |
| 矩阵页面行 | 120；覆盖蓝图与 TODO 的并集 |
| 中文页面文件 | 0 / 120 |
| 英文页面文件 | 0 / 120 |
| 源码逐页验收 | 0 / 120；117 行已定位候选证据族，3 行仍为 S0 |
| 图示验收 | 0 / 120；当前只记录明确要求与待判断项 |
| 生产构建 | 不可验证；`doc/wiki/package.json` 与 VitePress 工程尚未创建 |

后续维护时遵守以下规则：

1. `W001` 至 `W120` 是稳定行 ID；页面改名时保留 ID，并同步修改 `BLUEPRINT.md`、`TODO.md` 与本表。
2. 新增蓝图页面时追加新 ID，不复用已删除页面的 ID。
3. 中文、英文、源码、图示和构建必须分别更新；一种语言完成不得带动另一种语言状态。
4. `S2` 必须对应页面内可追溯的源码依据；仅列候选路径保持 `S1`。
5. `G0` 不是“不需要”。确认不需要图示时写明具体原因；需要图示时改为相应未创建/进行中/已验收状态。
6. `B2` 必须附实际命令、日期与结果；依赖缺失或未收集时不得标为通过。
