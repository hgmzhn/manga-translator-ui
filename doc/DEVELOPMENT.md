# 开发者指南

本文档面向需要修改源码、调试流程、扩展功能或参与打包发布的开发者。下面只列 Git 已跟踪、会随仓库一起维护的目录和文件，不展开本地缓存、运行产物或未提交目录。

## 1. 开发前提

### Python 与环境

- 当前仓库以 **Python 3.12** 为基线。
- `packaging/launch.py` 和 GitHub Actions 也都按 Python 3.12 运行。
- 可以使用 `venv`、Conda 或项目安装脚本创建环境，不强制要求环境名必须叫 `manga-env`。

### 依赖声明方式

依赖现在统一声明在仓库根目录的 `pyproject.toml`：

- 公共依赖写在 `[project] dependencies`。
- 四种后端是互斥的 dependency groups：`cpu` / `gpu` / `amd` / `metal`（`[tool.uv] conflicts` 保证互斥）。
- 默认组是 `gpu` + `packaging` + `test`，所以裸 `uv sync` / `uv run` 使用 NVIDIA CUDA 13.0；CUDA 12.6 的可复现环境位于 `cuda12.6` 分支。安装器使用 `--no-default-groups`，不会检查或安装 `test` 组。
- PyTorch 源通过 `[tool.uv.sources]` + `[[tool.uv.index]]` 绑定：主分支 `gpu` 使用 `whl/cu130`，`cuda12.6` 分支使用 `whl/cu126`，`cpu` 用 `download.pytorch.org/whl/cpu`，Linux `amd` 用 `whl/rocm7.2`，`metal` 走默认 PyPI。
- `uv.lock` 是锁定文件，已提交在仓库里，请勿手改。

旧的 `requirements_cpu.txt` / `requirements_gpu.txt` / `requirements_amd.txt` / `requirements_metal.txt` 已删除。

### 依赖安装

推荐使用 uv，按你的运行目标只装一套 dependency group：

```bash
# NVIDIA GPU（CUDA 13.0，默认；CUDA 12.6 使用 cuda12.6 分支）
uv sync

# 其他后端关闭默认组后显式选择
uv sync --no-default-groups --group cpu
uv sync --no-default-groups --group metal

# AMD（Linux）：使用 PyTorch 官方 ROCm 7.2 索引
uv sync --no-default-groups --group amd
# Windows AMD 由 Windows 安装脚本按 Radeon SDK -> PyTorch 顺序处理
```

源码仓库中的 `uv sync` 会自动创建 `.venv` 并按 `uv.lock` 复现依赖；Windows 便携安装包使用自带的 `packaging\python`，不会创建 `.venv`。如果想把源码依赖装进已有环境，也可以：

```bash
uv sync --active
```

默认 GPU 环境已经包含 `packaging` 与 `test` 组。其他后端做 PyInstaller 打包时加上：

```bash
uv sync --no-default-groups --group cpu --group packaging
```

## 2. 仓库结构

实际开发时优先关注下面这些已纳入版本控制的区域。

### 核心源码区

