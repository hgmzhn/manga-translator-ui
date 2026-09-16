#!/usr/bin/env bash

# Linux/macOS bootstrapper.
# Keep this file limited to system prerequisites and hand the application
# install/update workflow to the bilingual Python maintenance menu.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
REPO_URL="${MANGAT_REPO_URL:-https://github.com/hgmzhn/manga-translator-ui.git}"
ENV_DIR="$SCRIPT_DIR/.venv"
ENV_PYTHON="$ENV_DIR/bin/python"
UV=""
TEMP_DIR=""

on_error() {
    local status=$?
    printf '\n[ERROR] Bootstrap failed at line %s (exit code %s).\n' "${BASH_LINENO[0]:-unknown}" "$status" >&2
    printf '[ERROR] Fix the message above and run this script again.\n' >&2
    exit "$status"
}

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf "$TEMP_DIR"
    fi
}

trap on_error ERR
trap cleanup EXIT

info() { printf '[INFO] %s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[ERROR] %s\n' "$*" >&2; }

confirm() {
    local answer

    if [ "${MANGAT_AUTO_CONFIRM:-0}" = "1" ]; then
        return 0
    fi

    read -r -p "$1 [Y/n] " answer
    answer="${answer:-Y}"
    [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]
}

project_present() {
    [ -f "$SCRIPT_DIR/pyproject.toml" ] &&
        [ -d "$SCRIPT_DIR/desktop_qt_ui" ] &&
        [ -d "$SCRIPT_DIR/manga_translator" ] &&
        [ -f "$SCRIPT_DIR/packaging/launch.py" ]
}

check_platform() {
    local system
    local machine

    system="$(uname -s)"
    machine="$(uname -m)"
    case "$system" in
        Darwin)
            case "$machine" in
                arm64|x86_64) ;;
                *)
                    fail "Unsupported macOS architecture: $machine"
                    exit 1
                    ;;
            esac
            ;;
        Linux)
            case "$machine" in
                x86_64|amd64) ;;
                *)
                    warn "Architecture $machine is not covered by the bundled Linux wheels."
                    if ! confirm "Continue anyway?"; then
                        exit 1
                    fi
                    ;;
            esac
            ;;
        *)
            fail "This script supports Linux and macOS only (detected: $system)"
            exit 1
            ;;
    esac
}

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        fail "Root privileges are required, but sudo is not available"
        return 1
    fi
}

ensure_git() {
    local system

    if command -v git >/dev/null 2>&1; then
        ok "Git: $(git --version)"
        return 0
    fi

    system="$(uname -s)"
    info "Git is not installed. Attempting automatic installation..."
    case "$system" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                brew install git
            elif command -v xcode-select >/dev/null 2>&1; then
                warn "Git on macOS is provided by Xcode Command Line Tools."
                if confirm "Open the Xcode Command Line Tools installer?"; then
                    xcode-select --install || true
                    printf 'Finish the Apple installer, then run this script again.\n'
                fi
                exit 1
            else
                fail "Install Git with Homebrew or Xcode Command Line Tools, then retry"
                exit 1
            fi
            ;;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                run_as_root apt-get update
                run_as_root apt-get install -y git
            elif command -v dnf >/dev/null 2>&1; then
                run_as_root dnf install -y git
            elif command -v pacman >/dev/null 2>&1; then
                run_as_root pacman -Sy --noconfirm git
            elif command -v apk >/dev/null 2>&1; then
                run_as_root apk add git
            else
                fail "No supported Linux package manager was found. Install Git manually and retry"
                exit 1
            fi
            ;;
    esac

    if ! command -v git >/dev/null 2>&1; then
        fail "Git installation did not make git available in PATH"
        exit 1
    fi
    ok "Git: $(git --version)"
}

