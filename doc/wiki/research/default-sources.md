# Phase 0：三层默认值来源与差异清单

> 范围：只固定配置模型、Qt 桌面模型和仓库跟踪的发行模板三层静态默认值；不把本机 `config/config.json`、`.env`、预设或服务器运行状态当作发行默认值。
>
> 取证日期：2026-08-06。

## 固定的三种来源

| 标识 | 固定来源 | 取得方式 | 字段数 | 代码依据 |
| --- | --- | --- | ---: | --- |
| Core | `Config()` | `manga_translator.config.Config().model_dump(mode="json")` | 120 | `manga_translator/config.py:152`、`manga_translator/config.py:250`、`manga_translator/config.py:262`、`manga_translator/config.py:335`、`manga_translator/config.py:364`、`manga_translator/config.py:378`、`manga_translator/config.py:388`、`manga_translator/config.py:427`、`manga_translator/config.py:465` |
| Qt | `AppSettings()` | `desktop_qt_ui.core.config_models.AppSettings().model_dump(mode="json")` | 131 | `desktop_qt_ui/core/config_models.py:12`、`desktop_qt_ui/core/config_models.py:26`、`desktop_qt_ui/core/config_models.py:46`、`desktop_qt_ui/core/config_models.py:61`、`desktop_qt_ui/core/config_models.py:69`、`desktop_qt_ui/core/config_models.py:110`、`desktop_qt_ui/core/config_models.py:117`、`desktop_qt_ui/core/config_models.py:123`、`desktop_qt_ui/core/config_models.py:168`、`desktop_qt_ui/core/config_models.py:219` |
| Release | `config/config-example.json` | 直接解析仓库跟踪的 JSON 模板 | 131 | `config/config-example.json:1`；Qt 把它作为默认配置路径：`desktop_qt_ui/services/config_service.py:886` |

比较以 Pydantic 的 JSON 模式序列化进行，枚举因此以存储值比较。`—` 表示该层没有该键，**不是**空值或自动回退。各来源的叶子字段数为 120/131/131；三层并集为 143 键，其中 68 键至少有两层不同。按成对比较，Core vs Qt 有 53 个不同字段（含各自独有字段），Qt vs Release 有 21 个，Core vs Release 有 65 个。

## 完整默认值矩阵

