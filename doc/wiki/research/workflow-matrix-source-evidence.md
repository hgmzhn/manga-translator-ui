# Phase 0：九个工作流的源码证据清单

> 范围：固定桌面“Translation Workflow Mode:”选择器中的九种工作流，以及其源码可确认的输入、命名/查找、输出、阶段分支和参数边界。
>
> 取证日期：2026-08-06。此文件是 Phase 0 内部数据源，不是面向用户的 Wiki 页面；没有启动 GUI、模型下载或实际翻译任务。

## 固定的选择器和显示文案

选择器按下表的索引建立，索引同时是 `on_workflow_mode_changed()` 写入配置的映射依据。表中的“调用 key”是运行时代码传给 `_t()` 的字符串；不是 `label_*` 设置项的 key。

| 索引 | 调用 key / English UI | 简体中文 UI | 切换后 `cli` 工作流字段 | 开始按钮（English / 简体中文） |
| ---: | --- | --- | --- | --- |
| 0 | `Normal Translation` | 正常翻译流程 | 八个工作流字段均为 `false` | `Start Translation` / 开始翻译 |
| 1 | `Export Translation` | 导出翻译 | `generate_and_export=true` | `Export Translation` / 导出翻译 |
| 2 | `Export Original Text` | 导出原文 | `template=true` | `Generate Original Text Template` / 仅生成原文模板 |
| 3 | `Translate JSON Only` | 仅翻译（JSON） | `translate_json_only=true` | `Start JSON Translation` / 开始仅翻译（JSON） |
| 4 | `Import Translation and Render` | 导入翻译并渲染 | `load_text=true` | `Import Translation and Render` / 导入翻译并渲染 |
| 5 | `Colorize Only` | 仅上色 | `colorize_only=true` | `Start Colorizing` / 开始上色 |
| 6 | `Upscale Only` | 仅超分 | `upscale_only=true` | `Start Upscaling` / 开始超分 |
| 7 | `Inpaint Only` | 仅修复 | `inpaint_only=true` | `Start Inpainting` / 开始修复 |
| 8 | `Replace Translation` | 替换翻译 | `replace_translation=true` | `Start Replace Translation` / 开始替换翻译 |

`desktop_qt_ui/ui/main_page/runtime.py` 在改变索引前把 `load_text`、`translate_json_only`、`template`、`generate_and_export`、`colorize_only`、`upscale_only`、`inpaint_only`、`replace_translation` 全部清为 `false`，再只设定上表中的一个字段。因此 GUI 的一次选择是互斥的；“导出原文”另外依赖 `save_text`，见后文。

## 共用输入、输出和命名规则

### 主输入列表

- 桌面“Add Files”“Add Folder”以及拖放共享 `FileService`。文件夹递归查找 `SUPPORTED_IMAGE_EXTENSIONS`，按自然排序收集，并跳过名为 `manga_translator_work` 的目录；压缩包/文档扩展名由同一服务另行识别。
- 本清单以主输入图片为单位表示路径。压缩包解包后的输入与九种模式的副文件如何映射，尚未做运行验证，不能假定其副文件与压缩包内路径自动配对。
- 主输出图由 `MangaTranslator._calculate_output_path()` 决定：正常输出目录下保留输入文件夹名与相对层级；`save_to_source_dir=true` 时改为原图同级 `manga_translator_work/result/`；`cli.format` 为空或 `none` 时保留原扩展名，否则使用给定扩展名。

### 每图工作目录

以下规则均以输入图片的原始路径和不含扩展名的 `<stem>` 为基准；JSON 查找先用新位置，再回退旧的图片同级位置。

| 资源 | 新位置 / 文件名 | 兼容或优先级 |
| --- | --- | --- |
| 译文工程 JSON | `manga_translator_work/json/<stem>_translations.json` | 回退 `<image-dir>/<stem>_translations.json` |
| 原文导出 | `manga_translator_work/originals/<stem>_original.<template-format>` | 模板未指定或不可读时 `template-format=json` |
| 译文导出 | `manga_translator_work/translations/<stem>_translated.<template-format>` | 同上 |
| 修复图 | `manga_translator_work/inpainted/<stem>_inpainted.<original-ext>` | 无其他查找位置 |
| 上色/超分编辑器底图 | `manga_translator_work/editor_base/<original-filename>` | 兼容旧工作目录根部的同名底图 |
| 替换翻译的配对图 | `manga_translator_work/translated_images/<stem><ext>` | 先同扩展名，后遍历 `SUPPORTED_IMAGE_EXTENSIONS` |

