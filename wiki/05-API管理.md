# API 管理

“API 管理”集中维护翻译、OCR、上色和渲染服务。页面写入根目录 `.env` 和 `presets/*.json`；“参数设置”只负责选择使用哪个后端。两处配置必须匹配。

## 四个功能标签页

| 标签页 | 可显示的远程实现 | 本地替代 |
| --- | --- | --- |
| 翻译 | OpenAI、Gemini、Sakura | `none`、`original` 不调用翻译 API |
| OCR | OpenAI OCR、Gemini OCR；混合 OCR 时可能同时显示主/辅助通道 | 48px、Manga OCR、PaddleOCR 等 |
| 上色 | OpenAI Colorizer、Gemini Colorizer | MC2 |
| 渲染 | OpenAI Renderer、Gemini Renderer | Qt `default` 渲染器 |

当前所选实现不需要 API 时，标签页显示空状态。页面只展示该功能当前真正需要的配置组。

## 配置一个通道

OpenAI/Gemini 兼容通道的每个槽位包含 API Key、Model 和 API Base。三项必须来自同一个端点，不能把不同服务的 Key、Base 和 Model 拼到同一槽位。

1. 填写 API Base；OpenAI 兼容服务通常包含 `/v1`。
2. 填写 API Key。密钥默认隐藏，可用眼睛按钮临时显示。
3. 点击 Model 行的“获取模型”，从接口返回列表中选择；也可手工填写准确模型名。
4. 点击 API Key 行的“测试”，验证当前单个槽位。
5. 使用“测试当前标签页”并发检查当前页全部已配置通道。

本地 OpenAI 兼容地址允许 Key 为空，运行时会补兼容占位值。测试成功只证明当前测试请求可用；文本聊天可用不等于视觉 OCR、图像上色或 AI 渲染接口也兼容。

Sakura 只配置：

- `SAKURA_API_BASE`
- `SAKURA_DICT_PATH`

## `.env` 字段

### 翻译

| 后端 | 密钥 | 模型 | 服务地址 |
| --- | --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_MODEL` | `OPENAI_API_BASE` |
| Gemini | `GEMINI_API_KEY` | `GEMINI_MODEL` | `GEMINI_API_BASE` |
| Sakura | 无 | 无 | `SAKURA_API_BASE`；词典为 `SAKURA_DICT_PATH` |

### OCR、上色与渲染

| 功能 | OpenAI 字段前缀 | Gemini 字段前缀 |
| --- | --- | --- |
| OCR | `OCR_OPENAI_` | `OCR_GEMINI_` |
| 上色 | `COLOR_OPENAI_` | `COLOR_GEMINI_` |
| 渲染 | `RENDER_OPENAI_` | `RENDER_GEMINI_` |

每组都包含 `API_KEY`、`MODEL`、`API_BASE`，例如：

```dotenv
OCR_OPENAI_API_KEY=...
OCR_OPENAI_MODEL=gpt-4o
OCR_OPENAI_API_BASE=https://api.openai.com/v1
```

专用组缺少 Key 或 Base 时可回退到通用 OpenAI/Gemini 的对应值；专用模型名使用该功能的默认值，不直接把通用翻译模型当作视觉或图像模型。

## 多 API 通道

第一个槽位使用原字段，后续槽位增加 `_2`、`_3` 等编号：

```dotenv
OPENAI_API_ROTATION_STRATEGY=failover
OPENAI_API_KEY=key-one
OPENAI_MODEL=model-one
OPENAI_API_BASE=https://one.example/v1
OPENAI_API_KEY_2=key-two
OPENAI_MODEL_2=model-two
OPENAI_API_BASE_2=https://two.example/v1
```

策略：

- `failover`：按槽位顺序调用，失败后切换，默认策略。
- `round_robin`：在当前可用通道间轮询。

页面可添加或删除槽位；删除中间槽位时，后续槽位会前移以保持连续编号。API 预设默认纳入 3 个槽位，界面最多管理 10 个，底层运行时最多读取 30 个。某组初次可能只显示一个槽位，需要时再添加。

## 测试、冷却与恢复

- 单槽位“测试”只检查该通道。
- “测试当前标签页”收集当前页已配置通道并发测试，测试并发数为 3。
- `429` 等限流错误会使通道进入冷却。
- 无效 Key、欠费或模型不存在等永久错误会把通道标为不可用。
- 所有候选都不可用时，任务开始前会阻止处理并提示用户。
- “恢复通道”只清除当前进程记录的失败/冷却状态，不修改 `.env`；配置错误时恢复后仍会再次失败。

修改通道后应重新测试。已有任务使用已经创建的客户端，重大改动后等待任务结束并重启桌面端最稳妥。

## API 预设

页面顶部可新增、切换和删除预设。预设保存在 `presets/*.json`，覆盖翻译、OCR、上色、渲染字段以及轮换槽位和策略。

- 切换预设前会保存当前预设的最新值。
- 新建同名预设时会询问是否覆盖。
- 应用预设会更新当前 `.env`，切换前先结束正在运行的任务。
- 预设可能包含真实密钥，`presets/` 不应提交或公开。

## 自定义 API 参数

启用 `use_custom_api_params` 后，编辑器管理 `config/custom_api_params.json`。一级预设名应与当前实际模型名完全一致；找不到时回退到“通用”。

```json
{
  "通用": {
    "common": {},
    "translator": {"temperature": 0.3},
    "ocr": {"temperature": 0.0},
    "colorizer": {},
    "render": {}
  }
}
```

运行时只合并 `common` 和当前功能分区，不会把翻译参数发给 OCR、上色或渲染接口。编辑器支持模型预设的新增、重命名、删除、分组表格编辑和原始 JSON 编辑。出现 `400` 参数错误时，先关闭自定义参数验证基础请求，再逐项恢复。

## 安全与排障

- 不要在截图、日志、Issue 或 Wiki 中粘贴真实 Key。
- `.env`、`presets/` 和服务器用户数据不应提交到 Git。
- 第三方 API Base 可以看到发送的文本或图像，只使用可信服务。
- AI OCR、AI 上色和 AI 渲染都会上传图像；本地模型不会。
- `401` 先查 Key，`404` 查 Base 路径和模型名，`429` 查配额、客户端限速和轮换状态。

后端选择和并发参数见 [[参数设置|04-参数设置]]；提示词内容见 [[提示词与规则|06-提示词与规则]]；详细错误表见 [[故障排查|13-故障排查]]。
