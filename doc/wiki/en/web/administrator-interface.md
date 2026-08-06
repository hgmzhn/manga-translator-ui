---
title: Administrator Interface
description: Manage users, groups, quotas, sessions, tasks, history, logs, API-key presets, server configuration, announcements, and storage cleanup from the web admin panel
pageId: web.administrator-interface
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Administrator Interface

When a server is shared by multiple users, the administrator uses the admin interface (`admin-new.html` served at `GET /admin`) to manage accounts and runtime state: create users, organize groups and permissions, configure quotas, inspect and revoke sessions, monitor and cancel translation tasks, review user history and system logs, manage API-key presets and the server `.env`, set server parameters, publish announcements, and clean up storage. Only a session whose `role` is `admin` can enter; non-admin visitors are warned and sent back to the home page.

This page covers only what an administrator does in the browser. The request, response, and status-code contracts of the underlying JSON/form endpoints (for example `/api/admin/users`, `/api/admin/groups`, `/api/admin/quota/*`, `/audit/events`) belong to the developer HTTP API pages. Login and first-time admin setup are described in [Login, language, and session](./login-language-and-session.md); the user-side workspace is covered in [Upload, configure, and translate](./upload-config-and-translate.md).

## Feature boundary {#feature-boundary}

- Entry is restricted: the “admin” link in the user home-page top bar is shown only when the session role is `admin` (`static/script.js` checks `userSession.role === 'admin'`). Visiting `/admin` directly still runs `GET /auth/check` first; when the token is missing or invalid, `localStorage.session_token` is cleared and the browser is redirected to `/static/login.html?redirect=/admin`; a non-admin is told “您没有管理员权限” (no admin permission) and returned to `/` (`static/js/admin/app.js`).
- The panel has 12 navigation modules: dashboard, user management, group management, quota management, session management, task monitoring, history, system logs, API-key management, server configuration, announcement management, and cleanup management.
- The `app.js` title map also contains “权限管理” (permission management), but `admin-new.html` does not register `modules/permissions.js` in its navigation or initialization, so that module is an unwired static list and is not treated as an operable feature on this page.
- Auditing (`AuditService` plus the `/audit/*` routes) automatically records login, password change, user creation, permission changes, and translation start/complete/failure events. The current admin panel has no audit module; querying and exporting go through the developer HTTP API.
- The admin panel is not a reuse of the desktop Qt UI: `admin-new.html` hardcodes most copy in Chinese and only a few controls use i18n (see the UI-copy table below), which differs from the user site and desktop locales.

## Entering the admin interface {#enter-admin-panel}

1. Log in with an administrator account, or open `/admin` directly; after a successful login the login page returns to the admin interface via `redirect=/admin`.
2. The page first calls `GET /auth/check` with the `X-Session-Token` header. When the session is invalid, the token is cleared and the page returns to the login page; when `role` is not `admin`, the user is warned and sent back to the home page.
3. The left navigation is grouped into “概览 / 用户管理 / 系统监控 / 系统设置” (Overview / User management / System monitoring / System settings). Clicking an item switches modules and writes the module ID into the URL hash (for example `#users`); the top title and the “管理控制台 / 当前模块” (Admin console / current module) breadcrumb update together.
4. The “logout” button calls `POST /auth/logout`, clears the token, and returns to the login page.

```mermaid
flowchart TD
    A["Visit /admin"] --> B{session_token in localStorage?}
    B -- no --> C["Go to /static/login.html?redirect=/admin"]
    B -- yes --> D["GET /auth/check (X-Session-Token)"]
    D -- invalid or failed --> E["Clear token, go to login page"]
    D -- valid --> F{role is admin?}
    F -- no --> G["Warn no admin permission, go to /"]
    F -- yes --> H["Enter admin panel, switch module by URL hash"]
```

## Dashboard and task monitoring {#dashboard-and-tasks}

### Dashboard

The dashboard shows four stat cards: active users, today's translations, running tasks, and storage usage, followed by an “active tasks” table (task ID, user, status, progress, start time, actions) with a refresh button. `app.js` only wires the task count and user count; today's translations and storage usage start as `--`, and the storage card only shows real values in the cleanup module.

