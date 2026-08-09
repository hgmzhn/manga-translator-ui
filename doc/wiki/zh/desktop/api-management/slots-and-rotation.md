---
title: API 通道与轮询策略
description: 为同一个 API 提供商配置备用凭据，并控制请求失败后选择哪个请求端点
pageId: desktop.api-management.slots-and-rotation
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# API 通道与轮询策略

当一组 API 密钥容易触发限流，或者你同时使用官方地址和兼容服务时，可以为同一个提供商添加多个 API 通道。每个通道保存一组密钥、API 地址和模型；翻译器仍然是原来的翻译器，变化的只是下一次请求使用哪个 API 候选。

这里介绍候选通道的添加、删除、拖拽排序、编号徽标和两种轮询策略，以及它们如何组成运行时的候选列表。OpenAI 与 Gemini 翻译器之间的切换见[翻译器选择](../translator/selection-and-languages.md)，`translator_chain` 见[翻译器串联](../translator/translation-chain.md)。页签布局见[API 管理页签与提供商字段](./provider-tabs.md)，Key/Base/Model 字段与 `.env` 键映射见[凭据、地址与模型](./credentials-addresses-models.md)，冷却/不可用/恢复的完整说明见[故障、冷却与恢复](./failures-cooldown-and-recovery.md)，连接测试见[连接测试与模型列表](./connection-tests-and-model-list.md)。

## 在界面中配置备用 API {#configure-api-slots}

打开“API 管理”，选择实际使用 API 的功能页签，例如“翻译”。页面上方的功能选择器决定当前使用 OpenAI、Gemini 还是其他实现；下方的 API 通道只配置这个实现所使用的连接信息。

以 OpenAI 翻译为例，每张通道卡片显示以下三个字段。切换到 Gemini、OCR、上色或渲染时，字段会换成对应功能和提供商的 i18n 文案。

通道标题最左侧是拖拽手柄，随后是两位编号徽标（例如 `01`）和“API 通道”标题。编号只出现在徽标中，不会拼进标题文字。

1. 在编号 `01` 的“API 通道”卡片中填写“OpenAI API 密钥”“OpenAI 模型”和“OpenAI API 地址”。
2. 点击“+ 添加 API 通道”，创建第二组候选。新通道的三个 `.env` 键会先写入空值再刷新界面。
3. 为编号 `02` 的“API 通道”填写完整的连接信息。留空的通道不会成为有效候选（本地 OpenAI 兼容端点除外：空密钥会被规范化为 `ollama` 占位值）。
4. 按住卡片标题最左侧的移动图标，把卡片拖到目标卡片上方或下方；插入提示线显示新位置。松开后整组 Key/Model/Base 会一起改写到新的连续编号，按顺序故障切换会立即使用新顺序。
5. 在“轮询策略：”下拉框中选择“按顺序故障切换”或“轮询”。
6. 使用“测试当前页”确认至少有一个候选可以连接。

“测试当前页”只测试当前功能页签内所有已配置的通道（并发数为 3），不会测试其他页签；结果弹窗显示“共 N 个，可用 X 个，不可用 Y 个”，并把每个通道标记为可用、冷却中或不可用，状态条随即刷新在对应卡片上。如果当前功能没有可测试的通道，会提示“没有可测试的 API 通道”。

拖拽排序只重排当前提供商的完整通道，不会拆散 Key/Model/Base，也不会修改轮询策略或其他功能页签。删除中间通道时，后面的通道会向前补位，因此编号始终连续；被删除通道的 `.env` 键会被清理。界面上限为 10 个通道（`API_ROTATION_UI_MAX_SLOTS = min(10, 30)`），达到上限后“+ 添加 API 通道”按钮隐藏。

## 两种轮换策略有什么区别 {#rotation-strategies}

如果只有一个有效通道，两种策略的结果基本相同。轮询不会把一次翻译拆给多个模型，也不会在请求过程中更改翻译器。

## 候选解析与轮换调用图 {#candidate-resolution}

下面的调用图把本页与翻译器选择、功能选择器和 `translator_chain` 的边界放在一起：API 管理页的 Key/Base/Model 槽只参与“解析 feature + provider”和候选列表的构建，轮换发生在已选定的提供商内部，最终才发起 HTTP 请求。

