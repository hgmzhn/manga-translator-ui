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
- **目标语言**: 支持20+种语言代码 (CHS/CHT/JPN/ENG等)
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
- **旋转检测**: 自动旋转检测和手动旋转选项

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

#### 文本处理模式
- **`save_text`**: 保存翻译文本到JSON文件 (`{原文件名}_translations.json`)
- **`load_text`**: 从JSON文件加载预翻译内容，跳过检测和翻译阶段
- **`template`**: 生成翻译模板 - 翻译字段为原始文本副本，用于手动编辑

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