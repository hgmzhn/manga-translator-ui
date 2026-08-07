<div align="center">

<img src="doc/images/主页.png" width="500" alt="Main page">

[![DeepWiki Docs](https://img.shields.io/badge/DeepWiki-Online%20Docs-blue)](https://deepwiki.com/hgmzhn/manga-translator-ui)
[![Based On](https://img.shields.io/badge/Based%20On-manga--image--translator-green)](https://github.com/zyddnys/manga-image-translator)
[![Model](https://img.shields.io/badge/Model-Real--CUGAN-orange)](https://github.com/bilibili/ailab)
[![Model](https://img.shields.io/badge/Model-MangaJaNai-orange)](https://github.com/the-database/MangaJaNai)
[![Model](https://img.shields.io/badge/Model-YSG-orange)](https://github.com/lhj5426/YSG)
[![Model](https://img.shields.io/badge/Model-MangaLens%20Bubble%20Segmentation-orange?logo=huggingface)](https://huggingface.co/huyvux3005/manga109-segmentation-bubble)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR-blue)](https://github.com/PaddlePaddle/PaddleOCR)
[![OCR](https://img.shields.io/badge/OCR-MangaOCR-blue)](https://github.com/kha-white/manga-ocr)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR--VL--1.5-blue)](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5)
[![License](https://img.shields.io/badge/License-GPL--3.0-red)](LICENSE.txt)

</div>

**Language / 语言**: [简体中文](README.md) | English

Translate text in manga images with one click. The app supports Japanese manga, Korean comics, Western comics, black-and-white pages, and color pages. It can automatically detect text, run OCR, translate, inpaint, typeset, and it includes a built-in visual editor for adjusting text boxes.

**💬 QQ Group: 1079089991 (Password: `kP9#mB2!vR5*sL1`)** | **🐛 [Open an Issue](https://github.com/hgmzhn/manga-translator-ui/issues)**

---

## 📚 Documentation Navigation

The English companion versions of the documents below are being added one by one. Where an English file already exists, the link points to it. Otherwise it falls back to the current Chinese original so the navigation stays complete.

| Document | Description |
|------|------|
| [Installation Guide](doc/en/INSTALLATION.md) | Detailed installation steps, system requirements, and split archive notes |
| [Usage Guide](doc/en/USAGE.md) | Basic operations, translator selection, and common settings |
| [CLI Mode](doc/en/CLI_USAGE.md) | Command-line usage, argument reference, and batch processing |
| [API Configuration](doc/en/API_CONFIG.md) | API key registration and setup instructions |
| [Features](doc/en/FEATURES.md) | Full feature list and visual editor details |
| [Workflows](doc/en/WORKFLOWS.md) | Workflow modes, AI line breaking, and custom templates |
| [Settings Reference](doc/en/SETTINGS.md) | Translator config, OCR models, and parameter details |
| [Debugging Guide](doc/en/DEBUGGING.md) | Debug workflow, tunable parameters, and troubleshooting |
| [Developer Guide](doc/en/DEVELOPMENT.md) | Project structure, environment setup, and build packaging |

---

## 📸 Results Preview

<div align="center">

<table>
<tr>
<td align="center"><b>Before Translation</b></td>
<td align="center"><b>After Translation</b></td>
</tr>
<tr>
<td><img src="doc/images/0012.png" width="400" alt="Before translation"></td>
<td><img src="doc/images/110012.png" width="400" alt="After translation"></td>
</tr>
</table>

</div>

---

## ✨ Core Features

### Translation Features

- 🔍 **Smart Text Detection** - Automatically detects text regions in manga pages
- 📝 **Multilingual OCR** - Supports Japanese, Chinese, English, and more
- 🌐 **Multiple Translation Engines** - `OpenAI`, `Google Gemini`, `Vertex`, `OpenAI High Quality`, `Gemini High Quality`, `Vertex High Quality`, and `Sakura`
- 🎯 **High-Quality Translation** - Supports multimodal AI translation with GPT-4o-class and Gemini-class models
- 📚 **Automatic Glossary Extraction** - AI can collect new proper nouns and terms to keep translations consistent
- 🤖 **AI Line Breaking** - Improves readability by automatically optimizing line breaks
- 🎨 **Smart Typesetting** - Automatically lays out translated text and supports multiple fonts
- 📥 **PSD Export** - Exports editable PSD files with original image, inpainted image, and text layers
- 📦 **Batch Processing** - Process an entire folder in one run

### Visual Editor

- ✏️ **Region Editing** - Move, rotate, and reshape text boxes
- 📐 **Text Editing** - Manually edit translations and adjust styles
- 🖌️ **Mask Editing** - Brush and eraser tools for cleanup
- ⏪ **Undo / Redo** - Full editing history
- ⌨️ **Shortcut Support** - Supports `A` / `D` to switch images, `Q` / `W` / `E` to switch tools, `Ctrl+A` to select all text boxes, and `Ctrl+Q` to export in the editor
- 🖱️ **Mouse Wheel Shortcuts** - `Ctrl + wheel` can scale text boxes and `Shift + wheel` can adjust brush size

**Full Feature Reference** → [doc/en/FEATURES.md](doc/en/FEATURES.md)

---

## 🚀 Quick Start

### 📥 Installation Methods

> ⚠️ **Windows users: install the runtime first**. Before the first install, make sure the [Microsoft Visual C++ Redistributable (vc_redist.x64.exe)](https://aka.ms/vs/17/release/vc_redist.x64.exe) is installed; otherwise the app may fail to start (e.g., missing VCRUNTIME140.dll).

#### Method 1: Portable Package (⭐ Recommended, supports updates)

> ⚠️ **No Python pre-install required**: the package bundles a portable Python 3.12 and the uv package manager — fully portable, no registry writes
>  
> 💡 **One-click update**: run `Win-Install-or-Update.bat` and choose `[2] Update` to get the latest version

1. **Download the portable package**
   - Download the latest version from the [Portable Package release page](https://github.com/hgmzhn/manga-translator-ui/releases/tag/portable)
   - Extract it to any folder, for example `D:\manga-translator-ui\`

2. **Run the installer**
   - Double-click `Win-Install-or-Update.bat` and choose `[1] Install`
   - The script will automatically:
     - ✓ Let you pick a download route (GitHub official / Gitee / GitCode mirrors), and suggest switching routes if syncing fails
     - ✓ Sync the latest code
     - ✓ Detect your GPU type: NVIDIA, AMD, or integrated graphics
     - ✓ Automatically choose the matching PyTorch build
       - NVIDIA: CUDA 13.x build, with a driver that supports CUDA 13.0
       - AMD: ROCm build, experimental, **RX 7000 / 9000 only**. RX 5000 / 6000 should use the CPU build
       - Others: CPU build, slower but widely compatible
     - ✓ Install all dependencies with uv (fast batch install with mirror fallback)
     - ✓ Clean download caches automatically when done
   - The maintenance menu also supports: switching branch (main/beta), switching version by tag, switching mirror, and a Chinese/English interface toggle

3. **Start the program**
   - Double-click `Win-Start.bat`

4. **Uninstall**
   - Portable version: just delete the whole folder
   - Legacy version: see the [Uninstall Guide](doc/en/UNINSTALL.md)

#### Method 2: Download the Packaged Release

1. **Download the program**
   - Open [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases)
   - Choose a package:
     - **CPU build**: works on all computers
     - **GPU build (NVIDIA)**: requires an NVIDIA GPU with CUDA 13.x support
     - ⚠️ **AMD GPU is not supported in the packaged release**, please use Method 1 instead

2. **Extract and run**
   - Extract the archive to any folder
   - Double-click `app.exe`

#### Method 3: Docker Deployment (Experimental)

> 💡 **Note**: the commands below are fine for a quick trial. For a real Web UI deployment, mount the persistent paths listed below.

**Quick start**:

```bash
# Windows CMD / PowerShell
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu

# Linux / macOS
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

**Image registries**:

This project publishes Docker images to two registries. Use whichever is faster for you:

- **Docker Hub** (recommended):
  - CPU build: `hgmzhn/manga-translator:latest-cpu`
  - GPU build: `hgmzhn/manga-translator:latest-gpu`

- **GitHub Container Registry** (alternative, can be faster in some regions):
  - CPU build: `ghcr.io/hgmzhn/manga-translator:latest-cpu`
  - GPU build: `ghcr.io/hgmzhn/manga-translator:latest-gpu`

**Access URLs** (default port `8000`):

- 🌐 User UI: `http://localhost:8000`
- 🔧 Admin UI: `http://localhost:8000/admin`

**Recommended Web UI persistence**:

- Required directories: `/app/manga_translator/server/data`, `/app/config`, `/app/dict`, `/app/fonts`, `/app/models`
- Optional directories: `/app/logs`, `/app/result`
- Optional file: `/app/.env`. Mount this too if you want server API keys saved from the Web UI to survive container recreation
- `server/data` now also contains `admin_config.json`, `user_resources/`, accounts, sessions, groups, permissions, quotas, API key presets, user configs, and translation-history metadata

> 📖 **Detailed installation**: [Docker deployment section](doc/en/INSTALLATION.md#method-4-docker-deployment)
>  
> 📖 **Usage guide**: [CLI usage guide](doc/en/CLI_USAGE.md)

#### Method 4: Run from Source (Developers)

Best for developers or users who want full customization.

1. **Install Python 3.12**: [Download](https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe)
2. **Clone the repository**:

   ```bash
   git clone https://github.com/hgmzhn/manga-translator-ui.git
   cd manga-translator-ui
   ```

3. **Install dependencies** (run only the block for your hardware):

   ```bash
   python -m pip install uv

   # NVIDIA GPU (CUDA 13.0, default)
   uv sync

   # CPU build (choose this instead of the GPU command above)
   uv sync --no-default-groups --group cpu

   # AMD GPU (experimental; Linux uses the ROCm 7.2 index)
   uv sync --no-default-groups --group amd

   # Apple Silicon / Metal
   uv sync --no-default-groups --group metal
   ```

4. **Run the app**:

   ```bash
   # Desktop UI
   uv run --no-sync python -m desktop_qt_ui.main

   # Web UI (optional)
   uv run --no-sync python -m manga_translator web
   ```

> 📖 **Detailed installation**: [Installation Guide](doc/en/INSTALLATION.md)
>  
> 📖 **Usage guide**: [CLI usage guide](doc/en/CLI_USAGE.md)

#### Method 5: Linux/macOS Run

Linux and macOS use the same `uv`-based installer. Apple Silicon uses MPS when available; Linux selects NVIDIA, AMD ROCm, or CPU dependencies.

**Quick start (recommended)**:

1. **Download the install script**:

   ```bash
   curl -O https://raw.githubusercontent.com/hgmzhn/manga-translator-ui/main/Unix-Install-or-Update.sh
   chmod +x Unix-Install-or-Update.sh
   ```

2. **Run the installer**:

   ```bash
   ./Unix-Install-or-Update.sh
   ```

   The script will automatically:
   - Check for Git
   - Clone the project
   - Install Python 3.12 through `uv`
   - Create a project-local `.venv`
   - Open the bilingual Python installer/update menu, where you select the dependency group

   At startup, the script asks once: `Start installation now? [Y/n]`. After confirmation, it continues directly to the Python menu.

3. **Start the app**:

   ```bash
   ./Unix-Start.sh
   ```

4. **Update later**:

   ```bash
   ./Unix-Install-or-Update.sh
   # Choose [2] Update in the Python menu
   ```

**Or clone manually**:

```bash
git clone https://github.com/hgmzhn/manga-translator-ui.git
cd manga-translator-ui
chmod +x Unix-*.sh
./Unix-Install-or-Update.sh
```

> ⚠️ **Notes**:
> - Apple Silicon is the priority target
> - Intel Macs can still run, but they will use CPU mode
> - First-time installation downloads about 2 GB of dependencies, so make sure your network is stable

---

## 📖 Usage Guide

### 🖥️ Qt UI Mode

After installation, open the usage guide to learn how to translate images:

**Usage Guide** → [doc/en/USAGE.md](doc/en/USAGE.md)

Basic steps:

1. Fill in API information if you want to use online translators → [API Configuration Guide](doc/en/API_CONFIG.md)
2. Turn off `Use GPU` if you are using the CPU build
3. Set the output directory
4. Add images
5. Choose a translator
   - Recommended for first use: `OpenAI High Quality` or `Gemini High Quality`
   - If you want a separate Google-official key/model set, `Vertex High Quality` is also available
   - These require API keys. See the [API Configuration Guide](doc/en/API_CONFIG.md)
6. Start translation

### ⌨️ CLI Mode

Best for batch processing and automation scripts.

**CLI Guide** → [doc/en/CLI_USAGE.md](doc/en/CLI_USAGE.md)

> ⚠️ **Important**: before using the CLI, activate the virtual environment in the project directory first:
>
> ```bash
> # Windows
> conda activate manga-env
>
> # Linux/macOS
> source .venv/bin/activate
> ```

Quick start:

```bash
# Local mode (recommended for CLI translation)
python -m manga_translator local -i manga.jpg

# Short form (defaults to Local mode)
python -m manga_translator -i manga.jpg

# Translate a whole folder
python -m manga_translator local -i ./manga_folder/ -o ./output/

# Web server mode (with API and admin UI)
python -m manga_translator web --host 127.0.0.1 --port 8000 --use-gpu

# Show all arguments
python -m manga_translator --help
```

---

## 📋 Workflows

This program supports multiple workflows:

1. **Normal Translation** - translate images directly
2. **Export Translation** - translate first, then export text with the template-configured extension (JSON by default)
3. **Export Original Text** - detect and OCR only, then export the original text for manual translation
4. **Import Translation and Render** - import translated content from TXT or JSON and render again

The current Qt UI also includes additional workflow entries such as `Translate JSON Only`, `Colorize Only`, `Upscale Only`, `Inpaint Only`, and `Replace Translation`.

**Workflow Details** → [doc/en/WORKFLOWS.md](doc/en/WORKFLOWS.md)

---

## ⚙️ Common Translators

### Online Translators (Require API Keys)

- **OpenAI** - uses GPT-family models
- **Google Gemini** - uses Gemini-family models
- **Sakura** - optimized for Japanese
- Google Cloud or Vertex-related API keys can also be entered directly in the `Gemini` fields; keep the default official `Base URL`

### High-Quality Translators (Recommended)

- **OpenAI High Quality** - uses multimodal GPT-4o-class models
- **Gemini High Quality** - uses multimodal Gemini models
- 📸 Uses image context together with text, which usually gives more accurate translations

### AI OCR / Colorization / Rendering (Optional)

- **AI OCR** - supports `openai_ocr` and `gemini_ocr`
- **AI Colorization** - supports `openai_colorizer` and `gemini_colorizer`
- **AI Rendering** - supports `openai_renderer` and `gemini_renderer`
- In the desktop UI, configure them under `API Management` -> `OCR` / `Colorization` / `Render`
- The web admin UI exposes matching Gemini / OpenAI API-key groups as well. Google Cloud or Vertex-related API keys can also use the Gemini group directly; keep the default official `Base URL`. See the [API Configuration Guide](doc/en/API_CONFIG.md) and [Settings Reference](doc/en/SETTINGS.md)

**Full Settings Reference** → [doc/en/SETTINGS.md](doc/en/SETTINGS.md)

---

## 🔍 Need Help?

### Translation quality is not ideal

1. Turn on `Verbose Logging` in the settings
2. Check the debug files under `result/`
3. Adjust detector and OCR related parameters
4. After troubleshooting, clean up old logs and debug folders to keep `result/` from growing too large

**Debugging Guide** → [doc/en/DEBUGGING.md](doc/en/DEBUGGING.md)

---

## ⭐ Star History

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=hgmzhn/manga-translator-ui&type=Date)](https://star-history.com/#hgmzhn/manga-translator-ui&Date)

</div>

---

## 🙏 Acknowledgements

- [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) - core translation engine
- [bilibili/ailab](https://github.com/bilibili/ailab) - Real-CUGAN super-resolution model
- [the-database/MangaJaNai](https://github.com/the-database/MangaJaNai) - MangaJaNai / IllustrationJaNai super-resolution models
- [lhj5426/YSG](https://github.com/lhj5426/YSG) - model support
- [huyvux3005/manga109-segmentation-bubble](https://huggingface.co/huyvux3005/manga109-segmentation-bubble) - MangaLens Bubble Segmentation model
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - OCR model support
- [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) - MangaOCR model support
- [PaddlePaddle/PaddleOCR-VL-1.5](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) - official PaddleOCR-VL-1.5 model page
- All contributors and users who support the project

---

## ❤️ Support the Author

If this project helps you, you are welcome to buy the author a milk tea.

<div align="center">

<table style="border: none;">
<tr>
<td align="center" style="padding: 10px;">
<div style="border: 2px solid #e0e0e0; border-radius: 10px; padding: 15px; background: #f9f9f9; display: inline-block;">
  <div style="background: white; padding: 10px; border-radius: 8px;">
    <img src="doc/images/mm_reward_qrcode_1765200960689.png" width="180" alt="WeChat support QR code">
  </div>
  <p style="margin: 8px 0 0 0; font-size: 14px; color: #666;">💚 WeChat Support</p>
</div>
</td>
<td width="30"></td>
<td align="center" style="padding: 10px;">
<div style="border: 2px solid #e0e0e0; border-radius: 10px; padding: 15px; background: #f9f9f9; display: inline-block;">
  <div style="background: white; padding: 10px; border-radius: 8px;">
    <img src="doc/images/IMG_20251223_173711.jpg" width="180" alt="Alipay support QR code">
  </div>
  <p style="margin: 8px 0 0 0; font-size: 14px; color: #666;">💙 Alipay Support</p>
</div>
</td>
</tr>
</table>

<p style="font-size: 13px; color: #888; margin-top: 10px;">Thank you for your support.</p>

</div>

---

## 📝 License

This project is open sourced under the GPL-3.0 license.

### Model License Notice

The source code of this project uses the **GPL-3.0** license.

This project also supports MangaJaNai / IllustrationJaNai model weights for image super-resolution. Those model weights use the **CC BY-NC 4.0** license and are for non-commercial use only.

- **Model source**: [MangaJaNai](https://github.com/the-database/MangaJaNai)
- **Model license**: CC BY-NC 4.0
- **Usage restriction**: non-commercial use only

---

## ⚠️ Special Notice

This project is provided for technical demonstration, personal study, and communication purposes only. It does not constitute legal, commercial, or compliance advice.

When installing, configuring, calling, or distributing this project and related features, you are responsible for confirming and continuously complying with local laws, platform rules, content source licenses, and third-party service terms.

### Disclaimer and Limitation of Liability

- All actions and consequences resulting from use of this project, including but not limited to content processing, publishing, distribution, redistribution, and commercial use, are the sole responsibility of the user.
- You must ensure that your input content, output content, and data sources are legally authorized, and that they are not used in ways that infringe copyright, trademark, privacy, portrait rights, or other lawful rights and interests.
- This project must not be used for any illegal or non-compliant purpose, including but not limited to piracy distribution, unauthorized mass scraping or reposting, bypassing platform restrictions, fraud, defamation, or infringement of lawful rights and interests.
- This project depends on third-party models, APIs, datasets, and libraries, including OCR, translation, and super-resolution related services. Availability, accuracy, stability, pricing, risk control, and compliance requirements are the responsibility of the corresponding providers, and users bear the related risks and costs.
- To the maximum extent permitted by applicable law, the project author and contributors are not liable for any direct or indirect loss arising from the use of or inability to use this project, including but not limited to data loss, business interruption, profit loss, account risk, or third-party claims.
- If you use this project in a team or organizational environment, you are responsible for permission management, logging and auditing, content review, compliance assessment, and establishing the necessary human review process.

Please evaluate the risks carefully before use. Continuing to use this project is deemed as having read, understood, and agreed to the statements above.

---