```mermaid
flowchart LR
    A["翻译器下拉框\n设置页或 API 管理页"] --> B["translator.translator"]
    B --> C["选择翻译实现"]
    C --> D["解析 feature + provider"]
    E["API 管理\nKey / Base / Model 槽"] --> D
    D --> F["Runtime API candidates"]
    F --> G["failover / round_robin"]
    G --> H["实际 HTTP 请求"]
    I["translator_chain"] --> C
    I -. "翻译结果串联，不参与端点轮换" .-> C
```

运行时 `resolve_runtime_api_config()` 按以下顺序构造候选：

- 先读取当前 feature/provider 的策略键（例如 `OPENAI_API_ROTATION_STRATEGY`）和 `_2`、`_3` 等编号 `.env` 键，得到通道数量与策略。
- 对每个编号 `1..N` 读取 Key、Base、Model；三个字段齐全（Key 对本地 OpenAI 兼容端点可为空）才成为一个候选端点，完全重复的 `(key, base_url, model)` 会被去重。
- 只有被当前功能选择器激活的提供商分组才会出现在界面和候选池中；`translator.translator` 的取值决定最终请求由哪个实现发出。
- Web 多用户场景下，`user_api_key`/`user_api_base`/`user_api_model` 作为配置覆盖存在时，解析器只构造单个候选端点并把策略固定为 `failover`，编号通道轮换不参与。

## 一次请求怎样选择候选 {#candidate-selection}

```mermaid
flowchart TD
    Start["翻译器准备发送一次请求"] --> Order["按策略生成候选顺序\nfailover 保持 1..N；round_robin 轮换起始下标"]
    Order --> Pick{"还有未尝试的可用候选吗？"}
    Pick -->|没有| Exhausted["停止请求并报告所有候选均不可用"]
    Pick -->|有| Request["使用当前通道的密钥、API 地址和模型发起请求"]
    Request --> Result{"请求结果"}
    Result -->|成功| Success["返回翻译结果，并把该候选标记为可用"]
    Result -->|可重试错误| Retry["按 attempts 在当前候选上重试"]
    Retry --> Request
    Result -->|限流或 Retry-After| Cooldown["把当前候选标记为冷却中"]
    Result -->|密钥、模型或配额等永久错误| Unavailable["把当前候选标记为不可用"]
    Cooldown --> Next["记录失败并尝试下一个候选"]
    Unavailable --> Next
    Next --> Pick
```

系统先在当前候选内部执行普通请求重试；只有当前候选无法继续使用时，才会根据轮询策略选择下一个通道。因此“重试次数”（`cli.attempts`，见[重试、限流与质量](../translator/retry-rate-limit-and-quality.md)）和“API 通道数量”控制的是两个不同层级。

## 冷却、不可用和恢复 {#status-and-recovery}

| 界面状态 | 常见原因 | 系统行为 | 用户可以做什么 |
| --- | --- | --- | --- |
| 冷却中 | 429、速率限制、服务返回 `Retry-After` | 暂时跳过该候选，冷却结束后允许再次使用 | 等待冷却结束，或检查请求频率 |
| 不可用 | Key 无效、模型不存在、配额或计费错误 | 后续请求跳过该候选 | 修正配置后点击恢复按钮，再执行连接测试 |
| 可用 | 连接成功，或失败状态已被清除 | 可以参与后续候选选择 | 无需操作 |

状态条和恢复按钮显示在通道卡片标题下方；“恢复 API 通道”只清除当前进程中的失败状态，不会替你修改 Key、地址或模型。配置本身有误时，恢复后仍会再次失败。完整状态机见[故障、冷却与恢复](./failures-cooldown-and-recovery.md)。

## 与翻译器切换的关系 {#translator-boundary}

- 把 OpenAI 翻译切换为 Gemini 翻译，是更换翻译实现和提供商。
- 在 `OPENAI_API_KEY`、`OPENAI_API_KEY_2` 之间切换，是 OpenAI 提供商内部的候选轮询。
- `translator_chain` 会把一个翻译器的结果交给下一个翻译器，和 API 候选通道没有关系。

API 管理页顶部的翻译器选择器绑定 `translator.translator`，因此在那里切换选项会真正改变翻译器；API 通道和轮询策略本身不会改变该值。详细边界见[功能选择器](./feature-selectors.md)与[翻译器串联](../translator/translation-chain.md)。
