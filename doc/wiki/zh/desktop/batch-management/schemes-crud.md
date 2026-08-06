---
title: 批量方案管理
description: 管理批量编辑方案：查看、新建、复制、重命名、删除与自动保存
pageId: desktop.batch-management.schemes-crud
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 批量方案管理

当你想把“筛选哪些区域、对这些区域做什么”这组批量编辑设置保存下来并在后续会话复用，就把它们存成一个批量方案（`Scheme`）。方案由名称、匹配条件（`match`）和动作（`actions`）三部分组成；本页只负责方案的列表与新建、复制、重命名、删除，以及自动保存与持久化。匹配条件的字段、运算符与 `all`/`any` 逻辑见[匹配条件](./conditions.md)，动作的类型与固定执行顺序见[动作与顺序](./actions-and-order.md)，命中预览、批量写回与从备份恢复见[预览、执行与恢复](./preview-apply-restore.md)。

## 功能边界 {#feature-boundary}

- 一个方案 = 名称 + `match`（`logic` + `conditions`）+ `actions`；方案只保存在 `config/batch_edit_schemes.yaml`，不写入 `config/config.json`，也不参与渲染或翻译管线。
- 方案条的“新建 / 重命名 / 复制 / 删除”只管理方案本身；条件行、逻辑下拉框与三类动作卡片是相邻页面的内容，本页只说明它们作为方案的一部分被保存。
- 批量管理的作用范围跟随主页文件列表：面板底部显示“范围：主页文件列表中的 {count} 个已翻译文件”（`Scope: {count} translated files from the main file list`）。文件列表本身在[文件列表与输入](../translation/file-list-and-input.md)页管理。
- 方案内容不含密钥或用户私有数据；方案名、条件值与动作字段可能包含业务文本，公开报告前必须脱敏。

## UI 操作 {#ui-operations}

### 查看与切换方案

1. 从左侧导航打开“批量管理”（`Batch Management`）页。页面标题为“批量管理”，副标题为“跨主页文件列表匹配区域，批量修改文字、富文本样式与属性”。
2. 顶部方案条左侧是“方案:”（`Scheme:`）下拉框，列出所有方案；右侧依次是“新建”（`New`）、“重命名”（`Rename`）、“复制”（`Duplicate`）、“删除”（`Delete`）四个按钮。
3. 选中某个方案后，下方“匹配条件”“批量动作”和预览区会载入该方案的内容；条件与动作的编辑属于相邻页面。
4. 切换方案时，如果当前方案还有未落盘的修改，程序会先停止防抖计时器并保存当前方案，再载入新选中的方案。
5. 切回本页时，若没有待保存的修改，面板会重新从磁盘读取方案列表并选中第一项；若有待保存修改则跳过重新读取，避免覆盖内存中的编辑。

### 新建方案

1. 点击“新建”（`New`）。弹出文本输入框，标题为“新方案”（`New scheme`），字段标签为“方案名称”（`Scheme name`），按钮为“确定”（`OK`）/“取消”（`Cancel`）。
2. 输入名称后回车或点击“确定”。名称会去除首尾空白；空名称等同取消，不会创建方案。
3. 名称与现有方案重名时弹出警告“已存在名为“{name}”的方案。”，本次创建中止，需要换一个名称。
4. 新方案没有匹配条件和动作，需要先在[匹配条件](./conditions.md)与[动作与顺序](./actions-and-order.md)中补充内容；任何一次保存都会自动写盘。

### 复制方案

1. 选中要复制的方案，点击“复制”（`Duplicate`）。
2. 输入框默认名称为“`<原方案名> 2`”，字段标签仍为“方案名称”。
3. 确认后程序把当前方案的 `match` 与 `actions` 深拷贝到新方案，只替换名称，然后切到新方案并保存。
4. 复制同样遵守空名称与重名规则；重复复制时默认名可能已经存在，需要手动改名。

### 重命名方案

1. 选中方案，点击“重命名”（`Rename`）。输入框默认显示当前名称。
2. 确认后只修改名称，条件和动作保持不变，随后保存。
3. 重名时弹出同样的“已存在名为“{name}”的方案。”警告并中止。

### 删除方案

1. 选中方案，点击“删除”（`Delete`）。
2. 弹出确认框“删除方案“{name}”？”（`Delete scheme '{name}'?`），默认按钮为“否”；只有点击“是”才继续。
3. 删除前会停掉未触发的自动保存；删除后如果列表为空，面板自动创建一个默认“新方案”（`New scheme`）。
4. 删除方案只影响 `config/batch_edit_schemes.yaml`，不会删除或修改任何逐图 JSON、备份或翻译结果。

