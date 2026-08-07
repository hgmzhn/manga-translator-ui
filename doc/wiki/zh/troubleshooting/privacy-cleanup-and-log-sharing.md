---
title: 隐私、清理与日志分享
description: 清理各类运行数据，脱敏日志与调试产物，并安全地对外分享日志
pageId: troubleshooting.privacy-cleanup-and-log-sharing
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 隐私、清理与日志分享

当你要释放磁盘空间、删除含个人内容的运行数据，或把日志和调试产物发给别人排查问题时，这里说明数据清理的对象与顺序、脱敏规则以及日志分享的注意事项。逐类调试产物的生成阶段和阅读顺序见[如何阅读与分享一次调试运行](../debugging/how-to-read-and-share-a-debug-run.md)；按安装形态完整卸载见[卸载与数据清理](../install/uninstall-and-data-cleanup.md)；Web 管理界面的完整操作见[管理界面](../web/administrator-interface.md)。

## 先确认问题 {#scope}

- 内容包括：日志和运行数据的清理、对外分享前的脱敏规则、桌面/CLI 日志与 Web 会话/系统日志的查看导出，以及运行时配置表的自动重建。
- 这里不重复：数据保存位置与外发边界（见数据与隐私页）、逐类调试产物的含义（见调试运行页）、按安装形态的卸载步骤（见卸载页）、Web 管理界面的全部按钮与状态（见管理界面页）。
- 清理不是卸载，也不是备份。删除 `.env`、`config/`、服务器 `data/`、结果、模型缓存和调试目录都是不可逆操作；开始前先确认范围并备份需要保留的内容。

## 操作方法 {#operations}

### 清理运行数据 {#cleanup-data}

桌面端清理前先完全退出 Qt UI（或停止 CLI），否则 `result/` 下的日志文件被文件处理器占用，Windows 上可能删除失败：

1. 打开“设置”→“通用”，查看“详细日志”说明面板中给出的清理方法：先关闭 Qt UI，再删除 `result/` 下不需要的 `log_*.txt` 和对应的时间戳调试文件夹。
2. 删除 `result/` 下的 `log_<时间戳>.txt` 和 `时间戳-图片MD5-尺寸-语言-翻译器/` 调试子目录；两者配套删除，不要只删一半。
3. 输入目录旁的 `manga_translator_work/` 可能含逐图 JSON、导出文本、PSD/JSX 和中间图片，按任务选择性删除。
4. `config/` 下的运行时表（过滤列表、替换规则、富文本规则、翻译模板、提示词等）删除后会在下次启动时由 `ensure_runtime_files()` 重建默认值，但自定义修改会丢失。

Web 管理端的“清理”模块只作用于服务器清理服务定义的目录，不是删除程序目录的卸载器，也不覆盖桌面 `result/` 或输入目录旁的工作目录。结果页的“清空翻译结果”只清空浏览器结果列表和 blob URL，不等于删除服务器上的结果文件。

### 查看与导出日志 {#view-and-export-logs}

- 桌面端与 CLI 的运行日志都写在应用根目录（开发环境为仓库根目录，打包版为可执行文件所在目录）的 `result/log_<yyyyMMddHHmmss>.txt`。翻译出错弹出“翻译错误”对话框时，可点击“打开日志文件夹”跳转到日志目录。
- Web 管理端的“日志”模块可查看系统日志和对话框日志，按会话或级别过滤，并可“导出日志”或“清空日志”。
- 分享日志前按本页“脱敏与日志分享”一节处理：删除本机路径、凭据、用户正文和会话令牌，只保留版本、平台与复现步骤。

### 配置导入导出与密钥显示 {#config-import-export}

- “导出配置”的提示文案说明导出文件不包含 API 密钥等敏感信息；“导入配置”会保留已有敏感信息不被覆盖。
- API 管理页的密钥字段默认以密码模式显示，只有主动点击显示图标才在窗口中显示；这不是对剪贴板、屏幕录制或外部服务的保护。

## 问题怎样发生 {#runtime}

### 日志写入与清理边界 {#log-writing}

运行日志以 DEBUG 级别写入 `result/log_<yyyyMMddHHmmss>.txt`，桌面端与 CLI 使用同一位置和格式，文件日志级别独立于控制台输出。日志可能包含本机绝对路径、错误栈、请求阶段和会话信息，公开前必须脱敏。

### 服务器自动清理 {#server-auto-cleanup}

服务器清理服务只清理三类服务器数据目录：

- 结果目录 `SERVER_DATA_DIR/results`
- 用户字体 `USER_RESOURCES_DIR/fonts`
- 用户提示词 `USER_RESOURCES_DIR/prompts`

设置项（`admin_settings['cleanup']`）默认 `auto_cleanup: false`、`interval_hours: 24`、`max_age_days: 7`、`max_size_gb: 10`。每轮先删除修改时间早于保留期的文件，若总大小仍超过上限，再按最旧优先继续删除直到低于上限，最后移除空目录。该服务不覆盖桌面 `result/`、输入目录旁的 `manga_translator_work/` 或浏览器 `localStorage`。

