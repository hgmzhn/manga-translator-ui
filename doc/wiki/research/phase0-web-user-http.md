# Phase 0：Web 用户功能、HTTP 路由、鉴权、端口与状态码源码清单

> 范围：`TODO.md` 第 81 行；调查日期：2026-08-06；证据等级：静态源码已核对，未启动 Web 服务、浏览器或 Docker。
>
> 本清单是后续 Web 用户页面与开发者 HTTP API 页的证据索引，不是终端用户教程。Web 操作和协议契约特意分开，避免将 API 端点误写成用户界面操作。

## 1. 固定边界与计数

- 正式 CLI `web` 子命令的默认监听地址为 `0.0.0.0`，默认端口为 `8000`；`MT_WEB_HOST` 和 `MT_WEB_PORT` 可以覆盖它。`manga_translator/server/args.py` 另有一个未由正式顶层解析器使用的 `127.0.0.1:8000` 解析器，不能据此改写正式默认值。
- `manga_translator/server/main.py` 导入并注册全部路由模块。源码中有 **149 个显式路由声明**；`/api/history/downloads/t/{ticket}` 一个声明注册 `GET` 和 `HEAD` 两种方法，故为 **150 个方法—路径映射**。这个计数不包含静态 mount、FastAPI 自动文档路径或由框架产生的重定向。
- 静态资源 mount 为 `/static/{path}`；当桌面 locale 目录存在时，`/locales/{path}` 也会 mount。FastAPI 未覆盖默认文档配置，运行实例还应提供框架自动的 `/openapi.json`、`/docs`、`/docs/oauth2-redirect` 和 `/redoc`；这些路径需在后续启动验证中确认。
- 本文把 `GET /` 的普通用户界面、`GET /admin` 的管理界面和 `static/login.html` 的会话入口视为 Web 用户功能；所有 JSON、表单和流式端点均归开发者 HTTP 路由，即使静态前端会调用其中一部分。

## 2. Web 用户功能（不等同于 HTTP API）

| 用户区域 | 静态源码能固定的功能边界 | 直接证据 |
| --- | --- | --- |
| 登录与会话 | `GET /` 的 `script.js` 先读取 `localStorage.session_token`，调用 `GET /auth/check`；缺失、无效或请求失败时清除令牌并跳转 `/static/login.html`。登录页支持首次管理员设置、用户名/密码登录、按服务器开关显示注册、强制改密和安全的 `redirect=/admin` 返回。 | `static/script.js:88`、`:228`、`:249`；`static/login.html:605`、`:649`、`:697`；`routes/auth.py:125`、`:349`、`:443` |
| 主工作区 | 页面可选择文件、目录和 `image/*,.pdf,.json,.txt`；选择正常、导出译文、导出原文、导入译文并渲染、仅上色、仅超分、仅修复七种工作流，随后开始任务。多张正常翻译按 `cli.batch_size` 分批；其他模式逐文件处理。 | `static/index.html:99`、`:128`、`:141`；`static/script.js:2337`、`:2384`、`:2528` |
| 进度、结果与下载 | 主流程用带 30 分钟浏览器超时的批量请求，或读取自定义二进制流的进度帧；完成的图片/ZIP 在浏览器结果列表中预览、单项下载、批量打包下载或清空。该列表保存到浏览器 `localStorage`，不等同于服务器历史记录。 | `static/script.js:2419`、`:2600`、`:3050`、`:3225` |
| 配置与预设 | 页面按 Basic、Advanced、Options、API Keys 标签生成配置；加载可选值、翻译器、语言、工作流、用户设置和预设。配置导入/导出在浏览器本地进行 JSON 文件读写；用户预设和服务器 API Key 状态则经会话端点处理。 | `static/index.html:182`、`:197`、`:270`；`static/script.js:420`、`:662`、`:777`、`:820`、`:871`、`:931`、`:2855` |
| API Key、字体、提示词 | API Key 标签初始隐藏，是否显示及允许保存由返回的权限/策略决定。用户可在获权时上传、列出和删除私有字体与提示词；前端发送 `X-Session-Token`。前端把当前输入的 Key 暂存在 `localStorage.user_env_vars`，但后端的 `/env` 与 `/env/effective` 不返回服务器密钥明文。 | `static/index.html:235`、`:251`、`:270`；`static/script.js:1799`、`:1870`、`:2895`；`routes/config.py:890`、`:900`、`:944`；`routes/resources.py:62` |
| 历史与日志 | 用户界面刷新自身历史、打开图库、对单项或批量下载先申请短时下载票据；翻译流结束后以及轮询期间读取 `/api/logs`。历史查看、下载、删除和日志读取均受会话及相应权限限制。 | `static/index.html:167`；`static/js/history-gallery.js:67`、`:428`、`:585`；`static/script.js:2698`、`:2784`；`routes/history.py:186`、`:501`、`:766`；`routes/logs.py:225` |
| 语言与移动布局 | `zh_CN`、`zh_TW`、`en_US`、`ja_JP`、`ko_KR`、`es_ES` 是 HTML 中的选择值；前端从 `/i18n/{locale}` 取桌面 locale JSON，并将选择存入 `localStorage.locale`。窄屏下设置面板切为移动菜单，图片查看器实现双指缩放。 | `static/index.html:74`；`static/script.js:444`、`:488`、`:3452`、`:3565`；`routes/config.py:989`、`:996` |
| 管理界面 | 管理员账号才显示 `/admin` 链接；管理界面也会先检查会话并在无效时跳回登录页。它的用户、组、权限、配额、任务、日志、历史、存储/清理、配置、环境变量和公告功能使用单独的管理路由。 | `static/script.js:170`；`static/js/admin/app.js:20`；`static/admin-new.html:630`；`routes/admin.py:44`；`routes/users.py:79`；`routes/groups.py:87` |