### 自动保存与状态提示

- 修改条件、动作或逻辑后，面板启动 600 ms 防抖计时器，等待期间不写盘；计时结束后调用一次 `save_schemes()` 整体写回，状态栏显示“已自动保存”（`Saved automatically`）。
- 写盘遇到 `OSError` 时状态栏显示“保存失败”（`Save error`）加错误信息，不弹窗。
- 切换方案时，未落盘的修改会先写盘；删除确认后，未落盘的修改被丢弃。
- 关闭应用时 `shutdown()` 会先停掉计时器并把待保存修改写盘，再关闭后台服务。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Batch Management` | Batch Management | 批量管理 |
| `Match regions across the main file list and edit their text, styling, and properties in bulk` | Match regions across the main file list and edit their text, styling, and properties in bulk | 跨主页文件列表匹配区域，批量修改文字、富文本样式与属性 |
| `Scheme:` | Scheme: | 方案: |
| `New` | New | 新建 |
| `Rename` | Rename | 重命名 |
| `Duplicate` | Duplicate | 复制 |
| `Delete` | Delete | 删除 |
| `New scheme` | New scheme | 新方案 |
| `Scheme name` | Scheme name | 方案名称 |
| `OK` | OK | 确定 |
| `Cancel` | Cancel | 取消 |
| `Delete scheme '{name}'?` | Delete scheme '{name}'? | 删除方案“{name}”？ |
| `A scheme named '{name}' already exists.` | A scheme named '{name}' already exists. | 已存在名为“{name}”的方案。 |
| `Saved automatically` | Saved automatically | 已自动保存 |
| `Save error` | Save error | 保存失败 |
| `Scope: {count} translated files from the main file list` | Scope: {count} translated files from the main file list | 范围：主页文件列表中的 {count} 个已翻译文件 |

## 空状态与错误状态 {#empty-and-error-states}

| 触发条件 | 界面表现 | 后续行为 |
| --- | --- | --- |
| `config/batch_edit_schemes.yaml` 不存在 | 首次访问时按内置示例创建文件；下拉框显示示例方案 | 可直接使用，或重命名/删除后得到默认方案 |
| 文件损坏或 YAML 解析失败 | `load_schemes()` 返回空列表，下拉框临时显示一个“新方案” | 任何一次保存都会把内存列表整体覆盖写回文件 |
| 文件存在但没有任何有效方案条目 | 同上，临时“新方案” | 同上 |
| 名称输入为空或仅空白 | 新建/复制/重命名直接取消，无提示 | 不产生任何写盘 |
| 名称与现有方案重名 | 弹出“已存在名为“{name}”的方案。”警告 | 本次操作中止，需改名 |
| 保存失败（I/O 错误） | 状态栏显示“保存失败: {error}” | 内存列表保留，可重试保存 |

错误信息可能包含本机路径；复制到公开报告前必须脱敏。

## 运行机理 {#runtime-behavior}

方案的读取、编辑和保存共用同一条数据流：

```mermaid
flowchart TD
    A["打开批量管理页\n_load_schemes()"] --> B{"config/batch_edit_schemes.yaml 存在?"}
    B -->|否| C["ensure_schemes_exists 写入内置示例方案"]
    B -->|是| D["load_schemes() 归一化条目"]
    C --> E["下拉框填充方案名"]
    D --> E
    E --> F{"用户操作"}
    F -->|新建 New| G["输入名称：空名取消 / 重名警告"]
    F -->|复制 Duplicate| H["默认名 \"<原名> 2\"，深拷贝 match 与 actions"]
    F -->|重命名 Rename| I["只改名称，条件与动作不变"]
    F -->|删除 Delete| J["确认“是”后移除；删空则重建默认“新方案”"]
    F -->|修改条件或动作| K["600ms 防抖后整体保存"]
    G --> L["save_schemes() 写回 YAML"]
    H --> L
    I --> L
    J --> L
    K --> L
    L --> M["状态栏“已自动保存”"]