| Key | Core `Config()` | Qt `AppSettings()` | Release `config-example.json` | Difference |
| --- | --- | --- | --- | --- |
| `app.current_preset` | — | `"默认"` | `"默认"` | yes |
| `app.editor_auto_export_on_switch` | — | `true` | `true` | yes |
| `app.editor_auto_rich_text_rules` | — | `true` | `true` | yes |
| `app.editor_center_scale_enabled` | — | `false` | `false` | yes |
| `app.editor_rich_text_popup_enabled` | — | `true` | `true` | yes |
| `app.editor_snap_enabled` | — | `false` | `false` | yes |
| `app.favorite_folders` | — | `null` | `null` | yes |
| `app.last_open_dir` | — | `"."` | `"."` | yes |
| `app.last_output_path` | — | `""` | `""` | yes |
| `app.saved_colors` | — | `null` | `null` | yes |
| `app.saved_rich_text_presets` | — | `null` | `null` | yes |
| `app.saved_style_presets` | — | `null` | `null` | yes |
| `app.theme` | — | `"light"` | `"light"` | yes |
| `app.theme_user_preference` | — | `"light"` | `"light"` | yes |
| `app.ui_language` | — | `"auto"` | `"auto"` | yes |
| `app.unload_models_after_translation` | — | `false` | `false` | yes |
| `cli.attempts` | `-1` | `-1` | `3` | yes |
| `cli.batch_concurrent` | `false` | `false` | `false` |  |
| `cli.batch_size` | `1` | `1` | `3` | yes |
| `cli.colorize_only` | — | `false` | `false` | yes |
| `cli.context_size` | `3` | `3` | `3` |  |
| `cli.disable_onnx_gpu` | `false` | `false` | `false` |  |
| `cli.export_editable_psd` | `false` | `false` | `false` |  |
| `cli.format` | `null` | `"不指定"` | `"不指定"` | yes |
| `cli.generate_and_export` | — | `false` | `false` | yes |
| `cli.ignore_errors` | `false` | `false` | `false` |  |
| `cli.inpaint_only` | — | `false` | `false` | yes |
| `cli.load_text` | — | `false` | `false` | yes |
| `cli.overwrite` | `false` | `true` | `true` | yes |
| `cli.psd_script_only` | `false` | `false` | `false` |  |
| `cli.replace_translation` | `false` | `false` | `false` |  |
| `cli.save_quality` | `100` | `100` | `100` |  |
| `cli.save_text` | `false` | `true` | `true` | yes |
| `cli.save_to_source_dir` | `false` | `false` | `false` |  |
| `cli.skip_no_text` | `false` | `false` | `false` |  |
| `cli.template` | — | `false` | `false` | yes |
| `cli.translate_json_only` | `false` | `false` | `false` |  |
| `cli.upscale_only` | — | `false` | `false` | yes |
| `cli.use_gpu` | `true` | `true` | `true` |  |
| `cli.verbose` | `false` | `false` | `false` |  |
| `colorizer.ai_colorizer_history_pages` | `0` | `0` | `0` |  |
| `colorizer.colorization_size` | `576` | `576` | `2048` | yes |
| `colorizer.colorizer` | `"none"` | `"none"` | `"none"` |  |
| `colorizer.denoise_sigma` | `30` | `30` | `30` |  |
| `detector.box_threshold` | `0.7` | `0.5` | `0.5` | yes |
| `detector.det_rearrange_min_effective_short_side` | `341` | `341` | `341` |  |
| `detector.detection_size` | `2048` | `2048` | `2048` |  |
| `detector.detector` | `"default"` | `"default"` | `"default"` |  |
| `detector.import_yolo_labels` | `false` | `false` | `false` |  |
| `detector.min_box_area_ratio` | `0.0009` | `0.0009` | `0` | yes |
| `detector.sfx_filter_include_bubble_text` | `false` | `false` | `false` |  |
| `detector.text_threshold` | `0.5` | `0.5` | `0.5` |  |
| `detector.unclip_ratio` | `2.3` | `2.5` | `2.5` | yes |
| `detector.use_sfx_filter` | `false` | `false` | `false` |  |
| `detector.use_yolo_obb` | `false` | `false` | `true` | yes |
| `detector.yolo_obb_conf` | `0.4` | `0.4` | `0.4` |  |
| `detector.yolo_obb_overlap_threshold` | `0.1` | `0.1` | `0.1` |  |
| `filter_text_enabled` | — | `true` | `false` | yes |
| `force_simple_sort` | `false` | — | — | yes |
| `inpainter.force_use_torch_inpainting` | `false` | `false` | `false` |  |
| `inpainter.inpainter` | `"lama_large"` | `"lama_mpe"` | `"lama_large"` | yes |
| `inpainter.inpainting_precision` | `"bf16"` | `"fp32"` | `"fp32"` | yes |
| `inpainter.inpainting_size` | `2048` | `2048` | `2048` |  |
| `inpainter.per_block_inpainting` | `false` | `false` | `false` |  |
| `inpainter.solid_fill_pure_bubbles` | `false` | `false` | `false` |  |
| `kernel_size` | `3` | `3` | `3` |  |
| `mask_dilation_offset` | `20` | `70` | `50` | yes |
| `ocr.ai_ocr_concurrency` | `1` | `1` | `10` | yes |
| `ocr.ai_ocr_custom_prompt` | `null` | `null` | `null` |  |
| `ocr.ignore_bubble` | `0.0` | `0.0` | `0.0` |  |
| `ocr.limit_mask_dilation_to_bubble_mask` | `false` | `false` | `true` | yes |
| `ocr.merge_edge_ratio_threshold` | `0.0` | `0.0` | `0.0` |  |
| `ocr.merge_gamma` | `0.8` | `0.8` | `0.8` |  |
| `ocr.merge_sigma` | `2.5` | `2.5` | `2.5` |  |
| `ocr.merge_special_require_full_wrap` | `true` | `true` | `true` |  |
| `ocr.min_text_length` | `0` | `0` | `0` |  |
| `ocr.model_bubble_overlap_threshold` | `0.1` | `0.1` | `0.1` |  |
| `ocr.ocr` | `"48px"` | `"48px"` | `"48px"` |  |
| `ocr.ocr_vl_custom_prompt` | `null` | `null` | `null` |  |
| `ocr.ocr_vl_language_hint` | `"auto"` | `"auto"` | `"Japanese"` | yes |
| `ocr.prob` | `null` | `0.1` | `0.1` | yes |
| `ocr.secondary_ocr` | `"48px"` | `"mocr"` | `"mocr"` | yes |
| `ocr.use_hybrid_ocr` | `false` | `true` | `false` | yes |
| `ocr.use_model_bubble_filter` | `false` | `false` | `false` |  |
| `ocr.use_model_bubble_repair_intersection` | `false` | `false` | `false` |  |
| `render.ai_renderer_concurrency` | `1` | `1` | `1` |  |
| `render.alignment` | `"auto"` | `"auto"` | `"auto"` |  |
| `render.bubble_layout_english` | `false` | `false` | `false` |  |
| `render.center_text_in_bubble` | `false` | `false` | `false` |  |
| `render.check_br_and_retry` | `false` | `false` | `false` |  |
| `render.direction` | `"auto"` | `"auto"` | `"auto"` |  |
| `render.disable_auto_wrap` | `false` | `true` | `false` | yes |
| `render.disable_font_border` | `false` | `false` | `false` |  |
| `render.enable_template_alignment` | `false` | `false` | `false` |  |
| `render.font_color` | `null` | `null` | `null` |  |
| `render.font_family` | `null` | `""` | `"Microsoft YaHei UI"` | yes |
| `render.font_scale_ratio` | `1.0` | `1.0` | `1.0` |  |
| `render.font_size` | `null` | `null` | `null` |  |
| `render.font_size_minimum` | `-1` | `0` | `0` | yes |
| `render.font_size_offset` | `0` | `0` | `0` |  |
| `render.force_strict_layout` | `false` | — | — | yes |
| `render.layout_mode` | `"smart_scaling"` | `"smart_scaling"` | `"balloon_fill"` | yes |
| `render.letter_spacing` | `null` | `1.0` | `1.0` | yes |
| `render.line_spacing` | `null` | `1.0` | `1.0` | yes |
| `render.lowercase` | `false` | `false` | `false` |  |
| `render.max_font_size` | `0` | `0` | `0` |  |
| `render.no_hyphenation` | `false` | `false` | `false` |  |
| `render.optimize_line_breaks` | `false` | `false` | `false` |  |
| `render.paste_mask_dilation_pixels` | `10` | `10` | `10` |  |
| `render.remove_linebreak_punctuation` | `false` | `false` | `false` |  |
| `render.renderer` | `"default"` | `"default"` | `"default"` |  |
| `render.rtl` | `true` | `true` | `true` |  |
| `render.semantic_linebreak` | `false` | `false` | `false` |  |
| `render.strict_smart_scaling` | `false` | `false` | `false` |  |
| `render.stroke_width` | `0.07` | `0.07` | `0.07` |  |
| `render.uppercase` | `false` | `false` | `false` |  |
| `translator.convert_to_simplified` | `false` | `false` | `false` |  |
| `translator.convert_to_traditional` | `false` | `false` | `false` |  |
| `translator.enable_post_translation_check` | `false` | — | — | yes |
| `translator.enable_streaming` | `true` | `true` | `false` | yes |
| `translator.extract_glossary` | `false` | `false` | `false` |  |
| `translator.high_quality_prompt_path` | `null` | `"dict/prompt_example.yaml"` | `"dict/prompt_example.yaml"` | yes |
| `translator.keep_lang` | `"none"` | `"none"` | `"none"` |  |
| `translator.max_requests_per_minute` | `0` | `0` | `0` |  |
| `translator.no_text_lang_skip` | `false` | `false` | `false` |  |
| `translator.post_check_max_retry_attempts` | `3` | — | — | yes |
| `translator.post_check_repetition_threshold` | `20` | — | — | yes |
| `translator.post_check_target_lang_threshold` | `0.5` | — | — | yes |
| `translator.remove_trailing_period` | `false` | `false` | `true` | yes |
| `translator.selective_translation` | `null` | — | — | yes |
| `translator.skip_lang` | `null` | — | — | yes |
| `translator.target_lang` | `"ENG"` | `"CHS"` | `"CHS"` | yes |
| `translator.translator` | `"openai_hq"` | `"openai_hq"` | `"openai"` | yes |
| `translator.translator_chain` | `null` | — | — | yes |
| `translator.user_api_base` | `null` | — | — | yes |
| `translator.user_api_key` | `null` | — | — | yes |
| `translator.user_api_model` | `null` | — | — | yes |
| `upscale.realcugan_model` | `null` | `null` | `null` |  |
| `upscale.revert_upscaling` | `false` | `false` | `false` |  |
| `upscale.tile_size` | `null` | `null` | `400` | yes |
| `upscale.upscale_ratio` | `null` | `null` | `null` |  |
| `upscale.upscaler` | `"esrgan"` | `"esrgan"` | `"mangajanai"` | yes |
| `use_custom_api_params` | `false` | `false` | `true` | yes |

