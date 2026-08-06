---
title: OCR 与文本区域调试产物
description: 通过详细日志生成的 OCR 裁剪图、文本区域框和 ocrs/ 子目录定位识别与区域问题
pageId: debugging.ocr-and-text-regions
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# OCR 与文本区域调试产物

当 OCR 把文字识别错、漏识别、把多行并成一行，或者文本区域的合并与排序不符合阅读顺序时，本页用于读懂“详细日志”（Verbose Logging）在 `result/` 调试目录中生成的 OCR 输入裁剪图、文本区域框和 `ocrs/` 子目录。这些是条件写入的静态调试中间文件：只由 `verbose=True` 触发，不参与最终结果，也不代表每次翻译都会生成。

本页只覆盖 OCR 输入与文本区域框的可视化产物。检测阈值与 `mask_raw.png`、`bboxes_with_scores.png`、`mask_binary.png`、`hybrid_detection_boxes.png` 等见[检测与重排](./input-detection-and-rearrangement.md)；OCR 引擎、混合 OCR、过滤与合并参数见[OCR、过滤与文本行合并](../desktop/settings/ocr-filter-and-merge.md)；调试目录命名与全局清理见[调试目录命名与总览](./folder-naming-and-overview.md)。

## 功能边界 {#feature-boundary}

- 本页覆盖的产物：`ocrs/<index>.png`（单行 OCR 输入裁剪图）、`bboxes_unfiltered.png` 与 `bboxes_unfiltered_labeled.png`（OCR 前的文本行框）、`bboxes.png`（合并后的最终文本区域框），以及 `ocrs/` 子目录的路径、触发条件、画面含义与排查用途。
- 产物仅在 `verbose=True` 时写入；桌面端的“详细日志”开关写入 `cli.verbose`，CLI 使用 `-v/--verbose`。该开关的完整说明见[CLI、批处理与输出](../desktop/settings/cli-batch-and-output.md)。
- 不覆盖蒙版、修复、排版、替换翻译和 WebSocket 的调试产物（分别在[蒙版、修复与排版](./mask-inpainting-and-rendering.md)等页面）；不把条件产物描述成每次运行必有。

## UI 操作 {#ui-operations}

### 开启详细日志 {#enable-verbose}

1. 打开“设置”（`Settings`），选择“通用”（`General`）分组，打开“详细日志”（`Verbose Logging`）。该开关保存到 `cli.verbose`。
2. 或在使用 CLI 时附加 `-v/--verbose` 参数，例如 `python -m manga_translator local -i <输入路径> -v`。
3. 重新运行需要排查的图片。调试文件写入 `result/<图片调试子目录>/`，`ocrs/` 是其中的子目录。
4. 排查结束并准备分享前，先关闭应用，再删除 `result/` 下不需要的日志文件与调试文件夹。

### UI 调用 key 与实际文案 {#ui-i18n-keys}

下表记录本页操作与触发条件涉及的界面文案。UI 描述中的调试目录名写作“时间戳-图片名-目标语言-翻译器”，实际代码生成的子目录名是 `{毫秒时间戳}-{图片MD5}-{检测尺寸}-{目标语言}-{翻译器}`，以代码为准。

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `label_verbose` | Verbose Logging | 详细日志 |
| `desc_cli_verbose` | Output detailed debug info to logs for troubleshooting.<br><br>When enabled, Qt UI writes these items under result/:<br>- log_timestamp.txt: Qt UI runtime log<br>- timestamp-image-target-translator/: debug intermediate files for a single task<br><br>Cleanup: close Qt UI first, then delete the unneeded log_*.txt files and matching timestamp debug folders under result/. | 输出详细的调试信息到日志，方便排查问题。<br><br>开启后会在 result/ 目录生成：<br>- log_时间戳.txt：Qt UI 运行日志<br>- 时间戳-图片名-目标语言-翻译器/：单次任务的调试中间文件<br><br>清理方法：先关闭 Qt UI，再到 result/ 目录删除不需要的 log_*.txt 和对应的时间戳调试文件夹即可。 |
| `label_ocr` | OCR Model | OCR模型 |
| `label_secondary_ocr` | Secondary OCR | 备用OCR |
| `label_use_hybrid_ocr` | Enable Hybrid OCR | 启用混合OCR |
| `label_prob` | Text Region Min Probability | 文本区域最低概率 (prob) |
| `label_filter_text_enabled` | Enable Filter List | 启用过滤列表 |
| `label_min_text_length` | Minimum Text Length | 最小文本长度 |
| `label_skip_no_text` | Skip Images Without Text | 跳过无文本图像 |

## 调试产物与触发条件 {#debug-artifacts-and-triggers}

调试文件位于 `result/<图片调试子目录>/`（打包版为可执行文件旁的 `result/`）。`_run_ocr()` 在 verbose 时创建 `ocrs/`，其余图片经 `_result_path()` 写入同一子目录。以下产物全部是条件产物，不是每次运行必有。

### 产物清单 {#artifact-table}

