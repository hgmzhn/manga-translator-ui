---
title: API 配置教程
description: 申请常用在线翻译 API 的密钥，并在 API 管理中正确填写 Key、Base URL 与模型
pageId: desktop.api-management.api-key-guide
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# API 配置教程

本文介绍如何申请常用在线翻译 API 的密钥，并在“API 管理”页正确填写。只讲用户操作，以各平台实际页面为准。填写后的连接测试、模型拉取与通道管理分别见[连接测试与模型列表](./connection-tests-and-model-list.md)和[API 凭据、地址与模型](./credentials-addresses-models.md)。

## 模型选择建议

- **多模态模型（可看图）**：能直接看到漫画画面，翻译更贴合剧情与分镜。高质量翻译器（OpenAI 高质量翻译 / Gemini 高质量翻译）必须搭配多模态模型。
- **纯文本模型**：只接收识别出的文字，速度快、消耗少，但看不到画面，适合简单场景。
- 一般来说参数量越大翻译效果越好。模型名里的 `B` 表示十亿（Billion）参数：`Qwen3-235B` 是 2350 亿参数，`DeepSeek-V3-671B` 是 6710 亿参数，`Llama-3-70B` 是 700 亿参数。

多模态模型示例：`gpt-5.2`、`gemini-3-pro-preview`、`gemini-2.5-pro`、`grok-4.1`。
纯文本模型示例：`deepseek-chat`、`deepseek-reasoner`、`Qwen/Qwen3-235B-A22B`。

## OpenAI 兼容接口

OpenAI 翻译器几乎支持市面上所有平台，因为几乎所有 AI 平台都提供 OpenAI 兼容接口：

- Base URL 一般以 `/v1` 结尾，例如 `https://api.deepseek.com/v1`、`https://api.siliconflow.cn/v1`。
- 个别平台例外，例如火山引擎使用 `/v3` 结尾。
- 只要平台提供 OpenAI 兼容接口，就可以用 OpenAI 翻译器接入。

## 各平台申请与填法

### 硅基流动

硅基流动（SiliconFlow）是国内平台，支持 Qwen、DeepSeek 等多种模型，新用户有赠送额度，国内访问速度快。

1. 访问[硅基流动官网](https://cloud.siliconflow.cn/)注册账号（手机号注册并完成验证）。
2. 登录控制台，点左侧“API 密钥”→“新建 API 密钥”，复制生成的 Key。
3. 在“API 管理 → 翻译”中填写：
   - Key：你的硅基流动 API Key；
   - Base URL：`https://api.siliconflow.cn/v1`；
   - 模型：在[模型广场](https://cloud.siliconflow.cn/models)查看可用模型。

### DeepSeek

DeepSeek 只提供纯文本模型，**不支持多模态**，不能用于“高质量翻译 OpenAI / Gemini”；需要看图翻译时请改用支持多模态的模型。

1. 访问[DeepSeek 开放平台](https://platform.deepseek.com/)注册并完成验证。
2. 登录后充值（余额不足会导致请求失败）。
3. 点左侧“API Keys”→“创建 API Key”，命名后复制；关闭窗口后不再显示，请立即保存。
4. 在“API 管理 → 翻译”中填写：
   - Key：你的 DeepSeek API Key；
   - Base URL：`https://api.deepseek.com/v1`；
   - 模型：`deepseek-chat`（不思考、速度快，断句可能不稳定）或 `deepseek-reasoner`（有思考、速度慢，断句更稳定，推荐）。

### Gemini

1. 访问[Google AI Studio](https://aistudio.google.com/apikey)，登录 Google 账号，点击“Create API Key”，选择或创建 Google Cloud 项目后复制生成的 Key。
2. 在“API 管理 → 翻译”的 Gemini 分组中填写：
   - Key：你的 Gemini API Key；
   - Base URL：留空，或填 `https://generativelanguage.googleapis.com`（程序会自动加上 `/v1beta`）；
   - 模型：`gemini-2.5-pro`（断句稳定、质量高）或 `gemini-2.5-flash`（速度快、价格便宜）。

### Google Cloud / Vertex

Google Cloud / Vertex 的 API Key 也可以直接填到 Gemini 配置（Gemini 或 Gemini 高质量翻译器）中；Base URL 留空使用默认值，或保持 `https://generativelanguage.googleapis.com` 即可，无需改成其他地址。

## API OCR、上色与渲染配置要点

- AI OCR、AI 上色、AI 渲染在“API 管理”的“文字识别”“上色”“渲染”页签中分别配置，各页签下有 OpenAI 与 Gemini 两组 Key/Base/Model。
- 各功能优先读取自己的专用变量（`OCR_*`、`COLOR_*`、`RENDER_*`）；对应字段留空时自动回退到通用变量（`OPENAI_*`、`GEMINI_*`）。例如 AI OCR 的 `OCR_OPENAI_API_KEY` 未填时回退 `OPENAI_API_KEY`。
- 选模型时注意任务需要图片能力：AI OCR 识别文字，AI 上色 / AI 渲染需要出图模型，请确认所选模型支持对应任务。

## 常见问题

### 提示“API Key 无效”怎么办？

1. 检查 Key 是否完整复制（不要漏掉或带多余空格/换行）。
2. 检查 Base URL 是否正确（一般以 `/v1` 结尾）。
3. 确认账户余额或配额充足。
4. 检查网络连接（国外 API 可能需要代理）。

### API Key 泄露了怎么办？

1. 立即到对应平台删除（吊销）泄露的 Key。
2. 创建新的 API Key，并在“API 管理”中替换。
3. 检查账户用量与余额是否有异常。