模板解析的输出格式取 `config/translation_template.json` 首个 `output_format:` 行，合法值为安全的 1–32 字符扩展名；缺失/非法值回退 `json`。模板文本仍由 `workflow_service` 用 `<original>`、`<translated>` 占位符生成。

## 工作流矩阵

阶段列的“条件执行”表示由常规参数决定，例如 `colorizer.colorizer != none`、`upscale.upscale_ratio` 为真或检测到了文本；它不是工作流强制开启。`—` 表示该工作流路径不调用该阶段。

| 工作流 | 额外输入和发现规则 | 输出 | 执行阶段 | 跳过阶段 / 关键参数边界 |
| --- | --- | --- | --- | --- |
| 正常翻译 | 主输入图片。无工作流副文件前置。 | 主输出图；`save_text=true` 时写工程 JSON，修复完成时还写修复图；已启用上色或超分时写编辑器底图。 | 上色（条件）→ 超分（条件）→ 检测 → OCR → 文本行合并 → 翻译 → 蒙版细化 → 修复 → 渲染。无检测框或无 OCR 文本时提前返回输入/超分图。 | 无工作流专属强制值；仅此模式可进入 `batch_concurrent` 并发管线。 |
| 导出翻译 | 主输入图片与可用模板。 | 工程 JSON 和 `<stem>_translated.<template-format>`；不写主输出图。 | 上色（条件）→ 超分（条件）→ 检测 → OCR → 合并 → 翻译；有区域且有原始蒙版时做蒙版细化。 | 跳过修复、渲染和主图保存。`generate_and_export=true`；若 `detector.import_yolo_labels=true`，导出分支跳过蒙版细化且 JSON 不保存蒙版。 |
| 导出原文 | 主输入图片与可用模板。 | 工程 JSON 和 `<stem>_original.<template-format>`；不写主输出图。 | 上色（条件）→ 超分（条件）→ 检测 → OCR → 合并；有区域且有原始蒙版时做蒙版细化。 | 仅在 `template=true` **且** `save_text=true` 时进入此分支；跳过翻译、修复、渲染和主图保存。GUI 只设置 `template`，Qt 默认 `save_text=true`，外部配置把它改为 `false` 时需要运行验证实际退化路径。导入 YOLO 标签时的蒙版例外同“导出翻译”。 |
| 仅翻译（JSON） | 主输入图片必须能找到工程 JSON；接受旧 JSON 的区域列表或新 JSON 的 `regions` 对象结构。 | 回写工程 JSON；成功后删除同图 `<stem>_original.<template-format>`；不写主输出图。 | 从 JSON 载入区域 → 翻译 → JSON 回写。 | 跳过上色、超分、检测、OCR、合并、蒙版、修复和渲染。`translate_json_only=true`；不以 `save_text` 为条件保存 JSON。 |
| 导入翻译并渲染 | 主输入图片必须有工程 JSON；开始前若同时找到 TXT，优先 `<stem>_original.<template-format>`，否则用 `<stem>_translated.<template-format>`，按模板导入 JSON。 | 主输出图和更新后的工程 JSON；需要重新修复时会写修复图。 | JSON/内存载荷读取 →（已有精炼蒙版则复用；否则）蒙版细化 → 修复 → 渲染。若 JSON 没有蒙版且 `import_yolo_labels=true`，会额外调用检测生成蒙版。 | 正常路径跳过上色、超分、检测、OCR、翻译和文本行合并；上述 YOLO 缺蒙版情况是检测例外。`load_text=true`；已有修复图与可用 JSON 蒙版可复用，AI renderer 可令修复阶段跳过。 |
| 仅上色 | 主输入图片；上色是否实际改变图像由 `colorizer.colorizer` 决定。 | 主输出图；上色配置有效时写编辑器底图。 | 上色（条件）。 | 跳过超分、检测、OCR、合并、翻译、蒙版、修复、渲染。`colorize_only=true` 不会强制选择上色器；上色器为 `none` 时结果是原图。 |
| 仅超分 | 主输入图片；实际倍率来自 `upscale.upscale_ratio`。 | 主输出图；启用上色或倍率时写编辑器底图。 | 上色（条件）→ 超分（条件）。 | 跳过检测、OCR、合并、翻译、蒙版、修复、渲染。`upscale_only=true` 不强制倍率；倍率为空时输出为上色结果或原图。源代码不会在该模式自动关闭上色，因此“仅超分”的显示提示与 `colorizer.colorizer != none` 的实际前置上色不完全一致。 |
| 仅修复 | 主输入图片。 | 主输出图；该分支清空 `text_regions`，不以翻译文本渲染。 | 上色（条件）→ 超分（条件）→ 检测 → 以字面量 `TEXT` 填充检测行 → 文本行合并 → 蒙版细化 → 修复。 | 跳过 OCR、翻译和渲染。`inpaint_only=true`；无检测行/蒙版或无合并区域时返回未修复图。选择 AI renderer 时会跳过真正的修复而把工作图作为修复底图。 |
| 替换翻译 | 主输入为生肉图；必须在同图工作目录 `translated_images/` 放置同名翻译图（先同扩展名，后任意受支持扩展名）。 | 主输出图。非“直接粘贴”且 `save_text=true` 时另写修复图和工程 JSON；直接粘贴时明确不写二者，也不导出 PSD。 | 对生肉图和配对翻译图各运行上色（条件）→ 超分（条件）→ 检测 → OCR → 合并；再按缩放后的区域重叠（阈值 `0.3`）配对，修复生肉匹配区，最后重新渲染，或直接从翻译图提取文字粘贴。 | 不调用翻译服务。强制把 `render.disable_auto_wrap=true`、`render.layout_mode='strict'` 和 `cli.replace_translation=true` 写入运行时配置。`render.enable_template_alignment=true` 只在此模式走直接粘贴；`paste_mask_dilation_pixels` 也只消费在该直接粘贴分支。 |

