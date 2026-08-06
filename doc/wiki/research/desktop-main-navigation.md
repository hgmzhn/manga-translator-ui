# 桌面主页面与导航清单

> Phase 0 数据源；调查日期：2026-08-06。
>
> 范围仅限桌面窗口一级导航、其承载的主页面，以及主题/语言切换与该导航的连接点。二级页面、弹窗、编辑器内部导航、工作流状态和快捷键由其他 Phase 0 项目处理。

## 固定清单

`MainWindow._register_main_interfaces()` 按下表顺序调用 `addSubInterface`。前七项未传 `position`，因此本清单只将其称为“常规导航项”，不把框架默认位置写成项目自定义行为。`Editor View` 是单独初始化的底部导航项，不属于这七个 `MainView.page_widgets` 页面。

| 顺序 | 页面键 / 入口 | UI 调用 key | English 实际值 | 简体中文实际值 | 图标 | 承载组件与来源 | 注册后的对象名 / 位置 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `translation` | `Translation Interface` | Translation Interface | 翻译界面 | `FIF.HOME` | `translation_interface` 包装 `create_translation_page()` 结果；`ui/main_page/pages/translation_page.py` | `main_translation_page`；常规导航项 |
| 2 | `settings` | `Settings` | Settings | 设置 | `FIF.SETTING` | `create_settings_page()`；`ui/main_page/pages/settings_page.py` | `main_settings_page`；常规导航项 |
| 3 | `env` | `API Management` | API Management | API 管理 | `FIF.CONNECT` | `create_env_page()`；`ui/main_page/pages/env_page.py` | `main_env_page`；常规导航项 |
| 4 | `prompts` | `Prompt Management` | Prompt Management | 提示词管理 | `FIF.DOCUMENT` | `create_prompt_page()`；`ui/main_page/pages/prompt_page.py` | `main_prompts_page`；常规导航项 |
| 5 | `replacements` | `Replacement Rules` | Replacement Rules | 替换规则 | `FIF.EDIT` | `create_replacements_page()`；`ui/main_page/pages/replacements_page.py` | `main_replacements_page`；常规导航项 |
| 6 | `rich_text_rules` | `Rich Text Rules` | Rich Text Rules | 富文本规则 | `FIF.FONT` | `create_rich_text_rules_page()`；`ui/main_page/pages/rich_text_rules_page.py` | `main_rich_text_rules_page`；常规导航项 |
| 7 | `batch_edit` | `Batch Management` | Batch Management | 批量管理 | `FIF.LIBRARY` | `create_batch_edit_page()`；`ui/main_page/pages/batch_edit_page.py` | `main_batch_edit_page`；常规导航项 |
| 8 | `Editor View` | `Editor View` | Editor View | 编辑器视图 | `FIF.EDIT` | `EditorView`；`ui/editor/view.py` | `editor_page`；显式 `NavigationItemPosition.BOTTOM` |

### 固定边界

- 初始选中页面是 `translation_interface`：注册完成后调用 `switchTo(self.main_view.translation_interface)`。
- `MainView.page_widgets` 只映射前七个键。内部调用 `_switch_content_page()` 时，已设置的 navigation switcher 按键取出对应组件并调用 `switchTo()`。
- 顶部菜单栏不作为导航来源：`MainWindow` 注释明确说明其不显示，相关动作对象仅保留给内部行为。
- 侧栏代码设定展开宽度 `200`、收起完成后更新指示器、隐藏返回按钮；源码注释说明默认收起为 `48px` 图标栏。

## 页面激活时的刷新范围

`stackedWidget.currentChanged` 通过 `_main_pages_by_widget` 识别前七个页面，再调用 `_on_main_page_activated()`；内部 `_switch_main_page()` 也在切换后调用该方法。该方法的当前分支如下。

| 页面键 | 源码中的页面激活动作 |
| --- | --- |
| `translation` | 无专门刷新分支。 |
| `settings` | 仅在设置 UI 尚未就绪时，用当前配置 `model_dump()` 创建参数控件。 |
| `env` | 刷新 API 分组。 |
| `prompts` | 刷新提示词管理器。 |
| `replacements` | 若面板存在，刷新替换规则编辑器。 |
| `rich_text_rules` | 若面板存在，刷新富文本规则编辑器。 |
| `batch_edit` | 将当前文件目录快照交给面板后刷新。 |
| `Editor View` | 不在上述七页映射或激活分支中；其初始化、打开文件和内部状态属于编辑器范围。 |

## 主题与语言连接点

