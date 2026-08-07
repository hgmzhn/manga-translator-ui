---
title: 设置页与配置生命周期
description: 说明桌面端七个设置页签、参数编辑动作、配置写入优先级与运行时边界
pageId: desktop.settings.index
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 设置页与配置生命周期

设置页用于调整桌面端翻译流程的配置，并把修改后的值交给运行时配置模型。它负责设置页签、参数控件、导入/导出和自动保存；具体参数的算法含义分别见[通用与应用](./general-and-app.md)、[CLI、批量与输出](./cli-batch-and-output.md)、[检测](./detection.md)、[OCR、过滤与合并](./ocr-filter-and-merge.md)、[翻译](./translation.md)、[蒙版与修复](./mask-and-inpainting.md)、[排版与渲染](./typesetting-and-rendering.md)、[超分与上色](./upscale-and-colorization.md)和[模式专用参数](./mode-specific.md)。设置页不负责 API 凭据槽轮换、提示词列表、编辑器项目数据或九种工作流的具体处理步骤。

## 这组设置控制什么 {#feature-boundary}

- 设置页从 `settings_tab_layout.json` 读取七个 UI 分组：General、OCR、Detection、Translation、Inpainting、Typesetting、Mode Specific；布局文件中的 `Advanced` 和其他分隔线只是组内标题。
- 当前布局列出 110 个条目，其中 109 个是可见参数，1 个是未被当前动态设置渲染的条目。内部状态、已由工作流选择器代替的标志和废弃字段不会重复显示。
- 配置值分为三层：Qt 的 `AppSettings` 控件模型、核心 `Config` 处理模型和发行模板 `config/config-example.json`。三层不应被文档合并成一个默认值。
- 这里仅解释设置页如何改变配置及其保存边界；检测、OCR、翻译、修复、排版、超分和上色的阶段消费者留在对应专题页。

## 在桌面端修改 {#ui-operations}

启动桌面端后打开设置页面。页首显示“参数设置”和“调整翻译流程的各项参数。修改后将自动保存。”，右侧提供“导出配置”和“导入配置”。左侧为分段页签，中央为可滚动参数行，右侧为“参数说明”面板；点击参数行或其控件会显示配置键和说明。

### 页签与参数归属

| 界面页签 | English 实际值 | 简体中文实际值 | 页面中显示的主要参数 |
| --- | --- | --- | --- |
| `General` | General | 通用 | 语言、主题、日志/错误、GPU/ONNX、格式、覆盖、重试、批次、输出和模型卸载 |
| `OCR` | OCR | 识别 | 主/次 OCR、混合 OCR、AI OCR、过滤、气泡约束和合并阈值 |
| `Detection` | Detection | 检测 | 检测器、YOLO、SFX、检测尺寸和检测阈值 |
| `Translation` | Translation | 翻译 | 翻译器、目标/保留语言、流式、术语、RPM、上下文和译后文本转换 |
| `Inpainting` | Inpainting | 修复 | 修复器、蒙版膨胀、气泡交集、纯色气泡、逐块处理、尺寸和精度 |
| `Typesetting` | Typesetting | 排版 | 渲染器、字体、断句、方向、颜色、间距、布局和 AI 渲染并发 |
| `Mode Specific` | Mode Specific | 模式相关 | 替换翻译对齐、超分倍率/瓦片、上色模型/尺寸/降噪 |
| `Advanced` | Advanced | 高级 | OCR、Detection、Inpainting 页签中的高级分隔线，不是独立页签 |

操作步骤：

1. 选择一个页签；动态布局按 `settings_tab_layout.json` 的顺序重建参数行。
2. 修改开关、输入框或下拉框。下拉框显示值通过 `AppLogic.get_display_mapping()` 映射回存储值；字体和提示词等运行时列表不应按固定枚举理解。
3. 对可选数值清空输入框表示写入 `null`，从而回到消费者的默认/自动语义；无效数值输入会回退为 `null`，并由配置模型继续校验。
4. 点击参数行查看右侧说明。固定 AI OCR、AI renderer、AI colorizer 提示词项是“文件编辑动作/资源路径”，点击“编辑”打开对应编辑器，不是把提示词正文存进普通参数字段。
5. 修改 API 参数开关时使用“编辑”打开 `config/custom_api_params.json`；点击过滤开关旁的编辑动作可打开过滤列表编辑器。字体行提供“打开目录”。
6. 点击“导出配置”选择外部 JSON 文件；点击“导入配置”载入 JSON，并按逐键深合并和 Pydantic 校验处理无效值。导入后整页可能重建，说明面板和 API/提示词相关控件也会刷新。

`app.ui_language` 或应用语言切换后，页签、标签、说明和下拉显示值重新从 locale 加载；存储值不因语言切换而改变。设置页没有单独的“应用”按钮，普通修改先立即更新内存，再由配置服务合并写盘。

## 参数如何生效 {#runtime-behavior}

```mermaid
flowchart LR
    A["UI 控件或导入配置"] --> B["AppSettings / ConfigService"]
    B --> C["内存配置"]
    B --> D["config.json 原子写入"]
    C --> E["核心 Config"]
    E --> F["工作流和阶段消费者"]
    G["CLI 显式参数"] --> E
    H["发行配置默认"] --> B
    I["代码兜底"] --> E
```

`ConfigService` 初始化 `AppSettings()`，先读取发行/默认 JSON，再以用户 `config.json` 覆盖；优先级是用户配置 > `config-example.json` > Qt 模型默认。核心 `Config()` 的字段和默认值仍由核心代码定义，CLI 显式参数可在进入核心配置时覆盖对应值；Web 运行时覆盖属于另一个入口。

参数修改由控制器更新 `AppSettings`，Pydantic 模型在 `update_config()` 或导入的逐键合并过程中校验。内存和 `os.environ` 的 API 值立即更新；普通 JSON 和 `.env` 写入使用 250 ms 防抖、单线程写入器、临时文件加 `os.replace` 原子替换。显式导出会等待写入完成；退出时 flush 待写快照。

- 选择翻译、OCR、上色或渲染实现后，API 管理区域刷新对应凭据组；这是提供商选择，不是候选槽轮换。
- 选择 `upscale.upscaler` 会动态重填倍率：普通模型写整数 2/3/4，Real-CUGAN 还写 `realcugan_model`，MangaJaNai 写 `x2`、`x4` 或 `DAT2 x4`；“不使用”写 `null`。
- `cli.batch_size` 是阶段内批量大小，`cli.batch_concurrent` 是图片级流水线并发，二者不是同一开关；特殊工作流可能强制改写 CLI 标志。
- 固定提示词编辑器写入对应 YAML/兼容格式文件；三种 AI 提示词分别消费，不共享一个提示词字段。

## 搭配使用时的注意事项 {#dependencies-and-conflicts}

- 使用 OpenAI/Gemini 翻译、AI OCR、AI 上色或 AI renderer 时，需要对应功能的环境变量和可用 API 地址；混合 OCR 选 AI 次 OCR 时还需要次 OCR 凭据。真实值不属于本文。
- GPU、ONNX GPU、Torch 修复精度和模型选择受硬件、安装依赖和显存影响；`disable_onnx_gpu` 不等同于 `use_gpu=false`。
- 混合 OCR、AI 并发、RPM、重试和批量并发会增加识别/网络压力和成本。
- `upscale_ratio` 依赖 `upscaler`；模板匹配对齐和粘贴蒙版膨胀只在替换翻译模式有意义。
- 导入未知键不会成为新控件；无效值回退默认并记录警告。不要在应用仍有待写入操作时手改同一份 JSON 或 `.env`。
