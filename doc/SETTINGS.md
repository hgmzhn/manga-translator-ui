# 设置说明

本文档详细介绍程序的所有设置选项和参数。

---

## 📖 参数详解

界面分为三个标签页，每个标签页包含不同的设置选项：

---

## 基础设置

### 翻译器设置

- **翻译器 (translator)**：选择翻译引擎
  - 在线翻译器：OpenAI、Google Gemini、Vertex、DeepL、百度翻译等
  - 高质量翻译器：高质量翻译 OpenAI、高质量翻译 Gemini、高质量翻译 Vertex（推荐）

- **目标语言 (target_lang)**：翻译的目标语言
  - 简体中文、繁体中文、英语、日语、韩语等

- **保留源语言 (keep_lang)**：文本框合并完成后、进入翻译前，只保留识别为指定源语言的文本区域
  - 位置：翻译设置 → 翻译器
  - 默认：`none`（不过滤）
  - 适用场景：英文版日漫只保留英文文本，尽量跳过保留原样的日文标题、拟声词和装饰字
  - 工作阶段：仅作用于正常的 OCR → 文本框合并 → 翻译 主流程，在文本框合并后、翻译前执行
  - 英文规则：纯拉丁字母、数字、空白和常见英文标点会优先判为 `ENG`
  - 日文规则：含平假名 / 片假名时优先判为 `JPN`
  - 中日共用汉字规则：如果文本只有汉字、没有假名，不强行细分中日，而是按共享 CJK 文本处理；选择 `CHS`、`CHT` 或 `JPN` 时都会保留，避免把 `開始`、`決戦` 这类文本误过滤掉
  - 使用建议：
    - 英文漫画推荐配合 `paddleocr_latin` 使用，并将此项设为 `ENG`
    - 若希望不过滤任何文本，保持为 `none`

- **启用流式传输 (enable_streaming)**：控制是否优先使用流式翻译响应
  - 适用于：OpenAI、Gemini、Vertex、高质量翻译 OpenAI、高质量翻译 Gemini、高质量翻译 Vertex 六个翻译器
  - 默认：开启（`true`）
  - 开启后：优先使用统一流式传输层，实时接收增量响应；失败时自动回退到普通请求
  - 关闭后：始终使用普通非流式请求，不再尝试流式传输
  - 适用场景：某些代理、中转站、本地兼容层或 API 网关对流式支持不稳定时，可关闭此项提高兼容性

- **不跳过目标语言文本 (no_text_lang_skip)**：不跳过已是目标语言的文本（强制翻译）
  - 开启后：即使 OCR / 语言检测结果已经是目标语言，也会继续进入翻译流程
  - 覆盖范围：普通批量、高质量批量、`batch_concurrent` 并发流水线
  - 同文结果不会再因为“译文与原文相同”而被后处理阶段误判为已跳过

- **自定义提示词 (high_quality_prompt_path)**：自定义提示词文件路径
  - 适用于：OpenAI、Gemini、Vertex、高质量翻译 OpenAI、高质量翻译 Gemini、高质量翻译 Vertex 六个翻译器
  - 默认：`dict/prompt_example.yaml`
  - 可以在 `dict` 目录下创建新的 `.yaml` 或 `.json` 文件
  - YAML/JSON 格式只需符合标准规范即可加载，优先加载 YAML
  - 程序会在每次打开下拉菜单时自动扫描 `dict` 目录
  - 添加新提示词文件后，直接点击下拉菜单即可看到新文件，无需重启

- **自动提取新术语 (extract_glossary)**：自动从翻译结果中提取新术语
  - 适用于：OpenAI、Gemini、Vertex、高质量翻译 OpenAI、高质量翻译 Gemini、高质量翻译 Vertex 六个翻译器
  - 勾选后，AI 会自动识别并提取人名、地名、组织名等专有名词
  - 提取的术语会自动添加到自定义提示词的术语表中
  - 后续翻译时会参考这些术语，保持翻译一致性
  - 特别适合长篇漫画的连续翻译，确保角色名等专有名词前后一致

- **自动移除末尾句号 (remove_trailing_period)**：当原文结尾没有标点时，自动移除译文末尾额外补上的句号
  - 适用于：主翻译流程
  - 覆盖范围：普通批量、高质量批量、`batch_concurrent` 并发流水线
  - 会处理的结尾符号：`。`、`.`、`．`
  - 不会处理的情况：
    - 原文本来就有句末标点
    - 连续句点 / 省略号（如 `...`）
    - 编辑器内“单独翻译”按钮

- **简体转繁体 (convert_to_traditional)**：翻译后将简体中文转换为繁体中文
  - 位置：Qt UI 的“翻译”页 -> “Post Processing” 分组
  - 使用 OpenCC `s2twp` 配置（台湾用词标准），不仅转换字符，还会转换地区用词（如“软件”→“軟體”）
  - 适用于：所有翻译模式（普通批量、高质量批量、并发流水线）
  - 与“繁体转简体”互斥，同时开启时以本选项优先

- **繁体转简体 (convert_to_simplified)**：翻译后将繁体中文转换为简体中文
  - 位置：Qt UI 的“翻译”页 -> “Post Processing” 分组
  - 使用 OpenCC `t2s` 配置
  - 适用于：所有翻译模式（普通批量、高质量批量、并发流水线）
  - 与“简体转繁体”互斥，同时开启时以“简体转繁体”优先

