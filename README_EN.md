<div align="center">

<img src="doc/images/主页.png" width="760" alt="Manga Translator UI main window">

# Manga Translator UI

**Translate the text in manga pages with one click** — detect → OCR → translate → inpaint → typeset, with a built-in visual editor for the whole pipeline

[![Wiki](https://img.shields.io/badge/Wiki-Read%20the%20docs-4C8BF5?style=for-the-badge)](https://hgmzhn.github.io/manga-translator-ui/en/)
[![Download](https://img.shields.io/badge/Download-Releases-2EA043?style=for-the-badge)](https://github.com/hgmzhn/manga-translator-ui/releases)
[![Feedback](https://img.shields.io/badge/Feedback-Open%20an%20Issue-D73A49?style=for-the-badge)](https://github.com/hgmzhn/manga-translator-ui/issues)

[![Based On](https://img.shields.io/badge/Based%20On-manga--image--translator-green)](https://github.com/zyddnys/manga-image-translator)
[![DeepWiki Docs](https://img.shields.io/badge/DeepWiki-Online%20Docs-blue)](https://deepwiki.com/hgmzhn/manga-translator-ui)
[![License](https://img.shields.io/badge/License-GPL--3.0-red)](LICENSE.txt)

[![Model](https://img.shields.io/badge/Model-Real--CUGAN-orange)](https://github.com/bilibili/ailab)
[![Model](https://img.shields.io/badge/Model-MangaJaNai-orange)](https://github.com/the-database/MangaJaNai)
[![Model](https://img.shields.io/badge/Model-YSG-orange)](https://github.com/lhj5426/YSG)
[![Model](https://img.shields.io/badge/Model-MangaLens%20Bubble%20Segmentation-orange?logo=huggingface)](https://huggingface.co/huyvux3005/manga109-segmentation-bubble)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR-blue)](https://github.com/PaddlePaddle/PaddleOCR)
[![OCR](https://img.shields.io/badge/OCR-MangaOCR-blue)](https://github.com/kha-white/manga-ocr)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR--VL--1.5-blue)](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5)

**Language / 语言**: [简体中文](README.md) | English

</div>

Works with Japanese manga, Korean comics, and Western comics, in black-and-white or in color. The app detects text, runs OCR, translates, inpaints and typesets automatically, supports Japanese, Chinese, English and more, and ships with a visual editor for adjusting layout, styles and rich text box by box.

> 📖 **The full documentation now lives in the online wiki** → **[hgmzhn.github.io/manga-translator-ui](https://hgmzhn.github.io/manga-translator-ui/en/)**
> Installation · every setting explained · editor · batch management · workflows · CLI · troubleshooting · developer docs. Bilingual, with full-text search.

**💬 QQ Group: 1079089991 (password: `kP9#mB2!vR5*sL1`)** ｜ **🐛 [Open an Issue](https://github.com/hgmzhn/manga-translator-ui/issues)**

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

<table>
<tr>
<th width="25%">Translation pipeline</th>
<th width="25%">Typesetting & rich text</th>
<th width="25%">Visual editor</th>
<th width="25%">Batch & automation</th>
</tr>
<tr valign="top">
<td>

- 🔍 Smart text detection
- 📝 Multilingual OCR
- 🌐 OpenAI / Gemini / Vertex / Sakura
- 🎯 High-quality multimodal translation
- 📚 Automatic glossary extraction
- 🎨 AI colorization / OCR / rendering
- 🔑 API key rotation and cooldown

</td>
<td>

- 🎈 Smart balloon layout
- ✂️ Chinese semantic line breaking
- 🤖 AI line breaking
- 🖋️ Automatic horizontal / vertical detection
- 🎨 Full rich text (ruby / TCY)
- 🧩 Rich-text rules applied automatically
- 🔤 System fonts plus custom `fonts/`

</td>
<td>

- ✏️ Move, rotate and reshape text boxes
- 📐 Per-box size / color / stroke / spacing
- 🖌️ Mask brush, eraser and clone stamp
- 🔍 Side-by-side compare with the original
- 📏 Align and distribute multiple regions
- ⏪ Full undo / redo history
- ⌨️ Keyboard and mouse-wheel shortcuts

</td>
<td>

- 📦 Process a whole folder in one run
- 🧰 Batch schemes (preview → apply → restore)
- 📥 Layered PSD export
- 🔄 JSON / TXT import, export and write-back
- ⌨️ Command-line mode
- 🌐 Web UI with accounts and quotas
- 🐳 Docker deployment

</td>
</tr>
</table>

📖 [Full shortcut reference](https://hgmzhn.github.io/manga-translator-ui/en/desktop/editor/shortcuts) ｜ [Every setting explained](https://hgmzhn.github.io/manga-translator-ui/en/desktop/settings/)

### 🎈 Smart Balloon Layout

Translated text is **fitted to the shape of the speech balloon** instead of a plain rectangle. The layout mode offers Smart Balloon (fill the balloon outline) / Smart Scaling / Strict Bounds, with Smart Balloon as the default, plus `Center in Bubble` and `Bubble Layout (Force Horizontal)` toggles.

📖 [Typesetting and rendering](https://hgmzhn.github.io/manga-translator-ui/en/desktop/settings/typesetting-and-rendering)

### 🧰 Batch Management

Match regions across the whole file list by condition, then bulk-edit properties, replace text, or apply rich-text styles in one pass. Rules are saved as reusable schemes: preview the matches, tick what you want, write back, and restore from a backup if it goes wrong.

📖 [Batch management](https://hgmzhn.github.io/manga-translator-ui/en/desktop/batch-management/schemes-crud)

### ✂️ Chinese Semantic Line Breaking

A local HanLP model decides where to break lines by meaning, so words are no longer split down the middle — the difference is most visible in vertical Chinese text. It falls back to plain wrapping when the model is missing, and can be stacked with AI line breaking.

📖 [Line-breaking settings](https://hgmzhn.github.io/manga-translator-ui/en/desktop/settings/typesetting-and-rendering)

### 🎨 Full Rich Text

Inline bold, color, stroke and font size, plus ruby annotations and tate-chu-yoko (horizontal-in-vertical). The floating rich-text editor docks to the selected text box, styles can be saved as presets, and rules can apply them automatically while you type.

📖 [Floating rich-text editor](https://hgmzhn.github.io/manga-translator-ui/en/desktop/editor/floating-rich-text) ｜ [Rich-text rules](https://hgmzhn.github.io/manga-translator-ui/en/desktop/rich-text-rules/styles-and-presets)

---

## 🚀 Quick Start

> ⚠️ **Windows users: install the runtime first**. Before the first install, make sure the [Microsoft Visual C++ Redistributable (vc_redist.x64.exe)](https://aka.ms/vs/17/release/vc_redist.x64.exe) is installed; otherwise the app may fail to start (e.g., missing VCRUNTIME140.dll).

| Method | Best for | Notes |
|---|---|---|
| [① Portable package](#method-1-portable-package--recommended-supports-updates) | ⭐ Most users | Bundles Python, one-click install/update, no registry writes |
| [② Packaged release](#method-2-download-the-packaged-release) | Just want to double-click | Extract and run, no incremental updates, no AMD GPU |
| [③ Docker](#method-3-docker-deployment-experimental) | Servers / Web UI | One command, ships with an admin UI |
| [④ From source](#method-4-run-from-source-developers) | Developers | Edit the code, switch branches |
| [⑤ Linux / macOS](#method-5-linuxmacos) | Non-Windows | Shared `uv`-based installer |

### Method 1: Portable Package (⭐ Recommended, supports updates)

> ⚠️ **No Python pre-install required**: the package bundles a portable Python 3.12 and the uv package manager — fully portable, no registry writes
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

3. **Start the program**: double-click `Win-Start.bat`

4. **Uninstall**
   - Portable version: just delete the whole folder
   - Legacy version: see [Uninstall and data cleanup](https://hgmzhn.github.io/manga-translator-ui/en/install/uninstall-and-data-cleanup)

📖 [Portable package walkthrough](https://hgmzhn.github.io/manga-translator-ui/en/install/windows-portable) ｜ [Updates and version switching](https://hgmzhn.github.io/manga-translator-ui/en/install/update-and-version-switching)

### Method 2: Download the Packaged Release

1. **Download the program**
   - Open [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases)
   - Choose a package:
     - **CPU build**: works on all computers
     - **GPU build (NVIDIA)**: requires an NVIDIA GPU with CUDA 13.x support
     - ⚠️ **AMD GPU is not supported in the packaged release**, please use Method 1 instead

2. **Extract and run**: extract the archive to any folder, then double-click `app.exe`

📖 [Release download notes](https://hgmzhn.github.io/manga-translator-ui/en/install/release-download)

### Method 3: Docker Deployment (Experimental)

> 💡 **Note**: the command below is fine for a quick trial. For a real Web UI deployment, mount the persistent paths listed below.

**Quick start**:

```bash
docker run -d --name manga-translator -p 8000:8000 hgmzhn/manga-translator:latest-cpu
```

**Image registries**: this project publishes Docker images to two registries. Use whichever is faster for you.

| Registry | CPU build | GPU build |
|---|---|---|
| Docker Hub (recommended) | `hgmzhn/manga-translator:latest-cpu` | `hgmzhn/manga-translator:latest-gpu` |
| GitHub Container Registry (alternative) | `ghcr.io/hgmzhn/manga-translator:latest-cpu` | `ghcr.io/hgmzhn/manga-translator:latest-gpu` |

**Access URLs** (default port `8000`):

- 🌐 User UI: `http://localhost:8000`
- 🔧 Admin UI: `http://localhost:8000/admin`

**Recommended Web UI persistence**:

- Required directories: `/app/manga_translator/server/data`, `/app/config`, `/app/dict`, `/app/fonts`, `/app/models`
- Optional directories: `/app/logs`, `/app/result`
- Optional file: `/app/.env`. Mount this too if you want server API keys saved from the Web UI to survive container recreation
- `server/data` now also contains `admin_config.json`, `user_resources/`, accounts, sessions, groups, permissions, quotas, API key presets, user configs, and translation-history metadata

📖 [Docker deployment](https://hgmzhn.github.io/manga-translator-ui/en/install/docker) ｜ [Web UI launch and access](https://hgmzhn.github.io/manga-translator-ui/en/web/launch-and-access)

### Method 4: Run from Source (Developers)

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

📖 [Install from source (Windows)](https://hgmzhn.github.io/manga-translator-ui/en/install/source-windows) ｜ [Architecture and code boundaries](https://hgmzhn.github.io/manga-translator-ui/en/developer/architecture-and-code-boundaries)

### Method 5: Linux/macOS

Linux and macOS use the same `uv`-based installer. Apple Silicon uses MPS when available; Linux selects NVIDIA, AMD ROCm, or CPU dependencies.

**Quick install**:

```bash
mkdir -p ~/manga-translator-ui
cd ~/manga-translator-ui

curl -L -O https://raw.githubusercontent.com/hgmzhn/manga-translator-ui/main/Unix-Install-or-Update.sh
chmod +x Unix-Install-or-Update.sh
./Unix-Install-or-Update.sh
```

> At startup the script asks once: `Start installation now? [Y/n]`. After you confirm, it checks for Git, installs Python 3.12 through `uv`, creates a project-local `.venv`, and goes straight to the bilingual Python install/update menu. If the project code is missing, it clones the repository first.

**If you already have the repository**:

```bash
cd manga-translator-ui
chmod +x Unix-*.sh
./Unix-Install-or-Update.sh
```

**Start the app**:

```bash
./Unix-Start.sh
```

**Update code and dependencies**:

```bash
./Unix-Install-or-Update.sh
# Choose [2] Update in the Python menu
```

> ⚠️ **Notes**:
> - Apple Silicon is the priority target
> - Intel Macs can still run, but they will use CPU mode
> - First-time installation downloads about 2 GB of dependencies, so make sure your network is stable

📖 [Linux and macOS installation](https://hgmzhn.github.io/manga-translator-ui/en/install/linux-and-macos)

---

## 📖 Usage Guide

### 🖥️ Qt UI Mode

1. Fill in API information if you want to use online translators → [API key guide](https://hgmzhn.github.io/manga-translator-ui/en/desktop/api-management/api-key-guide)
2. Turn off `Use GPU` if you are using the CPU build
3. Set the output directory
4. Add images
5. Choose a translator
   - Recommended for first use: `OpenAI High Quality` or `Gemini High Quality`
   - If you want a separate Google-official key/model set, `Vertex High Quality` is also available
6. Start translation

📖 [Your first translation, step by step](https://hgmzhn.github.io/manga-translator-ui/en/introduction/first-translation)

### ⌨️ CLI Mode

Best for batch processing and automation scripts.

> ⚠️ **Important**: before using the CLI, activate the virtual environment in the project directory first:
>
> ```bash
> # Windows
> conda activate manga-env
>
> # Linux/macOS
> source .venv/bin/activate
> ```

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

📖 [Command structure and arguments](https://hgmzhn.github.io/manga-translator-ui/en/cli/command-structure)

---

## 📋 Workflows

| Workflow | Purpose |
|---|---|
| Normal translation | Translate images directly, end to end |
| Export translation | Translate first, then export text with the template-configured extension (JSON by default) |
| Export original text | Detect and OCR only, then export the original text for manual translation |
| Import translation and render | Import translated content from TXT or JSON and render again |
| Translate JSON only / Colorize only / Upscale only / Inpaint only / Replace translation | Run a single stage of the pipeline |

📖 [Workflow overview](https://hgmzhn.github.io/manga-translator-ui/en/workflows/normal)

---

## ⚙️ Common Translators

**Online translators (require API keys)**

- **OpenAI** — uses GPT-family models
- **Google Gemini** — uses Gemini-family models
- **Sakura** — optimized for Japanese
- Google Cloud or Vertex-related API keys can also be entered directly in the `Gemini` fields; keep the default official `Base URL`

**High-quality translators (recommended)**

- **OpenAI High Quality** — uses multimodal GPT-4o-class models
- **Gemini High Quality** — uses multimodal Gemini models
- 📸 Uses image context together with text, which usually gives more accurate translations

**AI OCR / colorization / rendering (optional)**

- **AI OCR** (`openai_ocr`, `gemini_ocr`), **AI colorization** (`openai_colorizer`, `gemini_colorizer`), **AI rendering** (`openai_renderer`, `gemini_renderer`)
- In the desktop UI, configure them under `API Management` → `OCR` / `Colorization` / `Render`; the web admin UI exposes matching groups

📖 [Translator selection and languages](https://hgmzhn.github.io/manga-translator-ui/en/desktop/translator/selection-and-languages) ｜ [Every setting explained](https://hgmzhn.github.io/manga-translator-ui/en/desktop/settings/)

---

## 🔍 Need Help?

1. Turn on `Verbose Logging` in the settings
2. Check the debug files under `result/`
3. Adjust detector and OCR related parameters
4. After troubleshooting, clean up old logs and debug folders to keep `result/` from growing too large

📖 [Installation and startup issues](https://hgmzhn.github.io/manga-translator-ui/en/troubleshooting/installation-and-startup) ｜ [Output, JSON and rendering issues](https://hgmzhn.github.io/manga-translator-ui/en/troubleshooting/output-json-and-rendering) ｜ [How to read and share a debug run](https://hgmzhn.github.io/manga-translator-ui/en/debugging/how-to-read-and-share-a-debug-run)

---

## ⭐ Star History

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=hgmzhn/manga-translator-ui&type=Date)](https://star-history.com/#hgmzhn/manga-translator-ui&Date)

</div>

---

## 🙏 Acknowledgements

**Core engine and reference projects**

- [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) — core translation engine
- [dmMaze/BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — text rendering reference
- [charlespfan/mtu-json-gui](https://github.com/charlespfan/mtu-json-gui) — rich text reference

**Models and recognition**

- [bilibili/ailab](https://github.com/bilibili/ailab) — Real-CUGAN super-resolution model
- [the-database/MangaJaNai](https://github.com/the-database/MangaJaNai) — MangaJaNai / IllustrationJaNai super-resolution models
- [lhj5426/YSG](https://github.com/lhj5426/YSG) — model support
- [huyvux3005/manga109-segmentation-bubble](https://huggingface.co/huyvux3005/manga109-segmentation-bubble) — MangaLens Bubble Segmentation model
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — OCR model support
- [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) — MangaOCR model support
- [PaddlePaddle/PaddleOCR-VL-1.5](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) — official PaddleOCR-VL-1.5 model page

And all the contributors and users who support the project ❤️

---

## ❤️ Support the Author

If this project helps you, you are welcome to buy the author a milk tea 🧋

<div align="center">

<table>
<tr>
<td align="center" width="220"><img src="doc/images/mm_reward_qrcode_1765200960689.png" width="180" alt="WeChat support QR code"><br><sub>💚 WeChat Support</sub></td>
<td width="40"></td>
<td align="center" width="220"><img src="doc/images/IMG_20251223_173711.jpg" width="180" alt="Alipay support QR code"><br><sub>💙 Alipay Support</sub></td>
</tr>
</table>

<sub>Thank you for your support ✨</sub>

</div>

---

## 📝 License

The source code of this project is released under the **GPL-3.0** license.

**Model license notice**: this project also supports MangaJaNai / IllustrationJaNai model weights for image super-resolution. Those model weights use the **CC BY-NC 4.0** license and are **for non-commercial use only**. Model source: [MangaJaNai](https://github.com/the-database/MangaJaNai).

---

## ⚠️ Special Notice

This project is provided for technical demonstration, personal study, and communication purposes only. It does not constitute legal, commercial, or compliance advice.
When installing, configuring, calling, or distributing this project and related features, you are responsible for confirming and continuously complying with local laws, platform rules, content source licenses, and third-party service terms.

**Disclaimer and limitation of liability**

- All actions and consequences resulting from use of this project, including but not limited to content processing, publishing, distribution, redistribution, and commercial use, are the sole responsibility of the user.
- You must ensure that your input content, output content, and data sources are legally authorized, and that they are not used in ways that infringe copyright, trademark, privacy, portrait rights, or other lawful rights and interests.
- This project must not be used for any illegal or non-compliant purpose, including but not limited to piracy distribution, unauthorized mass scraping or reposting, bypassing platform restrictions, fraud, defamation, or infringement of lawful rights and interests.
- This project depends on third-party models, APIs, datasets, and libraries, including OCR, translation, and super-resolution related services. Availability, accuracy, stability, pricing, risk control, and compliance requirements are the responsibility of the corresponding providers, and users bear the related risks and costs.
- To the maximum extent permitted by applicable law, the project author and contributors are not liable for any direct or indirect loss arising from the use of or inability to use this project, including but not limited to data loss, business interruption, profit loss, account risk, or third-party claims.
- If you use this project in a team or organizational environment, you are responsible for permission management, logging and auditing, content review, compliance assessment, and establishing the necessary human review process.

Please evaluate the risks carefully before use. Continuing to use this project is deemed as having read, understood, and agreed to the statements above.
