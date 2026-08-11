# Developer Guide

This document is for developers who want to modify the source code, debug the pipeline, extend features, or participate in packaging and release work.

This guide only lists Git-tracked directories and files that are maintained together with the repository. It does not expand local caches, runtime artifacts, or uncommitted directories.

---

## 1. Development Prerequisites

### Python and environment

- The current repository uses **Python 3.12** as the baseline.
- `packaging/launch.py` and GitHub Actions also run on Python 3.12.
- You can use `venv`, Conda, or the project install scripts to create the environment.
- The environment name does **not** have to be `manga-env`.

### Dependency declaration

Dependencies are now declared in `pyproject.toml` at the repository root:

- Common dependencies live in `[project] dependencies`.
- The four backends are mutually exclusive dependency groups: `cpu` / `gpu` / `amd` / `metal` (`[tool.uv] conflicts` enforces the exclusivity).
- The default groups are `gpu` + `packaging` + `test`, so plain `uv sync` / `uv run` uses NVIDIA CUDA 13.0. The reproducible CUDA 12.6 environment is maintained on the `cuda12.6` branch. The installer uses `--no-default-groups`, so it neither checks nor installs the `test` group.
- PyTorch sources are bound via `[tool.uv.sources]` + `[[tool.uv.index]]`: the main branch uses `whl/cu130` for `gpu`, the `cuda12.6` branch uses `whl/cu126`, `cpu` uses `download.pytorch.org/whl/cpu`, Linux `amd` uses `whl/rocm7.2`, and `metal` uses the default PyPI.
- `uv.lock` is the lockfile. It is committed to the repository; do not edit it by hand.

The old `requirements_cpu.txt` / `requirements_gpu.txt` / `requirements_amd.txt` / `requirements_metal.txt` files have been removed.

### Dependency installation

uv is recommended. Install only one dependency group for the target runtime:

```bash
# NVIDIA GPU (CUDA 13.0, default; use the cuda12.6 branch for CUDA 12.6)
uv sync

# For other backends, disable the defaults and select one group
uv sync --no-default-groups --group cpu
uv sync --no-default-groups --group metal

# AMD (Linux): use the official PyTorch ROCm 7.2 index
uv sync --no-default-groups --group amd
# Windows AMD is handled by the Windows installer in Radeon SDK -> PyTorch order
```

In a source checkout, `uv sync` creates `.venv` and reproduces dependencies from `uv.lock`. The Windows portable installer uses bundled `packaging\python` and does not create `.venv`. To install source dependencies into an existing environment instead:

```bash
uv sync --active
```

The default GPU environment already includes the `packaging` and `test` groups. For another backend, add `packaging` explicitly when building:

```bash
uv sync --no-default-groups --group cpu --group packaging
```

---

## 2. Repository Structure

In day-to-day development, the tracked areas below are the most important ones to focus on.

### Core source areas

```text
manga-translator-ui-package/
├─ desktop_qt_ui/              # Qt desktop application
│  ├─ main.py                  # Desktop entry point
│  ├─ ui/                      # Centralized desktop UI definitions
│  │  ├─ main_window.py        # Main window and main lifecycle
│  │  ├─ styles.py             # Unified styles for the main page, editor, and secondary pages
│  │  ├─ theme.py              # Theme runtime, palette, and application logic
│  │  ├─ theme_tokens.py       # Theme tokens and color definitions
│  │  ├─ main_page/            # Main page view, layout, and runtime
│  │  ├─ editor/               # Editor page, canvas, and shortcuts
│  │  ├─ secondary_pages/      # Secondary pages and editor dialogs
│  │  ├─ widgets/              # Shared UI widgets
│  │  └─ icons/                # UI icon assets
│  ├─ services/                # Service container, config, translation, OCR, logging, etc.
│  ├─ editor/                  # Editor controllers, models, rendering, and document logic
│  └─ locales/                 # Multilingual text
├─ manga_translator/           # Core translation engine and server
│  ├─ __main__.py              # Unified CLI / web / ws / shared entry
│  ├─ detection/               # Text detection
│  ├─ ocr/                     # OCR models and adapters
│  ├─ translators/             # Translator implementations
│  ├─ inpainting/              # Text removal and background repair
│  ├─ rendering/               # Typesetting and render-back
│  ├─ upscaling/               # Super-resolution
│  ├─ colorization/            # Colorization
│  ├─ utils/                   # Shared utilities and intermediate formats
│  └─ server/                  # FastAPI server, static pages, admin panel
├─ packaging/                  # Launch scripts, update scripts, PyInstaller, Docker
├─ config/                     # Default config, templates, translator registry
├─ .github/                    # CI/CD and issue templates
├─ doc/                        # User documentation and changelogs
├─ fonts/                      # Default font resources
├─ dict/                       # Prompt, dictionary, and template resources
└─ README.md                   # Main project entry document
```

