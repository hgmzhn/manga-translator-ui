# Web 服务与 Docker

`web` 模式在同一个 FastAPI 进程中提供用户页面、管理页面和 REST API，适合局域网共享、远程服务器和容器部署。桌面端仍是本地批量处理与可视化编辑的完整入口，两者功能范围并不完全相同。

## 直接启动

源码环境：

```bash
uv run --no-sync python -m manga_translator web --host 127.0.0.1 --port 8000
```

需要局域网或反向代理访问时监听所有网卡：

```bash
uv run --no-sync python -m manga_translator web --host 0.0.0.0 --port 8000
```

常用地址：

| 地址 | 内容 |
| --- | --- |
| `http://HOST:8000/` | 用户翻译界面 |
| `http://HOST:8000/admin` | 管理界面 |
| `http://HOST:8000/static/login.html` | 登录和首次管理员设置 |
| `http://HOST:8000/docs` | FastAPI OpenAPI 交互文档 |
| `http://HOST:8000/api` | 服务信息与主要端点摘要 |

监听 `0.0.0.0` 只是让服务可被其他主机访问，浏览器中仍应使用服务器实际 IP 或域名。

## 首次管理员设置

当前源码不会创建固定的 `admin/admin123` 默认账号。第一次启动时，服务器发现 `accounts.json` 中没有用户，会提示访问登录页创建第一个管理员。

1. 打开 `http://HOST:8000/static/login.html`。
2. 输入至少 2 个字符的用户名。
3. 输入至少 6 个字符的密码。
4. 创建完成后会同时建立登录会话。

也可以用 API 完成初始化：

```bash
curl http://127.0.0.1:8000/auth/status

curl -X POST http://127.0.0.1:8000/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"change-this-password"}'
```

`/auth/setup` 只在系统没有任何用户时有效。会话默认 60 分钟过期，并持久化到服务端数据目录。

> Compose 示例中的 `MANGA_TRANSLATOR_ADMIN_PASSWORD=admin123456` 会写入旧的服务管理密码设置，但不会创建登录账号，也不会替代 `/auth/setup`。公开部署前应修改或移除这个示例值，并通过首次设置创建独立管理员账号。

## API 鉴权与最小调用

登录接口返回 `token`：

```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"change-this-password"}'
```

翻译接口必须在 `X-Session-Token` 请求头中携带该 token。上传图片并接收 PNG：

```bash
curl -X POST http://127.0.0.1:8000/translate/with-form/image \
  -H "X-Session-Token: YOUR_SESSION_TOKEN" \
  -F "image=@page.png" \
  -F 'config={"translator":{"translator":"openai","target_lang":"CHS"}}' \
  --output translated.png
```

Windows PowerShell 中建议显式使用 `curl.exe`，避免旧版 PowerShell 把 `curl` 解析为其他命令。

配置结构与桌面端共用 `Config` 模型。准确的请求/响应结构应以当前进程的 `/docs` 为准，尤其是流式、批量、导入导出端点。

## 主要 API 分组

`/translate` 路由当前包含：

- `/translate/json`、`/translate/bytes`、`/translate/image`：JSON 请求。
- `/translate/with-form/json`、`/translate/with-form/bytes`、`/translate/with-form/image`：表单上传。
- 对应的 `/stream`：流式进度与结果。
- `/translate/batch/json`、`/translate/batch/images`：批量处理。
- `/translate/export/original`、`/translate/export/translated`：导出文本和 JSON。
- `/translate/upscale`、`/translate/colorize`、`/translate/inpaint`：单独处理。
- `/translate/import/json`、`/translate/import/txt`：导入翻译再渲染。

管理端还提供账号、用户组、权限、配额、会话、审计、历史、日志、资源、服务配置和预设接口。不要绕过管理 UI 猜测管理端 payload；在 `/docs` 查看当前 schema。

## Web 与桌面端差异

Web 配置暴露的流程代码是：

```text
normal, export_trans, export_raw, import_trans, colorize, upscale, inpaint
```

桌面端还包含“只翻译 JSON”和“替换翻译”等本地文件工作流，并提供完整可视化编辑器、目录工作区和批量 JSON 工具。因此：

- 需要浏览器上传、用户权限、队列和历史记录：使用 Web。
- 需要压缩包导入、替换翻译、JSON 往返或精细编辑：使用桌面端。
- 需要无人值守地处理本地目录：使用 Local CLI。

