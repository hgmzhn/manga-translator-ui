# launch.py 启动与维护脚本说明

本文档描述 `packaging/launch.py` 的实际行为。它是安装、更新、依赖管理、维护菜单的统一入口；日常启动 UI 则由批处理直接运行 Qt 主程序。

## 1. 启动流程

### 1.1 各入口脚本与 launch.py 的关系

| 脚本 | 行为 |
|------|------|
| `Win-Start.bat` | 定位 Python 环境后**直接运行 `desktop_qt_ui\main.py`**（不经过 launch.py）；异常退出时提示运行安装/更新脚本 |
| `Win-Install-or-Update.bat` | 定位 Python 环境后运行 `packaging\launch.py --maintenance`，进入维护菜单 |
| `Unix-Install-or-Update.sh` | 开头确认一次后，引导 Git、uv、Python 3.12 和 `packaging`，然后直接进入双语维护菜单 |
| `Unix-Start.sh` | 使用 `.venv` 直接运行 `desktop_qt_ui/main.py` |

### 1.2 Windows 批处理定位 Python 的顺序

两个 bat 脚本逻辑相同：

1. 设置 `PYTHONUTF8=1`，`cd` 到脚本自身目录（修复管理员运行时工作目录为 system32 的问题）；
2. 若存在 `PortableGit\cmd\git.exe`，加入 PATH（launch.py 内部也优先使用此便携版 Git）；
3. **打包版 Python 优先**：存在 `packaging\python\python.exe` 则直接使用；
4. **Conda 回退**（旧版布局兼容）：依次查找本目录 `Miniconda3`、盘符根目录 `Miniconda3`（路径含非 ASCII 字符时预期在盘符根）、`CONDA_EXE` / PATH 中的 conda，解析 `manga-env` 环境或旧版 `conda_env` 目录；
5. 都找不到则报错退出，提示重新下载安装包。

### 1.3 launch.py main() 的执行顺序

1. 校验 Python 版本：**仅支持 3.12**（3.13+ 拒绝启动）；
2. 解析维护参数并切到项目根目录；
3. 默认进入维护菜单。安装或更新同步代码成功后，使用绝对路径 `os.execv` 重新加载当前文件，并通过隐藏的 `--resume-install` / `--resume-update` 参数继续后续依赖流程；
4. 重新加载后的进程执行 `prepare_environment()`，因此依赖逻辑使用更新后的 `launch.py`、`pyproject.toml` 和 `uv.lock`。

## 2. 命令行参数

| 参数 | 说明 |
|------|------|
| `--maintenance` | 进入维护菜单（Win-Install-or-Update.bat 和 Unix-Install-or-Update.sh 使用） |

内部恢复参数由代码同步后的重启自动传递，不用于手动调用。

## 3. 依赖安装机制

### 3.1 依赖声明：pyproject.toml + dependency groups

依赖不再使用 `requirements_*.txt`，全部声明在 `pyproject.toml` 中：

- `[project].dependencies`：公共依赖；
- `[dependency-groups]`：`cpu` / `gpu` / `amd` / `metal` 四个互斥后端组，以及独立的 `packaging` 打包组；
- `[tool.uv].default-groups`：源码开发默认使用 `gpu` + `packaging`；
- `[[tool.uv.index]]` + `[tool.uv.sources].torch`：定义各变体对应的 PyTorch 主源（如 cpu → `download.pytorch.org/whl/cpu`，gpu → `.../cu130`）；
- `tool.uv.sources` 中 url/git 类型来源（如 pydensecrf）按平台 marker 解析成 `name @ url` 形式交给安装器。

`get_variant_packages(variant)` 返回公共依赖 + 指定 dependency group 的完整包列表；`get_variant_index_url(variant)` 返回该变体的 PyTorch 主源。便携安装流程把这些依赖直接装入 `packaging\python`，不会创建 `.venv`。

### 3.2 uv 查找顺序

`find_uv()` 依次尝试：

1. `packaging\uv.exe`
2. 项目根目录 `uv.exe`
3. 系统 PATH 中的 `uv`
4. 当前 Python 环境已安装 uv 模块时用 `python -m uv`（conda 旧环境兼容）

找到 uv 走批量安装快速路径，否则回退 pip 逐包安装。

