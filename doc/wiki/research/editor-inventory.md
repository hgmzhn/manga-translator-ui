# 编辑器菜单、工具、属性、快捷键与焦点冲突清单

> Phase 0 源码证据；调查日期：2026-08-06。
>
> 范围只覆盖桌面 `EditorView` 内的顶栏、画布菜单/工具、区域列表、属性面板、富文本浮窗、`EditorShortcutManager` 的实际注册，以及这些部件之间已在源码中处理的焦点优先级。本文不是最终用户页面，不替代后续目录中的 11 个编辑器页面、真实界面截图或有头模式验证。

## 固定入口与接线

`EditorView` 在构造时创建 `EditorToolbar`、左栏的“译文列表”和“属性编辑”、中心 `GraphicsView`、右栏文件列表及 `EditorShortcutManager`。属性面板和工具栏信号最终接到 controller；模型的选区、画笔大小、画笔颜色和活动工具又回写各 UI 部件。因此下表中的顶栏、属性和画布工具不是互不关联的平行控件。

| UI 部件 | 实际名称 / 行为 | 主要源码依据 |
| --- | --- | --- |
| 顶栏 | `EditorToolbar`；三个单级下拉菜单加“适应窗口”和“原图不透明度”常驻控件 | `desktop_qt_ui/ui/widgets/editor_toolbar.py:87`、`desktop_qt_ui/ui/widgets/editor_toolbar.py:173` |
| 左栏 | `Translation List` 与 `Property Editor` 两个路由；默认显示后者 | `desktop_qt_ui/ui/editor/view.py` |
| 中心 | `GraphicsView`；工具状态由 `EditorModel.active_tool` 提供 | `desktop_qt_ui/ui/editor/graphics_view.py:64`、`desktop_qt_ui/editor/editor_model.py:227` |
| 右栏 | 添加文件、添加文件夹、清空列表和文件选择；不在本文逐项展开 | `desktop_qt_ui/ui/editor/view.py:759` |
| 信号接线 | 工具栏、画布和属性面板均已连接至 controller；富文本浮窗写回 `update_translation_rich` | `desktop_qt_ui/ui/editor/view.py:770`、`desktop_qt_ui/ui/editor/view.py:801`、`desktop_qt_ui/ui/editor/view.py:809` |

下文的 `UI 调用 key` 是传给 `I18nManager.translate()` 的原始 key。English 与简体中文值直接读取 `desktop_qt_ui/locales/en_US.json`、`desktop_qt_ui/locales/zh_CN.json`，不自行翻译。

## 顶栏菜单与常驻控件

### `Menu`

顶栏的“菜单”下拉只有一层。导出、撤销和重做在 `QAction` 上仅显示快捷键文字；真正注册由 `EditorShortcutManager` 完成，避免一个动作被两套快捷键重复触发。

| 行为 / 存储值 | UI 调用 key | English 实际值 | 简体中文实际值 | 源码事实 |
| --- | --- | --- | --- | --- |
| 导出 | `Export Image` | Export Image | 导出图片 | 文本显示 `(Ctrl+Q)`；发出 `export_requested` |
| 撤销 | `Undo` | Undo | 撤销 | 文本显示 `(Ctrl+Z)`；发出 `undo_requested` |
| 重做 | `Redo` | Redo | 重做 | 文本显示 `(Ctrl+Y)`；发出 `redo_requested` |
| 放大 | `Zoom In (+)` | Zoom In (+) | 放大 (+) | 发出 `zoom_in_requested` |
| 缩小 | `Zoom Out (-)` | Zoom Out (-) | 缩小 (-) | 发出 `zoom_out_requested` |
| 吸附开关 | `Enable Editor Snapping` | Enable Editor Snapping | 启用编辑器吸附 | `app.editor_snap_enabled`，默认 `false` |
| 中心缩放开关 | `Scale Text Boxes from Center` | Scale Text Boxes from Center | 中心点缩放 | `app.editor_center_scale_enabled`，默认 `false` |
| 浮动富文本开关 | `Show Rich Text Editor Popup` | Show Rich Text Editor Popup | 显示富文本编辑弹窗 | `app.editor_rich_text_popup_enabled`，默认 `true` |
| 编辑时自动规则 | `Auto Apply Rich Text Rules While Editing` | Auto Apply Rich Text Rules While Editing | 编辑时自动应用富文本规则 | `app.editor_auto_rich_text_rules`，默认 `true` |
| 切图自动导出 | `Auto Export on Image Switch` | Auto Export on Image Switch | 切图时自动导出 | `app.editor_auto_export_on_switch`，默认 `true` |