| 产物 | 生成阶段 | 触发条件 | 画面/内容含义 | 排查用途 |
| --- | --- | --- | --- | --- |
| `ocrs/<index>.png` | OCR 阶段 | `verbose=True`，且该文本行通过 OCR 前置过滤（气泡过滤等） | 透视矫正后的单个文本行裁剪图，即送入 OCR 网络的输入；竖排文本旋转为横排；48px 与 PaddleOCR 路径把长边限制到 200px | OCR 识别错误/漏识别时，确认送入模型的图是否裁对、是否被旋转或压缩 |
| `bboxes_unfiltered.png` | 检测后、OCR 前 | `verbose=True`，且检测后仍有文本行 | 原图上绘制未过滤文本行框，跳过标签为 `other` 的框 | 确认哪些框进入 OCR，检测框是否完整覆盖文字 |
| `bboxes_unfiltered_labeled.png` | 检测后、OCR 前 | `verbose=True`，开启模型辅助合并（`ocr.merge_special_require_full_wrap`），且收到非空文本行 | 带序号与标签（`balloon`、`qipao`、`other` 等）的彩色文本行框，优先使用检测原始全集 | 排查标签分流与模型辅助合并的输入 |
| `bboxes.png` | 文本行合并后 | `verbose=True`，且存在 `ctx.text_regions` | 最终文本区域（`TextBlock`）可视化：panel 框（关闭简单排序时）、区域外框、行折线、角度/坐标标注与区域序号 | 排查文本行合并、阅读顺序与 panel 划分 |

### 无文本早退 {#no-text-early-exit}

- 检测后没有文本行：报告 `skip-no-regions`，`ctx.text_regions` 置为空列表，不写 `bboxes_unfiltered*.png` 与 `bboxes.png`，直接进入还原尺寸阶段。
- OCR 后没有文本行（全部为空文本、低置信或命中过滤列表）：报告 `skip-no-text`，`ctx.text_regions` 置为空列表，不写 `bboxes.png`。
- 因此“调试目录里没有 `bboxes.png`”不一定是写入失败，也可能是无文本早退；请先看日志中的 `skip-no-regions` / `skip-no-text`。

## 运行机理 {#runtime}

### 从文本行到文本区域的调试链路 {#data-flow}

```mermaid
flowchart LR
    T["检测文本行 ctx.textlines"] --> U["bboxes_unfiltered.png\n未过滤文本行框（跳过 other）"]
    T --> L["bboxes_unfiltered_labeled.png\n带标签/序号框（开启模型辅助合并）"]
    T -->|"前置过滤：气泡过滤等"| O["ocrs/ 子目录：单行透视矫正裁剪图"]
    O --> R["OCR 识别"]
    R -->|"空文本或低置信"| H["混合 OCR：备用引擎重识别\n写入同一 ocrs/ 目录"]
    R --> F["过滤：空文本 / ocr.prob / 过滤列表"]
    H --> F
    F -->|"无剩余文本行"| S["skip-no-text 早退\ntext_regions=[]"]
    F --> M["文本行合并 → text_regions"]
    M --> B["bboxes.png\n最终文本区域可视化"]
    T -.->|"无文本行"| N["skip-no-regions 早退\ntext_regions=[]"]
```

### `ocrs/` 子目录的写入与编号 {#ocrs-writing-and-indexing}

- `_run_ocr()` 在 verbose 时把环境变量 `MANGA_OCR_RESULT_DIR` 设置为 `<result>/<图片调试子目录>/ocrs/`（配置了 `result_sub_folder` 时在其下多一级），OCR 实现写入裁剪图时读取该变量。
- 写文件的实现：`model_32px.py`、`model_48px.py`、`model_48px_ctc.py`、`model_manga_ocr.py`、`model_paddleocr.py` 共五个。AI OCR（OpenAI/Gemini，`model_api_ocr.py`）与 PaddleOCR-VL 不写逐区域裁剪图。
- 编号按文本行处理顺序递增；PaddleOCR 使用原始文本行序号（`{i}.png`），32px 使用运行序号（`{ix}.png`），48px/48px-CTC/Manga OCR 使用批内全局编号（`{ix-N+i}.png`）。被前置过滤的行不写文件，因此编号不连续。
- 竖排文本写入前旋转 90°；48px 与 PaddleOCR 路径把长边限制到 200px 并启用高压缩 PNG。
- 当 `MANGA_OCR_RESULT_DIR` 未设置（例如独立调用 OCR 实现）时，回退到 `result/ocrs/`（不带图片级子目录）。

### 文本区域框的画面含义 {#text-region-box-meaning}

`bboxes.png` 由 `visualize_textblocks()` 绘制：

- panel 框为紫色矩形并带 panel 序号，仅在不使用简单排序（`force_simple_sort=False`）时执行 panel 检测；
- 每个文本区域画绿色外框（包围盒）、每行的青色折线与行号，以及黄色最小外接矩形；
- 区域中心标注角度 `a:`、左上角坐标 `x:`/`y:`，区域左上角标注区域序号。

### 过滤与混合 OCR 对产物的影响 {#filter-and-hybrid-effects}