## 差异结论

这张矩阵固定以下会改变处理行为或配置兼容性的差异；后续参数页必须分别注明来源，不能归并成一个“默认值”。

| 范围 | Core | Qt | Release |
| --- | --- | --- | --- |
| 处理入口 | `translator.translator="openai_hq"`、`target_lang="ENG"`、流式开启 | `openai_hq`、`CHS`、流式开启 | `openai`、`CHS`、流式关闭 |
| 检测 | `box_threshold=0.7`、`unclip_ratio=2.3`、最小面积 `0.0009`、YOLO OBB 关闭 | `0.5`、`2.5`、`0.0009`、关闭 | `0.5`、`2.5`、`0`、开启 |
| OCR | 混合关闭、次 OCR=`48px`、`prob=null`、语言提示 `auto`、并发 1 | 混合开启、次 OCR=`mocr`、`prob=0.1`、语言提示 `auto`、并发 1 | 混合关闭、`mocr`、`0.1`、`Japanese`、并发 10，且气泡蒙版限制开启 |
| 修复与蒙版 | `lama_large`、`bf16`、`mask_dilation_offset=20` | `lama_mpe`、`fp32`、`70` | `lama_large`、`fp32`、`50` |
| 排版 | `font_family=null`、自动换行开启、最小字号 `-1`、`smart_scaling`、行/字间距 `null` | 空字体、自动换行关闭、最小字号 `0`、`smart_scaling`、间距 `1.0` | `Microsoft YaHei UI`、自动换行开启、最小字号 `0`、`balloon_fill`、间距 `1.0` |
| 放大与上色 | `esrgan`、瓦片 `null`、上色尺寸 `576` | `esrgan`、`null`、`576` | `mangajanai`、`400`、`2048` |
| CLI 输出 | 无限重试、批次 1、`format=null`、不覆盖、不保存文本 | 无限重试、批次 1、`不指定`、覆盖、保存文本 | 重试 3、批次 3、`不指定`、覆盖、保存文本 |
| 顶层开关 | `force_simple_sort` 仅 Core，`use_custom_api_params=false` | `filter_text_enabled=true`、`use_custom_api_params=false` | `filter_text_enabled=false`、`use_custom_api_params=true` |