菜单条目和五个开关定义见 `desktop_qt_ui/ui/widgets/editor_toolbar.py:226`；`EditorView` 对应的应用、持久化和默认回退见 `desktop_qt_ui/ui/editor/view.py:153`、`desktop_qt_ui/ui/editor/view.py:206`；配置模型和发行示例同样给出这五个默认值，见 `desktop_qt_ui/core/config_models.py:176`、`config/config-example.json:10`。

### `Display Mode`

此菜单是互斥单选；实际存储值和翻译键由 `_display_mode_definitions()` 同时列出。

| 存储值 | UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- | --- |
| `full` | `Show Text and Boxes` | Show Text and Boxes | 文字文本框显示 |
| `text_only` | `Show Text Only` | Show Text Only | 只显示文字 |
| `box_only` | `Show Boxes Only` | Show Boxes Only | 只显示框线 |
| `none` | `Show Nothing` | Show Nothing | 都不显示 |
| `compare_original_split` | `Compare with Original (Two Panels)` | Compare with Original (Two Panels) | 与原图对比（双栏） |

来源：`desktop_qt_ui/ui/widgets/editor_toolbar.py:306`、`desktop_qt_ui/ui/widgets/editor_toolbar.py:381`；信号接至 `controller.set_display_mode`：`desktop_qt_ui/ui/editor/view.py:777`。

### `Arrange`

排列菜单保持打开，以便连续选择参照、对齐或分布。选区数决定可用状态：以画布为参照时至少选择 1 个区域即可对齐；以选区为参照时至少 2 个；两种间距分布都至少 3 个。

| 类别 | 存储值 / UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- | --- |
| 参照 | `selection` / `Reference: Selection` | Reference: Selection | 参照：选区 |
| 参照 | `canvas` / `Reference: Canvas` | Reference: Canvas | 参照：画布 |
| 对齐 | `left` / `Align Left` | Align Left | 左对齐 |
| 对齐 | `horizontal_center` / `Align Horizontal Center` | Align Horizontal Center | 水平居中 |
| 对齐 | `right` / `Align Right` | Align Right | 右对齐 |
| 对齐 | `top` / `Align Top` | Align Top | 顶对齐 |
| 对齐 | `vertical_center` / `Align Vertical Center` | Align Vertical Center | 垂直居中 |
| 对齐 | `bottom` / `Align Bottom` | Align Bottom | 底对齐 |
| 分布 | `spacing_v` / `Distribute Vertical Spacing` | Distribute Vertical Spacing | 垂直间距分布 |
| 分布 | `spacing_h` / `Distribute Horizontal Spacing` | Distribute Horizontal Spacing | 水平间距分布 |

来源：`desktop_qt_ui/ui/widgets/editor_toolbar.py:323`、`desktop_qt_ui/ui/widgets/editor_toolbar.py:510`；view 读取当前参照并调用 controller：`desktop_qt_ui/ui/editor/view.py:502`。

### 常驻控件

| 控件 | UI 调用 key | English 实际值 | 简体中文实际值 | 实际范围 / 连接 |
| --- | --- | --- | --- | --- |
| 适应窗口按钮 | `Fit to Window` | Fit to Window | 适应窗口 | `fit_window_requested` -> `graphics_view.fit_to_window` |
| 原图不透明度滑条 | `Original Image Opacity:` | Original Image Opacity: | 原图不透明度: | 0–100；初始 `0`，信号 -> `set_original_image_alpha` |

来源：`desktop_qt_ui/ui/widgets/editor_toolbar.py:193`、`desktop_qt_ui/ui/editor/view.py:774`。

## 画布菜单、工具与指针操作

### 右键菜单（当前没有 i18n key）

`GraphicsViewInputMixin.contextMenuEvent()` 直接创建 `Action(text, self)`，使用下表的中文字面量；因此这些项目没有 `en_US.json` / `zh_CN.json` 对照，也不会随语言切换。仿制印章工具活动时，右键专门用于取样，右键菜单被完全屏蔽。

