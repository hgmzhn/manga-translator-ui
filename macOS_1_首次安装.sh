#!/bin/bash

# ==================== macOS 首次安装脚本 ====================
# 对应 Windows 的「步骤1-首次安装.bat」
# 使用 Miniforge/Conda 管理 Python 3.12 环境
# =====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

CONDA_ENV_NAME="${CONDA_ENV_NAME:-manga-env}"
CONDA_ENV_PATH="${CONDA_ENV_PATH:-$SCRIPT_DIR/conda_env}"
MANGAT_PREFER_LOCAL_ENV="${MANGAT_PREFER_LOCAL_ENV:-0}"
MINIFORGE_DIR="${MINIFORGE_DIR:-$SCRIPT_DIR/Miniforge3}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
REPO_URL_DEFAULT="https://github.com/hgmzhn/manga-translator-ui.git"
REPO_URL="${MANGAT_REPO_URL:-$REPO_URL_DEFAULT}"
MINIFORGE_URL_ARM64="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh"
MINIFORGE_URL_X86_64="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-x86_64.sh"
MINIFORGE_URL="$MINIFORGE_URL_ARM64"
DEPENDENCY_GROUP="metal"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

CONDA_BIN=""
CONDA_ROOT=""
ENV_PATH=""
ENV_PYTHON=""
CONDA_ENV_MODE=""
USE_DIRECT_ENV_PYTHON=0
TEMP_DIR=""

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf "$TEMP_DIR"
    fi
}
trap cleanup EXIT

info() { echo -e "${BLUE}[*] $*${NC}"; }
ok() { echo -e "${GREEN}[OK] $*${NC}"; }
warn() { echo -e "${YELLOW}[警告] $*${NC}"; }
fail() { echo -e "${RED}[错误] $*${NC}" >&2; }

confirm() {
    local prompt="$1"
    local default="${2:-n}"
    local reply

    read -r -p "$prompt" reply
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[Yy]$|^[Yy][Ee][Ss]$ ]]
}

project_present() {
    [ -d "$SCRIPT_DIR/manga_translator" ] &&
        [ -d "$SCRIPT_DIR/desktop_qt_ui" ] &&
        [ -f "$SCRIPT_DIR/packaging/VERSION" ]
}

