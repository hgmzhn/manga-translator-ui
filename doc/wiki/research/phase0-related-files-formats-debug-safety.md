# Phase 0：关联文件、格式、调试产物与敏感信息

> 静态源码证据日期：2026-08-06。本文是 Phase 0 的资料归属清单，不是面向用户的 Wiki 页面，也不以未运行的文件树替代运行验证。

## 范围与判定

- 路径中的 `<image-dir>` 指输入图片所在目录，`<stem>` 指去掉扩展名后的图片名；实际根目录由 `manga_translator/utils/path_manager.py:20-48` 计算。
- 运行时外部配置目录由 `manga_translator/runtime_paths.py:10-28` 决定：开发环境为仓库根目录下的 `config/`，冻结包为可执行文件相邻的 `config/`。
- 本次没有读取任何用户可写、被 Git 忽略的配置值或用户内容；`git check-ignore` 已确认 `.env`、主配置、规则、模板、AI 提示词和服务器数据属于忽略范围。
- “所有者”指负责创建、写回或维护格式的主要模块；“消费者”指已经找到的读取或执行链，而非未来 Wiki 的页面归属。

## 应用配置与提示词

| 文件或模式 | 格式与主要字段/约束 | 所有者 | 已确认消费者 | 源码依据 |
| --- | --- | --- | --- | --- |
| `.env` | dotenv 的 `KEY=VALUE` 文本；API 管理把包含 `API_KEY`、`AUTH_KEY` 或 `TOKEN` 的键视为秘密 | `ConfigService` 立即更新内存和环境变量，250 ms 合并、原子写回 | 翻译器及 API 轮换从环境变量读取 | `desktop_qt_ui/services/config_service.py:153-161, 535-557, 760-785`; `desktop_qt_ui/ui/main_page/env_management.py:190-220` |
| `config/config-example.json` | UTF-8 JSON；发行/开发默认配置 | `ConfigService` | 默认配置加载；服务器缺少用户配置时也可作为复制来源 | `desktop_qt_ui/services/config_service.py:886-939`; `manga_translator/server/core/config_manager.py:328-337` |
| `config/config.json` | UTF-8 JSON；用户设置，优先于示例配置 | `ConfigService`（桌面）和 server config manager | `AppSettings`、核心 `Config` 与 Web 服务配置 | `desktop_qt_ui/services/config_service.py:634-669, 895-939`; `manga_translator/server/core/config_manager.py:19-21` |
| `config/config/translators.json` | JSON 翻译器元数据/所需环境变量清单 | `ConfigService` 的翻译器配置加载 | 桌面 API 管理和 Web `/config` 翻译器信息 | `desktop_qt_ui/services/config_service.py:229`; `manga_translator/server/routes/config.py:783-784` |
| `config/custom_api_params.json` | JSON；受支持 AI 后端的额外请求参数，不是 API 通道/模型选择文件 | `manga_translator.custom_api_params` | `Config.use_custom_api_params` 启用时的运行时 API 参数 | `manga_translator/custom_api_params.py:37`; `manga_translator/config.py:489-492` |
| `config/text_replacements.yaml` | YAML 替换规则；Raw 编辑必须保持有效 YAML | 替换规则编辑器与运行时文件初始化 | 渲染阶段文本替换 | `manga_translator/runtime_files.py:50-58`; `manga_translator/rendering/text_replacements.py:21-25`; `desktop_qt_ui/ui/secondary_pages/replacements_editor.py:1-4, 323-326` |
| `config/rich_text_rules.yaml` | YAML 富文本/竖排规则 | 富文本规则 UI 与运行时文件初始化 | 渲染布局与富文本规则解析 | `manga_translator/rendering/rich_text_rules.py:26-30`; `manga_translator/runtime_files.py` |
| `config/batch_edit_schemes.yaml` | YAML，顶层 `schemes`；`safe_load` / `safe_dump` | 桌面批量管理服务；不进入渲染管线 | 批量管理页面的方案保存与读取 | `desktop_qt_ui/services/batch_edit_schemes.py:1-5, 94-101, 223-246` |
| `config/translation_template.json` | 名称为 `.json`，但由模板解析器按文本读取：可选 `output_format` 行后接翻译模板，因此不得假定为严格 JSON | 翻译模板初始化与工作流服务 | 决定 `originals/`、`translations/` 导出扩展名；默认 `json` | `manga_translator/utils/translation_template.py:10-65`; `desktop_qt_ui/services/workflow_service.py:664-666` |
| `config/filter_list.json`（兼容 `filter_list.txt`） | JSON 过滤列表；旧 TXT 只作兼容 | 文本过滤模块/过滤列表页面 | OCR 或文本过滤链 | `manga_translator/utils/text_filter.py:14-29` |
| `dict/prompt_example.yaml` | YAML；高质量翻译提示词路径的默认值 | Qt 设置模型 | `openai_hq` 读取 `translator.high_quality_prompt_path` | `desktop_qt_ui/core/config_models.py:17-20`; `manga_translator/translators/openai_hq.py:535-539` |
| `dict/ai_ocr_prompt.yaml` | YAML（也兼容 `.yml` / `.json`）；首个 `ai_ocr_prompt`、`ocr_prompt` 或 `prompt` 字符串 | AI OCR prompt loader / 编辑器 | AI OCR | `manga_translator/ocr/prompt_loader.py:12-79`; `manga_translator/translators/prompt_loader.py:35-72` |
| `dict/ai_renderer_prompt.yaml` | YAML；`ai_renderer_prompt`、`renderer_prompt` 或 `prompt` | AI 渲染 prompt loader / 编辑器 | AI renderer | `manga_translator/rendering/prompt_loader.py:23-83`; `manga_translator/translators/prompt_loader.py:35-72` |
| `dict/ai_colorizer_prompt.yaml` | YAML；提示词、规则列表和参考图路径可共存 | AI 上色 prompt loader / 编辑器 | AI colorizer；相对参考图按提示词/图片目录解析 | `manga_translator/colorization/prompt_loader.py:16-113` |
| `dict/system_prompt_hq.yaml`、`system_prompt_hq_format.yaml`、`system_prompt_line_break.yaml`、`glossary_extraction_prompt.yaml` | YAML/JSON 均可解析，同 stem 时优先 YAML；系统提示词文件 | translators prompt loader | HQ 翻译、格式约束、断句和术语提取 | `manga_translator/translators/prompt_loader.py:35-95, 112-190, 276-281`; `manga_translator/server/routes/files.py:101-107, 174-178` |
| `dict/*_dict.txt`、`*.json`、`*.yaml` | 翻译词典/自定义提示词等可读文本或结构化文件；具体语法由对应 translator/loader 决定 | 对应翻译器或用户自定义文件 | 例如 Sakura 的 `SAKURA_DICT_PATH`，以及 CLI `pre_dict` / `post_dict` | `manga_translator/translators/keys.py:27-28`; `manga_translator/manga_translator.py:334-336, 931-939, 5144-5149` |