| 出现条件 | 实际显示文字（源码字面量） | 调用 |
| --- | --- | --- |
| 有选区 | `🔍 OCR识别选中项` | `ocr_regions(selected_regions)` |
| 有选区 | `🌐 翻译选中项` | `translate_regions(selected_regions)` |
| 仅单选 | `📋 复制区域` | `copy_region(index)` |
| 仅单选 | `🎨 粘贴样式` | `paste_region_style(index)` |
| 有选区 | `🗑️ 删除选中的 {selection_count} 个区域` | `delete_regions(selection)` |
| 无选区 | `➕ 添加文本框` | `enter_drawing_mode()`，活动工具变为 `draw_textbox` |
| 无选区 | `📋 粘贴区域` | 按当前鼠标位置 `paste_region(mouse_pos_image)` |
| 无选区 | `🔄 刷新视图` | `scene.update()`、`view.update()` |

来源：`desktop_qt_ui/ui/editor/graphics_view_input.py:1030`、`desktop_qt_ui/ui/editor/graphics_view_input.py:1097`；`draw_textbox` 的 controller 设置点为 `desktop_qt_ui/editor/editor_controller.py:1569`。

### 属性面板中的工具选择

“图像编辑”有 `Mask`、`Paint`、`Clone Stamp` 三个页签，共用一个互斥 `QButtonGroup`。工具按钮最终发出如下活动工具值；模型变化会反向同步按钮状态。

| 页签 / UI 调用 key | English 实际值 | 简体中文实际值 | 可选工具（活动值） | 其他控件 |
| --- | --- | --- | --- | --- |
| `Mask` | Mask | 蒙版 | `select`（`No Selection` / No Selection / 不选择）、`brush`（`Brush` / Brush / 画笔）、`eraser`（`Eraser` / Eraser / 橡皮擦） | `Brush Size:` 5–200、`Show Refined Mask`、`Clear All Masks` |
| `Paint` | Paint | 画笔 | `select`、`paint`、`paint_erase` | `Brush Size:` 5–200、`Brush Color:`、`Show Paint Layer`、`Clear Paint Layer` |
| `Clone Stamp` | Clone | 印章 | `select`、`clone`、`stamp_erase` | `Brush Size:` 5–200、`Show Stamp Layer`、`Clear Stamp Layer` |

补充翻译值：`Brush Size:` = Brush Size: / 笔刷大小:；`Brush Color:` = Brush Color: / 画笔颜色：；`Show Refined Mask` = Show Refined Mask / 显示优化蒙版；`Show Paint Layer` = Show Paint Layer / 显示画笔层；`Show Stamp Layer` = Show Stamp Layer / 显示印章层；`Clear All Masks` = Clear All Masks / 清除所有蒙版；`Clear Paint Layer` = Clear Paint Layer / 清除画笔图层；`Clear Stamp Layer` = Clear Stamp Layer / 清除印章层。`Clone Stamp Hint` 的实际提示为 “Clone stamp: right-click to sample, left-drag to paint” / “仿制印章：右键取样，左键拖动涂抹”。

来源：控件与值见 `desktop_qt_ui/ui/widgets/property_panel.py:310`、`desktop_qt_ui/ui/widgets/property_panel.py:385`、`desktop_qt_ui/ui/widgets/property_panel.py:465`、`desktop_qt_ui/ui/widgets/property_panel.py:1849`；信号接线见 `desktop_qt_ui/ui/editor/view.py:826`。画布按工具处理左键绘制、仿制取样和拖动，见 `desktop_qt_ui/ui/editor/graphics_view_input.py:179`；笔画提交时 `brush` 增加蒙版、`eraser` 擦除蒙版，见同文件 `:519`。

### 画布通用输入

| 输入 | 实际行为 | 来源 |
| --- | --- | --- |
| 普通滚轮 | 以鼠标点为锚缩放画布，倍率限制在 `0.05`–`50.0` | `desktop_qt_ui/ui/editor/graphics_view_input.py:22`、`:56` |
| 中键拖动 | 合成左键事件进入 `ScrollHandDrag`，松开后恢复 `NoDrag` | `desktop_qt_ui/ui/editor/graphics_view_input.py:123` |
| 画布空白处左键拖动 | 开始框选 | `desktop_qt_ui/ui/editor/graphics_view_input.py:227` |
| 选中区域左键拖动 | 超过阈值后发出 region drag 开始/结束信号；富文本浮窗在拖动期间隐藏，结束后恢复 | `desktop_qt_ui/ui/editor/graphics_view_input.py:219`、`desktop_qt_ui/ui/editor/view.py:805` |
| `Escape` / 画布失焦 | 取消进行中的框选、绘制、文本框、仿制、区域拖动或中键平移，不提交 | `desktop_qt_ui/ui/editor/graphics_view_input.py:67`、`:167`、`:174` |