- **使用自定义 API 参数 (use_custom_api_params)**：启用自定义 API 参数
  - 位置：Qt UI 的“通用”页
  - 适用于：翻译、AI 识别（OCR）、AI 渲染、AI 上色
  - 勾选后，程序会从 `examples/custom_api_params.json` 读取自定义参数并传递给已启用的 AI API
  - 点击"打开文件"按钮可自动创建并打开配置文件
  - 配置文件为标准 JSON 格式，支持实时生效（每次翻译都会重新加载）
  - 使用场景：
    - 控制 Ollama 等本地模型的特殊参数（如关闭思考模式）
    - 调整 API 的温度、最大 token 数等参数
    - 传递模型特定的配置选项
  - 推荐按类型分组配置：
    ```json
    {
      "translator": {
        "thinking": {"type": "disabled"}
      },
      "ocr": {
        "response_format": {"type": "json_object"}
      },
      "render": {
        "quality": "high"
      },
      "colorizer": {
        "size": "1536x1536"
      }
    }
    ```
  - 若某个参数要同时发给所有 AI 后端，可放到 `common`：
    ```json
    {
      "common": {
        "timeout": 120
      },
      "translator": {
        "thinking": {"type": "disabled"}
      }
    }
    ```
  - 兼容旧格式：如果直接在 JSON 顶层写键，会按 `common` 通用参数处理
  - 翻译器关闭思考模式示例：
    ```json
    {
      "translator": {
        "thinking": {"type": "disabled"},
        "thinking_budget": 0
      }
    }
    ```

- **最大请求速率 (max_requests_per_minute)**：每分钟最大请求数（0 = 不限制）

### CLI 选项

- **详细日志 (verbose)**：输出详细的调试信息（问题排查建议开启）
  - 默认：关闭（`false`）
  - 开启后：
    - 日志窗口会显示更多 `DEBUG` 级别过程日志（检测、OCR、渲染等中间步骤）
    - 每次任务会在 `result/时间戳-图片名-目标语言-翻译器/` 生成调试中间文件
    - UI 会写入 `result/log_时间戳.txt` 运行日志文件（文件日志始终保留完整信息）
  - 关闭后：
    - 界面以 `INFO` 级别为主，日志更简洁，适合日常使用
  - 建议：
    - 日常翻译关闭，遇到漏检/识别错误/排版异常时临时开启
    - 排查完成后清理旧日志，避免 `result/` 目录持续膨胀

- **使用 GPU (use_gpu)**：启用 GPU 加速

- **禁用 ONNX GPU 加速 (disable_onnx_gpu)**：禁用 ONNX Runtime 的 GPU 加速，强制使用 `CPUExecutionProvider`
  - 默认：关闭（`false`）
  - 适用场景：GPU 模式下 ONNX Runtime 出现兼容性、驱动或 provider 初始化问题时
  - 说明：该选项只影响 ONNX Runtime 路径，不会关闭整个程序的 GPU 开关
  - 建议：遇到 ONNX 模型在 GPU 上报错、闪退或启动失败时优先尝试开启

- **重试次数 (attempts)**：错误重试次数（-1 = 无限重试）

- **忽略错误 (ignore_errors)**：忽略错误继续处理

- **上下文页数 (context_size)**：翻译上下文页面数（用于多页联合翻译）

- **输出格式 (format)**：输出图片格式
  - PNG、JPEG、WEBP、不指定（保持原格式）

- **覆盖已存在文件 (overwrite)**：覆盖已存在的翻译文件

- **跳过无文本图像 (skip_no_text)**：跳过没有检测到文本的图片

- **图片可编辑 (save_text)**：保存翻译结果到 JSON 文件（用于后续编辑）
  - 输出路径：`原图目录/manga_translator_work/json/`
  - 开启后，程序会额外保存修复图到 `原图目录/manga_translator_work/inpainted/`
  - `导入翻译并渲染 (load_text)` 模式下，只有实际重新执行了修复时才会生成新的修复图

- **导入翻译 (load_text)**：从 JSON 文件加载翻译结果并直接渲染
  - 需要提前存在对应的 JSON 数据
  - 此模式固定基于原图处理，不走上色和超分
  - 若 JSON 已保存蒙版，则直接复用
  - 若 JSON 缺少蒙版，且开启了 `导入固定YOLO框 (import_yolo_labels)`，会基于原图补跑一次检测来生成蒙版
  - 已有旧修复图时，不会再参与检测输入；只会在可复用时作为渲染底图使用

- **仅翻译（JSON）(translate_json_only)**：从已有 JSON 提取原文并执行翻译，然后回写 JSON
  - 需要提前存在对应的 JSON 数据
  - 不执行检测、OCR、修复和渲染
  - 适合先导出原文、后续只更新译文内容的流程
  - 成功回写 JSON 后，会删除对应的 `_original.txt`

- **导出原文 (template)**：导出原文到文本文件（用于手动翻译）

- **图像保存质量 (save_quality)**：JPEG 保存质量（0-100）

- **批量大小 (batch_size)**：批量处理大小
  - 默认：1
  - 对于高质量翻译器（OpenAI HQ/Gemini HQ/Vertex HQ），此参数控制一次发送的图片数量
  - 越大翻译速度越快，但消耗的 tokens 越多
  - 建议范围：1-10

