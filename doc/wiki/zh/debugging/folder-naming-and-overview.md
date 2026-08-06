---
title: 调试目录命名与总览
description: 理解 verbose 调试目录的命名规则、目录结构与各类调试产物，并区分每次运行必有的产物与条件产物
pageId: debugging.folder-naming-and-overview
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

# 调试目录命名与总览

当你开启“详细日志”（`Verbose Logging`）后，应用会在 `result/` 目录下为每张输入图生成一个调试子目录，并写入检测、OCR、蒙版、修复和排版等中间文件。本页说明这个子目录的命名规则、`result/` 的目录结构，以及各类调试产物的生成阶段和触发条件，帮助你根据目录名定位某一次运行，并判断哪些文件每次都会出现、哪些只在特定配置或工作流下出现。

本页不深入单个产物的排查方法：检测与长图重排见[输入检测与长图重排](./input-detection-and-rearrangement.md)，OCR 与文本区域见[OCR 与文本区域](./ocr-and-text-regions.md)，蒙版、修复与排版见[蒙版、修复与排版](./mask-inpainting-and-rendering.md)，特殊工作流见[特殊工作流与 WebSocket](./special-workflows-and-websocket.md)。调试产物的清理、脱敏和对外分享见[如何阅读和分享一次调试运行](./how-to-read-and-share-a-debug-run.md)。

## 功能边界

- `cli.verbose`（UI：“详细日志” / `Verbose Logging`）决定是否生成单图调试子目录；关闭时不会创建带时间戳的图片级子目录。
- 图片级调试子目录名固定为 `{时间戳毫秒}-{图片 MD5 前 8 位}-{检测尺寸}-{目标语言}-{翻译器}`，由 `_set_image_context()` 在每张输入图开始处理时建立。
- 调试目录统一位于 `BASE_PATH/result/` 下；`BASE_PATH` 在冻结（打包）运行时是可执行文件所在目录，在源码运行时是仓库根目录。
- 本页只负责命名规则与产物总览；每个产物的深入含义分别由其他 debugging 页面承接，本页不重复展开。

## UI 操作

### 在设置页开启详细日志

1. 打开“设置”（`Settings`），选择“通用”（`General`）分组。
2. 打开“详细日志”（`Verbose Logging`）开关。开关保存到 `cli.verbose`；右侧说明面板显示 `desc_cli_verbose` 文案。
3. 开始翻译后，中间产物写入 `BASE_PATH/result/` 下按命名规则生成的图片级子目录。
4. 翻译失败时，“翻译错误”对话框会提供“打开日志文件夹”（`Open log folder`）按钮，直接打开 `result/` 目录。

命令行 `local` 模式同样支持 `-v/--verbose` 参数，效果与桌面开关一致，详见 CLI 文档。

### UI 调用 key 与中英文案

| UI 调用 key | English 实际值 | 简体中文实际值 |
| --- | --- | --- |
| `General` | General | 通用 |
| `label_verbose` | Verbose Logging | 详细日志 |
| `Open log folder` | Open log folder | 打开日志文件夹 |

设置项说明面板 `desc_cli_verbose` 的实际文案如下（英文）：

> Output detailed debug info to logs for troubleshooting.
>
> When enabled, Qt UI writes these items under result/:
> - log_timestamp.txt: Qt UI runtime log
> - timestamp-image-target-translator/: debug intermediate files for a single task
>
> Cleanup: close Qt UI first, then delete the unneeded log_*.txt files and matching timestamp debug folders under result/.

简体中文实际文案如下：

> 输出详细的调试信息到日志，方便排查问题。
>
> 开启后会在 result/ 目录生成：
> - log_时间戳.txt：Qt UI 运行日志
> - 时间戳-图片名-目标语言-翻译器/：单次任务的调试中间文件
>
> 清理方法：先关闭 Qt UI，再到 result/ 目录删除不需要的 log_*.txt 和对应的时间戳调试文件夹即可。

注意：说明面板把子目录写成“时间戳-图片名-目标语言-翻译器”，但实际代码生成的是“时间戳-图片 MD5-检测尺寸-目标语言-翻译器”。本页以代码为准（见下文“运行机理”）；这是 UI 文案与实现不一致的已知差异，不影响目录的实际用途。

