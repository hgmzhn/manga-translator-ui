# v2.2.2 更新日志

发布日期：2026-03-25

## ✨ 新功能

- 权限系统扩展到翻译器、OCR、上色器、渲染器四类能力；用户组/用户权限与权限编辑器现在都可以分别管理这些能力。
- 权限编辑器新增 API Key 策略可视化项：支持配置“允许用户在主页编辑 API Keys”“允许使用服务器默认 API Keys”“强制用户提供 API Keys 或预设”“允许把用户填写的 API Keys 保存到服务器”等策略。
- Web 管理后台与用户主页的 API Key 管理按 Qt 界面思路重做，统一分为“翻译 / OCR / 上色 / 渲染”四组，并加入 Vertex 分组。
- Web 启动流程现在会和 Qt 一样自动补齐 `config/custom_api_params.json`、`config/filter_list.json`、`dict/ai_ocr_prompt.yaml`、`dict/ai_renderer_prompt.yaml`、`dict/ai_colorizer_prompt.yaml` 等初始化文件。

## 🔐 安全与权限

- 用户主页的 API Keys 编辑器不再返回服务器默认 Key 或预设 Key 的明文；`/env/effective` 只返回来源元数据，不再泄露真实值。
- 移除服务端日志里的 API Key 前缀输出，避免在日志中暴露密钥片段。
- Web 端 API Key 合成逻辑改为在服务端完成，按“用户输入 > 用户选择的预设 > 服务器默认”顺序合并，再写入本次请求配置，避免前端拼装与全局环境变量污染。
- OCR / 上色 / 渲染的运行时 API 参数改为请求级覆盖；高并发时不再依赖全局 `os.environ` 临时注入，降低多用户并发串 Key 风险。
- 翻译请求与普通鉴权成功后都会刷新会话活动时间，长任务期间的会话过期行为改为真正滑动续期。

## 🐛 修复

- 修复桌面端临时 `asyncio` 事件循环收尾不完整导致的 `Task was destroyed but it is pending!` 报错：为 API 测试、模型拉取、导出渲染和主翻译 worker 统一补齐 pending task 取消回收、`shutdown_asyncgens` 与默认执行器关闭流程，避免 loop 关闭时残留协程任务。
- 修复竖排英文词组的“竖排内横排”自动标记与渲染异常问题：
  - `BlueBox`、`Blue Box` 这类英文内容在竖排场景下现在会按预期自动补 `<H>` 标记。
  - `Blue Box` 这类带空格的英文词组现在支持按整块旋转渲染，不会再出现加了横排标记却无法正常显示的问题。
  - `这是BlueBox`、`这是Blue Box` 这类中日文夹英文的混排场景会继续自动补 `<H>` 标记，保持竖排内横排效果。
- 修复“自动补 `<H>` 标记”职责混乱的问题：现在统一由 `manga_translator/rendering/__init__.py` 的高层预处理负责补标记；底层渲染、测量与换行链路不再自行偷偷补 `<H>`，避免预览、测量和最终渲染行为不一致。
- 修复文本渲染位置错误与文本框锚点策略混乱的问题：
  - 开启“气泡内居中”后，只有命中气泡的文本块会使用居中锚点；其他文本块不再误用居中。
  - `skip_font_scaling` 现在固定使用当前 `region.center` 渲染，不再额外执行上对齐。
  - `balloon_fill`、`smart_scaling`、`strict` 三种布局模式的文本框锚点规则已统一，修复不同模式下文本位置与渲染结果不一致的问题。
- 修复“导入翻译并渲染”在 JSON 仅包含蒙版、没有文字渲染区域时直接返回原图的问题：现在 `load_text` 模式会继续执行修复流程，支持“仅蒙版、无文本框”的修复输出，并保持保存流程正常落盘。
- 修复“导出原文 / 导出翻译”处理无字图时仍强制执行蒙版优化的问题：当页面没有任何文本区域时，现在会直接导出空 JSON / 空 TXT，不再触发 `mask_refinement` 对空文本列表报错。
- 修复 Web 用户主页 API Keys 标签页会回显服务器默认 Key / 预设 Key 明文的问题，防止普通用户直接读取服务器密钥。
- 修复填写但未点击“保存”的用户 API Key 在前端发起单图翻译时不会随请求提交的问题；Vertex 等仅用户侧填写 Key 的场景不再因此误报 `403 Forbidden`。
- 修复长任务期间前端日志轮询持续携带失效 token 导致 `/api/logs` 连续 `401 Unauthorized` 的问题；前端现在会在 `401` 时停止轮询并重新读取当前 token。
- 修复 Web 端权限校验只拦翻译器、不拦 OCR / 上色 / 渲染的缺口；现在这几类请求都会按用户组/用户权限做一致校验。
- 修复 OCR / 上色 / 渲染在未填写专用 Key 时错误回落到翻译分组 `OPENAI_*` / `GEMINI_*` 的行为；现在只认各自分组的专用参数。
- 修复 Web API Key 管理与用户主页仍保留 DeepSeek / Groq / DeepL / 百度 / 有道 / 彩云等当前网页端无效入口的问题，已清理为当前实际可用的参数组。

## 🔧 优化

- Web 用户 API Key 管理与预设逻辑改为服务端合成，支持“用户填写覆盖预设，预设再覆盖服务器默认”的行为，同时保留服务端统一校验与拒绝策略。
- Web 端配置选项、下拉项与后端可用能力列表统一按权限动态过滤，用户只能看到自己当前被允许使用的翻译器 / OCR / 上色器 / 渲染器。
- 桌面端 API 管理、API 测试逻辑与多语言文案同步补齐 Vertex、OCR、上色、渲染相关参数，和 Web 端参数体系保持一致。
- Web 服务内部数据结构收口：`admin_config.json` 与 `user_resources/` 现统一纳入 `manga_translator/server/data`，并在启动时自动迁移旧目录布局。
- Docker `docker-compose` 示例同步调整为单挂载 `./data/server:/app/manga_translator/server/data`，长期部署时目录结构更简单。

## 📝 文档

- 同步更新 `README.md`、`README_EN.md`、`doc/API_CONFIG.md`、`doc/en/API_CONFIG.md`、`doc/INSTALLATION.md`、`doc/en/INSTALLATION.md`、`doc/CLI_USAGE.md`、`doc/en/CLI_USAGE.md` 以及相关使用文档。
- 文档中补充了 Vertex / Vertex HQ 的参数说明、Web 端 API Key 分组与权限策略、`/admin` 管理后台地址、Web UI Docker 持久化目录，以及 `server/data` 单目录持久化的新布局说明。