### UI/i18n 结论与限制

- Web 页面不是桌面 Qt 界面的直接复用：`index.html`、`login.html`、`admin-new.html` 含有初始中文和部分硬编码提示；`script.js` 仅以其调用的 locale key 覆盖一部分静态文字。因此后续英文页面必须同时核对 HTML、`script.js` 与 `static/js/i18n.js`，不能仅引用桌面 i18n。
- `static/js/i18n.js` 和主脚本都将语言偏好保存在 `localStorage`，并经 `/locales/{locale}.json` 或 `/i18n/{locale}` 读取。当前用户流程实际使用后者；前者是可复用 i18n 类的实现，未见被 `index.html` 引入。
- 旧式密码门仍在：`checkUserAccess()` 调用 `/user/access` 和 `/user/login`，并以 `sessionStorage.user_logged_in` 记录成功。但是 DOMContentLoaded 只调用 `init()`，而 `init()` 先走 `/auth/check`；本阶段仅记录两条源码路径，不把旧式密码门写成当前启动的主登录流程。

下表是主脚本实际调用且已在两份 desktop locale 中核对的样本。`admin` 不是 locale key，`t('admin', '管理')` 在缺失时回退为中文“管理”，不能把它记为已本地化的 English 文案。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `Manga Translator` | Manga Translator | 漫画翻译器 |
| `Add Files` / `Clear List` | Add Files / Clear List | 添加文件 / 清空列表 |
| `Translation Workflow Mode:` / `Start Translation` | Translation Workflow Mode: / Start Translation | 翻译流程模式：/ 开始翻译 |
| `Export Config` / `Import Config` | Export Config / Import Config | 导出配置 / 导入配置 |
| `Basic Settings` / `Advanced Settings` / `Options` | Basic Settings / Advanced Settings / Options | 基础设置 / 高级设置 / 选项 |
| `API Keys (.env)` / `Log output...` | API Keys (.env) / Log output... | API密钥 (.env) / 日志输出... |
| `Normal Translation` / `Export Translation` / `Export Original Text` | Normal Translation / Export Translation / Export Original Text | 正常翻译流程 / 导出翻译 / 导出原文 |
| `Import Translation and Render` / `Colorize Only` / `Upscale Only` / `Inpaint Only` | Import Translation and Render / Colorize Only / Upscale Only / Inpaint Only | 导入翻译并渲染 / 仅上色 / 仅超分 / 仅修复 |
| `admin` | 缺失，使用调用处 fallback | 缺失，使用调用处 fallback |

来源：`static/script.js:444`、`:515`；`desktop_qt_ui/locales/en_US.json`；`desktop_qt_ui/locales/zh_CN.json`。

## 3. 鉴权与授权清单

### 3.1 当前会话主流程