### Task monitoring

“Task monitoring” lists all running tasks (task ID, user, type, status, progress bar, start time, actions) and auto-refreshes every 3 seconds while the module is open; “暂停刷新 / 自动刷新” (pause/auto refresh) toggles it. “Cancel” calls `POST /admin/tasks/{task_id}/cancel` for tasks in `pending/queued/processing/running` states; “details” is only a front-end alert placeholder. Note that “cancel all” calls `POST /admin/tasks/cancel-all`, which is not defined in the current `routes/admin.py` (static-source check), so the actual behavior requires runtime verification.

## User management {#user-management}

### Create and edit a user

The “user list” table columns are username, role, group, status, created time, and actions.

1. Click “➕ 添加用户” (add user), enter a username, a password (at least 6 characters), choose a group, an API-key preset (empty means “inherit the group setting”), and a role (regular user / administrator), then click “✅ 创建” (create); this maps to `POST /api/admin/users`.
2. Click row “编辑” (edit) to change the new password (leave empty to keep it), group, API-key preset, role, and the “账户启用” (account enabled) switch, then click “💾 保存” (save); this maps to `PUT /api/admin/users/{username}`.
3. “⚙️ 编辑权限配置（翻译器、参数限制等）” (edit permission configuration) opens the permission editor. In user mode it only shows the current group and states that the user's permissions and quota are fully determined by the group.

### Delete a user

Rows for non-`admin` users have a “删除” (delete) button mapping to `DELETE /api/admin/users/{username}`; administrator accounts show no delete button. Deleting a user also invalidates their sessions.

## Groups and permissions {#groups-and-permissions}

### Create a group

The “group list” table columns are group name (with ID), description, member count, API-key preset, default flag, and actions.

Click “➕ 创建用户组” (create group) and fill in: group ID (letters, digits, and underscores only), display name, description, and an optional default API-key preset (empty uses the server default), then click “✅ 创建” (create); this maps to `POST /api/admin/groups`. The system groups `admin`, `default`, and `guest` show no delete button; deleting a group moves its users to the `default` group.

### The shared permission editor

Click “编辑” (edit) on a group row to open the shared permission editor (`components/permission-editor.js`), which has 6 tabs:

| Tab (UI call key) | English actual value | Simplified Chinese actual value | Notes |
| --- | --- | --- | --- |
| `Basic Settings` | Basic Settings | 基础设置 | Default API-key preset plus translator, OCR, detector, and other parameter selects/inputs |
| `CLI Options` | Missing (falls back to “输出选项”) | 输出选项 | Output format, save quality, retries, batch size, GPU, and other CLI parameters |
| `Advanced Settings` | Advanced Settings | 高级设置 | Inpainter, upscaler, and colorizer parameters |
| `label_renderer` | Renderer | 渲染器 | Renderer and typesetting parameters |
| `web_quota_management` | Missing (falls back to “配额限制”) | 配额限制 | Daily quota, batch settings, upload limits |
| `web_group_permissions` | Missing (falls back to “功能权限”) | 功能权限 | Visible presets, capability allowlists, workflows, API-key policy, resource and feature permissions |

Each parameter row has a “✓ 启用 / 🚫 禁用（用户不可见）” (enable / disable, invisible to users) toggle; disabling a parameter hides it from the user side. Saving calls `PUT /api/admin/groups/{group_id}/config`, writing parameter config, allow/deny lists, the default preset, and visible presets.

### Feature permissions and inheritance

The “feature permissions” tab offers “allow all” plus per-item checkboxes for four capability classes (translator, OCR, colorizer, renderer) and a workflow selector. The “API keys” section can set: allow users to edit API keys on the home page, allow using server-default API keys, require users to provide API keys or a preset, and allow saving user-entered API keys to the server (this writes to the server `.env` and affects everyone, so it is not recommended in multi-user setups). The “resource management” section controls font and prompt upload/delete permissions, and the “feature permissions” section controls batch processing, API access, text export, history viewing, and log viewing.