- 过滤阶段（`_filter_ocr_textlines()`）依次丢弃空文本、低于 `ocr.prob` 的低置信行和命中过滤列表（`filter_text_enabled`）的行；被丢弃的行不会出现在 `text_regions` 与 `bboxes.png` 中。
- 混合 OCR（`ocr.use_hybrid_ocr`）把主 OCR 空文本或低置信的行交给 `ocr.secondary_ocr` 重新识别，两次运行都会写 `ocrs/`，编号可能冲突并被覆盖，因此同一编号最终保留哪次结果需要以实际运行为准。
- 无文本早退发生在过滤之后，此时 `bboxes.png` 不生成；这不影响 `input.png`、`final.png` 等其他阶段产物。

## 依赖与冲突 {#dependencies}

- `ocrs/` 与所有框图都依赖 `cli.verbose=True`；关闭 verbose 后不产生任何本页产物。
- `bboxes_unfiltered_labeled.png` 依赖模型辅助合并开关（`ocr.merge_special_require_full_wrap`），关闭时不写该文件。
- `bboxes.png` 是否画 panel 框依赖 `force_simple_sort`；开启简单排序时只画区域框。
- 混合 OCR 两次识别共用 `ocrs/` 目录，编号不保证与最终文本区域一一对应；不要把 `ocrs/<index>.png` 直接当作最终区域序号的证据。
- 产物来自用户原图与 OCR 文本，可能包含用户图像、原文与坐标；分享前必须逐文件检查并脱敏。

## 关联文件与格式 {#files-and-formats}

| 文件/目录 | 本页作用 | 注意 |
| --- | --- | --- |
| `result/<图片调试子目录>/ocrs/<index>.png` | 单行 OCR 输入裁剪图 | 条件产物；可能含用户图像与文本，分享前脱敏 |
| `result/<图片调试子目录>/bboxes_unfiltered.png` | 未过滤文本行框 | 跳过 `other` 标签框 |
| `result/<图片调试子目录>/bboxes_unfiltered_labeled.png` | 带标签/序号的文本行框 | 需开启模型辅助合并 |
| `result/<图片调试子目录>/bboxes.png` | 最终文本区域可视化 | 合并/排序/panel 排查 |
| `result/ocrs/` | 环境变量未设置时的回退目录 | 主要供独立调用 OCR 实现时出现 |
| `result/log_<时间戳>.txt` | 桌面端全局运行日志 | 与单图调试目录不同；含本机路径，分享前清理 |

## Mermaid 数据流限制 {#diagram-limits}

上图画的是源码中的条件写入链，不是“每次运行都会生成的完整文件树”。无文本早退、混合 OCR 编号覆盖、关闭模型辅助合并、启用简单排序等都会改变产物组合；`mask_raw.png`、`bboxes_with_scores.png`、`mask_binary.png`、`hybrid_detection_boxes.png` 属于检测阶段，见[检测与重排](./input-detection-and-rearrangement.md)。本页没有伪造运行截图，也没有收录用户图片或私有任务产物。

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| UI/i18n | `desktop_qt_ui/locales/en_US.json`、`zh_CN.json` | `label_verbose`、`desc_cli_verbose` 及 OCR 相关标签实际值 |
| verbose 开关 | `manga_translator/args.py`、`manga_translator/config.py` | CLI `-v/--verbose` 与 `cli.verbose` |
| 目录与调度 | `manga_translator/manga_translator.py` | `_set_image_context`、`_result_path`、`_run_ocr`、`_run_textline_merge`、过滤与早退、`bboxes*.png` 写入 |
| OCR 裁剪图 | `manga_translator/ocr/model_32px.py`、`model_48px.py`、`model_48px_ctc.py`、`model_manga_ocr.py`、`model_paddleocr.py` | `ocrs/<index>.png` 写入、旋转与 200px 限制 |
| 区域可视化 | `manga_translator/utils/textblock.py`、`manga_translator/utils/generic.py` | `visualize_textblocks` 画面元素、`get_transformed_region` 透视矫正 |
| 调试产物追踪 | `doc/wiki/research/phase0-debug-artifact-path-trace.md` | 路径契约与条件产物清单 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| 契约文档 | 完成 | 已按 PAGE_GUIDELINES、BLUEPRINT、TODO 1.3/5.15/6.3 编写 |
| UI/i18n 文案 | 完成 | 静态核对 `en_US.json`/`zh_CN.json` 与 `data/i18n.generated.json` 实际值 |
| OCR 与区域产物链 | 完成 | 静态核对 `_run_ocr`、五个 OCR 实现、`bboxes*.png` 与合并链路 |
| 路由镜像检查 | 完成 | `node scripts/verify-route-mirror.mjs .`：PASS（120 zh / 120 en） |
| 源码依据检查 | 完成 | `node scripts/verify-source-evidence.mjs .`：PASS |
| 脱敏运行验证 | 待后续 | 未读取真实 `.env`、用户图片、私有路径或任务产物 |
| VitePress 构建 | 待后续 | 由协调代理统一执行 `npm run docs:build --prefix doc/wiki` |