- **批量并发处理 (batch_concurrent)**：启用批量并发处理

- **导出可编辑 PSD (export_editable_psd)**：导出分图层的 PSD 文件
  - 需要安装 Photoshop
  - 导出包含：原图层、修复图层、可编辑文本层
  - 原图层优先使用 `manga_translator_work/editor_base/` 中的上色/超分底图
  - 修复图层优先使用当前会话修复图，回退 `manga_translator_work/inpainted/`
  - 导出路径：`原图目录/manga_translator_work/psd/`
- **PSD 默认字体 (psd_font)**：在 Photoshop 中显示的文本图层字体
  - 支持字体显示名称或 PostScript 名称
  - 留空时使用 Photoshop 默认字体
- **仅生成 PSD 脚本 (psd_script_only)**：只生成 `.jsx` 脚本，不自动运行 Photoshop
  - 不会直接生成 PSD 文件
  - 脚本保存路径：`原图目录/manga_translator_work/psd/`
  - 适合手动检查脚本或自行执行导出

- **翻译完成后卸载模型 (unload_models_after_translation)**：翻译完成后卸载所有模型以释放内存
  - 默认：关闭
  - 作用：更彻底地释放显存和内存，适合显存不足的场景
  - 缺点：下次翻译需要重新加载模型

- **生成并导出 (generate_and_export)**：生成并导出翻译结果

- **仅上色 (colorize_only)**：仅执行上色操作，不翻译

- **仅超分 (upscale_only)**：仅执行超分辨率操作，不翻译

- **输出到原图目录 (save_to_source_dir)**：将翻译结果输出到原图片所在目录
  - 启用后，输出路径为 `原图目录/manga_translator_work/result/`
  - 方便管理和查找翻译后的图片
  - 适合批量处理时保持文件组织结构

- **替换翻译 (replace_translation)**：从已翻译图片提取翻译数据并应用到生肉图片
  - 自动匹配生肉图和翻译图的文本区域
  - 支持多对一匹配（多个生肉框对应一个翻译框）
  - 自动保存优化后的蒙版，Qt 编辑器加载时跳过蒙版优化
  - 支持导出可编辑 PSD 文件
  - 不支持并行处理（自动使用顺序处理）
  - 使用方法：
    1. 准备生肉图和对应的翻译图（文件名需对应）
    2. 将翻译图放在 `原图目录/manga_translator_work/translated_images/` 目录下
    3. 勾选"替换翻译"选项
    4. 添加生肉图片
    5. 程序自动从翻译图提取 OCR 结果并应用

---

## 高级设置

### 检测器设置

- **文本检测器 (detector)**：文本检测算法
  - **default**：默认检测器（DBNet + ResNet34）
  - **ctd**：Comic 文本检测器
  - **craft**：CRAFT 检测器

- **检测大小 (detection_size)**：检测时的图像缩放尺寸（默认 2048，越大越准确但越慢）

- **文本阈值 (text_threshold)**：文本检测置信度阈值（0-1，越高越严格）

- **边界框生成阈值 (box_threshold)**：文本框生成的置信度阈值（值越低检测到的文本框越多）

- **Unclip比例 (unclip_ratio)**：Unclip 比例（控制文本框扩展程度）

- **最小检测框面积占比 (min_box_area_ratio)**：最小检测框面积占比，相对于图片总像素（默认 0.0009 = 0.09%）
  - 过滤掉面积过小的检测框
  - 值越大，过滤越严格，小文本框会被移除
  - 值越小，保留更多小文本框
  - 建议范围：0.0005-0.002（0.05%-0.2%）

- **导入固定YOLO框 (import_yolo_labels)**：从固定目录导入 YOLO 标注框
  - 固定路径：`原图目录/manga_translator_work/yolo_labels/图片同名.txt`
  - 忽略类别标签，文件中的框会全部导入
  - 正常翻译流程：保留检测器生成的蒙版，后续 OCR 和文本框合并改用导入框
  - 导出原文 / 生成并导出：直接使用导入框，不保存蒙版到 JSON
  - 导入翻译并渲染：不会覆盖 JSON 里的文本框；只在 JSON 缺少蒙版时用于补生成蒙版

- **启用YOLO辅助检测 (use_yolo_obb)**：使用 YOLO 有向边界框辅助检测（提高检测准确率）

- **YOLO置信度阈值 (yolo_obb_conf)**：YOLO 辅助检测的置信度阈值（值越高越严格）

- **YOLO交叉比(IoU) (yolo_obb_iou)**：YOLO IOU 阈值（控制框重叠度判断）

- **YOLO辅助检测重叠率删除阈值 (yolo_obb_overlap_threshold)**：YOLO 框重叠阈值（去除重叠的检测框）

### 修复器设置

- **修复模型 (inpainter)**：图像修复算法
  - **lama_large**：大型 LaMa 修复模型（推荐，效果最好）
  - **lama_mpe**：LaMa MPE 修复模型（速度快）
  - **default**：AOT 修复器（默认）

- **修复大小 (inpainting_size)**：修复处理时的图像尺寸（越大效果越好但越慢）

