---
title: 替换规则的 Raw YAML 编辑、正则与保存
description: 在替换规则页用表格或 Raw YAML 编辑渲染前文本替换规则，并理解正则语义、自动保存、恢复默认与渲染消费
pageId: desktop.replacement-rules.raw-yaml-regex-and-save
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 替换规则的 Raw YAML 编辑、正则与保存

当你想在译文排版到图片之前统一标点、修正全半角字符或做其他文本规范化时，使用“替换规则”（`Replacement Rules`）页面编辑 `config/text_replacements.yaml`。页面提供“表格视图”（`Table View`）与“源码编辑”（`Raw Edit`）两种模式，操作同一个文件；每条规则要么是字面替换，要么是正则替换，改动会自动保存。本页说明两种模式的用法、正则语义、保存与恢复机制，以及规则在渲染阶段的消费方式。

表格视图的行级操作、分组页签与分组执行顺序的完整说明见[表格分组与顺序](./table-groups-and-order.md)；富文本规则（在替换完成后对 `translation` 做样式匹配）见[富文本规则](../rich-text-rules/table-raw-and-match.md)。

## 功能边界 {#feature-boundary}

- 本页只读写 `config/text_replacements.yaml`：该文件保存渲染前的文本替换规则，不保存 API 凭据、翻译器选择、`.env` 或任何其他配置。
- 表格视图与 Raw 视图编辑的是同一个文件、同一组规则；切到 Raw 模式时表格工具栏和筛选行会被禁用（`_set_table_controls_enabled(False)`），避免两处同时编辑。
- 规则只在渲染阶段由 `apply_replacements` 消费，作用于区域译文（`region.translation`）；不改变原文、OCR 文本或翻译请求。
- 富文本规则读取替换完成后的 `translation` 做样式匹配，属于另一份文件 `config/rich_text_rules.yaml` 与另一个页面，不在本页。
- 不要把真实业务文本、密钥、用户名或私有绝对路径写进规则文件；文件内容会被渲染逐条读取，并可能出现在日志与调试产物中。

## UI 操作 {#ui-operations}

### 打开替换规则页 {#open-page}

1. 在主导航点击“替换规则”（`Replacement Rules`）进入页面；标题下方副标题为“管理应用到译文的文本替换规则（顺序：通用（始终执行）→ 横排/竖排；规则由上到下级联替换）”。
2. 面板顶部是工具栏，中部是“表格视图 / 源码编辑”（`Table View` / `Raw Edit`）模式切换，底部状态栏显示当前分组规则数量与模式。
3. 面板提供 `refresh()` 公共接口（重新加载文件并套用当前筛选）；切换语言时 `refresh_ui_texts()` 会刷新全部按钮与列头文案。

### 表格视图编辑 {#table-view-editing}

1. 分组页签：`Common (Always)`（通用，始终执行）、`Horizontal`（横排）、`Vertical`（竖排）。
2. 表格列：`Enabled`（启用，`✓`/`✗`）、`Pattern`（匹配）、`Replace`（替换）、`Regex`（正则，`✓`/`✗`）、`Comment`（备注）。`Enabled` 与 `Regex` 列不可直接输入，双击单元格切换 `✓`/`✗`。
3. 工具栏按钮：`Add Rule`（添加规则）、`Delete`（删除）、`↑`/`↓`（上移/下移，图标按钮无文字）、`Select All`（全部选中）、`Enable`/`Disable`（启用/禁用，按选中行多数状态动态显示）、`Regex`/`Cancel Regex`（正则/取消正则，动态显示）、`Restore Default`（恢复默认）。
4. “添加规则”会插入一行新规则并直接进入“匹配”单元格编辑；保存时 `Pattern` 为空的行会被跳过。
5. 筛选框（`Filter:`）按“匹配 / 替换 / 备注”做大小写不敏感的子串过滤，只影响显示，不改变文件内容。

### Raw YAML 编辑 {#raw-yaml-editing}

