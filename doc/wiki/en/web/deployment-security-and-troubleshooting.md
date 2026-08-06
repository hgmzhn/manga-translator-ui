---
title: Web Deployment, Security, and Troubleshooting
description: Deploy the web service locally, on a LAN, or with Docker; understand session and permission security boundaries; and resolve common startup, login, and access issues
pageId: web.deployment-security-and-troubleshooting
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Web Deployment, Security, and Troubleshooting

Use this page when you need to deploy the web interface locally, on a LAN, or with Docker; confirm the session-token, rate-limit, and permission boundaries; or run into problems such as “port already in use”, “LAN clients cannot connect”, or “login is rate limited”. This page covers web deployment, security boundaries, and troubleshooting only. Account, permission, and API-key operations live in [Accounts, permissions, and API keys](./accounts-permissions-and-api-keys.md); login and session UI flows live in [Login, language, and session](./login-language-and-session.md); the admin console is covered in [Administrator interface](./administrator-interface.md). The complete HTTP routing, status-code, and port contracts are developer documentation; see [Web server ports and deployment](../developer/web-server-ports-and-deployment.md) and [Authentication and errors](../developer/http-api/authentication-and-errors.md).

## Feature boundary

- The web service serves the user interface (`GET /`), the admin interface (`GET /admin`), the login page (`/static/login.html`), and the developer HTTP API together. This page documents user-facing deployment, security boundaries, and troubleshooting only and does not repeat the developer API contract.
- The default listen address is `0.0.0.0:8000`, overridable with `--host`/`--port` or the `MT_WEB_HOST`/`MT_WEB_PORT` environment variables. `0.0.0.0` means listening on all IPv4 interfaces; it is not a browser access address.
- Docker Compose ships two services: the CPU image maps `8000:8000` and the GPU image maps `8001:8000`.
- Sessions, permissions, rate limits, and audit logging are enforced server-side; hiding a control in the browser or deleting the front-end token does not replace server-side checks.
- This page never records real passwords, tokens, API keys, usernames, or private paths, and it does not show `.env` plaintext.

## Deployment methods {#deploy-methods}

### Run locally or on a LAN

Run `uv run python -m manga_translator web` from the project root (in an installed environment you can also run `python -m manga_translator web`). It listens on `0.0.0.0:8000` by default; use `--host 127.0.0.1` when only local access is needed.

| Parameter | Default | Notes |
| --- | --- | --- |
| `--host` / `MT_WEB_HOST` | `0.0.0.0` | Listen address; `127.0.0.1` is local-only, `0.0.0.0` accepts LAN clients |
| `--port` / `MT_WEB_PORT` | `8000` | Listen port |
| `--use-gpu` / `MT_USE_GPU` | Off | Enable GPU acceleration |
| `--models-ttl` / `MT_MODELS_TTL` | `0` | Seconds to keep models in memory; `0` keeps them forever; the Compose template sets `300` |
| `--retry-attempts` / `MT_RETRY_ATTEMPTS` | `None` when unset (uses API-provided config); explicit `-1` means infinite retries | Translation failure retry count |
| `-v` / `MT_VERBOSE` | Off | Verbose logging |

Browser access addresses: `http://127.0.0.1:8000` on the local machine; LAN clients must use the server’s actual LAN IP, e.g. `http://192.168.x.x:8000`, and the host firewall must allow the port. Whether the service is reachable from outside depends on the firewall, port mapping, and network environment; it cannot be asserted from the listen address alone.

### Docker CPU and GPU

`packaging/docker-compose.yml` defines two services:

| Service | Image | Port mapping | GPU | Memory limits (template example) |
| --- | --- | --- | --- | --- |
| `manga-translator-cpu` | `manga-translator:cpu` | `8000:8000` | Off | limit 8G / reserve 2G |
| `manga-translator-gpu` | `manga-translator:gpu` | `8001:8000` | On | limit 16G / reserve 4G |

The in-container service always listens on `8000`; the host entry points are `8000` (CPU) and `8001` (GPU). Do not confuse the GPU service with the container-default port. Compose sets the first-start admin password through `MANGA_TRANSLATOR_ADMIN_PASSWORD` (the template contains an example placeholder value that must be changed in production) and passes `MT_USE_GPU`, `MT_MODELS_TTL`, `MT_RETRY_ATTEMPTS`, and `MT_VERBOSE`.

