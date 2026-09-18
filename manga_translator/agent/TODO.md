# 基础 Agent 循环 TODO

## 本阶段目标

先建立一个最小、可观察、可取消的页面 Agent 执行循环。复用 PydanticAI，不自行重写模型请求与工具分发循环。模型、工具集和每次运行上下文独立装配，为后续逐步接入真实工具保留接口。

已先实现无工具文本聊天：`domain/chat.py` 定义消息与流式后端契约，`application/service.py` 管理隔离会话与完整轮次提交，`providers/openai.py` 使用 PydanticAI 原生 Responses 流；Qt 页面位于“关于应用”上方，支持停止与清空。连接配置通过现有 ConfigService 保存到 `.env` 的 `AGENT_OPENAI_*`，密钥明文落盘，聊天记录不落盘。以下页面 Agent、图片与工具循环仍为后续计划。Responses 流式和持久化改动按用户要求未继续执行测试，不能视为真实在线验证通过。

## 待办

- [ ] 核对异步入口与现有模型连接方式。
  - 确认安装版本的 Agent、toolsets、事件和取消 API。
  - 不读取用户密钥，不自动发起付费模型请求。
- [ ] 定义最小运行输入输出与依赖边界。
  - 输入包含任务文本、可选图片、页面会话标识及用户偏好快照。
  - 输出区分成功、失败、取消，不能把中断当正常完成。
  - 不预先堆叠尚未使用的工作区接口。
- [ ] 复用 PydanticAI 建立页面执行循环。
  - 使用异步调用，不阻塞 GUI。
  - 正常续跑保留历史，按页隔离；固定前缀不逐轮改写。
- [ ] 支持可替换模型与独立工具集装配。
  - 支持无工具运行，以及通过原生 toolsets 注入工具。
  - 添加工具不修改执行循环，不制造未实现的编辑工具。
- [ ] 记录执行事件并支持预算与取消。
  - 记录请求、工具调用、工具返回、结束状态和必要耗时。
  - 凭据脱敏，不默认保存完整图像载荷。
  - 设置请求上限与超时，支持外部取消并释放本次运行资源。
- [ ] 提供不读取密钥的离线调试入口。
  - 使用框架测试模型验证执行链，明确其不是在线模型。
  - 在线执行须由显式模型配置触发，不能隐式使用订阅或 API 凭据。
- [ ] 验证成功失败取消及会话隔离行为。
  - 验证类型化结果、实际工具执行和错误传播。
  - 验证取消后不再继续发起工具/模型请求。
  - 验证页面会话独立、续跑历史与固定前缀保持一致。

## 暂不实现

此文件记录基础聊天阶段范围。后续已接入后台工作区工具、页面子任务和可信目录自动插件加载，当前能力与限制见 `tools/实现与输出说明.md`、`plugins/协议.md`。MCP 连接、订阅登录及后台漫画工作区界面仍未实现；当前 Qt 不自动等同于已装配全部后台工具。

## 完成标准

离线调试能重复运行，给出明确结果与事件；工具集可替换，错误和取消可观测，页面历史不串用。交付时说明哪些场景离线验证、哪些实际在线验证，不能混称。

## 当前实现与交接总结

### 已完成

- 已梳理现有编辑器、Agent 目录、Qt 聊天页、ChatService、Responses API 后端及渲染入口。
- `manga_translator/agent/integrations/rendering.py` 已加入快速渲染入口、有限缓存、区域指纹、版本检查和完整渲染回退。
- 快速渲染在旋转、描边、富文本变化、缓存失效或版本不一致时应返回完整渲染结果，并标记 `status="full_fallback"` 及回退原因。
- `manga_translator/rendering/__init__.py` 已修正气泡检测缓存淘汰后无法重新计算的问题。
- `desktop_qt_ui/ui/main_page/pages/chat_page.py` 已增加当前图片上下文、`ChatImage` 附件、区域元数据上下文和可见历史过滤逻辑。

### 范围说明

- 用户当前明确要求的是把 `desktop_qt_ui/agent_debug.py` 接到真实 Agent 后端。
- 目标链路必须成立：

  `agent_debug.py -> AgentDebugWindow -> ChatPage -> ChatService -> OpenAIResponsesBackend -> Responses API`

- 独立 Agent Debug 入口没有编辑器实例，不能假设它能自动获得主窗口当前编辑器图片；没有明确数据源时应保持图片上下文为空。
- 当前任务不应继续扩大为主窗口编辑器、GraphicsView 或聊天布局改造。
- 之前对 `chat_page.py` 的 UI 上下文和历史改动来自更早的综合目标；本轮后端接线不应继续修改 UI 布局，也不应擅自回滚已有改动。

### 下一步优先事项

- 检查 `desktop_qt_ui/agent_debug.py` 和 `desktop_qt_ui/ui/agent/debug_window.py` 的服务注入，确认 `ChatPage` 使用现有 `ChatService` 或正确创建兼容的后端服务。
- 沿用现有 `ServiceContainer`、`ServiceManager`、`ConfigService`、`ChatService` 和 `OpenAIResponsesBackend`，不要新增请求协议或重复实现模型循环。
- 确认配置来源、Responses API 请求发送、流式文本接收、错误传播、取消和窗口关闭时的后台任务清理。
- 确认图片仍通过 `ChatService.stream(..., images=...)` 和现有 `ChatImage` 发送；请求体、thinking、工具操作历史和图片二进制不能进入可见聊天历史。
- 只做针对性编译和差异检查，不运行格式化、Lint 或整套测试。当前 `test/test_chat_service.py` 使用旧接口，不能直接作为可靠验证依据。

### 推荐验证

```text
uv run --no-sync python -m compileall -q desktop_qt_ui/agent_debug.py desktop_qt_ui/ui/agent/debug_window.py desktop_qt_ui/ui/main_page/pages/chat_page.py manga_translator/agent/application/service.py manga_translator/agent/providers/openai.py manga_translator/agent/integrations/rendering.py
git diff --check
```

验证报告应明确说明：Agent Debug 是否真正连接 `ChatService`，后端配置来自哪里，请求是否进入 Responses API，取消和关闭是否正常，以及快速渲染仍有哪些未验证场景。

### 后续渲染器改造计划

- 不重写文字绘制算法。将 Agent 渲染从编辑器状态中抽离，增加建立在现有文字渲染后端之上的独立 headless 适配与调度层；不得依赖 `EditorModel`、`EditorController`、`GraphicsView`、`EditorView` 或 QWidget。
- 先确认编辑器实际使用的文字渲染后端及其最小无头依赖。现有后端继续负责字体、测量、换行、富文本、描边、旋转和像素绘制，Agent 层只负责快照规范化、缓存、脏区域调度、worker 生命周期和版本结果。
- 输入采用页面快照：底图、区域几何、译文、字体、字号、富文本和渲染配置；输出实际 PNG、尺寸、revision、状态及回退原因。
- 优先复用现有后端完成常见无旋转/无描边/无复杂重叠区域的快速路径，缓存底图、布局输入和未变化区域，只重算受影响区域。
- 旋转、描边、富文本/字体变化、边界变化、重叠依赖、缓存缺失或 revision 不一致时调用现有后端的完整 headless render，不返回过期或残缺结果。
- worker 必须支持无显示器、无 QApplication、无编辑器窗口运行，并可取消和关闭；字体、编码和异常都要明确处理。
- 用相同页面快照比较快速路径与现有后端完整路径，验证像素结果、透明度、边界、版本隔离、缓存命中、取消、关闭和性能；完成后再让 Agent Debug 与工作区工具统一调用该适配接口。
