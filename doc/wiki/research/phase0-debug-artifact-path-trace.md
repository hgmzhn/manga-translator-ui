# Phase 0：调试产物路径与写入源码证据

> 范围：追踪 `MangaTranslator._result_path()` 的直接写入、`result_path_fn` / `debug_path_fn` 回调，以及同一 `result/` 树中未经过该方法的手工路径。
>
> 证据类型：静态源码核对，未启动 GUI、未执行真实翻译、未读取用户运行产物或私有配置。

## 路径契约

`MangaTranslator._set_image_context()` 在每张输入图开始处理时建立调试子目录名：

```text
{timestamp_ms}-{input_md5}-{detection_size}-{target_lang}-{translator}
```

`_result_path()` 的实际组成如下。`result_sub_folder` 在构造函数中默认为空字符串；当前源码搜索未发现仓库内的后续赋值。

| 条件 | 返回路径 | 来源 |
| --- | --- | --- |
| `verbose=True`、有图片上下文、`result_sub_folder` 非空 | `BASE_PATH/result/<result_sub_folder>/<image-subfolder>/<artifact>` | `manga_translator/manga_translator.py:3321` |
| `verbose=True`、有图片上下文、`result_sub_folder` 为空 | `BASE_PATH/result/<image-subfolder>/<artifact>` | `manga_translator/manga_translator.py:3321` |
| 无图片级 verbose 路径且 `result_sub_folder` 为空 | `BASE_PATH/result/<artifact>` | `manga_translator/manga_translator.py:3334` |
| 无图片级 verbose 路径且 `result_sub_folder` 非空 | `BASE_PATH/result/<result_sub_folder>/<artifact>` | `manga_translator/manga_translator.py:3341` |

该方法会创建父目录。下表中所有“图片级路径”均指前两种路径；它们不会自动代表每次运行都会产生。

## `_result_path()` 直接写入

除特别说明外，下列文件是终端诊断写入：静态搜索没有发现仓库内对这些文件名的后续读回，消费者是启用 verbose 的操作者或问题报告接收者。