Permission and quota resolution follows the priority: explicit user-level settings > group configuration > server defaults. User mode only saves deltas relative to the group (checking unlocks a disabled capability as an allowlist entry; unchecking adds an extra deny-list entry).

```mermaid
flowchart TD
    U["User requests a translation feature"] --> C{Explicit user-level setting?}
    C -- yes --> U1["Use user override: allowlist unlock / deny-list disable"]
    C -- no --> G{Group has a setting?}
    G -- yes --> G1["Use group config: parameter visible/read-only/default"]
    G -- no --> S["Server default config"]
    U1 --> R["Final parameters and permissions"]
    G1 --> R
    S --> R
    R --> F["User-side effect: unauthorized translator→403; disabled parameter→control hidden; quota exceeded→429"]
```

## Quota and sessions {#quota-and-sessions}

### Default quota settings

The “default quota settings” form contains daily limit, monthly limit, max file size, and max batch size; clicking “💾 保存配额设置” (save quota settings) maps to `PUT /admin/settings` (writes `default_quota`). These are server defaults edited directly on the page and are a different entry point from the “quota limits” tab of the group permission editor.

### Per-user quota usage

The “user quota usage” table shows each user's daily usage (with a progress bar that turns red above 80%) and monthly usage. Note that the row “编辑” (edit) and “重置” (reset) buttons are currently front-end `prompt`/`alert` placeholders that call no backend endpoint; the backend does expose `/api/admin/quota/set-limits` and `/api/admin/quota/reset`, but this admin UI is not wired to them (static-source check), so real persistence needs runtime verification.

### Session management

The “active sessions” table columns are user, token (first 12 characters), IP address, device (first 30 characters of the user agent), login time, and actions. The current session is highlighted green with a “当前” (current) badge and no revoke button; other sessions can be revoked with `DELETE /sessions/{session_token}`. “Revoke all other sessions” calls `POST /sessions/revoke-all`, which is not defined in the current `routes/sessions.py` (static-source check) and needs runtime verification.

## History and system logs {#history-and-logs}

### History

“Translation history” first requests `/api/history/admin/all?limit=1000&offset=0`, then aggregates by user into: user, translation count, file count, total size, and last activity, with “📷 查看相册” (view gallery) and “🗑 删除全部” (delete all) actions. The gallery modal reuses the user-side gallery style: preview images, select several, download the selection as a ZIP, download a single item, or download a user's entire history (each first requests a download ticket), and delete selected or single records. “Clear history” deletes records one by one and warns that the operation cannot be undone.

### System logs

“System logs” renders a dark terminal-style stream (time, level, session-ID tag, message) that auto-refreshes every 5 seconds while the module is open. It supports filtering by session ID, filtering by level (All / DEBUG / INFO / WARNING / ERROR), “暂停滚动 / 自动滚动” (pause/resume scrolling), and “📥 下载” (download, `GET /admin/logs/export`). Logs may contain paths, filenames, and request details, so they must be sanitized before export or sharing. “Clear” calls `POST /admin/logs/clear`, which is not defined in the current `routes/admin.py` (static-source check) and needs runtime verification.

## API keys and presets {#api-keys-and-presets}

1. The “📦 API 密钥预设” (API-key presets) section can create presets (name, description, multi-select visible groups, and per-provider API-key forms), edit them, and delete them; presets are assigned to groups, and a group chooses which preset's keys to use.
2. The “🔐 服务器默认API密钥” (server default API keys) section reads the server `.env` (`GET /api/admin/config/server?show_values=true`) and shows the key fields in category forms; “💾 保存API密钥” (save API keys) maps to `PUT /api/admin/config/server`, which backs up `.env` first by default.
3. The user-side resolution order is: user-entered > current preset > server default; provider-specific OCR/colorizer/renderer keys left empty fall back to the provider's general translation key.

This page never shows real keys, tokens, or `.env` content; documentation and screenshots use sanitized placeholders only. The front end temporarily stores keys the user enters on the user site in `localStorage.user_env_vars`, but the server does not return key plaintext through ordinary configuration endpoints.

## Server configuration, announcements, and cleanup {#config-announcement-cleanup}

### Server configuration

