---
title: API、鉴权、限流与超时排障
description: 定位并处理 API 请求错误、鉴权失败、限流与超时问题，区分桌面端与 Web 服务的排障入口
pageId: troubleshooting.api-auth-rate-limit-and-timeout
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# API、鉴权、限流与超时排障

当翻译、OCR、上色或渲染请求报 API 错误、鉴权失败、限流或超时，先分清问题发生在“本机到外部 API”还是“浏览器到 Web 服务”，再按症状定位配置、网络或候选状态。下面按四类症状给出诊断顺序和修复入口；候选通道、冷却状态机、重试/RPM 参数、连接测试和 Web 部署安全的细节分别在对应页面。

## 先确认问题 {#feature-boundary}

- 内容包括四类症状：API 错误（4xx/5xx）、鉴权失败（密钥无效、401/403）、限流（429/冷却/RPM）、超时与网络（timeout/connection/DNS）。
- 候选通道的增删、编号与 `failover`/`round_robin` 策略见[API 通道与轮询策略](../desktop/api-management/slots-and-rotation.md)；冷却、不可用、恢复与再失败状态机见[故障、冷却与恢复](../desktop/api-management/failures-cooldown-and-recovery.md)；连接测试与获取模型见[连接测试与模型列表](../desktop/api-management/connection-tests-and-model-list.md)；Key/Base/Model 字段与 `.env` 键映射见[凭据、地址与模型](../desktop/api-management/credentials-addresses-models.md)。
- `cli.attempts`、`translator.max_requests_per_minute` 与译后质量检查的完整参数说明见[重试、限流与质量](../desktop/translator/retry-rate-limit-and-quality.md)，这里不重复参数模板。
- Web 场景：登录/注册/会话限流、并发与配额属于[Web 部署、安全与排错](../web/deployment-security-and-troubleshooting.md)与[登录、语言与会话](../web/login-language-and-session.md)；完整状态码契约属于开发者文档[鉴权与错误](../developer/http-api/authentication-and-errors.md)与[翻译端点](../developer/http-api/translation-endpoints.md)。
- “限流”在桌面端与 Web 服务是两个不同层级：桌面端是外部 API 的 RPM 与候选冷却；Web 服务是登录/注册/并发/配额的服务端限流。不要混用两套概念排查。

## 症状速查 {#symptom-quick-reference}

下表按“错误特征 -> 源码归类 -> 系统行为 -> 排查入口”组织。归类依据 `manga_translator/api_key_rotation.py` 的永久错误/冷却判定；`cli.attempts` 与候选数量控制的是两个不同层级，见[重试、限流与质量](../desktop/translator/retry-rate-limit-and-quality.md)。

| 错误特征 | 归类（源码判定） | 系统行为 | 首选排查入口 |
| --- | --- | --- | --- |
| `invalid api key`、`api key not valid`、`api key expired`、`api key revoked`、`invalid authentication`、`invalid credentials`、`permission denied`、`access denied` | 永久错误（候选不可用） | 候选被标记为不可用，后续请求跳过 | [凭据、地址与模型](../desktop/api-management/credentials-addresses-models.md) 检查 Key；[连接测试](../desktop/api-management/connection-tests-and-model-list.md) 验证 |
| `401`、`403`、`unauthorized`、`forbidden`（消息不含上述标记） | 其他错误（仅记录失败） | 按 attempts 在同一候选重试，然后转下一候选 | 检查 Key 权限、账户状态与地区限制 |
| `404`、`not found`、`model not found`、`model does not exist` | 永久错误（候选不可用） | 候选被标记为不可用 | 检查 API 地址、模型名与翻译器类型是否匹配 |
| `402`、`insufficient_quota`、`billing`、`payment required` | 永久错误（候选不可用） | 候选被标记为不可用 | 检查账户余额、配额与计费状态 |
| `400` 且消息含 `unsupported model`、`invalid model`、`unknown variant image_url`、`did not contain an image` 等 | 永久错误（候选不可用） | 候选被标记为不可用 | 换用支持多模态输出的模型 |
| `429`、`rate limit`、`too many requests`、`Retry-After` | 冷却 | 同一候选按 attempts 重试后进入冷却（默认 60 秒，`Retry-After` 上限 600 秒） | [重试、限流与质量](../desktop/translator/retry-rate-limit-and-quality.md) 调整 RPM；[故障、冷却与恢复](../desktop/api-management/failures-cooldown-and-recovery.md) 查看冷却 |
| `408/409/425/500/502/503/504/520-524`、`bad gateway`、`service unavailable` | 其他错误（可重试） | 按 attempts 在同一候选重试，耗尽后转下一候选 | 增大[重试次数](../desktop/translator/retry-rate-limit-and-quality.md)；等待服务恢复 |
| `timeout`、`timed out`、`connection`、`network`、DNS/`getaddrinfo` | 其他错误（可重试） | 按 attempts 重试；真实请求客户端超时为 600 秒、流式 300 秒 | 检查网络、代理 TUN 与 API 地址 |
| `No available API candidates`、`exhausting API candidates` | 候选耗尽 | 抛出候选耗尽错误，阻止开始或中止请求 | [恢复候选](../desktop/api-management/failures-cooldown-and-recovery.md) 或「测试当前页」 |

## 鉴权失败 {#authentication-failures}

