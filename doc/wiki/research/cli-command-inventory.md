# CLI 正式子命令、参数与实际 `--help` 清单

> Phase 0 数据源；调查日期：2026-08-06。
>
> 本清单只固定正式顶层入口 `python -m manga_translator` 的命令契约。它不是终端用户页面，也不覆盖 HTTP、WebSocket 或 shared 协议细节；这些内容分别由后续 Web 和开发者页面处理。

## 固定入口与分发

在本仓库中，使用项目受管运行时的正式调用形式为：

```powershell
uv run --no-sync python -m manga_translator <mode> [options]
```

顶层解析器只注册下列四个子命令。`manga_translator.__main__` 解析完成后，将同一个 `args` 命名空间分发到相应执行链，因此四者均是本清单中的正式子命令。

| 子命令 | 用途 | 顶层分发目标 | 默认网络端点（如适用） |
| --- | --- | --- | --- |
| `local` | 本地图片/文件夹翻译 | `mode.local.run_local_mode(args)` | 不监听端口 |
| `web` | HTTP API 和 Web 界面服务器 | `server.run_server(args)` | `0.0.0.0:8000`，可由 `MT_WEB_HOST` / `MT_WEB_PORT` 改写 |
| `ws` | 内部 WebSocket 后端 | `MangaTranslatorWS(...).listen(...)` | 本地监听 `127.0.0.1:5003`；上游地址 `ws://localhost:5000` |
| `shared` | 内部 shared/API 实例 | `MangaShare(...).listen(...)` | `127.0.0.1:5003` |

`local` 是唯一支持省略模式的正式快捷写法：当第一个用户参数不是上述四个模式，且参数列表含 `-i` 或 `--input` 时，`parse_args()` 会在解析前插入 `local`。因此 `python -m manga_translator -i image.png` 等价于显式 `local`；任意位置参数本身不会触发该回退。

顶层没有可用于四种模式的通用业务选项。`python -m manga_translator --help` 只列出模式；应使用 `<mode> --help` 获取选项。

## 正式参数清单

所有模式均由 `argparse` 自动提供 `-h, --help`。下表的“默认值”来自解析器源码；Web 模式中的环境变量默认值会在进程启动时求值。

### `local`

| 选项 | 类型 / 默认值 | 实际 `--help` 和解析语义 |
| --- | --- | --- |
| `-i INPUT [INPUT ...]`, `--input INPUT [INPUT ...]` | 必填，1 个或多个字符串 | 输入图片或文件夹路径。 |
| `-o OUTPUT`, `--output OUTPUT` | 字符串；`None` | 输出目录。 |
| `--config CONFIG` | 字符串；`None` | 指定配置文件路径。 |
| `-v`, `--verbose` | 开关；`False` | 启用详细日志。 |
| `--overwrite` | 开关；`False` | 覆盖已存在文件。 |
| `--use-gpu` | 开关；`None` | 显式覆盖配置中的 GPU 值。 |
| `--disable-onnx-gpu` | 开关；`None` | 显式覆盖配置中的 ONNX Runtime GPU 值。 |
| `--format FORMAT` | 字符串；`None` | 输出格式覆盖；帮助实际列出 `png/jpg/jpeg/jfif/webp/avif/bmp/tiff/tif/heic/heif`，解析阶段不设置 `choices`。 |
| `--batch-size BATCH_SIZE` | 整数；`None` | 批量大小覆盖。 |
| `--attempts ATTEMPTS` | 整数；`None` | 翻译失败重试次数覆盖；`-1` 表示无限重试。 |
| `--subprocess` | 开关；`False` | 使用子进程管理路径。 |
| `--memory-limit MEMORY_LIMIT` | 整数；`0` | 子进程绝对内存阈值（MB）；`0` 表示不限制。 |
| `--memory-percent MEMORY_PERCENT` | 整数；`0` | 子进程系统内存百分比阈值；`0` 表示不限制。 |
| `--batch-per-restart BATCH_PER_RESTART` | 整数；`0` | 子进程每处理 N 张后重启；`0` 表示不限制。 |

### `web`

| 选项 | 类型 / 默认值 | 实际 `--help` 和解析语义 |
| --- | --- | --- |
| `--host HOST` | 字符串；`MT_WEB_HOST` 或 `0.0.0.0` | 服务器主机。 |
| `--port PORT` | 整数；`MT_WEB_PORT` 或 `8000` | 服务器端口。 |
| `--use-gpu` | 开关；`MT_USE_GPU` 为 `true`、`1`、`yes` 或 `on` 时为真 | 使用 GPU。 |
| `--disable-onnx-gpu` | 开关；`MT_DISABLE_ONNX_GPU` 采用同一真值规则 | 禁用 ONNX Runtime GPU。 |
| `--models-ttl MODELS_TTL` | 整数；`MT_MODELS_TTL` 或 `0` | 上次使用后保留模型的秒数；`0` 表示永久。 |
| `--retry-attempts RETRY_ATTEMPTS` | 整数；未设 `MT_RETRY_ATTEMPTS` 时为 `None` | 请求失败重试次数；`-1` 表示无限重试。 |
| `-v`, `--verbose` | 开关；`MT_VERBOSE` 为 `true`、`1` 或 `yes` 时为真 | 显示详细日志。 |