---

## 3. Code Layers and Entry Points

### 3.1 Qt desktop app

The desktop entry point is:

```bash
python -m desktop_qt_ui.main
```

The main startup path is roughly:

1. `desktop_qt_ui/main.py`
2. initialize logging, resource paths, and global exception handling
3. call `desktop_qt_ui.services.init_services(root_dir)`
4. create `MainWindow`
5. assemble the main UI and editor through `ui/main_window.py`, `ui/main_page/`, and `ui/editor/`

When making desktop-side changes, these are the usual landing points:

- change settings read/write behavior:
  - `desktop_qt_ui/services/config_service.py`
  - `desktop_qt_ui/core/config_models.py`
- change main page UI:
  - `desktop_qt_ui/ui/main_page/`
- change editor page or canvas UI:
  - `desktop_qt_ui/ui/editor/`
- change secondary pages or editor dialogs:
  - `desktop_qt_ui/ui/secondary_pages/`
- change shared widgets:
  - `desktop_qt_ui/ui/widgets/`
- change page, editor, or secondary-page styles:
  - `desktop_qt_ui/ui/styles.py`
- change theme tokens:
  - `desktop_qt_ui/ui/theme_tokens.py`
- change theme runtime:
  - `desktop_qt_ui/ui/theme.py`
- change editor business behavior:
  - `desktop_qt_ui/editor/`
- change service wiring:
  - `desktop_qt_ui/services/__init__.py`

### 3.2 Core engine and CLI

The unified runtime entry is:

```bash
python -m manga_translator <mode>
```

Currently implemented modes:

- `web`: start the FastAPI server and Web UI
- `local`: local command-line translation
- `ws`: WebSocket mode
- `shared`: shared API instance mode

Common examples:

```bash
# Web service
python -m manga_translator web --host 127.0.0.1 --port 8000

# Local translation
python -m manga_translator local -i path/to/image.png -o path/to/output
```

The core processing chain is mainly distributed under `manga_translator/`:

- `detection/`: text-region detection
- `ocr/`: text recognition
- `translators/`: text translation
- `inpainting/`: remove source text and repair the background
- `rendering/`: typeset and write translated text back
- `utils/textblock.py` and related files: intermediate structures and serialization

### 3.3 Server

The server entry is dispatched from the `web` mode in `manga_translator/__main__.py` into `manga_translator/server/main.py`.

The server directory is easiest to understand like this:

- `server/routes/`: HTTP routing layer
- `server/core/`: account, permission, quota, cleanup task, config-management, and similar service logic
- `server/repositories/`: JSON and file-storage wrappers
- `server/models/`: Pydantic or related data models
- `server/static/`: frontend static pages and admin-panel assets
- `server/data/`: server runtime data files

---

## 4. Configuration and Resource Packaging Rules

This project supports both development-mode execution and PyInstaller packaging.

Before changing any resource-path logic, make sure you know which tracked resources must be included in the release package.

### Common tracked resources used in development mode

