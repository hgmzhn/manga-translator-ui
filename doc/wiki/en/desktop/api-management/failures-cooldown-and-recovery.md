---
title: Failures, Cooldown, and Recovery
description: State machine for cooldown, unavailability, recovery, and re-failure after a candidate request fails, plus timeout and cooldown parameters
pageId: desktop.api-management.failures-cooldown-and-recovery
lang: en-US
outline: [2, 4]
lastUpdated: true
---

# Failures, Cooldown, and Recovery

When a request to an API candidate channel (a numbered slot used by translation, OCR, colorization, or rendering) fails, the program keeps an in-memory status for that candidate and uses it to decide whether later requests may still select it. This page documents the state machine after a failure — cooldown, unavailability, recovery to available, and why a wrong configuration fails again after being restored — and lists the cooldown and timeout parameters.

This page does not cover adding/deleting slots, numbering badges, or the two rotation policies (see [API Slots and Rotation](./slots-and-rotation.md)), the connection-test dialogs (see [Connection Tests and Model List](./connection-tests-and-model-list.md)), or the full parameters of ordinary request retries (see [Retries, Rate Limits, and Quality](../translator/retry-rate-limit-and-quality.md)).

## Feature boundary {#feature-boundary}

- This page covers per-candidate endpoint status: each candidate is identified by feature, provider, slot, base URL, model, and a key fingerprint; the status lives in memory and is never written to `.env` or `config.json`.
- Only rate-limit errors enter “Cooling down”, and only permanent errors enter “Unavailable”; every other error is recorded as “Failed” and does not prevent the candidate from being selected again.
- The state machine applies to the translation, OCR, colorization, and rendering API groups alike, because all four consumers call the same rotation entry point `run_with_api_candidates`.
- Status is process-local: restarting the app or editing Key/Base/Model (which changes the status identity) invalidates old state; clicking the “Restore” (`Restore`) button on a card actively clears one candidate’s state.

## UI status and operations {#ui-status-and-operations}

Open “API Management” (`API Management`). A status bar appears below the title of a slot card. The bar appears only for “Cooling down” or “Unavailable”; an ordinary “Failed” state shows no bar. The “Restore” (`Restore`) button on the right calls `clear_api_status` and only clears the in-process failure state; it never edits any `.env` value.

1. Use “Test Current Tab” (`Test Current Tab`) or the inline “Test” (`Test`) button to verify a channel. A successful test marks the candidate available; a failed test enters cooldown, unavailability, or a plain failure record depending on the error type.
2. A cooling-down candidate is automatically considered again after its cooldown expires; an unavailable candidate stays excluded until you click Restore or edit the credentials.
3. Before starting a translation, the app runs a candidate-availability check: if every candidate of a required feature group is unavailable, startup is blocked with “No available API candidates” (`No available API candidates`), the details list the affected channels, and the suggestion is to re-enable the matching key/channel or use “Test Current Tab” before starting.

| UI call key | English actual value | Simplified Chinese actual value |
| --- | --- | --- |
| `API slot cooldown marker` | Cooling down | 冷却中 |
| `API slot unavailable marker` | Unavailable | 不可用 |
| `Restore API channel` | Restore | 恢复 |
| `Test` | Test | 测试 |
| `Test Current Tab` | Test Current Tab | 测试当前页 |
| `API batch test summary` | {total} total, {available} available, {unavailable} unavailable | 共 {total} 个，可用 {available} 个，不可用 {unavailable} 个 |
| `API candidate availability failed` | No available API candidates | 没有可用的 API 候选 |
| `API candidate availability failed details` | The following API channels have no available candidates:<br>{details}<br><br>Re-enable the corresponding key/channel in API Management, or use "Test Current Tab" before starting. | 以下 API 通道当前没有可用候选：<br>{details}<br><br>请在 API 管理里重新启用对应 Key/通道，或使用「测试当前页」确认后再开始。 |
| `No API channels to test` | No API channels to test | 没有可测试的 API 通道 |
| `API test unavailable` | unavailable | 不可用 |
| `No unavailable API` | No unavailable API | 无不可用 API |

The status bar text distinguishes only “Cooling down” and “Unavailable”; the remaining cooldown time is not shown in the UI.

## State machine: cooldown, unavailability, recovery, and re-failure {#state-machine}

```mermaid
stateDiagram-v2
    [*] --> Available: startup, Restore button, or credential edit
    Available --> Requesting: selected by the rotation policy
    Requesting --> Available: request succeeded (state rewritten to available)
    Requesting --> Cooldown: 429, rate limit, or Retry-After
    Requesting --> Unavailable: 402, 404, matching 400, or quota/billing
    Requesting --> Failed: network, 5xx, or other errors
    Failed --> Requesting: only recorded, does not block later selection
    Cooldown --> Requesting: cooldown_until passed, automatically eligible again
    Cooldown --> Available: a test succeeds during cooldown
    Cooldown --> Unavailable: a test hits a permanent error during cooldown
    Unavailable --> Available: Restore button clears the state
    Available --> Unavailable: wrong configuration fails again after restore
```