### `ws`

| 选项 | 类型 / 默认值 | 实际 `--help` 和解析语义 |
| --- | --- | --- |
| `--host HOST` | 字符串；`127.0.0.1` | 本地 WebSocket 服务主机。 |
| `--port PORT` | 整数；`5003` | 本地 WebSocket 服务端口。 |
| `--nonce NONCE` | 字符串；`None` | 内部通信 nonce。 |
| `--ws-url WS_URL` | 字符串；`ws://localhost:5000` | 上游 WebSocket 服务器 URL。 |
| `--models-ttl MODELS_TTL` | 整数；`0` | 模型内存 TTL；`0` 表示永久。 |
| `--retry-attempts RETRY_ATTEMPTS` | 整数；`None` | 翻译失败重试次数；`-1` 表示无限重试。 |
| `-v`, `--verbose` | 开关；`False` | 显示详细日志。 |
| `--use-gpu` | 开关；`False` | 使用 GPU。 |
| `--disable-onnx-gpu` | 开关；`MT_DISABLE_ONNX_GPU` 采用顶层真值规则 | 禁用 ONNX Runtime GPU。 |

### `shared`

| 选项 | 类型 / 默认值 | 实际 `--help` 和解析语义 |
| --- | --- | --- |
| `--host HOST` | 字符串；`127.0.0.1` | 内部 API 服务主机。 |
| `--port PORT` | 整数；`5003` | 内部 API 服务端口。 |
| `--nonce NONCE` | 字符串；`None` | 内部 API 通信 nonce。 |
| `--models-ttl MODELS_TTL` | 整数；`0` | 模型内存 TTL；`0` 表示永久。 |
| `--retry-attempts RETRY_ATTEMPTS` | 整数；`None` | 翻译失败重试次数；`-1` 表示无限重试。 |
| `-v`, `--verbose` | 开关；`False` | 显示详细日志。 |
| `--use-gpu` | 开关；`False` | 使用 GPU。 |
| `--disable-onnx-gpu` | 开关；`MT_DISABLE_ONNX_GPU` 采用顶层真值规则 | 禁用 ONNX Runtime GPU。 |

## 实际帮助验证

在仓库根目录执行了下列无副作用命令。每条均退出 `0`；未启动服务器、翻译、网络请求或模型下载。

| 命令 | 退出码 | 实际输出结论 |
| --- | --- | --- |
| `uv run --no-sync python -m manga_translator --help` | `0` | usage 为 `__main__.py [-h] {web,local,ws,shared} ...`；只列四个模式和顶层帮助。 |
| `uv run --no-sync python -m manga_translator local --help` | `0` | 列出本稿 `local` 表中的 13 个业务选项和帮助选项。 |
| `uv run --no-sync python -m manga_translator web --help` | `0` | 列出本稿 `web` 表中的 7 个业务选项和帮助选项。 |
| `uv run --no-sync python -m manga_translator ws --help` | `0` | 列出本稿 `ws` 表中的 9 个业务选项和帮助选项。 |
| `uv run --no-sync python -m manga_translator shared --help` | `0` | 列出本稿 `shared` 表中的 8 个业务选项和帮助选项。 |
| `uv run --no-sync python -m manga_translator -i placeholder.png --help` | `0` | usage 变为 `__main__.py local ...`，确认 `-i` 触发隐式 `local`；`--help` 阶段未校验该占位路径。 |

实际输出与解析器定义一致，没有遇到帮助阶段的环境阻塞。帮助文本的两个限制需要保留：

- 顶层使用默认 `argparse` formatter，不会把所有子命令选项展开到根帮助中，也不会自动打印解析后的默认值。
- Web 选项的帮助文字写的是源码中的基准值（例如 `0.0.0.0`、`8000`）；真实默认值仍可能被启动时的 `MT_*` 环境变量改写，因此不能只从帮助文本反推当次运行的生效值。

## 与其他解析器的差异和运行时边界

