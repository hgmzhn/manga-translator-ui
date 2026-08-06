---
title: 卸载与数据清理
description: 按安装形态移除程序、环境和可选运行数据，并避免误删配置、模型或服务器资料
pageId: install.uninstall-and-data-cleanup
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 卸载与数据清理

## 功能边界 {#scope}

本页说明如何停止应用后移除 Windows 便携版、源码/Unix 安装或 Docker 部署，以及哪些配置、模型、日志、结果和服务器资料需要单独处理。当前仓库的 `Win-*.bat`、`Unix-*.sh` 和 Docker Compose 没有提供一个统一的“卸载”按钮或卸载命令；卸载主要是删除安装目录、虚拟环境或宿主机挂载数据。

本页不替代[Windows 便携版](./windows-portable.md)、[Linux/macOS 安装](./linux-and-macos.md)和[Docker 安装](./docker.md)中的安装步骤，也不把应用内“清空结果”误写成完整卸载。

## UI 操作 {#operations}

### 通用顺序

1. 等待当前任务结束，关闭桌面应用；Web 用户先停止服务，Docker 用户先执行 `docker compose down`。不要在进程仍写日志、结果或数据库时直接删除目录。
2. 备份需要保留的 `config/`、`.env`、`models/`、`result/`、`manga_translator_work/` 或服务器 `data/`。复制前逐项检查其中是否包含密钥、会话、提示词、用户图片和绝对路径。
3. 按下表删除对应安装目录或虚拟环境。只想释放空间时，先选择性清理缓存/结果，不要把它当成卸载完成。
4. 重新安装前确认没有旧环境路径仍被启动脚本优先使用，并在保留配置前检查其版本兼容性。

### 安装形态与删除动作

| 安装形态 | UI/命令动作 | 默认保留位置 | 删除范围 |
| --- | --- | --- | --- |
| Windows 便携版 | 关闭应用后删除包含 `Win-Start.bat` 的完整发行目录 | 发行目录中的 `config/`、`models/`、`result/`、资源和 `packaging/python/` | 整个发行目录；脚本没有注册表或服务卸载步骤 |
| Windows 旧 Conda 布局 | 先停用/移除仅供本项目使用的环境，再删除程序目录；共享 Conda 不要整体删除 | 外置 Miniconda、`manga-env` 或程序目录下 `conda_env/` | 仅删除确认属于本项目的环境和程序目录 |
| Linux/macOS 源码 | 停止进程后删除 checkout；若不保留环境，连同 `.venv/` 一起删除 | checkout 内 `.venv/`、`config/`、`models/`、`result/`、`logs/` | checkout 目录；输入目录旁的 `manga_translator_work/` 需另行处理 |
| Docker Compose | 在 Compose 所在目录执行 `docker compose down`，再按需删除宿主机 `data/` | `./data/config`、`server`、`models`、`result`、`logs`、`fonts`、`dict` | 容器/镜像与宿主机 bind mount 分开删除；`down` 不会自动删除 `./data/` |

### 应用内清理和 UI 文案

Web 管理界面中的清理功能只处理服务器清理服务定义的目录；它不是删除程序目录的卸载器。结果页的“清空翻译结果”只清空浏览器结果列表和 blob URL，不等同于删除宿主机结果文件。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `web_cleanup_management` | Cleanup | 清理管理 |
| `web_cleanup_rules` | Cleanup Rules | 清理规则 |
| `web_auto_cleanup` | Auto Cleanup | 自动清理 |
| `web_manual_cleanup` | Manual Cleanup | 手动清理 |
| `web_cleanup_now` | Cleanup Now | 立即清理 |
| `web_cleanup_report` | Cleanup Report | 清理报告 |
| `web_files_deleted` | Files Deleted | 已删除文件 |
| `web_space_freed` | Space Freed | 释放空间 |
| `web_clear_logs` | Clear Logs | 清空日志 |
| `confirm_clear_results` | Are you sure you want to clear all translation results? | 确定要清空所有翻译结果吗？ |
| `results_cleared` | Translation results cleared | 翻译结果已清空 |