- **修复精度 (inpainting_precision)**：精度设置
  - **fp32**：单精度（最准确，最慢）
  - **fp16**：半精度（平衡）
  - **bf16**：BFloat16（推荐）

- **强制使用PyTorch修复 (force_use_torch_inpainting)**：强制使用 PyTorch 进行图像修复
  - 默认情况下，CPU 模式会优先使用 ONNX 引擎（速度更快）
  - 勾选此选项后，强制使用 PyTorch 引擎进行修复
  - 适用场景：ONNX 引擎出现问题或需要更高精度时
  - GPU 模式下此选项无效（始终使用 PyTorch）

### 渲染器设置

- **渲染器 (renderer)**：渲染引擎
  - **default**：默认渲染器
  - **manga2eng_pillow**：Manga2Eng Pillow 渲染器
  - **openai_renderer**：调用 OpenAI 图像接口整页渲染，使用带编号框的清图 + 组合提示词
  - **gemini_renderer**：调用 Gemini 图像接口整页渲染，使用带编号框的清图 + 组合提示词

- **AI 渲染提示词**：OpenAI Renderer / Gemini Renderer 使用的固定提示词文件
  - Qt 界面中点击"编辑"即可修改
  - 固定路径：`dict/ai_renderer_prompt.yaml`
  - 注意：在 CLI 模式下如果找不到此文件，请先启动一次程序，会自动根据内置模板生成该文件。
  - 文件格式：YAML，主键为 `ai_renderer_prompt`
  - 实际请求会自动组合：带编号框的清图 + 对应编号的翻译文本
  - 拟声词 / 音效也会按翻译结果一起发给 AI 渲染

- **AI 渲染并发数 (ai_renderer_concurrency)**：OpenAI Renderer / Gemini Renderer 的最大并发请求数
  - 批量模式下可限制同时发出的整页渲染请求数量
  - 仅 Qt 桌面端显示，服务端网页配置页不显示

- **排版模式 (layout_mode)**：文本排版模式
  - **smart_scaling**：智能缩放（自动调整字体大小）
  - **strict**：严格边界（缩小字体以适应文本框）
  - **balloon_fill**：智能气泡（推荐，自动检测气泡并填充）

- **对齐方式 (alignment)**：文本对齐方式
  - **auto**：自动对齐
  - **left**：左对齐
  - **center**：居中对齐（水平和垂直方向均居中）
  - **right**：右对齐

- **文本方向 (direction)**：文字方向
  - **auto**：自动检测
  - **horizontal**：水平排列
  - **vertical**：垂直排列

- **字体路径 (font_path)**：字体文件路径（选择自定义字体）
  - 可以在 `fonts` 目录下添加新的字体文件（`.ttf`、`.otf`、`.ttc` 格式）
  - 程序会在每次打开下拉菜单时自动扫描 `fonts` 目录
  - 添加新字体后，直接点击下拉菜单即可看到新字体，无需重启

- **禁用字体边框 (disable_font_border)**：禁用字体边框（去除描边效果）

- **描边宽度比例 (stroke_width)**：字体描边（边框）宽度，相对于字体大小的比例
  - 默认：0.07（7%）
  - 范围：0.0-1.0
  - 设为 0 可完全禁用描边效果（等同于勾选"禁用字体边框"）
  - 建议范围：0.05-0.15（5%-15%）
  - 越大描边越粗，越小描边越细

- **AI断句 (disable_auto_wrap)**：禁用自动换行（启用 AI 断句时会自动禁用自动换行）
  - 勾选此选项将禁用自动换行功能
  - 常与 AI 断句功能配合使用

- **字体大小偏移量 (font_size_offset)**：字体大小偏移量（调整字体大小，正数增大，负数减小）

- **最小字体大小 (font_size_minimum)**：最小字体大小（限制字体最小尺寸）

- **最大字体大小 (max_font_size)**：最大字体大小（限制字体最大尺寸）

- **大写 (uppercase)**：转换为大写字母

- **小写 (lowercase)**：转换为小写字母

- **禁用连字符 (no_hyphenation)**：禁用连字符换行

- **根据气泡排版 (bubble_layout_english)**：开启后对所有语言使用气泡形状排版并强制横排
  - 默认：关闭（`false`）
  - 英文默认已启用此排版方式，此开关主要影响韩文、中文等其他语言
  - 开启后：程序会从原图提取气泡蒙版来决定断行位置，并强制横排渲染，无论原始文本方向如何
  - 适用场景：将漫画翻译为韩文或其他语言时，希望文本跟随气泡形状横排排版，类似英文的排版效果

- **字体颜色 (font_color)**：字体颜色（十六进制颜色代码，如 #FFFFFF）

- **行间距 (line_spacing)**：行间距倍率（调节行与行之间的空隙），默认 1.0，范围 0.1-5.0

- **字间距 (letter_spacing)**：字间距倍率（调节字符推进距离），默认 1.0，范围 0.1-5.0
  - `1.0` 与旧版默认渲染行为一致
  - 同时作用于排版、文本框尺寸计算和最终渲染
  - 支持全局设置，也支持在编辑器中按区域单独覆盖

- **字体大小 (font_size)**：固定字体大小（覆盖自动计算）

- **自动旋转符号 (auto_rotate_symbols)**：自动旋转符号（如 ！？等）

