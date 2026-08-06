# Phase 0：桌面二级页面、弹窗与状态源码清单

> 范围：`TODO.md` 第 74 行；调查日期：2026-08-06；证据等级：静态源码和 locale 已核对，未启动 GUI。
>
> 本清单是后续功能页的证据索引，不是终端用户 Wiki 页面，也不取代 `BLUEPRINT.md` 要求的页面归属。

## 1. 范围与归属

- 本清单覆盖桌面端 `desktop_qt_ui/` 中的嵌入式二级面板、可见对话框/选择器、确认与错误弹窗，以及这些界面直接显示的状态。
- Web 用户功能、HTTP 路由、鉴权和 Web 状态不在本项范围；它们归 `TODO.md` 第 81 行。
- 桌面主导航和翻译任务的主状态机归第 73 行；编辑器菜单、画布工具、属性和快捷键归第 75 行。本清单仅登记这些范围调用的通用弹窗、浮层和 Toast，防止遗漏交界。
- 后续正文不得建立独立的“弹窗百科”：按 `BLUEPRINT.md` 5.3–5.5，模型/API 弹窗写在 API 管理页，提示词弹窗写在提示词页，规则/样式弹窗写在规则页，批量条件部件写在批量页。

## 2. i18n 证据规则

桌面专用 UI 通过调用方传入的 `self._t(key)` 获取文字；键和值的权威来源分别为 `desktop_qt_ui/locales/en_US.json` 与 `desktop_qt_ui/locales/zh_CN.json`。下表列出本清单反复使用的证据样本；其余实体在各自源码行继续以 `_t(...)` 调用为准。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Select Folder` | Select Folder | 选择文件夹 |
| `Search models...` | Search models... | 搜索模型... |
| `Select Model` | Select Model | 选择模型 |
| `Edit Prompt` | Edit Prompt | 编辑提示词 |
| `Edit Custom API Params` | Edit Custom API Params | 编辑自定义 API 参数 |
| `Edit Rich Text Style` | Edit Rich Text Style | 编辑富文本样式 |
| `Loaded successfully` | Loaded successfully | 加载成功 |
| `Saved successfully` | Saved successfully | 保存成功 |
| `Save failed` | Save failed | 保存失败 |
| `JSON format error` | JSON format error | JSON 格式错误 |
| `All changes saved` | All changes saved | 所有修改已保存 |
| `API slot unavailable marker` | API channel unavailable | API 通道不可用 |
| `API slot cooldown marker` | API channel cooling down | API 通道冷却中 |
| `Restore API channel` | Restore | 恢复 |
| `Cancel` / `OK` / `Delete` | Cancel / OK / Delete | 取消 / 确定 / 删除 |

`themed_message_box.py` 将 `QMessageBox` 的 information、warning、critical 和 question 替换为 Fluent 对话框（`themed_message_box.py:222`、`262`）；该层的 `OK`、`Yes`、`No`、`Cancel`、`Close` 按钮映射目前是代码常量，不能把它误记为页面的 `_t` key。

## 3. 嵌入式二级页面

| 归属功能页 | 二级面板与入口 | 主要 UI/i18n 证据 | 已知状态与下一页归属 |
| --- | --- | --- | --- |
| 提示词管理 | `PromptPreviewPanel`，由 `ui/main_page/pages/prompt_page.py:74` 创建；列表的双击、应用和编辑信号在 `:85`–`:94` 接线 | 页面标题 `Prompt Management`、按钮 `New`、`Copy`、`Rename`、`Delete`、`Apply Selected Prompt` 均经 `_t(...)` | 列表选择、创建/复制/重命名/删除的结果写入 `prompt_status_label`；编辑对话框的加载、格式、保存状态见第 4 节 `PromptEditorDialog`。归提示词页。 |
| 替换规则 | `ReplacementsEditorPanel`，由 `ui/main_page/pages/replacements_page.py:27` 嵌入 | `ui/secondary_pages/replacements_editor.py:112`–`:154` 使用 `Add Rule`、`Enable`、`Regex`、`Restore Default`、`Filter:` 等 `_t` key | 表格/Raw 切换、表格控件禁用、文件不存在、加载错误、自动保存、YAML 语法错误、恢复成功/失败在 `replacements_editor.py:600`–`:777`。归替换规则页。 |
| 富文本规则 | `RichTextRulesEditorPanel`，由 `ui/main_page/pages/rich_text_rules_page.py:27` 嵌入 | `rich_text_rules_editor.py:497`–`:503` 和 `:825`–`:858` 使用 `Add Rule`、`Enable`、`Regex`、`Restore Default`、表格/Raw 文案 | `Load error`、`Saving...`、`Save error`、`All changes saved` 在 `:659`–`:725`；Raw YAML 校验会显示 `YAML Error`。样式编辑会打开 `RichTextStyleDialog`。归富文本规则页。 |
| 批量管理 | `BatchEditPanel`，由 `ui/main_page/pages/batch_edit_page.py:28` 嵌入 | `batch_edit_panel.py:144`–`:190` 使用 `Scheme:`、`New`、`Rename`、`Duplicate`、`Match all`、`Match any`、`Add condition` 等 `_t` key | 预览无匹配时禁用 Apply（`:510`–`:514`）；预览/应用/恢复分别显示可取消进度，取消、写入错误、成功摘要在 `:646`–`:753`。归批量管理页。 |

## 4. 对话框与选择器清单

| 归属 | 对话框/选择器 | 定义与发起源码 | UI/i18n 与状态证据 |
| --- | --- | --- | --- |
| 通用桌面层 | `FluentSecondaryDialog` | `ui/secondary_pages/fluent_dialog.py:9`–`:116`；所有下列 `FluentSecondaryDialog` 子类共享 | 父窗口归一化、无边框拖动、显示前按屏幕可用区裁剪。接受/拒绝以 `DialogCode` 返回；每个具体页面负责自己的按钮文案。 |
| API 管理 | `ModelSelectorDialog` | 定义 `ui/secondary_pages/model_selector_dialog.py:21`；获取模型后在 `ui/main_page/env_management.py:1411`–`:1468` 调用 | `Search models...`、`OK`、`Cancel`；无选择时 OK 禁用（`model_selector_dialog.py:71`–`:103`），单个过滤结果自动选中（`:88`–`:97`）。获取失败、空模型和取消在 API 管理页记录。 |
| API 管理 | `ThemedProgressDialog`（测试当前项、批量测试、获取模型） | 定义 `ui/secondary_pages/themed_progress_dialog.py:16`；调用 `env_management.py:1245`–`:1313`、`:1349`–`:1408`、`:1411`–`:1468` | `setRange` 在 indeterminate 与 determinate 条之间切换，关闭按钮执行 reject/cancel（`themed_progress_dialog.py:72`–`:124`）。异步任务会监听 rejected 并 cancel（`env_management.py:68`–`:129`）。 |
| API 管理 | `CustomApiParamsEditorDialog` | 定义 `ui/secondary_pages/custom_api_params_editor.py:228`；入口 `env_management.py:1321`–`:1346` | `Edit Custom API Params`、预设新增/重命名/删除、结构化/Raw；状态为加载成功、Load failed、JSON format error、JSON root error、重复参数、Save failed、保存成功（`custom_api_params_editor.py:616`–`:740`）。默认预设时重命名/删除禁用（`:495`–`:498`）。 |
| API 管理 | `ThemedTextInputDialog` | 定义与 `themed_get_text` 工厂在 `ui/secondary_pages/themed_text_input_dialog.py:10`–`:75`；API 预设入口 `env_management.py:1505`–`:1536` | 调用方传入 title、label、OK、Cancel；返回 `(text, accepted)`。空名、同名覆盖、创建/删除失败由 API 管理的 warning/question/critical 反馈。 |
| API 管理 | API 槽状态提示与恢复按钮 | 创建 `ui/main_page/env_management.py:512`–`:549`；状态来源 `manga_translator/api_key_rotation.py:173`–`:204`、`:330`–`:350` | 只有 `cooldown`/`unavailable` 槽显示状态条；恢复按钮调用 `clear_api_status` 后重建页。运行态还可能是 `available` 或 `failed`；汇总文案 key 在两个 locale 的 `API status line*` 和 `API status detail*` 条目。 |
| 设置 | `FilterListEditorDialog` | 定义 `ui/secondary_pages/filter_list_editor.py:57`；动态设置入口 `ui/main_page/dynamic_settings.py:389`–`:397` | `Edit Filter List`、`Refresh`、`Cancel`、`Save`；读取/JSON 根/JSON 格式/写入失败和成功状态在 `filter_list_editor.py:225`–`:300`。归 OCR 过滤设置页。 |
| 设置 | `SimplePromptEditorDialog`（AI OCR、AI Renderer） | 定义 `ui/secondary_pages/simple_prompt_editor_dialog.py:14`；固定提示词分流 `dynamic_settings.py:400`–`:505` | 调用配置路径键 `ocr.ai_ocr_prompt_path`、`render.ai_renderer_prompt_path` 的 label/description；显示文件路径提示，Save 成功 accept，异常用 `Error` 主题错误框（`simple_prompt_editor_dialog.py:45`–`:110`）。 |
| 设置/提示词 | `AIColorizerPromptEditorDialog` | 定义 `ui/secondary_pages/ai_colorizer_prompt_editor.py:136`；设置入口 `dynamic_settings.py:476`–`:505`，提示词管理入口 `ui/main_page/layout.py:236`–`:252` | `Edit Prompt`、`Cancel`、`Save`；模板/Raw 文本、序列化错误、格式错误、Save failed、加载/保存成功在 `ai_colorizer_prompt_editor.py:159`–`:216`、`:644`–`:670`。 |
| 提示词管理 | `PromptEditorDialog` | 定义 `ui/secondary_pages/prompt_preview.py:824`；由 `ui/main_page/layout.py:236`–`:252` 发起 | `Edit Prompt`、`Cancel`、`Save`；结构化与自由编辑模式，加载错误/成功、序列化错误、JSON/YAML 格式错误、Save failed/成功在 `prompt_preview.py:909`–`:927`、`:1718`–`:1765`。 |
| 提示词管理 | `PersonGlossaryEntryDialog` | 定义 `ui/secondary_pages/prompt_preview.py:715`；新增/编辑入口 `prompt_preview.py:1320`–`:1361` | `Category`、`Original`、`Translation`、`Nicknames`、`Introduction`、`Cancel`、`Save` 均 `_t`；类别改为非 `Person` 时隐藏人物专用字段（`:804`–`:809`）。 |
| 富文本规则、批量管理 | `RichTextStyleDialog` | 定义 `ui/secondary_pages/rich_text_rules_editor.py:411`；规则编辑器入口 `:784`–`:790`，批量条件入口 `ui/secondary_pages/batch_edit_condition_widgets.py:806`–`:819` | `Edit Rich Text Style`、`Reset`、`Cancel`、`OK`；样式序列化异常用 `Invalid Style` 警告（`rich_text_rules_editor.py:459`–`:465`）。 |
| 批量管理与通用操作 | `show_error_dialog` / `themed_information` / `themed_warning` / `themed_critical` / `themed_question` | 定义 `ui/secondary_pages/themed_message_box.py:159`–`:270`；批量使用见 `batch_edit_panel.py:455`–`:481`、`:646`–`:753`；提示词与 API 调用见 `ui/main_page/layout.py`、`env_management.py` | 统一替代 `QMessageBox`，支持 Yes/No、OK、Cancel/Close、长错误详情和可选额外动作。每个调用方保留具体确认、取消、错误文案。 |
| 文件/目录选择 | `FolderDialog` | 定义 `ui/secondary_pages/folder_dialog.py:234`；工厂 `:1199`–`:1217`，调用者 `desktop_qt_ui/app_logic.py:1601` 与 `desktop_qt_ui/editor/editor_logic.py:16` | `Select Folder`、`(Multi-select)`、`Cancel`。初始 OK 禁用；无选择时会以当前目录作为选择；后退/前进、路径编辑、无效路径 warning、单/多选文案在 `folder_dialog.py:875`–`:1028`。 |
| 通用文件选择 | Qt `QFileDialog` | 输出目录 `desktop_qt_ui/app_logic.py:332`；配置导入导出 `:1393`–`:1506`；导入图像 `ui/main_page/env_management.py:1612` 与 `editor/editor_logic.py:231` | 系统文件选择器，标题由调用者 `_t(...)` 传入。平台原生外观与按钮文字不是本项目 locale 证据。 |
| 编辑器交界 | 未保存编辑的三按钮 `Dialog` | `desktop_qt_ui/editor/controller_document_service.py:269`–`:289` | 固定中文 `导出图片` / `不保存` / `取消`，返回 export/discard/cancel；此处不是 `_t`，后续编辑器页必须按源码原文记录并安排运行核对。 |
| 编辑器交界 | `ScreenColorPicker` 覆盖层 | 定义/启动 `ui/widgets/color_picker.py:49`–`:99`、`:964`–`:989` | 全屏工具窗口，不是模态 Dialog；左键选色，右键或 Esc 取消（`:227`–`:244`）。色板本体为 `ColorPickerWidget` / `FlyoutViewBase`，归编辑器样式属性页。 |
| 编辑器交界 | `ToastNotification` / `ToastManager` | 定义 `ui/widgets/toast_notification.py:14`–`:169`；编辑器完成映射 `editor/editor_controller.py:420`–`:438` | 非阻塞 InfoBar 状态分为 success、error、info，可关闭，`duration <= 0` 持续显示。编辑器 OCR/翻译完成时由 status 映射；文案由任务结果提供，当前并非完整 i18n 表。 |
| 应用/编辑器交界 | 任务完成和未完成导出确认 | `ui/main_window.py:612`–`:645`、`:807`–`:829` | 两者都走 `show_error_dialog`；前者使用 `_t`，后者当前硬编码中文。归翻译进度页和编辑器导出页，运行时确认需要覆盖最小化窗口和未完成导出分支。 |

## 5. 状态覆盖矩阵

| 状态域 | 已固定的源码状态 | 用户可见载体 | 后续正文位置 |
| --- | --- | --- | --- |
| 弹窗通用生命周期 | accepted、rejected/cancel、关闭；确认框的 Yes/No/OK/Cancel/Close | Fluent dialog / 主题消息框 | 所属功能页，见第 4 节归属 |
| API 通道 | `available`、`failed`、`cooldown`、`unavailable`；恢复会清除内存状态 | API 槽状态条、恢复按钮、测试结果弹窗 | API 管理：失败、冷却和恢复页 |
| API 异步动作 | 测试/批量测试/获取模型的进行中、取消、成功、失败、无模型 | `ThemedProgressDialog`、结果/error dialog | API 管理：连接测试和模型列表页 |
| 文件/JSON 编辑器 | 加载成功、加载失败、JSON/YAML 格式错误、根类型错误、保存失败、保存成功 | Filter、Custom API、Prompt、Colorizer 的状态标签或错误框 | 各自设置、API 或提示词页 |
| 规则编辑器 | 表格/Raw 模式、Raw 时表格控件禁用、Saving、自动保存/全部已保存、加载/保存错误、恢复默认结果 | Replacement / Rich Text 面板状态标签和警告框 | 替换规则、富文本规则页 |
| 批量管理 | 无匹配（Apply 禁用）、预览/应用/恢复进度、取消、写入错误、已更新/已恢复 | 批量面板状态标签、进度框、错误详情框 | 批量管理页 |
| 文件夹选择 | 初始不可确认、当前目录回退、单选、多选、后退/前进可用性、路径无效 | FolderDialog 标签、按钮、警告框 | 文件/输出目录相关页 |
| 编辑器公共反馈 | success/error/info Toast；未保存编辑的 export/discard/cancel；选色 pick/cancel | InfoBar、三按钮 Dialog、全屏取色覆盖层 | 编辑器对应页面（第 75 行任务） |

## 6. 未能仅靠源码确认的运行时证据

- 每个动态设置动作是否都在当前配置、平台和依赖条件下可见；尤其是 AI OCR/渲染/上色提示词编辑入口。
- Fluent 对话框的实际模态、屏幕裁剪、焦点、Esc、窗口关闭和语言切换后的所有标签；静态代码仅能证明调用和预期分支。
- `QFileDialog` 的原生 UI、Windows 按钮文案与本项目语言切换的一致性。
- API 槽在真实 429、永久配置错误、恢复、重新测试以及冷却到期后的状态条刷新。
- 批量预览/写回/恢复的真实进度、取消边界、`.bak` 行为和编辑器冲突重载。
- 任务完成确认与未完成导出确认的分支，以及 Toast 中工作线程返回的实际文案。

这些事项应在未来有头模式、脱敏配置的截图与运行验证任务中记录；本阶段不启动界面、不截图、不生成用户教程。

## 7. 可复现的静态核对

```powershell
rg -n "^class " desktop_qt_ui/ui/secondary_pages -g "*.py"
rg -n "from ui\.secondary_pages|secondary_pages\." desktop_qt_ui -g "*.py"
rg -n "get_api_status|clear_api_status|record_api_(success|failure)" desktop_qt_ui/ui/main_page/env_management.py manga_translator/api_key_rotation.py
```

核对基线：`desktop_qt_ui/ui/secondary_pages/` 的全部可见对话框类均已在第 4 节列出；嵌入式可见面板均已在第 3 节列出。内部值编辑小部件、表格行、委托和布局辅助类不单列为页面或对话框，其所属面板已标明。