Compose mounts `./data/fonts`, `./data/dict`, `./data/result`, `./data/models`, `./data/logs`, `./data/server`, and `./data/config` as volumes. To keep server API keys saved from the web admin UI across container rebuilds, create an empty `./data/app.env` file and uncomment the `.env` volume mount. The entry script `packaging/docker-entrypoint.sh` restores defaults from the built-in `default_config`, `default_fonts`, `default_dict`, and `default_server_data` when a mounted volume is empty, so emptying a volume returns to the default state instead of failing.

```mermaid
flowchart LR
    A["Start the web service"] --> B{"Environment?"}
    B -->|"Local / LAN"| C["python -m manga_translator web<br/>--host 0.0.0.0 --port 8000"]
    B -->|"Docker CPU"| D["manga-translator-cpu<br/>host 8000 -> container 8000"]
    B -->|"Docker GPU"| E["manga-translator-gpu<br/>host 8001 -> container 8000"]
    C --> F["Browser: http://localhost:8000"]
    D --> F
    E --> G["Browser: http://localhost:8001"]
```

The container health check requests `http://localhost:8000/` every 30 seconds with a 60-second start period and up to 3 retries; brief health-check failures during first startup, while services initialize or models load, are normal.

## Security boundary {#security-boundary}

### Session tokens and authentication

After a successful login the server creates a session: the token is generated with `secrets.token_urlsafe(32)`, sessions are recorded in `manga_translator/server/data/sessions.json` (when persistence is enabled), and sessions expire automatically after 60 minutes of inactivity. The browser stores the token in `localStorage.session_token` and sends it via the `X-Session-Token` request header; when `/auth/check` fails or a business request returns `401`, the front end clears the local token and redirects to the login page.

The session security service (`session_security_service.py`) adds ownership and anti-enumeration protections:

- Ownership tokens use UUID v4 (128-bit random); tokens with an invalid format are rejected immediately.
- More than 10 failed access attempts for the same user within 5 minutes trigger rate limiting, preventing token-enumeration attacks.
- Regular users can only access their own sessions; admins can view all sessions.
- Every denial is written to an access-attempt log for audit queries.

```mermaid
flowchart LR
    A["Browser reads localStorage.session_token"] --> B["GET /auth/check"]
    B -->|"missing / invalid / expired"| C["Clear local token<br/>redirect to /static/login.html"]
    B -->|"valid"| D["Enter main workspace"]
    D --> E["Business request carries X-Session-Token"]
    E --> F{"require_auth / require_admin"}
    F -->|"401 session invalid"| C
    F -->|"403 permission denied"| G["Reject with no-permission message"]
    F -->|"429 concurrent or quota limit"| H["Reject with rate-limit message"]
```

### Rate limits and quotas

| Scenario | Rate limit (source) | When exceeded |
| --- | --- | --- |
| Login `/auth/login` | 15 per IP / 10 min; 8 per username / 10 min | `429` + `Retry-After` |
| Register `/auth/register` | 5 per IP / 10 min | `429` + `Retry-After` |
| Legacy password gate `/user/login` | 10 per IP / 10 min | `429` + `Retry-After` |
| Concurrent tasks / daily quota | Effective concurrent and daily limits per user or user group | `429` |

### Permission boundaries

- `require_auth` verifies the token, refreshes activity time, and rejects missing, expired, or deactivated accounts (`401`).
- `require_admin` additionally requires `role == 'admin'`; non-admin access to admin endpoints returns `403`.
- Translation endpoints also validate translator, OCR, colorizer, and renderer permissions and filter user-submitted parameters server-side; unauthorized parameters are silently dropped.
- Download tickets are short-lived tokens (default 5 minutes). `GET|HEAD /api/history/downloads/t/{ticket}` does not read the session header and depends only on the ticket; invalid or expired tickets return `404`.
- The CORS source configuration is `allow_origins=["*"]` with `allow_credentials=True`. This is server configuration and does not mean the browser will allow every origin/credential combination; tighten origins behind a reverse proxy for public deployments and verify with a browser preflight.

### Credentials and sensitive data