`uv run --no-sync python -m manga_translator.mode.local --help` 也返回 `0`，但这是独立模块入口，**不属于本稿的正式顶层子命令契约**。它的 `local.py` 解析器与正式 `args.py` 不同：额外公开 `--resume`、`--concurrent`，没有正式顶层的 GPU、ONNX、格式、批量大小和 attempts 选项，且内存参数默认值为 `8000`、`80`、`50`，而正式 `local` 为 `0`、`0`、`0`。`__main__.py` 不调用这份解析器，后续用户文档不得将这些差异混入正式顶层清单。

在当前源码中还有以下不能由帮助输出表达的边界，后续 CLI 页面和验证应保留：

- `__main__.py` 在解析参数前尝试导入 `torch`。本次受管环境中帮助命令成功；在缺少或 DLL 不兼容的 PyTorch 环境中，连 `--help` 也可能在解析前失败。
- `local` 的非子进程路径会将显式 `--use-gpu`、`--disable-onnx-gpu`、`--format`、`--batch-size` 和 `--attempts` 写入 `cli_config`。子进程路径只显式写入前两个 GPU 值，再把原配置交给 `translate_with_subprocess`；因此 `--subprocess` 与 `--format`、`--batch-size` 或 `--attempts` 组合时，帮助所称“覆盖配置文件”的行为尚未进入该分支。这是源码差异，不是已完成的运行验证。
- `--memory-limit`、`--memory-percent` 和 `--batch-per-restart` 仅在 `--subprocess` 路径传给 `translate_with_subprocess`。不启用子进程时不会消费这些值。
- 独立 `local.py` 解析器声明的 `--resume` 没有从 `run_local_mode()` 传递给 `translate_with_subprocess(..., resume=...)`；其帮助存在不等于该恢复行为已经接通。
- `manga_translator/server/args.py` 的另一套 `parse_arguments()` 未接入顶层分发；`server/main.py` 的直接模块守卫还导入不存在的 `manga_translator.args.parse_arguments`（正式顶层定义的是 `parse_args`）。它不能替代正式 `web` 命令，除非后续修复并重新验证。

完整服务启动、真实输入翻译、模型/API 依赖、端口占用和内部协议均未在本任务启动；它们属于后续功能与运行验证，不以本次 `--help` 成功作为通过证据。

## 源码依据

| 文件 | 核对内容 |
| --- | --- |
| `manga_translator/args.py:12` | 顶层四个子解析器、全部正式选项、默认值和隐式 `local` 规则。 |
| `manga_translator/__main__.py:34` | 参数解析前的 PyTorch 导入，以及四个模式的实际分发。 |
| `manga_translator/mode/local.py:125` | 非子进程路径的 CLI 覆盖写入。 |
| `manga_translator/mode/local.py:621` | 子进程分支只写入 GPU/ONNX 覆盖并传入三个内存阈值。 |
| `manga_translator/mode/local.py:28` | 独立模块解析器、其额外选项和直接执行守卫。 |
| `manga_translator/mode/subprocess_manager.py:223` | `resume` 参数的下游接口存在，但正式调用未传入。 |
| `manga_translator/server/main.py:384` | `web` 分发的服务器运行函数与直接模块守卫的导入差异。 |
| `manga_translator/mode/ws.py:19`、`manga_translator/mode/share.py:45` | `ws`、`shared` 的构造目标和连接字段。 |
| `manga_translator/image_formats.py:5` | `local --format` 帮助列出的单一格式来源。 |

本调查对应下列 SHA-256 源码快照，供后续页面差异追踪：

| 文件 | SHA-256 |
| --- | --- |
| `manga_translator/__main__.py` | `970554CB57725CB77C93BC66CB4FD2A7B40FCB59E0B25E2C008666EB04AC4266` |
| `manga_translator/args.py` | `8CA0B1081FDA09F2A9FA04AB1C6FE3CBD27F4F343BA384CEAA43EA8AED651021` |
| `manga_translator/mode/local.py` | `8B078F34A1628B9D386A0074271358A716474B0991D285DDF27049F1D76E50BB` |
| `manga_translator/mode/subprocess_manager.py` | `64036DF3CD930F6CE3CCFEE9961B42395F6A37519147AB534303F5E981B571BC` |
| `manga_translator/server/main.py` | `25C9B4C42226D9597D4CE1FCF76EAF8814BC62591EB6E29E422A33C4962CFFA8` |
| `manga_translator/mode/ws.py` | `3D846D7E2CB8C7A2E04E2510C21D4E831A003B258FE4E1F02FA4F2903F44B6A6` |
| `manga_translator/mode/share.py` | `A555F0A46198C815B89B1F043F97880257CCE55C2E3619FFE713D42D7B715D6A` |
| `manga_translator/image_formats.py` | `E87B45A9817E9470317EE2A924097C2C701932B4E08C6DBD23D34C6CB59963D6` |
