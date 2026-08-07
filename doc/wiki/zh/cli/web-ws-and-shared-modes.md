---
title: Web、WS 与 Shared 模式
description: 区分 web/ws/shared 三个服务子命令的用途、默认端口与内部协议边界
pageId: cli.web-ws-and-shared-modes
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# Web、WS 与 Shared 模式

当需要把翻译能力作为服务暴露出去时，`local` 之外还有三个并列的服务子命令：`web` 提供浏览器界面和 HTTP API，`shared` 提供受 nonce 保护的内部执行器 API，`ws` 作为客户端连接上游 WebSocket 调度器处理任务。本页固定这三种模式的启动方式、默认端口和内部协议边界，并说明哪些端点属于用户/开发者接口、哪些绝不能直接暴露。

`local` 的输入输出见[本地输入与输出](./local-input-output.md)，`web` 的浏览器操作见[Web 启动与访问](../web/launch-and-access.md)，HTTP API 契约和内部协议细节见开发者页面。

## 命令范围 {#feature-boundary}

- `web` 是唯一面向用户和开发者 HTTP 客户端的服务：同一进程提供 `GET /` 主工作区、`GET /admin` 管理界面、`/static/*` 静态资源和全部 JSON/表单/流式 API。默认监听 `0.0.0.0:8000`，可用 `MT_WEB_HOST`/`MT_WEB_PORT` 或 `--host`/`--port` 覆盖。
- `shared` 是内部 shared/API 实例：默认 `127.0.0.1:5003`，只暴露三个受控端点，用 `X-Nonce` 头保护，返回 pickle 序列化结果。浏览器不直接访问。
- `ws` 是内部 WebSocket 执行器：默认连接上游 `ws://localhost:5000`，用 `x-secret` 头认证，接收 protobuf 任务并回传状态。解析器虽然为它声明了 `--host 127.0.0.1 --port 5003`，但当前实现不消费这两个字段。
- 三种模式互不占用默认端口：web 是 `8000`，shared/ws 的解析器默认是 `5003`，ws 的实际连接目标是 `ws://localhost:5000` 上游。不要把 `5000`、`5003`、`8000` 混写。
- CLI 选项是源码中的固定中文帮助文案，不经过 i18n；与桌面设置共享的 GPU/ONNX/重试/日志开关映射到 `label_*` key，见[界面选项对照表](../reference/options-i18n-matrix.md)。

## 三种模式与端口 {#modes-and-ports}

| 模式 | 默认端点 | 角色 | 谁可以访问 |
| --- | --- | --- | --- |
| `web` | 监听 `0.0.0.0:8000`（`MT_WEB_HOST`/`MT_WEB_PORT` 或 `--host`/`--port` 可覆盖） | HTTP API + Web 界面 | 浏览器用户、HTTP API 客户端 |
| `shared` | 监听 `127.0.0.1:5003` | 内部 shared/API 实例 | 持有 nonce 的内部客户端 |
| `ws` | 不绑定监听端口；解析器默认 `127.0.0.1:5003`；连接上游 `ws://localhost:5000` | 内部 WebSocket 执行器 | 上游 WebSocket 调度器 |

```mermaid
flowchart LR
    subgraph User["用户侧"]
        B["浏览器 / HTTP 客户端"]
    end
    subgraph Server["web 模式（0.0.0.0:8000）"]
        W["Web UI + HTTP API 同一进程"]
    end
    subgraph Executor["内部执行器协议"]
        S["shared 实例（127.0.0.1:5003）"]
        WS["ws 执行器"]
        UP["上游调度器 ws://localhost:5000"]
    end
    B -->|"HTTP"| W
    W -.->|"旧路径：/register 注册后分发"| S
    S -->|"X-Nonce + pickle"| W
    UP -->|"WebSocket + protobuf"| WS
    WS -->|"GET source_image / PUT translation_mask"| UP
```

说明：shared 执行器分发是源码中保留的旧路径，当前 `run_server()` 强制 `args.start_instance=False`，web 模式不会自动拉起独立 shared 实例；`ws` 模式只作为客户端连向上游。

## 终端操作 {#terminal-operations}

### 启动 web 模式 {#start-web-mode}

```powershell
uv run --no-sync python -m manga_translator web
```

等价于 `--host 0.0.0.0 --port 8000`。启动后浏览器访问 `http://127.0.0.1:8000`（或服务器 LAN 地址）；`0.0.0.0` 表示监听所有网卡，默认对外暴露。完整启动步骤、Docker 和网络注意事项见[Web 启动与访问](../web/launch-and-access.md)。

### 启动 shared 模式 {#start-shared-mode}

```powershell
uv run --no-sync python -m manga_translator shared --host 127.0.0.1 --port 5003 --nonce <nonce>
```

把 `<nonce>` 换成调用方协商好的值；设置 nonce 后，所有 `/simple_execute/*` 和 `/execute/*` 请求必须带匹配的 `X-Nonce` 请求头，否则返回 `401`。

### 启动 ws 模式 {#start-ws-mode}

```powershell
uv run --no-sync python -m manga_translator ws --ws-url ws://localhost:5000
```

