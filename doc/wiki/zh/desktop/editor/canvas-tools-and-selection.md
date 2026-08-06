---
title: 画布工具与选区
description: 在编辑器画布上切换每种画布工具，选择、拖拽文本区域，并控制视图缩放与平移
pageId: desktop.editor.canvas-tools-and-selection
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 画布工具与选区

进入编辑器后，画布是修改文本区域和修复图片的主要工作区。本页说明如何切换每种画布工具（选择、蒙版画笔、橡皮擦、彩色画笔、仿制印章），以及选择、拖拽和缩放画布与文本区域的具体操作。蒙版笔画如何写入修复蒙版、画笔/印章图层与清除按钮见[蒙版、画笔与仿制印章](./mask-paint-and-clone-stamp.md)；顶栏菜单、显示模式与排列操作见[工具栏与菜单](./toolbar-and-menus.md)；快捷键注册与焦点优先级见[快捷键](./shortcuts.md)。

## 功能边界

- 画布工具是一个单一活动状态 `active_tool`，可选值为 `select`、`brush`、`eraser`、`paint`、`paint_erase`、`clone`、`stamp_erase` 以及临时绘制状态 `draw_textbox`。UI 只通过属性面板按钮、键盘 `Q`/`W`/`E` 和右键菜单“添加文本框”修改它。
- 选择（点击、框选、多选）和区域拖拽只在“选择”工具下发生；画笔类工具按下左键后画布不再响应区域选择，而是绘制笔画。
- 视图缩放与平移（滚轮缩放、放大/缩小、适应窗口、中键拖动）作用于整个画布视图，不改变区域数据；`Ctrl+滚轮` 调整选中区域字号、`Shift+滚轮` 调整共享画笔大小，这两类组合键归[快捷键](./shortcuts.md)页。
- 选区在画布、区域列表和属性面板之间双向同步；区域原文/译文编辑、查找替换、OCR/翻译按钮和列表行为见[区域列表与文本编辑](./region-list-and-text-editing.md)。
- 蒙版细化、画笔/印章图层的数据结构与渲染归[蒙版、画笔与仿制印章](./mask-paint-and-clone-stamp.md)，本页只说明工具入口与指针语义。

## UI 操作

### 在属性编辑中切换画布工具