| 产物 | 写入点 | 触发条件 | 内容与消费者 |
| --- | --- | --- | --- |
| `input.png` | `manga_translator/manga_translator.py:4255` | `_translate_until_translation()` 且 `verbose=True` | 处理前的输入图；检测/OCR 问题排查。 |
| `mask_raw.png` | `manga_translator/manga_translator.py:4551` | `verbose=True`，检测返回 `ctx.mask_raw` | 带颜色条的原始检测置信热图；检测阈值排查。 |
| `bboxes_unfiltered.png` | `manga_translator/manga_translator.py:4566` | `verbose=True`，且检测后仍有文本行 | 原图上的未过滤文本行框（跳过标签为 `other` 的框）；检测/OCR 过滤排查。 |
| `bboxes_unfiltered_labeled.png` | `manga_translator/manga_translator.py:4571`，经 `:1955` 写入 | 上一条件成立，`config.ocr.merge_special_require_full_wrap=True`，并且标签绘制器收到非空文本行 | 当前唯一调用者传入的带序号/标签文本行框；模型辅助合并排查。辅助方法的 `filename` 参数理论上允许其他文件名。 |
| `bboxes_with_scores.png` | `manga_translator/manga_translator.py:1759`、`:1776`、`:1796` | `verbose=True`，检测器第三返回值为三元/二元调试元组，或为非混合检测的图像 | 检测器返回的评分框图；检测器内部调试输出。 |
| `mask_binary.png` | `manga_translator/manga_translator.py:1763`、`:1779` | `verbose=True`，检测器第三返回值为三元或二元调试元组 | 与上一类检测返回配套的二值掩码；检测输出排查。 |
| `hybrid_detection_boxes.png` | `manga_translator/manga_translator.py:1791` | `verbose=True`，检测器第三返回值为图像，且 `config.detector.use_yolo_obb=True` | 主检测与 YOLO OBB 合并框图；混合检测排查。 |
| `bboxes.png` | `manga_translator/manga_translator.py:4607` | `verbose=True`，OCR 与文本行合并后存在 `ctx.text_regions` | 最终文本块可视化（是否展示 panel 取决于 `force_simple_sort`）；合并/排序排查。 |
| `mask_bubble_clip_debug.png` | `manga_translator/mask_refinement/__init__.py:301`，由 `manga_translator/manga_translator.py:3020` 传入回调 | `verbose=True`、`limit_mask_dilation_to_bubble_mask=True`、成功取得非空模型气泡掩码 | 气泡、裁剪前后和保护区域的叠加图；气泡约束蒙版排查。 |
| `inpaint_input.png` | `manga_translator/manga_translator.py:5247` | `verbose=True`，完成翻译后有 `ctx.mask` | 使用 `Inpainter.none` 生成的修复输入预览；修复输入排查。 |
| `mask_final.png` | `manga_translator/manga_translator.py:5251` | 与 `inpaint_input.png` 相同 | 最终用于修复的蒙版；修复范围排查。 |
| `inpainted.png` | `manga_translator/manga_translator.py:5277` | 正常流程 `verbose=True` | 修复后的图像；修复结果排查。 |
| `balloon_fill_boxes.png` | `manga_translator/manga_translator.py:3179` | `verbose=True`、`layout_mode='balloon_fill'`，且渲染器返回非空调试图 | 气泡填充布局的渲染器调试图；排版问题排查。 |
| `chinese_linebreak_debug.json` | `manga_translator/manga_translator.py:3188` | 与上一项相同，且配置上累计了非空 `_chinese_linebreak_debug_records` | 中文断句记录；内容可能含原文/译文，分享前须脱敏。 |
| `final.png` | `manga_translator/manga_translator.py:1547` | `_revert_upscale()` 被调用、存在 `ctx.result`、`verbose=True` | 最终（或被还原尺寸后的）PIL 输出；最终图与常规保存结果对照。 |
| `replace_debug_match.jpg` | `manga_translator/utils/replace_translation.py:367` | 替换翻译工作流、区域匹配阶段、`translator.verbose=True` | 生肉/翻译框、匹配线与重叠率；替换匹配排查。 |
| `inpainted.png` | `manga_translator/utils/replace_translation.py:484` | 替换翻译工作流完成修复、`translator.verbose=True` | 替换流程的修复图；与正常流程同名，属于该图的同一调试目录。 |
| `debug_extracted_text.png` | `manga_translator/utils/replace_translation.py:560` | 替换翻译工作流、`enable_template_alignment=True` 的直接粘贴分支、`translator.verbose=True` | 从翻译图按蒙版提取并叠加的文字像素；直接粘贴排查。 |
| `ws_final.png` | `manga_translator/mode/ws.py:159` | WebSocket 模式翻译有输出且 `verbose=True` | 上传给服务前、恢复原尺寸后的 PNG；WS 上传结果排查。 |
| `ws_render_in.png` | `manga_translator/mode/ws.py:301` | WebSocket 渲染阶段、`verbose=True` | WS 渲染前底图；WS 渲染差异排查。 |
| `ws_render_out.png` | `manga_translator/mode/ws.py:302` | 与上一项相同 | WS 渲染器输出；WS 渲染差异排查。 |
| `ws_mask.png` | `manga_translator/mode/ws.py:303` | 与上一项相同 | 根据差异补充后的渲染蒙版；WS 遮罩排查。 |
| `ws_inmask.png` | `manga_translator/mode/ws.py:307` | 与上一项相同 | 仅保留渲染蒙版区域的输入；WS 叠加边界排查。 |
| `ws_output.png` | `manga_translator/mode/ws.py:310` | 与上一项相同 | 仅保留渲染蒙版区域的 RGBA 输出；WS 上传载荷排查。 |

## `result_path_fn` 回调链

`_run_detection()` 在 `manga_translator/manga_translator.py:1744` 将 `self._result_path` 传给检测调度；即使关闭 verbose 也传递回调，但实际写入点均由 `verbose` 分支保护。

| 产物 | 回调写入点 | 触发条件 | 路径与消费者 |
| --- | --- | --- | --- |
| `rearrange_<index>.png` | `manga_translator/utils/generic.py:1682` | 默认、DBConvNext 或 CTD 检测器调用 `det_rearrange_forward()`；长宽比/检测尺寸满足 `build_det_rearrange_plan()`；`verbose=True` | 有回调时为图片级路径。各检测器的转发点为 `detection/default.py:79`、`detection/dbnet_convnext.py:559`、`detection/ctd.py:154`；内容是送入检测网络前的方形补边批次，供长图重排排查。 |
| `yolo_rearrange_<index>.png` | `manga_translator/detection/yolo_obb.py:430` | `use_yolo_obb=True`，YOLO OBB 也得到长图重排计划，当前 patch 有效，且 `verbose=True` | 有回调时为图片级路径；内容是 YOLO OBB 的单个重排 patch，供混合检测长图排查。 |
| `<input-stem>_photoshop_script.jsx` | `manga_translator/utils/photoshop_export.py:802` | 导出可编辑 PSD，`verbose=True` 或 `script_only=True`；当不是 `script_only and image_path` 且提供回调时 | 图片级路径中的 Photoshop JSX。常规调用位于 `manga_translator/manga_translator.py:699` 与 `manga_translator/utils/replace_translation.py:645`；消费者是人工在 Photoshop 中检查/执行脚本。JSX 可含文本和文件路径，分享前须脱敏。 |

