---
title: API Configuration Guide
description: How to apply for API keys for common online translation APIs and fill in Key, Base URL, and Model in API Management
pageId: desktop.api-management.api-key-guide
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# API Configuration Guide

This page explains how to apply for API keys for the most common online translation APIs and fill them in correctly on the “API Management” page. It only covers user actions; follow the actual pages of each platform. For connection tests, model fetching, and channel management after filling in the fields, see [Connection tests and model list](./connection-tests-and-model-list.md) and [API credentials, addresses, and models](./credentials-addresses-models.md).

## Model Selection Tips

- **Multimodal models (image-aware)**: they can see the page image, so translations fit the story and panels better. The high-quality translators (OpenAI High Quality / Gemini High Quality) require a multimodal model.
- **Text-only models**: they only receive the recognized text, so they are fast and cheap but cannot see the image; suitable for simple workloads.
- In general, larger models translate better. The `B` in a model name means billion parameters: `Qwen3-235B` is 235 billion parameters, `DeepSeek-V3-671B` is 671 billion, and `Llama-3-70B` is 70 billion.

Example multimodal models: `gpt-5.2`, `gemini-3-pro-preview`, `gemini-2.5-pro`, `grok-4.1`.
Example text-only models: `deepseek-chat`, `deepseek-reasoner`, `Qwen/Qwen3-235B-A22B`.

## OpenAI-Compatible Endpoints

The OpenAI translator works with almost every platform because nearly all AI platforms expose an OpenAI-compatible API:

- The Base URL usually ends with `/v1`, for example `https://api.deepseek.com/v1` or `https://api.siliconflow.cn/v1`.
- Some providers use a different version suffix; for example, Volcano Engine endpoints end with `/v3`.
- As long as the platform offers an OpenAI-compatible interface, you can use the OpenAI translator.

## Applying for Keys on Each Platform

### SiliconFlow

SiliconFlow is a China-based platform with many models such as Qwen and DeepSeek, gift credits for new users, and fast domestic access.

1. Visit the [SiliconFlow website](https://cloud.siliconflow.cn/) and register with your phone number (then finish verification).
2. Sign in to the console, open “API Keys” from the left menu, click the create button, and copy the generated key.
3. On “API Management → Translation” fill in:
   - Key: your SiliconFlow API key;
   - Base URL: `https://api.siliconflow.cn/v1`;
   - Model: pick any model from the [SiliconFlow Model Plaza](https://cloud.siliconflow.cn/models).

### DeepSeek

DeepSeek only offers text-only models and **does not support multimodal requests**, so it cannot be used with the “OpenAI High Quality” / “Gemini High Quality” translators; if you need image-aware translation, use a multimodal model instead.

1. Visit the [DeepSeek Platform](https://platform.deepseek.com/) and register (then finish verification).
2. Sign in and add credit (an empty balance makes requests fail).
3. Open “API Keys”, click “Create API Key”, name it, and copy the generated key. Save it immediately because it is not shown again after the dialog closes.
4. On “API Management → Translation” fill in:
   - Key: your DeepSeek API key;
   - Base URL: `https://api.deepseek.com/v1`;
   - Model: `deepseek-chat` (no reasoning, fast, but AI line breaking can be less stable) or `deepseek-reasoner` (reasoning, slower, more stable line breaking — recommended).

### Gemini

1. Visit [Google AI Studio](https://aistudio.google.com/apikey), sign in with your Google account, click “Create API Key”, choose or create a Google Cloud project, and copy the generated key.
2. On “API Management → Translation” under the Gemini group fill in:
   - Key: your Gemini API key;
   - Base URL: leave it empty, or enter `https://generativelanguage.googleapis.com` (the app adds `/v1beta` automatically);
   - Model: `gemini-2.5-pro` (stable line breaking, highest quality) or `gemini-2.5-flash` (fast and cheap).

### Google Cloud / Vertex

Google Cloud / Vertex API keys can also be entered directly in the Gemini configuration (Gemini or Gemini High Quality). Leave the Base URL empty for the default, or keep `https://generativelanguage.googleapis.com`; you do not need to change it to another host.

## API OCR, Colorization, and Rendering Setup Notes

- AI OCR, AI colorization, and AI rendering are configured on the “OCR”, “Colorization”, and “Render” tabs of “API Management”; each tab has OpenAI and Gemini Key/Base/Model groups.
- Each feature reads its own dedicated variables first (`OCR_*`, `COLOR_*`, `RENDER_*`); when those fields are empty, it falls back to the general variables (`OPENAI_*`, `GEMINI_*`). For example, AI OCR uses `OCR_OPENAI_API_KEY` and falls back to `OPENAI_API_KEY`.
- When picking a model, keep the image capability in mind: AI OCR reads text, while AI colorization / AI rendering need an image-generation model; make sure the selected model supports the task.

## FAQ

### “Invalid API Key” — What Should I Do?

1. Check that the key was copied completely (no missing characters or stray spaces/newlines).
2. Check that the Base URL is correct (it usually ends with `/v1`).
3. Make sure the account has enough balance or quota.
4. Check the network connection (some foreign APIs may require a proxy).

### My API Key Leaked — What Should I Do?

1. Delete (revoke) the leaked key on the platform immediately.
2. Create a new API key and replace it in “API Management”.
3. Check account usage and balance for anything abnormal.