1. 未有账号时，`GET /auth/status` 返回 `need_setup`，登录页将管理员用户名/密码 JSON 发送至 `POST /auth/setup`。
2. 登录或允许的注册分别调用 `POST /auth/login` 或 `POST /auth/register`。成功响应包含 `token`、用户、角色和权限；前端把 token 写入 `localStorage.session_token`。
3. 后续请求以 `X-Session-Token` 传递令牌。`require_auth` 验证令牌、刷新活动时间，并拒绝不存在、过期或已停用账号；`require_admin` 在此基础上要求 `role == 'admin'`。
4. `POST /auth/logout` 终止会话；前端无论请求结果如何都会清空本地 token 并回到登录页。`POST /auth/change-password` 也要求令牌。
5. 翻译入口另外调用 `verify_translation_auth`：除会话外检查翻译器、OCR、上色器、渲染器及可用参数，随后由路由层记录并发和每日配额。用户历史、资源、预设和日志使用各自的 `require_auth`/`require_admin` 依赖或权限服务。

### 3.2 不要求此会话头的例外

| 端点类别 | 静态行为与边界 |
| --- | --- |
| 页面、静态、locale、API 信息 | `/`、`/admin`、`/api`、`/favicon.ico`、`/static/*`，以及条件挂载的 `/locales/*`，在路由层没有 `X-Session-Token` 依赖；页面内的业务数据请求另行鉴权。 |
| 登录/设置/注册/状态 | `/auth/login`、`/auth/status`、`/auth/setup`、`/auth/register` 在建立会话前可访问；注册仍受管理员开关和速率限制。 |
| 旧密码 gate | `/user/access` 与 `/user/login` 是旧式单密码门。`/user/login` 在未要求密码时直接成功；否则单 IP 10 分钟内最多 10 次，超限返回 `429` 与 `Retry-After`。它不签发 `X-Session-Token`。 |
| 下载票据 | `GET|HEAD /api/history/downloads/t/{ticket}` 不取会话头，依赖先前获权请求生成的短时票据；无效或过期票据返回 `404`。 |
| 元数据/兼容端点 | `/config`、`/config/defaults`、`/config/options`、`/fonts`、`/translators`、`/languages`、`/workflows`、`/translator-config/{translator}`、`/user/access`、`/i18n/*`、`/announcement` 可在无 token 下返回公开/legacy 过滤结果；带 token 或 `mode=authenticated` 时会过滤用户可见配置/选项。后续运行验证应确认各 mode 的实际响应。 |

### 3.3 鉴权相关状态码

| 状态码 | 已固定的来源与用户/客户端含义 |
| --- | --- |
| `401` | 缺失、无效、过期、无法刷新或已停用的会话会由 `require_auth` 拒绝；翻译鉴权和登出/改密也明确拒绝无 token。Web 前端对 `/auth/check` 失败会删除本地 token 并跳登录页。 |
| `403` | 已登录但不是管理员，或用户没有相应的翻译器、OCR、上色器、渲染器、资源、历史、日志或管理权限。前端隐藏参数不能替代服务端检查。 |
| `429` | `/auth/login` 按 IP（15/10 分钟）和用户名（8/10 分钟）限速，`/auth/register` 按 IP（5/10 分钟）限速；旧 `/user/login` 为 10/10 分钟。三者均设置 `Retry-After`。任务并发上限和每日配额也返回 `429`。 |

来源：`routes/auth.py:20`、`:52`、`:125`、`:205`、`:242`、`:289`、`:323`、`:349`、`:443`；`core/middleware.py:94`、`:175`、`:310`、`:341`；`routes/translation_auth.py:228`；`routes/web.py:72`。

## 4. HTTP 路由总表（开发者契约索引）

除特别标出的成功状态外，FastAPI 路由未显式覆盖成功状态，因此默认成功为 `200`。`*` 表示该组的全部路由有会话/管理员依赖；详细 request/response 模型与示例留给后续开发者 API 页面，不在本阶段伪造运行响应。