- **竖排内横排 (auto_rotate_symbols)**：竖排文本中的横排处理（自动识别竖排文本中的横排符号并正确显示）

- **RTL（从右到左） (rtl)**：启用从右到左排版

- **字体缩放比例 (font_scale_ratio)**：字体缩放比例（整体缩放字体）

- **气泡内居中 (center_text_in_bubble)**：文本块在气泡框内居中显示

- **AI断句自动扩大文字 (optimize_line_breaks)**：启用 AI 断句优化（自动调整字体大小以减少断行）
  - 需要配合 OpenAI/Gemini 翻译器使用
  - AI 会自动优化文本断行，提升文本可读性

- **AI断句检查 (check_br_and_retry)**：检查 AI 断句结果并重试（确保断句质量）
  - 自动检查断句结果，如不符合要求则重试

- **AI断句自动扩大文字下不扩大文本框 (strict_smart_scaling)**：严格智能缩放模式（AI 断句时不扩大文本框，只缩小字体）
  - 保持文本框原始大小，通过缩小字体来适应

- **启用模板匹配对齐 (enable_template_alignment)**：启用直接粘贴模式（仅替换翻译模式）
  - 默认：关闭
  - 功能：根据坐标匹配，直接从翻译图裁剪区域并粘贴到生肉图
  - 使用场景：想保留翻译图的原始字体、样式、符号、音效等
  - 注意：仅在"替换翻译"模式下生效

- **粘贴模式连通距离比例 (paste_connect_distance_ratio)**：连接附近蒙版区域的距离比例（相对于图像长边），默认 0.03（3%）

- **粘贴模式蒙版膨胀大小 (paste_mask_dilation_pixels)**：粘贴前扩大蒙版区域的像素数，默认 10 像素，设为 0 禁用膨胀

### 超分辨率设置

- **超分模型 (upscaler)**：超分辨率模型
  - **waifu2x**：Waifu2x 超分模型（默认）
  - **realcugan**：Real-CUGAN 超分模型（推荐，效果更好）
  - **mangajanai**：MangaJaNai 超分模型（⭐ 效果最好，但最吃配置）
    - 自动检测彩色/黑白图片，选择对应模型
    - 彩色图片使用 IllustrationJaNai 模型
    - 黑白图片使用 MangaJaNai 模型（根据分辨率自动选择最佳模型）
  - 其他超分模型

- **超分倍数 (upscale_ratio)**：超分辨率放大倍数
  - **不使用**：不进行超分（默认）
  - **2**、**3**、**4**：放大 2/3/4 倍

- **Real-CUGAN 模型 (realcugan_model)**：Real-CUGAN 模型选择（仅在超分模型选择 realcugan 时生效）
  - **2倍系列**：2x-保守、2x-保守-Pro、2x-无降噪、2x-降噪1x/2x/3x、2x-降噪3x-Pro
  - **3倍系列**：3x-保守、3x-保守-Pro、3x-无降噪、3x-无降噪-Pro、3x-降噪3x、3x-降噪3x-Pro
  - **4倍系列**：4x-保守、4x-无降噪、4x-降噪3x
  - **Pro 版本**：效果更好，但速度稍慢
  - **降噪强度**：数字越大降噪越强，适合有噪点的图片

- **分块大小 (tile_size)**：分块处理大小（0 = 不分割）
  - 默认：0（不分割）
  - 建议范围：200-800
  - 作用：将大图分割成小块处理，降低显存占用
  - 越小越省显存，但速度越慢

- **还原超分 (revert_upscaling)**：翻译后恢复原始分辨率（避免图片变大）

### 上色器设置

- **上色模型 (colorizer)**：上色器类型
  - **none**：不上色（默认）
  - **openai_colorizer**：调用 OpenAI 兼容 / 硅基流动 / 百炼原生图像接口做整页上色，会按 `API Base URL` 自动切换请求格式
  - **gemini_colorizer**：调用 Gemini 图像接口做整页上色
  - 其他上色模型

- **上色大小 (colorization_size)**：上色处理尺寸（越大效果越好但越慢）

- **降噪强度 (denoise_sigma)**：去噪强度（控制降噪程度）

- **AI 上色提示词**：OpenAI Colorizer / Gemini Colorizer 使用的固定提示词文件
  - Qt 界面中点击"编辑"即可修改
  - 固定路径：`dict/ai_colorizer_prompt.yaml`
  - 注意：在 CLI 模式下如果找不到此文件，请先启动一次程序，会自动根据内置模板生成该文件。
  - 文件格式：YAML，主键为 `ai_colorizer_prompt`

- **AI 上色并发数 (ai_colorizer_concurrency)**：OpenAI Colorizer / Gemini Colorizer 的最大并发请求数
  - 批量模式下可限制同时发出的上色请求数量
  - 仅 Qt 桌面端显示，服务端网页配置页不显示
  - AI 上色多图提示词会自动按图号说明角色：`Image 1` 是当前待上色页，后续图片会分别标注为参考图或历史已上色页

---

## 选项

### OCR 设置