ensure_safe_install_dir() {
    local entry
    local name

    if project_present; then
        return 0
    fi

    if [ -d "$SCRIPT_DIR/.git" ]; then
        local remote_url
        remote_url="$(git -C "$SCRIPT_DIR" config --get remote.origin.url || true)"
        if [[ "$remote_url" == *manga-translator-ui* ]]; then
            return 0
        fi
    fi

    for entry in "$SCRIPT_DIR"/* "$SCRIPT_DIR"/.[!.]* "$SCRIPT_DIR"/..?*; do
        [ -e "$entry" ] || continue
        name="$(basename "$entry")"
        case "$name" in
            "macOS_1_首次安装.sh"|"macOS_common.sh"|"macOS_2_启动Qt界面.sh"|"macOS_3_检查更新并启动.sh"|"macOS_4_更新维护.sh"|".DS_Store")
                ;;
            *)
                fail "当前目录不是空目录，也不是现有项目目录"
                echo "当前目录: $SCRIPT_DIR"
                echo "为避免覆盖无关文件，请把安装脚本放到一个新目录后再运行。"
                exit 1
                ;;
        esac
    done
}

check_architecture() {
    local arch
    arch="$(uname -m)"

    if [ "$arch" = "arm64" ]; then
        ok "检测到 Apple Silicon (arm64)"
        MINIFORGE_URL="$MINIFORGE_URL_ARM64"
        DEPENDENCY_GROUP="metal"
    elif [ "$arch" = "x86_64" ]; then
        warn "检测到 Intel Mac (x86_64)，将使用 CPU 模式"
        MINIFORGE_URL="$MINIFORGE_URL_X86_64"
        DEPENDENCY_GROUP="cpu"
    else
        fail "不支持的 macOS 架构: $arch"
        exit 1
    fi
}

check_xcode_tools() {
    info "检查 Xcode 命令行工具..."
    if xcode-select -p >/dev/null 2>&1; then
        ok "Xcode 命令行工具已安装"
        return 0
    fi

    warn "需要安装 Xcode 命令行工具（包含 Git 和编译工具）"
    if confirm "是否现在打开安装程序? (y/n, 默认y): " "y"; then
        xcode-select --install || true
        echo "请等待安装完成后重新运行此脚本。"
        exit 0
    fi
}

check_git() {
    info "检查 Git..."
    if command -v git >/dev/null 2>&1; then
        ok "Git 已安装: $(git --version)"
        return 0
    fi

    if project_present; then
        warn "未检测到 Git；已有项目代码，将跳过代码克隆"
        return 0
    fi

    fail "未检测到 Git，且当前目录还没有项目代码"
    echo "请先安装 Xcode 命令行工具或 Git 后重新运行。"
    exit 1
}

setup_repository() {
    local repo_choice
    local clone_url

    info "检查代码仓库..."
    if project_present; then
        ok "检测到完整项目代码"
        if [ -d "$SCRIPT_DIR/.git" ]; then
            echo "安装脚本不会在首次安装阶段强制同步或覆盖本地代码。"
            echo "后续需要更新时，请运行 ./macOS_4_更新维护.sh。"
        fi
        return 0
    fi

    echo "未检测到项目代码，需要克隆仓库。"
    echo ""
    echo "请选择仓库源:"
    echo "  [1] GitHub 官方"
    echo "  [2] gh-proxy.com 镜像（国内网络可尝试）"
    read -r -p "请选择 (1/2, 默认1): " repo_choice

    if [ "$repo_choice" = "2" ]; then
        clone_url="https://gh-proxy.com/$REPO_URL"
    else
        clone_url="$REPO_URL"
    fi

    TEMP_DIR="$(mktemp -d "$SCRIPT_DIR/manga_translator_temp.XXXXXX")"
    info "克隆代码到临时目录..."
    git clone "$clone_url" "$TEMP_DIR"

    info "复制项目文件..."
    (cd "$TEMP_DIR" && tar -cf - .) | (cd "$SCRIPT_DIR" && tar -xf -)
    rm -rf "$TEMP_DIR"
    TEMP_DIR=""
    ok "代码克隆完成"
}

resolve_conda_root() {
    local candidate="$1"
    local root

    if [ ! -x "$candidate" ]; then
        return 1
    fi
    if ! "$candidate" --version >/dev/null 2>&1; then
        return 1
    fi

    root="$("$candidate" info --base 2>/dev/null || true)"
    if [ -z "$root" ]; then
        root="$(cd "$(dirname "$candidate")/.." && pwd)"
    fi
    if [ ! -x "$root/bin/conda" ]; then
        return 1
    fi

    CONDA_BIN="$root/bin/conda"
    CONDA_ROOT="$root"
    return 0
}

find_conda() {
    local candidate
    local command_conda

    for candidate in \
        "$MINIFORGE_DIR/bin/conda" \
        "$SCRIPT_DIR/Miniconda3/bin/conda" \
        "$HOME/miniforge3/bin/conda" \
        "$HOME/miniconda3/bin/conda" \
        "$HOME/anaconda3/bin/conda" \
        "/opt/homebrew/Caskroom/miniforge/base/bin/conda" \
        "/opt/homebrew/anaconda3/bin/conda"; do
        if resolve_conda_root "$candidate"; then
            return 0
        fi
    done

    if [ -n "${CONDA_EXE:-}" ] && resolve_conda_root "$CONDA_EXE"; then
        return 0
    fi

    command_conda="$(command -v conda 2>/dev/null || true)"
    if [ -n "$command_conda" ] && resolve_conda_root "$command_conda"; then
        return 0
    fi

    return 1
}

setup_miniforge() {
    local installer_path

    info "检查 Conda..."
    if find_conda; then
        ok "检测到 Conda: $CONDA_ROOT"
        return 0
    fi

    echo "未检测到 Conda，将安装 Miniforge 到:"
    echo "  $MINIFORGE_DIR"
    if ! confirm "是否继续安装 Miniforge? (y/n, 默认y): " "y"; then
        exit 1
    fi

    installer_path="$SCRIPT_DIR/miniforge_installer.sh"
    info "下载 Miniforge..."
    curl -fL -o "$installer_path" "$MINIFORGE_URL"

    info "安装 Miniforge..."
    bash "$installer_path" -b -p "$MINIFORGE_DIR"
    rm -f "$installer_path"

    resolve_conda_root "$MINIFORGE_DIR/bin/conda"
    ok "Miniforge 安装完成"
}

init_conda() {
    local hook

    export PATH="$CONDA_ROOT/bin:$PATH"
    hook="$("$CONDA_BIN" shell.bash hook 2>/dev/null || true)"
    if [ -n "$hook" ]; then
        eval "$hook"
    elif [ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]; then
        # shellcheck source=/dev/null
        source "$CONDA_ROOT/etc/profile.d/conda.sh"
    else
        fail "无法初始化 Conda shell"
        exit 1
    fi

    conda config --add channels conda-forge >/dev/null 2>&1 || true
    conda config --set channel_priority flexible >/dev/null 2>&1 || true
    ok "Conda 已初始化"
}

resolve_env_path() {
    local env_list
    local found_path

    ENV_PATH=""
    CONDA_ENV_MODE=""

    if [ -x "$CONDA_ROOT/envs/$CONDA_ENV_NAME/bin/python" ]; then
        ENV_PATH="$CONDA_ROOT/envs/$CONDA_ENV_NAME"
        CONDA_ENV_MODE="named"
        return 0
    fi

    env_list="$(conda env list 2>/dev/null || true)"
    found_path="$(printf '%s\n' "$env_list" | awk -v name="$CONDA_ENV_NAME" '$1 == name {print $NF; exit}')"
    if [ -n "$found_path" ] && [ -x "$found_path/bin/python" ]; then
        ENV_PATH="$found_path"
        CONDA_ENV_MODE="named"
        return 0
    fi

    if [ -x "$CONDA_ENV_PATH/bin/python" ]; then
        ENV_PATH="$CONDA_ENV_PATH"
        CONDA_ENV_MODE="legacy"
        return 0
    fi

    return 1
}

create_or_reuse_environment() {
    info "设置 Conda 环境..."

    if resolve_env_path; then
        ok "检测到已有环境: $ENV_PATH"
        if confirm "是否删除并重新创建? (y/n, 默认n): " "n"; then
            if [ "$CONDA_ENV_MODE" = "named" ]; then
                conda env remove -n "$CONDA_ENV_NAME" -y
            else
                conda env remove -p "$ENV_PATH" -y
            fi
            if [ "$MANGAT_PREFER_LOCAL_ENV" = "1" ]; then
                conda create -p "$CONDA_ENV_PATH" "python=$PYTHON_VERSION" -y
            else
                conda create -n "$CONDA_ENV_NAME" "python=$PYTHON_VERSION" -y
            fi
        fi
    else
        if [ "$MANGAT_PREFER_LOCAL_ENV" = "1" ]; then
            info "创建项目本地环境: $CONDA_ENV_PATH (Python $PYTHON_VERSION)"
            conda create -p "$CONDA_ENV_PATH" "python=$PYTHON_VERSION" -y
        else
            info "创建新环境: $CONDA_ENV_NAME (Python $PYTHON_VERSION)"
            if ! conda create -n "$CONDA_ENV_NAME" "python=$PYTHON_VERSION" -y; then
                warn "命名环境创建失败，改用项目本地环境: $CONDA_ENV_PATH"
                conda create -p "$CONDA_ENV_PATH" "python=$PYTHON_VERSION" -y
            fi
        fi
    fi

    resolve_env_path
    ENV_PYTHON="$ENV_PATH/bin/python"
    if [ "$CONDA_ENV_MODE" = "named" ]; then
        conda activate "$CONDA_ENV_NAME" || USE_DIRECT_ENV_PYTHON=1
    else
        conda activate "$ENV_PATH" || USE_DIRECT_ENV_PYTHON=1
    fi

    if [ "$USE_DIRECT_ENV_PYTHON" = "1" ]; then
        export PATH="$ENV_PATH/bin:$PATH"
        export CONDA_PREFIX="$ENV_PATH"
        export CONDA_DEFAULT_ENV="$ENV_PATH"
        warn "Conda 激活失败，改用环境内 Python 直接运行"
    fi

    ok "环境已就绪: $ENV_PATH"
}

run_env_python() {
    if [ "$USE_DIRECT_ENV_PYTHON" = "1" ]; then
        "$ENV_PYTHON" "$@"
    else
        python "$@"
    fi
}

install_dependencies() {
    info "安装依赖组: $DEPENDENCY_GROUP..."
    if [ ! -f "$SCRIPT_DIR/pyproject.toml" ]; then
        fail "未找到 pyproject.toml"
        exit 1
    fi
    if [ ! -f "$SCRIPT_DIR/packaging/launch.py" ]; then
        fail "未找到 packaging/launch.py"
        exit 1
    fi

    run_env_python -m pip install --upgrade pip
    run_env_python packaging/launch.py --requirements "$DEPENDENCY_GROUP" --install-deps-only
}

verify_installation() {
    info "验证安装..."
    run_env_python - <<'PY'
import sys

print(f"Python: {sys.version}")

try:
    import torch
    print(f"PyTorch: {torch.__version__}")
    print(f"MPS Available: {torch.backends.mps.is_available()}")
    print(f"MPS Built: {torch.backends.mps.is_built()}")
except Exception as exc:
    print(f"[警告] PyTorch 检查失败: {exc}")

try:
    import PyQt6
    print("[OK] PyQt6 模块导入成功")
except Exception as exc:
    print(f"[警告] PyQt6 模块导入失败: {exc}")

try:
    import manga_translator
    print("[OK] manga_translator 模块导入成功")
except Exception as exc:
    print(f"[警告] manga_translator 模块导入失败: {exc}")
PY
}

main() {
    echo "=============================================="
    echo "  Manga Translator UI - 首次安装"
    echo "=============================================="
    echo ""

    ensure_safe_install_dir
    check_architecture
    check_xcode_tools
    check_git
    setup_repository
    setup_miniforge
    init_conda
    create_or_reuse_environment
    install_dependencies
    verify_installation

    echo ""
    echo "=============================================="
    ok "安装完成"
    echo "=============================================="
    echo ""
    echo "启动方式:"
    echo "  ./macOS_2_启动Qt界面.sh"
    echo ""
    echo "检查更新并启动:"
    echo "  ./macOS_3_检查更新并启动.sh"
    echo ""
    echo "更新维护:"
    echo "  ./macOS_4_更新维护.sh"
}

main "$@"
