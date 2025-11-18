
"""
应用业务逻辑层
处理应用的核心业务逻辑，与UI层分离
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from manga_translator.config import (
    Alignment,
    Colorizer,
    Detector,
    Direction,
    Inpainter,
    InpaintPrecision,
    Ocr,
    Renderer,
    Translator,
    Upscaler,
)
from manga_translator.save import OUTPUT_FORMATS
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QFileDialog, QListView, QTreeView

from services import (
    get_config_service,
    get_file_service,
    get_logger,
    get_state_manager,
    get_translation_service,
)
from services.state_manager import AppStateKey


@dataclass
class AppConfig:
    """应用配置信息"""
    window_size: tuple = (1200, 800)
    theme: str = "dark"
    language: str = "zh_CN"
    auto_save: bool = True
    max_recent_files: int = 10

class MainAppLogic(QObject):
    """主页面业务逻辑控制器"""
    files_added = pyqtSignal(list)
    files_cleared = pyqtSignal()
    file_removed = pyqtSignal(str)
    config_loaded = pyqtSignal(dict)
    output_path_updated = pyqtSignal(str)
    task_completed = pyqtSignal(list)
    task_file_completed = pyqtSignal(dict)
    log_message = pyqtSignal(str)
    render_setting_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.logger = get_logger(__name__)
        self.config_service = get_config_service()
        self.translation_service = get_translation_service()
        self.file_service = get_file_service()
        self.state_manager = get_state_manager()

        self.thread = None
        self.worker = None
        self.saved_files_count = 0
        self.saved_files_list = []  # 收集所有保存的文件路径

        self.source_files: List[str] = [] # Holds both files and folders
        self.file_to_folder_map: Dict[str, Optional[str]] = {} # 记录文件来自哪个文件夹
        self.display_name_maps = None

        self.app_config = AppConfig()
        self.logger.info("主页面应用业务逻辑初始化完成")

    def _create_folder_source_file(self, output_folder: str, source_folder: str):
        """
        在输出文件夹内创建源路径文件，只记录原始文件夹的完整路径
        
        Args:
            output_folder: 输出文件夹路径
            source_folder: 源文件夹完整路径
        """
        try:
            # 创建源路径文件
            source_file_path = os.path.join(output_folder, '_source_path.txt')
            
            # 写入原始文件夹的完整路径
            with open(source_file_path, 'w', encoding='utf-8') as f:
                f.write(os.path.normpath(source_folder))
            
            self.logger.info(f"✅ 已创建源路径文件: {source_file_path}")
            
        except Exception as e:
            self.logger.error(f"创建源路径文件失败: {e}")

    def _create_source_files_for_all_folders(self, output_folder: str):
        """
        为所有已处理的文件夹创建源路径文件（在任务开始前调用）
        
        Args:
            output_folder: 输出根目录
        """
        try:
            # 收集所有唯一的源文件夹
            source_folders = set()
            for source_file, source_folder in self.file_to_folder_map.items():
                if source_folder:
                    source_folders.add(source_folder)
            
            # 为每个文件夹创建输出目录和源路径文件
            for source_folder in source_folders:
                folder_output_root = os.path.join(output_folder, os.path.basename(source_folder))
                # 提前创建输出文件夹
                os.makedirs(folder_output_root, exist_ok=True)
                
                source_file_path = os.path.join(folder_output_root, '_source_path.txt')
                if not os.path.exists(source_file_path):
                    self._create_folder_source_file(folder_output_root, source_folder)
            
            if source_folders:
                self.logger.info(f"✅ 已为 {len(source_folders)} 个文件夹创建源路径文件")
                
        except Exception as e:
            self.logger.error(f"批量创建源路径文件失败: {e}")

    @staticmethod
    def get_source_folder_from_output(output_folder: str) -> Optional[str]:
        """
        从输出文件夹读取原始源路径（读取"出生证明"）
        
        Args:
            output_folder: 输出文件夹路径
            
        Returns:
            原始文件夹的完整路径，如果文件不存在返回 None
        """
        source_file = os.path.join(output_folder, '_source_path.txt')
        if os.path.exists(source_file):
            try:
                with open(source_file, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception:
                return None
        return None

    @pyqtSlot(dict)
    def on_file_completed(self, result):
        """处理单个文件处理完成的信号并保存"""
        if not result.get('success'):
            self.logger.error(f"Skipping save for failed item: {result.get('original_path')}")
            return

        try:
            config = self.config_service.get_config()
            output_format = config.cli.format
            save_quality = config.cli.save_quality
            output_folder = config.app.last_output_path

            if not output_folder:
                self.logger.error("输出目录未设置，无法保存文件。")
                self.state_manager.set_status_message("错误：输出目录未设置！")
                return

            original_path = result['original_path']
            base_filename = os.path.basename(original_path)

            # 检查文件是否来自文件夹
            source_folder = self.file_to_folder_map.get(original_path)

            # ✅ 检查是否是导出原文模式（只生成txt，不保存图片）
            # 使用config.dict()获取配置字典，与代码库其他部分保持一致
            config_dict = config.dict() if hasattr(config, 'dict') else {}
            cli_config = config_dict.get('cli', {})
            is_template_mode = cli_config.get('template', False) and cli_config.get('save_text', False)
            has_image_data = result.get('image_data') is not None

            # 如果是导出原文模式且没有图片数据，不保存图片（源路径文件已在任务开始前创建）
            if is_template_mode and not has_image_data:
                return

            # 正常模式：需要图片数据才能保存
            if not has_image_data:
                self.logger.error(f"Skipping save for item without image data: {result.get('original_path')}")
                return

            if source_folder:
                # 文件来自文件夹，保持相对路径结构
                parent_dir = os.path.normpath(os.path.dirname(original_path))
                relative_path = os.path.relpath(parent_dir, source_folder)
                
                # Normalize path and avoid adding '.' as a directory component
                if relative_path == '.':
                    final_output_folder = os.path.join(output_folder, os.path.basename(source_folder))
                else:
                    final_output_folder = os.path.join(output_folder, os.path.basename(source_folder), relative_path)
                final_output_folder = os.path.normpath(final_output_folder)
            else:
                # 文件是单独添加的，直接保存到输出目录
                final_output_folder = output_folder

            # 确定文件扩展名
            if output_format and output_format != "不指定":
                file_extension = f".{output_format}"
                output_filename = os.path.splitext(base_filename)[0] + file_extension
            else:
                # 保持原扩展名
                output_filename = base_filename

            final_output_path = os.path.join(final_output_folder, output_filename)

            os.makedirs(final_output_folder, exist_ok=True)

            save_kwargs = {}
            image_to_save = result['image_data']

            # Convert RGBA to RGB for JPEG format
            if final_output_path.lower().endswith(('.jpg', '.jpeg')):
                if image_to_save.mode == 'RGBA':
                    image_to_save = image_to_save.convert('RGB')
                save_kwargs['quality'] = save_quality
            elif final_output_path.lower().endswith('.webp'):
                save_kwargs['quality'] = save_quality

            image_to_save.save(final_output_path, **save_kwargs)

            self.saved_files_count += 1
            self.saved_files_list.append(final_output_path)  # 收集保存的文件路径
            self.logger.info(f"成功保存文件: {final_output_path}")
            self.task_file_completed.emit({'path': final_output_path})

        except Exception as e:
            self.logger.error(f"保存文件 {result['original_path']} 时出错: {e}")

    @pyqtSlot(str)
    def on_worker_log(self, message):
        self.log_message.emit(message)

    @pyqtSlot()
    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(None, "选择输出目录")
        if folder:
            self.update_single_config('app.last_output_path', folder)
            self.output_path_updated.emit(folder)

    @pyqtSlot()
    def open_output_folder(self):
        import subprocess
        import sys
        output_dir = self.config_service.get_config().app.last_output_path
        if not output_dir or not os.path.isdir(output_dir):
            self.logger.warning(f"Output path is not a valid directory: {output_dir}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(os.path.realpath(output_dir))
            elif sys.platform == "darwin":
                subprocess.run(["open", output_dir])
            else:
                subprocess.run(["xdg-open", output_dir])
        except Exception as e:
            self.logger.error(f"Failed to open output folder: {e}")

    def open_font_directory(self):
        import subprocess
        import sys
        # fonts目录在_internal里（打包后）或项目根目录（开发时）
        fonts_dir = os.path.join(self.config_service.root_dir, 'fonts')
        try:
            if not os.path.exists(fonts_dir):
                os.makedirs(fonts_dir)
            if sys.platform == "win32":
                os.startfile(fonts_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", fonts_dir])
            else:
                subprocess.run(["xdg-open", fonts_dir])
        except Exception as e:
            self.logger.error(f"Error opening font directory: {e}")

    def open_dict_directory(self):
        import subprocess
        import sys
        # dict目录在_internal里（打包后）或项目根目录（开发时）
        dict_dir = os.path.join(self.config_service.root_dir, 'dict')
        try:
            if not os.path.exists(dict_dir):
                os.makedirs(dict_dir)
            if sys.platform == "win32":
                os.startfile(dict_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", dict_dir])
            else:
                subprocess.run(["xdg-open", dict_dir])
        except Exception as e:
            self.logger.error(f"Error opening dict directory: {e}")

    def get_hq_prompt_options(self) -> List[str]:
        try:
            # dict目录在_internal里（打包后）或项目根目录（开发时）
            dict_dir = os.path.join(self.config_service.root_dir, 'dict')
            if not os.path.isdir(dict_dir):
                return []
            prompt_files = sorted([
                f for f in os.listdir(dict_dir)
                if f.lower().endswith('.json') and f not in [
                    'system_prompt_hq.json',
                    'system_prompt_line_break.json'
                ]
            ])
            return prompt_files
        except Exception as e:
            self.logger.error(f"Error scanning prompt directory: {e}")
            return []

    @pyqtSlot(str, str)
    def save_env_var(self, key: str, value: str):
        self.config_service.save_env_var(key, value)
        self.logger.info(f"Saved {key} to .env file.")

    # region 配置管理
    def load_config_file(self, config_path: str) -> bool:
        try:
            success = self.config_service.load_config_file(config_path)
            if success:
                config = self.config_service.get_config()
                self.state_manager.set_current_config(config)
                self.state_manager.set_state(AppStateKey.CONFIG_PATH, config_path)
                self.logger.info(f"配置文件加载成功: {config_path}")
                self.config_loaded.emit(config.dict())
                if config.app.last_output_path:
                    self.output_path_updated.emit(config.app.last_output_path)
                return True
            else:
                self.logger.error(f"配置文件加载失败: {config_path}")
                return False
        except Exception as e:
            self.logger.error(f"加载配置文件异常: {e}")
            return False
    
    def save_config_file(self, config_path: str = None) -> bool:
        try:
            success = self.config_service.save_config_file(config_path)
            if success:
                self.logger.info("配置文件保存成功")
                return True
            return False
        except Exception as e:
            self.logger.error(f"保存配置文件异常: {e}")
            return False
    
    def update_config(self, config_updates: Dict[str, Any]) -> bool:
        try:
            self.config_service.update_config(config_updates)
            updated_config = self.config_service.get_config()
            self.state_manager.set_current_config(updated_config)
            self.logger.info("配置更新成功")
            return True
        except Exception as e:
            self.logger.error(f"更新配置异常: {e}")
            return False

    def update_single_config(self, full_key: str, value: Any):
        self.logger.debug(f"update_single_config: '{full_key}' = '{value}'")
        try:
            config_obj = self.config_service.get_config()
            keys = full_key.split('.')
            parent_obj = config_obj
            for key in keys[:-1]:
                parent_obj = getattr(parent_obj, key)
            setattr(parent_obj, keys[-1], value)
            
            self.config_service.set_config(config_obj)
            self.config_service.save_config_file()
            self.logger.debug(f"配置已保存: '{full_key}' = '{value}'")

            # 当翻译器设置被更改时，直接更新翻译服务的内部状态
            if full_key == 'translator.translator':
                self.logger.debug(f"翻译器已切换: '{value}'")
                self.translation_service.set_translator(value)

            # 当渲染设置被更改时，通知编辑器刷新
            if full_key.startswith('render.'):
                self.logger.debug(f"渲染设置已更改: '{full_key}'")
                self.render_setting_changed.emit()

        except Exception as e:
            self.logger.error(f"Error saving single config change for {full_key}: {e}")
    # endregion

    # region UI数据提供
    def get_display_mapping(self, key: str) -> Optional[Dict[str, str]]:
        if not hasattr(self, 'display_name_maps') or self.display_name_maps is None:
            self.display_name_maps = {
                "alignment": {"auto": "自动", "left": "左对齐", "center": "居中", "right": "右对齐"},
                "direction": {"auto": "自动", "h": "横排", "v": "竖排"},
                "layout_mode": {
                    'default': "默认模式 (有Bug)",
                    'smart_scaling': "智能缩放 (推荐)",
                    'strict': "严格边界 (缩小字体)",
                    'fixed_font': "固定字体 (扩大文本框)",
                    'disable_all': "完全禁用 (裁剪文本)",
                    'balloon_fill': "填充气泡 (气泡检测)"
                },
                "realcugan_model": {
                    "2x-conservative": "2倍-保守",
                    "2x-conservative-pro": "2倍-保守-Pro",
                    "2x-no-denoise": "2倍-无降噪",
                    "2x-denoise1x": "2倍-降噪1x",
                    "2x-denoise2x": "2倍-降噪2x",
                    "2x-denoise3x": "2倍-降噪3x",
                    "2x-denoise3x-pro": "2倍-降噪3x-Pro",
                    "3x-conservative": "3倍-保守",
                    "3x-conservative-pro": "3倍-保守-Pro",
                    "3x-no-denoise": "3倍-无降噪",
                    "3x-no-denoise-pro": "3倍-无降噪-Pro",
                    "3x-denoise3x": "3倍-降噪3x",
                    "3x-denoise3x-pro": "3倍-降噪3x-Pro",
                    "4x-conservative": "4倍-保守",
                    "4x-no-denoise": "4倍-无降噪",
                    "4x-denoise3x": "4倍-降噪3x",
                },
                "translator": {
                    "youdao": "有道翻译", "baidu": "百度翻译", "deepl": "DeepL", "papago": "Papago",
                    "caiyun": "彩云小译", "openai": "OpenAI",
                    "none": "无", "original": "原文", "sakura": "Sakura",
                    "groq": "Groq", "gemini": "Google Gemini",
                    "openai_hq": "高质量翻译 OpenAI", "gemini_hq": "高质量翻译 Gemini",
                    "offline": "离线翻译", "nllb": "NLLB", "nllb_big": "NLLB (Big)", "sugoi": "Sugoi",
                    "jparacrawl": "JParaCrawl", "jparacrawl_big": "JParaCrawl (Big)", "m2m100": "M2M100",
                    "m2m100_big": "M2M100 (Big)", "mbart50": "mBART50", "qwen2": "Qwen2", "qwen2_big": "Qwen2 (Big)",
                },
                "target_lang": self.translation_service.get_target_languages(),
                "labels": {
                    "filter_text": "过滤文本 (Regex)", "kernel_size": "卷积核大小", "mask_dilation_offset": "遮罩扩张偏移",
                    "translator": "翻译器", "target_lang": "目标语言", "no_text_lang_skip": "不跳过目标语言文本",
                    "gpt_config": "GPT配置文件路径", "high_quality_prompt_path": "高质量翻译提示词", "use_mocr_merge": "使用MOCR合并",
                    "ocr": "OCR模型", "use_hybrid_ocr": "启用混合OCR", "secondary_ocr": "备用OCR",
                    "min_text_length": "最小文本长度", "ignore_bubble": "忽略非气泡文本", "prob": "文本区域最低概率 (prob)",
                    "merge_gamma": "合并-距离容忍度", "merge_sigma": "合并-离群容忍度", "merge_edge_ratio_threshold": "合并-边缘距离比例阈值", "detector": "文本检测器",
                    "detection_size": "检测大小", "text_threshold": "文本阈值", "det_rotate": "旋转图像进行检测",
                    "det_auto_rotate": "旋转图像以优先检测垂直文本行", "det_invert": "反转图像颜色进行检测",
                    "det_gamma_correct": "应用伽马校正进行检测", "use_yolo_obb": "启用YOLO辅助检测", "yolo_obb_conf": "YOLO置信度阈值", "yolo_obb_iou": "YOLO交叉比(IoU)", "yolo_obb_overlap_threshold": "YOLO辅助检测重叠率删除阈值", "box_threshold": "边界框生成阈值", "unclip_ratio": "Unclip比例",
                    "inpainter": "修复模型", "inpainting_size": "修复大小", "inpainting_precision": "修复精度",
                    "renderer": "渲染器", "alignment": "对齐方式", "disable_font_border": "禁用字体边框",
                    "disable_auto_wrap": "AI断句", "font_size_offset": "字体大小偏移量", "font_size_minimum": "最小字体大小",
                    "max_font_size": "最大字体大小", "font_scale_ratio": "字体缩放比例",
                    "stroke_width": "描边宽度比例",
                    "center_text_in_bubble": "AI断句时文本居中",
                    "optimize_line_breaks": "AI断句自动扩大文字", "check_br_and_retry": "AI断句检查",
                    "strict_smart_scaling": "AI断句自动扩大文字下不扩大文本框",
                    "direction": "文本方向", "uppercase": "大写", "lowercase": "小写", "gimp_font": "GIMP字体",
                    "font_path": "字体路径", "no_hyphenation": "禁用连字符", "font_color": "字体颜色",
                    "auto_rotate_symbols": "竖排内横排", "rtl": "从右到左", "layout_mode": "排版模式",
                    "upscaler": "超分模型", "upscale_ratio": "超分倍数", "realcugan_model": "Real-CUGAN模型", "tile_size": "分块大小(0=不分割)", "revert_upscaling": "还原超分", "colorization_size": "上色大小",
                    "denoise_sigma": "降噪强度", "colorizer": "上色模型", "verbose": "详细日志",
                    "attempts": "重试次数", "max_requests_per_minute": "每分钟最大请求数", "ignore_errors": "忽略错误", "use_gpu": "使用 GPU",
                    "use_gpu_limited": "使用 GPU（受限）", "context_size": "上下文页数", "format": "输出格式",
                    "overwrite": "覆盖已存在文件", "skip_no_text": "跳过无文本图像",
                    "save_text": "图片可编辑", "load_text": "导入翻译", "template": "导出原文",
                    "prep_manual": "为手动排版做准备", "save_quality": "图像保存质量", "batch_size": "批量大小",
                    "batch_concurrent": "并发批量处理", "pipeline_mode": "流水线并行模式",
                    "pipeline_line1_concurrency": "流水线并发-线1(检测+OCR)",
                    "pipeline_line2_concurrency": "流水线并发-线2(翻译)",
                    "pipeline_translation_batch_size": "流水线打包-线2(每批图片数)",
                    "pipeline_line3_concurrency": "流水线并发-线3(修复/Inpainting)",
                    "pipeline_line4_concurrency": "流水线并发-线4(渲染+超分)",
                    "enable_long_image_stitching": "启用智能长图拼接",
                    "long_image_max_height": "长图最大高度(像素)",
                    "long_image_bubble_margin": "边界气泡检测范围(像素)",
                    "generate_and_export": "导出翻译", "high_quality_batch_size": "高质量批次大小",
                    "last_output_path": "最后输出路径", "line_spacing": "行间距", "font_size": "字体大小",
                    "YOUDAO_APP_KEY": "有道翻译应用ID", "YOUDAO_SECRET_KEY": "有道翻译应用秘钥",
                    "BAIDU_APP_ID": "百度翻译 AppID", "BAIDU_SECRET_KEY": "百度翻译密钥",
                    "DEEPL_AUTH_KEY": "DeepL 授权密钥", "CAIYUN_TOKEN": "彩云小译 API 令牌",
                    "OPENAI_API_KEY": "OpenAI API 密钥", "OPENAI_MODEL": "OpenAI 模型",
                    "OPENAI_API_BASE": "OpenAI API 地址", "OPENAI_HTTP_PROXY": "HTTP 代理", "OPENAI_GLOSSARY_PATH": "术语表路径",
                    "DEEPSEEK_API_KEY": "DeepSeek API 密钥", "DEEPSEEK_API_BASE": "DeepSeek API 地址", "DEEPSEEK_MODEL": "DeepSeek 模型",
                    "GROQ_API_KEY": "Groq API 密钥", "GROQ_MODEL": "Groq 模型",
                    "GEMINI_API_KEY": "Gemini API 密钥", "GEMINI_MODEL": "Gemini 模型", "GEMINI_API_BASE": "Gemini API 地址",
                    "SAKURA_API_BASE": "SAKURA API 地址", "SAKURA_DICT_PATH": "SAKURA 词典路径", "SAKURA_VERSION": "SAKURA API 版本",
                    "CUSTOM_OPENAI_API_BASE": "自定义 OpenAI API 地址", "CUSTOM_OPENAI_MODEL": "自定义 OpenAI 模型",
                    "CUSTOM_OPENAI_API_KEY": "自定义 OpenAI API 密钥", "CUSTOM_OPENAI_MODEL_CONF": "自定义 OpenAI 模型配置"
                }
            }
        return self.display_name_maps.get(key)

    def get_options_for_key(self, key: str) -> Optional[List[str]]:
        options_map = {
            "format": ["不指定"] + list(OUTPUT_FORMATS.keys()),
            "renderer": [member.value for member in Renderer],
            "alignment": [member.value for member in Alignment],
            "direction": [member.value for member in Direction],
            "upscaler": [member.value for member in Upscaler],
            "upscale_ratio": ["不使用", "2", "3", "4"],
            "realcugan_model": [
                "2x-conservative",
                "2x-conservative-pro",
                "2x-no-denoise",
                "2x-denoise1x",
                "2x-denoise2x",
                "2x-denoise3x",
                "2x-denoise3x-pro",
                "3x-conservative",
                "3x-conservative-pro",
                "3x-no-denoise",
                "3x-no-denoise-pro",
                "3x-denoise3x",
                "3x-denoise3x-pro",
                "4x-conservative",
                "4x-no-denoise",
                "4x-denoise3x",
            ],
            "translator": [member.value for member in Translator],
            "detector": [member.value for member in Detector],
            "colorizer": [member.value for member in Colorizer],
            "inpainter": [member.value for member in Inpainter],
            "inpainting_precision": [member.value for member in InpaintPrecision],
            "ocr": [member.value for member in Ocr],
            "secondary_ocr": [member.value for member in Ocr]
        }
        return options_map.get(key)
    # endregion

    # region 文件管理
    def add_files(self, file_paths: List[str]):
        """
        Adds files/folders to the list for processing.
        """
        new_paths = []
        for path in file_paths:
            norm_path = os.path.normpath(path)
            if norm_path not in self.source_files:
                new_paths.append(norm_path)

        if new_paths:
            self.source_files.extend(new_paths)
            self.logger.info(f"Added {len(new_paths)} files/folders to the list.")
            self.files_added.emit(new_paths)

    def get_last_open_dir(self) -> str:
        path = self.config_service.get_config().app.last_open_dir
        self.logger.info(f"Retrieved last open directory: {path}")
        return path

    def set_last_open_dir(self, path: str):
        self.logger.info(f"Saving last open directory: {path}")
        self.update_single_config('app.last_open_dir', path)

    def add_folder(self):
        """Opens a dialog to select folders (supports multiple selection) and adds their paths to the list."""
        last_dir = self.get_last_open_dir()

        # 使用自定义的现代化文件夹选择器
        from widgets.folder_dialog import select_folders

        folders = select_folders(
            parent=None,
            start_dir=last_dir,
            multi_select=True
        )

        if folders:
            self.set_last_open_dir(folders[0])  # 保存第一个文件夹的路径
            self.add_files(folders)
    
    def add_folders(self):
        """Alias for add_folder for backward compatibility."""
        self.add_folder()

    def remove_file(self, file_path: str):
        try:
            norm_file_path = os.path.normpath(file_path)
            if norm_file_path in self.source_files:
                self.source_files.remove(norm_file_path)
                self.file_removed.emit(file_path)
                self.logger.info(f"Removed path {os.path.basename(file_path)} from list.")
            else:
                self.logger.warning(f"Path not found in list for removal: {file_path}")
        except Exception as e:
            self.logger.error(f"移除路径时发生异常: {e}")

    def clear_file_list(self):
        if not self.source_files:
            return
        # TODO: Add confirmation dialog
        self.source_files.clear()
        self.file_to_folder_map.clear()  # 清空文件夹映射
        self.files_cleared.emit()
        self.logger.info("File list cleared by user.")
    # endregion

    # region 核心任务逻辑
    def _resolve_input_files(self) -> List[str]:
        """
        Expands folders in self.source_files into a list of image files.
        同时记录文件和文件夹的映射关系。
        按文件夹分组排序：先对文件夹进行排序，然后对每个文件夹内的图片排序。
        """
        resolved_files = []
        self.file_to_folder_map.clear()  # 清空旧的映射

        # 分离文件和文件夹
        folders = []
        individual_files = []
        
        for path in self.source_files:
            if os.path.isdir(path):
                folders.append(path)
            elif os.path.isfile(path):
                if self.file_service.validate_image_file(path):
                    individual_files.append(path)
        
        # 对文件夹进行排序
        folders.sort()
        
        # 按文件夹分组处理
        for folder in folders:
            # 获取文件夹中的所有图片
            folder_files = self.file_service.get_image_files_from_folder(folder, recursive=True)
            # 对文件夹内的图片进行排序
            folder_files.sort()
            resolved_files.extend(folder_files)
            # 记录这些文件来自这个文件夹
            for file_path in folder_files:
                self.file_to_folder_map[file_path] = folder
        
        # 处理单独添加的文件
        individual_files.sort()
        for file_path in individual_files:
            resolved_files.append(file_path)
            # 单独添加的文件，不属于任何文件夹
            self.file_to_folder_map[file_path] = None

        return list(dict.fromkeys(resolved_files)) # Return unique files

    def start_backend_task(self):
        """
        Resolves input paths and uses a 'Worker-to-Thread' model to start the translation task.
        """
        # 通过调用配置服务的 reload_config 方法，强制全面重新加载所有配置
        try:
            self.logger.info("即将开始后台任务，强制重新加载所有配置...")
            self.config_service.reload_config()
            self.logger.info("配置已刷新，继续执行任务。")
        except Exception as e:
            self.logger.error(f"重新加载配置时发生严重错误: {e}")
            # 根据需要，这里可以决定是否要中止任务
            # from PyQt6.QtWidgets import QMessageBox
            # QMessageBox.critical(None, "配置错误", f"无法加载最新配置: {e}")
            # return

        if self.thread is not None and self.thread.isRunning():
            self.logger.warning("一个任务已经在运行中。")
            return

        # 检查文件列表是否为空
        files_to_process = self._resolve_input_files()
        if not files_to_process:
            self.logger.warning("没有找到有效的图片文件，任务中止")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                "文件列表为空",
                "请先添加要翻译的图片文件！\n\n可以通过以下方式添加：\n• 点击「添加文件」按钮\n• 点击「添加文件夹」按钮\n• 直接拖拽文件到文件列表"
            )
            return

        # 检查输出目录是否合法
        output_path = self.config_service.get_config().app.last_output_path
        if not output_path or not os.path.isdir(output_path):
            self.logger.warning(f"输出目录不合法: {output_path}")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                "输出目录不合法",
                "请先设置有效的输出目录！\n\n可以通过以下方式设置：\n• 点击「浏览...」按钮选择输出目录\n• 直接在输出目录输入框中输入路径"
            )
            return

        self.saved_files_count = 0
        self.saved_files_list = []  # 重置保存文件列表
        
        # ✅ 提前创建所有文件夹的源路径文件（在任务开始前）
        self.logger.info("正在为源文件夹创建出生证明...")
        self._create_source_files_for_all_folders(output_path)
        
        self.thread = QThread()
        self.worker = TranslationWorker(
            files=files_to_process,
            config_dict=self.config_service.get_config().dict(),
            output_folder=self.config_service.get_config().app.last_output_path,
            root_dir=self.config_service.root_dir,
            file_to_folder_map=self.file_to_folder_map.copy()  # 传递文件到文件夹的映射
        )
        
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.process)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.finished.connect(self.on_task_finished)
        self.worker.error.connect(self.on_task_error)
        self.worker.progress.connect(self.on_task_progress)
        self.worker.log_received.connect(self.on_worker_log)
        self.worker.file_processed.connect(self.on_file_completed)

        self.thread.start()
        self.logger.info("翻译工作线程已启动。")
        self.state_manager.set_translating(True)
        self.state_manager.set_status_message("正在翻译...")

    def on_task_finished(self, results):
        """处理任务完成信号，并根据需要保存批量任务的结果"""
        print("--- MainAppLogic: Slot on_task_finished triggered.")
        saved_files = []
        # The `results` list will only contain items from a batch job now.
        # Sequential jobs handle saving in `on_file_completed`.
        if results:
            self.logger.info(f"批量翻译任务完成，收到 {len(results)} 个结果。正在保存...")
            try:
                config = self.config_service.get_config()
                output_format = config.cli.format
                save_quality = config.cli.save_quality
                output_folder = config.app.last_output_path

                if not output_folder:
                    self.logger.error("输出目录未设置，无法保存文件。")
                    self.state_manager.set_status_message("错误：输出目录未设置！")
                else:
                    for result in results:
                        if result.get('success'):
                            # In batch mode, image_data is None because the backend already saved the file.
                            # We just need to acknowledge it.
                            if result.get('image_data') is None:
                                # 构造翻译后的图片路径
                                original_path = result.get('original_path')
                                source_folder = self.file_to_folder_map.get(original_path)

                                if source_folder:
                                    # 文件来自文件夹
                                    folder_name = os.path.basename(source_folder)
                                    final_output_folder = os.path.join(output_folder, folder_name)
                                    translated_file = os.path.join(final_output_folder, os.path.basename(original_path))
                                else:
                                    # 单独添加的文件
                                    translated_file = os.path.join(output_folder, os.path.basename(original_path))

                                # 规范化路径，避免混合斜杠
                                translated_file = os.path.normpath(translated_file)
                                saved_files.append(translated_file)
                                self.logger.info(f"确认由后端批量保存的文件: {original_path}")
                            else:
                                # This handles cases where a result with image_data is present in a batch
                                try:
                                    base_filename = os.path.splitext(os.path.basename(result['original_path']))[0]
                                    file_extension = f".{output_format}" if output_format and output_format != "不指定" else ".png"
                                    output_filename = f"{base_filename}_translated{file_extension}"
                                    final_output_path = os.path.join(output_folder, output_filename)
                                    os.makedirs(output_folder, exist_ok=True)
                                    
                                    save_kwargs = {}
                                    image_to_save = result['image_data']

                                    # Convert RGBA to RGB for JPEG format
                                    if file_extension in ['.jpg', '.jpeg']:
                                        if image_to_save.mode == 'RGBA':
                                            image_to_save = image_to_save.convert('RGB')
                                        save_kwargs['quality'] = save_quality
                                    elif file_extension == '.webp':
                                        save_kwargs['quality'] = save_quality

                                    image_to_save.save(final_output_path, **save_kwargs)
                                    saved_files.append(final_output_path)
                                    self.logger.info(f"成功保存文件: {final_output_path}")
                                except Exception as e:
                                    self.logger.error(f"保存文件 {result['original_path']} 时出错: {e}")
                
                # In batch mode, the saved_files_count is the length of this list
                self.saved_files_count = len(saved_files)

            except Exception as e:
                self.logger.error(f"处理批量任务结果时发生严重错误: {e}")

        # This part runs for both sequential and batch modes
        self.logger.info(f"翻译任务完成。总共成功处理 {self.saved_files_count} 个文件。")
        
        # 对于顺序处理模式，使用累积的 saved_files_list
        if not saved_files and self.saved_files_list:
            saved_files = self.saved_files_list.copy()
        
        try:
            print("--- DEBUG: on_task_finished step 1: Setting translating state to False.")
            self.state_manager.set_translating(False)
            print("--- DEBUG: on_task_finished step 2: Setting status message.")
            self.state_manager.set_status_message(f"任务完成，成功处理 {self.saved_files_count} 个文件。")
            print("--- DEBUG: on_task_finished step 3: Emitting task_completed signal.")
            self.task_completed.emit(saved_files)
            print("--- DEBUG: on_task_finished step 4: Signal emitted successfully.")
        except Exception as e:
            self.logger.error(f"完成任务状态更新或信号发射时发生致命错误: {e}", exc_info=True)
        finally:
            print("--- DEBUG: on_task_finished step 5: Entering finally block.")
            if self.thread and self.thread.isRunning():
                self.thread.quit()
                self.thread.wait(5000)  # 等待线程结束，最多等待5秒
                if self.thread.isRunning():
                    self.logger.warning("Thread did not finish within timeout, terminating...")
                    self.thread.terminate()
                    self.thread.wait()
            self.thread = None
            self.worker = None
            print("--- MainAppLogic: Slot on_task_finished finished.")

    def on_task_error(self, error_message):
        print("--- MainAppLogic: Slot on_task_error triggered.")
        self.logger.error(f"翻译任务发生错误: {error_message}")
        self.state_manager.set_translating(False)
        self.state_manager.set_status_message(f"任务失败: {error_message}")
        
        # 正确停止线程
        if self.thread and self.thread.isRunning():
            self.logger.info("Waiting for error cleanup thread to finish...")
            self.thread.quit()
            self.thread.wait(5000)  # 等待最多5秒
            if self.thread.isRunning():
                self.logger.warning("Thread did not finish within timeout, terminating...")
                self.thread.terminate()
                self.thread.wait()
        
        self.thread = None
        self.worker = None
        print("--- MainAppLogic: Slot on_task_error finished.")

    def on_task_progress(self, current, total, message):
        self.logger.info(f"[进度] {current}/{total}: {message}")
        percentage = (current / total) * 100 if total > 0 else 0
        self.state_manager.set_translation_progress(percentage)
        self.state_manager.set_status_message(f"[{current}/{total}] {message}")

    def stop_task(self) -> bool:
        if self.thread and self.thread.isRunning():
            self.logger.info("正在请求停止翻译线程...")

            # 立即更新UI状态：设置为非翻译状态，显示"停止中"
            self.state_manager.set_translating(False)
            self.state_manager.set_status_message("正在停止翻译...")

            # 1. 先通知 worker 停止
            if self.worker:
                self.worker.stop()

            # 2. 请求线程退出
            self.thread.quit()

            # 保存线程引用，避免在等待过程中被清空
            thread_ref = self.thread
            worker_ref = self.worker

            # 在后台等待线程停止，不阻塞UI
            from PyQt6.QtCore import QTimer
            timeout_counter = [0]  # 使用列表以便在闭包中修改

            def wait_for_thread():
                try:
                    if thread_ref and not thread_ref.wait(100):  # 等待100ms
                        timeout_counter[0] += 100

                        # 如果超过5秒还没停止，强制终止
                        if timeout_counter[0] >= 5000:
                            self.logger.warning("线程5秒内未停止，强制终止...")
                            try:
                                thread_ref.terminate()
                                thread_ref.wait(1000)  # 等待1秒
                                self.logger.info("翻译线程已被强制终止。")
                                self.state_manager.set_status_message("任务已强制停止")
                            except Exception as e:
                                self.logger.error(f"强制终止线程失败: {e}")
                                self.state_manager.set_status_message("停止失败")
                        else:
                            # 继续等待
                            QTimer.singleShot(100, wait_for_thread)
                    else:
                        # 线程已停止
                        self.logger.info("翻译线程已成功停止。")
                        self.state_manager.set_status_message("任务已停止")
                except RuntimeError:
                    # 线程对象已被删除，认为已停止
                    self.logger.info("翻译线程已停止（对象已删除）。")
                    self.state_manager.set_status_message("任务已停止")

            # 启动非阻塞等待
            QTimer.singleShot(0, wait_for_thread)

            # 立即返回，不等待线程停止
            return True
        self.logger.warning("请求停止任务，但没有正在运行的线程。")
        return False
    # endregion

    # region 应用生命周期
    def initialize(self) -> bool:
        try:
            # The config is already loaded at startup. We just need to ensure the UI
            # reflects the loaded state without triggering a full, blocking rebuild.
            
            # Get the already loaded config
            config = self.config_service.get_config()

            # Manually emit the signal to populate UI options
            self.config_loaded.emit(config.dict())

            # Manually emit the signal to update the output path display in the UI
            if config.app.last_output_path:
                self.output_path_updated.emit(config.app.last_output_path)
            
            # Ensure the config path is stored in the state manager
            default_config_path = self.config_service.get_default_config_path()
            if os.path.exists(default_config_path):
                self.state_manager.set_state(AppStateKey.CONFIG_PATH, default_config_path)

            self.state_manager.set_app_ready(True)
            self.state_manager.set_status_message("就绪")
            self.logger.info("应用初始化完成")
            return True
        except Exception as e:
            self.logger.error(f"应用初始化异常: {e}")
            return False
    
    def shutdown(self):
        try:
            if self.state_manager.is_translating():
                self.stop_task()
            if self.translation_service:
                pass
        except Exception as e:
            self.logger.error(f"应用关闭异常: {e}")
    # endregion

class QtLogHandler(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self.signal = signal

    def emit(self, record):
        msg = self.format(record)
        self.signal.emit(msg)

class TranslationWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
    log_received = pyqtSignal(str)
    file_processed = pyqtSignal(dict)

    def __init__(self, files, config_dict, output_folder, root_dir, file_to_folder_map=None):
        super().__init__()
        self.files = files
        self.config_dict = config_dict
        self.output_folder = output_folder
        self.root_dir = root_dir
        self.file_to_folder_map = file_to_folder_map or {}  # 文件到文件夹的映射
        self._is_running = True
        self._current_task = None  # 保存当前运行的异步任务

    def stop(self):
        self.log_received.emit("--- Stop request received.")
        self._is_running = False
        # 取消当前运行的异步任务
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        
        # 添加GPU显存清理（自动清理模式）
        self.log_received.emit("--- [CLEANUP] Cleaning up GPU memory...")
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                self.log_received.emit("--- [CLEANUP] GPU memory cleared")
            else:
                self.log_received.emit("--- [CLEANUP] GPU not available, skipped GPU cleanup")
        except Exception as e:
            self.log_received.emit(f"--- [CLEANUP] Warning: Failed to cleanup GPU: {e}")

    def _build_friendly_error_message(self, error_message: str, error_traceback: str) -> str:
        """
        根据错误信息构建友好的中文错误提示
        """
        friendly_msg = "\n" + "="*80 + "\n"
        friendly_msg += "❌ 翻译任务失败\n"
        friendly_msg += "="*80 + "\n\n"
        
        # 检查是否是AI断句检查失败
        if ("BR markers missing" in error_message or 
            "AI断句检查" in error_message or 
            "BRMarkersValidationException" in error_traceback or
            "_validate_br_markers" in error_traceback):
            friendly_msg += "🔍 错误原因：AI断句检查失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI翻译时未能正确添加断句标记 [BR]，导致多次重试后仍然失败。\n\n"
            friendly_msg += "💡 解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 关闭「AI断句检查」选项（推荐）\n"
            friendly_msg += "      - 位置：高级设置 → 渲染设置 → AI断句检查\n"
            friendly_msg += "      - 说明：允许AI在少数情况下不添加断句标记\n\n"
            friendly_msg += "   2. 增加「重试次数」\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高（-1 表示无限重试）\n\n"
            friendly_msg += "   3. 更换翻译模型\n"
            friendly_msg += "      - 某些模型对断句标记的理解更好\n"
            friendly_msg += "      - 建议：尝试 gpt-4o 或 gemini-2.0-flash-exp\n\n"
            friendly_msg += "   4. 关闭「AI断句」功能\n"
            friendly_msg += "      - 位置：高级设置 → 渲染设置 → AI断句\n"
            friendly_msg += "      - 说明：使用传统的自动换行（可能导致排版不够精确）\n\n"
        
        # 检查是否是模型不支持多模态
        elif "不支持多模态" in error_message or "multimodal" in error_message.lower() or "vision" in error_message.lower():
            friendly_msg += "🔍 错误原因：模型不支持多模态输入\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   当前使用的是「高质量翻译器」（openai_hq 或 gemini_hq），\n"
            friendly_msg += "   这些翻译器需要发送图片给AI进行分析，但当前模型不支持图片输入。\n\n"
            friendly_msg += "💡 解决方案（选择其一）：\n"
            friendly_msg += "   1. ⭐ 更换为支持多模态的模型（推荐）\n"
            friendly_msg += "      - OpenAI: gpt-4o, gpt-4-turbo, gpt-4-vision-preview\n"
            friendly_msg += "      - Gemini: gemini-2.0-flash-exp, gemini-1.5-pro, gemini-1.5-flash\n"
            friendly_msg += "      - 注意：DeepSeek模型不支持多模态\n\n"
            friendly_msg += "   2. 切换到普通翻译器\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n"
            friendly_msg += "      - 将 openai_hq 改为 openai\n"
            friendly_msg += "      - 将 gemini_hq 改为 gemini\n"
            friendly_msg += "      - 说明：普通翻译器不需要发送图片，只翻译文本\n\n"
        
        # 检查是否是API密钥错误
        elif "api key" in error_message.lower() or "authentication" in error_message.lower() or "unauthorized" in error_message.lower() or "401" in error_message:
            friendly_msg += "🔍 错误原因：API密钥验证失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API密钥无效、过期或未正确配置。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查API密钥是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量配置区域\n"
            friendly_msg += "      - 确认密钥没有多余的空格或换行\n\n"
            friendly_msg += "   2. 验证API密钥是否有效\n"
            friendly_msg += "      - OpenAI: https://platform.openai.com/api-keys\n"
            friendly_msg += "      - Gemini: https://aistudio.google.com/app/apikey\n\n"
            friendly_msg += "   3. 检查API额度是否用完\n"
            friendly_msg += "      - 登录对应平台查看余额和使用情况\n\n"
        
        # 检查是否是网络连接错误
        elif "connection" in error_message.lower() or "timeout" in error_message.lower() or "network" in error_message.lower():
            friendly_msg += "🔍 错误原因：网络连接失败\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   无法连接到API服务器，可能是网络问题或需要代理。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查网络连接\n"
            friendly_msg += "      - 确认电脑可以正常访问互联网\n\n"
            friendly_msg += "   2. 配置代理（如果需要）\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → OPENAI_HTTP_PROXY\n"
            friendly_msg += "      - 格式：http://127.0.0.1:7890 或 socks5://127.0.0.1:7890\n\n"
            friendly_msg += "   3. 检查API地址是否正确\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → API_BASE\n"
            friendly_msg += "      - 默认值：https://api.openai.com/v1\n\n"
        
        # 检查是否是速率限制错误
        elif "rate limit" in error_message.lower() or "429" in error_message or "too many requests" in error_message.lower():
            friendly_msg += "🔍 错误原因：API请求速率限制 (HTTP 429)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   请求过于频繁，超过了API的速率限制。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 设置每分钟最大请求数（推荐）\n"
            friendly_msg += "      - 位置：通用设置 → 每分钟最大请求数\n"
            friendly_msg += "      - 建议：设置为 3-10（取决于API套餐）\n\n"
            friendly_msg += "   2. 稍后重试\n"
            friendly_msg += "      - 等待几分钟后再次尝试翻译\n\n"
            friendly_msg += "   3. 升级API套餐\n"
            friendly_msg += "      - 联系API提供商升级到更高的速率限制\n\n"
        
        # 检查是否是403禁止访问错误
        elif "403" in error_message or "forbidden" in error_message.lower():
            friendly_msg += "🔍 错误原因：访问被拒绝 (HTTP 403)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   服务器拒绝访问，可能是权限不足或地区限制。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查API密钥权限\n"
            friendly_msg += "      - 确认API密钥有访问该服务的权限\n\n"
            friendly_msg += "   2. 检查账户状态\n"
            friendly_msg += "      - 确认账户未被封禁或限制\n\n"
            friendly_msg += "   3. 配置代理\n"
            friendly_msg += "      - 某些API在特定地区被限制，需要使用代理\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → OPENAI_HTTP_PROXY\n\n"
        
        # 检查是否是404未找到错误
        elif "404" in error_message or "not found" in error_message.lower():
            friendly_msg += "🔍 错误原因：资源未找到 (HTTP 404)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   请求的API端点不存在或模型名称错误。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 检查API地址是否正确（推荐）\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → API_BASE\n"
            friendly_msg += "      - OpenAI默认：https://api.openai.com/v1\n"
            friendly_msg += "      - Gemini默认：https://generativelanguage.googleapis.com\n\n"
            friendly_msg += "   2. 检查模型名称\n"
            friendly_msg += "      - 位置：翻译设置 → 环境变量 → MODEL\n"
            friendly_msg += "      - 确认模型名称拼写正确（如 gpt-4o 不是 gpt4o）\n\n"
            friendly_msg += "   3. 验证模型可用性\n"
            friendly_msg += "      - 某些模型可能已下线或更名\n"
            friendly_msg += "      - 访问官方文档查看可用模型列表\n\n"
        
        # 检查是否是500服务器错误
        elif "500" in error_message or "internal server error" in error_message.lower():
            friendly_msg += "🔍 错误原因：服务器内部错误 (HTTP 500)\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   API服务器遇到内部错误，这通常是临时问题。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 增加重试次数（推荐）\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高\n"
            friendly_msg += "      - 服务器错误通常是临时的，重试可能成功\n\n"
            friendly_msg += "   2. 稍后重试\n"
            friendly_msg += "      - 等待几分钟，让服务器恢复正常\n\n"
            friendly_msg += "   3. 检查API服务状态\n"
            friendly_msg += "      - OpenAI: https://status.openai.com/\n"
            friendly_msg += "      - 查看是否有大规模服务中断\n\n"
        
        # 检查是否是502/503/504网关错误
        elif any(code in error_message for code in ["502", "503", "504"]) or "bad gateway" in error_message.lower() or "service unavailable" in error_message.lower() or "gateway timeout" in error_message.lower():
            error_code = "502/503/504"
            if "502" in error_message:
                error_code = "502"
            elif "503" in error_message:
                error_code = "503"
            elif "504" in error_message:
                error_code = "504"
            
            friendly_msg += f"🔍 错误原因：网关/服务不可用 (HTTP {error_code})\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   - 502: 网关接收到无效响应\n"
            friendly_msg += "   - 503: 服务暂时不可用（通常是维护或过载）\n"
            friendly_msg += "   - 504: 网关超时\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. ⭐ 等待后重试（推荐）\n"
            friendly_msg += "      - 这些错误通常是临时的\n"
            friendly_msg += "      - 等待5-10分钟后重新翻译\n\n"
            friendly_msg += "   2. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高\n\n"
            friendly_msg += "   3. 检查API服务状态\n"
            friendly_msg += "      - 访问API提供商的状态页面\n"
            friendly_msg += "      - OpenAI: https://status.openai.com/\n\n"
            friendly_msg += "   4. 更换API地址\n"
            friendly_msg += "      - 如果使用第三方API中转，尝试更换地址\n\n"
        
        # 检查是否是内容过滤错误
        elif "content filter" in error_message.lower() or "content_filter" in error_message:
            friendly_msg += "🔍 错误原因：内容被安全策略拦截\n\n"
            friendly_msg += "📝 详细说明：\n"
            friendly_msg += "   AI检测到内容可能违反使用政策。\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 检查图片内容\n"
            friendly_msg += "      - 某些敏感内容可能被API拒绝处理\n\n"
            friendly_msg += "   2. 更换翻译器\n"
            friendly_msg += "      - 尝试使用其他翻译器（如 Gemini、DeepL）\n\n"
            friendly_msg += "   3. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 有时重试可以解决临时的过滤问题\n\n"
        
        # 检查是否是语言不支持错误
        elif "language not supported" in error_message.lower() or "LanguageUnsupportedException" in error_traceback:
            friendly_msg += "🔍 错误原因：翻译器不支持当前语言\n\n"
            friendly_msg += "💡 解决方案：\n"
            friendly_msg += "   1. 更换翻译器\n"
            friendly_msg += "      - 位置：翻译设置 → 翻译器\n"
            friendly_msg += "      - 建议：使用支持更多语言的翻译器（如 OpenAI、Gemini）\n\n"
            friendly_msg += "   2. 检查目标语言设置\n"
            friendly_msg += "      - 位置：翻译设置 → 目标语言\n"
            friendly_msg += "      - 确认选择的语言被当前翻译器支持\n\n"
        
        # 通用错误
        else:
            friendly_msg += "🔍 错误原因：\n"
            friendly_msg += f"   {error_message}\n\n"
            friendly_msg += "💡 通用解决方案：\n"
            friendly_msg += "   1. 检查配置是否正确\n"
            friendly_msg += "      - 翻译器、API密钥、模型名称等\n\n"
            friendly_msg += "   2. 增加重试次数\n"
            friendly_msg += "      - 位置：通用设置 → 重试次数\n"
            friendly_msg += "      - 建议：设置为 10 或更高\n\n"
            friendly_msg += "   3. 查看详细日志\n"
            friendly_msg += "      - 在日志框中查找更多错误信息\n\n"
        
        friendly_msg += "="*80 + "\n"
        friendly_msg += "📋 原始错误信息：\n"
        friendly_msg += "-"*80 + "\n"
        friendly_msg += f"{error_message}\n"
        if error_traceback and "Traceback" in error_traceback:
            friendly_msg += "\n" + "-"*80 + "\n"
            friendly_msg += "详细错误：\n"
            friendly_msg += "-"*80 + "\n"
            
            # 只保留API详细错误信息（不保留代码路径）
            lines = error_traceback.split('\n')
            api_error_lines = []
            
            for line in lines:
                # 只保留API错误信息行（包含详细的错误内容）
                if line.strip() and any(keyword in line for keyword in ['BadRequest', 'Error code:', "'error':", "'message':", "{'error':"]):
                    # 如果这是详细的错误信息行，保留它
                    if 'Error code:' in line or "'error':" in line or "{'error':" in line:
                        api_error_lines.append(line.strip())
            
            if api_error_lines:
                friendly_msg += '\n'.join(api_error_lines) + "\n"
                
        friendly_msg += "="*80 + "\n"
        
        return friendly_msg

    async def _do_processing(self):
        log_handler = QtLogHandler(self.log_received)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_handler.setFormatter(formatter)
        manga_logger = logging.getLogger('manga_translator')
        manga_logger.addHandler(log_handler)
        manga_logger.setLevel(logging.INFO)

        results = []
        try:
            from manga_translator.config import (
                ColorizerConfig,
                Config,
                DetectorConfig,
                InpainterConfig,
                OcrConfig,
                RenderConfig,
                Translator,
                TranslatorConfig,
                UpscaleConfig,
            )
            from manga_translator.manga_translator import MangaTranslator
            from PIL import Image

            self.log_received.emit("--- [9] THREAD: Initializing translator...")
            translator_params = self.config_dict.get('cli', {})
            translator_params.update(self.config_dict)
            
            
            font_filename = self.config_dict.get('render', {}).get('font_path')
            if font_filename:
                font_full_path = os.path.join(self.root_dir, 'fonts', font_filename)
                if os.path.exists(font_full_path):
                    translator_params['font_path'] = font_full_path

            translator = MangaTranslator(params=translator_params)
            self.log_received.emit("--- [10] THREAD: Translator initialized.")

            explicit_keys = {'render', 'upscale', 'translator', 'detector', 'colorizer', 'inpainter', 'ocr'}
            remaining_config = {
                k: v for k, v in self.config_dict.items() 
                if k in Config.__fields__ and k not in explicit_keys
            }

            render_config_data = self.config_dict.get('render', {}).copy()

            # 转换 direction 值：'h' -> 'horizontal', 'v' -> 'vertical'
            if 'direction' in render_config_data:
                direction_value = render_config_data['direction']
                if direction_value == 'h':
                    render_config_data['direction'] = 'horizontal'
                elif direction_value == 'v':
                    render_config_data['direction'] = 'vertical'

            translator_config_data = self.config_dict.get('translator', {}).copy()
            hq_prompt_path = translator_config_data.get('high_quality_prompt_path')
            if hq_prompt_path and not os.path.isabs(hq_prompt_path):
                full_prompt_path = os.path.join(self.root_dir, hq_prompt_path)
                if os.path.exists(full_prompt_path):
                    translator_config_data['high_quality_prompt_path'] = full_prompt_path
                else:
                    self.log_received.emit(f"--- WARNING: High quality prompt file not found at {full_prompt_path}")
            
            # 将 CLI 配置中的 attempts 复制到 translator 配置中
            cli_attempts = self.config_dict.get('cli', {}).get('attempts', -1)
            translator_config_data['attempts'] = cli_attempts
            self.log_received.emit(f"--- Setting translator attempts to: {cli_attempts} (from UI config)")

            # 转换超分倍数：'不使用' -> None, '2'/'4' -> int
            upscale_config_data = self.config_dict.get('upscale', {}).copy()
            if 'upscale_ratio' in upscale_config_data:
                ratio_value = upscale_config_data['upscale_ratio']
                if ratio_value == '不使用' or ratio_value is None:
                    upscale_config_data['upscale_ratio'] = None
                else:
                    try:
                        upscale_config_data['upscale_ratio'] = int(ratio_value)
                    except (ValueError, TypeError):
                        upscale_config_data['upscale_ratio'] = None

            config = Config(
                render=RenderConfig(**render_config_data),
                upscale=UpscaleConfig(**upscale_config_data),
                translator=TranslatorConfig(**translator_config_data),
                detector=DetectorConfig(**self.config_dict.get('detector', {})),
                colorizer=ColorizerConfig(**self.config_dict.get('colorizer', {})),
                inpainter=InpainterConfig(**self.config_dict.get('inpainter', {})),
                ocr=OcrConfig(**self.config_dict.get('ocr', {})),
                **remaining_config
            )
            self.log_received.emit("--- [11] THREAD: Config object created correctly.")

            translator_type = config.translator.translator
            is_hq = translator_type in [Translator.openai_hq, Translator.gemini_hq]
            batch_size = self.config_dict.get('cli', {}).get('batch_size', 1)

            # 准备save_info（所有模式都需要）
            output_format = self.config_dict.get('cli', {}).get('format')
            if not output_format or output_format == "不指定":
                output_format = None # Set to None to preserve original extension

            # 收集输入文件夹列表（从file_to_folder_map中获取）
            input_folders = set()
            for file_path in self.files:
                folder = self.file_to_folder_map.get(file_path)
                if folder:
                    input_folders.add(os.path.normpath(folder))

            save_info = {
                'output_folder': self.output_folder,
                'format': output_format,
                'overwrite': self.config_dict.get('cli', {}).get('overwrite', True),
                'input_folders': input_folders
            }

            # 确定翻译流程模式
            workflow_mode = "正常翻译流程"
            workflow_tip = ""
            cli_config = self.config_dict.get('cli', {})
            if cli_config.get('upscale_only', False):
                workflow_mode = "仅超分"
                workflow_tip = "💡 提示：仅对图片进行超分处理，不进行检测、OCR、翻译和渲染"
            elif cli_config.get('colorize_only', False):
                workflow_mode = "仅上色"
                workflow_tip = "💡 提示：仅对图片进行上色处理，不进行检测、OCR、翻译和渲染"
            elif cli_config.get('generate_and_export', False):
                workflow_mode = "导出翻译"
                workflow_tip = "💡 提示：导出翻译后，可在 manga_translator_work/translations/ 目录查看 图片名_translated.txt 文件"
            elif cli_config.get('template', False):
                workflow_mode = "导出原文"
                workflow_tip = "💡 提示：导出原文后，可在 manga_translator_work/originals/ 目录手动翻译 图片名_original.txt 文件，然后使用「导入翻译并渲染」模式"
            elif cli_config.get('load_text', False):
                workflow_mode = "导入翻译并渲染"
                workflow_tip = "💡 提示：将从 manga_translator_work/originals/ 或 translations/ 目录读取 TXT 文件并渲染（优先使用 _original.txt）"
                
                # 在load_text模式下，先自动导入txt文件的翻译到JSON
                self.log_received.emit("📥 正在从TXT文件导入翻译到JSON...")
                from desktop_qt_ui.services.workflow_service import smart_update_translations_from_images, ensure_default_template_exists
                template_path = ensure_default_template_exists()
                if template_path:
                    import_result = smart_update_translations_from_images(self.files, template_path)
                    self.log_received.emit(f"导入结果：{import_result}")
                else:
                    self.log_received.emit("⚠️ 警告：无法找到模板文件，跳过自动导入翻译")

            if is_hq or (len(self.files) > 1 and batch_size > 1):
                self.log_received.emit(f"--- [12] THREAD: Starting batch processing ({'HQ mode' if is_hq else 'Batch mode'})...")

                # 输出批量处理信息
                total_images = len(self.files)
                total_batches = (total_images + batch_size - 1) // batch_size if batch_size > 0 else 1
                self.log_received.emit(f"📊 批量处理模式：共 {total_images} 张图片，分 {total_batches} 个批次处理")
                self.log_received.emit(f"🔧 翻译流程：{workflow_mode}")
                self.log_received.emit(f"📁 输出目录：{self.output_folder}")
                if workflow_tip:
                    self.log_received.emit(workflow_tip)

                images_with_configs = []
                for file_path in self.files:
                    if not self._is_running: raise asyncio.CancelledError("Task stopped by user.")
                    self.progress.emit(len(images_with_configs), len(self.files), f"Loading for batch: {os.path.basename(file_path)}")
                    image = Image.open(file_path)
                    image.name = file_path
                    images_with_configs.append((image, config))

                self.log_received.emit(f"🚀 开始翻译...")
                contexts = await translator.translate_batch(images_with_configs, save_info=save_info)

                # The backend now handles saving for batch jobs. We just need to collect the paths/status.
                success_count = 0
                failed_count = 0
                for ctx in contexts:
                    if not self._is_running: raise asyncio.CancelledError("Task stopped by user.")
                    if ctx:
                        # 检查是否有翻译错误
                        if hasattr(ctx, 'translation_error') and ctx.translation_error:
                            results.append({'success': False, 'original_path': ctx.image_name, 'error': ctx.translation_error})
                            failed_count += 1
                            # 输出详细的错误信息（包含原始错误）
                            self.log_received.emit(f"\n⚠️ 图片 {os.path.basename(ctx.image_name)} 翻译失败：")
                            self.log_received.emit(ctx.translation_error)
                        elif hasattr(ctx, 'success') and ctx.success:
                            # 优先检查success标志（因为result可能被清理了）
                            results.append({'success': True, 'original_path': ctx.image_name, 'image_data': None})
                            success_count += 1
                        elif ctx.result:
                            results.append({'success': True, 'original_path': ctx.image_name, 'image_data': None})
                            success_count += 1
                        else:
                            # 翻译结果为空的失败情况
                            img_name = os.path.basename(ctx.image_name) if hasattr(ctx, 'image_name') and ctx.image_name else 'Unknown'
                            results.append({'success': False, 'original_path': ctx.image_name, 'error': '翻译结果为空'})
                            failed_count += 1
                            self.log_received.emit(f"⚠️ 图片 {img_name} 翻译失败：翻译结果为空")
                    else:
                        # ctx 本身为 None 的失败情况
                        results.append({'success': False, 'original_path': 'Unknown', 'error': 'Batch translation returned no context'})
                        failed_count += 1
                        self.log_received.emit(f"⚠️ 图片翻译失败：批量翻译返回空上下文（ctx=None）")

                if failed_count > 0:
                    self.log_received.emit(f"\n⚠️ 批量翻译完成：成功 {success_count}/{total_images} 张，失败 {failed_count}/{total_images} 张")
                else:
                    self.log_received.emit(f"✅ 批量翻译完成：成功 {success_count}/{total_images} 张")
                self.log_received.emit(f"💾 文件已保存到：{self.output_folder}")

            else:
                self.log_received.emit("--- [12] THREAD: Starting sequential processing...")
                total_files = len(self.files)

                # 输出顺序处理信息
                self.log_received.emit(f"📊 顺序处理模式：共 {total_files} 张图片")
                self.log_received.emit(f"🔧 翻译流程：{workflow_mode}")
                self.log_received.emit(f"📁 输出目录：{self.output_folder}")
                if workflow_tip:
                    self.log_received.emit(workflow_tip)

                success_count = 0
                for i, file_path in enumerate(self.files):
                    if not self._is_running:
                        raise asyncio.CancelledError("Task stopped by user.")

                    current_num = i + 1
                    self.progress.emit(i, total_files, f"Processing: {os.path.basename(file_path)}")
                    self.log_received.emit(f"🔄 [{current_num}/{total_files}] 正在处理：{os.path.basename(file_path)}")

                    try:
                        image = Image.open(file_path)
                        image.name = file_path

                        ctx = await translator.translate(image, config, image_name=image.name)

                        if ctx and ctx.result:
                            self.file_processed.emit({'success': True, 'original_path': file_path, 'image_data': ctx.result})
                            success_count += 1
                            self.log_received.emit(f"✅ [{current_num}/{total_files}] 完成：{os.path.basename(file_path)}")
                        else:
                            self.file_processed.emit({'success': False, 'original_path': file_path, 'error': 'Translation returned no result or image'})
                            self.log_received.emit(f"❌ [{current_num}/{total_files}] 失败：{os.path.basename(file_path)}")

                    except Exception as e:
                        self.log_received.emit(f"❌ [{current_num}/{total_files}] 错误：{os.path.basename(file_path)} - {e}")
                        self.file_processed.emit({'success': False, 'original_path': file_path, 'error': str(e)})
                        # 抛出异常，终止整个翻译流程
                        raise

                self.log_received.emit(f"✅ 顺序翻译完成：成功 {success_count}/{total_files} 张")
                self.log_received.emit(f"💾 文件已保存到：{self.output_folder}")
            
            self.finished.emit(results)
            
            # ✅ 翻译完成后打印内存快照（调试用）
            try:
                import tracemalloc
                snapshot = tracemalloc.take_snapshot()
                top_stats = snapshot.statistics('lineno')
                self.log_received.emit("\n" + "="*80)
                self.log_received.emit("📊 内存占用 TOP 100:")
                self.log_received.emit("="*80)
                for i, stat in enumerate(top_stats[:100], 1):
                    self.log_received.emit(f"{i}. {stat}")
                self.log_received.emit("="*80 + "\n")
            except Exception as e:
                self.log_received.emit(f"Failed to print memory snapshot: {e}")

        except asyncio.CancelledError as e:
            self.log_received.emit(f"Task cancelled: {e}")
            self.error.emit(str(e))
        except Exception as e:
            import traceback
            error_message = str(e)
            error_traceback = traceback.format_exc()
            
            # 构建友好的中文错误提示
            friendly_error = self._build_friendly_error_message(error_message, error_traceback)
            
            self.log_received.emit(friendly_error)
            self.error.emit(friendly_error)
        finally:
            manga_logger.removeHandler(log_handler)

            # 翻译结束后清空翻译器缓存，确保下次翻译使用最新的 .env 配置
            try:
                from manga_translator.translators import translator_cache
                translator_cache.clear()
                self.log_received.emit(f"--- [CLEANUP] Cleared translator cache")
            except Exception as e:
                self.log_received.emit(f"--- [CLEANUP] Warning: Failed to clear cache: {e}")

    @pyqtSlot()
    def process(self):
        loop = None
        try:
            import asyncio
            self.log_received.emit("--- [1] THREAD: process() method entered, starting asyncio task.")

            # 创建事件循环并保存任务引用
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            self._current_task = loop.create_task(self._do_processing())
            loop.run_until_complete(self._current_task)
            self.log_received.emit("--- [END] THREAD: asyncio task finished.")

        except asyncio.CancelledError:
            self.log_received.emit("--- [CANCELLED] THREAD: asyncio task was cancelled.")
        except Exception as e:
            import traceback
            self.error.emit(f"An error occurred in the asyncio runner: {str(e)}\n{traceback.format_exc()}")
        finally:
            if loop:
                try:
                    # Cancel all remaining tasks
                    tasks = asyncio.all_tasks(loop=loop)
                    for task in tasks:
                        task.cancel()
                    
                    # Gather all tasks to let them finish cancelling
                    if tasks:
                        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

                    # Shutdown async generators
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception as e:
                    self.log_received.emit(f"--- ERROR during asyncio cleanup: {e}")
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
                    self.log_received.emit("--- [CLEANUP] THREAD: asyncio loop closed.")