- **OCR模型 (ocr)**：OCR 识别模型
  - **32px**：旧版轻量模型，可作兼容性备选
  - **48px**：默认模型（推荐，平衡速度和准确率）
  - **48px_ctc**：CTC 变体模型（可作为备选对比，不代表一定更精确）
  - **mocr**：Manga OCR 专用模型（专门针对漫画优化，日漫常用）
  - **paddleocr**：通用多语言模型
  - **paddleocr_korean**：韩文 / 韩漫推荐
  - **paddleocr_latin**：拉丁字母文本推荐，英文建议优先使用
  - **paddleocr_thai**：泰文推荐
  - **paddleocr_vl**：PaddleOCR-VL-1.5 通用模型（效果最好，最吃配置），建议配合下方语言提示或自定义提示词使用
  - **openai_ocr**：调用 OpenAI 兼容多模态接口逐框 OCR，文字颜色仍由本地 `48px` 模型提取
  - **gemini_ocr**：调用 Gemini 多模态接口逐框 OCR，文字颜色仍由本地 `48px` 模型提取
  - **AI OCR 提醒**：通常效果最好，但与本地 OCR 的差距往往不大；由于是按文本框逐次请求，十分消耗请求次数，如果是按次收费的站点不建议使用
  - **推荐**：日漫推荐 `48px` 或 `mocr`，韩漫推荐 `paddleocr_korean`，英文推荐 `paddleocr_latin`，泰文推荐 `paddleocr_thai`

- **AI OCR 提示词**：OpenAI OCR / Gemini OCR 使用的固定提示词文件
  - Qt 界面中点击"编辑"即可修改
  - 固定路径：`dict/ai_ocr_prompt.yaml`
  - 注意：在 CLI 模式下如果找不到此文件，请先启动一次程序，会自动根据内置模板生成该文件。
  - 文件格式：YAML，主键为 `ai_ocr_prompt`
  - 不再通过配置项切换文件，也没有另存为
  - 如果本地还保留旧版 `dict/ai_ocr_prompt.json`，程序首次使用时会自动迁移到 YAML

- **AI OCR 环境变量**：API OCR 优先读取独立的 OCR 接口配置
  - OpenAI OCR：`OCR_OPENAI_API_KEY`、`OCR_OPENAI_MODEL`、`OCR_OPENAI_API_BASE`
  - Gemini OCR：`OCR_GEMINI_API_KEY`、`OCR_GEMINI_MODEL`、`OCR_GEMINI_API_BASE`
  - 若未填写 OCR 专用变量，会自动回退到普通翻译接口使用的 `OPENAI_*` 或 `GEMINI_*`
  - Google Cloud / Vertex 相关 API Key 也直接填写到 `OCR_GEMINI_*` 或普通 `GEMINI_*`
  - `OCR_GEMINI_API_BASE` / `GEMINI_API_BASE` 保持默认官方地址 `https://generativelanguage.googleapis.com` 即可，无需修改

- **AI 上色环境变量**：API 上色优先读取独立的上色接口配置
  - OpenAI Colorizer：`COLOR_OPENAI_API_KEY`、`COLOR_OPENAI_MODEL`、`COLOR_OPENAI_API_BASE`
  - Gemini Colorizer：`COLOR_GEMINI_API_KEY`、`COLOR_GEMINI_MODEL`、`COLOR_GEMINI_API_BASE`
  - 若未填写上色专用变量，会自动回退到普通 `OPENAI_*` 或 `GEMINI_*`
  - Google Cloud / Vertex 相关 API Key 也直接填写到 `COLOR_GEMINI_*` 或普通 `GEMINI_*`
  - `COLOR_GEMINI_API_BASE` / `GEMINI_API_BASE` 保持默认官方地址 `https://generativelanguage.googleapis.com` 即可，无需修改
  - `COLOR_OPENAI_API_BASE` 命中不同后端时会自动切换请求格式：
    - 硅基流动 `https://api.siliconflow.cn/v1`
    - 百炼原生 `https://dashscope.aliyuncs.com/api/v1` / `https://dashscope-intl.aliyuncs.com/api/v1`
    - 火山引擎 / 其他 OpenAI 兼容接口
  - 若启用 `use_custom_api_params`，`colorizer` 分组参数会自动映射到对应后端请求体

- **AI 渲染环境变量**：API 渲染优先读取独立的渲染接口配置
  - OpenAI Renderer：`RENDER_OPENAI_API_KEY`、`RENDER_OPENAI_MODEL`、`RENDER_OPENAI_API_BASE`
  - Gemini Renderer：`RENDER_GEMINI_API_KEY`、`RENDER_GEMINI_MODEL`、`RENDER_GEMINI_API_BASE`
  - 若未填写渲染专用变量，会自动回退到普通 `OPENAI_*` 或 `GEMINI_*`
  - Google Cloud / Vertex 相关 API Key 也直接填写到 `RENDER_GEMINI_*` 或普通 `GEMINI_*`
  - `RENDER_GEMINI_API_BASE` / `GEMINI_API_BASE` 保持默认官方地址 `https://generativelanguage.googleapis.com` 即可，无需修改
  - `RENDER_OPENAI_API_BASE` 命中不同后端时也会自动切换请求格式，规则与 OpenAI Colorizer 一致
  - 若启用 `use_custom_api_params`，`render` 分组参数会自动映射到对应后端请求体

- **启用混合OCR (use_hybrid_ocr)**：启用混合 OCR（同时使用两个模型，提高准确率）
  - **日漫推荐组合**：`48px + mocr`