“Basic settings” contains server name, max concurrent tasks, task timeout (seconds), max file size (MB), allowed formats, max batch size, the “allow user registration” switch, and a default-group dropdown for new registrations (excluding the `admin` group); “💾 保存设置” (save settings) maps to `PUT /admin/settings`.

“Server font management” can upload `.ttf` / `.otf` / `.ttc` fonts, and list and delete server-shared fonts (available to all users); “server prompt management” can list, view, upload, and delete server prompts. The endpoints are `/upload/font`, `/fonts`, `/fonts/{name}` and `/upload/prompt`, `/prompts`, `/prompts/{name}` respectively.

### Announcement management

“Announcement management” can enable an announcement, choose its type (info = blue, warning = yellow, error = red), enter content, and preview it live; “💾 保存公告” (save announcement) maps to `PUT /admin/announcement`, writing to `admin_settings`. Once enabled, the user site reads it via `GET /announcement` and displays an announcement bar. “Clear announcement” only empties the form and needs a subsequent save to take effect.

### Cleanup management

“Storage usage” shows the size and file count of the uploads directory (user fonts + prompts), results directory, cache directory, and the total. The cache directory is currently always 0 (the source has no separate cache directory). Each directory has a “clean” button, plus “clean all”, mapping to `POST /admin/cleanup/{uploads|results|cache|all}`; this deletes directory contents but keeps `index.json` and returns the freed space. Cleanup cannot be undone and requires confirmation first.

“Auto-cleanup settings” contains enable auto-cleanup, interval (hours), max file retention (days), and max storage (GB); “💾 保存设置” (save settings) maps to `PUT /admin/settings` (writes `cleanup`).

## UI copy reference {#ui-copy}

