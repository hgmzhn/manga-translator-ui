---
title: Translator Selection and Target Languages
description: Choose the translation implementation, target language, and source-language filter
pageId: desktop.translator.selection-and-languages
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Translator Selection and Target Languages

This page documents desktop translator selection, target language, and source-language filtering after text-line merging. It does not document API slot rotation, prompt editing, context construction, or detailed translation-chain behavior.

## Feature boundary

- `translator.translator` selects OpenAI, Gemini, Sakura, high-quality variants, no translation, or original text. It changes the translation implementation, not endpoint rotation within one provider.
- `translator.target_lang` stores a three-letter code used as the target of an individual translation request.
- `translator.keep_lang` filters regions after text-line merging by detected source language. Non-matching regions remain unchanged and are not inpainted, translated, or rendered.
- `translator.no_text_lang_skip` controls whether text already detected as the target language may be skipped. Enabling “Don't Skip Target Lang” forces it through translation.
- API Key/Base/Model, `failover`/`round_robin`, prompts, glossary, streaming, RPM, and quality retries belong to API-management or other translator pages.

## UI operations

### Select values in the Translation settings tab

1. Open Settings and select the “Translation” tab.
2. Choose an implementation in “Translator”. Display text comes from the localized UI mapping, not directly from internal enum names.
3. Choose a language in “Target Language”. Display text comes from the interface language files and is reverse-mapped when saved; for example, “English” is stored as `ENG`.
4. In “Keep Source Language”, choose a source language or “No Filter”. With a language selected, only regions detected as that language continue to translation and later image processing.
5. Enable “Don't Skip Target Lang” to force target-language text through translation. Changes update memory immediately and are persisted by the configuration service.

The translator feature selector in API Management writes the same “Translator” setting and refreshes the required credential groups; it is not a separate translator setting. API slots only change the request endpoint inside the selected provider.

## Parameters

> For how each parameter's UI name, storage key, and default value map to each other, see [Options and I18n Matrix](../../reference/options-i18n-matrix.md).

### Translator

The “Translator” combo box is in Settings → Translation and decides which translation implementation is used:

- OpenAI: uses the OpenAI-compatible API.
- OpenAI High Quality (OpenAI高质量翻译): uses the OpenAI-compatible API with high-quality prompt/structured handling; the default option.
- Google Gemini: uses the Gemini API.
- Gemini High Quality (Gemini高质量翻译): uses the Gemini API with high-quality prompt/structured handling.
- Sakura: uses a Sakura service address/dictionary configuration.
- None (无): performs no translation.
- Original (原文): keeps the original text as the result.

Default: `openai_hq`.

### Target Language

The “Target Language” combo box is in Settings → Translation and chooses the target of a single translation request. It currently offers 25 languages: Simplified Chinese, Traditional Chinese, Czech, Dutch, English, French, German, Hungarian, Italian, Japanese, Korean, Polish, Portuguese (Brazil), Romanian, Russian, Spanish, Turkish, Ukrainian, Vietnamese, Arabic, Serbian, Croatian, Thai, Indonesian, and Filipino (Tagalog). Display text comes from the interface language files and is reverse-mapped to a three-letter code when saved.

Default: `CHS`.

### Keep Source Language

The “Keep Source Language” combo box is in Settings → Translation and filters regions after text-line merging by detected source language: choose “No Filter” to disable filtering; with a language selected, only regions detected as that language continue to translation and later image processing, and non-matching regions remain unchanged without erasure, translation, or rendering. The available languages are Simplified Chinese, Traditional Chinese, English, Japanese, Korean, and so on; the exact set is decided by the backend.

Default: `none` (No Filter).

### Don't Skip Target Lang

The “Don't Skip Target Lang” toggle is in Settings → Translation. When enabled, text already detected as the target language is also forced through translation; this increases requests and API cost.

Default: `false`.

## Runtime behavior

```mermaid
flowchart LR
    UI["Translation UI\ntranslator + target_lang"] --> Save["AppSettings / ConfigService"]
    Save --> Runtime["TranslatorConfig"]
    Runtime --> Gen["translator_gen"]
    Gen --> Impl["TRANSLATORS implementation"]
    Target["target_lang code"] --> Chain["TranslatorChain\ntranslator:target"]
    Gen --> Chain
    Chain --> Request["translate('auto', target)"]
    OCR["merged text regions"] --> Filter{"keep_lang / target-language skip"}
    Filter -->|kept| Request
    Filter -->|filtered| Original["unchanged region"]
    Request --> Output["translation -> inpainting/typesetting"]
```

The saved translator enum and target-language code are resolved into a chain, and implementations run in chain order. `translator_chain` or `selective_translation` is chaining/language-based selection, not API slot rotation. API Management can change the same `translator.translator` key; candidate resolution and cooldown remain API-management concerns.

## Dependencies and conflicts

- Detection/OCR must produce text regions and source-language information before `keep_lang` can operate; it runs after merging.
- `none` performs no translation while `original` explicitly preserves the original result. Neither requires a remote API; later rendering remains workflow-dependent.
- HQ options depend on the corresponding high-quality prompt resource. `extract_glossary` also depends on HQ prompt configuration.
- Changing target language changes the request and typesetting input, not OCR language, render direction, or provider.
- `keep_lang` filters by source language; `no_text_lang_skip` controls target-language skipping. Source filtering still applies first.

## Translator/API selection boundary

```mermaid
flowchart TD
    A["Translation setting or API feature selector"] --> B["translator.translator"]
    B --> C["OpenAI / Gemini / Sakura / none / original"]
    C --> D["feature-specific credentials"]
    D --> E["API candidates and rotation"]
    E --> F["HTTP request"]
    G["translator_chain"] --> C
    G -. "passes translation result onward; does not rotate endpoints" .-> C
```

Translator selection changes the implementation; the API feature selector is another UI write point; Key/Base/Model slots and `failover`/`round_robin` rotate only within the selected implementation; `translator_chain` passes output to the next translator.