桌面流程说明见 [[工作流详解|03-工作流详解]]。

## 环境变量

Web 启动参数可以通过环境变量配置：

| 环境变量 | 含义 | 默认 |
| --- | --- | --- |
| `MT_WEB_HOST` | 监听地址 | `0.0.0.0` |
| `MT_WEB_PORT` | 监听端口 | `8000` |
| `MT_USE_GPU` | 是否启用 GPU | false |
| `MT_DISABLE_ONNX_GPU` | 强制 ONNX 使用 CPU provider | false |
| `MT_MODELS_TTL` | 模型最后使用后保留秒数，`0` 永不自动卸载 | `0` |
| `MT_RETRY_ATTEMPTS` | 服务级重试覆盖，`-1` 无限重试 | 请求配置 |
| `MT_VERBOSE` | 详细日志 | false |
| `MT_WEB_NONCE` | 内部实例注册 nonce | 启动时随机生成 |

API 服务启动时会从应用根目录 `.env` 读取供应商密钥、Base URL 和模型名。密钥字段与预设见 [[API 管理|05-API管理]]。

管理员可以配置 API Key 策略：是否要求用户自带密钥、是否允许使用服务器密钥，以及是否将用户密钥保存到服务器。公开服务应根据信任边界收紧这些选项。

## Docker CPU 部署

Compose 文件位于 `packaging/docker-compose.yml`，其 build context 指向项目根目录。进入 `packaging` 后启动 CPU 服务：

```bash
cd packaging
docker compose up --build -d manga-translator-cpu
docker compose logs -f manga-translator-cpu
```

访问 `http://127.0.0.1:8000/`，再按“首次管理员设置”创建账号。

停止服务：

```bash
docker compose stop manga-translator-cpu
```

删除容器和网络但保留绑定目录中的数据：

```bash
docker compose down
```

## Docker NVIDIA GPU 部署

宿主机必须先能使用 NVIDIA 驱动和 NVIDIA Container Toolkit。启动 GPU 服务：

```bash
cd packaging
docker compose up --build -d manga-translator-gpu
docker compose logs -f manga-translator-gpu
```

Compose 将容器 `8000` 映射到宿主机 `8001`，访问 `http://127.0.0.1:8001/`。该服务设置 `MT_USE_GPU=true`，并请求所有可用 NVIDIA GPU。

项目 Dockerfile 支持 `BUILD_TYPE=cpu` 或 `gpu`，容器内使用 Python 3.12 和对应 `uv` dependency group。AMD/Metal 不在当前 Compose 服务定义中。

## Docker 数据持久化

Compose 默认把以下宿主目录挂载到容器：

| 宿主目录（相对 `packaging/`） | 容器目录 | 内容 |
| --- | --- | --- |
| `data/fonts` | `/app/fonts` | 字体 |
| `data/dict` | `/app/dict` | 提示词和规则 |
| `data/result` | `/app/result` | 普通结果目录 |
| `data/models` | `/app/models` | 本地模型缓存 |
| `data/logs` | `/app/logs` | 预留日志目录 |
| `data/server` | `/app/manga_translator/server/data` | 账号、权限、会话、历史和用户资源 |
| `data/config` | `/app/config` | 配置模板和用户配置 |

entrypoint 检测到 `config`、`fonts`、`dict` 或服务端数据挂载目录为空时，会从镜像内备份恢复默认内容。因此第一次挂载空目录是允许的。

服务器在 Web 管理界面保存 `.env` 时，如需在重建容器后保留，先创建 `packaging/data/app.env`，再取消 Compose 中以下挂载的注释：

```yaml
- ./data/app.env:/app/.env
```

不要把真实 `.env`、`accounts.json` 或会话文件提交到 Git。

## 反向代理与安全

当前 FastAPI 配置允许任意 CORS origin、method 和 header。账号系统、权限和配额能限制应用功能，但不等同于网络边界。公网部署至少应：

1. 在反向代理终止 HTTPS。
2. 用防火墙限制源地址或增加外层认证。
3. 不直接暴露管理端给不受信任网络。
4. 替换示例密码并保护 `.env` 与 `manga_translator/server/data/`。
5. 结合显存和内存设置并发、上传限制与配额。

服务异常、401、端口冲突或容器 GPU 问题见 [[故障排查|13-故障排查]]。
