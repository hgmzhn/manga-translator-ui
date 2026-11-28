# MTUI-MCP — `manga-translator-ui` 内置翻译后精修与自动化 MCP 服务

`mtui_mcp` 是内置于 **[manga-translator-ui](https://github.com/hgmzhn/manga-translator-ui)** 的 **Model Context Protocol (MCP)** 服务，让具备多模态视觉与推理能力的 AI Agent（**Antigravity**、**Codex**、**Claude Code**、**Cursor** 等）能够在 `manga-translator-ui` 运行期间或初翻完成后无缝介入，自动化执行：

1. **译文校对与术语统一**（整话台词按漫画阅读顺序提取、漏翻假名/韩文检测、术语表读写、跨页批量替换）
2. **图片修复检查与补充（消字补擦 & 误擦还原）**（CV 自动检测气泡残字/漏擦假名、像素级坐标标尺放大镜、`lama_large` / `lama_mpe` 增量局部 GPU 修复、毛玻璃模糊 `blur` 修复、误擦分镜框线/网点原图无损像素还原、仿制贴片 `paste_overlays`）
3. **专业嵌字排版与富文本精修**（字号大小、字体选择与生僻字缺字预检、`richtext.v1` 行内加粗/斜体/着重号/Ruby 注音与 `rubyInherit`/TCY 纵中横/双层描边/发光/高斯·定向·径向·动态模糊 `blur`、四点透视文本框 `white_frame_quad_local`、自动吸取原图字色与描边色、横竖排切换、多框对齐分布、**100% 由 Agent 结合语义与气泡视觉自行判断断句**、原生 Qt6 离屏重渲染、闭环光学居中与分层 PSD 导出）
4. **桌面 GUI 实时协同（`mtui_live_gui_session`）**（桌面端运行时自动开启本地 IPC 会话桥，支持查询打开页面、等待翻译进度、切页、撤销/重做与保存）

---

## 🏗️ 架构设计与双通道运行模式

`mtui_mcp` 同时支持 **桌面启动自动伴随（Streamable HTTP + SSE）** 与 **按需独立进程（STDIO / CLI）** 两种接入方式，且修改后的工程在 `manga-translator-ui` 编辑器中打开 **100% 兼容、渲染逐像素一致**：

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 模式 A：随桌面 GUI 自动启动（默认开启，零配置即连）                                               │
│ desktop_qt_ui/main.py ──▶ McpService                                                            │
│   ├─ EmbeddedMcpHttpServer (127.0.0.1:8765)                                                     │
│   │    ├─ POST/GET http://127.0.0.1:8765/mcp     (Streamable HTTP)                              │
│   │    ├─ GET      http://127.0.0.1:8765/sse     (SSE + /messages/)                             │
│   │    └─ GET      http://127.0.0.1:8765/health  (JSON 健康检查)                                 │
│   └─ McpGuiBridgeServer (Named Pipe / Unix Socket + ~/.cache/mtui-mcp/*.key 鉴权)               │
│        └─ 驱动 mtui_live_gui_session 线程安全访问 Qt 主线程画布/历史栈                             │
├─────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 模式 B：按需独立 CLI / STDIO 启动（无需打开桌面窗口）                                             │
│ python -m mtui_mcp  或  python -m manga_translator mcp                                          │
└───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                │
        ┌───────────────────────────────────────┴────────────────────────────────────────┐
        ▼ (毫秒级本地直通处理)                                                             ▼ (127.0.0.1 按需懒加载热进程 RPC)
  - 双向路径解析 (原图 / manga_translator_work / last_output_path / -translated)     MTUI Warm Engine Worker
  - 原子级 JSON 读改写 (全量保留 mask_raw / upscale_ratio / overlays / 透视四点 quad)  - Qt6 Offscreen 字体引擎与原生 ExportService 渲染
  - 自动快照备份与一键回滚/清理 (.mtui_mcp_snapshots / initial_before_mcp)             - lama_large / lama_mpe 局部增量裁窗 GPU 修复 (<1s) 与 blur 修复
  - richtext.v1 严格构建与多段差量样式同步 (保留未改字符的 Ruby/TCY/加粗/变色/模糊)     - 锁定禁用一切原生自动换行，100% 忠实渲染 Agent 决定的 `\n` 断句
  - QA 审计 (漏翻假名 / YuzuMarker 4x 字体核验 / 字体缺字检测 / CV 残墨与破框检测)      - 单框重跑指定 OCR 模型 / 翻译器 / 完整 CLI 翻译流水线
  - 带世界像素坐标标尺 (X, Y) 的多模态三联对比图与区域放大镜 (Loupe)                   - 分层可编辑 Photoshop (.psd / 仅 .jsx 脚本) 导出
```

### 关键底层细节保障
- **严守全量保留写回**：修改 `manga_translator_work/json/<name>_translations.json` 时采用带 Windows 文件锁重试的临时文件原子替换（`tempfile` + `os.replace` 重试），完整保留 `mask_raw`、`mask_is_refined`、`original_width/height`、`upscale_ratio`、`colorizer`、`last_export_dir`、`last_output_path`、`paint_overlay`、`stamp_overlay`、`paste_overlays`，以及区域级四点透视几何字段 `white_frame_quad_local` 与 `render_box_quad_local`。
- **真正的富文本样式协议（`richtext.v1`）**：`manga-translator-ui` 渲染器中真正生效的加粗、斜体（`15°`）、下划线、删除线、着重号、Ruby 注音（含 `rubyInherit` 继承开关）、TCY 纵中横、外描边、发光，以及模糊样式（`blur`、`blurAngle`、`blurRadial`、`blurMotion`、`blurMotionDistance`）全部通过 `translation_rich`（`richtext.v1`）驱动；内置完整的 `richtext.v1` 校验生成、默认值重置合并与纯文本修改差量同步器。
- **懒加载零闲置显存 & STDIO 零污染隔离**：桌面启动时仅开启轻量 HTTP/IPC 监听（0 MB 额外显存）；首次调用重度渲染/修图/OCR 工具时才按需拉起受控子进程（`127.0.0.1` 随机端口 + 256-bit 会话令牌），彻底杜绝底层库日志污染 `stdout`，常驻预热后单页重渲染仅需 **0.15s ~ 0.4s**，并在桌面退出时自动平滑回收。

---

## 🚀 快速接入指南

### 方式一：连接随桌面端自动启动的 HTTP / SSE 服务（推荐）

正常启动 `manga-translator-ui` 桌面界面（`Win-Start.bat`、`Unix-Start.sh` 或 `python desktop_qt_ui/main.py`）后，内置 MCP 服务默认自动监听：
- **Streamable HTTP 端点**：`http://127.0.0.1:8765/mcp`
- **SSE 端点**：`http://127.0.0.1:8765/sse`
- **健康检查**：`http://127.0.0.1:8765/health`

可在桌面端 **设置 → 通用设置** 中随时开关 MCP 服务或修改端口（对应 `config/config.json` 中的 `app.mcp_server_enabled` / `app.mcp_server_port`），也可通过桌面启动参数 `--mcp` / `--no-mcp`、`--mcp-host`、`--mcp-port` 或环境变量 `MTUI_MCP_ENABLED`、`MTUI_MCP_HOST`、`MTUI_MCP_PORT` 覆盖。

#### Antigravity / Cursor / Claude Code（Streamable HTTP 配置）
```json
{
  "mcpServers": {
    "mtui-mcp": {
      "serverUrl": "http://127.0.0.1:8765/mcp"
    }
  }
}
```
> 若客户端使用 `url` 字段（如 Cursor / 部分 MCP 客户端），填写 `"url": "http://127.0.0.1:8765/mcp"`（或 SSE `"url": "http://127.0.0.1:8765/sse"`）。

---

### 方式二：按需 STDIO 或独立命令行启动（无需打开桌面窗口）

在不启动桌面 GUI 的无头环境下，MCP 客户端可通过 `stdio` 直接拉起 `mtui_mcp`（自动识别当前仓库根目录与 `.venv` / `packaging/python` 解释器）：

```bash
# 默认 STDIO 模式（供 MCP 客户端自动拉起）
python -m mtui_mcp
python -m manga_translator mcp

# 独立启动 Streamable HTTP 或 SSE 服务
python -m mtui_mcp --transport streamable-http --host 127.0.0.1 --port 8765
python -m manga_translator mcp --transport sse --host 127.0.0.1 --port 8765
```

#### Antigravity / Claude Desktop（STDIO 配置）
> 由于仓库 `pyproject.toml` 设置了 `package = false`（项目源码不安装进 `site-packages`），从其他项目目录拉起 STDIO 子进程时需在 `env` 中设置 `PYTHONPATH` 指向 `<manga-translator-ui>` 仓库根目录。

```json
{
  "mcpServers": {
    "mtui-mcp": {
      "command": "/path/to/manga-translator-ui/.venv/Scripts/python.exe",
      "args": ["-m", "mtui_mcp"],
      "env": {
        "PYTHONPATH": "/path/to/manga-translator-ui",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```
> 便携版（Portable Release）可将 `command` 指向 `<manga-translator-ui>/packaging/python/python.exe`；Linux/macOS 指向 `<manga-translator-ui>/.venv/bin/python`（`PYTHONPATH` 同样填 `<manga-translator-ui>`）。

#### OpenAI Codex（`~/.codex/config.toml`）
```toml
[mcp_servers.mtui-mcp]
command = "/path/to/manga-translator-ui/.venv/Scripts/python.exe"
args = ["-m", "mtui_mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.mtui-mcp.env]
PYTHONPATH = "/path/to/manga-translator-ui"
PYTHONUTF8 = "1"
```

---

## 🧰 MCP 工具一览（共 24 个工具 + 2 个工作流 Prompt）

### 一、环境、目录与字体发现（3 个）
| 工具名 | 功能说明 |
| :--- | :--- |
| `mtui_status` | 检查/动态配置 `MTUI_ROOT`、Python 环境、GPU/CUDA 状态与当前默认配置（含 `inpainter_blur_radius` 等），支持预热常驻进程与 `release_vram=True` 主动释放显存 |
| `mtui_list_pages` | 按漫画自然页序扫描目录（支持生肉目录、`last_output_path` / `last_export_dir`、`-translated` 成品目录），汇总 JSON/修复图/成图状态 |
| `mtui_list_fonts` | 列出 `fonts/` 自定义漫画字体与系统字体，支持 CJK 筛选及 **`check_text_glyphs` 缺字（豆腐块）预检** |

### 二、译文校对、自动化 QA 审计与多模态视觉巡检（5 个）
| 工具名 | 功能说明 |
| :--- | :--- |
| `mtui_get_page_details` | 获取单页所有文本框的原文、译文、富文本、字号、字体、颜色、描边、方向、角度、坐标、四点透视状态（`has_perspective_quad` / `white_frame_quad_local`）及页面 `last_output_path` 等完整结构 |
| `mtui_chapter_script` | 按日漫右起（`rtl`）或条漫左起（`ltr`）阅读顺序提取整话双语台本，供 Agent 一次性通读校对 |
| `mtui_qa_audit` | 自动扫描漏翻假名/韩文、OCR 误读与错译雷达、YuzuMarker 4x 字体/字重核验、行首标点/断句失衡、横竖排与齐顶错配、字体缺字及 **CV 气泡残字/破框/框外过擦检测**，支持 `diff_against_json` 零倒退回归锁 |
| `mtui_inspect_page_visual` | 生成带像素标尺的整页视觉对比图（`side_by_side` / `inpaint_check` 三联图 / `annotated_rendered` / `clean_rendered`）并内联返回 `ImageContent` |
| `mtui_inspect_region_visual` | 生成指定区域的 **三联高清放大镜（原图 \| 修复底图+红框蒙版 \| 渲染成图）**，自动包络四点透视框范围，外框印有精确世界像素坐标 `(X, Y)` |

### 三、排版修改、富文本样式与 Agent 自主断句（3 个）
| 工具名 | 功能说明 |
| :--- | :--- |
| `mtui_update_regions` | 批量修改区域译文与 **Agent 自主判断的 `\n` 断句**（后端强制锁定禁用原生自动换行）、字号（或增减偏移，自动等比缩放透视框）、字体、整框 `bold`、字色/描边色（支持自动吸取原图颜色）、描边粗细、横竖排、对齐、行距/字距、中心位移、旋转角与四点透视框 `white_frame_quad_local`，支持 `region_uid` 稳定定位与 `expected_revision` 并发保护 |
| `mtui_style_region_richtext` | 构建严格 `richtext.v1` 富文本：整框或按子串/正则局部应用 **加粗、斜体（`15°`）、着重号、Ruby 注音（含 `rubyInherit` / `ruby_inherit`）、TCY 纵中横、字色、缩放、双层描边、发光、高斯/定向/径向/动态模糊（`blur` / `blurAngle` / `blurRadial` / `blurMotion` / `blurMotionDistance`）、位移旋转**，支持传默认值/空值重置单属性或一键应用 `rich_text_rules.yaml` |
| `mtui_align_regions` | 多框左/中/右/顶/底对齐与水平/垂直等距分布 |

### 四、图片修复补充、蒙版精修与区域增删合并（5 个）
| 工具名 | 功能说明 |
| :--- | :--- |
| `mtui_edit_mask_and_inpaint` | 按世界像素坐标添加矩形/多边形/画笔蒙版、`tight_halo_rects` 紧贴光晕遮罩或按框膨胀（擦除漏擦假名/拟声词），或擦除蒙版（**无损还原被误擦的分镜框线/背景原图**）；支持 `neural`（GPU `lama_large`/`lama_mpe` 局部增量修复）、`blur`（毛玻璃模糊修复，支持 `blur_radius` `0..200`）、`solid_fill`（纯色气泡瞬时填充）、`telea`/`ns` |
| `mtui_add_region` | 补加漏检气泡/拟声词区域，自动提取文字墨迹蒙版、修复底图并嵌字渲染 |
| `mtui_delete_regions` | 删除误检的非文字区域，并自动清除对应蒙版、恢复原图画面像素 |
| `mtui_merge_regions` | 将同一单室气泡被拆碎的多个检测框合并为单个干净区域（自动清理旧透视框并拦截连体多室泡误合并） |
| `mtui_manage_paste_overlays` | 管理 `paste_overlays` 贴片图层，内置 **`add_clone_patch` 羽化仿制印章克隆修补** 与外部 PNG 贴片叠加，支持图层深度 `z` 与水平/垂直翻转（`flip_h` / `flip_v`） |

### 五、整话批处理、术语表、快照回滚、光学居中、GUI 桥接与后端流水线（8 个）
| 工具名 | 功能说明 |
| :--- | :--- |
| `mtui_batch_edit_chapter` | 整话范围条件筛选、正则/纯文本替换（自动保留富文本样式）与排版参数批改（字号变更自动同步透视框），支持 `dry_run` 预览 |
| `mtui_manage_glossary` | 读写 `dict/*.yaml`（人名/地名/组织/物品/技能/生物术语表与自定义 `system_prompt`）及 `.txt` 词典 |
| `mtui_snapshots` | 按单页或整话创建、列出、回滚或清理（`cleanup`）时间点快照（首次修改任意页面前自动生成 `initial_before_mcp` 基线备份） |
| `mtui_render_pages` | 调用原生 Qt6 离屏渲染引擎重渲染单页或整话成图，支持 `center_bubbles=True` 同步光学居中、`export_psd=True` 导出分层 PSD 及 `psd_script_only=True` 仅生成 `.jsx` 脚本 |
| `mtui_center_bubbles` | 在同一个常驻热进程内执行多轮闭环气泡光学居中（自动跳过倾斜框与四点透视框）并完成 Qt6 离屏重渲染 |
| `mtui_live_gui_session` | 连接正在运行的 `manga-translator-ui` 桌面 GUI 会话桥（默认随桌面端自动启动或通过 `--mcp` 显式开启），支持查看状态、等待翻译完成、切页、撤销/重做与保存；未打开桌面窗口时 `status` 平滑返回 `live_gui_connected=false`，可直接调用其余全部离屏 `mtui_*` 工具 |
| `mtui_ocr_or_translate_regions` | 对指定区域或自定义坐标框重跑原生 OCR 模型（支持通过 `ocr` 参数临时指定 `mocr` / `48px` / `hayai_ocr_v2` / `paddleocr_vl` 等）与翻译器 |
| `mtui_run_translation_pipeline` | 对尚未翻译的生肉图片或文件夹触发 `manga_translator local` 翻译流水线，支持 `use_subprocess`、`memory_limit`、`memory_percent`、`batch_per_restart`、`batch_size`、`attempts`、`disable_onnx_gpu` 等大批量守护参数 |

内置 **2 个专家级工作流 Prompt**：`proofread_and_polish_chapter(target_folder)`（整话校对、修图与排版 SOP）与 `inspect_and_fix_page(page_path)`（单页深度巡检与精修 SOP），以及随包附带的端到端无人值守精修技能 [SKILL.md](skills/mtui-manga-proofreading/SKILL.md)（配套批处理 CLI：`mtui_mcp/skills/mtui-manga-proofreading/cli/audit_and_compare.py`）。

---

## 💡 推荐给 Agent 的提示词示例

在 `manga-translator-ui` 完成翻译（确保开启了默认的“**图片可编辑 / 保存文本**”选项）后，只需对 Antigravity、Cursor 或 Codex 说：

> 请使用 `mtui-mcp` 检查并精修 `D:\Manga\第01话`：
> 1. 先跑 QA 审计和整话剧本提取，校对译文错漏、统一角色名与人称代词；
> 2. 检查每页消字与嵌字视觉效果，用 `mtui_edit_mask_and_inpaint` 把漏擦的假名/标点擦干净，把误擦的画面线条还原；
> 3. 优化对白和拟声词的字体选择、字号大小、颜色描边、横竖排、富文本样式（如感叹号纵中横、重点词加粗/注音/模糊特效）和断句，重渲染并复检最终成图。