1. 点击“源码编辑”（`Raw Edit`）进入等宽字体编辑器；顶部提示“直接编辑原始 YAML 内容，修改会自动保存。”。编辑器关闭自动换行，并提供简单 YAML 语法高亮（注释斜体、键名加粗）。
2. 切到 Raw 模式时，若表格有未保存改动会先按表格内容保存，再把序列化后的完整 YAML 填入编辑器；因此编辑器里看到的是与表格一致的内容。
3. 从 Raw 切回“表格视图”（`Table View`）时，编辑器先解析 YAML：解析失败会弹出“解析错误 / YAML 语法错误，无法切换到表格视图。”并留在 Raw 模式；解析成功则按三个分组重建表格。
4. Raw 模式下你手写的键、缩进与转义由你自己负责；保存只校验“能否被 YAML 解析”，不校验根结构是否为对象（根类型错误只在切回表格视图时报错）。

### 界面文案 {#ui-copy}

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Replacement Rules` | Replacement Rules | 替换规则 |
| `Manage text replacement rules applied to translations before rendering` | Manage text replacements (order: Common (Always), then Horizontal/Vertical; rules cascade from top to bottom). | 管理应用到译文的文本替换规则（顺序：通用（始终执行）→ 横排/竖排；规则由上到下级联替换） |
| `Table View` | Table View | 表格视图 |
| `Raw Edit` | Raw Edit | 源码编辑 |
| `Common (Always)` | Common (Always) | 通用（始终执行） |
| `Horizontal` | Horizontal | 横排 |
| `Vertical` | Vertical | 竖排 |
| `Enabled` | Enabled | 启用 |
| `Pattern` | Pattern | 匹配 |
| `Replace` | Replace | 替换 |
| `Regex` | Regex | 正则 |
| `Comment` | Comment | 备注 |
| `Add Rule` | Add Rule | 添加规则 |
| `Delete` | Delete | 删除 |
| `Select All` | Select All | 全部选中 |
| `Enable` | Enable | 启用 |
| `Disable` | Disable | 禁用 |
| `Cancel Regex` | Cancel Regex | 取消正则 |
| `Restore Default` | Restore Default | 恢复默认 |
| `Filter:` | Filter: | 过滤: |
| `Type to filter by pattern / replace / comment...` | Type to filter by pattern / replace / comment... | 输入匹配 / 替换 / 备注以过滤... |
| `Edit raw YAML content directly. Changes are saved automatically.` | Edit raw YAML content directly. Changes are saved automatically. | 直接编辑原始 YAML 内容，修改会自动保存。 |
| `enabled` | enabled | 已启用 |
| `File not found` | File not found | 文件不存在 |
| `Load error` | Load error | 加载失败 |
| `Saved automatically` | Saved automatically | 已自动保存 |
| `Save error` | Save error | 保存失败 |
| `Save Error` | Save Error | 保存错误 |
| `YAML syntax error, changes not saved.` | YAML syntax error, changes not saved. | YAML 语法错误，修改尚未保存。 |
| `Parse Error` | Parse Error | 解析错误 |
| `YAML syntax error, cannot switch to table view.` | YAML syntax error, cannot switch to table view. | YAML 语法错误，无法切换到表格视图。 |
| `Restore replacement rules to the built-in defaults? Current custom rules will be overwritten.` | Restore replacement rules to the built-in defaults? Current custom rules will be overwritten. | 要将替换规则恢复为内置默认值吗？当前自定义规则会被覆盖。 |
| `Defaults restored` | Defaults restored | 已恢复默认 |
| `Restore default failed` | Restore default failed | 恢复默认失败 |

`✓`/`✗`、`↑`/`↓` 和状态栏中的 `●` 是代码常量，不是 i18n key；恢复默认确认框的 `Yes`/`No` 按钮由 `themed_message_box` 统一映射，也不是 `_t` key。状态栏格式为“`分组: 已启用数/总数 enabled/已启用 ● [模式]`”，例如 `common: 2/3 enabled ●  [Table View]`。

## 规则格式与正则语义 {#rule-format-and-regex}

### 规则字段 {#rule-fields}

文件顶层必须是对象，包含三个列表键：`common`、`horizontal`、`vertical`。每条规则是一个对象：

| 字段 | 必填 | 语义 |
| --- | --- | --- |
| `pattern` | 是 | 匹配模式；`regex: true` 时按 Python `re` 语法解析，否则按字面文本匹配 |
| `replace` | 是 | 替换内容；正则模式下支持反向引用 `\1`、`\2` 等 |
| `regex` | 否 | 默认 `false`（字面替换）；`true` 时 `pattern` 作为正则处理 |
| `enabled` | 否 | 默认 `true`；`false` 表示临时禁用该条规则 |
| `comment` | 否 | 备注，不参与匹配 |

### 字面替换与正则替换 {#literal-vs-regex}

运行时对每条规则做一次编译：字面替换调用 `re.compile(re.escape(pattern))`，正则替换调用 `re.compile(pattern)`；`pattern` 为空或 `enabled: false` 的规则被跳过。正则编译失败（`re.error`）时该条规则被跳过并写 warning 日志，不影响其他规则，也不会让渲染失败。因此：

- 字面替换中出现的 `.`、`(`、`\` 等正则特殊字符会被原样匹配，无需转义；
- 正则替换按 Python `re` 语法匹配，`\d`、`\.{3}`、反向引用 `\1` 等均可用；`replace` 中的 `\1` 引用第一个捕获组。

```mermaid
flowchart TD
    RULE["一条规则：pattern / replace / regex / enabled / comment"] --> CHECK{"enabled = false 或\npattern 为空?"}
    CHECK -->|"是"| SKIP["跳过该条规则（不参与替换）"]
    CHECK -->|"否"| ISREGEX{"regex = true?"}
    ISREGEX -->|"是"| RC["re.compile(pattern) 按 Python 正则语法匹配"]
    ISREGEX -->|"否"| LC["re.compile(re.escape(pattern)) 按字面逐字符匹配"]
    RC --> SUB["pattern.sub(replace, 译文)"]
    LC --> SUB
    SUB --> OUT["下一次渲染时该区域译文被替换"]
```

### 分组、方向与执行顺序 {#groups-direction-and-order}

- 执行顺序固定：先按文件顺序应用 `common` 全部规则；再按区域排版方向选择 `horizontal`（`direction == 0`，横排）或 `vertical`（`direction == 1`，竖排）分组，继续自上而下应用。
- 同一分组内规则按 YAML 列表顺序级联执行：前一条的输出是后一条的输入，因此“先替换 A 再替换 B”与“先替换 B 再替换 A”的结果可能不同。
- 方向在渲染阶段判定：`_resolve_region_render_horizontal` 先看区域强制方向（`horizontal`/`h` 或 `vertical`/`v`），`auto` 时回退到区域的 `horizontal` 属性。

```mermaid
flowchart TD
    IN["区域译文"] --> PROT["保护 [BR] / <br> / 【BR】 换行标记"]
    PROT --> COMMON["按顺序应用 common 分组全部规则"]
    COMMON --> DIR{"区域排版方向?"}
    DIR -->|"direction = 0 横排"| H["应用 horizontal 分组规则"]
    DIR -->|"direction = 1 竖排"| V["应用 vertical 分组规则"]
    H --> RESTORE["恢复被保护的换行标记"]
    V --> RESTORE
    RESTORE --> OUT["渲染使用的终稿文本"]
```

### 换行标记保护 {#linebreak-protection}

`apply_replacements` 会先把 `[BR]`、`【BR】`、`<br>`、`<br/>` 等换行标记替换为占位符，替换完成后再恢复。因此即使规则写到了这些标记，也不会命中受保护的占位符。

## 保存与恢复机制 {#save-and-restore}

### 自动保存 {#autosave}

任何编辑动作（改单元格、双击切换、增删/移动行、Raw 文本变化）都会调用 `_mark_modified`：把面板标记为已修改，启动 600ms 的单次防抖定时器，并刷新状态栏（追加 `●`）。定时器到点且仍有未保存修改时按当前模式保存：

- 表格模式用 `_tables_to_yaml()` 序列化（`allow_unicode=True`、`default_flow_style=False`、`sort_keys=False`；`Pattern` 为空的行会被跳过，`regex`/`enabled`/`comment` 为默认值或空时省略）；
- Raw 模式先 `yaml.safe_load` 校验语法，再原样写回。

写入成功后会清空渲染缓存（`invalidate_replacements_cache`）、停止定时器、清除修改标记，并把状态栏设为“已自动保存”（`Saved automatically`）。正常编辑不需要手动保存。

### 模式切换时的保存与校验 {#mode-switch-save}

- 表格 → Raw：先按当前表格内容保存一次（失败只更新状态栏，不弹窗），再把序列化结果填入编辑器，保证 Raw 编辑器反映表格最终状态。
- Raw → 表格：先解析编辑器内容。解析失败弹出“解析错误 / YAML 语法错误，无法切换到表格视图。”并留在 Raw 模式；解析成功且存在未保存修改时先保存，再按三个分组重建表格。
- Raw 模式自动保存失败（YAML 语法错误）时，状态栏显示“YAML 语法错误，修改尚未保存。”，文件保持最后一次成功写入的内容；修复语法后再次编辑或切换模式即可保存。

```mermaid
flowchart TD
    EDIT["表格或 Raw 编辑"] --> MARK["_mark_modified：标记已修改 + 启动 600ms 单次定时器"]
    MARK --> TIMEOUT{"600ms 内又有新改动?"}
    TIMEOUT -->|"是"| MARK
    TIMEOUT -->|"否"| SAVE["按当前模式保存"]
    SAVE --> VALID{"Raw 内容可解析?\n（表格模式恒可序列化）"}
    VALID -->|"否"| ERR["状态栏：YAML 语法错误，修改尚未保存。\n文件保持上次成功写入内容"]
    VALID -->|"是"| WRITE["写回 config/text_replacements.yaml（UTF-8）"]
    WRITE --> CACHE["invalidate_replacements_cache 清渲染缓存"]
    CACHE --> DONE["状态栏：已自动保存；下一次渲染读取新规则"]
    RESTORE["Restore Default 确认框（默认 No）"] -->|"Yes"| RESET["reset_text_replacements_to_default 写回内置默认模板 + 清缓存"]
    RESET --> RELOAD["重新加载文件；状态栏：已恢复默认"]
```

### 恢复默认 {#restore-default}

点击“恢复默认”（`Restore Default`）会弹出确认框（标题 `Restore Default`，正文“要将替换规则恢复为内置默认值吗？当前自定义规则会被覆盖。”，按钮 `Yes`/`No`，默认 `No`）。确认后：

1. 停止自动保存定时器，调用 `reset_text_replacements_to_default` 把内置默认模板写入 `config/text_replacements.yaml`；
2. 清空渲染缓存，重新加载文件并套用当前筛选；
3. 状态栏显示“已恢复默认”（`Defaults restored`），面板发出 `data_changed` 信号。

恢复默认会直接覆盖当前自定义规则且没有 `.bak` 备份（与批量管理方案的备份/恢复机制不同），确认前请确认当前规则不再需要。

### 渲染缓存与启动升级 {#cache-and-startup}

- 渲染侧 `load_replacements` 以“文件路径 → (mtime, 解析结果)”缓存规则；保存与恢复默认都会清缓存，因此下一次渲染会重新读取文件。手动改文件但 mtime 未变化时可能命中旧缓存。
- 启动初始化 `ensure_runtime_files` 会删除仍为历史内置模板 MD5 的 `text_replacements.yaml`（两个已知旧哈希：`5b8fbc89492ff2a1d5c064f5e85a458b`、`94b2787940afdde800db3aba0742ad98`），随后 `ensure_text_replacements_exists` 重建内置默认模板；用户自定义内容不受影响。
- 文件不存在时编辑器状态栏显示“文件不存在”（`File not found`）；读取失败显示“加载失败: {error}”（`Load error: {error}`）。

## 依赖与冲突 {#dependencies-and-conflicts}

- 规则只作用于渲染前的译文：`prepare_text_replacements_for_layout` 把替换前文本存入 `translation_raw`，替换结果写入 `translation`；渲染后 `sync_translation_raw_from_layout` 会把排版改动回映到 `translation_raw`。
- 富文本文档区域（`is_rich_text_document` 为真）跳过替换；已渲染过的 JSON 导出会记录 `skip_text_replacements`，导入重渲染时不再二次替换。
- 规则文件不参与翻译请求、API 候选轮换或词典（`pre_dict`/`post_dict`）流程；词典是另一套 `apply_dictionary` 消费者。
- 正则语法错误只跳过出错的那条规则，不会让渲染失败；但该条预期替换不会生效。
- 恢复默认、保存失败和 Raw 语法错误的处理见上文；共享日志或调试目录前不要包含规则文件中的业务文本。

## 关联文件与格式 {#related-files}

| 文件/格式 | 本页实际作用 | 手改与兼容注意 |
| --- | --- | --- |
| `config/text_replacements.yaml` | 本页唯一读写文件；三组规则的 YAML 源 | 根必须为对象；UTF-8；`yaml.safe_load` 解析；Raw 保存只校验语法 |
| 内置默认模板 `_DEFAULT_REPLACEMENTS_YAML` | “恢复默认”写入的内容 | `common` 两条示例（中点统一、`\.{3}` → `…`），`horizontal`/`vertical` 为全角/竖排标点映射；恢复会覆盖自定义规则 |
| `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | 页面与编辑器文案 | key 与实际显示值见上文表格 |
| `config/rich_text_rules.yaml` | 富文本规则，替换完成后消费 `translation` | 见[富文本规则](../rich-text-rules/table-raw-and-match.md)，不属于本页 |

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 编辑器 UI | `desktop_qt_ui/ui/secondary_pages/replacements_editor.py` | 表格/Raw 双模式、工具栏、筛选、自动保存、恢复默认、状态栏 |
| 页面与导航 | `desktop_qt_ui/ui/main_page/pages/replacements_page.py`、`ui/main_page/view.py` | 页面标题/副标题、导航注册（`replacements`）、语言与主题刷新 |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | key 映射与两个 locale 的实际显示值 |
| 文件初始化 | `manga_translator/runtime_files.py`、`rendering/text_replacements.py` | `ensure_text_replacements_exists`、历史默认升级、`reset_text_replacements_to_default` |
| 规则编译与缓存 | `manga_translator/rendering/text_replacements.py` | 字面/正则编译、mtime 缓存、换行标记保护、`apply_replacements` 顺序 |
| 渲染消费者 | `manga_translator/rendering/text_replacement_layout.py`、`rendering/__init__.py`、`manga_translator/manga_translator.py` | 方向判定、`translation_raw`/`translation`、`skip_text_replacements`、JSON 导出标志 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已读取 1.3 节、5.8 小节与 6.2 小节并按页面合同编写 |
| 编辑器 UI 与调用链 | 完成 | 静态核对 `replacements_editor.py`、`replacements_page.py`、`view.py` 与导航注册 |
| `en_US` / `zh_CN` 实际 locale | 完成 | 表格逐项记录 key、English、简体中文实际值 |
| 正则、保存与恢复运行链 | 完成 | 静态核对 `text_replacements.py`、`text_replacement_layout.py`、`rendering/__init__.py`、`manga_translator.py` |
| 脱敏运行验证 | 待后续 | 未读取真实 `.env`、用户 `config.json`、API key/token、用户名、用户图片或私有提示词 |
| VitePress | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` 及镜像/源码检查 |