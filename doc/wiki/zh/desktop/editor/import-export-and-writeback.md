---
title: 编辑器导入导出与写回
description: 把翻译结果与手改内容导入编辑器、导出渲染成图，并理解 JSON 与修复图等工程数据的写回机制
pageId: desktop.editor.import-export-and-writeback
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 编辑器导入导出与写回

编辑器把翻译流水线生成的工程数据读进来，让你逐区域修改文字、位置、样式、蒙版和画笔/印章层；修改完成后，执行一次“导出图片”会把当前图渲染成最终图片，同时把工程数据写回磁盘。本页说明这些数据的导入格式、导出渲染与输出路径，以及 JSON、修复图等工程数据的写回机制。

右侧文件列表的增删、切页和“任务完成”进入编辑器的完整操作见[布局与文件列表](./layout-and-file-list.md)；顶栏“导出图片”菜单项与“切图时自动导出”开关的界面和持久化见[工具栏与菜单](./toolbar-and-menus.md)；`Ctrl+Q` 等快捷键的分派见[快捷键](./shortcuts.md)；蒙版与画笔/印章层如何进入工程文件见[蒙版绘制与仿制印章](./mask-paint-and-clone-stamp.md)。

## 功能边界 {#feature-boundary}

- 本页负责编辑器与磁盘之间的数据边界：导入时读取哪些工程文件、导出时渲染什么、写回哪里，以及写回内容的格式。
- 不负责右侧文件列表的按钮、树形展示和行状态（归[布局与文件列表](./layout-and-file-list.md)）；不负责“导出图片”菜单项和“切图时自动导出”开关的界面与持久化（归[工具栏与菜单](./toolbar-and-menus.md)）。
- 编辑器导出不是重新跑完整翻译流水线：它直接渲染当前快照，不重新检测、OCR、翻译、上色或超分；蒙版视为已精炼，修复图直接复用。
- 翻译页的“导入翻译并渲染”（`load_text` / `Import Translation and Render`）与编辑器共用同一套 `_translations.json` 格式，但入口不同：前者是翻译页的工作模式，后者是编辑器文件列表加载图片时自动读取工程数据。
- 不在本页展示真实用户图片、工程 JSON、密钥或私有路径；格式只以键名和脱敏结构描述。

## UI 操作 {#ui-operations}

### 导入图片与工程数据 {#import-images-and-project-data}

1. 在编辑器右栏点击“添加文件”（`Add Files`）、“添加文件夹”（`Add Folder`）或直接拖放文件/文件夹，把图片加入页面列表；列表按钮的完整操作见[布局与文件列表](./layout-and-file-list.md)。
2. 点击列表行（或按 `A`/`D` 切页）加载该图片：编辑器在后台读取关联的 `*_translations.json`、修复图和画笔层，画布显示后即可编辑。
3. 翻译任务完成后，主窗口弹出“任务完成”（`Task Completed`）确认框；选择“是”会进入编辑器并打开结果对应的原图（先经 `translation_map.json` 或本次任务的输出映射解析原图路径）。

### 导出当前图片 {#export-current-image}

1. 打开顶栏“菜单”（`Menu`），点击“导出图片”（`Export Image`），或按 `Ctrl+Q`。
2. 编辑器先冲刷浮动富文本等防抖期内的草稿，再写回工程数据并进入导出队列；导出期间显示进度 Toast，成功后显示“导出成功 … 已同步 JSON”。
3. 开启“切图时自动导出”（`Auto Export on Image Switch`）时，切到下一张图会自动先导出当前图；自动导出被拒绝会中止切图。

### 未保存编辑与切页 {#unsaved-changes-and-switching}

关闭“切图时自动导出”后，当前图存在未保存编辑时切页会弹出三按钮对话框（源码硬编码中文）：

| 按钮 | 行为 |
| --- | --- |
| 导出图片 | 先导出当前图，等导出成功后再加载目标图；导出未入队或失败则不切页 |
| 不保存 | 放弃未保存的编辑，直接加载目标图 |
| 取消 | 中止切页，留在当前图 |

