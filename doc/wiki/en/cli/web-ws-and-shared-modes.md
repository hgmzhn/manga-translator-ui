---
title: Web, WS, and Shared Modes
description: Distinguish the purpose, default ports, and internal protocol boundaries of the web/ws/shared service subcommands
pageId: cli.web-ws-and-shared-modes
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Web, WS, and Shared Modes

Use this page when you want to expose translation as a service. Besides `local`, there are three sibling service subcommands: `web` provides a browser UI and HTTP API, `shared` provides a nonce-protected internal executor API, and `ws` acts as a client that connects to an upstream WebSocket scheduler and processes tasks. This page fixes how to start the three modes, their default ports, and their internal protocol boundaries, and explains which endpoints are user/developer interfaces and which must never be exposed directly.

See [Local input and output](./local-input-output.md) for `local` input/output, [Web launch and access](../web/launch-and-access.md) for browser operations of `web`, and the developer pages for the HTTP API contract and internal protocol details.

## Feature boundary {#feature-boundary}

- `web` is the only service for user-facing and developer HTTP clients: one process serves the `GET /` main workspace, the `GET /admin` admin UI, `/static/*` assets, and all JSON/form/streaming APIs. It listens on `0.0.0.0:8000` by default and can be overridden with `MT_WEB_HOST`/`MT_WEB_PORT` or `--host`/`--port`.
- `shared` is an internal shared/API instance: it listens on `127.0.0.1:5003` by default, exposes only three controlled endpoints, protects them with the `X-Nonce` header, and returns pickle-serialized results. Browsers never access it directly.
- `ws` is an internal WebSocket executor: it connects to the upstream `ws://localhost:5000` by default, authenticates with the `x-secret` header, and receives protobuf tasks while reporting status back. The parser declares `--host 127.0.0.1 --port 5003` for it, but the current implementation does not consume these two fields.
- The three modes never share a default port: `web` uses `8000`, the parser default for `shared`/`ws` is `5003`, and the actual connection target of `ws` is the upstream `ws://localhost:5000`. Do not mix up `5000`, `5003`, and `8000`.
- CLI options are fixed Chinese help strings in the source and do not go through i18n; the GPU/ONNX/retry/logging switches shared with desktop settings map to `label_*` keys (see the three-column table below).

## Modes and ports {#modes-and-ports}

| Mode | Default endpoint | Role | Who can access |
| --- | --- | --- | --- |
| `web` | Listens on `0.0.0.0:8000` (overridable via `MT_WEB_HOST`/`MT_WEB_PORT` or `--host`/`--port`) | HTTP API + Web UI | Browser users, HTTP API clients |
| `shared` | Listens on `127.0.0.1:5003` | Internal shared/API instance | Internal clients holding the nonce |
| `ws` | Binds no listen port; parser default `127.0.0.1:5003`; connects to upstream `ws://localhost:5000` | Internal WebSocket executor | Upstream WebSocket scheduler |

```mermaid
flowchart LR
    subgraph User["User side"]
        B["Browser / HTTP client"]
    end
    subgraph Server["web mode (0.0.0.0:8000)"]
        W["Web UI + HTTP API in one process"]
    end
    subgraph Executor["Internal executor protocols"]
        S["shared instance (127.0.0.1:5003)"]
        WS["ws executor"]
        UP["Upstream scheduler ws://localhost:5000"]
    end
    B -->|"HTTP"| W
    W -.->|"Legacy path: dispatch after /register"| S
    S -->|"X-Nonce + pickle"| W
    UP -->|"WebSocket + protobuf"| WS
    WS -->|"GET source_image / PUT translation_mask"| UP
```

Shared-executor dispatch is a legacy path retained in the source: `run_server()` currently forces `args.start_instance=False`, so the `web` mode does not automatically spawn a separate shared instance; `ws` mode only connects to the upstream as a client.

## Terminal operations {#terminal-operations}

### Start web mode {#start-web-mode}

```powershell
uv run --no-sync python -m manga_translator web
```