## 左栏：区域列表与属性控件

### 译文列表

每一行显示编号加原文、可编辑译文 `TextEdit` 和拖动手柄。拖动行会改变模型中的区域顺序，并可通过撤销栈恢复；列表支持全局查找替换和“应用所有译文修改”，画布/模型选区会反向选中列表项。列表发起的选择不会打开富文本浮窗。

| UI 调用 key | English 实际值 | 简体中文实际值 | 用途 |
| --- | --- | --- | --- |
| `Translation List` | Translation List | 译文列表 | 左栏路由标题 |
| `Find` | Find | 查找 | 查找输入框占位 |
| `Replace with` | Replace with | 替换为 | 替换输入框占位 |
| `Replace All` | Replace All | 全部替换 | 修改各列表项草稿 |
| `Apply All Translation Changes` | Apply All Translation Changes | 应用所有译文修改 | 汇总草稿并提交 controller |

来源：`desktop_qt_ui/ui/editor/view.py:339`、`:481`；列表增量更新、保留草稿和画布同步见 `desktop_qt_ui/ui/widgets/region_list_view.py:85`、`:151`、`:279`。

### `Property Editor` 的文本与操作区

无选区时，文本、样式和操作三区均禁用；单选时三区启用；多选时文本区禁用、样式和操作区启用。多选的样式修改通过 `style_patch_requested` 同时应用给全部选区。

| 控件组 | UI 调用 key | English 实际值 | 简体中文实际值 | 实际绑定 / 限制 |
| --- | --- | --- | --- | --- |
| 文本组 | `Text Content` | Text Content | 文本内容 | 单选时可编辑 |
| OCR 下拉 + 按钮 | `OCR Model:` / `Recognize` | OCR Model: / Recognize | OCR模型: / 识别 | 下拉取配置选项；按钮处理当前选区 OCR |
| 翻译器下拉 + 按钮 | `Translator:` / `Translate` | Translator: / Translate | 翻译器： / 翻译 | 显示值映射回 translator key；按钮处理当前选区翻译 |
| 目标语言 | `Target Language:` | Target Language: | 目标语言： | 显示值映射回语言代码 |
| 原文 | `Original Text:` | Original Text: | 原文: | 文本改动写回原文/`text` |
| 译文原始值显示 | `Show Translation (Raw)` | Show Translation (Raw) | 显示替换前译文 | 勾选写 `translation_raw`，未勾选写 `translation` |
| 译文 | `Translated Text:` | Translated Text: | 译文: | `↵` 显示形式保存时转换为 `[BR]` |
| 插入动作 | `Placeholder` / `Newline↵` | Placeholder / Newline↵ | 占位符 / 换行↵ | 插入 `＿` 或换行显示字符 |
| 统计 | `Character count: 0` | Character count: 0 | 字符数: 0 | 字符计数标签 |
| 操作组 | `Actions` | Actions | 操作 | 单选和多选均可用 |
| 复制、粘贴、删除 | `Copy` / `Paste` / `Delete` | Copy / Paste / Delete | 复制 / 粘贴 / 删除 | 分别调用区域复制、样式/区域粘贴、区域删除 |

来源：控件建立在 `desktop_qt_ui/ui/widgets/property_panel.py:587`、`:801`；选区启停和多选语义在 `:1475`；实际写回字段及 `↵` / `[BR]` 转换在 `:1660`、`:1690`；controller 信号接线在 `desktop_qt_ui/ui/editor/view.py:809`。

### 样式区

样式区的控件对当前所有选区发出 patch；因此“多选时显示哪个值”目前不是单独的混合值 UI，而是清空文本、保留样式编辑可用。字段范围和默认 widget 值如下，最终文档不得将这些 widget 值误写为核心或发行配置默认值。