## 选项中英对照

| 存储值 | English | 简体中文 |
| --- | --- | --- |
| `cli.verbose` | Verbose Logging | 详细日志 |
| `detector.detection_size` | Detection Size | 检测大小 |
| `translator.target_lang` | Target Language | 目标语言 |
| `translator.translator` | Translator | 翻译器 |

- `cli.verbose`：布尔开关。核心代码 `manga_translator/config.py` 读取参数时兜底为 `False`，Qt 模型默认 `False`，发行配置 `config/config-example.json` 为 `false`。它决定图片级调试子目录是否生成，并影响控制台日志级别。
- `detector.detection_size`：检测时的图像缩放尺寸，默认 `2048`。它直接出现在目录名中；`_set_image_context()` 使用 `getattr(config.detector, 'detection_size', 1024)` 兜底。
- `translator.target_lang`：目标语言代码（如 `CHS`、`ENG`）。目录名中的取值来自 `config.translator.target_lang`，兜底为 `unknown`。
- `translator.translator`：翻译器标识（如 `openai`）。目录名中的取值来自 `config.translator.translator`，兜底为 `unknown`。

## 运行机理

### 图片级子目录命名规则

`MangaTranslator._set_image_context()`（`manga_translator/manga_translator.py`）在每张输入图开始处理时按以下格式生成调试子目录名：

```text
{timestamp_ms}-{input_md5}-{detection_size}-{target_lang}-{translator}
```

| 字段 | 来源 | 示例 | 说明 |
| --- | --- | --- | --- |
| `timestamp_ms` | `str(int(time.time() * 1000))` | `1785860417472` | 毫秒级时间戳，保证目录名唯一 |
| `input_md5` | `get_image_md5(image)` | `3415b69c` | 图片内容 MD5 前 8 位；相同图片内容得到相同值 |
| `detection_size` | `config.detector.detection_size` | `2048` | 检测尺寸；兜底 `1024` |
| `target_lang` | `config.translator.target_lang` | `CHS` | 目标语言代码；缺配置时兜底 `unknown` |
| `translator` | `config.translator.translator` | `openai` | 翻译器标识；缺配置时兜底 `unknown` |

`get_image_md5()`（`manga_translator/utils/generic.py`）先把图片统一转为 RGB，再编码为 PNG 字节流计算 MD5，只取前 8 位十六进制，避免目录名过长；计算失败时回退为 `fallback_{毫秒时间戳}`。没有传入图片时，MD5 字段为 `unknown`。

TODO 中的核对样例 `result/1785860417472-3415b69c-2048-CHS-openai/` 正好对应上面五个字段：毫秒时间戳 `1785860417472`、图片 MD5 `3415b69c`、检测尺寸 `2048`、目标语言 `CHS`、翻译器 `openai`。目录名本身只包含哈希与配置值，不包含用户文字；目录内的文件属于用户内容，分享前必须脱敏。

```mermaid
flowchart LR
    TS["毫秒时间戳\ntime.time() * 1000"] --> SUB["{timestamp}-{md5}-{size}-{lang}-{translator}"]
    HASH["图片内容 MD5 前 8 位\nget_image_md5()"] --> SUB
    SIZE["检测尺寸\ndetector.detection_size"] --> SUB
    LANG["目标语言\ntranslator.target_lang"] --> SUB
    TR["翻译器\ntranslator.translator"] --> SUB
    SUB --> DIR["BASE_PATH/result/&lt;图片级子目录&gt;/产物"]
```

### 调试路径的组成

`_result_path()`（`manga_translator/manga_translator.py`）根据 verbose、图片上下文和 `result_sub_folder` 决定最终路径，并创建父目录：

| 条件 | 返回路径 |
| --- | --- |
| `verbose=True`、有图片上下文、`result_sub_folder` 非空 | `BASE_PATH/result/<result_sub_folder>/<图片级子目录>/<产物>` |
| `verbose=True`、有图片上下文、`result_sub_folder` 为空 | `BASE_PATH/result/<图片级子目录>/<产物>` |
| 非 verbose（或没有图片级路径）、`result_sub_folder` 为空 | `BASE_PATH/result/<产物>` |
| 非 verbose、`result_sub_folder` 非空 | `BASE_PATH/result/<result_sub_folder>/<产物>` |

