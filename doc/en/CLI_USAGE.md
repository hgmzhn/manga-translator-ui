# CLI Usage Guide

This document explains the current command-line entry points, Web server mode, and CLI-related workflow configuration.

To stay aligned with the current project wording, this English version uses the current `i18n` names wherever the CLI document references desktop features or workflow names. For example:

- `Verbose Logging`
- `Use GPU`
- `Disable ONNX GPU Acceleration`
- `Translation Workflow Mode:`
- `Export Original Text`
- `Import Translation and Render`
- `Translate JSON Only`
- `Export Translation`
- `Colorize Only`
- `Upscale Only`
- `Inpaint Only`
- `Replace Translation`

This English CLI document mirrors the current Chinese source while updating UI names, workflow names, and route descriptions to match the current codebase and `i18n`.

---

## Important Note

Before using the CLI, activate the virtual environment in the project directory first:

```bash
# Windows / Linux / macOS
conda activate manga-env
```

---

## Table of Contents

- [Quick Start](#quick-start)
- [Basic Usage](#basic-usage)
- [Config Files](#config-files)
- [Input and Output](#input-and-output)
- [Common Parameters](#common-parameters)
- [Examples](#examples)
- [Advanced Usage](#advanced-usage)
- [Web Mode](#web-mode---web-server-api--web-ui)
- [Web Mode API Endpoints](#web-mode-api-endpoints)
- [Functional Notes](#functional-notes)
- [Model Memory Management](#model-memory-management)
- [Retry Count Control](#retry-count-control)
- [WS Mode and Shared Mode](#ws-mode-and-shared-mode)
- [CLI Parameter Reference](#cli-parameter-reference)
- [Authentication and Permission System](#authentication-and-permission-system)
- [FAQ](#faq)

---

## Quick Start

### Run modes

The program currently supports four entry modes:

1. **Local mode** (recommended): command-line translation mode, suitable for translating images or folders directly
2. **Web mode**: Web server mode, providing an HTTP REST API and Web UI
3. **WS mode**: WebSocket backend mode
4. **Shared mode**: API backend instance mode

### Local mode

```bash
# Translate a single image (auto-load config)
python -m manga_translator local -i manga.jpg

# Translate an entire folder
python -m manga_translator local -i ./manga_folder/

# Short form (defaults to Local mode)
python -m manga_translator -i manga.jpg
```

That is the basic flow. The program will automatically:

- load `config/config.json`
- use the settings defined there for translation, OCR, rendering, and other stages
- output to the same directory by default, usually with a `-translated` style suffix

---

## Basic Usage

### Command syntax

```bash
# Local mode
python -m manga_translator local -i <input> [options]

# Short form (defaults to Local mode)
python -m manga_translator -i <input> [options]
```

### Required argument

| Argument | Description | Example |
|------|------|------|
| `-i`, `--input` | Input image or folder path | `-i manga.jpg` |

### Optional arguments

| Argument | Description | Default |
|------|------|------|
| `-o`, `--output` | Output directory | Automatic |
| `--config` | Config file path | Auto lookup |
| `-v`, `--verbose` | Enable detailed logging | Off |
| `--overwrite` | Overwrite existing files | Off |
| `--use-gpu` | Use GPU acceleration | From config |
| `--disable-onnx-gpu` | Disable ONNX Runtime GPU acceleration | From config |
| `--format` | Output format (`png` / `jpg` / `webp` / `avif`) | From config |
| `--batch-size` | Batch size | From config |
| `--attempts` | Retry count when translation fails (`-1` = unlimited) | From config |

### Memory-management arguments (`subprocess` mode)

| Argument | Description | Default |
|------|------|------|
| `--subprocess` | Enable subprocess mode for memory management | Off |
| `--memory-limit` | Process memory limit in MB; restart child process when exceeded, `0` = unlimited | `0` |
| `--memory-percent` | System memory usage percent limit; restart child process when exceeded, `0` = unlimited | `0` |
| `--batch-per-restart` | Restart child process after every N images, `0` = unlimited | `0` |

### How subprocess mode works

- The translation job runs inside a separate child process, so memory can actually be released
- When process memory or total system memory passes the configured limit, the child process exits and the parent process starts a new child process to continue
- `psutil` is required for memory monitoring:

```bash
pip install psutil
```

### Important notes

- Command-line arguments override the corresponding values in the config file
- The current top-level entry `python -m manga_translator` does **not** expose older `--resume` or `--concurrent` top-level switches directly
- The documentation in this English version follows the actually available current top-level arguments defined by the current codebase

---

## Config Files

### Automatic loading

CLI mode automatically looks up config files in the following priority order:

1. **`config/config.json`**: user config, highest priority
2. `config/config-example.json`: template config

### Specify a config file explicitly

```bash
python -m manga_translator -i manga.jpg --config my_config.json
```

### Config file contents

The config file contains the translation pipeline settings. For a full example, see:

- `config/config-example.json`

Basic example:

```json
{
  "translator": {
    "translator": "openai_hq",
    "target_lang": "CHS",
    "keep_lang": "none",
    "enable_streaming": true,
    "no_text_lang_skip": false,
    "high_quality_prompt_path": "dict/prompt_example.yaml",
    "max_requests_per_minute": 0
  },
  "detector": {
    "detector": "default",
    "detection_size": 2048,
    "text_threshold": 0.5,
    "box_threshold": 0.5,
    "unclip_ratio": 2.5,
    "use_yolo_obb": true,
    "min_box_area_ratio": 0.0008
  },
  "ocr": {
    "ocr": "48px",
    "use_hybrid_ocr": false,
    "secondary_ocr": "mocr",
    "min_text_length": 0,
    "prob": 0.1
  },
  "inpainter": {
    "inpainter": "lama_large",
    "inpainting_size": 2048,
    "inpainting_precision": "fp32"
  },
  "render": {
    "renderer": "default",
    "alignment": "auto",
    "direction": "auto",
    "font_path": "fonts/Arial-Unicode-Regular.ttf",
    "layout_mode": "balloon_fill",
    "disable_font_border": false,
    "font_size_offset": 0,
    "stroke_width": 0.07,
    "check_br_and_retry": false
  },
  "upscale": {
    "upscaler": "mangajanai",
    "upscale_ratio": null,
    "realcugan_model": "2x-conservative",
    "tile_size": 600
  },
  "colorizer": {
    "colorizer": "none",
    "colorization_size": 2048,
    "denoise_sigma": 30
  },
  "cli": {
    "verbose": false,
    "attempts": 3,
    "ignore_errors": false,
    "use_gpu": false,
    "disable_onnx_gpu": false,
    "context_size": 3,
    "format": "不指定",
    "overwrite": true,
    "skip_no_text": false,
    "save_text": true,
    "load_text": false,
    "template": false,
    "save_quality": 100,
    "batch_size": 3,
    "batch_concurrent": false,
    "generate_and_export": false,
    "colorize_only": false,
    "upscale_only": false,
    "inpaint_only": false,
    "replace_translation": false
  },
  "kernel_size": 3,
  "mask_dilation_offset": 20
}
```

### Config notes

- For the full config structure, use `config/config-example.json` as the source of truth
- For full parameter explanations, see [SETTINGS.md](SETTINGS.md)
- `translator.keep_lang` filters which candidate regions continue through later processing by source language after text-region merging. Non-matching regions stay unchanged and are not inpainted, translated, or rendered
  - Example: set it to `ENG` to keep only English text in English comics
  - Set it to `none` to disable source-language filtering
- `translator.enable_streaming` controls whether `OpenAI`, `Google Gemini`, `OpenAI High Quality`, and `Gemini High Quality` prefer streaming responses
  - Set it to `false` to force standard non-streaming requests
- You do not need to define every field manually
  - You can override only the parts you want to change and leave the rest at defaults

### CLI argument priority

**Command-line arguments > config file**

```bash
# Command-line flags override config values
python -m manga_translator -i manga.jpg -v
```

### Important format note

In current English UI wording, the output format label is `Output Format` and the UI choice is `Not Specified`.

However, some existing config files still store the older Chinese literal value:

- `format: "不指定"`

So if you see that value in old config files, that is normal. It corresponds to the current `Not Specified` UI choice.

---

## Input and Output

### Input types

#### 1. Single image

```bash
python -m manga_translator -i manga.jpg
```

Supported formats include:

- `.png`
- `.jpg`
- `.jpeg`
- `.bmp`
- `.webp`

#### 2. Multiple images

```bash
python -m manga_translator -i page1.jpg page2.jpg page3.jpg
```

#### 3. Folder

```bash
python -m manga_translator -i ./manga_folder/
```

The CLI recursively processes images in subfolders as well.

### Output rules

#### If no output path is specified

```bash
python -m manga_translator -i manga.jpg
```

Output:

`manga-translated.jpg`

in the same directory.

```bash
python -m manga_translator -i ./manga_folder/
```

Output:

`./manga_folder-translated/`

as a new folder.

#### If an output directory is specified

```bash
python -m manga_translator -i manga.jpg -o ./output/
```

Output:

`./output/manga.jpg`

```bash
python -m manga_translator -i ./manga_folder/ -o ./output/
```

Output:

`./output/`

while preserving the original directory structure.

### Important `--output` note

In the current top-level CLI entry, `--output` means:

- **output directory**

It does **not** mean:

- directly specifying a single output filename

---

## Common Parameters

### Verbose logging

```bash
# Show detailed logs and intermediate results
python -m manga_translator -i manga.jpg -v
```

This usually generates debug images under `result/`, for example:

- `bboxes.png`: merged text-box debug image
- `mask_final.png`: final text-erasure mask
- `inpainted.png`: repaired base image

This corresponds to the desktop UI setting:

- `Settings` -> `General` -> `Verbose Logging`

### Overwrite existing files

```bash
python -m manga_translator -i manga.jpg --overwrite
```

### Output format

```bash
# Output as PNG
python -m manga_translator -i manga.jpg --format png

# Output as JPEG
python -m manga_translator -i manga.jpg --format jpg
```

This corresponds to the desktop UI setting:

- `Settings` -> `General` -> `Output Format`

---

## Examples

### Example 1: Translate a single image

```bash
python -m manga_translator -i manga.jpg
```

Result:

`manga-translated.jpg`

### Example 2: Translate a folder into a target directory

```bash
python -m manga_translator -i ./raw/ -o ./translated/
```

Result:

All translated images are saved under `./translated/`.

### Example 3: Use a custom config file

```bash
python -m manga_translator -i manga.jpg --config my_config.json
```

### Example 4: Enable verbose logging

```bash
python -m manga_translator -i manga.jpg -v
```

### Example 5: Batch-translate multiple files

```bash
python -m manga_translator -i page1.jpg page2.jpg page3.jpg -o ./output/
```

### Example 6: Use subprocess mode for memory management

```bash
# Enable subprocess mode only
python -m manga_translator local -i ./manga_folder/ --subprocess

# Restart when process memory exceeds 6 GB
python -m manga_translator local -i ./manga_folder/ --subprocess --memory-limit 6000

# Restart when system memory usage exceeds 80%
python -m manga_translator local -i ./manga_folder/ --subprocess --memory-percent 80

# Restart after every 20 images
python -m manga_translator local -i ./manga_folder/ --subprocess --batch-per-restart 20

# Combined rule: restart after 6 GB or after every 50 images
python -m manga_translator local -i ./manga_folder/ --subprocess --memory-limit 6000 --batch-per-restart 50
```

---

## Advanced Usage

### Batch processing

```bash
# Translate a folder in Local mode
python -m manga_translator -i ./folder/
```

The actual batch size is controlled in the config file:

- `cli.batch_size`

This corresponds to the desktop UI setting:

- `Settings` -> `General` -> `Batch Size`

### Subprocess mode for memory management

Subprocess mode is useful for large batch translation jobs and can manage memory much more reliably.

```bash
# Basic usage: restart when process memory exceeds 6 GB
python -m manga_translator local -i ./manga_folder/ --subprocess --memory-limit 6000

# A more complete example
python -m manga_translator local -i ./manga_folder/ -o ./output/ ^
    --subprocess ^
    --memory-limit 6000 ^
    --verbose ^
    --overwrite
```

If you are using a Unix shell, the same multi-line command can be written with backslashes instead of `^`.

### How subprocess mode works internally

1. The parent process handles task scheduling and progress management
2. The child process performs the actual translation work
3. When process memory or system memory exceeds the configured limit, the child process exits
4. The parent process starts a new child process and continues processing
5. This continues until all files are finished

### Memory limit notes

- `--memory-limit` monitors the translation process itself
- `--memory-percent` monitors total system memory usage across all processes
- You can use both together, and either condition can trigger a restart

---

## Web Mode - Web Server (API + Web UI)

Web mode starts a full Web server that can be opened in the browser and used as a manga-translation service.

```bash
# Start the Web API server
python -m manga_translator web --host 0.0.0.0 --port 8000

# Use GPU
python -m manga_translator web --host 0.0.0.0 --port 8000 --use-gpu

# Set model TTL (unload 300 seconds after the last use)
python -m manga_translator web --models-ttl 300

# Force retry count (ignore API-provided retry config)
python -m manga_translator web --retry-attempts 3
```

### Environment variable setup

#### Automatically set the admin password

On the first Web server startup, you can set the administrator password through an environment variable instead of entering it manually in the interface:

```bash
# Windows (CMD)
set MANGA_TRANSLATOR_ADMIN_PASSWORD=your_password_here
python -m manga_translator web --host 0.0.0.0 --port 8000

# Windows (PowerShell)
$env:MANGA_TRANSLATOR_ADMIN_PASSWORD="your_password_here"
python -m manga_translator web --host 0.0.0.0 --port 8000

# Linux / macOS
export MANGA_TRANSLATOR_ADMIN_PASSWORD=your_password_here
python -m manga_translator web --host 0.0.0.0 --port 8000

# Docker
docker run -e MANGA_TRANSLATOR_ADMIN_PASSWORD=your_password_here ...
```

Notes:

- The password must be at least 6 characters long
- It only takes effect on the first startup when no admin password has been configured yet
- The password is saved automatically to `manga_translator/server/data/admin_config.json`
- Later startups use the saved password and no longer read the environment variable for initial setup
- If you want to change the password later, use the admin panel's password change function
- For a long-running Docker Web UI deployment, also persist `/app/manga_translator/server/data`, `/app/config`, `/app/dict`, `/app/fonts`, and `/app/models`; admin config and user-uploaded resources now live under `server/data`. If you save server API keys from the Web UI, persist `/app/.env` too

### Core features

#### 1. Web user interface

- Browser-based access without installing a desktop client
- Drag-and-drop upload for images and folders
- Batch processing of multiple images
- Real-time progress display with detailed logs
- Preview and download results directly in the browser

#### 2. Admin panel

- Server configuration such as GPU settings, model TTL, and retry count
- User management and access control
- API key policy control, including whether user-provided keys are required
- Parameter visibility control
- Separate administrator login protection

#### 3. Translation configuration

- Translator selection such as `OpenAI`, `Google Gemini`, and `Sakura`
- Target language selection
- Detector configuration including YOLO-assisted options
- OCR engine configuration including hybrid OCR
- Rendering configuration such as font, alignment, and layout mode
- Inpainting configuration
- Upscaling configuration

#### 4. API key management

- Visual API key editing in the Web UI
- Multi-user isolation so different users can use different API keys in parallel
- Persistent storage through browser storage
- Changes take effect immediately without restarting the server
- Concurrency protection for shared access

#### 5. Resource management

- Font management
- Prompt file management
- Temporary-file and result cleanup
- Batch upload and deletion operations

#### 6. REST API

- Full HTTP REST API support
- Remote access over the network
- Built-in task queue management
- Auto-generated Swagger documentation
- Multiple endpoint types for translation, export, import, upscaling, colorization, and more

#### 7. Real-time logs

- Real-time translation log viewing
- Log level filtering such as `INFO`, `WARNING`, and `ERROR`
- Progress tracking across processing stages
- Automatic refresh / polling support

#### 8. Multilingual interface

- Interface translations for Chinese, English, Japanese, Korean, and more
- Dynamic language switching
- Broad multilingual UI coverage

#### 9. Permission control

- User access password support
- Separate administrator permissions
- Capability restrictions such as upload and delete permissions
- Upload size and count limits

### Access URLs

- User UI: `http://127.0.0.1:8000/`
- Admin UI: `http://127.0.0.1:8000/admin`
- API docs: `http://127.0.0.1:8000/docs`
- Raw admin log endpoint: `http://127.0.0.1:8000/admin/logs`

### Common use cases

- Personal browser-based use
- Team collaboration with shared infrastructure and separate API keys
- Remote deployment on a server
- Mobile-device access through the browser
- Backend API integration into other applications
- Automation scripts built on the HTTP API

### Advantages

- Cross-platform browser access
- Multi-user support with API-key isolation
- Strong permission and safety controls
- Real-time logs and progress display
- Full REST API support
- Flexible configuration management
- Multilingual UI support

### Web mode arguments

- `--host`: server host, default `0.0.0.0`
  - If set to `127.0.0.1`, only the local machine can access it
- `--port`: server port, default `8000`
- `--use-gpu`: use GPU acceleration
- `--disable-onnx-gpu`: disable ONNX Runtime GPU acceleration
- `--models-ttl`: how long models stay in memory after the last use, in seconds
  - `0` means keep them forever
- `--retry-attempts`: retry count when translation fails
  - `-1` means unlimited retries
  - `None` means use the request-provided API config
- `-v`, `--verbose`: enable detailed logging

### Current code note

The current CLI implementation also supports several Web-related environment variables:

- `MT_WEB_HOST`
- `MT_WEB_PORT`
- `MT_USE_GPU`
- `MT_DISABLE_ONNX_GPU`
- `MT_MODELS_TTL`
- `MT_RETRY_ATTEMPTS`
- `MT_VERBOSE`

---

## Model Memory Management

The `--models-ttl` argument controls how long models stay loaded in memory, which helps balance memory usage and reload overhead:

```bash
# Keep models in memory forever (default, good for frequent use)
python -m manga_translator web --models-ttl 0

# Unload models 5 minutes after the last use
python -m manga_translator web --models-ttl 300

# Unload models 30 minutes after the last use
python -m manga_translator web --models-ttl 1800
```

Recommendations:

- High-frequency use such as production: set it to `0` to avoid repeated model loading
- Lower-frequency personal server use: `300-1800` seconds can save memory
- Memory-constrained environments: use a shorter time such as `300`

Notes:

- After a model is unloaded, the next request must load it again and that can take a few seconds to tens of seconds
- This argument also applies to `ws` mode and `shared` mode

---

## Retry Count Control

The `--retry-attempts` argument controls retry behavior after translation failures:

```bash
# Not specified: use config.translator.attempts from the API request
python -m manga_translator web

# Force unlimited retries
python -m manga_translator web --retry-attempts -1

# Force a maximum of 3 retries
python -m manga_translator web --retry-attempts 3

# Force no retries
python -m manga_translator web --retry-attempts 0
```

Priority:

1. `--retry-attempts` from the server startup command, if specified
2. `config.translator.attempts` from the API request
3. default behavior

Recommendations:

- Production: use a fixed value such as `3` to avoid wasting resources on endless retries
- Development and testing: leaving it unset can be useful when you want request-side config to control behavior
- Stability-first scenarios: `-1` can be used if you truly want to keep retrying until success

---

## WS Mode and Shared Mode

Both of these modes also support `--models-ttl` and `--retry-attempts`:

```bash
# WebSocket mode
python -m manga_translator ws --host 127.0.0.1 --port 5003 --models-ttl 300 --retry-attempts 3

# Shared mode (API backend instance)
python -m manga_translator shared --host 127.0.0.1 --port 5003 --models-ttl 300 --retry-attempts 3
```

### Arguments

- `--host`: service listen host
- `--port`: service listen port
- `--nonce`: nonce used to secure internal communication
- `--ws-url`: upstream WebSocket server URL, only in `ws` mode
- `--models-ttl`: model lifetime in memory in seconds, `0` means forever
- `--retry-attempts`: retry count when translation fails
  - `-1` means unlimited retries
  - `None` means use API-provided config
- `-v`, `--verbose`: detailed logging
- `--use-gpu`: use GPU
- `--disable-onnx-gpu`: disable ONNX Runtime GPU acceleration

### Typical use cases

- As backend translation instances behind a Web server
- As translation workers serving API calls

---

## CLI Parameter Reference

The `cli` section in the config file contains the following important fields.

### Workflow-related fields mapped to current UI names

- `save_text` -> `Editable Image`
  - Save translation results to JSON so they can be edited later in the editor
- `load_text` -> `Import Translation` / `Import Translation and Render`
  - Load translation content from existing JSON or TXT and render directly
- `template` + `save_text` -> `Export Original Text`
  - Export original text as TXT and JSON for manual translation workflows
- `generate_and_export` -> `Export Translation`
  - Export translated TXT and JSON
- `upscale_only` -> `Upscale Only`
- `colorize_only` -> `Colorize Only`
- `inpaint_only` -> `Inpaint Only`
- `replace_translation` -> `Replace Translation`

### Runtime-related fields

- `use_gpu` -> `Use GPU`
- `disable_onnx_gpu` -> `Disable ONNX GPU Acceleration`
- `attempts` -> `Retry Attempts`
- `batch_size` -> `Batch Size`
- `batch_concurrent` -> `Concurrent Batch Processing`

### Additional notes

- `Export Original Text` is effectively the combination:
  - `template=true`
  - `save_text=true`
- `Editable Image` is not a separate workflow by itself
  - it controls whether editable JSON data is also saved
- `Replace Translation` can be combined with:
  - `render.enable_template_alignment`
  - In the current UI, that option is named `Enable Direct Paste Mode`

> Important: these fields are only effective in CLI mode. In API mode, these settings are ignored automatically:
>
> - workflow behavior is controlled by the API endpoint itself
> - GPU behavior is controlled by server startup flags such as `--use-gpu`
> - retry count prefers server startup `--retry-attempts`, then request-side `config.translator.attempts`

---

## Web Mode API Endpoints

The endpoint list below is based on the original Chinese document and checked against the current route decorators in the codebase.

### Basic and page endpoints

| Endpoint | Method | Description |
|------|------|------|
| `/` | GET | User Web UI homepage |
| `/admin` | GET | Admin Web UI page |
| `/api` | GET | API server information |
| `/docs` | GET | API documentation (Swagger UI) |
| `/translate/queue-size` | POST | Get the current translation queue size |

### Authentication endpoints (`/auth`)

| Endpoint | Method | Description |
|------|------|------|
| `/auth/login` | POST | User login |
| `/auth/logout` | POST | User logout |
| `/auth/register` | POST | User registration, when enabled by the administrator |
| `/auth/change-password` | POST | Change password |
| `/auth/check` | GET | Check session status |
| `/auth/status` | GET | Get authentication system status |
| `/auth/setup` | POST | Initial setup, used to create the first administrator |

### Config management endpoints

| Endpoint | Method | Description |
|------|------|------|
| `/config` | GET | Get config schema and visibility information, supports `mode=user/authenticated/admin` |
| `/config/defaults` | GET | Get server default config |
| `/config/options` | GET | Get available config options such as translators and languages |
| `/translator-config/{translator}` | GET | Get config information for a specific translator |

### Metadata endpoints

| Endpoint | Method | Description |
|------|------|------|
| `/fonts` | GET | Get the available font list |
| `/translators` | GET | Get the available translator list, supports `mode` |
| `/languages` | GET | Get the available target-language list, supports `mode` |
| `/workflows` | GET | Get the available workflow list, supports `mode` |
| `/i18n/languages` | GET | Get the available interface-language list |
| `/i18n/{locale}` | GET | Get translations for a specific UI locale |
| `/announcement` | GET | Get the current announcement |

### User-side config endpoints

| Endpoint | Method | Description |
|------|------|------|
| `/user/settings` | GET | Get user settings, including user-group quota info |
| `/user/access` | GET | Get the current user-access policy |
| `/api-key-policy` | GET | Get the API key usage policy |
| `/env` | GET | Get the API keys visible to the current user |
| `/env` | POST | Save the current user's API keys |
| `/api/config/user` | GET | Get the current user's saved config |
| `/api/config/user` | PUT | Save the current user's config |
| `/api/presets` | GET | Get config presets visible to the current user |
| `/api/presets/{preset_id}/apply` | POST | Apply a preset to the current user |

### Admin endpoints (`/admin`)

| Endpoint | Method | Description |
|------|------|------|
| `/admin/settings` | GET / POST / PUT | Get or update admin settings |
| `/admin/announcement` | PUT | Update the current announcement |
| `/admin/tasks` | GET | Get all active tasks |
| `/admin/tasks/{task_id}/cancel` | POST | Cancel a specific task, supports `force` |
| `/admin/logs` | GET | Get logs with filtering and pagination |
| `/admin/logs/export` | GET | Export logs as a text file |
| `/admin/storage/info` | GET | Get storage usage information |
| `/admin/cleanup/{target}` | POST | Clean a target directory such as `uploads`, `results`, `cache`, or `all` |

### Admin config endpoints (`/api/admin`)

| Endpoint | Method | Description |
|------|------|------|
| `/api/admin/config/server` | GET | Get server config |
| `/api/admin/config/server` | PUT | Update server config |
| `/api/admin/config/backups` | GET | Get the server-config backup list |
| `/api/admin/config/restore` | POST | Restore server config from a backup |
| `/api/admin/presets` | POST | Create a config preset |
| `/api/admin/presets` | GET | Get all config presets |
| `/api/admin/presets/{preset_id}` | GET | Get a specific config preset |
| `/api/admin/presets/{preset_id}` | PUT | Update a specific config preset |
| `/api/admin/presets/{preset_id}` | DELETE | Delete a specific config preset |

### User management endpoints (`/api/admin/users`)

| Endpoint | Method | Description |
|------|------|------|
| `/api/admin/users` | GET | List all users |
| `/api/admin/users` | POST | Create a new user |
| `/api/admin/users/{username}` | GET | Get user information |
| `/api/admin/users/{username}` | PUT | Update user information |
| `/api/admin/users/{username}` | DELETE | Delete a user |
| `/api/admin/users/{username}/permissions` | PUT | Update user permissions |

### User-group management endpoints (`/api/admin/groups`)

| Endpoint | Method | Description |
|------|------|------|
| `/api/admin/groups` | GET | Get all user groups |
| `/api/admin/groups` | POST | Create a new user group |
| `/api/admin/groups/{group_id}` | GET | Get a specific user group |
| `/api/admin/groups/{group_id}` | DELETE | Delete a user group |
| `/api/admin/groups/{group_id}/rename` | PUT | Rename a user group |
| `/api/admin/groups/{group_id}/config` | PUT | Update user-group config |

### Session-management endpoints (`/sessions`)

| Endpoint | Method | Description |
|------|------|------|
| `/sessions/` | GET | List the current user's sessions |
| `/sessions/` | POST | Create a new session |
| `/sessions/{session_token}` | GET | Get session details |
| `/sessions/{session_token}` | DELETE | Delete a session |
| `/sessions/{session_token}/status` | PUT | Update session status |
| `/sessions/access-log` | GET | Get access logs, admin only |
| `/sessions/access-log/unauthorized` | GET | Get unauthorized-access logs, admin only |

### Resource-management endpoints (`/api/resources`)

| Endpoint | Method | Description |
|------|------|------|
| `/api/resources/prompts` | GET | Get the current user's prompt list |
| `/api/resources/prompts` | POST | Upload a prompt file |
| `/api/resources/prompts/{resource_id}` | DELETE | Delete a prompt |
| `/api/resources/prompts/by-name/{filename}` | DELETE | Delete a prompt by filename |
| `/api/resources/fonts` | GET | Get the current user's font list |
| `/api/resources/fonts` | POST | Upload a font file |
| `/api/resources/fonts/{resource_id}` | DELETE | Delete a font |
| `/api/resources/fonts/by-name/{filename}` | DELETE | Delete a font by filename |
| `/api/resources/stats` | GET | Get resource statistics |

### Server-level file endpoints

| Endpoint | Method | Description |
|------|------|------|
| `/upload/font` | POST | Upload a server font, admin only |
| `/fonts/{filename}` | DELETE | Delete a server font, admin only |
| `/upload/prompt` | POST | Upload a server prompt, admin only |
| `/prompts` | GET | Get the server prompt list, admin only |
| `/prompts/{filename}` | GET | Get the content of a specific server prompt, admin only |
| `/prompts/{filename}` | DELETE | Delete a specific server prompt, admin only |

### History endpoints (`/api/history`)

| Endpoint | Method | Description |
|------|------|------|
| `/api/history/downloads/t/{ticket}` | GET / HEAD | Download a file using a short-lived ticket |
| `/api/history` | GET | Get the current user's translation history |
| `/api/history/search` | GET | Search translation history |
| `/api/history/admin/all` | GET | Admin view of all history, supports pagination |
| `/api/history/batch-download-ticket` | POST | Create a short-lived ticket for batch-history download |
| `/api/history/batch-download` | POST | Download multiple sessions directly in one batch |
| `/api/history/{session_token}` | GET | Get session details |
| `/api/history/{session_token}` | DELETE | Delete a translation session |
| `/api/history/{session_token}/download-ticket` | POST | Create a short-lived ticket for a session ZIP download |
| `/api/history/{session_token}/download` | GET | Download the session result ZIP directly |
| `/api/history/{session_token}/file/{filename}` | GET | Get a single file from history |
| `/api/history/{session_token}/file/{filename}/download-ticket` | POST | Create a short-lived ticket for a single history file |

### Quota endpoints (`/api`)

| Endpoint | Method | Description |
|------|------|------|
| `/api/quota/stats` | GET | Get the current user's quota stats |
| `/api/admin/quota/stats` | GET | Get quota stats for all users, admin only |
| `/api/admin/quota/user/{user_id}` | GET | Get quota for a specific user, admin only |
| `/api/admin/quota/reset` | POST | Reset quota, admin only |
| `/api/admin/quota/set-limits` | POST | Set quota limits, admin only |

### Log endpoints (`/api/logs`)

| Endpoint | Method | Description |
|------|------|------|
| `/api/logs` | GET | Get current task logs, supports `task_id` filtering |
| `/api/logs/user` | GET | Get the current user's logs |
| `/api/logs/search` | GET | Search logs |
| `/api/logs/session/{session_token}` | GET | Get logs for a specific session |
| `/api/logs/session/{session_token}/export` | GET | Export logs for a specific session |
| `/api/logs/session/{session_token}/clear` | DELETE | Clear logs for a specific session |
| `/api/logs/admin/system` | GET | Get system logs, admin only |
| `/api/logs/admin/sessions` | GET | Get all session logs, admin only |
| `/api/logs/admin/export` | POST | Export session logs in batch, admin only |
| `/api/logs/admin/statistics` | GET | Get log statistics, admin only |
| `/api/logs/admin/cleanup` | POST | Clean old logs, admin only |

### Translation endpoints (`/translate`)

JSON-body endpoints:

| Endpoint | Method | Description |
|------|------|------|
| `/translate/json` | POST | Translate an image and return JSON |
| `/translate/bytes` | POST | Translate an image and return a custom byte stream |
| `/translate/image` | POST | Translate an image and return the final image |
| `/translate/json/stream` | POST | Streaming translation that returns JSON with progress |
| `/translate/bytes/stream` | POST | Streaming translation that returns bytes with progress |
| `/translate/image/stream` | POST | Streaming translation that returns the image with progress |

Multipart form endpoints:

| Endpoint | Method | Description |
|------|------|------|
| `/translate/with-form/json` | POST | Translate an image and return JSON |
| `/translate/with-form/bytes` | POST | Translate an image and return byte format |
| `/translate/with-form/image` | POST | Translate an image and return the rendered image |
| `/translate/with-form/json/stream` | POST | Streaming translation that returns JSON with progress |
| `/translate/with-form/bytes/stream` | POST | Streaming translation that returns byte format with progress |
| `/translate/with-form/image/stream` | POST | Streaming translation that returns the image, recommended for scripts |
| `/translate/with-form/image/stream/web` | POST | Streaming translation that returns the image, optimized for the Web frontend |

Batch translation endpoints:

| Endpoint | Method | Description |
|------|------|------|
| `/translate/batch/json` | POST | Batch translation, returns a JSON array |
| `/translate/batch/images` | POST | Batch translation, returns a ZIP archive |

> Important: the batch endpoints use JSON requests, and images must be sent as base64 strings rather than `multipart/form-data`.

Batch translation example:

```python
import base64
import json
import requests

with open("image1.jpg", "rb") as f:
    img1_b64 = base64.b64encode(f.read()).decode("utf-8")
with open("image2.jpg", "rb") as f:
    img2_b64 = base64.b64encode(f.read()).decode("utf-8")

data = {
    "images": [
        f"data:image/jpeg;base64,{img1_b64}",
        f"data:image/jpeg;base64,{img2_b64}"
    ],
    "config": {},
    "batch_size": 2
}

response = requests.post(
    "http://127.0.0.1:8000/translate/batch/json",
    json=data,
    timeout=600
)

if response.status_code == 200:
    results = response.json()
    print(f"Translated {len(results)} images successfully")
```

Export endpoints:

| Endpoint | Method | Description |
|------|------|------|
| `/translate/export/original` | POST | Export original text, returns ZIP with JSON + TXT |
| `/translate/export/original/stream` | POST | Streaming export of original text with progress |
| `/translate/export/translated` | POST | Export translated text, returns ZIP with JSON + TXT |
| `/translate/export/translated/stream` | POST | Streaming export of translated text with progress |

Processing endpoints:

| Endpoint | Method | Description |
|------|------|------|
| `/translate/upscale` | POST | Upscale only, returns a higher-resolution image |
| `/translate/upscale/stream` | POST | Streaming upscale with progress |
| `/translate/colorize` | POST | Colorize only, returns a colorized image |
| `/translate/colorize/stream` | POST | Streaming colorization with progress |
| `/translate/inpaint` | POST | Inpaint only, detects text and outputs a repaired image |
| `/translate/inpaint/stream` | POST | Streaming inpaint-only mode with progress |

Import endpoints:

| Endpoint | Method | Description |
|------|------|------|
| `/translate/import/json` | POST | Import JSON + image and return the rendered image |
| `/translate/import/json/stream` | POST | Streaming JSON import and render with progress |
| `/translate/import/txt` | POST | Import TXT + JSON + image and return the rendered image |
| `/translate/import/txt/stream` | POST | Streaming TXT import and render with progress |

Other endpoints:

| Endpoint | Method | Description |
|------|------|------|
| `/translate/complete` | POST | Translate an image and return a multipart result containing JSON + image |

---

## Functional Notes

All of the endpoints above already have their own built-in workflow behavior, so you do not need to activate the workflow through `cli` flags first.

> Important: API endpoints ignore `cli` workflow settings in config, such as `load_text`, `template`, `generate_and_export`, and related flags. Those `cli` settings are only for command-line mode.

### Translation endpoints

#### Translate and return an image

This is the full translation flow and returns the final rendered image.

Flow:

```text
Input image -> text detection -> OCR -> machine translation -> image inpainting -> text rendering -> output image
```

API endpoints:

```python
POST /translate/image
POST /translate/image/stream
POST /translate/with-form/image
POST /translate/with-form/image/stream
```

#### Translate and return JSON

This still runs the full text pipeline, but skips image rendering and returns the translation data directly, so it is faster.

Flow:

```text
Input image -> text detection -> OCR -> machine translation -> output JSON
```

Advantages:

- skips inpainting and final rendering, so it is faster
- suitable when you only need the translated text
- can be followed later by an import endpoint for rendering

API endpoints:

```python
POST /translate/json
POST /translate/json/stream
POST /translate/with-form/json
POST /translate/with-form/json/stream
```

### Export endpoints

#### Export original text

This runs only detection and OCR, without translation, and is used to extract the source text.

Flow:

```text
Input image -> text detection -> OCR -> generate ZIP (JSON + TXT)
```

Returned files:

- `translation.json`: includes text-box positions and source text
- `original.txt`: plain-text source text, usually one text box per entry

Use cases:

- manual translation
- source-text proofreading
- batch text extraction

API endpoints:

```python
POST /translate/export/original
POST /translate/export/original/stream
```

Example:

```python
import requests

with open("manga.jpg", "rb") as f:
    files = {"image": f}
    response = requests.post("http://localhost:8000/translate/export/original", files=files)
    with open("original_export.zip", "wb") as out:
        out.write(response.content)
```

#### Export translated text

This runs the full translation flow and exports JSON and TXT files together.

Flow:

```text
Input image -> full translation flow -> generate ZIP (JSON + TXT)
```

Returned files:

- `translation.json`: includes source text, translated text, and positional data
- `translated.txt`: plain-text translated output

Use cases:

- saving translation data for later editing
- exporting translated text
- re-rendering later with different rendering settings

API endpoints:

```python
POST /translate/export/translated
POST /translate/export/translated/stream
```

Example:

```python
import requests

with open("manga.jpg", "rb") as f:
    files = {"image": f}
    response = requests.post("http://localhost:8000/translate/export/translated", files=files)
    with open("translated_export.zip", "wb") as out:
        out.write(response.content)
```

### Import endpoints

#### Import JSON

This loads translation data from a JSON file, skips detection, OCR, and translation, and renders directly.

Flow:

```text
Input image + JSON file -> load text boxes and translations from JSON -> image inpainting -> text rendering -> output image
```

Use cases:

- re-render after manually editing the translation in JSON
- re-render after changing fonts or rendering parameters
- compare different translation versions on the same page

API endpoints:

```python
POST /translate/import/json
POST /translate/import/json/stream
```

#### Import TXT

This imports translation content from a TXT file and supports template parsing plus fuzzy matching.

Flow:

```text
Input image + TXT + JSON -> merge TXT into JSON -> image inpainting -> text rendering -> output image
```

Use cases:

- importing manual translations
- importing results from an external translation tool
- batch-importing translated text

API endpoints:

```python
POST /translate/import/txt
POST /translate/import/txt/stream
```

### Processing endpoints

#### Upscale only

This performs image super-resolution only and does not translate.

Flow:

```text
Input image -> super-resolution processing -> output higher-resolution image
```

Use cases:

- improving image quality
- enlarging images
- image enhancement

API endpoints:

```python
POST /translate/upscale
POST /translate/upscale/stream
```

Example:

```python
import json
import requests

with open("manga.jpg", "rb") as f:
    files = {"image": f}
    data = {"config": json.dumps({"upscale": {"upscaler": "waifu2x", "upscale_ratio": 2}})}
    response = requests.post("http://localhost:8000/translate/upscale", files=files, data=data)
    with open("upscaled.png", "wb") as out:
        out.write(response.content)
```

#### Colorize only

This performs colorization only and does not translate.

Flow:

```text
Input black-and-white image -> AI colorization -> output color image
```

Use cases:

- colorizing black-and-white manga
- colorizing monochrome images

API endpoints:

```python
POST /translate/colorize
POST /translate/colorize/stream
```

Example:

```python
import json
import requests

with open("manga.jpg", "rb") as f:
    files = {"image": f}
    data = {"config": json.dumps({"colorizer": {"colorizer": "mc2"}})}
    response = requests.post("http://localhost:8000/translate/colorize", files=files, data=data)
    with open("colorized.png", "wb") as out:
        out.write(response.content)
```

### TXT import endpoint notes

The `/translate/import/txt` endpoint uses the same import logic as the UI and supports:

1. template-based parsing
2. fuzzy matching against source text, even when there are small differences
3. custom template files

Parameters:

- `image`: original image file
- `txt_file`: translated TXT file
- `json_file`: JSON file that includes text-box positions and source text
- `config`: config JSON string, optional
- `template`: template file, optional; if omitted, the default template is used

Default template format:

```text
Original: <original>
Translation: <translated>
```

TXT format example:

```text
Original: こんにちは
Translation: Hello

Original: ありがとう
Translation: Thank you
```

Or a simple format, one translation per line in order:

```text
Hello
Thank you
```

### Manual translation workflow example

```python
import requests

# Step 1: export original text
with open("manga.jpg", "rb") as f:
    files = {"image": f}
    response = requests.post(
        "http://localhost:8000/translate/export/original",
        files=files
    )
    with open("export.zip", "wb") as out:
        out.write(response.content)

# Step 2: unzip export.zip to get translation.json and original.txt

# Step 3: manually translate original.txt and save it as translated.txt

# Step 4: import the translation and render
with open("manga.jpg", "rb") as img, \
     open("translated.txt", "rb") as txt, \
     open("translation.json", "rb") as json_file:
    files = {
        "image": img,
        "txt_file": txt,
        "json_file": json_file
    }
    response = requests.post(
        "http://localhost:8000/translate/import/txt",
        files=files
    )
    with open("result.png", "wb") as out:
        out.write(response.content)
```

Import logic notes:

1. The API uses the same `safe_update_large_json_from_text` logic as the UI
2. It matches text boxes through the original `text` field
3. Fuzzy matching is supported after normalization
4. The `translation` field is updated in the JSON

### History downloads

The current recommended way to download history files is by requesting a short-lived download `ticket`. This works well for both browsers and download managers, and avoids exposing the session token directly in the URL.

Recommended flow:

```python
import requests

headers = {"X-Session-Token": token}

# 1. Request a short-lived download ticket
ticket_response = requests.post(
    f"http://localhost:8000/api/history/{session_token}/download-ticket",
    headers=headers
)
ticket = ticket_response.json()

# 2. Download from the returned temporary URL
download_url = f"http://localhost:8000{ticket['url']}"
response = requests.get(download_url)

with open("history.zip", "wb") as f:
    f.write(response.content)
```

Notes:

- `POST /api/history/{session_token}/download-ticket` requires the `X-Session-Token` header
- The returned ticket URL is temporary and is suitable for direct browser downloads or download managers
- If you are calling from a script directly, you can still use authenticated endpoints such as `/api/history/{session_token}/download` and `/api/history/batch-download`

### Supported workflow names

- `normal`: normal translation, default
- `export_original`: export original text, detection + OCR only, generates JSON + TXT
- `save_json`: save JSON, full translation plus JSON + TXT output
- `load_text`: import translation and render
- `upscale_only`: upscale only
- `colorize_only`: colorize only

### Generated file locations

- JSON file: `manga_translator_work/json/<image_name>_translations.json`
- Editor base image: `manga_translator_work/editor_base/<image_name>.<original_ext>`
- Original TXT: `manga_translator_work/originals/<image_name>_original.txt`
- Translated TXT: `manga_translator_work/translations/<image_name>_translated.txt`
- Inpainted image: `manga_translator_work/inpainted/<image_name>_inpainted.<original_ext>`
- Final translated image: `manga_translator_work/result/<image_name>.png` when `--save-to-source-dir` is enabled

### Workflow notes

1. `export_original`
   - generates a JSON file that contains source text and text-box information
   - generates a TXT file with plain source text
   - is suitable for manual translation

2. `save_json`
   - generates a JSON file that contains translations and text-box information
   - generates a TXT file with plain translated text
   - is suitable for later editing or re-rendering

3. `load_text`
   - loads translations from JSON
   - re-renders the image
   - is suitable after manual-translation workflows

### Streaming response format

```text
[1-byte status code][4-byte data length][N-byte data]

Status codes:
- 0: result payload such as image data
- 1: progress update
- 2: error message
- 3: queue position
- 4: waiting for a translator instance
```

### Example API usage

```python
import io
import json
import requests

# Method 1: normal translation
with open("manga.jpg", "rb") as f:
    files = {"image": f}
    data = {"config": "{}"}
    response = requests.post(
        "http://localhost:8000/translate/with-form/image",
        files=files,
        data=data
    )
    with open("result.png", "wb") as out:
        out.write(response.content)

# Method 2: translate and return JSON
with open("manga.jpg", "rb") as f:
    files = {"image": f}
    data = {"config": "{}"}
    response = requests.post(
        "http://localhost:8000/translate/with-form/json",
        files=files,
        data=data
    )
    result = response.json()
    print(f"Success: {result['success']}")
    print(f"Text region count: {len(result['text_regions'])}")
    for region in result["text_regions"]:
        print(f"Original: {region['text']}")
        print(f"Translation: {region['translation']}")

# Method 3: export original text
with open("manga.jpg", "rb") as f:
    files = {"image": f}
    response = requests.post(
        "http://localhost:8000/translate/export/original",
        files=files
    )
    with open("original_export.zip", "wb") as out:
        out.write(response.content)

# Method 4: upscale only
with open("manga.jpg", "rb") as f:
    files = {"image": f}
    data = {
        "config": json.dumps({"upscale": {"upscaler": "waifu2x", "upscale_ratio": 2}})
    }
    response = requests.post(
        "http://localhost:8000/translate/upscale",
        files=files,
        data=data
    )
    with open("upscaled.png", "wb") as out:
        out.write(response.content)

# Method 5: streaming translation with progress
with open("manga.jpg", "rb") as f:
    files = {"image": f}
    data = {"config": "{}"}
    response = requests.post(
        "http://localhost:8000/translate/with-form/image/stream",
        files=files,
        data=data,
        stream=True
    )

    buffer = io.BytesIO(response.content)
    while True:
        status_byte = buffer.read(1)
        if not status_byte:
            break
        status = int.from_bytes(status_byte, "big")
        size = int.from_bytes(buffer.read(4), "big")
        payload = buffer.read(size)

        if status == 0:
            with open("result.png", "wb") as out:
                out.write(payload)
        elif status == 1:
            print(f"Progress: {payload.decode('utf-8')}")
        elif status == 2:
            print(f"Error: {payload.decode('utf-8')}")
```

Use cases:

- serving an HTTP API
- integration into other applications
- remote translation services
- task-queue based workflows
- load-balanced or multi-worker setups

---

## Authentication and Permission System

Web mode supports a full user authentication and permission-management system.

### Initial setup

When starting the server for the first time, you need to create an administrator account:

```bash
# Method 1: set through environment variable
set MANGA_TRANSLATOR_ADMIN_PASSWORD=your_password_here
python -m manga_translator web

# Method 2: set through the Web UI
# Visit http://127.0.0.1:8000/admin for initial setup

# Method 3: set through the API
curl -X POST http://127.0.0.1:8000/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'
```

### User login

```python
import requests

response = requests.post("http://localhost:8000/auth/login", json={
    "username": "your_username",
    "password": "your_password"
})
data = response.json()
token = data["token"]

headers = {"X-Session-Token": token}
response = requests.get("http://localhost:8000/api/history", headers=headers)
```

### User groups and permissions

The system supports permission management based on user groups:

- `admin`: administrator group, full permissions
- `default`: default user group
- `guest`: guest group with restricted permissions

Permission types include:

- `allowed_translators`: whitelist of translators a user can use
- `denied_translators`: blacklist of translators a user cannot use
- `allowed_workflows`: workflows a user is allowed to use
- `allowed_parameters`: parameters a user is allowed to adjust
- `max_concurrent_tasks`: maximum concurrent task count
- `daily_quota`: daily translation quota, `-1` means unlimited
- `can_upload_files`: whether uploads are allowed
- `can_delete_files`: whether deleting files is allowed

### Quota management

```python
import requests

response = requests.get("http://localhost:8000/api/quota/stats", headers=headers)
quota = response.json()
print(f"Used today: {quota['used_today']}/{quota['daily_limit']}")
```

### History management

```python
import requests

response = requests.get("http://localhost:8000/api/history", headers=headers)
history = response.json()

ticket_response = requests.post(
    f"http://localhost:8000/api/history/{session_token}/download-ticket",
    headers=headers
)
ticket = ticket_response.json()

response = requests.get(f"http://localhost:8000{ticket['url']}")
with open("history.zip", "wb") as f:
    f.write(response.content)
```

---

## FAQ

### Q: How do I view all available arguments?

```bash
python -m manga_translator --help
```

### Q: Where is the config file?

Default location:

`config/config.json`

If it does not exist, the program falls back to:

`config/config-example.json`

### Q: How do I change the translator?

Edit `config/config.json`:

```json
{
  "translator": {
    "translator": "openai_hq",
    "target_lang": "CHS"
  }
}
```

You can also change it in the Qt desktop UI.

### Q: How do I force CPU mode?

Edit the config file:

```json
{
  "cli": {
    "use_gpu": false
  }
}
```

### Q: What can I do if translation is slow?

1. Enable GPU by setting `cli.use_gpu: true`
2. Lower detection size, for example `detector.detection_size: 1536`
3. Increase batch size, for example `cli.batch_size: 3`

---

Related documents:

- [Installation Guide](INSTALLATION.md)
- [Usage Guide](USAGE.md)
- [API Configuration](API_CONFIG.md)
- [Settings Reference](SETTINGS.md)
- [README_EN](../../README_EN.md)
