# API 配置教程

本文档提供常用在线翻译 API 的申请和配置教程。

---

## 📋 目录

- [模型选择建议](#模型选择建议)
- [通用 API 配置说明](#通用-api-配置说明)
- [硅基流动 API 配置](#硅基流动-api-配置)
- [DeepSeek API 配置](#deepseek-api-配置)
- [Google Gemini API 配置](#google-gemini-api-配置)
- [API OCR 配置（OpenAI OCR / Gemini OCR）](#api-ocr-配置openai-ocr--gemini-ocr)
- [API 上色配置（OpenAI Colorizer / Gemini Colorizer）](#api-上色配置openai-colorizer--gemini-colorizer)
- [API 渲染配置（OpenAI Renderer / Gemini Renderer）](#api-渲染配置openai-renderer--gemini-renderer)
- [常见问题](#常见问题)

---

## 模型选择建议

- **高质量翻译器**需要多模态模型（能"看图"的 AI），如 Grok、Gemini、ChatGPT 等，AI 能看到漫画画面，翻译更准确
- 一般来说，参数量越大的模型翻译效果越好

### 如何看参数量

模型名称中通常包含参数量信息，例如：
- `Qwen3-235B` → 2350 亿参数
- `DeepSeek-V3-671B` → 6710 亿参数
- `Llama-3-70B` → 700 亿参数

参数量单位：`B` = Billion（十亿），所以 `235B` = 2350 亿参数

### 多模态模型示例

| 模型 | 平台 | 说明 |
|------|------|------|
| `gpt-5.2` | OpenAI | ChatGPT 最新多模态 |
| `gemini-3-pro-preview` | Google | Gemini 最新多模态 |
| `gemini-2.5-pro` | Google | Gemini 多模态 |
| `grok-4.1` | xAI | Grok 最新多模态 |

### 纯文字模型示例

| 模型 | 平台 | 说明 |
|------|------|------|
| `deepseek-chat` | DeepSeek | 速度快 |
| `deepseek-reasoner` | DeepSeek | 有思考，断句稳定 |
| `Qwen/Qwen3-235B-A22B` | 硅基流动 | 通义千问3，2350亿参数 |

---

## 通用 API 配置说明

### 翻译器类型

程序提供了两类翻译器，它们的区别只是**接口不同**：

#### 普通翻译器（OpenAI / Gemini）
- 使用纯文本 API
- 只发送识别出的文字
- 翻译速度快，消耗少
- 适合简单场景

#### 高质量翻译器（高质量翻译 OpenAI / 高质量翻译 Gemini）
- 使用多模态 API（支持图片）
- 发送图片 + 文字
- AI 可以"看到"图片，理解场景
- 翻译更准确，但消耗较多
- **需要模型支持多模态**（如 GPT-4o、Gemini）

> 💡 **提示**：如果你的模型支持多模态，强烈推荐使用"高质量翻译器"获得最佳效果！

### API 地址填写规范

#### OpenAI 兼容接口

OpenAI 翻译器**几乎支持市面上所有模型**，因为几乎所有的 AI 平台都提供 OpenAI 兼容接口。

- **一般情况**：API 地址以 `/v1` 结尾
  - 例如：`https://api.openai.com/v1`
  - 例如：`https://api.deepseek.com/v1`
  - 支持：DeepSeek、Groq、Together AI、OpenRouter、**硅基流动**、**火山引擎**等
- **例外情况**：某些服务商可能使用其他版本号
  - 例如：火山引擎使用 `/v3` 结尾

> 💡 **提示**：只要你的 API 提供商支持 OpenAI 兼容接口，就可以使用 OpenAI 翻译器！

#### Gemini 接口
- **无需添加版本号**：直接填写基础地址即可
  - 填写：`https://generativelanguage.googleapis.com`
  - 程序会自动添加 `/v1beta`
- **使用 AI Studio 官方 key**：无需填写 API 地址（自动使用默认地址）

#### Google Cloud / Vertex 相关 Key 说明
- Google Cloud / Vertex 相关 API Key 也直接填写到 Gemini 配置即可
- `Base URL` 留空使用默认值，或填写 `https://generativelanguage.googleapis.com` 都可以，无需改成其他地址

---

## 硅基流动 API 配置

硅基流动（SiliconFlow）是国内 AI 平台，提供多种模型，新用户有赠送额度，价格便宜，国内访问速度快。

> 💡 **优势**：新用户注册赠送额度，支持 Qwen3、DeepSeek 等多种模型，国内直连无需科学上网。

### 1. 注册账号

1. 访问 [硅基流动官网](https://cloud.siliconflow.cn/)
2. 点击"注册"，使用手机号注册
3. 完成验证

### 2. 创建 API Key

1. 登录后进入控制台
2. 点击左侧"API 密钥"
3. 点击"新建 API 密钥"
4. 复制生成的 API Key

### 3. 配置到程序中

1. 打开程序
2. 在"基础设置"→"翻译器"中选择"OpenAI"
3. 在"高级设置"中填写：
   - **API Key**：你的硅基流动 API Key
   - **Base URL**：`https://api.siliconflow.cn/v1`
   - **模型**：在 [模型广场](https://cloud.siliconflow.cn/models) 查看所有可用模型

---

## DeepSeek API 配置

DeepSeek 提供高质量、低成本的 AI 翻译服务，非常适合漫画翻译使用。

> ⚠️ **注意**：DeepSeek 不支持多模态，无法使用"高质量翻译器"。为了获得最佳翻译效果，建议使用支持多模态的模型（如 OpenAI GPT-4o、Google Gemini）。

### 1. 注册账号

1. 访问 [DeepSeek 开放平台](https://platform.deepseek.com/)
2. 点击"注册"按钮，使用邮箱或手机号注册
3. 完成邮箱验证

### 2. 充值

1. 登录后，点击右上角头像 → "充值"
2. 选择充值金额（建议最低 10 元起）
3. 使用支付宝或微信支付

### 3. 创建 API Key

1. 点击左侧菜单"API Keys"
2. 点击"创建 API Key"按钮
3. 输入名称（如"漫画翻译"）
4. 复制生成的 API Key（格式：`sk-xxxxxxxxxxxxxxxx`）
5. ⚠️ **重要**：立即保存 API Key，关闭窗口后无法再次查看

### 4. 配置到程序中

1. 打开程序
2. 在"基础设置"→"翻译器"中选择"OpenAI"
3. 在"高级设置"中填写：
   - **API Key**：填入你的 DeepSeek API Key（`sk-xxxxxxxxxxxxxxxx`）
   - **Base URL**：填入 `https://api.deepseek.com/v1`
   - **模型**：选择以下两种之一：
     - `deepseek-chat`：不思考，速度快，**但可能导致 AI 断句不生效**
     - `deepseek-reasoner`：有思考，速度慢，**但断句稳定可靠** ⭐ 推荐

> 💡 **提示**：AI 断句功能可以智能拆分长文本，避免气泡溢出。如果需要最佳翻译效果，建议使用 `deepseek-reasoner`。

---

## Google Gemini API 配置

Google Gemini 是 Google 最新的多模态 AI 模型，性能强劲。

> ⚠️ **注意**：Google AI Studio 已全面收费，不再提供免费额度。

### 1. 获取 API Key

1. 访问 [Google AI Studio](https://aistudio.google.com/apikey)
2. 登录 Google 账号
3. 点击"Create API Key"
4. 选择 Google Cloud 项目（或创建新项目）
5. 复制生成的 API Key

### 2. 配置到程序中

1. 打开程序
2. 在"基础设置"→"翻译器"中选择"高质量翻译 Gemini"或"Gemini"
3. 在"高级设置"中填写：
   - **API Key**：你的 Gemini API Key
   - **Base URL**：无需填写（自动使用默认地址）
    - **模型**：
       - `gemini-2.5-pro`：断句稳定，质量最高 ⭐ 推荐
       - `gemini-2.5-flash`：速度快，价格便宜
4. 你的 Google Cloud / Vertex 相关 API Key 也直接填写到 Gemini 配置即可
5. `Base URL` 保持默认官方地址 `https://generativelanguage.googleapis.com` 即可，无需改成其他地址

---

## API OCR 配置（OpenAI OCR / Gemini OCR）

这些 OCR 主要用于 **Qt 桌面端** 的 OCR 模型选择；服务端 **Web 管理端** 的 API Key 配置页也同步提供对应 OCR 分组，供服务端运行时读取。

### OpenAI OCR

- OCR 模型选择：`openai_ocr`
- 优先读取的环境变量：
  - `OCR_OPENAI_API_KEY`
  - `OCR_OPENAI_MODEL`
  - `OCR_OPENAI_API_BASE`
- 如果 OCR 专用变量未填写，会自动回退到：
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
  - `OPENAI_API_BASE`

### Gemini OCR

- OCR 模型选择：`gemini_ocr`
- 优先读取的环境变量：
  - `OCR_GEMINI_API_KEY`
  - `OCR_GEMINI_MODEL`
  - `OCR_GEMINI_API_BASE`
- 如果 OCR 专用变量未填写，会自动回退到：
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL`
  - `GEMINI_API_BASE`

### AI OCR 提示词

- 固定文件：`dict/ai_ocr_prompt.yaml`
- Qt 设置页中点击"AI OCR 提示词"旁边的"编辑"即可修改
- 文件格式为 YAML，主键使用 `ai_ocr_prompt`
- 如果本地仍有旧版 `dict/ai_ocr_prompt.json`，程序首次使用时会自动迁移内容到 YAML

### 说明

- OpenAI OCR / Gemini OCR 是逐个文本框调用 API
- 文本识别完成后，文字颜色仍由本地 `48px` 模型提取
- 通常识别效果最好，但和本地 OCR 的差距往往不大
- 因为是按文本框逐次请求，会非常消耗请求次数；如果是按次收费的站点，不建议使用
- Google Cloud / Vertex 相关 API Key 也直接填写 `OCR_GEMINI_*` 或普通 `GEMINI_*`；`OCR_GEMINI_API_BASE` / `GEMINI_API_BASE` 保持默认官方地址即可，无需修改

---

## API 上色配置（OpenAI Colorizer / Gemini Colorizer）

这些上色器主要用于 **Qt 桌面端** 的上色模型选择；服务端 **Web 管理端** 的 API Key 配置页也同步提供对应上色分组。

### OpenAI Colorizer

- 上色模型选择：`openai_colorizer`
- 优先读取的环境变量：
  - `COLOR_OPENAI_API_KEY`
  - `COLOR_OPENAI_MODEL`
  - `COLOR_OPENAI_API_BASE`
- 如果上色专用变量未填写，会自动回退到：
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
  - `OPENAI_API_BASE`

### Gemini Colorizer

- 上色模型选择：`gemini_colorizer`
- 优先读取的环境变量：
  - `COLOR_GEMINI_API_KEY`
  - `COLOR_GEMINI_MODEL`
  - `COLOR_GEMINI_API_BASE`
- 如果上色专用变量未填写，会自动回退到：
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL`
  - `GEMINI_API_BASE`

### AI 上色提示词

- 固定文件：`dict/ai_colorizer_prompt.yaml`
- Qt 设置页中点击"AI 上色提示词"旁边的"编辑"即可修改
- 文件格式为 YAML，主键使用 `ai_colorizer_prompt`

### 说明

- OpenAI Colorizer / Gemini Colorizer 是整页调用 API
- 并发由 `ai_colorizer_concurrency` 控制，仅 Qt 桌面端显示
- `openai_colorizer` 会根据 `COLOR_OPENAI_API_BASE` / `OPENAI_API_BASE` 自动适配图像接口：
  - `https://api.siliconflow.cn/v1`：自动使用硅基流动 `images/generations` 风格，请求体支持 `image` / `image2` / `image3`
  - `https://dashscope.aliyuncs.com/api/v1` 或 `https://dashscope-intl.aliyuncs.com/api/v1`：自动切换到百炼原生 `services/aigc/multimodal-generation/generation`
  - 火山引擎 / 其他 OpenAI 兼容图像接口：继续使用 OpenAI 兼容格式
  - **OpenRouter**(`https://openrouter.ai/api/v1`):自动走 `chat/completions` 出图,自动注入 `modalities: ["image", "text"]`;模型选 `google/gemini-3-pro-image-preview` / `google/gemini-2.5-flash-image` 等。`image_config` 等参数通过 `custom_api_params.json` 的 `colorizer` 段传入
- AI 上色多图提示词会自动按图号说明角色：`Image 1` 为当前待上色页，`Image 2+` 会分别标注为提示词参考图或历史已上色页
- 若启用 `use_custom_api_params`，上色器会自动把 `examples/custom_api_params.json` 中 `colorizer` 分组的自定义参数并入对应后端请求体；例如：
  - 硅基流动：`cfg`、`num_inference_steps`、`image_size`、`guidance_scale`
  - 百炼原生：`n`、`watermark`、`negative_prompt`、`prompt_extend`、`size`
- Google Cloud / Vertex 相关 API Key 也直接填写 `COLOR_GEMINI_*` 或普通 `GEMINI_*`；`COLOR_GEMINI_API_BASE` / `GEMINI_API_BASE` 保持默认官方地址即可，无需修改

---

## API 渲染配置（OpenAI Renderer / Gemini Renderer）

这些渲染器主要用于 **Qt 桌面端** 的渲染模型选择；服务端 **Web 管理端** 的 API Key 配置页也同步提供对应渲染分组。

### OpenAI Renderer

- 渲染模型选择：`openai_renderer`
- 优先读取的环境变量：
  - `RENDER_OPENAI_API_KEY`
  - `RENDER_OPENAI_MODEL`
  - `RENDER_OPENAI_API_BASE`
- 如果渲染专用变量未填写，会自动回退到：
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
  - `OPENAI_API_BASE`

### Gemini Renderer

- 渲染模型选择：`gemini_renderer`
- 优先读取的环境变量：
  - `RENDER_GEMINI_API_KEY`
  - `RENDER_GEMINI_MODEL`
  - `RENDER_GEMINI_API_BASE`
- 如果渲染专用变量未填写，会自动回退到：
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL`
  - `GEMINI_API_BASE`

### AI 渲染提示词

- 固定文件：`dict/ai_renderer_prompt.yaml`
- Qt 设置页中点击"AI 渲染提示词"旁边的"编辑"即可修改
- 文件格式为 YAML，主键使用 `ai_renderer_prompt`

### 说明

- OpenAI Renderer / Gemini Renderer 是整页调用 API
- 实际请求会自动组合：
  - 清理后的页面图
  - 仿高质量翻译器的编号框标注图
  - 对应编号的翻译文本列表
- 拟声词 / 音效也会按翻译结果一起发送给渲染模型
- 并发由 `ai_renderer_concurrency` 控制，仅 Qt 桌面端显示
- `openai_renderer` 也会根据 `RENDER_OPENAI_API_BASE` / `OPENAI_API_BASE` 自动适配图像接口,规则与 `openai_colorizer` 一致
- 若启用 `use_custom_api_params`,渲染器会自动把 `examples/custom_api_params.json` 中 `render` 分组的自定义参数并入对应后端请求体
- **OpenRouter 出图**:`API_BASE` 设为 `https://openrouter.ai/api/v1`、模型选 `google/gemini-3-pro-image-preview` / `google/gemini-2.5-flash-image` 等支持出图的模型即可。程序会自动:
  - 优先走 `chat/completions`(跳过 `images/edits` / `images/generations` 的无效探测)
  - 注入 `modalities: ["image", "text"]`(OpenRouter 出图必须;用户在 `custom_api_params.json` 写的同名字段会覆盖默认值)
  - 默认 `max_tokens: 2048`(避免 OpenRouter 默认 32768 触发 402 余额不足;同样可被用户覆盖)
  - `image_config` 等其他 OpenRouter 专有参数走 `custom_api_params.json` 顶层传入即可。**注意**:`aspect_ratio` 不要乱填,流水线会把原图 pad 成正方形发出去,目标输出比例 ≠ 方形会在裁回原页时失真
- Google Cloud / Vertex 相关 API Key 也直接填写 `RENDER_GEMINI_*` 或普通 `GEMINI_*`;`RENDER_GEMINI_API_BASE` / `GEMINI_API_BASE` 保持默认官方地址即可,无需修改

---

## 常见问题

### Q1：哪个 API 最推荐？

**回答**：
- **性价比最高**：DeepSeek（国内用户推荐）
- **质量最高**：OpenAI GPT-4o / Google Gemini

### Q2：API Key 泄露怎么办？

**回答**：
1. 立即到对应平台删除泄露的 API Key
2. 创建新的 API Key
3. 检查账户余额是否异常

### Q3：提示"API Key 无效"怎么办？

**回答**：
1. 检查 API Key 是否完整复制
2. 检查 Base URL 是否正确
3. 确认账户余额充足
4. 检查网络连接（国外 API 可能需要科学上网）

---

返回 [主页](../README.md) | 返回 [使用教程](USAGE.md)

