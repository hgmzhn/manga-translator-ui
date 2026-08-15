# Installation Guide

This document provides detailed installation steps, system requirements, first-run guidance, and troubleshooting notes.

---

## 📋 Table of Contents

- [System Requirements](#system-requirements)
- [Method 1: Portable Installer](#method-1-portable-installer)
- [Method 2: Packaged Release](#method-2-packaged-release)
- [Method 3: Run from Source](#method-3-run-from-source)
- [Method 4: Docker Deployment](#method-4-docker-deployment)
- [Method 5: Native Linux/macOS Run](#method-5-native-linuxmacos-run)
- [First Run](#first-run)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

---

## System Requirements

### Minimum

- **Operating system**: Windows 10/11 (64-bit), Linux, or macOS 12+ (Apple Silicon recommended)
- **Memory**: 8 GB RAM
- **Storage**: 5 GB free space for the program and model files
- **Python version** for source runs: Python 3.12

### Recommended

- **Memory**: 16 GB RAM or more
- **GPU**:
  - **NVIDIA GPU**: GeForce 10-series GPUs must use CUDA 12.6; CUDA 13.0 requires Turing (compute capability 7.5) or newer. Drivers supporting CUDA 13.0 or newer can also run the CUDA 12.6 build
    - Recommended VRAM: 6 GB or more
    - Typical supported class: GTX 1060 and above
    - GeForce 10-series GPUs require CUDA 12.6; RTX 50-series GPUs require CUDA 13.0. Update the NVIDIA driver if needed
  - **AMD GPU**: ROCm support is experimental
    - Supported cards: **RX 7000 / 9000 only**
    - ⚠️ RX 5000 / 6000 should use the CPU build
    - ⚠️ Windows AMD can use the experimental AMD portable release or the maintenance installer; a supported GPU and AMD driver 26.2.2 are required
    - ⚠️ ROCm support on Windows is limited. Linux usually works better
- **Storage**: SSD with 10 GB or more free space

---

## Method 1: Portable Installer

This is the recommended path for Windows users. Download the portable installer package from GitHub Releases, extract it, and it is ready to use. The package ships with a bundled Python 3.12 (`packaging\python\python.exe`) and the uv package manager (`packaging\uv.exe`). It is fully portable: no registry writes and **no Python pre-install required**.

> ⚠️ **Network note**: installation downloads code and dependencies. Users in mainland China can pick the Gitee mirror and domestic PyPI mirrors from the menu.

### Prerequisites

- **No Python pre-install needed**: bundled Python 3.12 and uv are included
- Download the latest version from the [Portable Package release page](https://github.com/hgmzhn/manga-translator-ui/releases/tag/portable) and extract it to any folder

### Two entry scripts

After extraction, the folder contains two entry scripts. Just double-click them:

| Script | Purpose |
|------|------|
| `Win-Start.bat` | Start the program |
| `Win-Install-or-Update.bat` | Open the install / update maintenance menu |

### First-time install

Double-click `Win-Install-or-Update.bat` and choose **[1] Install** in the maintenance menu. The flow is:

1. **Choose a download route**: GitHub official / Gitee mirror (recommended in mainland China)
2. **Force-sync the latest code**: after a successful sync, the maintenance launcher reloads the updated code before continuing
3. **GPU detection**: automatically detects NVIDIA / AMD / integrated graphics; with multiple GPUs you get a list to pick from
4. **Choose a PyTorch build**:
   - **NVIDIA**: selected automatically from the GPU model, compute capability, and driver; GeForce 10-series GPUs are forced to CUDA 12.6, while Turing (compute capability 7.5) or newer uses CUDA 13.0 when supported by the driver
   - **AMD**: ROCm, experimental, **RX 7000 / 9000 series only**
   - **Other / integrated graphics**: CPU build
5. **Fast batch dependency install with uv**:
   - PyTorch comes from the official source or a domestic mirror
   - Everything else uses PyPI with mirror fallback: Tsinghua → Aliyun → Douban → official
   - Failed installs can be retried; already-installed packages are kept
6. **Download caches are cleaned up automatically** when finished

### Maintenance menu

The menu detects your system language and displays Chinese or English automatically. Its configuration is persisted in `packaging\maintenance_config.json`. Menu options:

- **[1] Install**: full install flow, see above
- **[2] Update**: checks code (compares remote VERSION and commit count on the current branch), reloads the launcher after code sync, then rechecks and updates dependencies
- **[3] Switch branch**: `main` stable / `beta` testing
- **[4] Switch to a historical version by tag**
- **[5] Switch mirror source**
- **[6] Re-check versions**
- **[7] Switch language** (Chinese / English)
- **[8] Exit**

### Dependency management

Dependencies are declared in `pyproject.toml` (five mutually exclusive dependency groups: `cpu` / `cuda13.0` / `cuda12.6` / `rocm7.2.1` / `metal`) and locked with `uv.lock`. The portable installer installs them directly into bundled `packaging\python`; it **does not create `.venv`**. `.venv` is only for source development.

### Start the program

After installation, just double-click `Win-Start.bat` whenever you want to use the app.

### Update later

Double-click `Win-Install-or-Update.bat` and choose **[2] Update**.

### Uninstall

The new setup is fully portable: **just delete the whole folder**. For old conda-based installs, see the [Uninstall Guide](UNINSTALL.md).

> 💡 **Compatibility with old installs**: if you previously installed with the old scripts (Miniconda3 plus a `manga-env` / `conda_env` environment), the new scripts fall back to that conda environment automatically when the bundled Python is not found. No reinstall is required.

---

## Method 2: Integrated Portable Release

This path is for Windows users who want to extract and run immediately. Each release already contains portable Python, the selected hardware dependencies, and model files, so downloads are large.

### 1. Open the release page

Go to [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases).

### 2. Choose a build

- `manga-translator-cpu-vX.Y.Z.7z.001`: best compatibility; no dedicated GPU required.
- `manga-translator-cuda13.0-vX.Y.Z.7z.001`: required for RTX 50-series GPUs; other NVIDIA GPUs with CUDA 13.0 support may also use it.
- `manga-translator-cuda12.6-vX.Y.Z.7z.001`: required for GeForce 10-series GPUs; RTX 50-series GPUs cannot use it.
- `manga-translator-rocm7.2.1-vX.Y.Z.7z.001`: experimental Radeon ROCm 7.2.1; requires a supported AMD GPU and AMD driver 26.2.2.

> CUDA 13.0 no longer supports NVIDIA architectures before Turing. GeForce 10-series GPUs such as the GTX 1060/1070/1080 must use the CUDA 12.6 package and must not download the CUDA 13.0 package.


### 3. Extract split volumes

Download every `.7z.001`, `.002`, and later volume for the selected build into one directory without renaming them, then extract `.001`. Missing any volume makes extraction fail.

### 4. Start

The extracted directory contains:

```text
manga-translator/
├── Win-Start.bat
├── Win-Install-or-Update.bat
├── packaging/
│   ├── python/         # Python 3.12 and installed dependencies
│   └── uv.exe
├── PortableGit/
├── models/             # Installed model files
├── config/
├── dict/
├── fonts/
└── desktop_qt_ui/
```

Double-click `Win-Start.bat`. Run `Win-Install-or-Update.bat` when you need to reinstall dependencies or switch versions.

## Method 3: Run from Source

Best for developers or users who want full control.

### 1. Clone the repository

```bash
git clone https://github.com/hgmzhn/manga-translator-ui.git
cd manga-translator-ui
```

### 2. Install dependencies

Dependencies are declared in `pyproject.toml`. The five dependency groups `cpu` / `cuda13.0` / `cuda12.6` / `rocm7.2.1` / `metal` are mutually exclusive; select one backend:

```bash
# NVIDIA CUDA 13.0 (source-development default)
uv sync

# NVIDIA CUDA 12.6
uv sync --no-default-groups --group cuda12.6

# CPU
uv sync --no-default-groups --group cpu

# Linux AMD ROCm 7.2; Windows uses the installer's ROCm 7.2.1 flow
uv sync --no-default-groups --group rocm7.2.1

# Apple Silicon / Metal
uv sync --no-default-groups --group metal
```

> 💡 **pip users**: run `uv export` to generate a requirements file, then install it with pip.

### 3. Run the program

```bash
# Qt desktop UI
uv run --no-sync python -m desktop_qt_ui.main

# Web UI / API server
uv run --no-sync python -m manga_translator web
```

---

## Method 4: Docker Deployment

Good for Docker users, server deployments, or users working through panel tools such as BT Panel or Portainer.

> 💡 **Note**: the `docker run` commands below are good for a quick test. For a long-running Web UI deployment, mount the persistent paths listed below.

### Quick start

**Windows CMD / PowerShell**

```cmd
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

**Linux / macOS**

```bash
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

After startup:

- 🌐 User UI: `http://localhost:8000`
- 🔧 Admin UI: `http://localhost:8000/admin`

### Image registries

This project publishes the same image to two registries:

**Docker Hub**

- CPU: `hgmzhn/manga-translator:latest-cpu`
- GPU: `hgmzhn/manga-translator:latest-gpu`

**GitHub Container Registry**

- CPU: `ghcr.io/hgmzhn/manga-translator:latest-cpu`
- GPU: `ghcr.io/hgmzhn/manga-translator:latest-gpu`

### Recommended Web UI persistence paths

For a real Web UI deployment, persist these paths:

| Path inside container | Priority | Purpose |
|------|------|------|
| `/app/manga_translator/server/data` | Required | Unified storage for `admin_config.json`, `user_resources/`, accounts, sessions, groups, permissions, quotas, API key presets, user configs, audit logs, translation-history indexes, and Web history result files |
| `/app/config` | Strongly recommended | Stores `config.json`, `custom_api_params.json`, `filter_list.json`, and other auto-created editable config files |
| `/app/dict` | Strongly recommended | Stores glossaries and AI prompt files such as `ai_ocr_prompt.yaml`, `ai_renderer_prompt.yaml`, and `ai_colorizer_prompt.yaml` |
| `/app/fonts` | Strongly recommended | Server-level fonts |
| `/app/models` | Strongly recommended | Downloaded models, so container recreation does not re-download them |
| `/app/.env` | As needed | Required if you want server API keys saved from the Web UI to survive container recreation |
| `/app/logs` | Optional | Root-level runtime logs |
| `/app/result` | Optional | CLI/debug outputs. Web history results mainly live under `server/data/results` |

> 💡 **File bind reminder**:
> - `admin_config.json` and `user_resources/` now live inside `/app/manga_translator/server/data`
> - Only `/app/.env` is still a **file bind**. Create an empty host file before starting the container, otherwise Docker may create a directory there instead

### Recommended docker-compose example

This is a better starting point for a long-running Web UI deployment than the minimal `docker run` example:

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
      # If you want server API keys saved in the Web UI to survive container recreation,
      # create an empty ./data/app.env first, then uncomment this line:
      # - ./data/app.env:/app/.env
```

### Port mapping

- **Container port**: `8000`
- **Host port**: `8000` by default, can be changed

### Environment variables

> 💡 All environment variables are optional. Reasonable defaults are used when possible.

#### Basic settings

| Variable | Example | Default | Description |
|--------|--------|--------|------|
| `MT_WEB_HOST` | `0.0.0.0` | `0.0.0.0` | Listen address |
| `MT_WEB_PORT` | `8000` | `8000` | Web server port |
| `MT_USE_GPU` | `true` | `false` | Enable GPU, only meaningful for GPU images |
| `MT_MODELS_TTL` | `300` | `0` | Model lifetime in memory, in seconds. `0` keeps models loaded |
| `MT_RETRY_ATTEMPTS` | `-1` | `None` | Retry count for failures. `-1` means unlimited |
| `MT_VERBOSE` | `true` | `false` | Enable verbose logs |
| `MANGA_TRANSLATOR_ADMIN_PASSWORD` | `your_password` | none | Admin password, at least 6 characters |

#### API keys

**OpenAI family**

| Variable | Description |
|--------|------|
| `OPENAI_API_KEY` | OpenAI API key, used by OpenAI translators |
| `OPENAI_MODEL` | OpenAI model name |
| `OPENAI_API_BASE` | OpenAI-compatible base URL |
| `OPENAI_HTTP_PROXY` | Optional HTTP proxy |
| `OPENAI_GLOSSARY_PATH` | Optional glossary path |

**Gemini family**

| Variable | Description |
|--------|------|
| `GEMINI_API_KEY` | Gemini API key |
| `GEMINI_MODEL` | Gemini model name |
| `GEMINI_API_BASE` | Gemini API base URL |

> Note: Google Cloud or Vertex-related API keys can also be entered directly in `GEMINI_API_KEY`. Leave `GEMINI_API_BASE` empty for the default official host, or keep `https://generativelanguage.googleapis.com`.

**Other commercial providers**

| Variable | Description |
|--------|------|
| `DEEPL_AUTH_KEY` | DeepL API key |
| `GROQ_API_KEY` | Groq API key |
| `GROQ_MODEL` | Groq model name |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `DEEPSEEK_API_BASE` | DeepSeek API base URL |
| `DEEPSEEK_MODEL` | DeepSeek model name |

**Domestic services**

| Variable | Description |
|--------|------|
| `BAIDU_APP_ID` | Baidu Translate App ID |
| `BAIDU_SECRET_KEY` | Baidu Translate secret |
| `YOUDAO_APP_KEY` | Youdao app key |
| `YOUDAO_SECRET_KEY` | Youdao secret |
| `CAIYUN_TOKEN` | Caiyun API token |

**Local / custom models**

| Variable | Description |
|--------|------|
| `SAKURA_API_BASE` | Sakura API base URL |
| `SAKURA_DICT_PATH` | Sakura glossary path |
| `CUSTOM_OPENAI_API_KEY` | Custom OpenAI-compatible API key |
| `CUSTOM_OPENAI_API_BASE` | Custom OpenAI-compatible base URL |
| `CUSTOM_OPENAI_MODEL` | Custom model name |
| `CUSTOM_OPENAI_MODEL_CONF` | Custom model config |

### Access URLs

After deployment:

- **User UI**: `http://server-ip:8000`
- **Admin UI**: `http://server-ip:8000/admin`

### BT Panel deployment outline

1. Open port `8000`
2. Install Docker manager from the panel
3. Pull the image
4. Create a container with `8000:8000`
5. Add environment variables if needed
6. Add persistent mounts for `/app/manga_translator/server/data`, `/app/config`, `/app/dict`, `/app/fonts`, and `/app/models`
7. If you want server API keys saved from the Web UI to persist too, also bind-mount `/app/.env` from an empty host file you created in advance
8. Start the container and open the site

> ⚠️ Docker support is still experimental.

---

## Method 5: Native Linux/macOS Run

Linux and macOS share the same installer. Apple Silicon uses MPS when available; Linux selects NVIDIA, AMD ROCm, or CPU dependencies.

### System requirements

- **Hardware**: Linux x86_64 or macOS; Intel Mac runs in CPU mode
- **OS**: Linux or macOS 12.0 or later
- **Tools**: Git; the script installs `uv` when needed

### Script mapping

| Script | Purpose | Windows equivalent |
|---------|------|-------------|
| `Unix-Install-or-Update.sh` | After one confirmation, bootstrap Git, uv, Python 3.12, and `packaging`, then open the bilingual install/update menu | `Win-Install-or-Update.bat` |
| `Unix-Start.sh` | Start the Qt UI | `Win-Start.bat` |

### Install steps

**Option 1: Quick install**

```bash
# 1. Download script
curl -O https://raw.githubusercontent.com/hgmzhn/manga-translator-ui/main/Unix-Install-or-Update.sh

# 2. Make it executable
chmod +x Unix-Install-or-Update.sh

# 3. Run installer
./Unix-Install-or-Update.sh
```

The script automatically:

- Checks Git
- Clones the project
- Installs Python 3.12 through `uv`
- Creates a project-local `.venv`
- Opens the bilingual Python menu; `launch.py` selects and installs `cpu`, `cuda13.0`, `cuda12.6`, `rocm7.2.1`, or `metal`

At startup, the script asks only once: `Start installation now? [Y/n]`. After confirmation, the normal bootstrap flow proceeds directly to the bilingual menu.

**Option 2: Clone manually first**

```bash
git clone https://github.com/hgmzhn/manga-translator-ui.git
cd manga-translator-ui
chmod +x Unix-*.sh
./Unix-Install-or-Update.sh
```

### Verify and run

```bash
# Normal launch
./Unix-Start.sh

# Update check and launch
./Unix-Install-or-Update.sh
# Choose [2] Update in the Python menu

# Maintenance menu
./Unix-Install-or-Update.sh
# Choose [2] Update in the Python menu
```

### FAQ

**Q: How long does the first install take?**
About 10 to 20 minutes depending on your network.

**Q: Can Intel Mac run it?**
Yes, but it will use CPU mode.

**Q: How do I update later?**
Run `./Unix-Install-or-Update.sh` and choose [2] Update in the Python menu.

---

## First Run

This section uses the current Qt UI labels from `en_US.json`.

### 1. Launch the program

Start one of these:

- `Win-Start.bat` for a Windows portable release
- `python -m desktop_qt_ui.main` for a source environment

The app uses the bundled dependency environment and local model files, initializes translation backends, then opens the main window on `Translation Interface`.

### 2. CPU build users: turn off GPU

If you are using the CPU package or a machine without a compatible GPU:

1. Open `Settings`
2. Open the `General` section
3. Turn off `Use GPU`

> ⚠️ Enabling `Use GPU` on a CPU-only setup can cause crashes or startup failures.

### 3. Set the output directory

1. Stay on `Translation Interface`
2. Find `Output Directory:`
3. Click `Browse...`
4. Choose where translated results should be saved

### 4. Configure your translator

If you want online translation:

1. Open `API Management`
2. Fill the required key such as `OpenAI API Key` or `Gemini API Key`
3. Return to `Translation Interface`
4. Choose `Translator`
5. Choose `Target Language`

Recommended first choices:

- `OpenAI High Quality`
- `Gemini High Quality`

### 5. Add images

You can add input pages in three ways:

- Click `Add Files`
- Click `Add Folder`
- Drag and drop files or folders into the file list

Supported formats include `.png`, `.jpg`, `.jpeg`, `.jfif`, `.webp`, `.avif`, `.bmp`, `.tiff`, `.tif`, `.heic`, and `.heif`.

### 6. Start translation

1. Confirm the settings
2. Click `Start Translation`
3. Wait for the task to finish
4. The output is saved to the selected output folder

If you want to fine-tune the result later, open it in `Editor View`.

---

## Troubleshooting

### The program does not start

**Symptoms**

- `Win-Start.bat` exits with an error
- The window flashes and closes immediately

**Try this**

1. Make sure every archive volume was fully extracted
2. Check whether antivirus blocked the package
3. Run `Win-Install-or-Update.bat` to check dependencies
4. Check the runtime log under `result/log_*.txt`

### Missing DLL files

**Symptoms**

- Error messages about `VCRUNTIME140.dll` or similar DLLs

**Try this**

1. Install [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)
2. Reboot the system
3. Start the app again

### GPU build crashes

**Symptoms**

- Crash or initialization error in GPU mode

**Try this**

1. Confirm the build matches the GPU: GeForce 10-series GPUs must use CUDA 12.6; CUDA 13.0 requires Turing (compute capability 7.5) or newer plus a driver that supports CUDA 13.0
2. Update the NVIDIA driver
3. If the issue is ONNX-specific, open `Settings` -> `General` and enable `Disable ONNX GPU Acceleration`
4. If the machine is not compatible, switch to the CPU build

### Translation fails after adding images

**Try this**

1. Confirm the image format is supported
2. Check whether model files are complete
3. Turn on `Verbose Logging`
4. Review:
   - `result/log_*.txt`
   - `result/<timestamp-image-target-translator>/`

### Model loading is very slow

**Why this happens**

- The first run needs to load several AI models and supporting files

**Suggestions**

- Wait 5 to 10 minutes on the first launch
- Later runs are much faster because models are cached
- Install on an SSD if possible

---

## Next Steps

After installation, these documents are the most useful next reads:

- [Features](./FEATURES.md)
- [Workflows](./WORKFLOWS.md)
- [Settings Reference](./SETTINGS.md)

---

Back to [README_EN](../../README_EN.md)