| 路由组（前缀） | 方法和路径后缀 | 数量 | 鉴权边界 / 来源 |
| --- | --- | ---: | --- |
| 页面、系统与静态 | `GET /`、`GET /admin`、`GET /api`、`POST /user/login`、`GET /favicon.ico`、`POST /register`（内部 `X-Nonce`）；mount：`/static/*`、条件 `/locales/*`；框架默认文档见第 1 节 | 6 声明 + mount | `web.py:30`；`main.py:280`、`:283`、`:314` |
| 会话认证（`/auth`） | `POST /login`、`POST /logout`、`POST /change-password`、`GET /check`、`GET /status`、`POST /setup`、`POST /register` | 7 | 登录前端与 `auth.py:125`–`:443` |
| 翻译（`/translate`） | `POST /json`、`/bytes`、`/image`、`/json/stream`、`/bytes/stream`、`/image/stream`；`POST /with-form/json`、`/bytes`、`/image`、`/json/stream`、`/bytes/stream`、`/image/stream`、`/image/stream/web`；`POST /batch/json`、`/batch/images`、`/queue-size`；`POST /export/original`、`/export/translated`、`/export/original/stream`、`/export/translated/stream`；`POST /upscale`、`/colorize`、`/inpaint`、`/upscale/stream`、`/colorize/stream`、`/inpaint/stream`；`POST /import/json`、`/import/txt`、`/import/json/stream`、`/import/txt/stream`、`/complete` | 31 | 翻译请求在路由内验证会话、功能权限、参数、并发和每日配额；`translation.py:148`–`:1333`、`translation_auth.py:228` |
| 基础配置（无前缀） | `GET /config/defaults`、`/config`、`/config/options`、`/fonts`、`/translators`、`/languages`、`/workflows`、`/translator-config/{translator}`、`/user/settings`、`/user/access`、`/api-key-policy`、`/env`、`/env/effective`、`/i18n/languages`、`/i18n/{locale}`、`/announcement`；`POST /env` | 17 | `/env*` 与写入 `/env` 需要会话；部分无 token endpoint 会按 legacy/mode 返回过滤内容；`config.py:201`–`:1007` |
| 用户资源（`/api/resources`） | `POST|GET /prompts`、`DELETE /prompts/{resource_id}`、`POST|GET /fonts`、`DELETE /fonts/{resource_id}`、`DELETE /fonts/by-name/{filename}`、`DELETE /prompts/by-name/{filename}`、`GET /stats` | 9 | `require_auth`，上传/删除再检查资源权限；`resources.py:62`–`:449` |
| 历史（`/api/history`） | `GET /`、`/{session_token}`、`/admin/all`、`/search`、`/{session_token}/download`、`/{session_token}/file/{filename}`；`POST /{session_token}/download-ticket`、`/batch-download-ticket`、`/batch-download`、`/{session_token}/file/{filename}/download-ticket`；`DELETE /{session_token}`；`GET|HEAD /downloads/t/{ticket}` | 13 方法—路径 | 除票据兑现端点外需会话；历史查看/删除/下载还按角色和权限验证；`history.py:131`、`:186`–`:766` |
| 日志（`/api/logs`） | `GET /session/{session_token}`、`/session/{session_token}/export`、`/`、`/user`、`/search`、`/admin/system`、`/admin/sessions`、`/admin/statistics`；`DELETE /session/{session_token}/clear`；`POST /admin/export`、`/admin/cleanup` | 11 | 前六个按 `require_auth`/所有权，`/admin/*` 需管理员；`logs.py:43`–`:463` |
| 旧管理（`/admin`） | `GET /settings`、`POST|PUT /settings`、`PUT /announcement`、`GET /tasks`、`POST /tasks/{task_id}/cancel`、`GET /logs`、`/logs/export`、`/storage/info`、`POST /cleanup/{target}` | 10 | 全部 `require_admin`；`admin.py:44`–`:306` |
| 配置与预设（`/api`） | `GET|PUT /admin/config/server`、`GET /admin/config/backups`、`POST /admin/config/restore`、`/admin/presets`、`GET /presets`、`/admin/presets`、`/admin/presets/{preset_id}`、`PUT|DELETE /admin/presets/{preset_id}`、`POST /presets/{preset_id}/apply`、`DELETE /config/user/preset`、`GET|PUT /config/user` | 14 | `/admin/*` 为管理员；预设应用与当前用户配置为会话用户；`config_management.py:65`–`:546` |
| 用户（`/api/admin/users`） | `POST /`、`GET /`、`GET|PUT|DELETE /{username}`、`PUT /{username}/permissions` | 6 | 全部 `require_admin`；创建成功 `201`，删除成功 `204`；`users.py:79`–`:457` |
| 用户组（`/api/admin/groups`） | `POST /`、`GET /`、`GET /{group_id}`、`PUT /{group_id}/rename`、`/{group_id}/config`、`DELETE /{group_id}` | 6 | 全部 `require_admin`；创建成功 `201`；`groups.py:87`–`:345` |
| 配额（`/api`） | `GET /quota/stats`、`GET /admin/quota/stats`、`POST /admin/quota/reset`、`/admin/quota/set-limits`、`GET /admin/quota/user/{user_id}` | 5 | 第一个为会话用户，其余管理员；`quota.py:89`–`:259` |
| 会话管理（`/sessions`） | `POST /`、`GET /`、`GET /{session_token}`、`DELETE /{session_token}`、`PUT /{session_token}/status`、`GET /access-log`、`/access-log/unauthorized` | 7 | 使用独立 `get_current_user`/会话安全服务；访问日志为管理员限制；创建成功 `201`；`sessions.py:50`–`:182` |
| 审计（`/audit`） | `GET /events`、`/export` | 2 | 全部 `require_admin`；`audit.py:43`、`:145` |
| 旧管理文件（无前缀） | `POST /upload/font`、`/upload/prompt`；`GET /prompts`、`/prompts/{filename}`；`DELETE /fonts/{filename}`、`/prompts/{filename}` | 6 | 全部 `require_admin`；与用户资源 `/api/resources/*` 不同；`files.py:36`–`:164` |