## 互斥、覆盖和忽略清单

### GUI 保证与非 GUI 配置

- GUI 切换保证上述八个工作流布尔字段互斥，但不会校验手工 JSON、服务请求或其他入口提供的组合。
- `sync_workflow_mode_from_config()` 读取已有组合时的显示优先级是：替换翻译、仅修复、仅超分、仅上色、导入翻译、仅翻译 JSON、导出原文、导出翻译、正常。
- `translate_batch()` 的实际入口先执行 `load_text` 的 TXT 预导入，再让 `replace_translation` 优先分派；之后顺序处理 `load_text`、`translate_json_only`。落到常规预处理后，`colorize_only` 比 `upscale_only` 和 `inpaint_only` 更早返回；导出原文分支又先于导出翻译分支。因此手工叠加模式没有“同时执行”的契约，后续文档不能把它描述为受支持组合。

### `batch_concurrent`

`batch_concurrent` 仅正常翻译可进入并发流水线。桌面控制层和 `translate_batch()` 都将下列情况视为不兼容：导入翻译、仅翻译 JSON、导出原文（`template and save_text`）、导出翻译、仅上色、仅超分、仅修复、替换翻译。前端会把本次局部变量改为非并发；核心分支即使实例字段仍为真，也只在“无不兼容模式”时构建 `ConcurrentPipeline`。

### 其他影响输出的参数

