---
title: Connection Tests and Model List
description: Test API channel connections, review test results, and fetch the available model list from the server into the Model field
pageId: desktop.api-management.connection-tests-and-model-list
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Connection Tests and Model List

After filling in Key, Base, and Model on the API Management page, use the features on this page to verify that a connection actually works, then fetch model names from the server back into the Model field. Testing does more than show a result dialog: success or failure is written into the in-memory channel status, which also affects the candidate-availability check before starting translation.

This page does not cover filling, masking, or `.env` persistence of credential fields (see [API Credentials, Addresses, and Models](./credentials-addresses-models.md)), slot add/delete and rotation strategy (see [Slots and rotation](./slots-and-rotation.md)), or the cooldown, unavailable, and auto-recovery state machine for real requests (see [Failures, cooldown, and recovery](./failures-cooldown-and-recovery.md)).

## Feature boundary

- “Test” (`Test`) tests a single API channel (the button on the right of the Key row); “Test Current Tab” (`Test Current Tab`) batch-tests every configured channel on the current feature tab. The batch test applies only to the current tab (Translation/OCR/Colorization/Render) and never crosses tabs.
- Test results are written into the in-memory status through `record_api_success` / `record_api_failure`; before starting translation, `validate_api_candidate_availability()` checks each required channel group for available candidates and blocks the start with a warning when none remain.
- “Get Models” (`Get Models`) fetches the model list from the server and writes the selected model back into the Model input. Only the Model row has this button; the Key row has only “Test”, and the Base row has neither button.
- This page does not cover the `run_with_api_candidates` failover/round_robin request retries, cooldown timing, or recovery logic; that state machine belongs to [Failures, cooldown, and recovery](./failures-cooldown-and-recovery.md).
- Testing and model fetching issue real network requests and require the channel to have Key/Base/Model filled in; local OpenAI-compatible endpoints (`localhost`, private IPs, `.local`, etc.) automatically use the `ollama` placeholder key when the key is empty.

## UI operations

### Test a single API channel

1. Open “API Management” (`API Management`) and choose the “Translation” (`Translation`), “OCR” (`OCR`), “Colorization” (`Colorization`), or “Render” (`Render`) tab.
2. Click “Test” (`Test`) on the right of the Key row in an API slot card.
3. A progress dialog appears with “Testing” (`Testing`) and “Testing API connection, please wait...” (`Testing API connection, please wait...`); you can stop it with “Cancel” (`Cancel`). Cancelling shows no result dialog.
4. On success, an information dialog appears with the title “API connection test successful!” (`API connection test successful!`); on failure, an “Error” (`Error`) dialog appears with the title “API connection test failed” (`API connection test failed`) and a body containing classified troubleshooting advice and an API address example.

### Test all channels on the current tab

1. Click “Test Current Tab” (`Test Current Tab`) on the right of the feature-selector row at the top of the tab.
2. An “API Batch Test” (`API Batch Test`) progress dialog appears, stating “Testing {count} API channels with concurrency {concurrency}...” (`Testing API channels`); the concurrency is fixed at 3.
3. When it finishes, an “API Batch Test Results” (`API Batch Test Results`) dialog appears with the summary “{total} total, {available} available, {unavailable} unavailable” (`API batch test summary`). The body lists only the unavailable channels, each prefixed with `[unavailable]` plus the error message; when everything is available it shows only “No unavailable API” (`No unavailable API`).
4. If the current tab has no testable channels (no Key/Base filled and the local-placeholder rule does not apply), clicking it only shows “No API channels to test” (`No API channels to test`).

### Fetch the model list and write it into the Model field