- 桌面端 Key 保存在 `.env`，由 API 管理页的“API 密钥”字段编辑。先确认当前功能页签与翻译器/提供商匹配：OpenAI 兼容端点应选择 OpenAI 系翻译器，Gemini 官方端点应选择 Gemini 系；再把 Key 粘贴进对应页签，避免多余空格、换行或复制错行。
- 用“测试”或“测试当前页”验证。失败弹窗标题为“API连接测试失败”，正文按网络错误、服务端异常或通用配置给出分类建议。
- 源码把密钥无效按消息识别为永久错误：`invalid api key`、`api key not valid`、`api key expired`、`invalid authentication`、`invalid credentials`、`permission denied`、`access denied` 等命中后，候选进入“不可用”，后续请求跳过该候选，直到修改凭据或点击“恢复”。
- Web 服务有两类“鉴权失败”：浏览器会话 401（令牌缺失/无效/过期，前端清本地令牌并跳转登录页）与服务器端保存的 API Key 无效（翻译请求 401/403）。前者见[登录、语言与会话](../web/login-language-and-session.md)，后者先检查管理界面的 API Key 策略与 `.env` 持久化，见[Web 部署、安全与排错](../web/deployment-security-and-troubleshooting.md)。
- 这里不展示真实 Key；错误弹窗和日志中出现的明文 Key 片段不要复制到公开报告。

## 限流与冷却 {#rate-limit-and-cooldown}

- 外部 API 限流：`translator.max_requests_per_minute`（“每分钟最大请求数”）按模型维护全局请求时间戳，`0` 表示不限制；只影响走 OpenAI/Gemini 系列的真实请求，不影响本地翻译器。
- 收到 `429` 或消息含 `rate limit`/`too many requests` 时，候选进入“冷却中”，默认冷却 60 秒；若响应带 `Retry-After` 头，按该值冷却但上限 600 秒。冷却到期会自动重新参与候选选择，但不保证服务端已经恢复。
- 冷却/不可用状态只保存在进程内存（`_API_STATUS`），不写入 `.env` 或 `config.json`，重启即清空。“恢复”只清除状态记录，不修复 Key、地址或模型。
- Web 服务的服务端限流与配额口径见下表，完整说明在[Web 部署、安全与排错](../web/deployment-security-and-troubleshooting.md)。

| 限流场景 | 口径（源码） | 超限返回 |
| --- | --- | --- |
| 外部 API RPM | `translator.max_requests_per_minute`，`0` 不限制 | 客户端自行限速，不产生 429 |
| 外部 API 429 / `Retry-After` | 账户级 RPM/TPM 或渠道限流 | 候选进入冷却（默认 60 秒，上限 600 秒） |
| Web 登录 `/auth/login` | 每 IP 10 分钟 15 次；每用户名 10 分钟 8 次 | `429` + `Retry-After` |
| Web 注册 `/auth/register` | 每 IP 10 分钟 5 次 | `429` + `Retry-After` |
| 旧密码门 `/user/login` | 每 IP 10 分钟 10 次 | `429` + `Retry-After` |
| 并发任务 / 每日配额 | 按用户或用户组生效的并发上限与每日配额 | `429` |

## 超时与网络 {#timeout-and-network}

- 真实翻译请求的客户端超时是硬编码：OpenAI/Gemini 普通请求 `timeout=600` 秒、流式 `stream_timeout=300` 秒；连接测试与取模型使用 30 秒（文本/OCR）或 60 秒（图像类）；Sakura 本地服务等待超时为 999 秒并单独重试 3 次。当前没有界面开关可以修改这些值。
- 超时/连接类错误属于可重试错误：`timeout`、`timed out`、`connection`、`network`、`reset by peer`、`temporary failure` 等命中后按 `cli.attempts` 重试；同一候选的重试间隔为 1 秒、2 秒、3 秒封顶。
- 先区分“本机连不上外部 API”与“Web 服务自身超时”：前者检查网络、代理 TUN、DNS 与 API 地址；后者与 Uvicorn `timeout_keep_alive=1800`（连接保持 30 分钟）、会话 60 分钟不活动过期、下载票据默认 5 分钟 TTL 有关，详见[Web 部署、安全与排错](../web/deployment-security-and-troubleshooting.md)与[Web 服务器端口与部署](../developer/web-server-ports-and-deployment.md)。
- `cli.attempts=-1`（无限重试）叠加持续超时或 5xx 可能长时间不退出；中断批量任务后检查失败列表与日志，而不是反复重启。

## 一次失败请求的处理顺序 {#failure-handling-order}

```mermaid
flowchart TD
    Start["翻译器准备发送一次请求"] --> Resolve["解析候选列表\nfailover 保持 1..N；round_robin 轮换起始下标"]
    Resolve --> Pick{"还有可用候选吗？"}
    Pick -->|没有| Exhaust["APIRotationExhaustedError\n阻止开始或中止请求"]
    Pick -->|有| Attempt["在当前候选上发起请求"]
    Attempt --> Result{"请求结果"}
    Result -->|成功| Success["返回结果并把候选标记为可用"]
    Result -->|永久错误| Unavailable["标记不可用\nKey 无效 / 模型不存在 / 配额计费 / 多模态不匹配"]
    Result -->|429 或 Retry-After| Cooldown["标记冷却中\n默认 60s，上限 600s"]
    Result -->|其他错误| Failed["仅记录失败\n网络 / 5xx / 超时"]
    Unavailable --> Next["按策略尝试下一候选"]
    Cooldown --> Next
    Failed --> Next
    Next --> Pick
```

上图是候选级处理顺序：先在同一候选上按 `cli.attempts` 重试，只有永久错误和限流才会改变候选状态，然后才按策略尝试下一候选。“重试次数”与“API 通道数量”是两个层级；`attempts=-1`、单候选、无失败等场景会走对应旁路，文档。