- **Available**: no status record, or the last request/test succeeded. The candidate may take part in later selection.
- **Cooldown**: the status record contains `cooldown_until`; until that time `is_endpoint_unavailable` returns true and the candidate is skipped. After expiry it becomes eligible again automatically, but the state field still reads “Cooldown” until the next success rewrites it to “Available”.
- **Unavailable**: a permanent error; excluded for the whole process until `clear_api_status` (the Restore button), a credential edit, or an app restart.
- **Failed**: other errors only record `last_error`; they do not affect later selection, and the same candidate may be picked again by the next request.
- **Requesting**: the candidate was selected and an actual request is sent; success, rate limit, permanent error, or ordinary error rewrite the state to the corresponding state above.

`run_with_api_candidates` builds the candidate list once at the start of a call via `iter_api_candidates`: unavailable or cooling-down candidates are filtered out from the beginning, so the state machine mainly affects the *next* request, not the one currently running.

## Cooldown and timeout parameters {#cooldown-and-timeout-parameters}

There is **no settings control for the cooldown duration**; it is decided entirely by the server response and code constants. The only related UI setting is the ordinary retry count on the same candidate (`cli.attempts`).

| Parameter/constant | Source and storage | Default | Role |
| --- | --- | --- | --- |
| `cli.attempts` (UI: Retry Attempts / 重试次数) | `manga_translator/config.py`, settings input | Core `-1`, Qt `-1`, release `3` | How many times a failed candidate is retried before it is recorded as failed and the next candidate is tried; `-1` means unlimited retries. Full details in [Retries, Rate Limits, and Quality](../translator/retry-rate-limit-and-quality.md) |
| `Retry-After` response header | server HTTP response | none (server decides) | Preferred cooldown seconds on rate limit; supports integer seconds or an HTTP date and is clamped to `[1, 600]` seconds |
| `DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS` | `manga_translator/api_key_rotation.py` constant | `60` | Default cooldown seconds when the server sends no `Retry-After` |
| `MAX_RATE_LIMIT_COOLDOWN_SECONDS` | `manga_translator/api_key_rotation.py` constant | `600` | Upper bound for cooldown seconds (10 minutes) |
| Same-candidate retry backoff | `run_with_api_candidates` | `min(1.0 * attempt number, 3.0)` seconds | Delay between retries on the same candidate, increasing 1 s, 2 s, ... capped at 3 s |
| `DEFAULT_ROTATION_STRATEGY` | `manga_translator/api_key_rotation.py` constant | `failover` | Fallback when the strategy key is missing or invalid |
| `MAX_ROTATION_SLOTS` / `API_ROTATION_UI_MAX_SLOTS` | constants | `30` / `10` | Total slot cap and UI cap (`min(10, 30)`) |
| Status identity | `make_endpoint_status_key` | `feature:provider:slot:base_url:model:key-fingerprint` | Changing any identity part creates a new candidate; old cooldown/unavailable state no longer applies |

“Ordinary retries” (`cli.attempts`), “Cooldown”, and “Unavailable” are three different layers: ordinary retries run inside the same candidate, while cooldown and unavailability decide whether later requests select the candidate at all. Do not mistake the ordinary retry count for the cooldown duration.

## What makes a candidate available again {#recovery-conditions}

| Recovery path | Trigger | Effect | Note |
| --- | --- | --- | --- |
| Cooldown expires | `cooldown_until` has passed | The candidate is automatically eligible again | The state field still reads “Cooldown” until the next success rewrites it to “Available” |
| Request/test succeeds | `record_api_success` | State rewritten to “Available” | Any successful request or connection test triggers this |
| Manual restore | Click “Restore” (`Restore`) on the status bar | `clear_api_status` deletes the status record | Clears state only; never edits Key/Base/Model |
| Credential edit | Edit Key/Base/Model | Status identity changes, old state no longer applies | For example a new key is not affected by the old “Unavailable” record |
| App restart | Quit and relaunch the app | All state is cleared | `_API_STATUS` and the status secret are process-random |

## When the configuration itself is wrong: it fails again after restore {#refailure-after-recovery}

The Restore button, a credential edit, or an app restart only clears the **status record**; they do not fix the connection information in `.env`. If the failure is real (invalid key, missing model, exhausted quota, or billing error), the next request fails with the same error, the candidate is marked “Unavailable” again, and the status bar reappears. Cooldown expiry is the same: cooldown is only a temporary skip and does not mean the candidate became healthy; if the server is still rate limiting, the candidate enters “Cooldown” again.