启动/维护脚本的菜单文案来自 `packaging/launch.py` 的 `L(中文, English)`，不是 Qt locale key；当前菜单只有安装、更新、分支/tag、镜像、版本检查、语言和退出，没有卸载项。`Win-Start.bat` 的错误恢复提示是硬编码英文，不能声称有完整双语回退。

## 运行机理 {#runtime}

```mermaid
flowchart TD
    Stop["停止桌面/Web/Docker 进程"] --> Shape{"安装形态"}
    Shape -->|Windows 便携| Folder["备份后删除发行目录"]
    Shape -->|源码或 Unix| Venv["删除 checkout 与可选 .venv"]
    Shape -->|Docker| Down["docker compose down"]
    Down --> Mount{"是否删除宿主机 ./data/？"}
    Mount -->|否| Keep["保留 bind mount 数据"]
    Mount -->|是| Data["备份后选择性删除 ./data/"]
    Folder --> Optional["检查输入目录旁的 manga_translator_work/、用户缓存"]
    Venv --> Optional
    Keep --> Optional
    Data --> Optional
    Optional --> End["清理结果、模型缓存或凭据前再次确认范围"]
```

运行时路径由 `manga_translator.runtime_paths` 决定：源码运行时配置目录是项目根的 `config/`，冻结运行时是可执行文件旁的 `config/`。模型默认位于应用目录下的 `models/`；桌面日志位于应用目录下的 `result/`。若启用 `save_to_source_dir`，结果会写到输入图片所在目录的 `manga_translator_work/result/`，所以删除安装目录不会删除这些输入目录旁的产物。

Docker 把宿主机 `./data/{fonts,dict,result,models,logs,server,config}` 分别挂载到容器；删除容器不会删除这些宿主机文件。服务器数据还包括 `manga_translator/server/data` 下的管理员配置、用户资源和历史相关资料，必须先备份并脱敏再决定是否删除。

服务器自动清理默认关闭；默认配置为每 24 小时检查一次、删除超过 7 天的文件，并在相关目录总量超过 10 GiB 时按最旧文件继续删除。它只遍历服务器 `data/results`、用户字体和用户提示词目录，不清理应用安装目录、模型目录、桌面日志或输入旁的工作目录。

## 依赖与冲突 {#dependencies}

- **进程占用**：未停止的 Qt、Python、uvicorn 或 Docker 容器可能继续写文件，Windows 也可能拒绝删除 DLL；先关闭进程或停止容器。
- **便携 Python 与 Conda/venv**：`Win-Start.bat` 和 `Win-Install-or-Update.bat` 优先使用 `packaging/python/python.exe`，缺失时才查找 `manga-env` 或 `conda_env`。不要为了卸载便携版删除仍被其他项目使用的 Miniconda；不要让旧 PATH 指向已删除环境。
- **硬件依赖**：卸载 GPU 版只会移除应用目录内的环境和文件，不会替用户卸载系统 NVIDIA/AMD 驱动；驱动是其他软件的共享依赖。
- **Docker 持久化**：`docker compose down`、删除容器、删除镜像和删除 bind mount 是不同动作。删除 `./data/server` 会丢失 Web 账户/会话/历史及服务器资源；删除 `./data/models` 会导致以后重新下载模型。
- **缓存与凭据**：Hugging Face/Torch 等用户级缓存可能位于用户 profile 外部目录；`.env` 和配置文件可能含 API 凭据。删除前先决定是否迁移，分享日志前检查密钥、令牌、用户名、绝对路径和用户内容。
- **版本切换**：卸载不是更新。维护器更新时可能清理 uv/pip 下载缓存并移除不适合当前平台的启动文件，但不会替用户删除全部数据目录。

## 关联文件与格式 {#files}

