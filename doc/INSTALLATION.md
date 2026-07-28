# 安装指南

本文档提供详细的安装步骤和系统要求说明。

---

## 📋 目录

- [系统要求](#系统要求)
- [安装方式一：便携安装包（推荐，支持自动更新）](#安装方式一便携安装包推荐支持自动更新)
- [安装方式二：下载打包版本](#安装方式二下载打包版本)
- [安装方式三：从源码运行](#安装方式三从源码运行)
- [安装方式四：Docker部署](#安装方式四docker部署)
- [安装方式五：macOS 原生运行（Apple Silicon）](#安装方式五macos原生运行apple-silicon)
- [故障排除](#故障排除)

---

## 系统要求

### 最低配置

- **操作系统**：Windows 10/11 (64位)、Linux 或 macOS 12+ (Apple Silicon)
- **内存**：8 GB RAM
- **存储空间**：5 GB 可用空间（用于程序和模型文件）
- **Python 版本**（开发版）：Python 3.12

### 推荐配置

- **内存**：16 GB RAM 或更多
- **GPU**：
  - **NVIDIA 显卡**：支持 CUDA 12.x（需驱动版本 >= 525.60.13）
    - 建议显存：6 GB 或更多
    - 支持的 NVIDIA 显卡：GTX 1060 及以上
  - **AMD 显卡**：支持 ROCm（实验性）
    - 支持的显卡：**仅 RX 7000/9000 系列（RDNA 3/4）**
    - ⚠️ RX 5000/6000 系列请使用 CPU 版本
    - ⚠️ AMD GPU 仅支持便携安装包方式，不支持打包版本
    - ⚠️ Windows 上 ROCm 支持有限，Linux 下体验更好
- **存储空间**：10 GB SSD

---

## 安装方式一：便携安装包（⭐ 推荐，支持自动更新）

从 GitHub Releases 下载便携安装包，解压即用。安装包内置打包版 Python 3.12（`packaging\python\python.exe`）和 uv 包管理器（`packaging\uv.exe`），完全绿色、不写注册表、**无需预装 Python**。

> ⚠️ **网络提示**：安装过程需要下载代码和依赖，国内网络可在菜单中选择 Gitee 镜像和国内 PyPI 镜像。

### 前提条件

- **无需预装 Python**：安装包自带打包版 Python 3.12 和 uv
- 从 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases) 下载便携安装包并解压到任意目录

### 两个入口脚本

解压后目录中有两个入口脚本，双击即可运行：

| 脚本 | 作用 |
|------|------|
| `Win-Start.bat` | 启动程序 |
| `Win-Install-or-Update.bat` | 打开安装/更新维护菜单 |

### 首次安装

双击 `Win-Install-or-Update.bat`，在维护菜单中选择 **[1] 安装**，流程如下：

1. **选择下载线路**：GitHub 官方 / Gitee 镜像（国内推荐）
2. **强制同步最新代码**：同步失败会提示切换到另一条线路重试
3. **检测显卡**：自动识别 NVIDIA / AMD / 集显；多显卡时列出让用户选择
4. **选择 PyTorch 版本**：
   - **NVIDIA**：CUDA 12.x（需驱动版本 >= 525.60.13）
   - **AMD**：ROCm（实验性，**仅 RX 7000/9000 系列**）
   - **其他/集显**：CPU 版本
5. **uv 高速批量安装依赖**：
   - PyTorch 走官方源或国内镜像
   - 其余依赖走 PyPI 多镜像回退：清华 → 阿里 → 豆瓣 → 官方
   - 安装失败可重试（已装的包会保留，不会重复下载）
6. **完成后自动清理下载缓存**

### 维护菜单说明

维护菜单会自动检测系统语言显示中文或英文，配置持久化在 `packaging\maintenance_config.json`。菜单选项：

- **[1] 安装**：完整安装流程（见上）
- **[2] 更新**：检查代码（按当前分支比对远程 VERSION 和提交数）+ 依赖（只安装缺失的包）
- **[3] 切换分支**：main 稳定版 / beta 测试版
- **[4] 按 tag 切换历史版本**
- **[5] 切换镜像源**
- **[6] 重新检查版本**
- **[7] 切换语言**（中/英）
- **[8] 退出**

### 依赖管理说明

依赖声明在 `pyproject.toml`（公共依赖 + `cpu` / `gpu` / `amd` / `metal` 四个互斥 dependency groups），并由 `uv.lock` 锁定版本。便携安装脚本直接把依赖装入自带的 `packaging\python`，**不会创建 `.venv`**；`.venv` 仅用于源码开发。

### 启动程序

安装完成后，以后每次使用只需双击 `Win-Start.bat`。

### 更新程序

双击 `Win-Install-or-Update.bat`，选择 **[2] 更新** 即可。

### 卸载

新版为完全绿色安装，**直接删除整个文件夹即可卸载**。旧版（conda 方式）的卸载请参考 [卸载指南](UNINSTALL.md)。

> 💡 **旧版用户兼容说明**：如果你之前用旧版脚本安装了 Miniconda3 + `manga-env` / `conda_env` 环境，新脚本在找不到打包版 Python 时会自动回退使用旧的 conda 环境，无需重装。

---

## 安装方式二：下载打包版本

适合不想安装 Python 的用户，但文件较大（约 3-5 GB）。

### 1. 访问发布页面

前往 [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases) 页面。

### 2. 选择版本

下载最新版本的安装包：

**CPU 版本**：
- 文件名：`manga-translator-cpu-vX.X.X.zip` 或分卷文件
- 适用范围：所有电脑
- 优点：无需 GPU，兼容性好
- 缺点：翻译速度较慢

**GPU 版本**：
- 文件名：`manga-translator-gpu-vX.X.X.zip` 或分卷文件
- 适用范围：拥有 NVIDIA 显卡的电脑
- 要求：CUDA 12.x 支持
- 优点：翻译速度快
- 缺点：需要兼容的 NVIDIA 显卡

### 3. 分卷下载说明

如果文件被分成多个压缩包（如 `part1.rar`, `part2.rar`, `part3.rar`...），请按照以下步骤操作：

1. **下载所有分卷**：
   - 必须下载所有分卷文件到同一文件夹
   - 例如：`part1.rar`, `part2.rar`, `part3.rar`

2. **解压第一个分卷**：
   - 只需右键点击 `part1.rar`
   - 选择"解压到..."或"Extract to..."
   - 其他分卷会自动参与解压

3. **注意事项**：
   - 所有分卷必须在同一目录
   - 不要重命名分卷文件
   - 缺少任何一个分卷都会导致解压失败

### 4. 安装步骤

1. **解压文件**：
   ```
   将下载的压缩包解压到任意目录
   例如：D:\manga-translator\
   ```

2. **检查文件结构**：
   ```
   manga-translator/
   ├── app.exe          # 主程序
   ├── _internal/       # PyInstaller/Python 依赖文件
   ├── fonts/           # 字体文件
   ├── models/          # AI 模型文件
   ├── config/          # 可编辑配置
   ├── dict/            # 词典和提示词
   ├── doc/             # 文档与图标资源
   └── desktop_qt_ui/   # UI 静态资源和语言文件
   ```

3. **运行程序**：
   - 双击 `app.exe` 启动程序
   - 首次运行会自动加载模型文件

---

## 安装方式三：从源码运行

适合开发者或想自定义的用户。

### 1. 克隆仓库

```bash
git clone https://github.com/hgmzhn/manga-translator-ui.git
cd manga-translator-ui
```

### 2. 安装依赖

依赖声明在 `pyproject.toml`（公共依赖 + `cpu` / `gpu` / `amd` / `metal` 四个互斥 dependency groups），使用 [uv](https://docs.astral.sh/uv/) 安装。下面四种后端只选一种：

```bash
# NVIDIA GPU（CUDA 12.8，默认；同时安装 packaging 组）
uv sync

# CPU 版本
uv sync --no-default-groups --group cpu

# AMD GPU 版本（实验性；ROCm PyTorch 由 launch.py 单独安装）
uv sync --no-default-groups --group amd
uv run --no-sync python packaging/launch.py --requirements amd --install-deps-only

# Apple Silicon / Metal
uv sync --no-default-groups --group metal
```

> 💡 **pip 用户**：可用 `uv export` 生成 requirements 文件后再用 pip 安装。

### 3. 运行程序

```bash
# 运行 PyQt6 界面
uv run --no-sync python -m desktop_qt_ui.main

# 或运行旧版 CustomTkinter 界面
uv run --no-sync python -m desktop-ui.main
```

---

## 安装方式四：Docker 镜像部署（实验性）

适合使用宝塔面板、Portainer 等 Docker 管理工具的用户。

> 💡 **说明**：下面的 `docker run` 命令适合临时测试。正式部署 Web UI 时，建议至少按“Web UI 持久化目录”一节挂载数据目录。

### 快速启动

**Windows CMD / PowerShell**：
```cmd
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

**Linux / macOS**：
```bash
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

启动后访问：
- 🌐 用户界面：http://localhost:8000
- 🔧 管理界面：http://localhost:8000/admin

### 镜像仓库

本项目的 Docker 镜像同时发布在两个镜像仓库，选择下载速度更快的即可：

**Docker Hub（推荐）**：
- CPU 版本：`hgmzhn/manga-translator:latest-cpu`
- GPU 版本：`hgmzhn/manga-translator:latest-gpu`

**GitHub Container Registry（备用，国内可能更快）**：
- CPU 版本：`ghcr.io/hgmzhn/manga-translator:latest-cpu`
- GPU 版本：`ghcr.io/hgmzhn/manga-translator:latest-gpu`

> 💡 **提示**：两个仓库的镜像完全相同，选择下载速度更快的即可。

### Web UI 持久化目录（推荐）

如果你准备长期使用 Web UI，建议持久化下面这些路径：

| 容器内路径 | 建议程度 | 作用 |
|-----------|---------|------|
| `/app/manga_translator/server/data` | 必须 | 统一保存 `admin_config.json`、`user_resources/`、账号、会话、用户组、权限、配额、API Key 预设、用户配置、审计日志、翻译历史索引与 Web 历史结果 |
| `/app/config` | 强烈建议 | 保存 `config.json`、`custom_api_params.json`、`filter_list.json` 等会自动生成或被编辑的配置文件 |
| `/app/dict` | 强烈建议 | 保存术语表、网页端/本地 AI prompt 文件（如 `ai_ocr_prompt.yaml`、`ai_renderer_prompt.yaml`、`ai_colorizer_prompt.yaml`） |
| `/app/fonts` | 强烈建议 | 保存服务器级字体文件 |
| `/app/models` | 强烈建议 | 保存下载后的模型文件，避免容器重建后重新下载 |
| `/app/.env` | 按需 | 如果你会在 Web 管理界面保存服务器 API Keys，必须额外挂这个文件 |
| `/app/logs` | 可选 | 保存根目录运行日志 |
| `/app/result` | 可选 | 保存 CLI/调试产物；Web 历史结果主要还是在 `server/data/results` 里 |

> 💡 **文件挂载提醒**：
> - 现在 `admin_config.json` 和 `user_resources/` 都包含在 `/app/manga_translator/server/data` 目录里
> - 只有 `/app/.env` 还是**文件**绑定；请先在宿主机创建空文件，再启动容器，否则 Docker 可能会把它当目录创建出来

### 推荐的 docker-compose 持久化示例

下面这份示例比最小启动命令更适合长期运行 Web UI：

```yaml
services:
  manga-translator:
    image: hgmzhn/manga-translator:latest-cpu
    container_name: manga-translator
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      MT_WEB_HOST: 0.0.0.0
      MT_WEB_PORT: 8000
      MANGA_TRANSLATOR_ADMIN_PASSWORD: change_me_123456
    volumes:
      - ./data/models:/app/models
      - ./data/fonts:/app/fonts
      - ./data/dict:/app/dict
      - ./data/config:/app/config
      - ./data/server:/app/manga_translator/server/data
      - ./data/logs:/app/logs
      - ./data/result:/app/result
      # 如果要让 Web 管理界面里保存的服务器 API Keys 在重建容器后仍然保留，
      # 先创建空文件 ./data/app.env，再取消下面这行注释：
      # - ./data/app.env:/app/.env
```

### 端口映射

- **容器端口**：`8000`
- **主机端口**：`8000`（可自定义）

### 环境变量配置

> 💡 **提示**：所有环境变量都是可选的，程序会使用合理的默认值。

#### 基础配置（可选）

| 变量名 | 示例值 | 默认值 | 说明 |
|--------|--------|--------|------|
| `MT_WEB_HOST` | `0.0.0.0` | `0.0.0.0` | 监听地址（0.0.0.0 允许外部访问，127.0.0.1 仅本地访问） |
| `MT_WEB_PORT` | `8000` | `8000` | 服务端口 |
| `MT_USE_GPU` | `true` | `false` | 是否使用 GPU（仅 GPU 版本镜像需要设置） |
| `MT_MODELS_TTL` | `300` | `0` | 模型在内存中的存活时间（秒），0 表示永久保留 |
| `MT_RETRY_ATTEMPTS` | `-1` | `None` | 翻译失败重试次数，-1 表示无限重试 |
| `MT_VERBOSE` | `true` | `false` | 是否显示详细日志 |
| `MANGA_TRANSLATOR_ADMIN_PASSWORD` | `your_password` | 无 | 管理员密码（至少 6 位，不设置则无法访问管理界面） |

#### API Keys 配置（根据使用的翻译器选择）

**OpenAI 系列**：
| 变量名 | 说明 |
|--------|------|
| `OPENAI_API_KEY` | OpenAI API Key（用于 openai、openai_hq 翻译器） |
| `OPENAI_MODEL` | OpenAI 模型名称（可选，默认 gpt-4o） |
| `OPENAI_API_BASE` | OpenAI API 基础 URL（可选，默认官方地址，可用于自定义端点） |
| `OPENAI_HTTP_PROXY` | OpenAI HTTP 代理（可选） |
| `OPENAI_GLOSSARY_PATH` | OpenAI 术语表路径（可选，默认 ./dict/mit_glossary.txt） |

**Google Gemini 系列**：
| 变量名 | 说明 |
|--------|------|
| `GEMINI_API_KEY` | Google Gemini API Key（用于 gemini、gemini_hq 翻译器） |
| `GEMINI_MODEL` | Gemini 模型名称（可选，默认 gemini-1.5-flash-002） |
| `GEMINI_API_BASE` | Gemini API 基础 URL（可选，默认官方地址） |

> 💡 **说明**：Google Cloud / Vertex 相关 API Key 也直接填写到 `GEMINI_API_KEY` 即可；`GEMINI_API_BASE` 留空或保持默认官方地址 `https://generativelanguage.googleapis.com`，无需修改。

**其他商业翻译服务**：
| 变量名 | 说明 |
|--------|------|
| `DEEPL_AUTH_KEY` | DeepL API Key |
| `GROQ_API_KEY` | Groq API Key |
| `GROQ_MODEL` | Groq 模型名称（可选，默认 mixtral-8x7b-32768） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_API_BASE` | DeepSeek API 基础 URL（可选，默认官方地址） |
| `DEEPSEEK_MODEL` | DeepSeek 模型名称（可选，默认 deepseek-chat） |
| `TOGETHER_API_KEY` | Together AI API Key |
| `TOGETHER_VL_MODEL` | Together AI 视觉模型（可选，默认 Qwen/Qwen2.5-VL-72B-Instruct） |

**国内翻译服务**：
| 变量名 | 说明 |
|--------|------|
| `BAIDU_APP_ID` | 百度翻译 APP ID |
| `BAIDU_SECRET_KEY` | 百度翻译密钥 |
| `YOUDAO_APP_KEY` | 有道翻译应用 ID |
| `YOUDAO_SECRET_KEY` | 有道翻译应用密钥 |
| `CAIYUN_TOKEN` | 彩云小译 API 访问令牌 |
| `PAPAGO_CLIENT_ID` | Papago 客户端 ID |
| `PAPAGO_CLIENT_SECRET` | Papago 客户端密钥 |

**本地/自定义模型**：
| 变量名 | 说明 |
|--------|------|
| `SAKURA_API_BASE` | Sakura API 地址（默认 http://127.0.0.1:8080/v1） |
| `SAKURA_DICT_PATH` | Sakura 术语表路径（可选，默认 ./dict/sakura_dict.txt） |
| `CUSTOM_OPENAI_API_KEY` | 自定义 OpenAI 兼容 API Key（如 Ollama，默认 ollama） |
| `CUSTOM_OPENAI_API_BASE` | 自定义 OpenAI 兼容 API 地址（默认 http://localhost:11434/v1） |
| `CUSTOM_OPENAI_MODEL` | 自定义模型名称（如 qwen2.5:7b） |
| `CUSTOM_OPENAI_MODEL_CONF` | 自定义模型配置（如 qwen2） |

> 💡 **提示**：
> - 只需配置你要使用的翻译器对应的 API Key
> - 如果不设置管理员密码，用户可以直接使用翻译功能，但无法访问管理界面
> - API Keys 也可以在启动后通过管理界面配置（需要先设置管理员密码）

### 访问地址

部署成功后访问：
- **用户界面**：`http://服务器IP:8000`
- **管理界面**：`http://服务器IP:8000/admin`（需要管理员密码）

### 宝塔面板部署步骤

1. **开放端口**：
   - 进入宝塔面板 → **安全** → 放行端口 `8000`
   - 如有云服务器安全组，也需要开放 `8000` 端口

2. **安装 Docker**：
   - 软件商店 → 搜索 **Docker 管理器** → 安装

3. **拉取镜像**：
   - Docker 管理器 → **镜像** → **从仓库拉取**
   - 填写镜像名：
     - CPU 版本：`hgmzhn/manga-translator:latest-cpu`
     - GPU 版本：`hgmzhn/manga-translator:latest-gpu`

4. **创建容器**：
   - **容器** → **创建容器**
   - **镜像**：选择刚才拉取的镜像
   - **端口映射**：`8000:8000`
   - **环境变量**：根据需要添加（可选）
   - **挂载目录/文件**：建议至少挂载下面这些路径
     - `宿主机目录 -> /app/manga_translator/server/data`
     - `宿主机目录 -> /app/config`
     - `宿主机目录 -> /app/dict`
     - `宿主机目录 -> /app/fonts`
     - `宿主机目录 -> /app/models`
     - 如需在网页后台保存服务器 API Keys，再额外挂 `宿主机文件 -> /app/.env`

     **最小配置**（无需设置环境变量，直接启动即可）

     **推荐配置示例**（设置管理员密码和 GPU）：
      ```
      MT_USE_GPU=true
      MANGA_TRANSLATOR_ADMIN_PASSWORD=your_secure_password
      ```

     **完整配置示例**（包含 API Keys）：
     ```
      MT_USE_GPU=true
      MANGA_TRANSLATOR_ADMIN_PASSWORD=your_secure_password
      OPENAI_API_KEY=sk-xxxxxxxxxxxxx
      GEMINI_API_KEY=xxxxxxxxxxxxx
      ```

5. **启动容器**，访问 `http://服务器IP:8000` 即可使用

> ⚠️ **注意**：Docker 镜像功能目前处于实验阶段，可能存在未知问题。

**部署完成后**：
- 🌐 **用户界面**：`http://服务器IP:8000` - 上传图片进行翻译
- 🔧 **管理界面**：`http://服务器IP:8000/admin` - 配置翻译器和参数（需要管理员密码）
- 📖 **使用教程**：[命令行使用指南](CLI_USAGE.md) - 了解更多功能和命令行模式

---

---

## 安装方式五：macOS 原生运行（Apple Silicon）

专为搭载 Apple Silicon (M1/M2/M3/M4) 芯片的 Mac 设备设计，利用 MPS (Metal Performance Shaders) 提供原生 GPU 加速。

### 系统要求

- **硬件**：Mac 电脑 (M1/M2/M3/M4 芯片，Intel Mac 也可运行但使用 CPU 模式)
- **系统**：macOS 12.0 或更高版本
- **软件**：Xcode Command Line Tools（脚本会自动检查并提示安装）

### 脚本说明

项目提供了 4 个 macOS 专用脚本，对应 Windows 的批处理脚本：

| 脚本文件 | 说明 | 对应 Windows |
|---------|------|-------------|
| `macOS_1_首次安装.sh` | 首次环境配置、代码克隆、Miniforge 安装、依赖安装 | Win-Install-or-Update.bat → [1] 安装 |
| `macOS_2_启动Qt界面.sh` | 启动图形界面 | Win-Start.bat |
| `macOS_3_检查更新并启动.sh` | 检查版本更新后启动 | （无单独对应，可用 Win-Install-or-Update.bat 更新后启动） |
| `macOS_4_更新维护.sh` | 运行维护菜单（更新代码、更新依赖、清理缓存等） | Win-Install-or-Update.bat |

### 安装步骤

**方式一：快速安装（推荐）**

只需下载安装脚本，其他全自动：

```bash
# 1. 下载安装脚本
curl -O https://raw.githubusercontent.com/hgmzhn/manga-translator-ui/main/macOS_1_首次安装.sh

# 2. 赋予执行权限
chmod +x macOS_1_首次安装.sh

# 3. 运行安装
./macOS_1_首次安装.sh
```

脚本会自动完成：
- 检查并提示安装 Xcode Command Line Tools（包含 Git）
- 克隆项目代码
- 检测并安装 Miniforge（如未安装）
- 创建独立的 `manga-env` 虚拟环境（Python 3.12）
- 从 `pyproject.toml` 安装 `metal` dependency group（Intel Mac 使用 `cpu`）
- 配置 MPS GPU 加速

**方式二：手动克隆**

如果你想先查看代码或已有 Git：

```bash
# 1. 克隆仓库
git clone https://github.com/hgmzhn/manga-translator-ui.git
cd manga-translator-ui

# 2. 赋予执行权限
chmod +x macOS_*.sh

# 3. 运行安装
./macOS_1_首次安装.sh
```

### 验证与启动

安装完成后：

- **正常启动**：
  ```bash
  ./macOS_2_启动Qt界面.sh
  ```

- **检查更新并启动**：
  ```bash
  ./macOS_3_检查更新并启动.sh
  ```

- **更新维护**：
  ```bash
  ./macOS_4_更新维护.sh
  ```
  维护菜单提供：
  - 更新代码（强制同步到远程）
  - 更新/安装依赖
  - 完整更新（代码+依赖）
  - 修复模式（重装所有依赖）

### 常见问题

**Q: 首次安装需要多长时间？**
A: 约 10-20 分钟，取决于网络速度。需要下载约 2GB 的依赖包。

**Q: Intel Mac 可以使用吗？**
A: 可以，脚本会自动检测并使用 Intel 版本的 Miniforge，但只能使用 CPU 模式（无 MPS 加速）。

**Q: 如何更新到最新版本？**
A: 运行 `./macOS_4_更新维护.sh`，选择"完整更新"即可。

---

## 首次运行

### 1. 启动程序

双击 `app.exe`，程序会自动：
- 加载 AI 模型（首次运行需要几分钟）
- 初始化翻译引擎
- 打开主界面

### 2. 通用设置（CPU 版本用户必看）

如果使用 **CPU 版本**，请务必：

1. 点击左侧导航的"设置"
2. 打开"通用"页
3. **取消勾选"使用 GPU"**，修改会自动保存

> ⚠️ **重要**：CPU 版本如果启用 GPU 会导致程序崩溃！

### 3. 设置输出目录

1. 点击左侧导航的"翻译界面"
2. 在"翻译任务"区域找到"输出目录:"
3. 点击"浏览..."选择翻译结果的保存位置
4. 也可以直接输入路径或拖拽输出文件夹到输入框，程序会记住此设置

### 4. 选择翻译器

1. 点击左侧导航的"设置"，打开"翻译"页
2. 在"翻译器"下拉菜单中选择：
   - **高质量翻译 OpenAI** 或 **高质量翻译 Gemini**（多模态，看图翻译，效果最好）⭐ 强烈推荐
   - 如需把 Google 官方 Key 与 Gemini 配置隔离，可选 **高质量翻译 Vertex**
   - 需要配置 API Key → [查看 API 配置教程](API_CONFIG.md)

### 5. 添加图片

支持以下方式添加图片：

- **方式 1**：点击"添加文件"按钮选择图片
- **方式 2**：点击"添加文件夹"按钮选择文件夹
- **方式 3**：直接拖拽图片到窗口

支持的图片格式：`.png`, `.jpg`, `.jpeg`, `.jfif`, `.webp`, `.avif`, `.bmp`, `.tiff`, `.tif`, `.heic`, `.heif`

### 6. 开始翻译

1. 确认设置无误
2. 点击"开始翻译"按钮
3. 等待翻译完成
4. 结果会自动保存到输出文件夹

---

## 故障排除

### 程序无法启动

**问题**：双击 `app.exe` 没有反应或闪退

**解决方法**：
1. 检查是否解压了所有文件（不要直接在压缩包中运行）
2. 检查杀毒软件是否拦截了程序
3. 以管理员身份运行 `app.exe`
4. 查看 `logs/error.log` 文件

### 缺少 DLL 文件

**问题**：提示缺少 `VCRUNTIME140.dll` 或其他 DLL 文件

**解决方法**：
1. 下载并安装 [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)
2. 重启电脑
3. 重新运行程序

### GPU 版本崩溃

**问题**：GPU 版本运行时崩溃或报错

**解决方法**：
1. 确认显卡支持 CUDA 12.x
2. 安装或更新 NVIDIA 显卡驱动
3. 下载并安装 [CUDA Toolkit 12.x](https://developer.nvidia.com/cuda-downloads)
4. 如果仍然失败，使用 CPU 版本

### 翻译失败

**问题**：添加图片后翻译失败

**解决方法**：
1. 检查图片格式是否支持
2. 确认 `models/` 目录中的模型文件完整
3. 在"设置" -> "通用"中勾选"详细日志"查看错误信息
4. 查看 `logs/app.log` 文件

### 模型加载缓慢

**问题**：首次运行时模型加载时间过长

**原因**：程序需要加载多个 AI 模型文件（总计约 2-3 GB）

**建议**：
- 首次运行耐心等待 5-10 分钟
- 后续运行会快很多（模型已缓存）
- 建议安装在 SSD 上以提高加载速度

---

## 下一步

安装完成后，建议阅读以下文档：

- [功能特性](FEATURES.md) - 了解程序的所有功能
- [工作流程](WORKFLOWS.md) - 学习不同的翻译工作流程
- [设置说明](SETTINGS.md) - 配置翻译器和参数

---

返回 [主页](../README.md)