### 3.3 uv 批量安装（run_uv_packages）

- 缓存目录固定为 `UV_CACHE_DIR = packaging\uv_cache`（与包同盘，避免跨盘硬链接退化成整份复制）；
- 包列表分两批安装：
  - **PyTorch 相关包**（torch/torchvision/torchaudio/xformers/nvidia-* 等一大串前缀名单，torchsummary、torchmetrics 除外）：按 `get_pytorch_index_candidates()` 顺序回退。cu130 优先国内镜像（阿里云 → 上交大 → 官方源兜底）；官方 `download.pytorch.org` 是标准 PEP 503 索引作 `--index-url`，国内镜像是静态 wheel 目录，用 `--find-links` 挂载、其余依赖走 PyPI 镜像；
  - **普通包**：走 PyPI 镜像按顺序回退：清华 → 阿里云 → 豆瓣 → PyPI 官方（环境变量 `INDEX_URL` 可插队为首选）；
- 任一批次所有源都失败时抛异常，由上层回退到 pip 逐包安装。

### 3.4 pip 逐包回退（run_pip_packages_fallback）

未检测到 uv 时逐包安装：PyTorch 相关包用 PyTorch 专用源候选列表，普通包用上述 PyPI 镜像列表；某包在某源失败时自动切换到下一个镜像重试，全部失败才报错。

### 3.5 缓存自动清理

维护菜单的安装/更新成功后自动执行 `cleanup_caches()`：`uv cache clean` + `pip cache purge`，不询问用户。

## 4. GPU 检测与依赖方案选择

`detect_gpu()` 返回 `(类型, 名称, cuda_major, cuda_version, driver_version)`。

### 4.1 检测方式

- Windows：依次尝试 PowerShell `Get-CimInstance`、`wmic`、`Get-WmiObject`、注册表 DriverDesc、wmi Python 库（最后一招，必要时临时 pip 安装），多种结果合并去重；
- macOS arm64：`system_profiler` 读取芯片名，识别为 Apple Silicon；
- Linux / Intel Mac：`lspci`、`lshw`；
- 全部失败返回 `CPU`（进入手动选择流程）。

### 4.2 多显卡选择

检测到多张显卡时交互选择，按优先级给出默认推荐（NVIDIA > 有 ROCm 支持的 AMD > AMD 独显 > Intel 独显 > 核显）。设置环境变量 **`MANGAT_SELECTED_GPU`** 可跳过交互，支持三种匹配：序号（`1`）、类型（`NVIDIA`/`AMD`）、名称模糊匹配（`4070`、`780M`）。同类型多卡时按优先级取最高。

### 4.3 NVIDIA

- 通过 `nvidia-smi` 读取驱动版本和 CUDA 版本，正则**兼容新旧输出格式**：`CUDA Version: 12.8` 与新版的 `CUDA UMD Version: 13.3`；
- CUDA ≥ 13 时默认推荐 GPU 方案（cu130）；CUDA < 13 时提示更新驱动或改用 CPU；无法检测时由用户自行选择 y/n。

### 4.4 AMD（Linux ROCm 7.2 / Windows Radeon ROCm 7.2.1）

- `detect_amd_gfx_version()` 按显卡名称映射 gfx 架构。支持 PyTorch 的有：MI300/MI350 系列、RX 7900 XTX / 7800 XT / 7700S（gfx110X-dgpu）、Strix Halo iGPU（gfx1151）、RX 9060/9070 系列（gfx120X-all）；RX 5000/6000、Vega 明确不支持；
- 不支持或无法识别时提供选择：CPU（默认）/ 强制安装 AMD（实验性）/ 退出；
- Linux AMD 使用 `pyproject.toml` 中的 PyTorch ROCm 7.2 索引，并由 uv 安装 `torch`、`torchvision` 和 `triton-rocm`；
- Windows AMD 保留 `packaging/launch.py` 中的两阶段固定 URL 安装：先装 Radeon ROCm SDK，再装配套 Torch wheels，前置要求 [AMD 显卡驱动 26.2.2](https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-WIN-26-2-2.html)；
- 不自动设置 `HSA_OVERRIDE_GFX_VERSION`。需要该变量时由用户在当前启动会话中显式设置，不写入系统或项目持久化配置。