- **备用OCR (secondary_ocr)**：第二个 OCR 模型（混合 OCR 时使用）
  - **日漫推荐**：主 OCR 为 `48px` 时，备用 OCR 设为 `mocr`

- **最小文本长度 (min_text_length)**：最小文本长度（过滤掉长度小于此值的文本）

- **忽略非气泡文本 (ignore_bubble)**：智能过滤非对话框区域的文本
  - **功能**：自动识别并跳过非气泡区域的文字（如标题、音效、背景文字等）
  - **支持的 OCR 模型**：所有模型（48px、48px_ctc、32px、manga_ocr、paddleocr）
  - **参数范围**：0-1（0 表示禁用）
  - **阈值效果**：
    - **0**：禁用，保留所有文本
    - **0.01-0.3**：宽松过滤，只过滤明显的非气泡区域
    - **0.3-0.7**：中等过滤，平衡准确率
    - **0.7-1.0**：严格过滤，可能误过滤正常气泡
  - **工作原理**：
    - 计算文本框边缘 2 像素区域的黑白像素比例
    - 正常白色气泡：边缘几乎全白 → 保留翻译
    - 正常黑色气泡：边缘几乎全黑 → 保留翻译
    - 非气泡区域：边缘黑白混杂 → 跳过
    - 彩色文字：检测到彩色 → 跳过
  - **使用场景**：
    - 漫画中有大量音效、标题等不需要翻译的文字
    - 想要只翻译对话框内的文字
    - 减少不必要的翻译，提高效率
  - **日志输出**：过滤时会显示 `[FILTERED] Region X ignored - Non-bubble area detected`

- **膨胀不超过气泡蒙版 (limit_mask_dilation_to_bubble_mask)**：限制蒙版膨胀范围不超出气泡区域
  - 默认：`false`
  - 启用后：在蒙版优化后处理阶段，使用模型气泡区域约束最终修复蒙版，避免修复范围溢出到气泡外
  - 适用场景：防止气泡边框在修复时被误擦除，减少对白框外背景被误修复

- **文本区域最低概率 (prob)**：OCR 识别概率阈值（低于此值的文本会被过滤）

- **合并-距离容忍度 (merge_gamma)**：合并时的距离容忍度（控制文本区域合并的距离阈值）

- **合并-离群容忍度 (merge_sigma)**：合并时的离群容忍度（控制离群文本的合并程度）

- **合并-边缘比率阈值 (merge_edge_ratio_threshold)**：边缘比率阈值（控制边缘文本的合并条件）

- **模型辅助合并 (merge_special_require_full_wrap)**：控制是否启用模型标签辅助预合并流程
  - **开启（默认）**：先执行模型辅助预合并（`changfangtiao` 独立组、`balloon/qipao/other` 组），且无标签框必须被目标标签框完全包裹才参与预合并；预合并后的框不再参与后续原始合并
  - **关闭**：不执行模型辅助预合并，全部文本框直接走原始合并算法

### 全局参数

- **卷积核大小 (kernel_size)**：文本擦除卷积核大小（默认 3，控制文本擦除的范围）

- **遮罩扩张偏移 (mask_dilation_offset)**：蒙版膨胀偏移量（默认 70，控制文本擦除区域的扩展程度）

### 过滤列表

程序支持通过过滤列表跳过特定文本区域（如水印、广告等）。

- **文件位置**：`examples/filter_list.json`
- **格式**：JSON 对象，包含 `contains` 和 `exact` 两个数组
- **工作原理**：OCR 识别的原文包含过滤词时，该文本区域会被完全跳过（不翻译、不擦除、不渲染）
- **自动创建**：程序启动时会自动创建该文件（如果不存在），旧版 `filter_list.txt` 会自动迁移

**示例**：
```json
{
  "contains": [
    "pixiv",
    "twitter",
    "@username",
    "广告",
    "宣传"
  ],
  "exact": []
}
```

---

## 📂 路径配置说明

### 相对路径基准

- **打包版本**：相对于 `_internal` 目录
- **开发版本**：相对于项目根目录

### 常用路径

**自定义提示词路径**（`dict` 目录）：
- **系统提示词**（程序内置，自动调用，支持 `.yaml`/`.yml`/`.json`，优先 YAML）：
  - `dict/system_prompt_hq.yaml` - 高质量翻译的系统提示词
  - `dict/system_prompt_line_break.yaml` - AI断句的系统提示词
  - `dict/glossary_extraction_prompt.yaml` - 术语提取的系统提示词
  - `dict/system_prompt_hq_format.yaml` - 高质量翻译输出格式的系统提示词
  - `dict/ai_ocr_prompt.yaml` - OpenAI OCR / Gemini OCR / Vertex OCR 使用的固定 OCR 提示词
  - `dict/ai_colorizer_prompt.yaml` - OpenAI Colorizer / Gemini Colorizer / Vertex Colorizer 使用的固定上色提示词
  - `dict/ai_renderer_prompt.yaml` - OpenAI Renderer / Gemini Renderer / Vertex Renderer 使用的固定渲染提示词
- **用户自定义提示词**（在界面中选择）：
  - `dict/prompt_example.yaml` - 提示词示例
  - 可以在此目录添加自己的 `.yaml` 或 `.json` 提示词文件