```mermaid
flowchart TD
    A["清理服务启动"] --> B{"auto_cleanup 开启?"}
    B -->|否| Z["不启动清理循环"]
    B -->|是| C["等待 interval_hours 小时"]
    C --> D["删除超过 max_age_days 的旧文件"]
    D --> E{"总大小 > max_size_gb?"}
    E -->|是| F["按最旧优先删除，直到低于上限"]
    E -->|否| G["结束本轮清理"]
    F --> G
    G --> C
```

自动清理只按修改时间和总大小删除，不区分文件是否含敏感内容；被清理目录外的数据仍需手动处理。

### 运行时文件重建 {#runtime-files-rebuild}

启动时系统会为每个入口点补齐用户可编辑的运行时表：`custom_api_params.json`、AI OCR/渲染/上色提示词、`filter_list.json`、`text_replacements.yaml`、`rich_text_rules.yaml` 和 `translation_template.json`。它不覆盖用户文件；只有内容命中历史内置默认值哈希时才删除并由后续流程重建。因此删除这些文件后重启会恢复默认，但用户自定义修改会丢失，不能把“自动重建”当作备份。

### 脱敏边界 {#sanitization-boundary}

- 服务器只在特定输出边界递归掩盖键名含 `api_key`、`api_secret`、`password`、`token`、`key` 的配置值（替换为 `***`）。这只是该输出边界的脱敏，不是对日志、调试目录或数据库的通用清洗。
- `mask_raw` 只是 base64 编码的 PNG，编码不等于匿名化；PSD/JSX 脚本可能含图层文本和本机文件路径；错误信息可能带地址、模型名和请求阶段。任何包含这些内容的文件都要逐项检查后再分享。

## 清理与脱敏流程 {#cleanup-and-sanitize}

### 数据清理 {#cleanup-table}

| 数据位置 | 可能包含的内容 | 清理动作 | 注意 |
| --- | --- | --- | --- |
| `result/log_<时间戳>.txt` | 桌面/CLI 运行日志、路径、错误栈 | 关闭应用后删除 | Windows 下文件被占用时删除失败 |
| `result/<时间戳>-<MD5>-<尺寸>-<语言>-<翻译器>/` | verbose 调试中间图、`ocrs/`、JSON、JSX | 关闭应用后删除整个目录 | 与 `log_*.txt` 配套删除 |
| `<输入目录>/manga_translator_work/` | 逐图 JSON、导出文本、PSD/JSX、中间图片 | 按任务选择性删除 | 含原图、OCR、译文和路径 |
| `config/` 运行时表 | 过滤列表、替换/富文本规则、翻译模板、提示词 | 删除后重启由 `ensure_runtime_files()` 重建默认 | 自定义修改会丢失 |
| `manga_translator/server/data/` | 账号、会话、历史、日志、用户资源 | 部署者备份后按策略清理 | 清理服务只覆盖 results/fonts/prompts |
| 浏览器 `localStorage` | `session_token`、结果列表、`user_env_vars` | 退出登录并清理站点数据 | 与服务器历史是两套数据 |

### 脱敏与日志分享 {#sanitize-and-share}

分享日志或调试产物的目标是让接收方无需你的图片和密钥就能复现问题。优先整理最小复现集，而不是打包整个 `result/` 或工作目录。

```mermaid
flowchart LR
    A["准备分享日志或调试产物"] --> B{"包含敏感内容？"}
    B -->|是| C["逐文件脱敏：密钥/Token、正文、路径、提示词"]
    C --> D["再次复查"]
    B -->|否| E["整理最小复现集"]
    D --> E
    E --> F["附版本、平台与复现步骤后分享"]
```

| 应包含 | 不应包含 |
| --- | --- |
| 应用/CLI 版本号与操作系统 | 真实 API Key、Token、密码、会话令牌 |
| 复现步骤、目标语言、翻译器与关键参数 | 用户原图、大段原文/译文、OCR 文本 |
| 脱敏后的日志片段和对应调试子目录 | 整个 `result/` 或整个工作目录 |
| 脱敏后的配置片段 | 本机绝对路径、私有提示词 |

## 相关设置与限制 {#dependencies}

- `verbose` 与最终输出目录相互独立：调试产物写应用根的 `result/`，最终图片按输出配置写入别处；清理调试目录不影响已保存的输出。
- 服务器清理服务不等于卸载；Web 结果页“清空翻译结果”只清浏览器列表与 blob URL，不删除宿主机结果文件。完整卸载见卸载页。
- 运行时文件删除后会重建默认，但自定义内容丢失；不要把自动重建当作备份，也不要拿示例配置当用户配置。
- 服务端 `_sanitize_config` 只作用于特定输出边界，不能替代逐文件检查；`mask_raw` 编码不等于脱敏。
- 删除 `.env`、`config/`、服务器 `data/`、结果和模型缓存是不可逆操作；日志与错误截图必须先脱敏再对外分享。