| UI 调用 key | English 实际值 | 简体中文实际值 | 控件 / 范围或初始值 | 样式 patch 字段 |
| --- | --- | --- | --- | --- |
| `Style Settings` | Style Settings | 样式设置 | 样式区标题 | — |
| `Style Preset:` | Style Preset: | 样式组合： | 已保存样式下拉、保存/删除按钮 | 整个样式组合 |
| `Font:` | Font: | 字体： | `FontComboBox` | `font_family` |
| `Font Size:` | Font Size: | 字体大小： | 数值 8–1000；滑条 8–150 | `font_size` |
| `Font Color:` | Font Color: | 字体颜色： | 颜色选择器，组件默认 `#000000` | `font_color` |
| `Stroke Color:` | Stroke Color: | 描边颜色： | 颜色选择器，组件默认 `#ffffff` | `stroke_color` |
| `Stroke Width:` | Stroke Width: | 描边宽度： | 0–1，步长 0.01，初始 `0.07` | `stroke_width` |
| `Line Spacing:` | Line Spacing: | 行间距： | 0.1–5，步长 0.1，初始 `1.0` | `line_spacing` |
| `Letter Spacing:` | Letter Spacing: | 字间距： | 0.1–5，步长 0.1，初始 `1.0` | `letter_spacing` |
| `Angle:` | Angle: | 角度： | -9999–9999°，步长 1，初始 `0.0` | `angle` |
| `Alignment:` | Alignment: | 对齐： | 下拉；显示值来自 `alignment` mapping | `alignment` |
| `Direction:` | Direction: | 方向： | 下拉；排除 `auto`，显示 `direction_horizontal` 或 `direction_vertical` | `direction` |

`direction_horizontal` 的实际值为 Horizontal / 横排，`direction_vertical` 为 Vertical / 竖排。控件建立和范围见 `desktop_qt_ui/ui/widgets/property_panel.py:680`；多选 patch 发射见 `:1773`；每个 patch 字段的处理见 `:1780`；映射填充见 `:898`。

## 浮动富文本编辑器

富文本不是画布子控件，而是带 `Qt.Tool | FramelessWindowHint` 的顶层工具窗口。它有正文 `TextEdit`、一组风格开关、按连续文本段生成的属性卡、以及可收起预设侧栏。

### 工具条风格码

风格工具条实际显示的是代码（如 `B`、`TCY`），悬浮提示和属性卡标题读取下表翻译键。按钮会对当前选择范围的富文本文档应用样式，而不是编辑整个区域的基础样式。

| 代码 | UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- | --- |
| `B` | `Bold` | Bold | 加粗 |
| `I` | `Italic` | Italic | 斜体 |
| `U` | `Underline` | Underline | 下划线 |
| `C` | `Text Color` | Text Color | 文字颜色 |
| `S` | `Font Size` | Font Size | 绝对字号 |
| `%` | `Scale` | Scale | 字号倍率 |
| `F` | `Font` | Font | 字体 |
| `O` | `Stroke` | Stroke | 描边 |
| `G` | `Glow` | Glow | 发光 |
| `OS` | `Outer Stroke` | Outer Stroke | 外描边 |
| `D` | `Emphasis` | Emphasis | 着重号 |
| `FA` | `Force Advance` | Force Advance | 强制推进 |
| `T` | `TCY` | TCY | 纵中横 |
| `R` | `Ruby` | Ruby | 注音 |
| `Rot` | `Rotation` | Rotation | 局部旋转 |
| `K` | `Kerning` | Kerning | 字后间距 |
| `PK` | `Pre Kerning` | Pre Kerning | 字前间距 |
| `LK` | `Line Kerning` | Line Kerning | 与前一行间距 |
| `NK` | `Next Kerning` | Next Kerning | 与后一行间距 |
| `XY` | `Offset` | Offset | 偏移 |
| `M` | `Mirror Horizontal` | Mirror Horizontal | 水平镜像 |
| `MV` | `Mirror Vertical` | Mirror Vertical | 垂直镜像 |

来源：风格定义和默认 patch 在 `desktop_qt_ui/ui/widgets/rich_text_editor_components.py:72`；按钮建立在 `:163`；按文本段创建的属性行和控件在 `:443`、`:558`。预设侧栏键包括 `Rich Text Presets`（Rich Text Presets / 富文本预设）、`No saved styles`（No saved styles / 暂无已保存样式）、`Rename preset`（Rename preset / 重命名预设）、`Delete preset`（Delete preset / 删除预设）、`Collapse preset sidebar`（Collapse preset sidebar / 收起预设侧边栏）和 `Expand preset sidebar`（Expand preset sidebar / 展开预设侧边栏），来源 `rich_text_editor_components.py:200`。