| 参数 | 生效/被忽略的边界 |
| --- | --- |
| `cli.save_text` | GUI/发行默认值为 `true`。它是“导出原文”进入导出分支的必要条件；还控制普通工作流的 JSON、修复图和一些编辑器工程写入。仅翻译 JSON 自己无条件回写 JSON。 |
| `colorizer.colorizer` | 不是“仅上色”的强制值。正常、仅超分、仅修复和替换翻译也会在其不为 `none` 时先上色。 |
| `upscale.upscale_ratio` | 不是“仅超分”的强制值；为空时仅超分会原样通过（或保留前置上色结果）。正常、仅修复和替换翻译也会在其为真时先超分。 |
| `detector.import_yolo_labels` | 导出原文/导出翻译会跳过蒙版细化和蒙版保存；导入翻译且 JSON 无蒙版时会触发检测补蒙版。 |
| `render.enable_template_alignment` | 设置说明明确为替换翻译专用。替换翻译开启时走直接粘贴，并不写 JSON、修复图或 PSD；关闭时以 OCR 得到的配对区域重新渲染。 |
| `cli.overwrite` | GUI 开始前按工作流检查既有副文件或主输出图。导出原文/翻译检查对应 TXT；仅翻译 JSON 检查原文副文件；其他模式检查主输出图。仅翻译 JSON 的“原文文件不存在则跳过”条件需运行验证，因其方向与通常的覆盖检查不同。 |

## 未完成的运行验证

以下项目故意不据静态源码标记为已验证：

1. 使用脱敏小图逐一运行九种 GUI 模式，记录实际按钮、提示、输出目录及取消/错误后的保留文件。
2. 验证文件夹和压缩包输入下，工作目录和主输出相对层级不会碰撞；尤其是同名不同扩展名与解包图片。
3. 对导出原文设置 `save_text=false`，确认 GUI 所谓“导出原文”是否退回正常翻译；对手工叠加多个工作流字段记录实际优先级。
4. 验证仅超分/仅修复/替换翻译在上色器开启、倍率为空和 AI renderer 选中时的实际输出，确认本清单标出的“显示名称与分支不完全一致”。
5. 验证导入翻译时原文 TXT 优先于译文 TXT、JSON 无蒙版 + 导入 YOLO 标签的检测回退、已存在修复图的复用条件，以及 JSON-only 成功后原文副文件删除。
6. 验证直接粘贴与重新渲染两条替换翻译分支，包含不同尺寸配对、无配对图/无匹配区域、`paste_mask_dilation_pixels`、JSON/修复图/PSD 的实际写入差异。
7. 验证 `batch_concurrent` 在全部特殊模式下的前端状态、核心实际执行路径和运行日志；不得仅以警告文字当作禁用证据。

## 源码依据

| 文件 | 核对内容 |
| --- | --- |
| `desktop_qt_ui/ui/main_page/pages/translation_page.py:27` | 翻译页、工作流下拉、开始按钮及其事件连接。 |
| `desktop_qt_ui/ui/main_page/runtime.py:21` | 九个索引、提示、互斥字段写入、配置同步优先级和按钮文本。 |
| `desktop_qt_ui/locales/en_US.json:488` | English 工作流、开始按钮、提示与设置实际值。 |
| `desktop_qt_ui/locales/zh_CN.json:486` | 简体中文工作流、开始按钮、提示与设置实际值。 |
| `desktop_qt_ui/services/file_service.py:31` | 主输入的扩展名验证、递归发现、自然排序和工作目录排除。 |
| `desktop_qt_ui/app_logic.py:3094` | 主输出路径传递、覆盖前检查和特殊模式并发禁用。 |
| `desktop_qt_ui/core/config_models.py:123` | Qt 工作流字段与 `save_text` 默认值。 |
| `manga_translator/manga_translator.py:504` | 主输出路径、JSON 写入、模板导出、TXT 导入和九种运行时分派。 |
| `manga_translator/manga_translator.py:3399` | `translate_batch()` 的特殊工作流优先级、导出/JSON-only/导入分支和并发限制。 |
| `manga_translator/manga_translator.py:4236` | 上色、超分、仅上色、仅超分、仅修复及常规预处理的实际顺序。 |
| `manga_translator/manga_translator.py:5206` | 常规译后蒙版、修复和渲染顺序。 |
| `manga_translator/utils/path_manager.py:12` | 每图工作目录、JSON/TXT/修复/编辑器底图和副文件发现规则。 |
| `manga_translator/utils/translation_template.py:10` | 模板默认文件、`output_format` 解析与回退。 |
| `manga_translator/utils/replace_translation.py:128` | 替换翻译的双图处理、强制排版设置、配对、修复、直接粘贴和输出边界。 |
| `manga_translator/utils/replace_translation.py:726` | 翻译图的同名/扩展名查找顺序。 |