## 5. 传输与响应类型的静态边界

| 类型 | 已固定的契约证据 |
| --- | --- |
| JSON 翻译 | `/translate/json`、`/bytes`、`/image` 和它们的 stream 变体接收 `TranslateRequest`；`/translate/batch/json` 与 `/batch/images` 接收 `BatchTranslateRequest`。批量 Web 前端将图片读为 data URI，连同 `config`、`batch_size` 和 `filenames` JSON 发送到 `/translate/batch/images`。 |
| `multipart/form-data` | `/translate/with-form/*`、导出、导入和单项处理端点接收 `image` 与 JSON 字符串 `config`；一般流式图片端点还接收 `user_env_vars`。用户资源上传接收文件。 |
| 普通响应 | 翻译 JSON 端点声明 `TranslationResponse`；图片端点返回 PNG `StreamingResponse`；批量图片端点返回二进制流，使用 `X-Content-Type: application/zip` 标示 ZIP。导入、导出、处理和历史下载可返回图片、ZIP 或文件响应。 |
| 自定义流 | 前端解析每帧 `1-byte status + 4-byte big-endian length + payload`：状态 `0` 为结果字节、`1` 为 JSON 进度、`2` 为 JSON 错误。该实现与路由装饰器的 stream 描述一致。 |
| 下载票据 | 历史 API 先返回包含 `url`、文件名、剩余秒数和过期时间的票据对象；随后 `GET|HEAD /api/history/downloads/t/{ticket}` 返回 `FileResponse`。 |

来源：`routes/translation.py:148`、`:219`、`:322`、`:352`、`:435`、`:653`、`:981`；`static/script.js:2384`、`:2528`、`:2600`；`routes/history.py:131`、`:309`、`:569`。

## 6. 端口、监听和外部暴露

| 场景 | 源码固定值 | 文档时必须区分 |
| --- | --- | --- |
| `manga_translator web` | `--host` 默认 `MT_WEB_HOST` 或 `0.0.0.0`；`--port` 默认 `MT_WEB_PORT` 或 `8000`。 | `0.0.0.0` 是服务器监听所有 IPv4 接口，不是浏览器访问地址；本机通常访问 `http://127.0.0.1:8000` 或 `http://localhost:8000`，局域网客户端必须使用服务器实际 LAN 地址。对外可达性取决于防火墙、端口映射和网络环境，不能由静态源码断言。 |
| Uvicorn | 使用上述 `args.host`/`args.port`；`timeout_keep_alive=1800`，`timeout_graceful_shutdown=30`。 | 长连接超时是服务器参数，浏览器批量请求也单独设为 30 分钟。 |
| Docker CPU | 容器暴露/监听 `8000`；Compose 映射 `8000:8000`。 | 容器内端口与主机端口相同。 |
| Docker GPU | 容器仍监听 `8000`；Compose 映射 `8001:8000`。 | 主机访问入口是 `8001`，不是容器内默认 `8000`。 |
| CORS | `allow_origins=["*"]`、`allow_credentials=True`、全部方法和头。 | 这是源码配置，不代表浏览器实际会在所有 origin/credential 组合放行；必须通过浏览器预检运行验证。 |

来源：`manga_translator/args.py:23`–`:30`；`server/main.py:245`、`:413`；`packaging/Dockerfile:112`、`:123`；`packaging/docker-compose.yml:13`–`:17`、`:64`–`:68`。

## 7. 状态码矩阵