- default config template:
  - `config/config-example.json`
- translator registry:
  - `config/config/translators.json`
- resource directories:
  - `fonts/`
  - `dict/`
  - `doc/`
  - `desktop_qt_ui/locales/`

### Tracked resources to pay attention to during packaging

- `config/`
- `fonts/`
- `dict/`
- `doc/`
- `desktop_qt_ui/locales/`

If you add a new resource directory, template file, or config file, check both of these:

1. whether development mode can load it correctly relative to the project root
2. whether the PyInstaller spec files and GitHub workflows also include it in the release package

---

## 5. Local Development Workflow

### 5.1 Recommended startup order

```bash
# 1. Install the default GPU dependencies (creates .venv and reproduces uv.lock automatically)
uv sync

# 2. Activate the environment (Windows PowerShell)
.venv\Scripts\Activate.ps1

# 3. Start the desktop app
python -m desktop_qt_ui.main
```

If you mainly work on the server:

```bash
python -m manga_translator web --host 127.0.0.1 --port 8000 -v
```

### 5.2 Common change landing points

#### Add a new setting

At minimum, check the chain below. Many settings require more than changing only a few files.

1. `desktop_qt_ui/core/config_models.py`
   Define the field, default value, type, validation, and compatibility migration.
2. `manga_translator/config.py`
   If the setting is used by the core translation pipeline, CLI, Web service, or a lower-level module, sync the core config model and related enums here as well.
   Otherwise the desktop app may save the value, but the backend runtime may never read it.
3. `config/config-example.json`
   Sync the default config template so the new field appears in exported config and first-run config.
4. `desktop_qt_ui/ui/main_page/settings_tab_layout.json`
   If the setting should appear in the settings page, add `section.key` to the correct tab `items`.
5. `desktop_qt_ui/app_logic.py`
   If the setting is a dropdown or needs friendly display text, add support in `get_options_for_key()`, `get_display_mapping()`, and related label mapping if needed.
6. `desktop_qt_ui/locales/*.json`
   At minimum, add `label_xxx` and `desc_section_key`.
   If it is an enum-style option, also add the corresponding option text keys.
7. `desktop_qt_ui/ui/main_page/dynamic_settings.py`
   If the default generic widget is not enough, or if the field should be hidden, grouped, given buttons, given placeholders, or routed to a special editor, add the custom logic here.
8. `desktop_qt_ui/app_logic.py`
   If changing the setting should trigger immediate side effects, such as switching translators, refreshing rendering, or updating linked fields, add the runtime behavior in `update_single_config()`.
9. The module that actually consumes the setting
   For example:
   - `desktop_qt_ui/services/`
   - `manga_translator/ocr/`
   - `manga_translator/rendering/`
   - `manga_translator/translators/`

   Otherwise the setting will only be stored but will not actually do anything.

Depending on the setting type, also check these extra locations:

- If it is a **new enum value** rather than a **new field**:
  also check the enum or config type in `manga_translator/config.py`, the option list and display mapping in `desktop_qt_ui/app_logic.py`, and the related locale strings.
- If the setting should also affect CLI or Web behavior:
  check `manga_translator/config.py`, `manga_translator/args.py`, the relevant mode or service parameter-merge logic, and the actual backend consumption point.
- If the setting introduces new API dependencies or environment variables:
  check `config/config/translators.json` and the validation logic in `desktop_qt_ui/services/config_service.py`.
- If the setting is temporary state that should be excluded from import/export:
  check `export_config()` and `import_config()` in `desktop_qt_ui/app_logic.py`.
- If the setting affects editor-side display or editing behavior:
  continue into `desktop_qt_ui/ui/editor/`, `desktop_qt_ui/ui/widgets/property_panel.py`, and the related `desktop_qt_ui/editor/` logic.

Current UI note:

If the setting is meant to appear on the current desktop settings page, it will usually belong under one of these current UI pages instead of the older broad tab wording:

