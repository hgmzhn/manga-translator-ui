# API Configuration Guide

This document explains how to register, choose, and configure the most commonly used online APIs for the current desktop UI.

---

## Table of Contents

- [Model Selection Advice](#model-selection-advice)
- [General API Setup Rules](#general-api-setup-rules)
- [SiliconFlow API Setup](#siliconflow-api-setup)
- [DeepSeek API Setup](#deepseek-api-setup)
- [Google Gemini API Setup](#google-gemini-api-setup)
- [API OCR Setup (OpenAI OCR / Gemini OCR)](#api-ocr-setup-openai-ocr--gemini-ocr)
- [API Colorization Setup (OpenAI Colorizer / Gemini Colorizer)](#api-colorization-setup-openai-colorizer--gemini-colorizer)
- [API Rendering Setup (OpenAI Renderer / Gemini Renderer)](#api-rendering-setup-openai-renderer--gemini-renderer)
- [FAQ](#faq)

---

## Model Selection Advice

- High-quality translators need multimodal models that can see the page image, such as GPT-, Gemini-, or Grok-class multimodal models.
- In general, larger models usually translate better than smaller ones.

### How to read parameter size

Model names often include the parameter count:

- `Qwen3-235B` -> 235 billion parameters
- `DeepSeek-V3-671B` -> 671 billion parameters
- `Llama-3-70B` -> 70 billion parameters

`B` means billion.

### Current translator types in the app

The current `Translator` dropdown on `Translation Interface` separates text-only and multimodal use cases:

- **Text-only translators**:
  - `OpenAI`
  - `Google Gemini`
- **Multimodal translators**:
  - `OpenAI High Quality`
  - `Gemini High Quality`

Use the high-quality translators when your selected model supports image input.

### Example multimodal models

| Model | Platform | Notes |
|------|------|------|
| `gpt-5.2` | OpenAI | Current ChatGPT-class multimodal example |
| `gemini-3-pro-preview` | Google | Current Gemini multimodal example |
| `gemini-2.5-pro` | Google | Stable Gemini multimodal example |
| `grok-4.1` | xAI | Current Grok multimodal example |

### Example text-only models

| Model | Platform | Notes |
|------|------|------|
| `deepseek-chat` | DeepSeek | Fast |
| `deepseek-reasoner` | DeepSeek | Uses reasoning, more stable for line breaking |
| `Qwen/Qwen3-235B-A22B` | SiliconFlow | Large Qwen 3 example |

---

## General API Setup Rules

### Where to configure APIs in the current UI

Use these pages together:

1. `Translation Interface`
2. `API Management`

The `API Management` page currently has four tabs:

- `Translation`
- `OCR`
- `Colorization`
- `Render`

Common operations on this page:

- API key rows provide a `Test` button
- model rows provide a `Get Models` button
- if the provider supports model listing, `Get Models` can fill the model field for you

### Translator categories

The app currently provides two main translator interface styles:

#### Text-only translators (`OpenAI` / `Google Gemini`)

- use text API requests
- only send OCR text
- faster
- cheaper
- good for simpler workloads

#### High-quality translators (`OpenAI High Quality` / `Gemini High Quality`)

- use multimodal requests
- send image plus text
- the model can see the manga page
- usually more accurate
- costs more
- requires a model with image support

If your provider supports multimodal requests, the high-quality translators are strongly recommended.

### API base URL rules

#### OpenAI-compatible APIs

The `OpenAI` translator path can work with many providers because a large number of platforms expose OpenAI-compatible APIs.

- In most cases, the API base ends with `/v1`
  - Example: `https://api.openai.com/v1`
  - Example: `https://api.deepseek.com/v1`
  - Example: `https://api.siliconflow.cn/v1`
- Some providers use a different version suffix
  - Example: some Volcano Engine endpoints use `/v3`

If your provider offers an OpenAI-compatible interface, you can usually configure it through the `OpenAI` path.

#### Gemini APIs

- For Gemini, you usually enter the base host only:
  - `https://generativelanguage.googleapis.com`
- The app adds the API version path automatically
- If you use an official Google AI Studio key, leaving `Gemini API Base` empty is usually fine

- Google Cloud or Vertex-related API keys can also be entered directly in the Gemini fields
- You do not need to change the Gemini base URL. Leave it empty for the default official host, or keep `https://generativelanguage.googleapis.com`

### Current translation-tab field names

On `API Management` -> `Translation`, the most commonly used rows are:

- `OpenAI API Key`
- `OpenAI Model`
- `OpenAI API Base`
- `Gemini API Key`
- `Gemini Model`
- `Gemini API Base`
- `DeepSeek API Key`
- `DeepSeek Model`
- `DeepSeek API Base`
- `Groq API Key`
- `Groq Model`
- `Sakura API Base`
- `Custom OpenAI API Key`
- `Custom OpenAI Model`
- `Custom OpenAI API Base`
- `Custom OpenAI Model Config`

### Recommended current workflow

1. Choose the translator on `Translation Interface`
2. Open `API Management`
3. Open the matching tab
4. Fill the API key, model, and base URL fields
5. Click `Test`
6. Use `Get Models` if you want the app to query available models
7. Return to `Translation Interface`
8. Start translation

---

## SiliconFlow API Setup

SiliconFlow is a China-based AI platform with many model choices, gift credits for new users, and good domestic access speed.

Advantages:

- free or gift quota for new accounts
- supports many models such as Qwen and DeepSeek
- good pricing
- direct domestic access for many users

### 1. Register an account

1. Visit [SiliconFlow](https://cloud.siliconflow.cn/)
2. Click registration
3. Register with your phone number
4. Finish account verification

### 2. Create an API key

1. Sign in to the console
2. Open `API Keys` from the left menu
3. Click the create button
4. Copy the generated API key

### 3. Configure it in the app

If you want text-only translation:

1. Open `Translation Interface`
2. Set `Translator` to `OpenAI`
3. Open `API Management`
4. Open the `Translation` tab
5. Fill:
   - `OpenAI API Key`: your SiliconFlow API key
   - `OpenAI API Base`: `https://api.siliconflow.cn/v1`
   - `OpenAI Model`: any model you want to use from the SiliconFlow model list
6. Click `Test`
7. Optionally click `Get Models`

If you want multimodal translation:

1. Open `Translation Interface`
2. Set `Translator` to `OpenAI High Quality`
3. Use the same `OpenAI API Key`, `OpenAI API Base`, and `OpenAI Model` fields
4. Make sure the selected model really supports image input

Useful note:

- You can browse the available models from the [SiliconFlow Model Plaza](https://cloud.siliconflow.cn/models)

---

## DeepSeek API Setup

DeepSeek offers high-quality and relatively low-cost text translation.

Important limitation:

- DeepSeek is text-only
- it does not support the multimodal `OpenAI High Quality` path
- if you want the best image-aware translation, use a multimodal OpenAI- or Gemini-class model instead

### 1. Register an account

1. Visit the [DeepSeek Platform](https://platform.deepseek.com/)
2. Register with email or phone
3. Finish verification

### 2. Add credit

1. Sign in
2. Open account recharge from the user menu
3. Choose an amount
4. Pay with the supported payment method

### 3. Create an API key

1. Open `API Keys`
2. Click the create button
3. Name the key
4. Copy the generated key
5. Save it immediately, because many providers do not show it again after the dialog closes

### 4. Configure it in the app

1. Open `Translation Interface`
2. Set `Translator` to `OpenAI`
3. Open `API Management`
4. Open `Translation`
5. Fill:
   - `OpenAI API Key`: your DeepSeek key, such as `sk-xxxxxxxxxxxxxxxx`
   - `OpenAI API Base`: `https://api.deepseek.com/v1`
   - `OpenAI Model`: choose one of:
     - `deepseek-chat`: faster, but AI line breaking can be less stable
     - `deepseek-reasoner`: slower, but more stable for line breaking
6. Click `Test`

Recommendation:

- If AI line breaking matters to your workflow, `deepseek-reasoner` is the safer choice

---

## Google Gemini API Setup

Google Gemini is Google's multimodal model family and is a strong choice for manga translation.

Current note from the original Chinese guide:

- Google AI Studio is fully paid now and no longer provides a free allowance

### 1. Get an API key

1. Visit [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with a Google account
3. Click `Create API Key`
4. Select or create a Google Cloud project
5. Copy the generated API key

### 2. Configure it in the app

If you want text-only translation:

1. Open `Translation Interface`
2. Set `Translator` to `Google Gemini`
3. Open `API Management`
4. Open `Translation`
5. Fill:
   - `Gemini API Key`: your Gemini API key
   - `Gemini API Base`: leave empty for the default official host, or enter `https://generativelanguage.googleapis.com`
   - `Gemini Model`: choose the model you want
6. Click `Test`

If you want multimodal translation:

1. Open `Translation Interface`
2. Set `Translator` to `Gemini High Quality`
3. Reuse the same `Gemini API Key`, `Gemini API Base`, and `Gemini Model` fields

Suggested model choices from the source guide:

- `gemini-2.5-pro`: best quality, stable for line breaking
- `gemini-2.5-flash`: faster and cheaper
4. The Google Cloud or Vertex-related API key you use can also go directly into the same Gemini fields
5. Keep the default official Gemini base URL and do not replace it with another host

---

## API OCR Setup (OpenAI OCR / Gemini OCR)

These OCR backends are mainly used by the Qt desktop UI and are configured through both `Settings` and `API Management`. The web admin API-key page now also exposes matching OCR groups for server-side runtime config.

### Where they appear in the UI

1. Open `Settings`
2. Open `OCR`
3. Set `OCR Model` to:
   - `openai_ocr`
   - `gemini_ocr`

Then configure the keys in:

1. `API Management`
2. `OCR`

### Important label behavior in `API Management`

Inside the `OCR` tab, the row labels are shown as plain provider labels:

- `OpenAI API Key`
- `OpenAI Model`
- `OpenAI API Base`
- `Gemini API Key`
- `Gemini Model`
- `Gemini API Base`

But the actual env vars behind them are OCR-specific:

- `OCR_OPENAI_API_KEY`
- `OCR_OPENAI_MODEL`
- `OCR_OPENAI_API_BASE`
- `OCR_GEMINI_API_KEY`
- `OCR_GEMINI_MODEL`
- `OCR_GEMINI_API_BASE`

If the OCR-specific values are empty, the app falls back to:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_API_BASE`

or:

- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GEMINI_API_BASE`

### AI OCR prompt

The current UI uses:

- `AI OCR Prompt`
- `AI OCR Concurrency`

Related file:

- `dict/ai_ocr_prompt.yaml`

Editing flow:

1. Open `Settings`
2. Open `OCR`
3. Find `AI OCR Prompt`
4. Click `Edit`

Prompt file notes:

- YAML file
- main key: `ai_ocr_prompt`
- if an old `dict/ai_ocr_prompt.json` still exists locally, the app migrates it to YAML on first use

### Notes

- `OpenAI OCR` and `Gemini OCR` call the API once per text box
- after text is recognized, text color is still extracted by the local `48px` model
- recognition quality is often very good
- the improvement over local OCR is usually not dramatic enough to justify request-count billing on some providers
- Google Cloud or Vertex-related API keys can also be entered in `OCR_GEMINI_*` or the regular `GEMINI_*` fields; keep the default official Gemini base URL unchanged

---

## API Colorization Setup (OpenAI Colorizer / Gemini Colorizer)

These colorizers are desktop-side colorization options, not generic translation APIs. The web admin API-key page now also exposes matching colorization groups for server-side runtime config.

### Where they appear in the UI

1. Open `Settings`
2. Open `Mode Specific`
3. In the `Colorization` group, set `Colorization Model` to:
   - `OpenAI Colorizer`
   - `Gemini Colorizer`

Then configure the keys in:

1. `API Management`
2. `Colorization`

### Important label behavior in `API Management`

Inside the `Colorization` tab, the row labels are shown as plain provider labels:

- `OpenAI API Key`
- `OpenAI Model`
- `OpenAI API Base`
- `Gemini API Key`
- `Gemini Model`
- `Gemini API Base`

But the actual env vars behind them are colorization-specific:

- `COLOR_OPENAI_API_KEY`
- `COLOR_OPENAI_MODEL`
- `COLOR_OPENAI_API_BASE`
- `COLOR_GEMINI_API_KEY`
- `COLOR_GEMINI_MODEL`
- `COLOR_GEMINI_API_BASE`

If the colorization-specific values are empty, the app falls back to the regular provider variables.

### AI colorizer prompt

The current UI uses:

- `AI Colorizer Prompt`
- `AI Colorizer History Pages`

Related file:

- `dict/ai_colorizer_prompt.yaml`

Editing flow:

1. Open `Settings`
2. Open `Mode Specific`
3. Find `AI Colorizer Prompt`
4. Click `Edit`

Prompt file notes:

- YAML file
- main key: `ai_colorizer_prompt`

### Notes

- `OpenAI Colorizer` and `Gemini Colorizer` work on full pages
- the current desktop UI exposes `AI Colorizer History Pages` instead of the older `ai_colorizer_concurrency` wording used in older docs
- multi-image prompts can label reference pages as `Image 1`, `Image 2`, and so on
- Google Cloud or Vertex-related API keys can also be entered in `COLOR_GEMINI_*` or the regular `GEMINI_*` fields; keep the default official Gemini base URL unchanged

If `COLOR_OPENAI_API_BASE` or `OPENAI_API_BASE` points to different OpenAI-compatible image backends, the app automatically adapts the request format. Examples from the source guide:

- `https://api.siliconflow.cn/v1`: SiliconFlow image-generation style
- `https://dashscope.aliyuncs.com/api/v1`
- `https://dashscope-intl.aliyuncs.com/api/v1`
- Volcano Engine or other OpenAI-compatible image endpoints

If `Use Custom API Params` is enabled, the app also merges the `colorizer` group from `config/custom_api_params.json` into the backend request payload.

---

## API Rendering Setup (OpenAI Renderer / Gemini Renderer)

These renderers are desktop-side full-page rendering options that combine cleaned page images with translated text regions. The web admin API-key page now also exposes matching rendering groups for server-side runtime config.

### Where they appear in the UI

1. Open `Settings`
2. Open `Typesetting`
3. Set `Renderer` to:
   - `OpenAI Renderer`
   - `Gemini Renderer`

Then configure the keys in:

1. `API Management`
2. `Render`

### Important label behavior in `API Management`

Inside the `Render` tab, the row labels are shown as plain provider labels:

- `OpenAI API Key`
- `OpenAI Model`
- `OpenAI API Base`
- `Gemini API Key`
- `Gemini Model`
- `Gemini API Base`

But the actual env vars behind them are render-specific:

- `RENDER_OPENAI_API_KEY`
- `RENDER_OPENAI_MODEL`
- `RENDER_OPENAI_API_BASE`
- `RENDER_GEMINI_API_KEY`
- `RENDER_GEMINI_MODEL`
- `RENDER_GEMINI_API_BASE`

If the render-specific values are empty, the app falls back to the regular provider variables.

### AI renderer prompt

The current UI uses:

- `AI Renderer Prompt`
- `AI Renderer Concurrency`

Related file:

- `dict/ai_renderer_prompt.yaml`

Editing flow:

1. Open `Settings`
2. Open `Typesetting`
3. Find `AI Renderer Prompt`
4. Click `Edit`

Prompt file notes:

- YAML file
- main key: `ai_renderer_prompt`

### Notes

- `OpenAI Renderer` and `Gemini Renderer` work on full pages
- the actual request combines:
  - the cleaned page image
  - a numbered annotation image similar to the HQ translation workflow
  - the translated text list for the matching numbers
- translated sound effects are also sent together when available
- concurrency is controlled by `AI Renderer Concurrency`
- Google Cloud or Vertex-related API keys can also be entered in `RENDER_GEMINI_*` or the regular `GEMINI_*` fields; keep the default official Gemini base URL unchanged

If `RENDER_OPENAI_API_BASE` or `OPENAI_API_BASE` points to a special OpenAI-compatible image backend, the app adapts the request format automatically, using rules similar to `OpenAI Colorizer`.

If `Use Custom API Params` is enabled, the app also merges the `render` group from `config/custom_api_params.json` into the backend request payload.

---

## FAQ

### Q1: Which API is the most recommended?

Answer:

- Best cost-performance for many mainland users: DeepSeek
- Best overall quality: multimodal OpenAI- or Gemini-class models

### Q2: What should I do if an API key leaks?

Answer:

1. Delete or revoke the leaked API key immediately on the provider platform
2. Create a new key
3. Check account usage or balance for abnormal activity

### Q3: What should I do if the app says the API key is invalid?

Answer:

1. Check whether the full API key was copied correctly
2. Check whether the API base URL is correct
3. Check whether the account has available balance or quota
4. Check the network connection
5. Use the `Test` button in `API Management`

---

Back to [README_EN](../../README_EN.md) | Back to [Usage Guide](./USAGE.md)