- 主题和语言控件位于主页面的 General 设置区，而不是侧栏项。`theme_registry.py` 当前固定主题值为 `light`、`dark`、`gray`、`ocean`、`forest`、`sunset`、`rose`、`system`，默认值是 `light`。
- 语言下拉取 `I18nManager.get_available_locales()` 的有序映射：`zh_CN`、`zh_TW`、`en_US`、`ja_JP`、`ko_KR`、`es_ES`。本清单的双语值直接核对 `en_US.json` 与 `zh_CN.json`，没有从 key 自行翻译。
- 选择语言会发出 `language_change_requested`；`MainWindow._change_language()` 更新 `app.ui_language` 并保存配置，随后调用 `_refresh_ui_texts()`。后者刷新主页面文本、七个常规导航项及其收起时提示，并刷新编辑器页面自身文本；该路径没有调用 `switchTo()`。当前页保持的实际 UI 效果仍应在后续有头模式阶段验证。
- `_refresh_navigation_texts()` 的字典恰好覆盖前七个页面键。底部编辑器导航项没有被保存到 `_main_navigation_items`，且该字典未含编辑器；因此本项只记录源码能证明的刷新范围，不把编辑器侧栏标签的语言刷新写成已验证结论。

## 源码依据

| 依据 | 已核对内容 |
| --- | --- |
| `desktop_qt_ui/ui/main_window.py:53` | 侧栏收起、展开和返回按钮行为。 |
| `desktop_qt_ui/ui/main_window.py:140` | 七个常规导航项的顺序、图标、调用 key、对象名、默认页与内部切换器。 |
| `desktop_qt_ui/ui/main_window.py:170` | 页面切换后的刷新分支。 |
| `desktop_qt_ui/ui/main_window.py:188` | `EditorView` 的构造、`editor_page` 对象名和底部导航位置。 |
| `desktop_qt_ui/ui/main_window.py:529` | 语言持久化和全局 UI 刷新入口。 |
| `desktop_qt_ui/ui/main_window.py:571` | 主页面、常规导航和编辑器页面的刷新范围。 |
| `desktop_qt_ui/ui/main_window.py:790` | 七个常规导航标签及提示的刷新字典。 |
| `desktop_qt_ui/ui/main_page/view.py:115` | 前七个页面的构造和 `page_widgets` 键映射。 |
| `desktop_qt_ui/ui/main_page/view.py:301` | 主页面语言刷新内容。 |
| `desktop_qt_ui/ui/main_page/layout.py:111` | 主题/语言下拉的数据来源和语言变更信号。 |
| `desktop_qt_ui/services/i18n_service.py:62` | 支持语言的固定映射和 locale 文件加载。 |
| `desktop_qt_ui/theme_registry.py:8` | 主题存储值与默认主题。 |
| `desktop_qt_ui/locales/en_US.json`、`desktop_qt_ui/locales/zh_CN.json` | 表中八个 UI 调用 key 的 English 与简体中文实际值。 |

## 调查快照与验证

本清单对应下列 SHA-256 源码快照，可用于后续差异追踪：

| 文件 | SHA-256 |
| --- | --- |
| `desktop_qt_ui/ui/main_window.py` | `c9b1999bfc6be85b6cb06733ed1e5141eb9590d241de7df7527b5759caaef2b3` |
| `desktop_qt_ui/ui/main_page/view.py` | `e120332cd00121acaa959ede6199034484aaba4a80fdc68893bd18c348296e5c` |
| `desktop_qt_ui/services/i18n_service.py` | `994e617f889ebc334fd9ccd458cec6acc1583521f4cfaa1df9e69f95990fdae6` |
| `desktop_qt_ui/ui/main_page/layout.py` | `5b94cbc1aee8a8a98673dd362a91d7658db55d52b1982599c344ed9b05e3c521` |
| `desktop_qt_ui/theme_registry.py` | `ababd292c1efe2d112507bbc128394973bf9646596bb1ebf8fc82116e3316f4e` |
| `desktop_qt_ui/locales/en_US.json` | `849a03f5bc725306919907c0bae294e7da0fa303d9fb2ac6612f764db71ab0b0` |
| `desktop_qt_ui/locales/zh_CN.json` | `92113934a1c9b1ed0874714f56e025c13d956e0568db2549b53551023fad1116` |

已完成的静态验证：

1. 以 `rg` 定位注册表、页面映射、激活分支、语言刷新路径和两份 locale 的八个 key。
2. 以 PowerShell `ConvertFrom-Json -AsHashtable` 解析 `en_US.json` 与 `zh_CN.json`，并逐项比较本表的八个 key；该模式保留仅大小写不同的现有 locale key。
3. 以 `git diff --check` 验证本调查文件和对应 TODO 改动没有空白错误。

未执行应用启动、截图或有头模式交互；这些由蓝图规定的后续阶段完成。