```

- 读取：`load_schemes()` 先 `ensure_schemes_exists()` 惰性创建文件，再用 `yaml.safe_load` 解析；条目经过 `normalize_scheme()`：空名称丢弃、非法动作丢弃、`actions` 按 `set_fields -> replace_text -> rich_text` 稳定排序。
- 收集：`_collect_scheme()` 从逻辑下拉框、条件行和三个动作卡片收集数据，`enabled` 恒写为 `True`，再经 `normalize_scheme()` 归一化。
- 写回：`save_schemes()` 用 `yaml.safe_dump(allow_unicode=True, sort_keys=False, width=120)` 序列化整个列表，以 UTF-8、LF 换行写入；每次保存都是整体覆盖，不依赖文件原有内容。
- 防抖：`_AUTOSAVE_DELAY_MS = 600`，`_mark_dirty()` 在条件或动作变化时启动单次计时器并清空上次预览结果；`_save_current_scheme()` 保存成功后发出 `data_changed` 信号（当前源码静态核对未发现连接方，保留给外部集成）。
- `enabled` 字段会被读取保留，但当前 UI 保存时恒为 `True`，批量引擎也不按该字段过滤方案；不要依赖它做启用/停用开关。

## 依赖与冲突 {#dependencies-and-conflicts}

- 方案文件只服务桌面批量管理页，`batch_edit_schemes.py` 明确不加入 `manga_translator/runtime_files.py` 的启动引导，因此方案不会进入渲染或翻译管线。
- 方案不写入 `config/config.json` 或任何配置模型；它不属于设置页参数。
- 切换、复制、重命名或删除方案不会修改逐图 JSON、`.bak` 备份或编辑器内存；编辑器冲突与写回时机见[预览、执行与恢复](./preview-apply-restore.md)。
- 条件或动作被修改后，上一次的命中预览立即作废（表格清空、执行按钮禁用），因为盘上结果已经不可信；预览与执行细节见[预览、执行与恢复](./preview-apply-restore.md)。
- 方案名会显示在下拉框中，可能包含业务文本；共享截图或日志前需检查方案名、条件值和动作内容。

## 关联文件与格式 {#files-and-formats}

| 文件/格式 | 本页实际作用 | 手改与兼容注意 |
| --- | --- | --- |
| `config/batch_edit_schemes.yaml` | 方案持久化文件：顶层 `schemes` 列表，每项含 `name`、`enabled`、`comment`、`match`、`actions` | 用 `yaml.safe_load`/`safe_dump` 读写；手改需保持可解析 YAML，非法条目在下次保存时被丢弃 |
| `config/` | 运行时外部配置目录，由 `get_config_path()` 解析 | 开发环境为仓库 `config/`，冻结包为可执行文件旁的 `config/`；不写入真实用户私密路径 |
| `*_translations.json`、`<json-file>.bak` | 逐图译文与备份，由预览/写回/恢复流程管理 | 本页 CRUD 不触碰它们；相关行为见[预览、执行与恢复](./preview-apply-restore.md) |
| `config/config.json` | 设置页参数持久化 | 方案不写入该文件，也不由配置模型管理 |

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 页面容器 | `desktop_qt_ui/ui/main_page/pages/batch_edit_page.py`、`desktop_qt_ui/ui/main_page/layout.py` | 页面标题/副标题、面板嵌入、页面激活时的刷新与快照同步 |
| 方案面板 | `desktop_qt_ui/ui/secondary_pages/batch_edit_panel.py` | 方案条、新建/重命名/复制/删除、名称校验、防抖自动保存、状态栏、`data_changed` |
| 持久化 | `desktop_qt_ui/services/batch_edit_schemes.py` | YAML 结构、惰性创建、归一化、`save_schemes()` 写回 |
| 运行时路径 | `manga_translator/runtime_paths.py` | `get_config_path()` 决定 `batch_edit_schemes.yaml` 的实际位置 |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | key 映射与页表实际中英文显示值 |
| 文件列表范围 | `desktop_qt_ui/ui/main_window.py`、`desktop_qt_ui/ui/main_page/pages/translation_page.py` | 主页文件目录快照推入与刷新 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已完整读取并按页面合同编写 |
| UI 布局与调用 | 完成 | 静态核对批量管理页、方案面板与名称输入对话框 |
| `en_US` / `zh_CN` 实际 locale | 完成 | 页面表格逐项记录 key、English、简体中文实际值 |
| 方案 CRUD 与持久化链路 | 完成 | 静态核对读取、归一化、防抖保存、删除重建与关闭落盘 |
| 脱敏运行验证 | 待后续 | 本页未读取真实 `.env`、用户 `config.json`、API key/token、用户名、用户图片或私有提示词 |
| VitePress | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` 及镜像/源码检查 |