### 浮窗的同步与可见性

- 只有一个区域被选中且 `app.editor_rich_text_popup_enabled` 为真时，`EditorView` 绑定该区域、定位并显示浮窗；多选或无选区会清除并隐藏浮窗。来源：`desktop_qt_ui/ui/editor/view.py:516`。
- 导出前会同步 `flush_pending_changes()`，避免 180ms 防抖期内的正文或注音改动未写入模型。来源：`desktop_qt_ui/ui/editor/view.py:486`、`desktop_qt_ui/ui/widgets/rich_text_floating_editor.py:177`。
- 区域数据从属性面板、撤销等外部路径变更时，可见浮窗先提交自身去抖内容再刷新；自身发起的写回被识别并跳过，防止旧文档覆盖模型或光标跳动。来源：`desktop_qt_ui/ui/editor/view.py:557`。

## 已注册快捷键与焦点优先级

### `EditorShortcutManager` 注册表

这里仅列 `EditorShortcutManager._setup_editor_shortcuts()` 和其 viewport 事件过滤器实际注册/拦截的项目。`QKeySequence.StandardKey` 的显示会随 Qt 平台映射；在当前 Windows 调查环境里，工具栏文案将 Undo/Redo/Copy/Paste/Select All 标为 Ctrl+Z、Ctrl+Y、Ctrl+C、Ctrl+V、Ctrl+A，代码本身仍以 StandardKey 为准。

| 注册名 | 实际注册序列 | 焦点在文本控件时 | 非文本焦点时 |
| --- | --- | --- | --- |
| `undo` | `StandardKey.Undo` | 调用文本控件 `undo()` | controller 撤销 |
| `redo` | `StandardKey.Redo` | 调用文本控件 `redo()` | controller 重做 |
| `copy` | `StandardKey.Copy` | 调用文本控件 `copy()` | 复制最后选中的区域 |
| `paste` | `StandardKey.Paste` | 调用文本控件 `paste()` | 单选粘贴样式；否则按鼠标位置或默认位置粘贴区域 |
| `select_all` | `StandardKey.SelectAll` | 调用文本控件 `selectAll()` | 选中全部区域 |
| `delete` | `StandardKey.Delete` | 不删除区域 | 删除选中区域 |
| `export` | `Ctrl+Q` | 仍导出 | 仍导出 |
| `tool_select` | `Q` | 转发字符 `q` 给文本控件 | `set_active_tool('select')` |
| `tool_brush` | `W` | 转发字符 `w` 给文本控件 | `set_active_tool('brush')` |
| `tool_eraser` | `E` | 转发字符 `e` 给文本控件 | `set_active_tool('eraser')` |
| `prev_image` | `A` | 转发字符 `a` 给文本控件 | 文件列表选上一张 |
| `next_image` | `D` | 转发字符 `d` 给文本控件 | 文件列表选下一张 |

来源：快捷键注册见 `desktop_qt_ui/ui/editor/shortcut_manager.py:116`；各处理分支见 `:214`、`:244`、`:275`、`:283`、`:306`、`:327`。

### 滚轮和焦点冲突的源码优先级

