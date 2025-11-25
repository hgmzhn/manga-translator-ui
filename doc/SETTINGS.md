# 设置说明

本文档详细介绍程序的所有设置选项和参数。

---

## 📖 参数详解

界面分为三个标签页，每个标签页包含不同的设置选项：

---

## 基础设置

### 翻译器设置

- **翻译器 (translator)**：选择翻译引擎
  - 离线翻译器：Sugoi、NLLB、M2M100 等
  - 在线翻译器：Google Gemini、OpenAI、DeepL、百度翻译等
  - 高质量翻译器：高质量翻译 OpenAI、高质量翻译 Gemini（推荐）

- **目标语言 (target_lang)**：翻译的目标语言
  - 简体中文、繁体中文、英语、日语、韩语等

- **不跳过目标语言文本 (no_text_lang_skip)**：不跳过已是目标语言的文本（强制翻译）

- **GPT配置文件路径 (gpt_config)**：GPT 配置文件路径（用于 OpenAI/Gemini 翻译器）
  - 默认：`examples/gpt_config-example.yaml`

- **高质量翻译提示词 (high_quality_prompt_path)**：高质量翻译提示词文件路径（用于高质量翻译模式）
  - 默认：`dict/prompt_example.json`
  - 可以在 `dict` 目录下创建新的 `.json` 文件
  - JSON 格式只需符合标准 JSON 规范即可加载
  - 程序会在每次打开下拉菜单时自动扫描 `dict` 目录
  - 添加新提示词文件后，直接点击下拉菜单即可看到新文件，无需重启

- **最大请求速率 (max_requests_per_minute)**：每分钟最大请求数（0 = 不限制）

### CLI 选项

- **详细日志 (verbose)**：输出详细的调试信息

- **使用 GPU (use_gpu)**：启用 GPU 加速

- **使用受限 GPU (use_gpu_limited)**：使用受限 GPU 模式（降低显存占用）

- **重试次数 (attempts)**：错误重试次数（-1 = 无限重试）

- **忽略错误 (ignore_errors)**：忽略错误继续处理

- **上下文页数 (context_size)**：翻译上下文页面数（用于多页联合翻译）

- **输出格式 (format)**：输出图片格式
  - PNG、JPEG、WEBP、不指定（保持原格式）

- **覆盖已存在文件 (overwrite)**：覆盖已存在的翻译文件

- **跳过无文本图像 (skip_no_text)**：跳过没有检测到文本的图片

- **图片可编辑 (save_text)**：保存翻译结果到 JSON 文件（用于后续编辑）

- **导入翻译 (load_text)**：从 JSON 文件加载翻译结果

- **导出原文 (template)**：导出原文到文本文件（用于手动翻译）

- **图像保存质量 (save_quality)**：JPEG 保存质量（0-100）

- **批量大小 (batch_size)**：批量处理大小
  - 默认：1
  - 对于高质量翻译器（OpenAI HQ/Gemini HQ），此参数控制一次发送的图片数量
  - 越大翻译速度越快，但消耗的 tokens 越多
  - 建议范围：1-10

- **批量并发处理 (batch_concurrent)**：启用批量并发处理

- **生成并导出 (generate_and_export)**：生成并导出翻译结果

- **仅上色 (colorize_only)**：仅执行上色操作，不翻译

- **仅超分 (upscale_only)**：仅执行超分辨率操作，不翻译

---

## 高级设置

### 检测器设置

- **文本检测器 (detector)**：文本检测算法
  - **default**：默认检测器（DBNet + ResNet34）
  - **ctd**：Comic 文本检测器
  - **craft**：CRAFT 检测器

- **检测大小 (detection_size)**：检测时的图像缩放尺寸（默认 2048，越大越准确但越慢）

- **文本阈值 (text_threshold)**：文本检测置信度阈值（0-1，越高越严格）

- **旋转图像进行检测 (det_rotate)**：旋转图像进行检测（提高检测率）

- **旋转图像以优先检测垂直文本行 (det_auto_rotate)**：自动旋转优先检测垂直文本（适合竖排文字）

- **反转图像颜色进行检测 (det_invert)**：反转图像颜色进行检测（适合白底黑字）

- **应用伽马校正进行检测 (det_gamma_correct)**：应用伽马校正进行检测（提高低对比度图像检测率）

- **边界框生成阈值 (box_threshold)**：文本框生成的置信度阈值（值越低检测到的文本框越多）

- **Unclip比例 (unclip_ratio)**：Unclip 比例（控制文本框扩展程度）

- **最小检测框面积占比 (min_box_area_ratio)**：最小检测框面积占比，相对于图片总像素（默认 0.0009 = 0.09%）
  - 过滤掉面积过小的检测框
  - 值越大，过滤越严格，小文本框会被移除
  - 值越小，保留更多小文本框
  - 建议范围：0.0005-0.002（0.05%-0.2%）

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

### 渲染器设置

- **渲染器 (renderer)**：渲染引擎
  - **default**：默认渲染器
  - **manga2eng_pillow**：Manga2Eng Pillow 渲染器

- **排版模式 (layout_mode)**：文本排版模式
  - **smart_scaling**：智能缩放（推荐，自动调整字体大小）
  - **strict_boundary**：严格边界（缩小字体以适应文本框）
  - **fixed_font**：固定字体（扩大文本框以适应文字）
  - **balloon_fill**：气泡填充（自动检测气泡并填充）
  - **disabled**：完全禁用（裁剪超出的文本）