Core 独有的字段包括 `force_simple_sort`、`render.force_strict_layout`、翻译链/选择性翻译/用户 API 覆盖以及译后检查字段；Qt/Release 独有的是 16 个 `app.*` 字段和 6 个 Qt 工作流开关（`cli.colorize_only`、`generate_and_export`、`inpaint_only`、`load_text`、`template`、`upscale_only`）。这些不是缺失时自动由另一层补齐的同义键。

## 加载链与运行态未决项

- 桌面端先构造 `AppSettings()`，再以逐键校验的深合并加载文件；启动优先级是用户 `config/config.json` > `config/config-example.json` > Qt 模型默认。依据：`desktop_qt_ui/services/config_service.py:172`、`desktop_qt_ui/services/config_service.py:361`、`desktop_qt_ui/services/config_service.py:904`。因此矩阵只能固定模板/代码来源，不能推断某台机器启动后的有效值。
- Qt 在开发环境保存“默认配置”时还有输出归一化：最小检测框面积会改写为 `0`，并改写一批 `app`、渲染、提示词和混合 OCR 值。依据：`desktop_qt_ui/services/config_service.py:436`。本次没有写文件或启动 GUI，尚未验证现有模板是否由该分支在当前版本生成。
- `.env` 在 Qt 服务初始化时载入，API 的有效 Key/Base/Model 因而不在三层 JSON 矩阵中。依据：`desktop_qt_ui/services/config_service.py:150`。用户 API 覆盖属于 Core 的字段，Qt/Release 没有序列化这些字段；其实际优先级必须由带脱敏凭据的运行验证确认。
- 服务端把 `config/config.json` 当作运行配置；不存在时才从 `config/config-example.json` 复制，然后再以 `Config` 校验。依据：`manga_translator/server/core/config_manager.py:207`、`manga_translator/server/core/config_manager.py:222`、`manga_translator/server/core/config_manager.py:327`。服务 `/config/defaults` 还会过滤 Qt 部分并附加配额/权限默认值，不等于这里的原始 Release JSON。依据：`manga_translator/server/routes/config.py:201`。
- Docker 先调用运行时文件工厂，再备份 `/app/config`；挂载空目录时入口脚本恢复该快照。依据：`packaging/Dockerfile:97`、`packaging/docker-entrypoint.sh:4`。本次未构建镜像，故不能声明最终镜像内的文件哈希与仓库模板完全一致。
- 发行版配置目录位于可执行文件旁，不能从 PyInstaller 内部目录推断其内容。依据：`manga_translator/runtime_paths.py:12`、`manga_translator/runtime_paths.py:20`。当前工作区的 `config/config.json`、预设和历史 `manga_translator/server/server_config.json` 被忽略，均排除在结论之外。

## 可复现核对

在仓库根目录运行以下命令可重新计算三层叶子字段数；完整默认值矩阵的键并集为 143 行。该命令只读取跟踪的模板和模型默认值，不读取用户配置：

```powershell
@'
import json, sys
from pathlib import Path
sys.path.insert(0, 'desktop_qt_ui')
from manga_translator.config import Config
from core.config_models import AppSettings

def flatten(value):
    if not isinstance(value, dict):
        return [value]
    return [leaf for child in value.values() for leaf in flatten(child)]

sources = {
    'Core': Config().model_dump(mode='json'),
    'Qt': AppSettings().model_dump(mode='json'),
    'Release': json.loads(Path('config/config-example.json').read_text(encoding='utf-8')),
}
counts = {name: len(flatten(value)) for name, value in sources.items()}
assert counts == {'Core': 120, 'Qt': 131, 'Release': 131}, counts
print(counts)
'@ | uv run python -
```

用于本清单的完整比较会递归展开上述三个对象；验证时应断言叶子键数为 Core 120、Qt 131、Release 131，以及上表中每个 `yes` 行的三个值。