This is equivalent to `--host 0.0.0.0 --port 8000`. After startup, open `http://127.0.0.1:8000` (or the server's LAN address) in a browser; `0.0.0.0` binds every network interface and is exposed by default. See [Web launch and access](../web/launch-and-access.md) for the full startup steps, Docker, and network notes.

### Start shared mode {#start-shared-mode}

```powershell
uv run --no-sync python -m manga_translator shared --host 127.0.0.1 --port 5003 --nonce <nonce>
```

Replace `<nonce>` with a value agreed with the caller. Once a nonce is set, every `/simple_execute/*` and `/execute/*` request must carry a matching `X-Nonce` header or it returns `401`.

### Start ws mode {#start-ws-mode}

```powershell
uv run --no-sync python -m manga_translator ws --ws-url ws://localhost:5000
```

This repository is missing `manga_translator/server/ws_pb2.py` (the generated protobuf module), so `from ..server import ws_pb2` inside `listen()` raises `ImportError` and `ws` mode cannot actually start; `ws --help` is unaffected. This is a source-level difference, not a verified runtime behavior.

## Parameters and options {#options}

Option defaults come from `manga_translator/args.py`; the `MT_*` environment variables of `web` mode are evaluated at process startup and take precedence over the baseline values in the help text.

### web options {#web-options}

| Option | Type / default | Actual `--help` and parse semantics |
| --- | --- | --- |
| `--host HOST` | string; `MT_WEB_HOST` or `0.0.0.0` | Server host |
| `--port PORT` | integer; `MT_WEB_PORT` or `8000` | Server port |
| `--use-gpu` | flag; true when `MT_USE_GPU` is `true`/`1`/`yes`/`on` | Use GPU |
| `--disable-onnx-gpu` | flag; `MT_DISABLE_ONNX_GPU` uses the same truthy rule | Disable ONNX Runtime GPU |
| `--models-ttl MODELS_TTL` | integer; `MT_MODELS_TTL` or `0` | Seconds to keep models in memory after last use; `0` means forever |
| `--retry-attempts RETRY_ATTEMPTS` | integer; `None` when `MT_RETRY_ATTEMPTS` is unset | Retries on request failure; `-1` unlimited; `None` uses the config sent by the API |
| `-v`, `--verbose` | flag; true when `MT_VERBOSE` is `true`/`1`/`yes` | Show verbose logs |

### shared options {#shared-options}

| Option | Type / default | Actual `--help` and parse semantics |
| --- | --- | --- |
| `--host HOST` | string; `127.0.0.1` | API service host |
| `--port PORT` | integer; `5003` | API service port |
| `--nonce NONCE` | string; `None` | Nonce protecting internal API traffic; clients must send the `X-Nonce` header once set |
| `--models-ttl MODELS_TTL` | integer; `0` | Model TTL in memory (seconds); `0` means forever |
| `--retry-attempts RETRY_ATTEMPTS` | integer; `None` | Retries on translation failure; `-1` unlimited; `None` uses the config sent by the API |
| `-v`, `--verbose` | flag; `False` | Show verbose logs |
| `--use-gpu` | flag; `False` | Use GPU |
| `--disable-onnx-gpu` | flag; `MT_DISABLE_ONNX_GPU` uses the top-level truthy rule | Disable ONNX Runtime GPU |

### ws options {#ws-options}

| Option | Type / default | Actual `--help` and parse semantics |
| --- | --- | --- |
| `--host HOST` | string; `127.0.0.1` | Parser default; the current `MangaTranslatorWS` does not consume it |
| `--port PORT` | integer; `5003` | Parser default; the current `MangaTranslatorWS` does not consume it |
| `--nonce NONCE` | string; `None` | Parser default; unused by the current implementation; the secret comes from the `WS_SECRET` environment variable or the `ws_secret` parameter |
| `--ws-url WS_URL` | string; `ws://localhost:5000` | Upstream WebSocket server URL (the actual connection target) |
| `--models-ttl MODELS_TTL` | integer; `0` | Model TTL in memory (seconds); `0` means forever |
| `--retry-attempts RETRY_ATTEMPTS` | integer; `None` | Retries on translation failure; `-1` unlimited; `None` uses the config sent by the API |
| `-v`, `--verbose` | flag; `False` | Show verbose logs |
| `--use-gpu` | flag; `False` | Use GPU |
| `--disable-onnx-gpu` | flag; `MT_DISABLE_ONNX_GPU` uses the top-level truthy rule | Disable ONNX Runtime GPU |

### UI copy shared with desktop settings {#ui-copy}

CLI options themselves are fixed Chinese strings in the source and do not go through i18n. The desktop “Basic Settings” rows that share the same configuration keys with these service options use `label_*` keys; their actual three-column display values are below (`--models-ttl` and `--retry-attempts` have no dedicated desktop rows):

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `label_use_gpu` | Use GPU | 使用 GPU |
| `label_disable_onnx_gpu` | Disable ONNX GPU Acceleration | 禁用 ONNX GPU 加速 |
| `label_attempts` | Retry Attempts | 重试次数 |
| `label_verbose` | Verbose Logging | 详细日志 |

## Runtime behavior {#runtime-behavior}

### web mode {#web-runtime}

`__main__.py` dispatches `web` to `server.run_server(args)`: it loads `.env` from the application directory, initializes the server configuration and data directories, then starts Uvicorn with `args.host`/`args.port` (`timeout_keep_alive=1800`, 30-second graceful shutdown). An internal nonce is generated at startup (`MT_WEB_NONCE` or `secrets.token_hex(16)`) and printed in the logs. Translation tasks run in-process in a translator thread via `_run_translate_sync`/`_run_translate_batch_sync` in `request_extraction.py`; the source retains the legacy shared-executor registration/dispatch path, but `run_server()` forces `args.start_instance=False`, so no separate shared instance is spawned automatically, and external instances can register via `POST /register` (with `X-Nonce`).

### shared internal protocol {#shared-protocol}

`MangaShare` wraps a `MangaTranslator` and exposes three endpoints with FastAPI:

| Endpoint | Behavior |
| --- | --- |
| `GET /is_locked` | Returns `{"locked": true/false}` |
| `POST /simple_execute/{method_name}` | Synchronous execution; returns a pickle byte stream on success or 4xx/5xx on failure |
| `POST /execute/{method_name}` | Streaming execution returning `application/octet-stream`; each frame is 1-byte status + 4-byte big-endian length + payload |

Every execution endpoint runs these checks in order: nonce validation (once set, `X-Nonce` must match or it returns `401`), method whitelist (only `translate` and `translate_batch`, otherwise `403`), method existence (`404`), and a non-blocking lock (`429` when busy). In the request JSON, images are PNG base64 and the config is `Config.model_dump(mode="json")`; invalid image/config/batch requests return `422`. Streaming frame statuses: `0` result (pickle), `1` progress (UTF-8 status string), `2` error. When a result carries `use_placeholder`, only a 1×1 white placeholder image is transferred to avoid moving large images.

```mermaid
flowchart LR
    C["Internal client"] -->|"POST /execute/translate + X-Nonce"| N{"Nonce matches?"}
    N -->|no| E401["401"]
    N -->|yes| W{"Method in whitelist?"}
    W -->|no| E403["403"]
    W -->|yes| L{"Lock available?"}
    L -->|no| E429["429"]
    L -->|yes| R["Run translate/translate_batch"]
    R --> S["StreamingResponse frames 1/0/2"]
```

### ws internal protocol {#ws-protocol}

`MangaTranslatorWS` extends `MangaTranslator`; `listen()` connects to the `--ws-url` upstream as a client: `websockets.connect(url, extra_headers={'x-secret': secret}, max_size=1_000_000)`. The secret comes from the `ws_secret` parameter or the `WS_SECRET` environment variable (empty string by default). Messages are `ws_pb2.WebSocketMessage` protobufs: the upstream sends `new_task` (task id, `source_image`, `translation_mask`, and translation parameters) and the executor sends back `status` and `finish_task`.

Single-task flow: `pending` → HTTP GET `source_image` to download (on failure send `error-download`) → `downloading` → translate → `preparing` → `saving` (result converted to PNG; in verbose mode also saved as `result/<id>/ws_final.png`) → `uploading` → HTTP PUT `translation_mask` to upload (on failure send `error-upload`) → `finish_task(success, has_translation_mask)`. Status frames are throttled with a 0.2-second throttler, tasks are scheduled with a `PriorityLock`, and images larger than 1200 pixels on either side force `upscale_ratio=1`. On Windows it calls `WSAStartup` and sets the `ProactorEventLoopPolicy` first.

```mermaid
flowchart LR
    T["new_task"] --> P["pending"]
    P --> D{"GET source_image"}
    D -->|"failure"| ED["error-download"]
    D -->|"success"| G["downloading"]
    G --> R["Translate (incl. preparing)"]
    R --> S["saving"]
    S --> U{"PUT translation_mask"}
    U -->|"failure"| EU["error-upload"]
    U -->|"success"| F["finish_task"]
```

## Dependencies and conflicts {#dependencies-and-conflicts}

- Port boundaries: `web` defaults to `8000`, the parser default for `shared`/`ws` is `5003`, and the connection target of `ws` is `ws://localhost:5000`; they never overlap. Startup fails when a port is occupied, which is an environment issue.
- `ws --host/--port/--nonce` exist in the parser, but the current `MangaTranslatorWS` does not consume them; do not infer from the help text that `ws` listens on `5003`.
- This repository is missing `ws_pb2.py`, so `ws` mode cannot start; this is a source-level difference, not a runtime-verification conclusion.
- `shared`/`ws` are internal protocols: never access them directly from a browser and never expose them to the public internet; nonce/secret handling and pickle deserialization carry security risks, and the nonce printed in logs must not be copied into public reports.
- This page never reads or shows real `.env` contents, `WS_SECRET`, nonces, API keys, tokens, usernames, or private paths.

## Related files and formats {#related-files-and-formats}

| File/format | Actual role on this page | Note |
| --- | --- | --- |
| `manga_translator/args.py` | Official options and defaults of the three service subcommands | The standalone parser in `server/args.py` is not the official entry |
| `manga_translator/__main__.py` | Mode dispatch: `web`→`run_server`, `ws`→`MangaTranslatorWS`, `shared`→`MangaShare` | Imports torch before parsing |
| `manga_translator/mode/share.py` | shared instance endpoints, nonce, lock, and pickle frames | `X-Nonce` header, method whitelist |
| `manga_translator/mode/ws.py` | ws executor, upstream connection, protobuf tasks and status | `ws_pb2` module missing |
| `manga_translator/server/main.py` | web startup, nonce, `/register`, `start_instance` | Forces `start_instance=False` |
| `manga_translator/server/instance.py`, `sent_data_internal.py` | shared client calls and frame parsing | PNG base64 images, pickle round-trip |
| `manga_translator/server/request_extraction.py` | In-process translation execution for web | `_run_translate_sync`/`_run_translate_batch_sync` |
| `desktop_qt_ui/locales/en_US.json`, `zh_CN.json`, `data/i18n.generated.json` | Actual bilingual `label_*` values for desktop settings | No real secrets included |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Parameters and defaults | `manga_translator/args.py` | `web`/`ws`/`shared` subparsers, default endpoints, `MT_*` environment variables |
| Mode dispatch | `manga_translator/__main__.py` | Four modes dispatched to `run_server`/`MangaTranslatorWS`/`MangaShare` |
| shared service | `manga_translator/mode/share.py` | `/is_locked`, `/simple_execute/*`, `/execute/*`, nonce, lock, pickle and frame format |
| ws executor | `manga_translator/mode/ws.py` | Upstream `ws_url`, `x-secret`, protobuf tasks/status, download/upload, throttling and lock |
| web service | `manga_translator/server/main.py` | Uvicorn startup, nonce, `/register`, `args.start_instance=False` |
| shared client | `manga_translator/server/instance.py`, `sent_data_internal.py` | End-to-end calls, base64 images, pickle, stream-frame parsing |
| In-process execution | `manga_translator/server/request_extraction.py` | `_run_translate_sync`/`_run_translate_batch_sync` translator thread |
| i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json`, `doc/wiki/data/i18n.generated.json` | Actual bilingual values of keys such as `label_use_gpu` |
| Research baseline | `doc/wiki/research/cli-command-inventory.md`, `phase0-web-user-http.md` | `--help` exit codes and port/protocol boundaries |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract |
| `local/web/ws/shared` vs `--help` | Complete | Statically checked `args.py` and the actual help output in `research/cli-command-inventory.md` |
| Port distinction | Complete | web `0.0.0.0:8000`, shared `127.0.0.1:5003`, and ws upstream `ws://localhost:5000` checked item by item |
| shared/ws internal protocols | Complete | Statically checked `mode/share.py`, `mode/ws.py`, `instance.py`, `sent_data_internal.py` |
| `en_US` / `zh_CN` actual locales | Complete | The three-column table records key and actual display values |
| Sanitized runtime verification | Deferred | No real `.env`, nonce/secret, API key/token, username, or private content read; no server started or screenshots taken |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` plus mirror/source checks before merge |