```text
manga-translator-ui-package/
├─ desktop_qt_ui/              # Qt 桌面应用
│  ├─ main.py                  # 桌面端入口
│  ├─ ui/                      # 桌面 UI 定义集中目录
│  │  ├─ main_window.py        # 主窗口与主生命周期
│  │  ├─ styles.py             # 主页面、编辑器、二级页面的统一样式入口
│  │  ├─ theme.py              # 主题运行时、调色板与应用逻辑
│  │  ├─ theme_tokens.py       # 主题 token 与配色定义
│  │  ├─ main_page/            # 主页面视图、布局与运行态
│  │  ├─ editor/               # 编辑器页面、画布与快捷键
│  │  ├─ secondary_pages/      # 二级页面与编辑弹窗
│  │  ├─ widgets/              # 通用 UI 控件
│  │  └─ icons/                # UI 图标资源
│  ├─ services/                # 服务容器、配置、翻译、OCR、日志等
│  ├─ editor/                  # 编辑器控制器、模型、渲染与文档逻辑
│  └─ locales/                 # 多语言文本
├─ manga_translator/           # 核心翻译引擎与服务端
│  ├─ __main__.py              # CLI / web / ws / shared 统一入口
│  ├─ detection/               # 文本检测
│  ├─ ocr/                     # OCR 模型与适配
│  ├─ translators/             # 翻译器实现
│  ├─ inpainting/              # 修复与去字
│  ├─ rendering/               # 嵌字与排版
│  ├─ upscaling/               # 超分
│  ├─ colorization/            # 上色
│  ├─ utils/                   # 通用工具与中间格式
│  └─ server/                  # FastAPI 服务端、静态页面、管理后台
├─ packaging/                  # 启动脚本、更新脚本、PyInstaller、Docker
├─ config/                     # 默认配置、模板、翻译器注册表
├─ .github/                    # CI/CD、Issue 模板
├─ doc/                        # 用户文档与 changelog
├─ fonts/                      # 默认字体资源
├─ dict/                       # Prompt、词典、模板资源
└─ README.md                   # 项目入口文档
```

## 3. 代码分层与入口

### 3.1 Qt 桌面端

桌面端主入口是：

```bash
python -m desktop_qt_ui.main
```

主路径大致是：

1. `desktop_qt_ui/main.py`
2. 初始化日志、资源路径、全局异常处理
3. 调用 `desktop_qt_ui.services.init_services(root_dir)`
4. 创建 `MainWindow`
5. 再由 `ui/main_window.py`、`ui/main_page/`、`ui/editor/` 组装主界面和编辑器

桌面端改动时通常按下面的落点找文件：

- 改设置读写：`desktop_qt_ui/services/config_service.py`、`desktop_qt_ui/core/config_models.py`
- 改主界面 UI：`desktop_qt_ui/ui/main_page/`
- 改编辑器页面/画布 UI：`desktop_qt_ui/ui/editor/`
- 改二级页面或编辑弹窗：`desktop_qt_ui/ui/secondary_pages/`
- 改通用控件：`desktop_qt_ui/ui/widgets/`
- 改页面、编辑器、二级页面样式：`desktop_qt_ui/ui/styles.py`
- 改主题 token：`desktop_qt_ui/ui/theme_tokens.py`
- 改主题运行时：`desktop_qt_ui/ui/theme.py`
- 改编辑器业务行为：`desktop_qt_ui/editor/`
- 改服务装配：`desktop_qt_ui/services/__init__.py`

### 3.2 核心引擎与命令行

统一入口是：

```bash
python -m manga_translator <mode>
```

已实现模式：

- `web`：启动 FastAPI 服务与 Web UI
- `local`：命令行本地翻译
- `ws`：WebSocket 模式
- `shared`：共享 API 实例模式

常用示例：

```bash
# Web 服务
python -m manga_translator web --host 127.0.0.1 --port 8000

# 本地翻译
python -m manga_translator local -i path/to/image.png -o path/to/output
```

核心处理链主要分散在 `manga_translator/` 下：

- `detection/`：文本区域检测
- `ocr/`：文字识别
- `translators/`：文本翻译
- `inpainting/`：清除原文与补全背景
- `rendering/`：译文排版回写
- `utils/textblock.py` 等：中间结构与序列化

### 3.3 服务端

服务端入口由 `manga_translator/__main__.py` 的 `web` 模式分发到 `manga_translator/server/main.py`。

服务端目录建议这样理解：

- `server/routes/`：HTTP 路由层
- `server/core/`：账户、权限、配额、清理任务、配置管理等服务逻辑
- `server/repositories/`：JSON/文件存储封装
- `server/models/`：Pydantic 或数据模型
- `server/static/`：前端静态页面与管理后台资源
- `server/data/`：服务端运行时数据文件

## 4. 配置与资源打包约定