### 配置写入与手改边界

- 桌面服务对 `config.json` 和 `.env` 均使用临时文件替换式原子写入；配置更改先进入内存，常规保存由 250 ms 防抖合并。不要在应用仍有待写入操作时手改同一文件。
- `config/config.json` 的加载优先级为用户配置 > 示例配置 > 代码默认值，不能把任一文件中的值笼统写成唯一默认值。
- `translation_template.json` 的 `output_format` 只能是受正则限制的安全扩展名；该值影响文本导出文件名，不能把它与逐图的 `*_translations.json` 项目数据混为同一格式。
- 系统提示词文件由服务端路由禁止上传/删除；用户可上传的普通提示词可为 YAML、YML 或 JSON，且可能包含私有提示词和相对/绝对资源引用。

## 逐图工作目录、导出与项目数据

| 文件或目录模式 | 格式与命名 | 所有者 | 已确认消费者/用途 | 源码依据 |
| --- | --- | --- | --- | --- |
| `<image-dir>/manga_translator_work/json/<stem>_translations.json` | UTF-8 JSON；顶层键为图片的绝对路径，值为区域数据、尺寸、可选 `mask_raw`、`mask_is_refined`、覆盖层及导出目录信息 | `MangaTranslator._save_text_to_file`，编辑器可回写 | `load_text`、编辑器与批量修改；先找新路径，再兼容原图同目录的旧 `*_translations.json` | `manga_translator/utils/path_manager.py:151-169, 368-386`; `manga_translator/manga_translator.py:714-718, 832-872, 1375-1524`; `desktop_qt_ui/editor/controller_export_service.py:334-337` |
| `<image-dir>/manga_translator_work/originals/<stem>_original.<output_format>` | 文本导出；扩展名来自 `translation_template.json`，默认 `.json` | 路径管理器和导出工作流 | 导出原文、仅翻译 JSON 前置检查与工作流服务 | `manga_translator/utils/path_manager.py:178-201`; `manga_translator/mode/local.py:90-105`; `desktop_qt_ui/services/workflow_service.py:368-372` |
| `<image-dir>/manga_translator_work/translations/<stem>_translated.<output_format>` | 文本导出；扩展名同上 | 路径管理器和导出工作流 | 导出翻译和导出前存在性检查 | `manga_translator/utils/path_manager.py:204-227`; `manga_translator/mode/local.py:108-114`; `desktop_qt_ui/services/workflow_service.py:456-460` |
| `<image-dir>/manga_translator_work/yolo_labels/<stem>.txt` | UTF-8 YOLO 一行一框；至少 5 个数为类别 + bbox，至少 9 个数为 OBB；坐标可归一化，类别在导入时忽略 | 用户准备，路径管理器定位 | `import_yolo_labels` 检测工作流 | `manga_translator/utils/path_manager.py:230-285`; `manga_translator/detection/imported_yolo.py:13-115` |
| `<image-dir>/manga_translator_work/inpainted/<stem>_inpainted.<原图扩展名>` | 图像；保留原始扩展名 | 核心翻译器或编辑器导出 | `load_text` 可复用已有修复图；编辑器导出保存 | `manga_translator/utils/path_manager.py:129-149, 388-406`; `manga_translator/manga_translator.py:1069-1072, 3650-3653`; `desktop_qt_ui/editor/controller_export_service.py:346-350` |
| `<image-dir>/manga_translator_work/paint_overlay/<stem>_overlay.png` | PNG，专门保留 alpha 的画笔覆盖层 | 路径管理器和编辑器会话 | 编辑器画笔层保存、加载与 JSON 的 `paint_overlay` / `stamp_overlay` 关联 | `manga_translator/utils/path_manager.py:409-438`; `desktop_qt_ui/editor/session.py:240-243`; `manga_translator/manga_translator.py:862-868, 1283-1300` |
| `<image-dir>/manga_translator_work/editor_base/<原图文件名>` | 上色/超分后供编辑器使用的底图，保留图像扩展名 | 核心翻译器 | 编辑器文档服务查找并丢弃过期底图 | `manga_translator/utils/path_manager.py:102-127`; `manga_translator/manga_translator.py:1074-1077`; `desktop_qt_ui/editor/controller_document_service.py:183-187` |
| `<image-dir>/manga_translator_work/translated_images/` | 目录，不是固定单文件格式 | 替换翻译工作流 | 从对应已翻译图、工作目录 JSON 或旧位置寻找源 JSON | `manga_translator/utils/path_manager.py:302-364`; `manga_translator/utils/replace_translation.py:29-32` |
| `<image-dir>/manga_translator_work/psd/<stem>.psd` | Photoshop PSD | Photoshop 导出器 | 开启 `export_editable_psd` 时输出 | `manga_translator/utils/photoshop_export.py:645-679`; `manga_translator/manga_translator.py:687-700` |
| `<image-dir>/manga_translator_work/psd/<stem>_photoshop_script.jsx` | Photoshop ExtendScript；verbose 或 `psd_script_only` 时保留 | Photoshop 导出器 | 供 Photoshop 执行/排错；非 verbose 且非 script-only 时会删除临时脚本 | `manga_translator/utils/photoshop_export.py:787-804, 900-903` |
| 实际翻译输出 `<output-dir>/<stem>.<format>` | PNG、JPG/JPEG、WEBP、AVIF、BMP、TIFF/TIF、HEIC/HEIF；未指定则保留输入格式 | 核心翻译器 | 普通结果、替换翻译及编辑器导出 | `manga_translator/manga_translator.py:540-633`; `manga_translator/image_formats.py:8-61` |
| `<json-file>.bak` | 原逐图 JSON 的同目录备份，字节/内容由批量编辑引擎管理 | 批量管理页面/引擎 | 批量写入前备份；恢复时覆盖 JSON 并删除 `.bak` | `desktop_qt_ui/ui/secondary_pages/batch_edit_panel.py:246-279, 627-642, 667-695` |