- `Settings` -> `General`
- `Settings` -> `OCR`
- `Settings` -> `Detection`
- `Settings` -> `Translation`
- `Settings` -> `Inpainting`
- `Settings` -> `Typesetting`
- `Settings` -> `Mode Specific`

#### Add or integrate a new translator / OCR / renderer

You usually need to update all of these together:

1. add the implementation under `manga_translator/<corresponding_module>/`
2. update the config and enum entry points
3. if API environment variables are involved, update `config/config/translators.json`
4. add UI options, documentation, and tests if needed

#### Modify editor behavior

Start in `desktop_qt_ui/editor/`, especially:

- `editor_controller.py`
- `editor_logic.py`
- `graphics_view.py`
- `graphics_items.py`
- `commands.py`
- `selection_manager.py`

---

## 6. Validation and Debugging

### Code style

The only tracked static-check configuration currently visible in the repository is:

- `desktop_qt_ui/ruff.toml`

If `ruff` is already installed locally, you can run this basic check:

```bash
ruff check desktop_qt_ui manga_translator --config desktop_qt_ui/ruff.toml
```

The boundary of that statement is:

- the `pyproject.toml` at the repository root only declares dependencies and contains no lint configuration; beyond that, the repository does not include other tracked config files such as `setup.cfg`, `tox.ini`, `.flake8`, or a second `ruff.toml`
- the current GitHub Actions workflows also do not explicitly run a lint step
- so the command above is better treated as a local self-check, not proof that CI currently treats it as a required pass gate

The current `ruff.toml` mainly enables `E`, `F`, and `I`, while ignoring:

- `E501`
- `E701`
- `E402`

### Debugging document

- For detailed troubleshooting flow, see [DEBUGGING.md](DEBUGGING.md)

---

## 7. Packaging and Release

### Local PyInstaller build

Build script entry:

```bash
python packaging/build_packages.py <version> --build cpu
python packaging/build_packages.py <version> --build gpu
python packaging/build_packages.py <version> --build both
```

Related files:

- `packaging/build_packages.py`
- `packaging/manga-translator-cpu.spec`
- `packaging/manga-translator-gpu.spec`
- `packaging/create-manga-pdfs.spec`
- `packaging/manga-chapter-splitter.spec`

### Launch and install scripts

The main end-user scripts live in the repository root:

- `Win-Start.bat` (launcher)
- `Win-Install-or-Update.bat` (install / update maintenance menu)
- `Unix-Install-or-Update.sh` / `Unix-Start.sh` (Linux/macOS)

The actual logic behind those scripts is concentrated in files such as:

- `packaging/launch.py`
- `packaging/git_update.py`

When changing install or update behavior, do not change only the `.bat` or `.sh` shell wrappers.

### CI/CD

- `.github/workflows/build-and-release.yml`
  - builds CPU, NVIDIA CUDA 13.0 GPU, NVIDIA CUDA 12.6 GPU, and AMD Windows runtimes from the portable base, installs locked dependencies and models, then creates split 7z archives
  - collects all four build artifacts on Ubuntu and publishes the GitHub Release
- `.github/workflows/docker-build-push.yml`
  - builds CPU and GPU Docker images based on `packaging/Dockerfile`

If you add resources that must be included in packaged builds, also update the workflow steps that copy them next to the executable.

---

## 8. Development Advice

- When touching config, templates, fonts, or dictionaries, always verify both development-mode and packaged-mode paths.
- When modifying desktop settings, at minimum check the default config, UI text, locale files, and serialization compatibility.
- When changing release flow, do not look only at local `packaging/`; check GitHub Actions at the same time.

---

## 9. Related Documents

- [Installation Guide](INSTALLATION.md)
- [Usage Guide](USAGE.md)
- [CLI Usage Guide](CLI_USAGE.md)
- [Debugging Guide](DEBUGGING.md)
- [Settings Reference](SETTINGS.md)
- [README_EN](../../README_EN.md)