- With no account on first start, the login page enters “initial setup”: it creates the first admin account (username at least 2 characters, password at least 6 characters). Accounts flagged `must_change_password` are forced to change the password after login.
- Registration is controlled by the admin “Allow user registration” toggle and is disabled by default; registration requests return `403` when disabled.
- The admin “Server default API keys” section maps to `.env`; `/env` and `/env/effective` never return server key plaintext, and the front end only shows “saved”-style statuses.
- The “API Keys (.env)” tab is hidden by default; whether it is shown and editable depends on login state and the permission policy. User input is temporarily kept in `localStorage.user_env_vars`.
- Before deploying or sharing troubleshooting details, remove logs, error messages, tickets, tokens, `.env` content, private paths, and user images. The admin password in the Docker Compose template is an example placeholder and must be replaced in production.

## Common problems {#troubleshooting}

| Symptom | Common cause | Fix |
| --- | --- | --- |
| Startup reports “port already in use / address already in use” | `8000` is occupied by another process | Use `--port 8001` or set `MT_WEB_PORT`; on Windows locate the process with `netstat -ano` |
| LAN devices cannot connect | The listen address is `127.0.0.1`, the firewall blocks the port, or the wrong IP is used | Confirm `--host 0.0.0.0`, allow the port in the firewall, and use the server’s actual LAN IP |
| The Docker GPU service is unreachable | The container-default port `8000` was used | The host entry point is `8001`, matching the Compose mapping `8001:8000` |
| Repeated login failures show “too many attempts” | Login rate limiting was triggered | Wait for the time indicated by `Retry-After`; never record real passwords in documentation or public logs |
| The page redirects back to the login page | Session expired (60 minutes of inactivity), token invalid, or local storage cleared | Log in again; a missing token after clearing site data is expected |
| The admin UI or an operation returns `403` | The current account is not an admin or lacks the permission | Log in with an admin account or have an admin grant permissions via users/groups |
| A batch task returns `499` | The task was cancelled or cancellation was detected | Restart the task; cancellation is a user action, not a server crash |
| A request returns `422` | The request body failed FastAPI validation | Check field types and required fields; the response includes `detail` and the request body string |
| The Docker health check is red | The service is still initializing, models are loading, or the port mapping is wrong | Inspect `docker logs` and confirm the host port maps to container `8000` |
| Config/fonts/data “revert to defaults” after container start | A mounted volume is empty and the entry script restored defaults | This is by design; do not delete files such as `admin_config.json` or `accounts.json` from the volume |

The full status-code matrix and trigger sources are in [Authentication and errors](../developer/http-api/authentication-and-errors.md); detailed translation, import, and export request errors are in [Translation endpoints](../developer/http-api/translation-endpoints.md).

## UI copy reference {#ui-copy}

The web pages use both the desktop locale files (read via `/i18n/{locale}`, plus the conditionally mounted `/locales/{locale}`) and HTML/JS hardcoded text. The following is a sample relevant to deployment, login, and security; keys missing from `script.js` fall back to the hardcoded default at the call site and are not treated as localized copy.

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `Manga Translator` | Manga Translator | 漫画翻译器 |
| `Add Files` / `Clear List` | Add Files / Clear List | 添加文件 / 清空列表 |
| `Translation Workflow Mode:` / `Start Translation` | Translation Workflow Mode: / Start Translation | 翻译流程模式：/ 开始翻译 |
| `Basic Settings` / `Advanced Settings` / `Options` | Basic Settings / Advanced Settings / Options | 基础设置 / 高级设置 / 选项 |
| `API Keys (.env)` / `Log output...` | API Keys (.env) / Log output... | API密钥 (.env) / 日志输出... |
| `Export Config` / `Import Config` | Export Config / Import Config | 导出配置 / 导入配置 |
| `admin` | Missing; uses call-site fallback | Missing; uses call-site fallback (“管理”) |
| `env_hint` | API key input fields will appear below based on the selected translator | 根据选择的翻译器，下方会显示所需的 API 密钥输入框 |
| `env_var_saved` / `env_var_save_failed` | API key saved / Failed to save API key | API密钥已保存 / API密钥保存失败 |
| `save_api_keys` | Missing; uses call-site fallback | Missing; uses call-site fallback (“保存 API 密钥”) |
| `api_keys_will_be_saved` / `api_keys_session_only` | Missing; uses call-site fallback | Missing; uses call-site fallback (“API 密钥将保存到服务器”/“API 密钥仅在本次会话中使用，不会保存到服务器”) |
| `login_required_for_api_keys` | Missing; uses call-site fallback | Missing; uses call-site fallback (“登录后可查看和保存 API 密钥”) |