`result_sub_folder` 在构造函数中默认为空字符串；当前源码静态搜索未发现仓库内的后续赋值，因此实际运行通常落在第二行或第三行。

```mermaid
flowchart TD
    V{"verbose 开启?"}
    V -->|否| N1["BASE_PATH/result/&lt;产物&gt;"]
    V -->|是| S{"有图片级子目录?"}
    S -->|否| N2["BASE_PATH/result/&lt;产物&gt;"]
    S -->|是| R{"result_sub_folder 非空?"}
    R -->|否| P1["BASE_PATH/result/&lt;图片级子目录&gt;/&lt;产物&gt;"]
    R -->|是| P2["BASE_PATH/result/&lt;result_sub_folder&gt;/&lt;图片级子目录&gt;/&lt;产物&gt;"]
```

`_run_detection()` 即使关闭 verbose 也会把 `self._result_path` 作为回调传给检测调度（`manga_translator/manga_translator.py:1744`），但各写入点都由 `verbose` 分支保护，因此关闭 verbose 不会产生图片级调试子目录。

### 目录结构总览

```text
BASE_PATH/result/
├─ log_20260807120000.txt          # Qt UI / CLI 运行日志（时间戳为 yyyyMMddHHmmss）
└─ 1785860417472-3415b69c-2048-CHS-openai/   # verbose 单图调试目录
   ├─ input.png                     # 处理前输入图
   ├─ bboxes.png                    # 合并后的文本块可视化
   ├─ mask_raw.png                  # 原始检测置信热图
   ├─ inpaint_input.png             # 修复输入预览
   ├─ mask_final.png                # 最终修复蒙版
   ├─ inpainted.png                 # 修复结果
   ├─ final.png                     # 最终输出（含还原尺寸）
   ├─ ocrs/                         # 每个文本区域的 OCR 裁切输入
   │  ├─ 0.png
   │  └─ 1.png
   └─ ...                           # 条件产物见下表
```

### 产物总览：基础产物

以下产物在 verbose 正常流程中最常出现；是否真正生成仍取决于检测/OCR 结果与翻译进度：

| 产物 | 生成阶段与写入点 | 触发条件 | 内容与排查用途 |
| --- | --- | --- | --- |
| `input.png` | `_translate_until_translation()`（`manga_translator.py:4255`） | `verbose=True` | 处理前的输入图；检测/OCR 问题排查 |
| `mask_raw.png` | 检测后（`manga_translator.py:4551`） | `verbose=True` 且检测返回 `ctx.mask_raw` | 带颜色条的原始检测置信热图；检测阈值排查 |
| `bboxes_unfiltered.png` | 检测后（`manga_translator.py:4566`） | `verbose=True` 且检测后仍有文本行 | 原图上的未过滤文本行框；检测/OCR 过滤排查 |
| `bboxes.png` | OCR 与文本行合并后（`manga_translator.py:4607`） | `verbose=True` 且存在 `ctx.text_regions` | 最终文本块可视化；合并/排序排查 |
| `inpaint_input.png` | 修复前（`manga_translator.py:5247`） | `verbose=True` 且翻译完成后有 `ctx.mask` | 修复输入预览 |
| `mask_final.png` | 修复前（`manga_translator.py:5251`） | 同上 | 最终用于修复的蒙版 |
| `inpainted.png` | 修复后（`manga_translator.py:5277`） | verbose 正常流程 | 修复后的图像 |
| `final.png` | `_revert_upscale()`（`manga_translator.py:1547`） | 调用还原尺寸、存在 `ctx.result` 且 verbose | 最终（或还原尺寸后）的 PIL 输出 |
| `ocrs/<序号>.png` | 各 OCR 实现（`manga_translator/ocr/*.py`） | `verbose=True` 且文本区域未被前置过滤 | 透视裁切后的 OCR 输入，垂直文本会旋转；48px/Manga OCR/PaddleOCR 压缩至 200px |

### 产物总览：条件产物

以下产物只在特定配置、检测器、工作流或模式下生成，不能描述成每次 verbose 运行必有：