### 4.5 Apple Silicon

arm64 Mac 自动选择 `metal` 方案（MPS 加速），无需交互。

### 4.6 PyTorch 版本一致性

`prepare_environment()` 会在子进程中检测已装 PyTorch 类型（CUDA/ROCm/MPS/CPU），与目标方案不匹配时自动卸载重装（最多重试 3 次处理文件占用，卸载后清 pip 缓存），避免 DLL 冲突。

## 5. 维护菜单（--maintenance）

菜单为**中英双语**：`init_language()` 首次运行按系统语言自动选择并写入 `packaging\maintenance_config.json`，之后从配置读取；`L(zh, en)` 按当前语言输出文案。菜单顶部常驻显示当前分支（含 tag/游离状态标注）与镜像源，进入时先做一次版本检查。

| 选项 | 功能 |
|------|------|
| [1] 安装 | 选择下载线路（GitHub/Gitee）→ 强制同步代码 → 重启加载新代码 → 检测显卡并交互选择 CPU/GPU/AMD/Metal → 安装依赖 → 清理缓存 |
| [2] 更新 | `check_all_updates()` 检查代码（版本号 + commit 双比对）与依赖完整性 → 确认后强制同步代码 → 重启加载新代码并重新检查依赖 → 安装/同步依赖 → 清理缓存 |
| [3] 切换分支 | 在 `main`（稳定）/ `beta`（测试）间切换，`git checkout -f -B <branch> origin/<branch>` 强制同步，本地修改被覆盖 |
| [4] 切换版本 | fetch tags 后列出最近 20 个 tag（也可手输 tag 名），`checkout -f <tag>` 进入游离状态；游离状态下更新比对回落到 main |
| [5] 切换镜像源 | GitHub 官方 / Gitee 镜像（国内推荐）/ 手动输入仓库地址，`git remote set-url origin` |
| [6] 重新检查版本 | 显示本地/远程 `packaging/VERSION` 与落后提交数 |
| [7] 切换语言 | 中英互切并持久化到 maintenance_config.json |
| [8] 退出 | — |

容错机制：

- **同步失败推荐另一条线路**：`git_fetch_with_mirror_prompt()` 在 fetch 失败时自动推荐当前未使用的那条镜像（GitHub 失败推 Gitee，反之亦然），确认后切换并重试；
- **依赖失败重试**：`run_deps_with_retry()` 在依赖安装/更新失败时询问是否重试；已装成功的包会保留，重试只装剩余的包。

## 6. 更新后自动清理平台无关文件

`update_code_force()` 同步代码成功后按平台删除无关文件（删除失败静默忽略）：

- **Windows**：删除 `Unix-Install-or-Update.sh`、`Unix-Start.sh`，以及 `.gitattributes`、`.gitignore`、`LICENSE.txt`；
- **Linux/macOS**：只删除 `Win-Start.bat` 和 `Win-Install-or-Update.bat`，保留 `.gitattributes`、`.gitignore`、`LICENSE.txt` 和 Unix 脚本。

## 7. 相关文件与环境变量速查

| 项 | 说明 |
|----|------|
| `packaging/launch.py` | 本文档描述的脚本 |
| `packaging/VERSION` | 版本号文件，更新检查以此比对 |
| `packaging/maintenance_config.json` | 维护菜单语言等配置的持久化 |
| `packaging/uv_cache/` | uv 下载缓存（装完自动清理） |
| `packaging/uv.exe`、根目录 `uv.exe`、系统 PATH | uv 查找位置 |
| `pyproject.toml` | 依赖声明（公共依赖 + dependency groups + PyTorch 源） |
| `PortableGit/cmd/git.exe` | 便携版 Git，存在时优先使用 |
| `INDEX_URL` | 环境变量：指定首选 PyPI 镜像 |
| `MANGAT_SELECTED_GPU` | 环境变量：多显卡时跳过交互选择（序号/类型/名称模糊匹配） |
| `GIT` | 环境变量：无便携版 Git 时指定 git 可执行文件 |
| `HSA_OVERRIDE_GFX_VERSION` | 不由安装/启动脚本自动设置；需要时由用户临时显式设置 |