```mermaid
flowchart LR
    R["Click Restore\nclear_api_status"] --> A["Candidate is eligible again"]
    A --> Q["Send the request again"]
    Q -->|"Configuration is really wrong (invalid key / missing model / quota)"| F["Same permanent error"]
    F --> U["Marked unavailable again"]
    U -.->|"Status bar reappears"| R
```

So the debugging order is: first confirm that the key, address, and model are actually correct, then click Restore and run “Test Current Tab”; do not use repeated Restore clicks as a substitute for fixing the configuration.

## Dependencies and conflicts {#dependencies-and-conflicts}

- Cooldown/unavailable state is kept per `feature:provider` group: a translation cooldown does not affect OCR, colorization, or rendering groups, and vice versa.
- Status only affects candidate selection; it never changes the translator implementation (`translator.translator`) and is not used by `translator_chain`. See [Feature Selectors](./feature-selectors.md) and [Translation Chaining](../translator/translation-chain.md) for the boundary.
- Ordinary retries, HQ/quality retries, region retries, and API candidate switching are four different mechanisms; do not conflate them. Ordinary retries are fully documented in [Retries, Rate Limits, and Quality](../translator/retry-rate-limit-and-quality.md).
- Test results share the same `_API_STATUS` as real requests: a failed test marks the candidate cooling down or unavailable, so later real requests also skip it (until restored).
- In the web/server scenario, `_runtime_api_overrides` fixes the candidate list to a single endpoint with `failover`; there is no multi-candidate rotation, but that single endpoint still records cooldown/unavailable state. The desktop app has no such overrides by default.

## Related files and formats {#related-files-and-formats}

| File/format | Actual role on this page | Manual-edit and compatibility note |
| --- | --- | --- |
| `.env` | Stores Key/Base/Model and `*_API_ROTATION_STRATEGY` | Never stores cooldown/unavailable state; contains real keys and must not be committed or shown |
| `manga_translator/api_key_rotation.py` | Status records, cooldown/unavailability checks, recovery, and candidate iteration | All state is process-local and cleared on restart |
| `manga_translator/runtime_api_resolver.py` | Builds candidates from `.env` and generates status identity | The identity includes a key fingerprint, so a key change creates a new candidate |
| `config/config-example.json` | Release default `cli.attempts: 3` | Affects only the ordinary retry count, not the cooldown duration |
| `desktop_qt_ui/ui/main_page/env_management.py` | Status bar, Restore button, batch tests, and the pre-start availability check | UI text follows `en_US.json` / `zh_CN.json` |

## Source evidence {#source-evidence}

| Layer | File | What was checked |
| --- | --- | --- |
| UI status bar and restore | `desktop_qt_ui/ui/main_page/env_management.py` | `_add_api_slot_status_notice`, `_restore_api_slot_status`, `_api_slot_status_style`, `validate_api_candidate_availability` |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`, `zh_CN.json` | Keys and actual bilingual values for cooling down / unavailable / restore |
| Status records | `manga_translator/api_key_rotation.py` | `record_api_failure`, `record_api_success`, `clear_api_status`, `is_endpoint_unavailable`, `is_permanent_api_unavailable_error`, `is_rate_limit_cooldown_error`, `_extract_retry_after_seconds` |
| Candidate iteration | `manga_translator/api_key_rotation.py` | `iter_api_candidates`, `run_with_api_candidates`, backoff, and `APIRotationExhaustedError` |
| Candidate resolution | `manga_translator/runtime_api_resolver.py` | `make_endpoint_status_key`, candidate deduplication, and strategy parsing |
| Final consumers | `manga_translator/translators/openai.py`, `gemini.py`, `ocr/model_api_ocr.py`, `colorization/model_api_colorizer.py`, `rendering/model_api_renderer.py` | `run_with_api_candidates` calls and the `retry_attempts` source |

## Verification {#verification}

| Check | Status | Notes |
| --- | --- | --- |
| BLUEPRINT, PAGE_GUIDELINES, TODO | Complete | Read section 1.3 and 5.6 and followed the page contract |
| UI/i18n actual values | Complete | Three-column tables checked against `en_US.json` / `zh_CN.json` |
| State machine and recovery logic | Complete | Statically checked status records, cooldown/unavailability checks, restore entry, and re-failure path in `api_key_rotation.py` |
| Sanitized runtime verification | Deferred | No real `.env`, user `config.json`, API key/token, username, user image, or private prompt was read |
| VitePress | Deferred | Coordinator should run `npm run docs:build --prefix doc/wiki` plus mirror/source checks before merge |