`script_only=True` 且有 `image_path` 时，PSD 模块优先改写到 `<input-dir>/manga_translator_work/psd/<input-stem>_photoshop_script.jsx`（`manga_translator/utils/photoshop_export.py:798`），因此不是本清单的 `result/` 图片级产物。

## `debug_path_fn` 回调链

只有蒙版精炼使用该回调。`_run_mask_refinement()` 仅在 verbose 时传入 `self._result_path`（`manga_translator/manga_translator.py:3020`）；`mask_bubble_clip_debug.png` 的构成、条件和消费者见直接写入表。

## 同一 `result/` 树的手工路径

这些写入不经 `_result_path()`，所以路径和命名规则需要单独记录。

| 产物或目录 | 手工写入点 | 触发条件 | 路径与消费者 |
| --- | --- | --- | --- |
| `ocrs/<index>.png` | `manga_translator/ocr/model_32px.py:97`、`model_48px.py:133`、`model_48px_ctc.py:116`、`model_manga_ocr.py:307`、`model_paddleocr.py:323` | 对应 OCR 实现收到 `verbose=True`，且该文本区域未被前置过滤 | `_run_ocr()` 临时设置 `MANGA_OCR_RESULT_DIR` 为图片级 `ocrs/`（`manga_translator/manga_translator.py:2406`）；文件是透视裁切后的 OCR 输入，垂直文本会旋转。48px、Manga OCR 与 PaddleOCR 会压缩并限制至 200px。消费者是 OCR 识别问题排查；同一次混合 OCR 的两次运行可能使用同一编号，需运行确认最终保留情况。 |
| `result/ocrs/<index>.png` | 上一行的五个 OCR 实现 | 直接调用 OCR 实现且环境变量未设置、`verbose=True` | 回退路径，不带图片级子目录；静态正常翻译入口会设置环境变量，故此分支主要供独立调用/异常调用路径。 |
| `result/rearrange_<index>.png` | `manga_translator/utils/generic.py:1684` | `det_rearrange_forward()` 满足重排计划、`verbose=True`，但未提供 `result_path_fn` | 长图检测的回退路径，不带图片级子目录。`manga_translator/utils/ctd_replace.py:55` 是当前源码中未传回调的调用点；普通检测调度会传入回调。 |
| `result/yolo_rearrange_<index>.png` | `manga_translator/detection/yolo_obb.py:432` | YOLO OBB 重排、`verbose=True`，但未提供 `result_path_fn` | YOLO OBB 的回退路径，不带图片级子目录；普通检测调度会传入回调。 |
| `result/log_<yyyyMMddHHmmss>.txt` | `desktop_qt_ui/main.py:208` | 启动桌面 Qt UI | 应用根目录的全局 DEBUG 日志，不属于单图调试子目录。未捕获异常会写入同一日志配置（`desktop_qt_ui/main.py:72`）；消费者是桌面端诊断与错误报告。 |

## 静态验证与运行未决项

静态核对命令：

```powershell
rg -n -F 'self._result_path(' manga_translator desktop_qt_ui -g '*.py'
rg -n -F 'result_path_fn' manga_translator desktop_qt_ui -g '*.py'
rg -n -F 'debug_path_fn' manga_translator desktop_qt_ui -g '*.py'
rg -n -F 'MANGA_OCR_RESULT_DIR' manga_translator
rg -n -F 'det_rearrange_forward(' manga_translator
```

以上命令已用于生成本清单；未运行任何翻译或 GUI。仍需以脱敏样例分别验证：

1. 正常 verbose 流程的基础文件、无文本早退和 `final.png` 的实际组合。
2. 默认/DBConvNext/CTD 与 YOLO OBB 长图重排的命名、回退路径和坐标可读性。
3. 五种 OCR 实现及混合 OCR 的 `ocrs/` 编号、覆盖行为和 200px 缩放。
4. 气泡约束蒙版、`balloon_fill`、中文断句记录、替换翻译两条分支和 PSD JSX。
5. WebSocket 上传流程、不同部署方式的 `BASE_PATH`，以及运行时是否有外部组件设置 `result_sub_folder`。

任何调试图、OCR 裁切图、断句 JSON、JSX 和日志都可能含用户图像、文本或本机路径；对外分享前必须脱敏，且不能把条件产物描述成每次运行必有。