1. Click “Get Models” (`Get Models`) on the right of the Model row in an API slot card.
2. A “Get Models” progress dialog appears with “Fetching models, please wait...” (`Fetching models, please wait...`); it can be cancelled.
3. On success, a “Select Model” (`Select Model`) dialog appears with the prompt “Available models:” (`Available models:`), a search box placeholder of “Search models...” (`Search models...`), and “OK” (`OK`) / “Cancel” (`Cancel`) buttons; OK stays disabled until a model is selected and the single filtered result is auto-selected.
4. After selecting a model and clicking OK, the model name is written back into the Model input and saved; an empty server response shows “No models available” (`No models available`); a fetch failure shows “Failed to get models” (`Failed to get models`).

## Test result display {#test-result-display}

### Single-channel test results

The success dialog title is fixed as “API connection test successful!”, and the body shows the detail returned by the test function (for example a hardcoded Chinese message that the connection succeeded and model {model} is available). The failure dialog title is “API connection test failed”, and the body classifies advice by error keywords, then appends “API 地址示例：{address}” and the original error (wrapped at 60 characters). These detail strings are currently hardcoded Chinese and do not follow the UI language.

### Batch test results

The batch result dialog summarizes by available/unavailable. The title shows total and available/unavailable counts; the body lists only the failed channels, each line prefixed with `[unavailable]` followed by the channel label (for example `OpenAI API Key #2`) and the wrapped error; when all pass, the body is empty and shows “No unavailable API”. After the dialog closes, the slot cards and status notices are rebuilt from the latest status.

### Slot status notice and restore

When a channel is `unavailable` (permanently unavailable) or `cooldown` (cooling down), the corresponding slot card inserts a colored status notice: `unavailable` shows “Unavailable” (`Unavailable`) and `cooldown` shows “Cooling down” (`Cooling down`), with a sync-icon restore button on the right (tooltip “Restore”). Clicking restore calls `clear_api_status` to remove the in-memory status and immediately rebuilds the group.

## Runtime behavior {#runtime-behavior}

### Test-target detection and channel collection

Each environment-variable key is split by scope (`OCR_` / `COLOR_` / `RENDER_`) and provider (`OPENAI` / `GEMINI` / `DEEPSEEK` / `GROQ` / `CUSTOM_OPENAI` / `SAKURA`) before the test target is resolved: the scope decides `openai_ocr`, `gemini_ocr`, `openai_colorizer`, `gemini_renderer`, and so on; unscoped providers map to `openai`, `gemini`, `sakura`, and others; the translation tab also falls back to the current translator key. The batch test collects only keys whose field is `API_KEY` / `AUTH_KEY` / `TOKEN` under the current tab scope and dedupes by “feature:provider:slot”; on the translation tab with Sakura selected it additionally collects `SAKURA_API_BASE` as one item. A channel enters the test list only when a Key is configured, or when Sakura or a local OpenAI-compatible endpoint only needs an address.

### How test requests are built

`test_api_connection_async()` dispatches to different implementations by test target; all of them issue real HTTP requests:

- OpenAI text (translation): with a model filled in it calls `chat.completions.create` for that model, otherwise `models.list`; client timeout is 30 seconds.
- OpenAI OCR: without a model it uses the default `gpt-4o` and sends a 50×50 white test image with “Read the image and reply with OK.”; timeout is 30 seconds.
- OpenAI colorization/rendering: without a model it uses the default `gpt-image-1` and calls the image-generation interface; timeout is 60 seconds.
- Gemini text/OCR: without a model, OCR uses the default `gemini-1.5-flash`; it generates content or lists models; timeout is 30 seconds.
- Gemini colorization/rendering: without a model it uses the default `gemini-2.0-flash-preview-image-generation`, requests the TEXT+IMAGE modality, and disables safety thresholds; timeout is 60 seconds.
- Sakura: uses an OpenAI-compatible client with a fixed placeholder key to test the model or list models; the test path sets no explicit short timeout and relies on SDK defaults.

OpenAI-family tests prefer the `curl_cffi` client with a browser fingerprint (`impersonate="chrome110"`) and fall back to the standard `openai` client; Gemini-family tests prefer `AsyncGeminiCurlCffi` and fall back to the synchronous `google-genai` client (run in the event-loop executor).