| 产物 | 触发条件 | 内容与排查用途 |
| --- | --- | --- |
| `bboxes_unfiltered_labeled.png` | `ocr.merge_special_require_full_wrap=True` 且标签绘制器收到非空文本行 | 带序号/标签的文本行框；模型辅助合并排查 |
| `bboxes_with_scores.png`、`mask_binary.png` | 检测器第三返回值为评分框/二值掩码调试元组 | 检测评分框与配套二值掩码 |
| `hybrid_detection_boxes.png` | 检测器第三返回值为图像且 `detector.use_yolo_obb=True` | 主检测与 YOLO OBB 合并框图 |
| `rearrange_<序号>.png` | 默认/DBConvNext/CTD 检测器触发长图重排计划 | 送入检测网络前的方形补边批次；长图重排排查 |
| `yolo_rearrange_<序号>.png` | `use_yolo_obb=True` 且 YOLO OBB 得到重排计划 | YOLO OBB 单个重排 patch |
| `mask_bubble_clip_debug.png` | `limit_mask_dilation_to_bubble_mask=True` 且成功取得非空气泡掩码 | 气泡裁剪前后和保护区域叠加图 |
| `balloon_fill_boxes.png` | `layout_mode='balloon_fill'` 且渲染器返回非空调试图 | 气泡填充布局调试图 |
| `chinese_linebreak_debug.json` | 同上且累计了非空断句记录 | 中文断句记录；可能含原文/译文，分享前须脱敏 |
| `replace_debug_match.jpg`、`debug_extracted_text.png`、`inpainted.png` | 替换翻译工作流且 verbose | 匹配框/重叠信息、抽取文字与替换流程修复图 |
| `ws_final.png`、`ws_render_in.png`、`ws_render_out.png`、`ws_mask.png`、`ws_inmask.png`、`ws_output.png` | WebSocket 模式且 verbose | WS 渲染各中间/最终图 |
| `<输入文件名>_photoshop_script.jsx` | PSD 导出且 verbose 或 `psd_script_only` | Photoshop 自动化脚本；可能含图层文本和文件路径，分享前须脱敏 |
| `log_<yyyyMMddHHmmss>.txt` | Qt UI 启动或 CLI local 初始化日志 | 应用根级运行日志，不属于单图调试子目录 |

### 实际产物与条件产物的区分

- 上表列出的是当前源码在不同模式、配置和工作流下**可能**产生的完整集合，不是某一次运行必然生成的全部文件。
- 无文本页、早退、失败/取消、特殊工作流（替换翻译、WebSocket、仅 JSON 等）都会跳过不同阶段，从而跳过对应产物。
- 静态搜索未发现仓库内对这些调试文件名的后续读回；它们是启用 verbose 的操作者或问题报告接收者的终端诊断写入。
- 判断某次运行“实际存在哪些产物”，应回到 `result/` 目录按本页命名规则找到对应子目录，再结合运行时的配置与工作流核对，不能把条件产物当作每次必有。

## 依赖与冲突

- 图片级子目录依赖 verbose 开关、图片上下文和 `result_sub_folder`；三者缺一都会落到不带图片级子目录的路径。
- `ocrs/` 子目录由 `_run_ocr()` 在 verbose 时通过临时设置 `MANGA_OCR_RESULT_DIR` 指向图片级 `ocrs/` 生成；直接调用 OCR 实现且环境变量未设置时回退到 `result/ocrs/`。
- `BASE_PATH` 在冻结打包与源码运行下指向不同位置（可执行文件目录 vs 仓库根目录），跨机器对照目录时要注意。
- Qt UI 的 `result/log_*.txt` 文件日志在启动时创建且文件处理器始终为 DEBUG 级别，即使关闭 verbose 也会生成；verbose 主要影响控制台日志级别和图片级调试子目录。
- 调试目录可能包含完整页面图、识别文本、框坐标、翻译结果或 JSX 中的本机路径，不能直接打包上传或公开。

## 关联文件与格式