导出成功的提示、导出队列的“正在导出…”和退出时的“导出任务尚未完成”对话框都是源码中的硬编码中文，不在 locale 文件中；下表只列出真正调用 i18n key 的 UI 文案。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Menu` | Menu | 菜单 |
| `Export Image` | Export Image | 导出图片 |
| `Auto Export on Image Switch` | Auto Export on Image Switch | 切图时自动导出 |
| `Add Files` | Add Files | 添加文件 |
| `Add Folder` | Add Folder | 添加文件夹 |
| `Clear List` | Clear List | 清空列表 |
| `Drag and drop files or folders here\nor click the buttons above to add` | Drag and drop files or folders here\nor click the buttons above to add | 拖拽文件或文件夹到此处\n或点击上方按钮添加 |
| `Task Completed` | Task Completed | 任务完成 |
| `Translation completed, {count} files saved.\n\nOpen results in editor?` | Translation completed, {count} files saved.\n\nOpen results in editor? | 翻译完成，成功保存 {count} 个文件。\n\n是否在编辑器中打开结果？ |

## 导入：文件与工程数据 {#import-files-and-project-data}

选中图片后，编辑器以原图路径为键，在 `manga_translator_work/` 工作目录下发现并读取以下工程文件；找不到新位置时会回退到图片同目录的旧版文件。

### 工程文件 {#project-files}

| 文件/目录 | 实际作用 | 发现规则 |
| --- | --- | --- |
| `manga_translator_work/json/<图片名>_translations.json` | 区域、蒙版、画笔/印章层等工程数据 | 新位置优先；`find_json_path` 找不到时回退到图片同目录的旧版 `*_translations.json` |
| `manga_translator_work/inpainted/<图片名>_inpainted.<扩展名>` | 修复后底图 | 存在则作为画布 z=1 底层；不存在时画布显示原图 |
| `manga_translator_work/paint_overlay/<图片名>_overlay.png` | 旧版彩色画笔单文件图层（RGBA PNG） | 仅当 JSON 内没有 `paint_overlay` base64 时作为画笔层兜底 |
| `manga_translator_work/editor_base/<图片名>.<扩展名>` | 上色/超分后的编辑器底图 | 仅当 JSON 记录 `upscale_ratio` 或 `colorizer` 时有效；无标记视为过期并删除 |
| `<输出目录>/translation_map.json` | `{翻译结果图路径: 原图路径}` 映射 | 由主翻译流程写入；编辑器与文件列表用它把结果图解析回原图 |

支持加载的页面图片扩展名与主页一致：`.png`、`.jpg`、`.jpeg`、`.jfif`、`.webp`、`.avif`、`.bmp`、`.tiff`、`.tif`、`.heic`、`.heif`。

### JSON 单图数据键 {#json-image-keys}

JSON 顶层键是原图的绝对路径（后端 `load_text` 读取时取第一个值，不校验键名）；单图数据为以下键：

| JSON 键 | 类型/内容 | 读写角色 |
| --- | --- | --- |
| `regions` | 区域数组；每条含 `lines`（N×4×2 多边形）、`text`、`translation`、`translation_raw`、`font_color`、`font_size`、`alignment`、`direction` 等 | 编辑器读写；导出时经 `_normalize_regions_for_backend` 规范化后写回 |
| `upscale_ratio` / `upscaler` | 超分倍率与模型 | 主流程写入；编辑器导出保留，决定 `editor_base` 是否有效 |
| `colorizer` | 上色器名称 | 同上 |
| `last_export_dir` | 上次导出目录 | 导出时写入；下次导出优先使用 |
| `mask_raw` | base64 PNG 蒙版 | 导出时写入并标记 `mask_is_refined: true`；加载时解码为原始蒙版 |
| `skip_text_replacements` | `true` | 编辑器导出写入；后端渲染不再二次文本替换 |
| `paint_overlay` / `stamp_overlay` | base64 PNG（RGBA） | 导出时写入；加载时解码为画布画笔/印章图层 |
| `original_width` / `original_height` | 原图尺寸 | 主流程写入；加载时用于尺寸对齐 |

### 导入流程 {#import-flow}

```mermaid
flowchart LR
    A["添加文件/文件夹或拖放"] --> B["文件列表后台扫描"]
    B --> C["选中图片并加载"]
    C --> D["translation_map.json 解析原图"]
    D --> E{"editor_base 是否有效？"}
    E -->|"JSON 有超分/上色标记"| F["显示 editor_base 底图"]
    E -->|"无标记或已过期"| G["删除过期底图，显示原图"]
    F --> H["后台并行加载工程文件"]
    G --> H
    H --> H1["_translations.json\nregions / mask_raw / overlays"]
    H --> H2["_inpainted 修复图"]
    H --> H3["paint_overlay PNG（旧版兜底）"]
    H1 --> I["EditorModel 文档快照"]
    H2 --> I
    H3 --> I