打开编辑器后，左侧默认显示“属性编辑”（`Property Editor`）。在“图像编辑”（`Image Editing`）分组中有 `Mask`、`Paint`、`Clone Stamp` 三个页签，每个页签提供一组互斥工具按钮；三个页签共用一个按钮组，因此选中任一页签的工具都会取消其他页签的工具选中。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Property Editor` | Property Editor | 属性编辑 |
| `Image Editing` | Image Editing | 图像编辑 |
| `Mask` | Mask | 蒙版 |
| `Paint` | Paint | 画笔 |
| `Clone Stamp` | Clone | 印章 |
| `No Selection` | No Selection | 不选择 |
| `Selection Tool` | Selection Tool | 选择工具 |
| `Brush` | Brush | 画笔 |
| `Brush Tool` | Brush Tool | 画笔工具 |
| `Eraser` | Eraser | 橡皮擦 |
| `Eraser Tool` | Eraser Tool | 橡皮擦工具 |
| `Clone Stamp Hint` | Clone stamp: right-click to sample, left-drag to paint | 仿制印章：右键取样，左键拖动涂抹 |
| `Brush Size:` | Brush Size: | 笔刷大小: |
| `Brush Color:` | Brush Color: | 画笔颜色： |
| `Show Refined Mask` | Show Refined Mask | 显示优化蒙版 |
| `Show Paint Layer` | Show Paint Layer | 显示画笔层 |
| `Show Stamp Layer` | Show Stamp Layer | 显示印章层 |
| `Clear All Masks` | Clear All Masks | 清除所有蒙版 |
| `Clear Paint Layer` | Clear Paint Layer | 清除画笔图层 |
| `Clear Stamp Layer` | Clear Stamp Layer | 清除印章层 |

三个页签的工具按钮按下后分别发出 `select`、`brush`、`eraser`、`paint`、`paint_erase`、`clone`、`stamp_erase` 活动值；按钮的选中状态又随模型中的活动工具反向同步。切换页签时，如果当前工具不属于新页签，会自动把活动工具切回该页签的“不选择”（`No Selection`）按钮，避免跨页工具冲突。

蒙版页额外提供“显示优化蒙版”（`Show Refined Mask`）和“清除所有蒙版”（`Clear All Masks`）；画笔页提供“显示画笔层”（`Show Paint Layer`）、“清除画笔图层”（`Clear Paint Layer`）和“画笔颜色：”（`Brush Color:`）；印章页提供“显示印章层”（`Show Stamp Layer`）、“清除印章层”（`Clear Stamp Layer`）。三个页签共用同一个“笔刷大小：”（`Brush Size:`）模型字段，范围 5–200，初始 30。

不用打开面板时，也可以按 `Q` 切换“选择工具”、`W` 切换“画笔工具”、`E` 切换“橡皮擦工具”。在空白画布上右键选择“添加文本框”，会进入 `draw_textbox` 绘制模式。

### 画布指针操作

下表按活动工具列出指针语义；输入层把画笔类工具一律当作绘制，不响应区域选择。

| 活动工具 | 左键 | 右键 | 说明 |
| --- | --- | --- | --- |
| `select` | 点击选中区域；在白框内拖拽移动；拖动手柄缩放/旋转；在空白处拖拽框选 | 打开上下文菜单 | 区域选择与几何编辑 |
| `draw_textbox` | 拖出矩形创建新文本区域，松开后自动回到 `select` | “添加文本框”由右键菜单进入 | 矩形宽或高小于 20px 时放弃创建 |
| `brush` | 按住拖动向优化蒙版写入白色笔画 | — | 提交 `MaskEditCommand` 并触发修复笔画 |
| `eraser` | 按住拖动把蒙版笔画擦成 0 | — | 提交 `MaskEditCommand` |
| `paint` | 按住拖动以画笔颜色写入画笔图层 | — | 写入 `paint_overlay` |
| `paint_erase` | 按住拖动擦除画笔图层 | — | 写入 `paint_overlay` |
| `clone` | 按住拖动仿制涂抹到印章图层 | 右键取样；右键菜单被屏蔽 | 取样圈标记；偏移在落笔时锁定 |
| `stamp_erase` | 按住拖动擦除印章图层 | — | 写入 `stamp_overlay` |

画笔、橡皮擦、彩色画笔、仿制印章和印章擦除都显示圆形光标，半径随画笔大小和当前缩放变化；`draw_textbox` 显示十字光标。进行中的框选或笔画在切换工具、打开右键菜单、按 `Escape` 或窗口失焦时统一清理：切工具前按旧工具语义提交，右键菜单/失焦时直接丢弃。

### 选择、拖拽与缩放

- 选择：在“选择”工具下点击文本区域即选中；按住 `Ctrl` 点击可追加选择；在空白画布按住左键拖出虚线框，松开后按“与框相交”精确命中旋转和细长区域，未按 `Ctrl` 时先清空旧选择。点击空白处（零尺寸框选）等效于取消选择。
- 拖拽：拖动已选中的区域（白框内部）可移动它，其他同时选中的区域会一起跟随移动；拖动选中区域四周的白色方形手柄可调整外框大小，拖动旋转手柄按角度旋转。移动/缩放/旋转完成后通过 `update_region_geometry` 写回区域数据并进入撤销历史。
- 缩放：滚轮向上以 1.15 倍放大、向下以 1/1.15 缩小，倍率钳制在 0.05–50；缩放以鼠标位置为锚点。顶栏“菜单”中的“放大 (+)”（`Zoom In (+)`）、“缩小 (-)”（`Zoom Out (-)`）和常驻“适应窗口”（`Fit to Window`）按钮调用同一套视图变换。
- 平移：按住鼠标中键拖动可平移画布；中键按下时内部切到 `ScrollHandDrag`，松开后恢复 `NoDrag`。
- 视图状态（变换矩阵与中心点）通过 `view_state_changed` 同步给原图对比面板和富文本浮窗定位。

## 工具与选区流程

下面是工具状态流转和选区双向同步的示意。工具切换先按旧工具语义提交进行中的交互，因此不会把蒙版笔画错误地提交到新工具；`draw_textbox` 提交后自动回到“选择”。

```mermaid
flowchart LR
    S["select（默认工具）"] -->|"属性面板按钮 / Q / W / E"| B["brush / eraser"]
    S -->|"Paint 页签按钮"| P["paint / paint_erase"]
    S -->|"Clone Stamp 页签按钮"| C["clone / stamp_erase"]
    S -->|"右键菜单「添加文本框」"| D["draw_textbox"]
    D -->|"拖出矩形并松开（≥20px）"| S
    B -->|"切换工具或页签"| S
    P -->|"切换工具或页签"| S
    C -->|"切换工具或页签"| S
```

选区在任何一端变化都会同步到另一端：画布上的点击/框选先落到 Qt 场景的选择状态，再由 `SelectionManager` 转成模型中的索引列表，属性面板、区域列表和富文本浮窗都读取同一份模型选择；在区域列表中点击则通过 controller 反向写回模型并刷新画布选中状态。

```mermaid
flowchart LR
    A["画布：点击 / Ctrl 追加 / 框选"] --> B["Qt 场景选择状态"]
    B -->|"selectionChanged"| C["SelectionManager 正向同步"]
    C --> D["EditorModel 选区索引"]
    D -->|"selection_changed"| E["属性面板 / 区域列表 / 富文本浮窗"]
    F["区域列表点击"] -->|"set_selection_from_list"| D
    D -->|"反向同步 setSelected"| B