| 文件/目录 | 实际作用 | 清理/兼容注意 |
| --- | --- | --- |
| `config/config.json`、`config/` | 桌面/CLI 配置和运行时表 | 可迁移但要检查版本、绝对路径和敏感字段；不要上传真实内容 |
| `.env` | API/服务器环境变量（若启用） | 视为凭据文件；备份或删除前脱敏，不能在截图中展示 |
| `packaging/python/`、`.venv/`、`conda_env/`、`Miniconda3/` | 运行时和依赖 | 只删除确认属于本项目的环境；共享 Conda/驱动不要误删 |
| `models/`、用户级模型缓存 | 下载的模型和临时下载相关文件 | 删除后下次运行需重新下载；不等同于卸载驱动 |
| `result/`、`logs/`、`manga_translator_work/` | 日志、结果、输入目录旁的工作文件 | 可能含原图、OCR、译文和调试内容；对外分享前清理敏感信息 |
| `manga_translator/server/data/` | Web 管理配置、用户资源和服务器数据 | Docker 中通常由 `./data/server` 挂载；删除前必须备份并确认影响 |
| `./data/{fonts,dict,result,models,logs,config}` | Compose 宿主机持久化挂载 | `down` 不删除；按目录选择性清理 |
| `packaging/uv_cache/`、pip/uv 下载缓存 | 安装/更新下载缓存 | 维护流程可清理下载缓存，但不应当作用户结果或模型清理 |

## 截图与流程图边界 {#visuals}

本页 Mermaid 只表达停止、按安装形态分流、容器与 bind mount 分离以及可选数据清理边界。没有运行发行包、Docker 管理面板或实际卸载流程，因此不伪造截图。未来截图只能使用脱敏测试目录和虚构用户路径；必须裁去用户名、私有绝对路径、API Key、Token、管理员密码、用户图片、提示词和历史内容，并提供中英文 alt 与图注。

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| Windows 启动 | `Win-Install-or-Update.bat`、`Win-Start.bat` | 脚本目录、便携 Python 优先、Conda 回退、无卸载分支 |
| Unix 启动 | `Unix-Install-or-Update.sh`、`Unix-Start.sh` | checkout 相对路径、`.venv`、旧环境回退、临时 clone 清理 |
| 应用路径 | `manga_translator/runtime_paths.py`、`manga_translator/server_paths.py` | `config/`、服务器 `data/` 和用户资源的源码/冻结路径 |
| 结果与模型 | `desktop_qt_ui/main.py`、`manga_translator/config.py`、`manga_translator/utils/inference.py` | 日志、`manga_translator_work/`、`models/` 和临时下载目录 |
| Web/Docker | `manga_translator/server/core/cleanup_service.py`、`packaging/Dockerfile`、`packaging/docker-compose.yml` | 自动清理范围、默认值和宿主机挂载持久化 |
| UI/i18n | `manga_translator/server/static/script.js`、`desktop_qt_ui/locales/en_US.json`、`desktop_qt_ui/locales/zh_CN.json` | 清空结果行为与清理相关实际文案 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| 页面合同 | 完成 | 双语边界、操作、运行机理、依赖冲突、文件格式、Mermaid/截图边界、安全审查均已覆盖 |
| 当前源码静态核对 | 完成 | Windows/Unix 启动脚本、运行路径、Docker 挂载、服务器清理服务和结果行为已核对 |
| UI 调用 key → en_US → zh_CN | 完成 | 清理/结果相关 key 已逐项列出；启动器硬编码文案按非 Qt key 处理 |
| 实际卸载/数据恢复运行验证 | 未运行 | 未启动发行包、Docker 或真实服务；未把静态结论写成运行成功 |
| 静态 Wiki 检查与构建 | 待执行 | 完成本页后运行路由镜像、源码依据、覆盖检查和 VitePress 构建 |

## 安全审查 {#privacy}

本文不包含真实 API Key、Token、管理员密码、用户名、私有绝对路径、用户图片、OCR/译文、模型输出或私有提示词。清理前应先停止服务并备份必要数据；删除 `server/data`、`.env`、配置、结果或模型缓存是不可逆操作，日志和错误截图也必须先脱敏。