```

## 导出：渲染、输出路径与队列 {#export-rendering-output-and-queue}

### 导出做了什么 {#what-export-does}

“导出图片”不重新运行检测/OCR/翻译/上色/超分，而是把当前快照直接交给后端 `load_text` 纯渲染：`translator='none'`、`load_text=True`、`save_text=False`；蒙版视为已精炼（`mask_is_refined`），修复图直接复用；导出配置还会强制 `render.disable_auto_wrap=True`，因为文本框布局已由用户排布。

导出前先做两件持久化：把工程数据写回 `*_translations.json`（见[写回：JSON 与修复图](#writeback-json-and-inpainted)），并把当前修复图（没有修复图时用底图充当）写回 `_inpainted` 文件，让后端 `load_text` 跳过自己的修复步骤。之后才把渲染任务加入导出队列。

### 输出路径与文件名 {#output-path-and-filename}

输出目录按以下优先级确定：

1. 该图 JSON 中记录的 `last_export_dir`（上次导出目录）。
2. `cli.save_to_source_dir` 开启时：`<原图目录>/manga_translator_work/result`。
3. `app.last_output_path`（存在且有效时）。
4. 原图所在目录。

文件名规则：`cli.format` 非空且不是“不指定”时使用该扩展名（小写）；否则沿用原图扩展名；都没有时使用 `.png`。图片质量取 `cli.save_quality`。若开启了 `cli.export_editable_psd`，渲染完成后还会在 `manga_translator_work/psd/` 下导出可编辑 PSD（`cli.psd_script_only` 时只导出脚本）。

### 导出队列与“已保存”语义 {#export-queue-and-clean-state}

导出是异步单线程队列任务（`ThreadPoolExecutor(max_workers=1)`）：

- 同一张图的自动导出会合并：新任务入队时取消该图未开始的旧自动导出；手动导出不合并。
- 工程数据写回成功后任务即入队并 `mark_clean()`（QUndoStack 干净状态）。因此“未保存”判定只看导出是否已入队，不看渲染是否最终成功；渲染失败时 JSON 已写回，但输出图片不会生成，界面显示失败 Toast。
- 导出期间 Toast 显示“正在导出…”或“正在导出（N 个任务）”；成功显示“导出成功\n<输出路径>\n已同步 JSON”，失败显示“<文件名> 导出失败：<原因>”。
- 关闭应用时若有未完成任务，先弹出“导出任务尚未完成”确认框；选择“是”会排空导出队列再退出，选择“否”则取消关闭。

### 导出流程 {#export-flow}

```mermaid
flowchart LR
    A["菜单「导出图片」/ Ctrl+Q / 切图自动导出"] --> B["提交视图层草稿"]
    B --> C["快照图片、区域、蒙版、画笔/印章层"]
    C --> D["写回 JSON + 修复图"]
    D --> E["加入单线程导出队列"]
    E --> F["后端 load_text 纯渲染\n跳过替换 / 蒙版已精炼 / 禁用自动换行"]
    F --> G["保存输出图片（原子替换）"]
    G --> H["成功 Toast + mark_clean"]
    F -->|"失败"| I["失败 Toast\nJSON 已写回但图片未生成"]