| 冲突场景 | 实际优先级 / 结果 | 来源 |
| --- | --- | --- |
| 焦点进入另一个顶层窗口（尤其富文本 `Qt.Tool`） | `QApplication.focusWidget()` 的窗口不是 `EditorView` 所在窗口时，所有 context-aware 编辑器快捷键直接返回；不以主窗口残留焦点误删画布区域 | `desktop_qt_ui/ui/editor/shortcut_manager.py:49` |
| 焦点在主窗口的 `QTextEdit` 或 `QLineEdit` | Undo/redo/copy/paste/select-all 留给文本控件；Q/W/E/A/D 被转发为文字，不切工具或图片 | `desktop_qt_ui/ui/editor/shortcut_manager.py:83`、`:214`、`:288` |
| `Delete` 与文本输入 | 只有焦点不是文本控件时删除区域；文本控件不触发区域删除 | `desktop_qt_ui/ui/editor/shortcut_manager.py:275` |
| `Shift + 滚轮` 位于画布 viewport | 调整共享画笔大小，每格 ±1，钳制 5–200，并吞掉事件 | `desktop_qt_ui/ui/editor/shortcut_manager.py:343`、`:363` |
| 任意包含 Ctrl 的滚轮组合 | 调整所有选中区域字号；无选区也吞掉事件，不能穿透成画布缩放 | `desktop_qt_ui/ui/editor/shortcut_manager.py:376` |
| 普通滚轮位于画布 | 上述过滤器不拦截，`GraphicsView.wheelEvent()` 缩放画布 | `desktop_qt_ui/ui/editor/shortcut_manager.py:394`、`desktop_qt_ui/ui/editor/graphics_view_input.py:56` |
| 浮窗因选区显示 | 用 `WA_ShowWithoutActivating` 显示，且选区变化不调用 `focus_text()`；画布保留焦点，所以 Delete/A/D/Q/W/E 继续按画布语义，用户点击文本框后才进入文字编辑 | `desktop_qt_ui/ui/widgets/rich_text_floating_editor.py:63`、`desktop_qt_ui/ui/editor/view.py:550` |
| 画布点击与属性文本 | 鼠标按下先 `force_save_property_panel_edits()`，后 `setFocus()` 给画布，防止切画布时丢失属性文本修改 | `desktop_qt_ui/ui/editor/graphics_view_input.py:179` |
| 属性面板滑条、数值框和下拉 | 未获得键盘焦点的控件不吞滚轮，让父滚动区域接管；`CustomSlider` 仅在有焦点时改值 | `desktop_qt_ui/ui/widgets/property_panel.py:109`、`:246` |
| 区域列表的正在编辑行 | 差量同步保留草稿；持焦点译文框不被模型更新覆盖，避免丢焦点、光标或 IME 组合字 | `desktop_qt_ui/ui/widgets/region_list_view.py:101`、`:205` |
| 属性面板的正在编辑文本 | 常规刷新不覆盖有焦点的原文/译文框；异步强制字段更新例外 | `desktop_qt_ui/ui/widgets/property_panel.py:1453`、`:1533` |

## 源码快照与验证边界

| 文件 | SHA-256 |
| --- | --- |
| `desktop_qt_ui/ui/widgets/editor_toolbar.py` | `87f86b8fa41b4ec6dbf0414510fbfc94a579d4af3cfb8a54bccc3c43a39fa669` |
| `desktop_qt_ui/ui/editor/view.py` | `9021ddd1d7ac91aecc084a3dbd00d937878085598f6c2219ee003177a2e4009f` |
| `desktop_qt_ui/ui/editor/shortcut_manager.py` | `251c70da2c7ae161718416b32c221d4d6b286d456b39c78bfc07a3e9c2a1bc74` |
| `desktop_qt_ui/ui/widgets/property_panel.py` | `1ab22fe05a1b0aee50c58c08f5df3626843e16c7ca42acb05f5ef023ad3d4941` |
| `desktop_qt_ui/ui/widgets/region_list_view.py` | `bdabc4494f0b373ccafc9e5d36438461320aeb45f6495a977444176ddcb245a4` |
| `desktop_qt_ui/ui/editor/graphics_view_input.py` | `f715f8be2098bdc577151dd7127760eec7e5e69ac053c7b067c0f3726efb9b08` |
| `desktop_qt_ui/ui/widgets/rich_text_floating_editor.py` | `636852d48cdced11ec4cd9b6f38b1b5318c9d90679cbd514e7dfe726be284c48` |
| `desktop_qt_ui/ui/widgets/rich_text_editor_components.py` | `9c33e990f2eb7cc989eb5da72104c82284d0f1fa0788db360e8be9fbbd86bf88` |
| `desktop_qt_ui/locales/en_US.json` | `849a03f5bc725306919907c0bae294e7da0fa303d9fb2ac6612f764db71ab0b0` |
| `desktop_qt_ui/locales/zh_CN.json` | `92113934a1c9b1ed0874714f56e025c13d956e0568db2549b53551023fad1116` |

已完成的静态核对：

1. 用 `rg` 定位编辑器菜单、工具、属性控件、右键菜单、信号接线、快捷键注册和焦点分支。
2. 用 PowerShell `ConvertFrom-Json -AsHashtable` 解析两份 locale，并读取本文列出的 UI 调用 key 的 English 与简体中文值。
3. 用 `Get-FileHash -Algorithm SHA256` 固定上表源码和翻译文件快照。

未执行应用启动、无头/有头交互、截图、快捷键实际触发或不同 Qt 平台的 StandardKey 显示验证；这些仍由后续页面实施和截图/运行验证阶段处理。