- 适用于：OpenAI、Gemini、Vertex、高质量翻译 OpenAI、高质量翻译 Gemini、高质量翻译 Vertex 六个翻译器
- 可以自定义翻译风格、术语表、上下文说明等

**如何添加自定义提示词**：

> 💡 **说明**：此提示词适用于以下 6 个翻译器：**OpenAI**、**Gemini**、**Vertex**、**高质量翻译 OpenAI**、**高质量翻译 Gemini**、**高质量翻译 Vertex**。

> ⚠️ **重要**：使用脚本版安装的用户，**不要直接修改** `prompt_example.yaml`，更新时会被覆盖！请新建文件。

1. 点击"自定义提示词"旁边的"打开目录"按钮，打开 `dict` 目录
2. 在该目录下新建一个 `.yaml` 或 `.json` 文件（如 `my_prompt.yaml`）
3. 打开 `prompt_example.yaml`，复制里面的内容到新文件中
4. 编辑新文件，填入你的作品角色名、术语表等信息
5. 回到界面，在"自定义提示词"下拉菜单选择新创建的提示词文件

**提示词示例**（YAML 格式，也支持 JSON）：

```yaml
# 自定义系统提示词（留空则仅使用内置的基础提示词，此处内容会叠加在基础提示词之前）
# 可以使用 {{{target_lang}}} 占位符，会被替换为目标语言名称
system_prompt: |
  你是一名精通多国语言的专业漫画翻译家。你的任务是将漫画中的文本翻译成自然、流畅的目标语言。

  规则：
  1. 保持原文的语气、风格和情感。
  2. 严格参考以下术语表进行翻译。
  3. 如果没有特定译法，请采用最通用的翻译。

# 术语表（确保角色名、地名等翻译一致）
glossary:
  Person:
    - original: "ましろ"
      translation: "真白"
  Location: []
  Org: []
  Item: []
  Skill: []
  Creature: []
```

**字段说明**：
- `system_prompt`：系统提示词，定义翻译风格和规则
- `glossary`：术语表，包含各类专有名词
  - `Person`：人名
  - `Location`：地名
  - `Org`：组织名
  - `Item`：物品名
  - `Skill`：技能名
  - `Creature`：生物名
- 每个术语包含 `original`（原文）和 `translation`（译文）两个字段

> 💡 **懒人方法**：如果觉得手写麻烦，可以把以下内容发给 AI 帮你生成：
> - 作品原名和翻译后的名字
> - 角色的原文名和翻译后的名字
> - `prompt_example.yaml` 的内容作为参考格式

**导出原文模版路径**：
- 默认：`examples/translation_template.json`
- 注意：在 CLI 模式下如果找不到此文件，请先启动一次程序，会自动根据内置模板生成该文件。
- 用于自定义导出原文的格式
- 定义一组文本框的结构，程序会自动重复应用

**过滤列表路径**：
- 默认：`examples/filter_list.json`
- 注意：在 CLI 模式下如果找不到此文件，请先启动一次程序，会自动根据内置模板生成该文件。
- 旧版兼容：`examples/filter_list.txt`（启动时会自动迁移到 JSON）
- 用于跳过水印、广告等不需要翻译的文本

**文本替换规则路径**：
- 默认：`examples/text_replacements.yaml`
- 注意：在 CLI 模式下如果找不到此文件，请先启动一次程序，会自动根据内置模板生成该文件。
- 用于翻译后、渲染前对译文执行自定义替换
- 支持三个分组：`common`（通用）、`horizontal`（横排）、`vertical`（竖排）
- **执行顺序**：首先执行 `common` 组，然后根据排版方向执行 `horizontal` 或 `vertical` 组。
- **级联机制**：规则由上到下逐条执行；上一条规则替换出的新文字，若匹配后续规则，会被再次替换。
- 每条规则格式：
  ```yaml
  - pattern: "匹配内容"
    replace: "替换内容"
    regex: true          # 可选，默认 false
    enabled: true        # 可选，默认 true
    comment: "备注"      # 可选
  ```
- 替换结果会写入 JSON，编辑器导出时自动跳过
- 可在 Qt 界面"数据管理 → 替换规则"中可视化编辑

**自定义 API 参数路径**：
- 默认：`examples/custom_api_params.json`
- 注意：在 CLI 模式下如果找不到此文件，请先启动一次程序，会自动根据内置模板生成该文件。
- 用于翻译、AI OCR、AI 渲染、AI 上色的额外 API 参数
- 推荐分组键：`translator`、`ocr`、`render`、`colorizer`
- 可选共享键：`common`
- 旧版兼容：直接写在 JSON 顶层的键会按 `common` 处理

**字体路径**：
- 默认：`fonts` 目录
- 可以指定具体字体文件路径（如 `fonts/my_font.ttf`）
- 支持 `.ttf` 和 `.otf` 格式

**如何添加自定义字体**：
1. 将字体文件（`.ttf` 或 `.otf`）复制到 `fonts` 目录
2. 字体文件名建议使用英文（如 `myfont.ttf`）
3. 重启程序
4. 在"渲染器设置"中的"字体路径"下拉菜单选择新字体
5. 或直接在"字体路径"输入框填写字体文件路径（如 `fonts/myfont.ttf`）

**输出文件夹**：
- 默认：与输入文件相同目录
- 可以在界面中自定义输出路径