这个项目同时支持开发环境和 PyInstaller 打包环境。改资源路径相关逻辑前，重点确认哪些已跟踪资源需要被一起打进发布包。

### 开发环境常用的已跟踪资源

- 默认配置模板：`config/config-example.json`
- 翻译器注册表：`config/config/translators.json`
- 资源目录：`fonts/`、`dict/`、`doc/`、`desktop_qt_ui/locales/`

### 打包时需要关注的已跟踪资源

- `config/`
- `fonts/`
- `dict/`
- `doc/`
- `desktop_qt_ui/locales/`

如果你新增了资源目录、模板文件或配置文件，需要同时检查：

1. 开发态是否按项目根目录能加载到。
2. PyInstaller spec 和 GitHub workflow 是否把它一起打进发布包。

## 5. 本地开发流程

### 5.1 推荐启动顺序

```bash
# 1. 安装默认 GPU 依赖（会自动创建 .venv 并按 uv.lock 复现）
uv sync

# 2. 激活环境（Windows PowerShell）
.venv\Scripts\Activate.ps1

# 3. 启动桌面端
python -m desktop_qt_ui.main
```

如果你主要开发服务端：

```bash
python -m manga_translator web --host 127.0.0.1 --port 8000 -v
```

### 5.2 常见改动落点

#### 新增一个设置项

至少按下面这条链路检查，很多设置不是只改 5 个地方就够：

1. `desktop_qt_ui/core/config_models.py`
   定义字段、默认值、类型、校验和兼容迁移。
2. `manga_translator/config.py`
   如果这个设置会进入核心翻译流水线、CLI、Web 服务或底层模块配置，还要同步这里的核心配置模型和相关枚举；否则桌面端存下来了，后端实际运行时可能根本读不到。
3. `config/config-example.json`
   同步默认配置模板，保证新字段能写入导出配置和首次启动配置。
4. `desktop_qt_ui/ui/main_page/settings_tab_layout.json`
   如果这个设置要出现在设置页，需要把 `section.key` 放进对应 tab 的 `items`。
5. `desktop_qt_ui/app_logic.py`
   如果设置是下拉选项或需要友好显示，补 `get_options_for_key()`、`get_display_mapping()`，必要时补 `labels` 映射。
6. `desktop_qt_ui/locales/*.json`
   至少补 `label_xxx` 和 `desc_section_key`；如果是枚举值，还要补对应选项文案 key。
7. `desktop_qt_ui/ui/main_page/dynamic_settings.py`
   如果默认的通用控件不够用，或者这个字段要隐藏、分组、加按钮、加占位符、走特殊编辑器，就在这里补特殊逻辑。
8. `desktop_qt_ui/app_logic.py`
   如果设置变化后要立刻触发副作用，比如切换翻译器、刷新渲染、联动其他字段，就补 `update_single_config()` 里的即时处理。
9. 实际消费该设置的模块
   例如 `desktop_qt_ui/services/`、`manga_translator/ocr/`、`manga_translator/rendering/`、`manga_translator/translators/` 等，否则设置只会“存起来但不起作用”。

按设置类型，再额外检查这些位置：

- 如果是“新枚举值”而不是“新字段”：
  同时检查 `manga_translator/config.py` 里的 Enum / 配置类型、`desktop_qt_ui/app_logic.py` 里的选项列表和显示映射，以及相关 locale 文案。
- 如果设置也要影响命令行或 Web 运行：
  检查 `manga_translator/config.py`、`manga_translator/args.py`、相关 mode/service 的参数合并逻辑，以及后端实际消费点。
- 如果设置引入新的 API 依赖或环境变量：
  检查 `config/config/translators.json` 和 `desktop_qt_ui/services/config_service.py` 里的校验逻辑。
- 如果设置属于导入导出时应排除的临时状态：
  检查 `desktop_qt_ui/app_logic.py` 的 `export_config()` / `import_config()`。