### 逐图 JSON 的安全与兼容性

- `mask_raw` 在落盘时是 base64 编码的 PNG；加载时也兼容内存 `ndarray` 或数值列表。`mask_is_refined` 指示是否可跳过再次细化。该 JSON 同时含原文、译文、坐标、样式、覆盖层和可能的 `last_export_dir`，不得作为可公开的最小样例。
- 读取新 JSON 失败时可兼容旧列表形式；区域解析失败会禁止该图片的 JSON 回写以防丢失项目数据。面向用户的字段样例仍必须做序列化/反序列化运行验证。
- `save_to_source_dir` 的实现另有源码注释指向工作目录下的 `result/`，但真实的普通输出路径还取决于 `save_info`；正文不得承诺固定目录，直到以实际工作流验证。

## 调试与诊断产物

`MangaTranslator._result_path()` 将 verbose 模式的中间产物写到 `BASE_PATH/result/[result_sub_folder/]<image_subfolder>/`；非 verbose 的 Web/server 分支和调用方传入的绝对输出路径有不同分支。所有下面的 `result/` 产物均可能含完整页面图、识别文本、框坐标或翻译结果。

| 产物 | 触发条件（静态） | 内容/格式 | 源码依据 |
| --- | --- | --- | --- |
| `input.png`、`final.png` | `verbose` | 输入页与最终页 PNG | `manga_translator/manga_translator.py:1541-1549, 4248-4257` |
| `mask_raw.png`、`bboxes_unfiltered.png`、`bboxes_unfiltered_labeled.png`、`bboxes.png` | `verbose`；带标签版本还要求模型辅助合并开关 | 原始蒙版置信热图、检测框、标签和合并后文本区域图 | `manga_translator/manga_translator.py:4549-4566, 4567-4607, 1916-1957` |
| `bboxes_with_scores.png`、`mask_binary.png`、`hybrid_detection_boxes.png` | 检测器返回相应调试数据；混合图还要求 YOLO OBB | 检测评分框、二值蒙版或混合检测框 | `manga_translator/manga_translator.py:1757-1798` |
| `rearrange_<n>.png`、`yolo_rearrange_<n>.png` | `verbose` 且检测重排实际发生 | 方形批次重排图 | `manga_translator/utils/generic.py:1655-1702`; `manga_translator/detection/yolo_obb.py:428-432` |
| `mask_bubble_clip_debug.png` | `verbose`、提供 `debug_path_fn` 且气泡裁剪代码路径执行 | 气泡限制膨胀前后和保护区域图 | `manga_translator/mask_refinement/__init__.py:270-303` |
| `inpaint_input.png`、`mask_final.png`、`inpainted.png` | `verbose`；对应蒙版细化/修复阶段成功可用 | 修复输入、最终蒙版和修复结果 PNG | `manga_translator/manga_translator.py:5230-5279` |
| `balloon_fill_boxes.png`、`chinese_linebreak_debug.json` | `verbose` 风格的调试路径；前者要求渲染器返回 debug 图，后者要求存在语义断句记录 | 气泡排版框 PNG；含断句记录的 JSON | `manga_translator/manga_translator.py:3160-3204` |
| `replace_debug_match.jpg`、`debug_extracted_text.png` | 替换翻译且 `verbose` | 匹配框/重叠信息 JPG，及抽取文字 PNG | `manga_translator/utils/replace_translation.py:317-369, 556-562` |
| 替换翻译的 `inpainted.png` | 替换翻译且 `verbose` | 替换流程的修复调试 PNG | `manga_translator/utils/replace_translation.py:481-486` |
| `ws_final.png`、`ws_render_in.png`、`ws_render_out.png`、`ws_mask.png`、`ws_inmask.png`、`ws_output.png` | WebSocket 模式且 `verbose` | WebSocket 渲染各中间/最终 PNG | `manga_translator/mode/ws.py:157-159, 299-310` |
| `<stem>_photoshop_script.jsx` | verbose 或 `psd_script_only` | Photoshop 自动化脚本；可能含图层文本和本地文件路径 | `manga_translator/utils/photoshop_export.py:787-804` |
| `result/log_<timestamp>.txt` | local CLI 初始化日志文件时 | 运行日志文本 | `manga_translator/mode/local.py:137-170` |