| 状态 | 触发范围（静态源码） | 来源 |
| --- | --- | --- |
| `200` | 没有显式成功状态的普通 JSON、HTML、流、文件和删除响应。 | FastAPI 默认；各路由装饰器见第 4 节。 |
| `201` | `POST /sessions/`、`POST /api/admin/users/`、`POST /api/admin/groups/` 成功创建。 | `sessions.py:61`；`users.py:79`；`groups.py:87` |
| `204` | `DELETE /api/admin/users/{username}` 成功。 | `users.py:378` |
| `400` | 请求字段、初始设置/注册验证、无批量图片、无效导入或资源/管理输入；部分历史/票据请求亦使用。 | `auth.py:362`；`translation.py:449`；`history.py:582`；`config_management.py:227` |
| `401` | 会话头缺失、令牌无效/过期、活动刷新失败、账号停用，或内部 `/register` 的 nonce 无效。 | `core/middleware.py:119`；`translation_auth.py:253`；`main.py:317` |
| `403` | 非管理员、未获功能/资源/历史权限，或注册被管理员关闭。 | `core/middleware.py:198`、`:246`；`translation_auth.py:345`；`auth.py:460` |
| `404` | favicon/文件/用户/组/会话/历史/下载票据/预设等对象不存在。 | `main.py:288`；`history.py:136`；`users.py:236`；`config_management.py:182` |
| `409` | 创建名称重复的管理员预设。 | `config_management.py:227` |
| `422` | 全局 `RequestValidationError` handler 返回 `detail` 和请求 body 字符串。 | `main.py:255`–`:273` |
| `429` | 登录或注册限速（附 `Retry-After`）、旧密码 gate 限速、并发任务数或每日配额超限。 | `auth.py:52`；`web.py:89`；`core/middleware.py:326`、`:365` |
| `499` | 批量翻译任务被强制取消或检测为取消。 | `translation.py:421`、`:518` |
| `500` | 服务未初始化、翻译/导入/导出、持久化、资源和管理服务的未处理或明确捕获失败。 | `auth.py:135`；`translation.py:527`；`resources.py:111`；`logs.py:249` |

## 8. 未能仅靠源码确认的运行时证据

- `uv run --no-sync python -m manga_translator web` 是否在当前依赖、模型和配置下成功启动，以及实际 Uvicorn 日志显示的 host/port、文档路径和 conditional `/locales` mount。
- 浏览器从本机、局域网和 Docker CPU/GPU 映射访问时的实际地址、Windows 防火墙/网络暴露、CORS 预检和 30 分钟连接行为。
- 登录、首次设置、注册启用/禁用、必须改密、过期/停用会话、旧 `/user/login` gate 与当前 `/auth/*` 主流程的界面可达性和各自 `401`/`403`/`429` 提示。
- 权限过滤是否同时正确反映在前端控件与最终翻译端点；API Key 编辑权限、上传/删除资源、预设、历史下载票据及管理员入口的完整交互。
- 7 个工作流的真实文件兼容性、批量 ZIP、流帧进度/错误、取消 `499`、日志轮询、历史写入及前端的结果/图册预览。
- i18n 的六种选择值、HTML 硬编码文本、缺失 locale 的英语回退、窄屏菜单和触摸图片查看器的实际显示。

这些事项应由后续有头模式、脱敏账号和最小可运行服务验证；本阶段不启动服务、不提交真实凭据、不截图，也不创建用户文档正文。

## 9. 静态核对命令

```powershell
rg -n -e '^router = APIRouter' -e '^logs_router = APIRouter' -e '^@(router|logs_router|app)\.(get|post|put|delete|patch|head)' manga_translator\server\routes manga_translator\server\main.py
rg -n -C 2 -e 'MT_WEB_HOST' -e 'MT_WEB_PORT' -e 'uvicorn.run' -e 'allow_origins' manga_translator\args.py manga_translator\server\main.py
rg -n -e 'fetch\(' -e 'X-Session-Token' -e 'localStorage' -e 'sessionStorage' manga_translator\server\static\script.js manga_translator\server\static\js\history-gallery.js
```

核对结果：第一条命令统计 148 个 `get/post/put/delete/patch/head` 装饰器；另有 `history.py:131` 的 `@router.api_route(..., methods=["GET", "HEAD"])`，因此本清单总计 150 个方法—路径映射。未执行服务启动、浏览器、Docker 或端到端请求；这些不是“通过”的替代证据。