Most admin-panel copy is hardcoded Chinese in `admin-new.html` without an i18n key; the table below records this honestly. Rows whose key is `web_*` come from the user-site i18n (`doc/wiki/data/i18n.generated.json` and `desktop_qt_ui/locales/*.json`) and are not necessarily used by the admin panel itself.

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| User home-page “admin” link (`script.js` calls key `admin`) | Missing (desktop locales have no `admin`, falls back to “管理”) | 管理 |
| `web_admin_panel` (user-site i18n) | Admin Panel | 管理面板 |
| `admin-new.html` hardcoded: sidebar title | Missing (Chinese hardcode only) | 管理控制台 |
| Hardcoded: 概览 / 系统监控 / 系统设置 | Missing (Chinese hardcode only) | 概览 / 系统监控 / 系统设置 |
| Hardcoded: 仪表盘 | Missing (Chinese hardcode only) | 仪表盘 |
| Hardcoded: 用户管理 | Missing (Chinese hardcode only) | 用户管理 |
| `web_user_management` | User Management | 用户管理 |
| Hardcoded: 用户组管理 | Missing (Chinese hardcode only) | 用户组管理 |
| `web_group_management` | Group Management | 用户组管理 |
| Hardcoded: 配额管理 | Missing (Chinese hardcode only) | 配额管理 |
| `web_quota_management` | Quota Management | 配额管理 |
| Hardcoded: 会话管理 | Missing (Chinese hardcode only) | 会话管理 |
| Hardcoded: 任务监控 | Missing (Chinese hardcode only) | 任务监控 |
| Hardcoded: 历史记录 | Missing (Chinese hardcode only) | 历史记录 |
| `web_history_management` | History Management | 历史记录管理 |
| Hardcoded: 系统日志 | Missing (Chinese hardcode only) | 系统日志 |
| `web_log_management` | Logs | 日志管理 |
| Hardcoded: API密钥管理 | Missing (Chinese hardcode only) | API密钥管理 |
| `web_preset_management` | Presets | 预设管理 |
| Hardcoded: 服务器配置 | Missing (Chinese hardcode only) | 服务器配置 |
| `web_server_config` | Server Configuration | 服务器配置 |
| Hardcoded: 公告管理 | Missing (Chinese hardcode only) | 公告管理 |
| Hardcoded: 清理管理 | Missing (Chinese hardcode only) | 清理管理 |
| `web_cleanup_management` | Cleanup | 清理管理 |
| Hardcoded: ➕ 添加用户 / ➕ 创建用户组 | Missing (Chinese hardcode only) | ➕ 添加用户 / ➕ 创建用户组 |
| Hardcoded: 编辑 / 删除 / 取消 / 保存 | Missing (Chinese hardcode only) | 编辑 / 删除 / 取消 / 保存 |
| `web_cancel` / `web_save` | Cancel / Save (not in desktop locales; actually falls back to Chinese) | 取消 / 保存 |
| Hardcoded: 💾 保存配额设置 / 💾 保存设置 / 💾 保存API密钥 | Missing (Chinese hardcode only) | 💾 保存配额设置 / 💾 保存设置 / 💾 保存API密钥 |
| Hardcoded: 撤销所有其他会话 | Missing (Chinese hardcode only) | 撤销所有其他会话 |
| Hardcoded: 取消全部 / 清空历史 / 清空 / 📥 下载 | Missing (Chinese hardcode only) | 取消全部 / 清空历史 / 清空 / 📥 下载 |
| Hardcoded: 活跃会话 | Missing (Chinese hardcode only) | 活跃会话 |
| `web_active_sessions` | Active Sessions | 活跃会话 |
| Hardcoded: 启用公告 / 保存公告 / 清除公告 | Missing (Chinese hardcode only) | 启用公告 / 保存公告 / 清除公告 |
| Hardcoded: 启用自动清理 / 清理 | Missing (Chinese hardcode only) | 启用自动清理 / 清理 |
| `web_auto_cleanup` | Auto Cleanup | 自动清理 |
| Permission-editor tab `Basic Settings` | Basic Settings | 基础设置 |
| Permission-editor tab `CLI Options` | Missing (falls back to “输出选项”) | 输出选项 |
| Permission-editor tab `Advanced Settings` | Advanced Settings | 高级设置 |
| Permission-editor tab `label_renderer` | Renderer | 渲染器 |
| Permission-editor tab `web_group_permissions` | Missing (falls back to “功能权限”) | 功能权限 |
| `web_daily_quota` / `web_daily_limit` | Daily Quota / Daily Limit | 每日配额 / 每日限制 |
| `web_upload_limit` / `web_max_file_size` / `web_max_files` | Upload Limit / Max File Size / Max Files | 上传限制 / 单文件最大 / 最多文件数 |
| `web_can_upload_font` / `web_can_upload_prompt` | Can Upload Font / Can Upload Prompt | 可上传字体 / 可上传提示词 |
| `web_can_view_history` / `web_can_view_logs` | Can View History / Can View Logs | 可查看历史 / 可查看日志 |
| `web_default_preset` | Default Configuration | 默认配置 |
| `data-i18n="API Keys (.env)"` (the only i18n attribute in `admin-new.html`) | API Keys (.env) | 服务器默认API密钥 (hardcoded fallback) |

Notes: `admin-new.html` loads `static/js/i18n.js`, which reads the desktop locale from `/locales/{locale}.json`; the `AdminI18n` class in `js/admin/i18n.js` is not loaded by `admin-new.html`. The permission editor and envvars modules fetch strings through `window.i18n.t(key, fallback)`, and when a `web_*` key is absent from the desktop locale it falls back to the Chinese fallback at the call site, so some controls still show Chinese even under the English locale. This is a statically confirmable i18n gap; the exact rendering requires headless-browser runtime verification.

## Dependencies and conflicts {#dependencies-and-conflicts}

- Users, groups, quotas, sessions, tasks, history, and logs are managed by different services but depend on each other: quota resolution follows “user level > group level > global default” (`quota_service.py`) and permission resolution is done by `permission_service.py` / `permission_calculator.py`; editing a group immediately affects all of its users.
- The panel's “quota management” and the permission editor's “quota limits” are two entry points whose field names differ (`daily_limit`/`monthly_limit` versus `daily_image_limit`/`daily_char_limit`); the backend contract belongs to the developer HTTP API pages.
- Saving the server `.env` (including “allow saving user-entered API keys to the server”) affects all users globally and is not recommended in multi-user environments.
- The admin “history” is a cross-user aggregated view; it is neither the user-side `localStorage` result list nor the server history store itself.
- Cancelling tasks, clearing history, and cleaning storage are irreversible; deletions and revocations that touch real users must be careful and leave audit records.
- Audit events from login, password change, registration, task creation, permission denials, translation progress, and user/permission management are written automatically to `audit.log` (10 MB rotation, 5 backups kept); the current admin UI has no audit module, so querying/exporting goes through the `/audit/*` endpoints.