### Model list fetching

`get_available_models_async()` calls `models.list()` for OpenAI-compatible targets, reads all `data[].id` values, and applies `sort(reverse=True)` so newer models come first, with a 60-second client timeout; Gemini uses `models.list()` (the curl_cffi branch reads `id` directly, while the google-genai fallback strips the `models/` prefix); Sakura also uses OpenAI-compatible `models.list()`. The model list comes from the server response and depends on credentials, address, and the remote service, so it cannot be a static option table. Unsupported targets (neither OpenAI-compatible nor Gemini/Sakura) return a hardcoded Chinese message that this translator does not support fetching the model list, and show “Failed to get models”.

### Failure classification and user advice

`_format_test_connection_error()` classifies errors by keyword into three categories: network-class (connection, timeout, DNS, host, `curl: (7)`, `curl: (28)`, etc.) suggests checking model/address/key first, then the network and enabling TUN (virtual NIC mode); service-class (502/503/504, service unavailable, bad gateway, upstream, etc.) suggests retrying later or switching API sites/relays; everything else suggests checking the model, address, and key. The body also appends an example API address. `record_api_failure()` then marks the state as `failed` / `cooldown` / `unavailable` based on 400/402/404/429 and message markers.

### Timeouts and cancellation

Single-channel tests, batch tests, and model fetching all run in separate async tasks with a cancellable progress dialog. Cancelling the dialog or the task closes the progress dialog and shows no result dialog. Client timeouts (30 seconds for text/OCR, 60 seconds for images and model fetching) are passed by the test code; timeout exceptions enter failure classification and are reported under the “connection error, timeout” advice.

## Test and model-fetch data flow {#flow-diagram}

```mermaid
flowchart LR
    A["Key row 「Test」 button"] --> S1["Single-channel async test"]
    B["Tab-top 「Test Current Tab」 button"] --> C["Collect configured channels on the current tab"]
    C --> B1["Batch async test\nconcurrency fixed at 3"]
    S1 --> R["record_api_success / record_api_failure"]
    B1 --> R
    R --> ST["In-memory channel status\navailable / failed / cooldown / unavailable"]
    ST --> D1["Test result dialog"]
    ST --> D2["Slot card status notice\nUnavailable / Cooling down + restore button"]
    ST --> G["Candidate-availability check before translation"]
    G -->|"required group has no candidate"| BL["Warn and block translation start"]
    M["Model row 「Get Models」 button"] --> F["models.list fetch"]
    F --> DL["Model selector dialog\nsearch / OK / Cancel"]
    DL -->|"model selected"| W["Write back to Model input and save"]
```

The diagram shows the source-confirmed data flow: test results feed the result dialogs, the slot-card status notices, and the translation-start gate through shared in-memory status; model fetching goes through `models.list` alone and writes back to configuration only after the user selects a model. It does not claim that a successful test guarantees a successful real translation — real requests are still governed by the rotation strategy and the cooldown/recovery state machine; see [Failures, cooldown, and recovery](./failures-cooldown-and-recovery.md).

## Dependencies and conflicts

- Tests and model fetching depend on the channel Key/Base/Model and the network; this page never writes real keys and never treats the server model list as a static enum.
- Status written by tests is in-memory (`_API_STATUS`) and is not persisted to `.env` or `config.json`; a restart clears the state and cards show no historical notice.
- The pre-translation candidate check treats channels in `cooldown` / `unavailable` as unusable; re-run “Test Current Tab” or click the restore button before starting.
- With hybrid OCR enabled, the OpenAI/Gemini groups of the primary and secondary OCR may appear together, and the batch test includes both groups in the current tab.
- The Sakura group has no Model field, so there is no “Get Models” button; a test target containing `sakura` only needs the address to run.
- Model fetching differs between OpenAI-compatible endpoints and Gemini (sorting, prefix handling, client fallback), so returned model-name formatting can differ; confirm compatibility with the `model` field of the request body before writing it into the Model field.