- **对齐方式 (alignment)**：文本对齐方式
  - **auto**：自动对齐
  - **left**：左对齐
  - **center**：居中对齐
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

- **字体颜色 (font_color)**：字体颜色（十六进制颜色代码，如 #FFFFFF）

- **行间距 (line_spacing)**：行间距（控制行与行之间的距离）

- **字体大小 (font_size)**：固定字体大小（覆盖自动计算）

- **自动旋转符号 (auto_rotate_symbols)**：自动旋转符号（如 ！？等）

- **竖排内横排 (auto_rotate_symbols)**：竖排文本中的横排处理（自动识别竖排文本中的横排符号并正确显示）

- **RTL（从右到左） (rtl)**：启用从右到左排版

- **字体缩放比例 (font_scale_ratio)**：字体缩放比例（整体缩放字体）

- **AI断句时文本居中 (center_text_in_bubble)**：AI 断句时文本居中（断句模式下居中显示）

- **AI断句自动扩大文字 (optimize_line_breaks)**：启用 AI 断句优化（自动调整字体大小以减少断行）
  - 需要配合 OpenAI/Gemini 翻译器使用
  - AI 会自动优化文本断行，减少不必要的换行

- **AI断句检查 (check_br_and_retry)**：检查 AI 断句结果并重试（确保断句质量）
  - 自动检查断句结果，如不符合要求则重试

- **AI断句自动扩大文字下不扩大文本框 (strict_smart_scaling)**：严格智能缩放模式（AI 断句时不扩大文本框，只缩小字体）
  - 保持文本框原始大小，通过缩小字体来适应

### 超分辨率设置

- **超分模型 (upscaler)**：超分辨率模型
  - **waifu2x**：Waifu2x 超分模型（默认）
  - **realcugan**：Real-CUGAN 超分模型（推荐，效果更好）
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
  - 其他上色模型

- **上色大小 (colorization_size)**：上色处理尺寸（越大效果越好但越慢）

- **降噪强度 (denoise_sigma)**：去噪强度（控制降噪程度）

---

## 选项

### OCR 设置

- **OCR模型 (ocr)**：OCR 识别模型
  - **48px**：默认模型（推荐，平衡速度和准确率）
  - **48px_ctc**：CTC 模型（识别准确率更高）
  - **mocr**：Manga OCR 专用模型（专门针对漫画优化）
  - **paddleocr**：PaddleOCR 引擎（支持多语言）

- **启用混合OCR (use_hybrid_ocr)**：启用混合 OCR（同时使用两个模型，提高准确率）

- **备用OCR (secondary_ocr)**：第二个 OCR 模型（混合 OCR 时使用）

- **最小文本长度 (min_text_length)**：最小文本长度（过滤掉长度小于此值的文本）

- **忽略非气泡文本 (ignore_bubble)**：忽略气泡阈值（忽略非对话框内的文本）

- **文本区域最低概率 (prob)**：OCR 识别概率阈值（低于此值的文本会被过滤）

- **使用MOCR合并 (use_mocr_merge)**：使用 MOCR 合并（合并相邻的文本区域）

- **合并-距离容忍度 (merge_gamma)**：合并时的距离容忍度（控制文本区域合并的距离阈值）

- **合并-离群容忍度 (merge_sigma)**：合并时的离群容忍度（控制离群文本的合并程度）

- **合并-边缘比率阈值 (merge_edge_ratio_threshold)**：边缘比率阈值（控制边缘文本的合并条件）

### 全局参数

- **过滤文本 (filter_text)**：文本过滤正则表达式（使用正则表达式过滤特定文本）

- **卷积核大小 (kernel_size)**：文本擦除卷积核大小（默认 3，控制文本擦除的范围）

- **遮罩扩张偏移 (mask_dilation_offset)**：蒙版膨胀偏移量（默认 70，控制文本擦除区域的扩展程度）

---

## 📂 路径配置说明

### 相对路径基准

- **打包版本**：相对于 `_internal` 目录
- **开发版本**：相对于项目根目录

### 常用路径

**GPT配置文件路径**：
- 默认：`examples/gpt_config-example.yaml`
- 用于配置 OpenAI/Gemini API 密钥和参数
- 示例文件包含 API Key、模型名称、代理设置等

**提示词路径**（`dict` 目录）：
- **系统提示词**（程序内置，自动调用）：
  - `dict/system_prompt_hq.json` - 高质量翻译的系统提示词
  - `dict/system_prompt_line_break.json` - AI断句的系统提示词
- **用户自定义提示词**（在界面中选择）：
  - `dict/prompt_example.json` - 提示词示例
  - `dict/wyxxl.json` - 另一个提示词示例
  - 可以在此目录添加自己的 `.json` 提示词文件
- 用于高质量翻译器（OpenAI HQ/Gemini HQ）
- 可以自定义翻译风格、术语表、上下文说明等

**如何添加高质量提示词**：
1. 在 `dict` 目录下创建新的 `.json` 文件（如 `dict/my_prompt.json`）
2. 参考 `dict/prompt_example.json` 的格式编写提示词
3. 提示词文件应包含翻译风格、术语表、上下文等说明
4. 在界面中"高质量翻译提示词"下拉菜单选择新创建的提示词文件
5. 提示词会在高质量翻译时发送给 AI

**导出原文模版路径**：
- 默认：`examples/translation_template.json`
- 用于自定义导出原文的格式
- 定义一组文本框的结构，程序会自动重复应用

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