```

## 写回：JSON 与修复图 {#writeback-json-and-inpainted}

### JSON 写回 {#json-writeback}

导出时 `EditorControllerExportService.save_editor_json()` 把当前快照写到 `find_json_path()` 找到的 `*_translations.json`：

- 以原图绝对路径作为顶层键；`regions` 经 `_normalize_regions_for_backend` 规范化：补齐 `translation`、`texts`、`font_size`、`angle`、`target_lang`、`language`、`direction` 等字段，把 `fg_colors`/`fg_color` 元组转成 `font_color` 十六进制，把 `v`/`h` 转成 `vertical`/`horizontal`。
- 恒写 `skip_text_replacements: true`：编辑器 `translation` 字段就是替换后终稿（`translation_raw` 才是替换前），后端重渲染不能再次替换。
- 蒙版存在时写 `mask_raw`（base64 PNG）并标 `mask_is_refined: true`，后端跳过蒙版优化。
- 画笔/印章层有内容时以 base64 PNG（RGBA）写入 `paint_overlay` / `stamp_overlay`。
- 保留已有 JSON 的超分/上色信息与 `last_export_dir`（`preserve_existing_preprocess_flags`），避免下次导出丢失底图来源标志。
- 写入采用“同目录临时文件 + `os.replace`”的原子替换，避免半截 JSON 被后端读到。

### 修复图写回 {#inpainted-writeback}

`save_inpainted_image()` 把当前修复图（没有则用底图充当）写到 `manga_translator_work/inpainted/<图片名>_inpainted.<扩展名>`，质量取 `cli.save_quality`，同样走临时文件 + `os.replace`。后端渲染后若重新生成了修复图，也会回写同一路径（`_persist_backend_inpainted_image`），保证下次编辑看到的是最新修复结果。

### 写回流程 {#writeback-flow}

```mermaid
flowchart LR
    A["编辑操作（QUndoCommand）"] --> B["导出时提交快照"]
    B --> C["_save_regions_data_internal"]
    C --> C1["regions 规范化 + skip_text_replacements"]
    C --> C2["mask_raw base64 PNG + mask_is_refined"]
    C --> C3["paint/stamp overlay base64 PNG"]
    C --> C4["保留 upscale/colorizer/last_export_dir"]
    C1 --> D["临时文件 + os.replace 原子写"]
    C2 --> D
    C3 --> D
    C4 --> D
    D --> E["*_translations.json"]
    B --> F["save_inpainted_image"]
    F --> G["manga_translator_work/inpainted/*_inpainted"]