Most body copy of the login page (`static/login.html`) and the admin console (`admin-new.html`) is hardcoded Chinese in HTML without i18n keys, e.g. “管理员用户名”, “创建管理员账户”, “管理控制台”, “会话管理”, “服务器配置”. English wording must be verified item by item in a future i18n phase; this page does not invent translations.

## Related files and formats {#related-files}

| File/directory | Role on this page | Notes |
| --- | --- | --- |
| `manga_translator/server/data/` | Server data directory: `accounts.json`, `sessions.json`, `admin_config.json`, `audit.log`, `permissions.json`, `translation_history.json`, `results/`, `user_resources/` | Maps to the `./data/server` volume in Docker; never commit or share real accounts, tokens, or audit content |
| `admin_config.json` (legacy `manga_translator/server/admin_config.json` is migrated automatically) | Admin settings: admin password, registration toggle, API-key policy, upload limits | Can be initialized on first start by `MANGA_TRANSLATOR_ADMIN_PASSWORD` (minimum 6 characters) |
| `.env` | Server-side API keys and environment variables | `/env` never returns plaintext; Compose needs an explicit `./data/app.env` mount to persist |
| `config/config.json` | Web default configuration (generated from template) | Importing a user config overrides in-memory settings |
| `packaging/Dockerfile`, `packaging/docker-compose.yml`, `packaging/docker-entrypoint.sh` | Image build, ports/volumes/health check, and default-data restore | Replace the example admin password and memory limits for production |
| `manga_translator/server/static/` | Front-end pages and scripts: `index.html`, `login.html`, `admin-new.html`, `script.js`, `js/i18n.js` | Page text mixes locale keys and hardcoded strings |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Deployment | `packaging/Dockerfile`, `packaging/docker-compose.yml`, `packaging/docker-entrypoint.sh` | CPU/GPU port mapping, environment variables, volumes, health check, default-data restore |
| Startup and ports | `manga_translator/args.py`, `manga_translator/server/main.py` | `MT_WEB_HOST`/`MT_WEB_PORT` default `0.0.0.0:8000`, CORS, static mounts, Uvicorn timeouts |
| Authentication/authorization | `manga_translator/server/core/middleware.py`, `routes/auth.py`, `routes/web.py` | `require_auth`/`require_admin`, 401/403/429, login/register/legacy-gate rate limits |
| Session security | `manga_translator/server/core/session_service.py`, `session_security_service.py` | Token generation, 60-minute timeout, UUID v4 validation, ownership, anti-enumeration rate limit, audit |
| Download tickets | `manga_translator/server/core/download_ticket_service.py` | Short-lived tickets, default 5-minute TTL, expiry cleanup |
| Front end | `manga_translator/server/static/script.js`, `js/i18n.js`, `login.html`, `admin-new.html` | `localStorage.session_token`, `X-Session-Token`, `/auth/check` redirect, locale loading and fallback |
| Admin settings | `manga_translator/server/core/config_manager.py`, `server_paths.py` | `MANGA_TRANSLATOR_ADMIN_PASSWORD`, `admin_config.json` path and migration |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract; TODO.md was not modified |
| Deployment and ports | Complete | Statically checked Dockerfile, docker-compose.yml, args.py, main.py |
| Security boundary | Complete | Statically checked middleware.py, auth.py, session_security_service.py, download_ticket_service.py |
| Three-column UI copy | Complete | Verified `en_US.json`/`zh_CN.json` values and script.js call-site fallbacks item by item |
| Route-mirror / source-evidence scripts | Complete | `node scripts/verify-route-mirror.mjs .` and `node scripts/verify-source-evidence.mjs .` pass |
| Sanitized runtime verification | Deferred | No service started; no real `.env`, account, token, user image, or private path read |
| VitePress build | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` before merge |