ensure_safe_install_dir() {
    local entry
    local name

    if project_present; then
        return 0
    fi

    if [ -d "$SCRIPT_DIR/.git" ]; then
        fail "This directory is a Git repository, but it is not a complete Manga Translator project"
        exit 1
    fi

    for entry in "$SCRIPT_DIR"/* "$SCRIPT_DIR"/.[!.]* "$SCRIPT_DIR"/..?*; do
        [ -e "$entry" ] || continue
        name="$(basename "$entry")"
        case "$name" in
            Unix-Install-or-Update.sh|Unix-Start.sh|.DS_Store)
                ;;
            *)
                fail "Refusing to clone into a non-empty unrelated directory: $SCRIPT_DIR"
                printf 'Move the two Unix scripts to a new directory and retry.\n' >&2
                exit 1
                ;;
        esac
    done
}

clone_project() {
    if project_present; then
        ok "Project files found: $SCRIPT_DIR"
        return 0
    fi

    info "Cloning project from $REPO_URL..."
    TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/manga-translator.XXXXXX")"
    GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=Never git clone --depth 1 "$REPO_URL" "$TEMP_DIR"
    (cd "$TEMP_DIR" && tar -cf - .) | (cd "$SCRIPT_DIR" && tar -xf -)
    rm -rf "$TEMP_DIR"
    TEMP_DIR=""

    if ! project_present; then
        fail "The cloned repository does not contain the expected project files"
        exit 1
    fi
    ok "Project files are ready"
}

find_uv() {
    local candidate

    if [ -n "${MANGAT_UV:-}" ] && [ -x "$MANGAT_UV" ]; then
        UV="$MANGAT_UV"
        return 0
    fi

    candidate="$(command -v uv 2>/dev/null || true)"
    if [ -n "$candidate" ]; then
        UV="$candidate"
        return 0
    fi

    for candidate in \
        "$SCRIPT_DIR/uv" \
        "$HOME/.local/bin/uv" \
        "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            UV="$candidate"
            return 0
        fi
    done

    return 1
}

ensure_uv() {
    if find_uv; then
        ok "uv: $UV"
        return 0
    fi

    if ! command -v curl >/dev/null 2>&1; then
        fail "curl is required to install uv"
        exit 1
    fi

    info "uv is not installed. Installing it with the official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if ! find_uv; then
        fail "uv was installed but could not be found. Add ~/.local/bin to PATH and retry"
        exit 1
    fi
    ok "uv: $UV"
}

create_environment() {
    local current_version

    info "Installing managed Python $PYTHON_VERSION..."
    "$UV" python install "$PYTHON_VERSION"

    current_version=""
    if [ -x "$ENV_PYTHON" ]; then
        current_version="$($ENV_PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    fi

    if [ "$current_version" != "$PYTHON_VERSION" ]; then
        if [ -n "$current_version" ]; then
            warn "Existing .venv uses Python $current_version; recreating it with $PYTHON_VERSION"
        fi
        "$UV" venv --clear "$ENV_DIR" --python "$PYTHON_VERSION"
    else
        "$UV" venv --allow-existing "$ENV_DIR" --python "$PYTHON_VERSION"
    fi

    if [ ! -x "$ENV_PYTHON" ]; then
        fail "The virtual environment was created, but $ENV_PYTHON is missing"
        exit 1
    fi
    ok "Virtual environment: $ENV_DIR"
}

install_launcher_dependencies() {
    info "Installing launcher bootstrap dependency: packaging<25.0"
    "$UV" pip install --python "$ENV_PYTHON" "packaging<25.0"
    "$UV" run --no-sync --python "$ENV_PYTHON" python -c 'import packaging; print(f"packaging {packaging.__version__}")'
    ok "Launcher bootstrap dependencies are ready"
}

start_python_maintenance() {
    info "Starting packaging/launch.py --maintenance"
    exec "$UV" run --no-sync --python "$ENV_PYTHON" packaging/launch.py --maintenance
}

main() {
    case "${1:-}" in
        --help|-h)
            printf 'Usage: %s\n' "$0"
            printf 'The script bootstraps Git, uv, Python 3.12, and the launcher, then opens the bilingual Python menu.\n'
            exit 0
            ;;
        "") ;;
        *)
            fail "Unknown argument: $1"
            exit 1
            ;;
    esac

    printf '%s\n' '=============================================='
    printf '%s\n' 'Manga Translator UI - Linux/macOS bootstrap'
    printf '%s\n\n' '=============================================='

    if ! confirm "Start installation now?"; then
        printf 'Installation cancelled.\n'
        exit 0
    fi

    check_platform
    ensure_git
    ensure_safe_install_dir
    clone_project
    ensure_uv
    create_environment
    install_launcher_dependencies
    start_python_maintenance
}

main "$@"