## Related files and formats {#related-files}

| File/format | Actual role on this page | Notes |
| --- | --- | --- |
| `manga_translator/server/static/admin-new.html` | Admin panel page | Mostly hardcoded Chinese; never write real usernames or keys |
| `manga_translator/server/static/js/admin/app.js`, `modules/*.js`, `components/permission-editor.js` | Module logic and the permission editor | Some buttons call endpoints not defined in the backend (see body) |
| `manga_translator/server/routes/web.py`, `admin.py`, `users.py`, `groups.py`, `sessions.py`, `quota.py`, `audit.py`, `config_management.py` | `/admin`, `/api/admin/*`, `/sessions/*`, `/audit/*` endpoints | Contract details belong to the developer HTTP API pages |
| `manga_translator/server/core/config_manager.py` | `DEFAULT_ADMIN_SETTINGS` and `admin_config.json` read/write | Source of registration switch, announcement, permission, and quota defaults |
| `manga_translator/server/core/group_management_service.py`, `group_service.py` | Group CRUD and parameter config | System groups `admin`/`default`/`guest` cannot be deleted |
| `manga_translator/server/core/quota_service.py`, `audit_service.py`, `permission_service.py`, `task_manager.py`, `middleware.py` | Quota, audit, permission, task, and auth | `require_admin` decides 401/403 |
| `manga_translator/server/data/admin_config.json` | Admin-settings persistence | Record structure only, never real content |
| `manga_translator/server/data/accounts.json`, `group_config.json`, `env_presets.json`, `audit.log` | Accounts, groups, presets, audit log | Never read or display real user data |
| `.env` (server) and `server/data/backups/.env.backup.{timestamp}` | Server key config and pre-save backup | Never write or display real keys |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| Entry and auth | `manga_translator/server/routes/web.py`, `static/js/admin/app.js`, `static/script.js` | `/admin` page, session check, admin-role gate, logout |
| Page structure | `manga_translator/server/static/admin-new.html` | 12 nav modules, stat cards, module forms and buttons |
| Module logic | `static/js/admin/modules/{users,groups,quota,sessions,tasks,history,logs,envvars,config,announcement,cleanup}.js` | Per-module load, render, and called endpoints |
| Permission editor | `static/js/admin/components/permission-editor.js` | 6 tabs, parameter locking, allow/deny lists, presets, workflows |
| Backend endpoints | `server/routes/{admin,users,groups,sessions,quota,audit,config_management}.py` | Existence, status codes, and permission dependencies of admin endpoints |
| Service layer | `server/core/{config_manager,group_management_service,group_service,quota_service,audit_service,permission_service,middleware,task_manager}.py` | Defaults, quota priority, audit rotation, `require_admin` |
| i18n | `static/js/i18n.js`, `static/js/admin/i18n.js`, `doc/wiki/data/i18n.generated.json`, `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | key→en_US→zh_CN actual values and missing/fallback cases |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read in full and followed the page contract |
| Page structure and module inventory | Complete | Statically checked `admin-new.html` and `app.js` nav modules |
| Module behavior and endpoints | Complete | Statically checked module JS against backend routes; found `cancel-all`, `logs/clear`, and `sessions/revoke-all` front-end calls that do not match backend routes, and quota edit/reset as front-end placeholders |
| `en_US` / `zh_CN` actual locales | Complete | The table records key, actual English, and actual Simplified Chinese values; hardcoded items are marked missing/fallback |
| Sanitized runtime verification | Deferred | No real `.env`, accounts, audit logs, user data, or keys were read; needs a headless browser with a sanitized admin account to verify English rendering, permission filtering, and the undefined endpoints above |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` plus mirror/source checks before merge |