当前仓库缺少 `manga_translator/server/ws_pb2.py`（protobuf 生成模块），`listen()` 中的 `from ..server import ws_pb2` 会直接 `ImportError`，因此 `ws` 模式目前无法实际启动；`ws --help` 不受影响。这是源码差异，不是已验证的运行行为。

## 命令如何执行 {#runtime-behavior}

### web 模式 {#web-runtime}

`__main__.py` 把 `web` 分发给 `server.run_server(args)`：读取应用目录 `.env`、初始化服务器配置和数据目录，再以 `args.host`/`args.port` 启动 Uvicorn（`timeout_keep_alive=1800`，优雅关闭超时 30 秒）。启动时生成内部 nonce（`MT_WEB_NONCE` 或 `secrets.token_hex(16)`）并在日志打印。翻译任务在当前进程内通过 `request_extraction.py` 的 `_run_translate_sync`/`_run_translate_batch_sync` 在翻译线程中执行；源码保留 shared 执行器注册/分发旧路径，但 `run_server()` 强制 `args.start_instance=False`，不会自动拉起独立 shared 实例，外部实例可通过 `POST /register`（带 `X-Nonce`）注册。

### shared 内部协议 {#shared-protocol}

`MangaShare` 包装一个 `MangaTranslator`，用 FastAPI 暴露三个端点：

| 端点 | 行为 |
| --- | --- |
| `GET /is_locked` | 返回 `{"locked": true/false}` |
| `POST /simple_execute/{method_name}` | 同步执行，成功返回 pickle 字节流；失败返回 4xx/5xx |
| `POST /execute/{method_name}` | 流式执行，返回 `application/octet-stream`，帧格式为 1 字节状态 + 4 字节大端长度 + 载荷 |

所有执行端点在处理前依次做：nonce 校验（设置 nonce 后 `X-Nonce` 必须匹配，否则 `401`）、方法白名单（只允许 `translate`、`translate_batch`，否则 `403`）、方法存在性（`404`）、非阻塞锁（忙时 `429`）。请求 JSON 中图片是 PNG base64，配置是 `Config.model_dump(mode="json")`；非法图片/配置/批量请求分别返回 `422`。流式帧状态：`0` 结果（pickle）、`1` 进度（UTF-8 状态串）、`2` 错误。结果对象带 `use_placeholder` 时，只传一个 1×1 白色占位图片，避免传输大图。

```mermaid
flowchart LR
    C["内部客户端"] -->|"POST /execute/translate + X-Nonce"| N{"nonce 匹配?"}
    N -->|否| E401["401"]
    N -->|是| W{"方法在白名单?"}
    W -->|否| E403["403"]
    W -->|是| L{"锁可用?"}
    L -->|否| E429["429"]
    L -->|是| R["执行 translate/translate_batch"]
    R --> S["StreamingResponse 帧 1/0/2"]
```

### ws 内部协议 {#ws-protocol}

`MangaTranslatorWS` 继承 `MangaTranslator`，`listen()` 以客户端身份连接 `--ws-url` 上游：`websockets.connect(url, extra_headers={'x-secret': secret}, max_size=1_000_000)`。secret 来自 `ws_secret` 参数或 `WS_SECRET` 环境变量（默认为空串）。消息是 `ws_pb2.WebSocketMessage` protobuf：上游发来 `new_task`（含任务 id、`source_image`、`translation_mask` 和翻译参数），执行器回发 `status` 和 `finish_task`。

单任务流程：`pending` → HTTP GET `source_image` 下载（失败发 `error-download`）→ `downloading` → 翻译 → `preparing` → `saving`（结果转 PNG，verbose 时另存 `result/<id>/ws_final.png`）→ `uploading` → HTTP PUT `translation_mask` 上传（失败发 `error-upload`）→ `finish_task(success, has_translation_mask)`。状态帧用 0.2 秒节流器合并发送，任务用 `PriorityLock` 调度；图片长宽超过 1200 像素时强制 `upscale_ratio=1`。Windows 上会先调用 `WSAStartup` 并设置 `ProactorEventLoopPolicy`。

```mermaid
flowchart LR
    T["new_task"] --> P["pending"]
    P --> D{"GET source_image"}
    D -->|"失败"| ED["error-download"]
    D -->|"成功"| G["downloading"]
    G --> R["翻译（含 preparing）"]
    R --> S["saving"]
    S --> U{"PUT translation_mask"}
    U -->|"失败"| EU["error-upload"]
    U -->|"成功"| F["finish_task"]
```

## 使用限制 {#dependencies-and-conflicts}

- 端口边界：web 默认 `8000`，shared/ws 解析器默认 `5003`，ws 连接目标是 `ws://localhost:5000`；三者不互相覆盖。端口被占用时启动失败，属环境问题。
- `ws --host/--port/--nonce` 在解析器中存在，但 `MangaTranslatorWS` 当前不消费这些字段；不要根据帮助文本推断 ws 会监听 `5003`。
- 当前仓库缺少 `ws_pb2.py`，补齐这个生成模块前无法启动 `ws` 模式。
- shared/ws 是内部协议：不要用浏览器直接访问，不要暴露到公网；nonce/secret 和 pickle 反序列化都有安全风险，日志里打印的 nonce 不得复制进公开报告。
- 这里不读取或展示真实 `.env`、`WS_SECRET`、nonce、API key、令牌、用户名或私有路径。
