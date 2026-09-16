#!/usr/bin/env bash

# Linux/macOS Qt launcher.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
ENV_DIR="$SCRIPT_DIR/.venv"
UV=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { printf '%b\n' "${BLUE}[*] $*${NC}"; }
ok() { printf '%b\n' "${GREEN}[OK] $*${NC}"; }
warn() { printf '%b\n' "${YELLOW}[WARN] $*${NC}"; }
fail() { printf '%b\n' "${RED}[ERROR] $*${NC}" >&2; }

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

ensure_project() {
    if [ ! -f "$SCRIPT_DIR/pyproject.toml" ] ||
       [ ! -d "$SCRIPT_DIR/desktop_qt_ui" ] ||
       [ ! -d "$SCRIPT_DIR/manga_translator" ]; then
        fail "Project files are missing from: $SCRIPT_DIR"
        echo "Run ./Unix-Install-or-Update.sh first"
        exit 1
    fi
}

run_legacy_python() {
    local candidate

    for candidate in \
        "$SCRIPT_DIR/conda_env/bin/python" \
        "$HOME/miniforge3/envs/manga-env/bin/python" \
        "$HOME/miniconda3/envs/manga-env/bin/python" \
        "$HOME/anaconda3/envs/manga-env/bin/python"; do
        if [ -x "$candidate" ]; then
            warn "Starting with legacy environment: $candidate"
            exec "$candidate" "$SCRIPT_DIR/desktop_qt_ui/main.py" "$@"
        fi
    done

    if command -v conda >/dev/null 2>&1; then
        warn "Starting with legacy Conda environment: manga-env"
        exec conda run --no-capture-output -n manga-env python "$SCRIPT_DIR/desktop_qt_ui/main.py" "$@"
    fi

    fail "No .venv or compatible legacy environment was found"
    echo "Run ./Unix-Install-or-Update.sh first"
    exit 1
}

main() {
    ensure_project

    if [ -x "$ENV_DIR/bin/python" ]; then
        if find_uv; then
            ok "Starting with uv/.venv"
            if [ "${MANGAT_DRY_RUN:-0}" = "1" ]; then
                echo "[DRY-RUN] $UV run --no-sync --python $ENV_DIR/bin/python desktop_qt_ui/main.py"
                exit 0
            fi
            exec "$UV" run --no-sync --python "$ENV_DIR/bin/python" desktop_qt_ui/main.py "$@"
        fi

        warn "uv was not found; starting directly with .venv Python"
        if [ "${MANGAT_DRY_RUN:-0}" = "1" ]; then
            echo "[DRY-RUN] $ENV_DIR/bin/python desktop_qt_ui/main.py"
            exit 0
        fi
        exec "$ENV_DIR/bin/python" "$SCRIPT_DIR/desktop_qt_ui/main.py" "$@"
    fi

    run_legacy_python "$@"
}

main "$@"
