# 漫画图片翻译器 UI

## 项目说明

**本项目由 hgmzhn 基于 [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) 核心翻译引擎开发，为其添加了功能完整的桌面用户界面。**

### 开发说明
- **核心翻译引擎**: 完全使用原项目的 manga-image-translator 引擎，提供了强大的OCR识别、文本检测、图像修复和多种翻译器支持。
- ### 使用说明
这是一个为原翻译引擎提供易用图形界面的项目。关于核心翻译引擎的详细技术说明、算法原理和底层实现，请参考原项目的完整文档：

**[zyddnys/manga-image-translator 官方文档](https://github.com/zyddnys/manga-image-translator)**

原项目文档包含了：
- 详细的算法原理说明
- 模型架构和技术实现
- API接口文档
- 开发指南和贡献说明
- 故障排除和性能优化
- **编辑器功能**: 提供了完整的可视化编辑器，支持文本区域的移动、旋转、形状调整等高级编辑功能。

### 技术特点
- **现代化UI**: 基于CustomTkinter的响应式界面设计
- **可视化编辑**: 完整的图形化文本区域编辑功能  
- **服务架构**: 模块化的服务层设计
- **完整文档**: 详细的功能说明和使用指南

### 可视化编辑器功能
编辑器提供了完整的视觉编辑能力，支持对文本区域进行精确的手动调整和优化：

#### 区域编辑功能
- **移动操作**: 拖动文本区域到任意位置，支持多选移动
- **旋转操作**: 使用旋转手柄进行0-360度精确旋转  
- **形状调整**: 顶点编辑、边编辑、实时变形

#### 绘制和创建功能
- **新建区域**: 矩形绘制、多边形绘制、自由绘制
- **区域操作**: 复制粘贴、删除区域、合并分割

#### 高级编辑功能
- **蒙版编辑**: 画笔工具、橡皮擦、蒙版优化
- **批量处理**: 批量选择、属性同步、模板应用
- **撤销重做**: 完整的操作历史管理系统

## 功能特性

### 核心功能
- **多语言OCR识别**: 支持32px、48px、CTC等多种OCR模型
- **智能文本检测**: 自动检测漫画中的文字区域
- **多翻译引擎**: 支持Google、DeepL、ChatGPT、Sakura、Sugoi等20+翻译器
- **批量处理**: 支持文件和文件夹批量翻译
- **高质量渲染**: 智能文本布局和字体渲染

### 用户界面
- **现代化UI**: 基于CustomTkinter的现代化界面设计
- **标签页设置**: 基础设置、高级设置、选项三个标签页
- **实时日志**: 内置日志显示和控制台输出
- **文件管理**: 拖拽支持、文件列表管理
- **视觉编辑器**: 内置强大的可视化编辑工具

### 编辑功能
- **蒙版编辑**: 支持画笔、橡皮擦等蒙版编辑工具
- **文本区域操作**: 支持移动、旋转、缩放文本区域
- **实时预览**: 翻译结果实时预览
- **撤销重做**: 完整的操作历史管理

## 技术架构

### 主要模块

#### 1. 桌面UI模块 (`desktop-ui/`)
- `app.py`: 主应用程序和控制器
- `main.py`: 应用程序入口点
- `editor_frame.py`: 可视化编辑器框架
- `canvas_frame_new.py`: 画布渲染组件
- `editing_logic.py`: 编辑逻辑处理
- `ui_components.py`: UI组件库

#### 2. 服务层 (`desktop-ui/services/`)
- `translation_service.py`: 翻译服务管理
- `config_service.py`: 配置管理
- `file_service.py`: 文件操作服务
- `state_manager.py`: 状态管理
- `ocr_service.py`: OCR服务
- `drag_drop_service.py`: 拖拽处理
- `shortcut_manager.py`: 快捷键管理

#### 3. 核心翻译引擎 (`manga_translator/`)
- `manga_translator.py`: 主翻译引擎(3068行)
- `config.py`: 配置模型和枚举定义
- `translators/`: 20+翻译器实现
- `ocr/`: OCR识别模块
- `detection/`: 文本检测模块
- `inpainting/`: 图像修复模块
- `rendering/`: 文本渲染模块

### 支持的翻译器

#### 在线翻译器
- `google`: Google翻译
- `deepl`: DeepL翻译
- `baidu`: 百度翻译
- `youdao`: 有道翻译
- `caiyun`: 彩云小译
- `papago`: Papago翻译
- `chatgpt`: OpenAI ChatGPT
- `deepseek`: DeepSeek翻译
- `gemini`: Google Gemini
- `groq`: Groq API
- `qwen2`: 通义千问
- `sakura`: Sakura翻译

#### 离线翻译器
- `sugoi`: Sugoi翻译器
- `nllb`: Facebook NLLB
- `m2m100`: M2M100模型
- `mbart50`: mBART50
- `jparacrawl`: JParaCrawl

### OCR支持
- `ocr32px`: 32像素OCR模型
- `ocr48px`: 48像素OCR模型
- `ocr48px_ctc`: CTC OCR模型
- `mocr`: Manga OCR专用模型

## 安装和运行

### 环境要求
- Python 3.8+
- PyTorch (CPU/GPU)
- CUDA (可选，GPU加速)

### 安装依赖
```bash
pip install -r requirements.txt
# 或使用GPU版本
pip install -r requirements_gpu.txt
```

### 运行应用程序
```bash
# 直接运行
python -m desktop-ui.main

# 或使用打包版本
./manga-translator.exe
```

### 构建打包
```bash
# CPU版本
build_cpu.bat

# GPU版本
build_gpu.bat

# 一键构建所有版本
build_all.bat
```

### 一键包使用说明

项目提供了预编译的一键安装包，无需安装Python环境即可使用：

#### Windows 一键包
- **CPU版本**: 包含所有依赖的独立可执行文件，无需CUDA支持
- **GPU版本**: 支持NVIDIA GPU加速，需要CUDA 12.x支持

#### 使用方法
1. 下载对应版本的一键包
2. 解压到任意目录
3. 双击运行 `manga-translator.exe` 即可启动程序
4. 无需安装Python或其他依赖

#### 优势
- 🚀 **开箱即用**: 无需配置开发环境
- 📦 **完整封装**: 包含所有模型和依赖
- ⚡ **性能优化**: 针对不同硬件预编译优化
- 🔧 **易于分发**: 单个可执行文件，方便分享

## 配置说明

### 配置文件
默认配置文件: `examples/config-example.json`

### UI设置与后端功能对应关系

#### 1. 翻译器设置 (Translator)
**UI选项**: 翻译器选择、目标语言、跳过无文本语言、GPT配置
**后端代码**: `manga_translator/translators/` 目录下的20+翻译器实现

**功能说明**:
- **翻译器选择**: 控制使用哪个翻译引擎
  - `openai`: ChatGPT翻译 (`chatgpt.py`)
  - `deepl`: DeepL API翻译 (`deepl.py`) 
  - `sugoi`: 离线Sugoi翻译器 (`sugoi.py`)
  - `nllb`: Facebook NLLB离线翻译 (`nllb.py`)
- **目标语言**: 在UI中，此选项为一个下拉菜单，支持选择多种目标语言。当前支持的语言包括：
  - `CHS`: 简体中文
  - `CHT`: 繁体中文
  - `CSY`: 捷克语
  - `NLD`: 荷兰语
  - `ENG`: 英语
  - `FRA`: 法语
  - `DEU`: 德语
  - `HUN`: 匈牙利语
  - `ITA`: 意大利语
  - `JPN`: 日语
  - `KOR`: 韩语
  - `POL`: 波兰语
  - `PTB`: 葡萄牙语（巴西）
  - `ROM`: 罗马尼亚语
  - `RUS`: 俄语
  - `ESP`: 西班牙语
  - `TRK`: 土耳其语
  - `UKR`: 乌克兰语
  - `VIN`: 越南语
  - `ARA`: 阿拉伯语
  - `SRP`: 塞尔维亚语
  - `HRV`: 克罗地亚语
  - `THA`: 泰语
  - `IND`: 印度尼西亚语
  - `FIL`: 菲律宾语（他加禄语）
- **跳过无文本语言**: 跳过没有检测到文本的图像
- **GPT配置**: OpenAI API配置路径

#### 2. OCR设置 (Text Recognition)
**UI选项**: OCR模型、最小文本长度、忽略气泡、概率阈值
**后端代码**: `manga_translator/ocr/` 目录

**功能说明**:
- **OCR模型**: 选择不同的OCR识别模型
  - `32px`: 32像素OCR模型 (`model_32px.py`)
  - `48px`: 48像素OCR模型 (`model_48px.py`) - 主要模型
  - `48px_ctc`: CTC OCR模型 (`model_48px_ctc.py`)
  - `mocr`: Manga OCR专用模型 (`model_manga_ocr.py`)
- **最小文本长度**: 过滤掉太短的文本识别结果
- **忽略气泡**: 阈值控制是否忽略气泡内的文本
- **概率阈值**: OCR识别置信度阈值

#### 3. 检测器设置 (Detector)
**UI选项**: 检测器类型、检测尺寸、文本阈值、旋转检测等
**后端代码**: `manga_translator/detection/` 目录

**功能说明**:
- **检测器类型**: 文本区域检测算法
  - `default`: 默认检测器 (`default.py`) - DBNet + ResNet34
  - `dbconvnext`: ConvNext检测器 (`dbnet_convnext.py`)
  - `ctd`: Comic文本检测器 (`ctd.py`)
  - `craft`: CRAFT检测器 (`craft.py`)
  - `paddle`: PaddleOCR检测器 (`paddle_rust.py`)
- **检测尺寸**: 图像检测时的缩放尺寸 (默认2048)
- **文本阈值**: 文本检测置信度阈值 (0.5)
- **旋转检测**: 提供多种旋转检测选项，以优化不同方向文本的识别效果。
  - `旋转图像进行检测`: 对整个图像进行旋转，可能改善整体检测效果。
  - `旋转图像以优先检测垂直文本行`: 优化算法，优先识别垂直排列的文本。
  - `反转图像颜色进行检测`: 通过反转颜色来提高特定背景下文本的识别率。
  - `应用伽马校正进行检测`: 通过伽马校正改善图像对比度，辅助文本检测。

#### 4. 修复器设置 (Inpainter)
**UI选项**: 修复器类型、修复尺寸、精度设置
**后端代码**: `manga_translator/inpainting/` 目录

**功能说明**:
- **修复器类型**: 文本擦除和图像修复算法
  - `lama_large`: 大型LaMa修复模型 (`inpainting_lama_mpe.py`)
  - `lama_mpe`: LaMa MPE修复模型
  - `sd`: Stable Diffusion修复 (`inpainting_sd.py`)
  - `default`: AOT修复器 (`inpainting_aot.py`)
- **修复尺寸**: 修复处理时的图像尺寸
- **精度设置**: FP32/FP16/BF16精度选择

#### 5. 渲染器设置 (Renderer)
**UI选项**: 排版模式、对齐方式、字体设置、文字方向等
**后端代码**: `manga_translator/rendering/` 目录

**功能说明**:
- **排版模式**: 文本布局算法 (`rendering/__init__.py:51-367`)
  - `smart_scaling`: 智能缩放 (推荐)
  - `strict`: 严格边界 (缩小字体)
  - `fixed_font`: 固定字体 (扩大文本框)
  - `disable_all`: 完全禁用 (裁剪文本)
  - `default`: 默认模式 (有Bug)
- **字体路径**: 在UI中，此选项为一个下拉菜单，自动加载 `fonts` 目录下的所有字体文件。旁边配有“打开目录”按钮，方便用户管理字体。
- **对齐方式**: 左对齐/居中/右对齐/自动
- **字体边框**: 是否禁用字体边框
- **文字方向**: 水平/垂直/自动检测

#### 6. 修复参数 (Repair Parameters)
**UI选项**: 过滤文本、核大小、蒙版膨胀偏移
**后端代码**: `manga_translator/mask_refinement/` 目录

**功能说明**:
- **过滤文本**: 文本过滤正则表达式
- **核大小**: 形态学操作核大小 (默认3)
- **蒙版膨胀偏移**: 蒙版膨胀的像素偏移量

#### 7. 超分辨率设置 (Upscale)
**UI选项**: 超分器类型、恢复超分
**后端代码**: `manga_translator/upscaling/` 目录

**功能说明**:
- **超分器类型**: ESRGAN等超分辨率模型
- **恢复超分**: 是否恢复原始分辨率

#### 8. 上色器设置 (Colorizer)
**UI选项**: 上色器类型、上色尺寸、去噪强度
**后端代码**: `manga_translator/colorization/` 目录

### 主要配置项示例

#### 翻译器配置
```json
"translator": {
    "translator": "chatgpt",
    "target_lang": "CHS",
    "no_text_lang_skip": false,
    "gpt_config": "./examples/gpt_config-example.yaml"
}
```

#### OCR配置
```json
"ocr": {
    "use_mocr_merge": false,
    "ocr": "48px",
    "min_text_length": 0,
    "ignore_bubble": 0,
    "prob": 0.001
}
```

#### 检测器配置
```json
"detector": {
    "detector": "default",
    "detection_size": 2048,
    "text_threshold": 0.5,
    "det_rotate": false,
    "det_auto_rotate": false,
    "det_invert": false,
    "det_gamma_correct": false,
    "box_threshold": 0.7,
    "unclip_ratio": 2.5
}
```

#### 渲染配置
```json
"render": {
    "renderer": "default",
    "alignment": "auto",
    "disable_font_border": true,
    "font_size_offset": 0,
    "font_size_minimum": 0,
    "direction": "auto",
    "uppercase": false,
    "lowercase": false,
    "gimp_font": "Sans-serif",
    "no_hyphenation": false,
    "font_color": ":FFFFFF",
    "rtl": true,
    "layout_mode": "smart_scaling"
}
```

### 自定义翻译模板

本程序支持高度自由的文本模板系统，允许用户完全自定义手动翻译时所使用的`.txt`文件格式。

#### 工作原理：四大部分

程序能理解任意格式的模板，因为它会自动将模板文件拆解为四个部分进行处理：

1.  **前缀 (Prefix)**: 文件开头不变的部分。
2.  **条目 (Item)**: 为每个文本框重复的格式，**必须包含 `<original>`**。
3.  **分隔符 (Separator)**: 每个“条目”之间的内容，也会重复。
4.  **后缀 (Suffix)**: 文件结尾不变的部分。

**核心规则**: 程序通过识别模板文件中所有包含 `<original>` 的行来自动切分出这四个部分。具体来说，“条目”就是第一个包含`<original>`的行；“分隔符”就是第一和第二个“条目”之间的内容。

#### 示例解析

为了清晰地展示其灵活性，我们来看一个自定义的例子。

**1. 假设您的模板文件内容如下:**

```
**最终生成的`.txt`文件 (假设有三个文本框):**

```
# 项目: 我的漫画翻译
# 日期: 2025-09-06

[原文]: "原文1"
[译文]: "译文1"
---
[原文]: "原文2"
[译文]: "译文2"
---
[原文]: "原文3"
[译文]: "译文3"

# 翻译结束
```
```

**2. 程序会这样解析它:**

-   **前缀**: `# 项目: 我的漫画翻译
# 日期: 2025-09-06

`
-   **条目**: `[原文]: <original>
`
-   **分隔符**: `[译文]: <translated>
---
`
-   **后缀**: `# 翻译结束`

**3. 最终生成的`.txt`文件 (假设有两个文本框):**

```
# 项目: 我的漫画翻译
# 日期: 2025-09-06

[原文]: "原文1"
[译文]: "译文1"
---
[原文]: "原文2"
[译文]: "译文2"

# 翻译结束
```

#### 高级技巧：特殊字符处理

程序在生成和解析`.txt`文件时，会将原文和译文作为JSON字符串处理。如果您手动编辑的译文包含特殊字符，如双引号 `"` 或反斜杠 `\`，您需要将它们手动转义为 `\"` 和 `\\`，以确保程序能正确解析。



## 使用指南

### 基本使用
1. 启动应用程序
2. 添加要翻译的图片文件或文件夹
3. 选择输出目录
4. 配置翻译设置
5. 点击"开始翻译"

### 高级功能
- **文本模板**: 支持从TXT文件导入翻译
- **批量处理**: 支持并发批量翻译
- **质量设置**: 可调整检测精度、渲染质量等
- **字体管理**: 支持多种字体和文字效果

### CLI命令行选项详解

#### 通用设置
- **`verbose`**: 详细模式 - 打印调试信息并保存中间处理图像
- **`attempts`**: 错误重试次数 (0=不重试, -1=无限重试)
- **`ignore_errors`**: 忽略错误 - 遇到错误时跳过当前图像
- **`use_gpu`**: 启用GPU加速 (自动选择CUDA/MPS)
- **`use_gpu_limited`**: 有限GPU使用 - 排除离线翻译器

#### 文本处理模式详解 (手动翻译工作流)

本程序提供了强大的文本处理工作流，核心围绕“保存文本”、“加载文本”和“模板模式”三个选项展开。正确理解它们的组合用法，是高效地进行手动翻译和校对的关键。

- **`保存文本 (save_text)`**: 
  - **作用**: 执行完整的“检测->OCR->翻译”流程，并将所有结果（包括文本区域坐标、原文、译文等）保存到一个与原图片同名的 `_translations.json` 文件中。
  - **用途**: 这是所有后续操作的**基础**。生成的 `.json` 文件是进入编辑器、重新渲染、或生成翻译模板的数据源。

- **`加载文本 (load_text)`**:
  - **作用**: **跳过**耗时的“检测”和“翻译”步骤，直接从 `.json` 文件中读取数据进行渲染。
  - **用途**: 当您对文本内容满意，只想调整字体、颜色、排版等**渲染效果**时，此模式可以快速重新输出图片，无需重复翻译。

- **`模板模式 (template)`**: 
  - **作用**: 这是一个**组合开关**，必须与 `保存文本` 或 `加载文本` 配合使用，是实现**手动翻译工作流**的核心。

--- 

### 手动翻译完整流程

#### 流程一：生成翻译模板 (`保存文本` + `模板模式`)

1.  **勾选选项**: 在UI中同时勾选 `保存文本` 和 `模板模式`。
2.  **执行操作**: 点击“开始翻译”。
3.  **产出结果**: 程序将**跳过翻译步骤**，仅执行“文本检测”和“OCR识别”，然后根据您选择的模板文件格式，生成一个可供编辑的 `.txt` 文件。在此文件中，`<translated>` 占位符（即译文）会默认用原文填充。

#### 流程二：导入手动翻译 (`加载文本` + `模板模式`)

1.  **完成翻译**: 在外部文本编辑器中打开上一步生成的 `.txt` 文件，将您的译文填入。
2.  **勾选选项**: 在UI中同时勾选 `加载文本` 和 `模板模式`。
3.  **执行操作**: 点击“开始翻译”。
4.  **内部流程**: 
    1.  程序会读取您的 `.txt` 文件，并将其中的译文更新到对应的 `.json` 文件中的 `translation` 字段。
    2.  **【关键步骤】**: 程序紧接着会将 `translation` 字段的内容，**覆盖**掉同一条目下的 `text` (原文) 字段。这一步“回写”操作，意味着您的手动翻译被正式“固化”，成为新的“原文”。
    3.  程序使用这个被更新和“固化”后的 `.json` 文件进行最终的渲染。

#### 流程三：基于手动翻译调整样式 (仅 `加载文本`)

1.  **完成流程二**: 确保您已经执行过一次“导入手动翻译”的流程。
2.  **勾选选项**: **只勾选** `加载文本`，确保 `模板模式` **不被勾选**。
3.  **执行操作**: 此时，您可以任意调整“渲染参数”中的字体、颜色、大小等设置，然后点击“开始翻译”。
4.  **产出结果**: 程序会直接使用 `.json` 文件中已经被您“固化”的手动翻译稿，进行快速的重新渲染，生成最终图片。


#### 文件处理选项
- **`overwrite`**: 覆盖已翻译的图像文件
- **`skip_no_text`**: 跳过没有检测到文本的图像
- **`format`**: 输出格式选择 (PNG/JPEG/WEBP)
- **`save_quality`**: JPEG保存质量 (0-100)

#### 性能优化
- **`batch_size`**: 批量处理大小 (默认1=不批量)
- **`batch_concurrent`**: 并发批处理 - 分别处理每个图像，防止模型输出问题
- **`disable_memory_optimization`**: 禁用内存优化 - 处理期间保持模型加载

#### 高级选项
- **`font_path`**: 自定义字体文件路径
- **`pre_dict`/`post_dict`**: 翻译前后处理词典文件
- **`kernel_size`**: 文本擦除卷积核大小 (默认3)
- **`context_size`**: 翻译上下文页面数
- **`prep_manual`**: 手动排版准备 - 输出空白修复图像和原始参考
- **`use_mtpe`**: 机器翻译后编辑 (仅Linux可用)

### 模式优先级逻辑
- **保存文本 + 模板**: 仅运行检测和OCR，跳过翻译
- **模板 + 加载文本**: TXT内容作为翻译，跳过翻译阶段
- **仅模板**: 无效果，继续正常翻译流程

### 编辑器使用
1. 在主界面点击"视觉编辑器"
2. 加载图片后可以进行:
   - 文本区域编辑
   - 蒙版绘制
   - 实时翻译预览
   - 手动调整文本布局

## 项目结构

```
manga-translator-ui-package/
├── desktop-ui/                 # 桌面应用程序
│   ├── services/              # 服务层
│   ├── components/            # UI组件
│   ├── app.py                 # 主应用
│   ├── main.py                # 入口点
│   └── editor_frame.py        # 编辑器
├── manga_translator/          # 核心引擎
│   ├── translators/           # 翻译器实现
│   ├── ocr/                   # OCR模块
│   ├── detection/             # 文本检测
│   ├── inpainting/            # 图像修复
│   └── rendering/             # 文本渲染
├── examples/                  # 示例文件
│   ├── config-example.json    # 配置示例
│   └── gpt_config-example.yaml
├── dict/                      # 词典文件
├── fonts/                     # 字体文件
├── models/                    # 模型文件
├── requirements.txt          # 依赖列表
└── build_*.bat               # 构建脚本
```

## 开发说明

### 代码风格
- 使用Python类型注解
- 遵循PEP8规范
- 模块化设计，易于扩展

### 扩展翻译器
1. 在 `manga_translator/translators/` 创建新翻译器
2. 实现必要的接口方法
3. 在 `translators/__init__.py` 中注册
4. 在 `config.py` 中添加枚举值

### 自定义OCR
1. 在 `manga_translator/ocr/` 添加新模型
2. 实现 `CommonOCR` 接口
3. 在 `ocr/__init__.py` 中注册

## 许可证

本项目基于MIT许可证开源。

## 贡献

欢迎提交Issue和Pull Request来改进这个项目。

## 支持

如有问题请查看:
- GitHub Issues: 提交问题和建议
- 文档: 查看详细使用说明
- 示例: 参考配置示例文件