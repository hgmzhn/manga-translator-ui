<div align="center">

<img src="doc/images/主页.png" width="360" style="max-width: 100%; height: auto;" alt="Manga Translator UI main window">


**Translate text in all mainstream manga images with one click** — detect → OCR → translate → inpaint → typeset, with a built-in full rich-text editor

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

**💬 QQ Group: 1079089991 (password: `kP9#mB2!vR5*sL1`)**

**Developers** are welcome to submit **PRs** and help improve the project. If you are unsure whether a change fits the project direction, or want to discuss an implementation before coding, open an Issue first. Before submitting, read the **[PR contribution guidelines](https://hgmzhn.github.io/manga-translator-ui/en/community/contributing)**.

If you encounter any problem, please **[open an Issue](https://github.com/hgmzhn/manga-translator-ui/issues)** and include the environment, reproduction steps, logs, or screenshots whenever possible so it can be diagnosed quickly.

If this tool has helped you, you can **[support the author](#support-author)**! Your support is what keeps the project maintained—thank you!

**🔗 Friend Link: [MTU-JSON-GUI](https://github.com/charlespfan/mtu-json-gui)**: A Web-based visual typesetting tool built specifically for [Manga Translator UI](https://github.com/hgmzhn/manga-translator-ui). Beyond text replacement, it includes a geometric layout engine that algorithmically handles alignment, spacing, and perspective issues in manga typesetting.

---

## 📸 Results Preview

<div align="center">

<table>
<tr>
<td align="center"><b>Before Translation</b></td>
<td align="center"><b>After Translation</b></td>
</tr>
<tr>
<td><img src="doc/images/20012.png" width="400" alt="Before translation"></td>
<td><img src="doc/images/0012.png" width="400" alt="After translation"></td>
</tr>
</table>

</div>

### 🖥️ Application Interface

<div align="center">

<table>
<tr>
<td colspan="2" align="center"><b>Visual Editor</b></td>
</tr>
<tr>
<td colspan="2"><img src="doc/images/QQ20260811-044038.png" width="760" alt="Visual editor interface"></td>
</tr>
<tr>
<td colspan="2" align="center"><b>Settings</b></td>
</tr>
<tr>
<td colspan="2"><img src="doc/images/QQ20260811-044012.png" width="760" alt="Settings interface"></td>
</tr>
</table>

</div>

---

## ✨ Core Features

### Translation

- 🔍 **Smart text detection** - Automatically detects text regions in manga pages
- 📝 **Multilingual OCR** - Supports Japanese, Chinese, English, and more
- 🌐 **Multiple translation engines** - OpenAI, Gemini, Vertex, and Sakura, including high-quality modes
- 🎯 **High-quality translation** - Uses GPT-4o and Gemini multimodal translation
- 📚 **Automatic glossary extraction** - Identifies and accumulates proper nouns for consistent translation
- 🎨 **AI colorization / OCR / rendering** - Provides multimodal AI image-processing tools
- 🔑 **API key rotation and cooldown** - Rotates across multiple keys to reduce rate-limit interruptions

### Typesetting and Rich Text

- 🎈 **Smart balloon layout** - Fits text inside the balloon shape, with Smart Balloon, Smart Scaling, and Strict Bounds modes plus centering and balloon-layout options ([Typesetting and rendering](https://hgmzhn.github.io/manga-translator-ui/en/desktop/settings/typesetting-and-rendering))
- ✂️ **Chinese semantic line breaking** - Uses a local HanLP model to choose meaningful line breaks, falls back automatically when unavailable, and can be combined with AI line breaking ([Line-breaking settings](https://hgmzhn.github.io/manga-translator-ui/en/desktop/settings/typesetting-and-rendering))
- 🤖 **AI line breaking** - Optimizes line breaks from context for better readability
- 🖋️ **Automatic horizontal / vertical detection** - Selects the appropriate text direction automatically
- 🎨 **Full rich text** - Supports inline bold, color, stroke, font size, ruby, and tate-chu-yoko, with presets and automatic rules ([Floating rich-text editor](https://hgmzhn.github.io/manga-translator-ui/en/desktop/editor/floating-rich-text) ｜ [Rich-text rules](https://hgmzhn.github.io/manga-translator-ui/en/desktop/rich-text-rules/styles-and-presets))
- 🧩 **Automatic rich-text rules** - Applies styles automatically while text is entered
- 🔤 **Font management** - Supports system fonts and custom fonts from `fonts/`

### Visual Editor

- ✏️ **Region editing** - Move, rotate, and reshape text boxes
- 📐 **Text editing** - Edit translations and adjust size, color, stroke, and spacing per box
- 🖌️ **Mask editing** - Includes brush, eraser, and clone-stamp tools
- 🔍 **Original comparison** - Compare the edited page side by side with the original
- 📏 **Multi-selection layout** - Align text boxes and distribute spacing
- ⏪ **Undo / redo** - Maintains a complete operation history
- ⌨️ **Keyboard shortcuts** - `A/D` navigate images, `1/2/3` switch the Mask/Paint/Clone Stamp tabs, `Q/W/E` choose the three tools on the current tab, `Ctrl+A` selects all regions, `Ctrl+S` saves project data, `Ctrl+Q` exports the image, and `Ctrl+Shift+R` shows/hides the floating rich-text editor
- 🖱️ **Mouse-wheel shortcuts** - Resize text boxes and brushes with modifier keys

### Batch and Automation

- 📦 **Batch processing** - Processes a whole folder in one run
- 🧰 **Batch management** - Matches regions by condition, bulk-edits properties or text, applies rich-text styles, previews matches, and supports backup restoration ([Guide](https://hgmzhn.github.io/manga-translator-ui/en/desktop/batch-management/schemes-crud))
- 📥 **PSD export** - Exports original, repaired, and text layers for further editing
- 🔄 **JSON / TXT import and export** - Imports, exports, and writes text back
- ⌨️ **Command-line mode** - Supports batch jobs and automation scripts
- 🌐 **Web UI** - Includes account and quota management
- 🐳 **Docker deployment** - Supports containerized operation

📖 [Full shortcut reference](https://hgmzhn.github.io/manga-translator-ui/en/desktop/editor/shortcuts) ｜ [Every setting explained](https://hgmzhn.github.io/manga-translator-ui/en/reference/settings-index)

---

## 🚀 Quick Start

> ⚠️ **Windows users: install the runtime first**: [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)

### Windows

#### Portable package (⭐ Recommended, supports updates)

No separate Python installation is required. Download the [portable package](https://github.com/hgmzhn/manga-translator-ui/releases/tag/portable), extract it, run `Win-Install-or-Update.bat`, then start with `Win-Start.bat`. Choose `[2] Update` in the installer menu when updating. 📖 [Portable package walkthrough](https://hgmzhn.github.io/manga-translator-ui/en/install/windows-portable) ｜ [Updates and version switching](https://hgmzhn.github.io/manga-translator-ui/en/install/update-and-version-switching)

#### Download a portable release

Download a CPU, NVIDIA GPU, or experimental AMD build from [GitHub Releases](https://github.com/hgmzhn/manga-translator-ui/releases), extract it, and run `Win-Start.bat`. Python dependencies and model files are already installed; the AMD build requires a supported GPU and driver 26.2.2. 📖 [Release download notes](https://hgmzhn.github.io/manga-translator-ui/en/install/release-download)

#### Run from source (developers)

For users who need to modify the code, follow the [Windows source installation guide](https://hgmzhn.github.io/manga-translator-ui/en/install/source-windows).

### Linux / macOS

Linux and macOS use the same installer:

```bash
mkdir -p ~/manga-translator-ui && cd ~/manga-translator-ui
curl -L -O https://raw.githubusercontent.com/hgmzhn/manga-translator-ui/main/Unix-Install-or-Update.sh
chmod +x Unix-Install-or-Update.sh && ./Unix-Install-or-Update.sh
```

After installation, start with `./Unix-Start.sh`; update code and dependencies with `./Unix-Install-or-Update.sh`.

Apple Silicon uses Metal/MPS; Linux automatically selects NVIDIA, AMD ROCm, or CPU dependencies. 📖 [Linux and macOS installation](https://hgmzhn.github.io/manga-translator-ui/en/install/linux-and-macos)

### Docker Deployment (Experimental)

```bash
docker run -d --name manga-translator -p 8000:8000 --restart unless-stopped -v manga-translator-models:/app/models -v manga-translator-fonts:/app/fonts -v manga-translator-dict:/app/dict -v manga-translator-config:/app/config -v manga-translator-server:/app/manga_translator/server/data -v manga-translator-logs:/app/logs -v manga-translator-result:/app/result hgmzhn/manga-translator:latest-cpu
```

The named volumes persist models, fonts, dictionaries, configuration, account data, logs, and translation results even if the container is removed. Open `http://localhost:8000` after startup; see the deployment guides for GPU support or custom host directories. 📖 [Docker deployment](https://hgmzhn.github.io/manga-translator-ui/en/install/docker) ｜ [Web UI launch and access](https://hgmzhn.github.io/manga-translator-ui/en/web/launch-and-access)

---

## 📖 Usage Guide

### 🖥️ Qt UI Mode

After opening the app, choose the source language, target language, and translator. If you use an online translator, configure its [API key](https://hgmzhn.github.io/manga-translator-ui/en/desktop/api-management/api-key-guide) first. `OpenAI High Quality` and `Gemini High Quality` are recommended for first-time users; choose `Vertex High Quality` if you want separate Google-official key and model settings.

Set the output directory, add images or a folder, then click **Start Translation**. Turn off **Use GPU** when running the CPU build. 📖 [Your first translation, step by step](https://hgmzhn.github.io/manga-translator-ui/en/introduction/first-translation)

### ⌨️ CLI Mode

Best for batch processing and automation scripts.

Run these commands from the project root. After dependencies are prepared with `uv sync` or an installer, no manual virtual-environment activation is required.

```bash
# Local mode (recommended for CLI translation)
uv run --no-sync python -m manga_translator local -i manga.jpg

# Short form (defaults to Local mode)
uv run --no-sync python -m manga_translator -i manga.jpg

# Translate a whole folder
uv run --no-sync python -m manga_translator local -i ./manga_folder/ -o ./output/

# Web server mode (with API and admin UI)
uv run --no-sync python -m manga_translator web --host 127.0.0.1 --port 8000 --use-gpu

# Show all arguments
uv run --no-sync python -m manga_translator --help
```

📖 Need CLI options? [View command structure and arguments](https://hgmzhn.github.io/manga-translator-ui/en/cli/command-structure)

📋 Looking for another workflow? [View the workflow overview](https://hgmzhn.github.io/manga-translator-ui/en/workflows/normal)

⚙️ Want to configure every setting? [View the settings and parameter reference](https://hgmzhn.github.io/manga-translator-ui/en/reference/settings-index)

🔍 Something went wrong? [View troubleshooting](https://hgmzhn.github.io/manga-translator-ui/en/troubleshooting/installation-and-startup)

---

## ⭐ Star History

<div align="center">

<a href="https://www.star-history.com/?repos=hgmzhn%2Fmanga-translator-ui&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=hgmzhn/manga-translator-ui&type=date&theme=dark&legend=top-left&sealed_token=wJmi34JS3-ufiYKvCyzrANr-YuO12VxLmfwOZ7j3MUXhYUg-P8-wb9yOJ4U5AhPutbjbuoMya-DHcGHo6g9x-yUNIQldu--QrADIwtoFHbhaiKamXEvPZg" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=hgmzhn/manga-translator-ui&type=date&legend=top-left&sealed_token=wJmi34JS3-ufiYKvCyzrANr-YuO12VxLmfwOZ7j3MUXhYUg-P8-wb9yOJ4U5AhPutbjbuoMya-DHcGHo6g9x-yUNIQldu--QrADIwtoFHbhaiKamXEvPZg" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=hgmzhn/manga-translator-ui&type=date&legend=top-left&sealed_token=wJmi34JS3-ufiYKvCyzrANr-YuO12VxLmfwOZ7j3MUXhYUg-P8-wb9yOJ4U5AhPutbjbuoMya-DHcGHo6g9x-yUNIQldu--QrADIwtoFHbhaiKamXEvPZg" />
 </picture>
</a>

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

<a id="support-author"></a>

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