| 文件/目录 | 格式与命名 | 说明 |
| --- | --- | --- |
| `BASE_PATH/result/` | 目录 | verbose 调试产物根目录 |
| `BASE_PATH/result/<图片级子目录>/` | 目录 | 命名规则见“运行机理”；每张输入图一个 |
| `BASE_PATH/result/<图片级子目录>/ocrs/` | 目录 | OCR 裁切输入，`<序号>.png` |
| `BASE_PATH/result/log_<yyyyMMddHHmmss>.txt` | UTF-8 文本日志 | Qt UI / CLI 运行日志，应用根级 |
| 图片级产物（`input.png`、`bboxes.png`、`mask_raw.png`、`inpaint_input.png`、`mask_final.png`、`inpainted.png`、`final.png` 等） | PNG | 供人工排查的终端诊断写入 |
| 条件产物（JSON/JSX/JPG 等） | 见“产物总览” | 触发条件见对应表格 |

## 源码依据 {#source-evidence}

| 层级 | 文件 | 本页核对内容 |
| --- | --- | --- |
| 命名规则 | `manga_translator/manga_translator.py` `_set_image_context()`（约 L457） | 子目录名五字段的组成与兜底值 |
| 图片哈希 | `manga_translator/utils/generic.py` `get_image_md5()` | RGB 归一化、PNG 字节流、MD5 前 8 位与 fallback |
| 路径组成 | `manga_translator/manga_translator.py` `_result_path()`（约 L3315） | verbose/图片上下文/`result_sub_folder` 四分支 |
| 检测回调 | `manga_translator/manga_translator.py` `_run_detection()`（约 L1744） | verbose 关闭也传回调但写入受保护 |
| OCR 目录 | `manga_translator/manga_translator.py` `_run_ocr()`（约 L2406-2415） | `MANGA_OCR_RESULT_DIR` 与 `ocrs/` 路径 |
| 各产物写入点 | `manga_translator/manga_translator.py`、`manga_translator/ocr/*.py`、`manga_translator/mask_refinement/__init__.py`、`manga_translator/utils/replace_translation.py`、`manga_translator/mode/ws.py`、`manga_translator/utils/photoshop_export.py` | 产物总览两表的写入点与触发条件 |
| UI/i18n | `desktop_qt_ui/ui/main_page/settings_tab_layout.json`、`desktop_qt_ui/ui/main_page/dynamic_settings.py`、`desktop_qt_ui/app_logic.py`、`desktop_qt_ui/locales/en_US.json`、`desktop_qt_ui/locales/zh_CN.json` | `General`、`label_verbose`、`desc_cli_verbose` 实际显示值 |
| 日志文件 | `desktop_qt_ui/main.py`（约 L208-222）、`manga_translator/mode/local.py`（约 L162-167） | `log_<yyyyMMddHHmmss>.txt` 的生成 |
| 调查基线 | `doc/wiki/research/phase0-debug-artifact-path-trace.md`、`doc/wiki/research/phase0-related-files-formats-debug-safety.md`、`doc/wiki/research/phase0-page-coverage-matrix.md` | 产物清单、路径契约与覆盖矩阵 |

## 验证记录 {#verification}

| 验证内容 | 状态 | 说明 |
| --- | --- | --- |
| BLUEPRINT、PAGE_GUIDELINES、TODO | 完成 | 已完整读取 1.3 节、5.15 节与 6.3 节并按页面合同编写 |
| 目录命名规则 | 完成 | 静态核对 `_set_image_context()`、`get_image_md5()` 与 TODO 核对样例 |
| 路径组成 | 完成 | 静态核对 `_result_path()` 四分支与 `result_sub_folder` 默认值 |
| 产物总览与触发条件 | 完成 | 静态核对 `_result_path()` 直接写入、`result_path_fn`/`debug_path_fn` 回调与手工路径 |
| UI 与 i18n | 完成 | 逐项记录 `General`、`label_verbose`、`desc_cli_verbose` 等实际值；已标注说明面板与代码命名规则的差异 |
| 脱敏运行验证 | 待后续 | 未启动 GUI、未执行真实翻译；基础文件组合、早退与各条件产物的实际生成需脱敏运行确认 |
| VitePress | 待运行 | 由协调代理在合并前运行 `npm run docs:build --prefix doc/wiki` 及镜像/源码检查 |