### 与下一项的边界

上表固定了本任务能以静态源码确认的命名、格式和触发条件；并未宣称“每个实际运行均生成全部文件”。`TODO.md` 的下一项专门要求追踪所有 `_result_path` 直接调用、`result_path_fn` / `debug_path_fn` 回调和同目录手工路径，并以完整调用点清单验收；该项保持未开工。

## 敏感信息规则

| 数据类别 | 可能出现的位置 | 对 Wiki、截图、日志和共享包的具体规则 | 依据 |
| --- | --- | --- | --- |
| API 密钥、认证密钥、令牌 | `.env`；环境变量槽；API 请求或导入配置 | 不写值、不复制到示例、不在截图中显示；用明显虚构的占位文本。不要因 UI 密码框或局部服务端清洗而认定其他日志安全。 | `desktop_qt_ui/ui/main_page/env_management.py:190-220`; `doc/wiki/PAGE_GUIDELINES.md:23-24` |
| 自定义 API 请求参数 | `config/custom_api_params.json` | 仅记录字段结构/所属 provider；任何自定义 header、Bearer 值或用户供应商地址均按敏感处理，除非明确为公开测试地址。 | `manga_translator/custom_api_params.py:37`; `manga_translator/config.py:489-492` |
| 管理员密码、账号、密码哈希、会话令牌 | `manga_translator/server/data/admin_config.json`、`accounts.json`、会话请求头和服务日志 | 账号名、哈希和令牌都不公开；密码哈希不是示例数据。只记录认证头名称 `X-Session-Token`，不记录其值。 | `manga_translator/server_paths.py:8-19`; `manga_translator/server/core/models.py:81-113`; `manga_translator/server/core/account_service.py:350-393`; `manga_translator/server/core/middleware.py:94-97` |
| 管理员环境密码 | `MANGA_TRANSLATOR_ADMIN_PASSWORD` 与管理员设置 | 不展示环境变量值、配置值或启动日志；仅写存在最短长度校验这一行为。 | `manga_translator/server/core/config_manager.py:161-186` |
| 用户图片、原文、译文、OCR 文本、框坐标、蒙版和富文本 | 输入/输出图片、`manga_translator_work/`、JSON、TXT、调试 PNG/JPG/JSON、PSD/JSX | 默认视为用户内容；公开前逐文件检查并使用可公开样例。调试目录不能直接打包上传，`mask_raw` base64 也不等于脱敏。 | `manga_translator/manga_translator.py:832-872, 1375-1524`; `doc/wiki/PAGE_GUIDELINES.md:23-24` |
| 私有提示词、参考图、绝对路径 | `dict/*.yaml`、自定义 prompt JSON/YAML、`last_export_dir`、上色参考图路径、PS/JSX | 正文只说明 schema 和相对示例；不得展示真实提示词、绝对个人路径或私人参考图片。对含 `reference_images` 的上色提示词逐项检查。 | `manga_translator/colorization/prompt_loader.py:16-113`; `manga_translator/manga_translator.py:862-872`; `doc/wiki/PAGE_GUIDELINES.md:23-24` |
| 服务器返回或审计配置 | 服务器任务详情、错误/审计记录 | `TranslationIntegration` 会递归掩盖键名含 `api_key`、`api_secret`、`password`、`token` 或 `key` 的配置值，但这只是该输出边界；文档仍不应复用运行记录。 | `manga_translator/server/core/translation_integration.py:323-352` |

## 运行时未决项

- 当前机器上哪些文件/目录实际生成、图像子目录命名和 `result_sub_folder` 取值，必须用脱敏一次性运行验证；静态 `_result_path()` 有 verbose、Web/server 与调用方绝对路径分支。
- 每种工作流、无文本页、失败/取消、并发和 WebSocket 模式会跳过不同阶段；不能把上表条件产物写成每次都有。
- `translation_template.json` 配置的任意安全扩展名是否与导入/导出 UI 的实际可用格式完全一致，需用最小样例运行验证。
- `mask_raw`、逐图 JSON 的完整字段集合，以及旧 list/new dict 的往返兼容，需通过实际序列化/反序列化测试确认。
- Git 忽略规则确认的是本仓库当前规则，不能证明发行包、用户工作目录或服务器部署路径的 ACL、备份策略和保留期；这些需在对应安装/Web 验证任务中确认。