```

## 运行机理

### 工具状态机

`session.py` 中 `active_tool` 的初始值是 `select`。`EditorModel.set_active_tool()` 只在值变化时发出信号；`GraphicsView._on_active_tool_changed()` 先按旧工具语义提交进行中的框选/笔画，再切换内部 `_active_tool` 并更新光标。`clone` 之外的工具切换会清除仿制印章的取样点和偏移。输入层还保留 `pen` 的兼容分支，但当前 UI 不会发出该值。

`draw_textbox` 不会出现在属性面板按钮中：只有右键菜单“添加文本框”（`enter_drawing_mode`）会先清空选择、把活动工具设为 `draw_textbox`，画布拖出矩形后 `_finish_textbox_drawing` 创建区域并自动回到 `select`；矩形宽或高小于 20px 时放弃创建。新区域会继承最后选中区域的字体、颜色、对齐等样式作为模板，文字方向按框的宽高推断。

### 选区双向同步

`SelectionManager` 用 `_syncing` 标志防止循环同步：Qt 场景 `selectionChanged` → 模型 `set_selection`；模型 `selection_changed` → 逐个 `setSelected`。框选使用 `scene.items(rect, IntersectsItemShape)` 做精确命中，而不是 `boundingRect`，因此旋转和细长区域不会被误选。区域列表重建后通过 `restore_selection_after_rebuild` 恢复选中状态。

## 依赖与冲突

- 区域选择只在 `select` 工具下发生；画笔类工具的左键总是绘制，不能用来点选区域。
- `Delete` 删除区域、`Ctrl+Z`/`Ctrl+Y` 撤销重做都受焦点约束：焦点在文本控件时留给文本编辑，焦点在画布时才执行区域操作，详见[快捷键](./shortcuts.md)。
- `Ctrl+滚轮` 和 `Shift+滚轮` 会被快捷键管理器拦截，分别调整选中区域字号和共享画笔大小，不会穿透成画布缩放。
- 切换页签会把工具切回该页的“不选择”，因此“蒙版画笔”和“彩色画笔”不会同时处于活动状态。
- 右键菜单的“🔍 OCR识别选中项”“🌐 翻译选中项”“📋 复制区域”“🎨 粘贴样式”“🗑️ 删除选中的 N 个区域”“➕ 添加文本框”“📋 粘贴区域”“🔄 刷新视图”是代码里的中文字面量，没有 `en_US`/`zh_CN` 对照，不会随语言切换；仿制印章活动时右键被取样占用，菜单完全屏蔽。
- 视图缩放钳制在 0.05–50 之间，防止滚轮缩放跑飞和极小缩放下描边残影；缩放只改变视图变换，不改变区域数据。

## 关联文件与格式

| 文件/状态 | 本页实际作用 | 注意 |
| --- | --- | --- |
| 会话状态 `active_tool`、`brush_size`、`brush_color` | 画布工具与画笔参数的运行态 | 只在编辑器会话内存中，不写入配置文件 |
| 区域数据 `polygons`、`white_frame_rect_local`、`angle` 等 | 画布拖拽/手柄编辑最终写回的区域几何字段 | 持久化到 `*_translations.json`，见[导入导出与回写](./import-export-and-writeback.md) |
| 优化蒙版、画笔图层、印章图层 | `brush`/`eraser`/`paint`/`clone` 等工具的写入目标 | 图层结构、渲染与清除见[蒙版、画笔与仿制印章](./mask-paint-and-clone-stamp.md) |

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 工具选择 UI | `desktop_qt_ui/ui/widgets/property_panel.py` | 图像编辑分组、三页签、共享互斥按钮组、`_on_mask_tool_changed` 映射、页签切换复位 |
| 工具状态 | `desktop_qt_ui/editor/session.py`、`editor_model.py`、`editor_controller.py` | `active_tool` 初始 `select`、信号与 controller 转发 |
| 画布输入 | `desktop_qt_ui/ui/editor/graphics_view_input.py` | 各工具左/右键分支、框选、文本区域绘制、滚轮缩放、中键平移、光标 |
| 视图与缩放 | `desktop_qt_ui/ui/editor/graphics_view.py` | 缩放钳制 0.05–50、变换锚点、`fit_to_window`、区域拖拽阈值 5px |
| 选区同步 | `desktop_qt_ui/ui/editor/selection_manager.py` | 正反向同步、框选相交命中、重建后恢复选择 |
| 区域几何 | `desktop_qt_ui/ui/editor/graphics_items.py` | 白色手柄缩放/平移、旋转手柄、批量移动 |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | 表格中 key 与两种语言实际显示值 |
| 信号接线 | `desktop_qt_ui/ui/editor/view.py` | 属性面板工具/画笔信号、工具栏缩放/适应窗口、列表选择回写 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已完整读取并按页面合同编写；本页 TODO 保持 `[未开工]`，由主代理统一勾选 |
| UI 布局与调用 | 完成 | 静态核对属性面板工具页签、view 信号接线与画布输入分支 |
| `en_US` / `zh_CN` 实际 locale | 完成 | 页面表格逐项记录 key、English、简体中文实际值 |
| 工具/选区/缩放运行链 | 完成 | 静态核对工具状态机、选区双向同步、框选命中、滚轮缩放与中键平移 |
| 脱敏运行验证 | 待后续 | 未启动 GUI、未截图；未读取真实用户图片、`.env`、密钥或私有任务产物 |
| VitePress | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` 及镜像/源码检查 |