```

## 依赖与冲突 {#dependencies-and-conflicts}

- 编辑器导出强制 `disable_auto_wrap=True`，因此导出结果不受“启用 AI 断句”等自动换行设置影响；文本框大小和位置以编辑器为准。
- `translation` 恒为替换后终稿：导入旧 JSON 缺 `translation_raw` 时用 `translation` 回填；写回时 `skip_text_replacements` 防止二次替换。
- 批量管理等外部写回会修改 JSON，但编辑器把区域常驻内存且不监听文件变化；批量面板写回后会调用 `load_image_and_regions` 让编辑器重新加载，否则切图自动导出会用旧内存覆盖新写入。相关流程见[批量管理：预览、应用与恢复](../batch-management/preview-apply-restore.md)。
- 切页自动导出依赖导出队列：自动导出被拒绝会中止切图；手动导出时切图等待导出完成。
- `editor_base` 只在 JSON 有超分/上色标记时有效；没有标记时编辑器删除过期底图并回退到原图，避免显示与当前 JSON 不匹配的旧底图。
- JSON 不存在时导出会新建（`find_json_path` 无结果则 `get_json_path(create_dir=True)`）；写入后位于新位置，旧版同目录 JSON 仍可读但不再作为写入目标。

## 关联文件与格式 {#related-files-and-formats}

| 文件/格式 | 本页实际作用 | 手改与兼容注意 |
| --- | --- | --- |
| `*_translations.json` | 编辑器工程数据：区域、蒙版、图层、超分/上色标记、导出目录 | 顶层键为原图绝对路径；新位置 `manga_translator_work/json/`，旧版同目录兼容读取 |
| `manga_translator_work/inpainted/*_inpainted.*` | 修复底图的写回与加载 | 导出前写当前修复图；后端重新生成也会回写同一路径 |
| `manga_translator_work/paint_overlay/*_overlay.png` | 旧版画笔层单文件（RGBA PNG） | JSON 内 `paint_overlay` base64 优先 |
| `manga_translator_work/editor_base/*` | 上色/超分后的编辑器底图 | 无超分/上色标记即视为过期删除 |
| `<输出目录>/translation_map.json` | 结果图 → 原图映射 | 主翻译流程写入；编辑器与文件列表用于解析原图 |
| `cli.format` / `cli.save_quality` / `cli.save_to_source_dir` / `app.last_output_path` | 导出文件名、质量与目录 | 发行配置 `format` 为“不指定”（沿用原图扩展名） |
| `cli.export_editable_psd` / `cli.psd_script_only` | 可编辑 PSD 的开关与脚本模式 | PSD 输出到 `manga_translator_work/psd/`；失败只记日志不中断图片导出 |
| 输出图片格式 | `png` / `jpg` / `webp` 等 | 由 `cli.format` 或原图扩展名决定，按 `resolve_pil_image_format` 编码 |

## Mermaid 数据流限制 {#mermaid-limits}

三张图描述的是源码确认的导入、导出与写回数据流，不代表每次操作都会成功：导出可能因缺少图片/蒙版、队列关闭或渲染失败而中止；没有未保存编辑时切图不触发导出；自动导出合并只作用于同一张图的未开始任务。本页没有伪造运行截图或私有任务产物。

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| UI | `desktop_qt_ui/ui/widgets/editor_toolbar.py` | 导出菜单项与“切图时自动导出”开关 |
| UI | `desktop_qt_ui/ui/editor/view.py` | 导出统一入口 `export_image`、文件列表按钮接线、浮动编辑器 `flush_pending_changes` |
| UI | `desktop_qt_ui/ui/editor/shortcut_manager.py` | `Ctrl+Q` 导出快捷键注册与焦点分派 |
| 主窗口 | `desktop_qt_ui/ui/main_window.py` | “任务完成 → 在编辑器中打开结果？”、退出时未完成导出确认与排空 |
| 列表逻辑 | `desktop_qt_ui/editor/editor_logic.py` | 添加文件/文件夹、清空列表、`load_image_into_editor` |
| 文档服务 | `desktop_qt_ui/editor/controller_document_service.py` | `translation_map.json` 解析、`editor_base` 过期判断、未保存三按钮对话框、延迟加载 |
| 文档加载 | `desktop_qt_ui/editor/document_load_worker.py` | JSON/修复图/画笔层的后台并行加载 |
| 导出服务 | `desktop_qt_ui/editor/controller_export_service.py` | 快照、`save_editor_json`、`save_inpainted_image`、输出路径、队列与 `mark_clean` |
| 写回/渲染 | `desktop_qt_ui/services/export_service.py` | `_save_regions_data_with_path`/`_save_regions_data_internal`、原子写、`load_text` 内存载荷直通 |
| 路径/格式 | `manga_translator/utils/path_manager.py`、`manga_translator/image_formats.py` | 工作目录布局、新旧 JSON 发现、输出格式解析 |
| 后端 | `manga_translator/manga_translator.py` | `translation_map.json` 写入、`load_text` 读取与 `skip_text_replacements` 分支 |
| i18n | `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | 三列表实际值；硬编码中文如实标注 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已完整读取并按页面合同编写（1.3 节、5.10 小节） |
| UI/i18n 文案 | 完成 | 静态核对 `editor_toolbar.py`、`view.py`、`main_window.py` 的调用 key 与两份 locale 实际值；硬编码中文已标注 |
| 导入/导出/写回运行链 | 完成 | 静态核对 `document_load_worker.py`、`controller_export_service.py`、`export_service.py`、`path_manager.py`、`manga_translator.py` |
| 脱敏运行验证 | 待后续 | 未读取真实用户图片、工程 JSON、密钥或私有路径；有头模式截图由后续阶段采集 |
| VitePress | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` 及镜像/源码检查 |