- 如果设置会影响编辑器侧展示或编辑行为：
  继续检查 `desktop_qt_ui/ui/editor/`、`desktop_qt_ui/ui/widgets/property_panel.py` 和相关 `desktop_qt_ui/editor/` 逻辑。

#### 新增或接入一个翻译器 / OCR / 渲染器

通常需要同步：

1. 在 `manga_translator/<对应模块>/` 新增实现
2. 更新配置/枚举入口
3. 如果涉及 API 环境变量，更新 `config/config/translators.json`
4. 必要时补 UI 选项、文档说明和测试

#### 修改编辑器行为

优先从 `desktop_qt_ui/editor/` 下找：

- `editor_controller.py`
- `editor_logic.py`
- `graphics_view.py`
- `graphics_items.py`
- `commands.py`
- `selection_manager.py`

## 6. 校验与调试

### 代码风格

仓库里目前能看到的唯一已跟踪静态检查配置文件是 `desktop_qt_ui/ruff.toml`。

如果本地已经安装 `ruff`，可以用下面这条命令做一次基础检查：

```bash
ruff check desktop_qt_ui manga_translator --config desktop_qt_ui/ruff.toml
```

这个结论的边界是：

- 仓库根目录的 `pyproject.toml` 只负责依赖声明，不包含 lint 配置；此外没有其他已跟踪的 `setup.cfg`、`tox.ini`、`.flake8`、第二份 `ruff.toml` 等配置文件。
- 当前 GitHub Actions 里也没有显式执行 lint 步骤。
- 所以上面的命令更适合作为本地自检入口，不表示仓库 CI 当前已经把它当成必过步骤。

这份 `ruff.toml` 当前规则以 `E`、`F`、`I` 为主，忽略 `E501`、`E701`、`E402`。

### 调试文档

- 详细排障流程请看 [DEBUGGING.md](DEBUGGING.md)

## 7. 打包与发布

### 本地 PyInstaller 构建

构建脚本入口：

```bash
python packaging/build_packages.py <version> --build cpu
python packaging/build_packages.py <version> --build gpu
python packaging/build_packages.py <version> --build both
```

相关文件：

- `packaging/build_packages.py`
- `packaging/manga-translator-cpu.spec`
- `packaging/manga-translator-gpu.spec`
- `packaging/create-manga-pdfs.spec`
- `packaging/manga-chapter-splitter.spec`

### 启动与安装脚本

面向最终用户的脚本主要在仓库根目录：

- `Win-Start.bat`（启动）
- `Win-Install-or-Update.bat`（安装或更新维护菜单）
- `Unix-Install-or-Update.sh` / `Unix-Start.sh`（Linux/macOS）

这些脚本的实际逻辑集中在 `packaging/launch.py`、`packaging/git_update.py` 等文件里。修改安装/更新行为时，不要只改 `.bat` 或 `.sh` 外壳。

### CI/CD

- `.github/workflows/build-and-release.yml`
  - Windows 上基于便携基础包构建 CPU、NVIDIA CUDA 13.0 GPU、NVIDIA CUDA 12.6 GPU、AMD 四种运行时，安装锁定依赖和模型后生成 7z 分卷
  - Ubuntu 上汇总四个构建产物，并发布 GitHub Release
- `.github/workflows/docker-build-push.yml`
  - 基于 `packaging/Dockerfile` 构建 CPU/GPU Docker 镜像

如果你新增了打包必须资源，请同步更新 workflow 中复制到可执行文件同级的步骤。

## 8. 开发建议

- 涉及配置、模板、字体、词典时，始终同时验证开发态和打包态路径。
- 修改桌面端设置项时，至少检查默认配置、UI 文案、多语言文件和序列化兼容。
- 修改发布流程时，别只看本地 `packaging/`，还要一起检查 GitHub Actions。

## 9. 相关文档

- [安装指南](INSTALLATION.md)
- [使用教程](USAGE.md)
- [命令行模式](CLI_USAGE.md)
- [调试指南](DEBUGGING.md)
- [设置说明](SETTINGS.md)
