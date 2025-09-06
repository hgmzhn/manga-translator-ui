# -*- coding: utf-8 -*-

import sys
import customtkinter as ctk
import json
from tkinter import filedialog, messagebox, Listbox
import os
import asyncio
import logging
import queue
import threading
import subprocess
import signal
from dotenv import dotenv_values, set_key
from PIL import Image
from typing import List

# 设置控制台编码
if sys.platform == "win32":
    try:
        # 设置控制台代码页为UTF-8
        import subprocess
        subprocess.run(["chcp", "65001"], shell=True, capture_output=True)
    except:
        pass

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    return os.path.join(base_path, relative_path)


# 核心UI导入（保持功能完整性）
from ui_components import CollapsibleFrame
from services import init_services, get_config_service
from services.shortcut_manager import ShortcutManager
from manga_translator.config import (
    Config, RenderConfig, UpscaleConfig, TranslatorConfig, DetectorConfig,
    ColorizerConfig, InpainterConfig, OcrConfig, Renderer, Alignment,
    Direction, InpaintPrecision, Detector, Inpainter, Colorizer, Ocr,
    Translator, Upscaler
)
from manga_translator.save import OUTPUT_FORMATS

# 只延迟导入真正重量级的翻译器和工作流模块
from manga_translator.manga_translator import TranslationInterrupt
_manga_translator_module = None
_workflow_service_module = None

def get_manga_translator_classes():
    """延迟导入翻译器相关类"""
    global _manga_translator_module
    if _manga_translator_module is None:
        from manga_translator.manga_translator import MangaTranslator, TranslationInterrupt
        _manga_translator_module = (MangaTranslator, TranslationInterrupt)
    return _manga_translator_module

def get_workflow_service():
    """延迟导入工作流服务"""
    global _workflow_service_module
    if _workflow_service_module is None:
        from services.workflow_service import generate_text_from_template, should_restore_translation_to_text, process_json_file_list, get_default_template_path
        _workflow_service_module = (generate_text_from_template, should_restore_translation_to_text, process_json_file_list, get_default_template_path)
    return _workflow_service_module

# 配置类已经直接导入，不需要延迟加载函数


class TextAreaLogHandler(logging.Handler):
    def __init__(self, text_widget, log_queue):
        super().__init__()
        self.text_widget = text_widget
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record) + '\n')

class QueueIO:
    def __init__(self, log_queue):
        self.log_queue = log_queue

    def write(self, text):
        if text.strip():
            self.log_queue.put(text)

    def flush(self):
        pass
    
    def isatty(self):
        return False

class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("漫画图片翻译器 UI")
        self.geometry("1200x800")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.controller = AppController(self)

    def on_close(self):
        self.controller.on_close()

    def show_view(self, view_class):
        frame = self.controller.views[view_class]
        frame.tkraise()

class AppController:
    def __init__(self, app):
        self.app = app
        
        # 快速初始化基本属性
        self.root_dir = os.path.dirname(os.path.abspath(__file__))
        self.user_config_path = resource_path(os.path.join("examples", "config-example.json"))
        self.env_path = os.path.join(self.root_dir, "..", ".env")
        
        # 初始化容器和状态
        self.main_view_widgets = {}
        self.parameter_widgets = {}
        self.env_widgets = {}
        self.input_files = []
        self.translation_process = None
        self.translation_process_pid = None
        self.stop_requested = threading.Event()
        self.current_config_path = None
        self.service = None
        self.cli_widgets = {}
        self.gimp_font_widgets = {}
        self.font_path_widgets = {}
        
        # 字体监控服务
        self.font_monitor = None
        
        # 延迟初始化重量级组件
        self._translator_env_map = None
        self._shortcut_manager = None
        
        # 初始化字体监控服务
        self._init_font_monitor()
        
        # 立即初始化必要的组件（保证功能正常）
        self.translations = {}
        self.load_translations()
        
        # 立即初始化服务（简化版）
        init_services(self.root_dir, self.app)
        
        # 立即初始化配置服务
        config_service = get_config_service()
        if config_service:
            config_service.load_default_config()
        
        # 创建UI容器和Views
        self._create_ui_container_sync()
        
        # 延迟初始化重量级组件 (等UI完全启动后)
        self.app.after(500, self._async_init_heavy_components)

    def _init_essential_attributes(self):
        """初始化必要的属性，避免AttributeError"""
        # 确保所有必要的方法存在，即使是空实现
        if not hasattr(self, 'load_default_config'):
            self.load_default_config = lambda: None
        if not hasattr(self, 'setup_logging'):
            self.setup_logging = lambda: None  
        if not hasattr(self, 'load_and_apply_output_path'):
            self.load_and_apply_output_path = lambda: None

    def _create_ui_container_sync(self):
        """同步创建UI容器和Views"""
        self.views = {}
        container = ctk.CTkFrame(self.app)
        container.grid(row=0, column=0, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        
        # 同步创建Views（在文件末尾定义）
        # 使用延迟导入方式获取Views类
        self.app.after(1, lambda: self._create_views(container))
    
    def _create_views(self, container):
        """创建Views"""
        try:
            # 获取Views类（在文件末尾定义）
            MainView = globals().get('MainView')
            EditorView = globals().get('EditorView')
            
            if MainView and EditorView:
                for F in (MainView, EditorView):
                    frame = F(container, self)
                    self.views[F] = frame
                    frame.grid(row=0, column=0, sticky="nsew")
                
                # 显示主视图
                self.show_view(MainView)
                
                # 立即初始化其他方法
                self.load_default_config() if hasattr(self, 'load_default_config') else None
                self.setup_logging() if hasattr(self, 'setup_logging') else None
                self.load_and_apply_output_path() if hasattr(self, 'load_and_apply_output_path') else None
            else:
                # Views类还没定义，稍后重试
                self.app.after(50, lambda: self._create_views(container))
        except Exception as e:
            print(f"View creation error: {e}")
            self.app.after(100, lambda: self._create_views(container))
    
# 配置类现在直接导入，不需要确保方法
    
    def _init_views(self):
        """延迟初始化Views以加快启动速度"""
        if self._views_initialized:
            return
            
        try:
            # 在文件末尾的类现在应该已经定义了
            # 使用globals()来获取类引用
            MainView = globals().get('MainView')
            EditorView = globals().get('EditorView')
            
            if MainView and EditorView:
                for F in (MainView, EditorView):
                    frame = F(self._container, self)
                    self.views[F] = frame
                    frame.grid(row=0, column=0, sticky="nsew")
                
                # 显示主视图
                self.show_view(MainView)
                self._views_initialized = True
            else:
                # 如果类还没定义，稍后再试
                self.app.after(50, self._init_views)
        except Exception as e:
            print(f"View initialization error: {e}")
            # 稍后重试
            self.app.after(100, self._init_views)
    
    def _async_init_services(self):
        """异步初始化服务"""
        try:
            init_services(self.root_dir, self.app)
            config_service = get_config_service()
            if config_service:
                config_service.load_default_config()
        except Exception as e:
            print(f"Service initialization error: {e}")
    
    def _async_init_final(self):
        """最终初始化步骤"""
        try:
            # 等Views初始化完成后再进行最终设置
            if self._views_initialized:
                # 只有在Views完全初始化后才设置日志
                if hasattr(self, 'main_view_widgets') and self.main_view_widgets.get('log_textbox'):
                    # 重新定义setup_logging以使用实际方法
                    self._setup_logging_actual()
                
                # 其他最终初始化
                self._load_default_config_actual()
                self._load_and_apply_output_path_actual()
                
                # 快捷键初始化
                if not self._shortcut_manager:
                    self._shortcut_manager = ShortcutManager(self.app)
            else:
                # Views还没准备好，稍后重试
                self.app.after(200, self._async_init_final)
        except Exception as e:
            print(f"Final initialization error: {e}")
    
    def _setup_logging_actual(self):
        """实际的日志设置方法"""
        # 这里需要找到实际的setup_logging实现
        pass
    
    def _load_default_config_actual(self):
        """实际的配置加载方法"""
        # 这里需要找到实际的load_default_config实现
        pass
    
    def _async_init_heavy_components(self):
        """异步初始化重量级组件"""
        try:
            # 在主线程中初始化快捷键管理器
            if not self._shortcut_manager:
                self._shortcut_manager = ShortcutManager(self.app)
        except Exception as e:
            # 如果在后台线程中，重新调度到主线程
            self.app.after(100, self._async_init_heavy_components)
    
    @property 
    def translator_env_map(self):
        """延迟创建translator_env_map"""
        if self._translator_env_map is None:
            self._translator_env_map = self.create_translator_env_map()
        return self._translator_env_map
    
    @property
    def shortcut_manager(self):
        """延迟获取shortcut_manager"""
        if self._shortcut_manager is None:
            self._shortcut_manager = ShortcutManager(self.app)
        return self._shortcut_manager

    def _resolve_input_files(self) -> List[str]:
        """解析输入文件，优化性能避免重复操作"""
        if not self.input_files:
            return []
        
        # 使用缓存的文件扩展名集合（性能优化）
        if not hasattr(self, '_image_extensions'):
            self._image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'}
        
        resolved_files = []
        
        for path in self.input_files:
            if os.path.isfile(path):
                # 优化：使用set查找而不是tuple.endswith
                _, ext = os.path.splitext(path.lower())
                if ext in self._image_extensions:
                    resolved_files.append(path)
            elif os.path.isdir(path):
                # 优化：使用os.scandir而不是os.walk（更快）
                try:
                    with os.scandir(path) as entries:
                        for entry in entries:
                            if entry.is_file():
                                _, ext = os.path.splitext(entry.name.lower())
                                if ext in self._image_extensions:
                                    resolved_files.append(entry.path)
                            elif entry.is_dir():
                                # 递归处理子文件夹
                                for root, _, files in os.walk(entry.path):
                                    for file in files:
                                        _, ext = os.path.splitext(file.lower())
                                        if ext in self._image_extensions:
                                            resolved_files.append(os.path.join(root, file))
                except (OSError, PermissionError):
                    # 如果访问权限有问题，回退到原方法
                    for root, _, files in os.walk(path):
                        for file in files:
                            _, ext = os.path.splitext(file.lower())
                            if ext in self._image_extensions:
                                resolved_files.append(os.path.join(root, file))
        
        return resolved_files

    def setup_logging(self):
        self.log_queue = queue.Queue()
        self.log_text = self.main_view_widgets.get('log_textbox')
        
        if not self.log_text:
            print("Error: log_textbox widget not found during logging setup.")
            return

        # Create a handler that puts log messages into the queue
        queue_handler = TextAreaLogHandler(self.log_text, self.log_queue)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
        queue_handler.setFormatter(formatter)

        # Configure the root logger to use the queue handler
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(queue_handler)

        # Redirect stdout and stderr to the log queue as well
        queue_io = QueueIO(self.log_queue)
        sys.stdout = queue_io
        sys.stderr = queue_io
        
        # Start polling the queue to update the UI
        self.app.after(100, self.process_log_queue)

    def process_log_queue(self):
        try:
            while True: # Process all available messages in the queue
                message = self.log_queue.get_nowait()
                self.update_log(message + '\n', from_queue=True)
        except queue.Empty:
            pass
        finally:
            # Schedule the next check
            self.app.after(100, self.process_log_queue)

    def show_view(self, view_class):
        EditorView = globals().get('EditorView')

        if view_class == EditorView:
            self.shortcut_manager.push_context("editor")
            
            # When switching to the editor, reload its config to reflect changes
            editor_view_instance = self.views.get(view_class)
            if editor_view_instance and hasattr(editor_view_instance, 'editor_frame'):
                editor_frame = editor_view_instance.editor_frame
                if hasattr(editor_frame, 'reload_config_and_redraw'):
                    # Use app.after to ensure this runs after the view is raised and visible
                    self.app.after(50, editor_frame.reload_config_and_redraw)
        else:
            self.shortcut_manager.pop_context("editor")

        frame = self.views.get(view_class)
        if frame:
            frame.tkraise()

    def on_close(self):
        # 停止字体监控服务
        if self.font_monitor:
            self.font_monitor.stop_monitoring()
        
        # Check for unsaved changes in the editor
        EditorView = globals().get('EditorView')
        if not EditorView:
            self.app.destroy()
            return

        editor_view_instance = self.views.get(EditorView)
        
        # Check if the editor frame exists and has unsaved changes
        if editor_view_instance and hasattr(editor_view_instance, 'editor_frame'):
            editor_frame = editor_view_instance.editor_frame
            if hasattr(editor_frame, '_has_unsaved_changes') and editor_frame._has_unsaved_changes():
                from tkinter import messagebox
                
                result = messagebox.askyesnocancel(
                    "退出确认",
                    "您有未保存的修改。是否要保存后再退出？\n\n- 选择‘是’将保存并退出。\n- 选择‘否’将不保存并退出。\n- 选择‘取消’将不退出。"
                )
                
                if result is True:  # Yes
                    # We need to ensure save is complete before destroying
                    editor_frame._save_file()
                    self.app.destroy()
                elif result is False:  # No
                    self.app.destroy()
                else:  # Cancel (result is None)
                    return # Do nothing, do not close
            else:
                self.app.destroy()
        else:
            # If editor view doesn't exist for some reason, just close
            self.app.destroy()

    def load_translations(self):
        self.translations = {
            "Manga Image Translator UI": "漫画图片翻译器 UI",
            "Load Config": "加载 JSON 配置",
            "Save Config": "保存配置与环境变量",
            "Default config file not found.\nPlease load a config file manually.": "未找到默认配置文件。\n请手动加载一个配置文件。",
            "JSON Config": "翻译设置",
            "GPT Prompt Editor": "提示词编辑器",
            "GPT Config": "GPT 配置",
            "Load GPT Config": "加载 GPT 配置",
            "Save GPT Config": "保存 GPT 配置",
            "general_settings": "通用设置",
            "render": "渲染参数",
            "renderer": "渲染器",
            "alignment": "对齐方式",
            "disable_font_border": "禁用字体边框",
            "font_size_offset": "字体大小偏移量",
            "font_size_minimum": "最小字体大小",
            "direction": "文本方向",
            "uppercase": "大写",
            "lowercase": "小写",
            "gimp_font": "GIMP字体",
            "no_hyphenation": "禁用连字符",
            "font_color": "字体颜色",
            "line_spacing": "行间距",
            "font_size": "字体大小",
            "rtl": "从右到左",
            "upscale": "超分参数",
            "upscaler": "超分模型",
            "revert_upscaling": "还原超分",
            "upscale_ratio": "超分比例",
            "translator": "翻译器",
            "target_lang": "目标语言",
            "no_text_lang_skip": "不跳过目标语言文本",
            "skip_lang": "跳过语言",
            "gpt_config": "GPT配置文件路径",
            "translator_chain": "链式翻译",
            "selective_translation": "选择性翻译",
            "detector": "文本检测器",
            "detection_size": "检测大小",
            "text_threshold": "文本阈值",
            "det_rotate": "旋转图像进行检测",
            "det_auto_rotate": "旋转图像以优先检测垂直文本行",
            "det_invert": "反转图像颜色进行检测",
            "det_gamma_correct": "应用伽马校正进行检测",
            "box_threshold": "边界框生成阈值",
            "unclip_ratio": "Unclip比例",
            "colorizer": "上色模型",
            "colorization_size": "上色大小",
            "denoise_sigma": "降噪强度",
            "inpainter": "修复模型",
            "inpainting_size": "修复大小",
            "inpainting_precision": "修复精度",
            "ocr": "OCR模型",
            "use_mocr_merge": "使用MOCR合并",
            "min_text_length": "最小文本长度",
            "ignore_bubble": "忽略非气泡文本",
            "prob": "文本区域最低概率 (prob)",
            "kernel_size": "卷积核大小",
            "mask_dilation_offset": "遮罩扩张偏移",
            "filter_text": "过滤文本 (Regex)",
            "YOUDAO_APP_KEY": "有道翻译应用ID (YOUDAO_APP_KEY)",
            "YOUDAO_SECRET_KEY": "有道翻译应用秘钥 (YOUDAO_SECRET_KEY)",
            "BAIDU_APP_ID": "百度翻译 AppID (BAIDU_APP_ID)",
            "BAIDU_SECRET_KEY": "百度翻译密钥 (BAIDU_SECRET_KEY)",
            "DEEPL_AUTH_KEY": "DeepL 授权密钥 (DEEPL_AUTH_KEY)",
            "CAIYUN_TOKEN": "彩云小译 API 令牌 (CAIYUN_TOKEN)",
            "OPENAI_API_KEY": "OpenAI API 密钥 (OPENAI_API_KEY)",
            "OPENAI_MODEL": "OpenAI 模型 (OPENAI_MODEL)",
            "OPENAI_API_BASE": "OpenAI API 地址 (OPENAI_API_BASE)",
            "OPENAI_HTTP_PROXY": "HTTP 代理 (OPENAI_HTTP_PROXY)",
            "OPENAI_GLOSSARY_PATH": "术语表路径 (OPENAI_GLOSSARY_PATH)",
            "DEEPSEEK_API_KEY": "DeepSeek API 密钥 (DEEPSEEK_API_KEY)",
            "DEEPSEEK_API_BASE": "DeepSeek API 地址 (DEEPSEEK_API_BASE)",
            "DEEPSEEK_MODEL": "DeepSeek 模型 (DEEPSEEK_MODEL)",
            "GROQ_API_KEY": "Groq API 密钥 (GROQ_API_KEY)",
            "GROQ_MODEL": "Groq 模型 (GROQ_MODEL)",
            "GEMINI_API_KEY": "Gemini API 密钥 (GEMINI_API_KEY)",
            "GEMINI_MODEL": "Gemini 模型 (GEMINI_MODEL)",
            "GEMINI_API_BASE": "Gemini API 地址 (GEMINI_API_BASE)",
            "translator_parameters": "翻译器参数",
            "SAKURA_API_BASE": "SAKURA API 地址 (SAKURA_API_BASE)",
            "SAKURA_DICT_PATH": "SAKURA 词典路径 (SAKURA_DICT_PATH)",
            "SAKURA_VERSION": "SAKURA API 版本 (SAKURA_VERSION)",
            "CUSTOM_OPENAI_API_BASE": "自定义 OpenAI API 地址 (CUSTOM_OPENAI_API_BASE)",
            "CUSTOM_OPENAI_MODEL": "自定义 OpenAI 模型 (CUSTOM_OPENAI_MODEL)",
            "CUSTOM_OPENAI_API_KEY": "自定义 OpenAI API 密钥 (CUSTOM_OPENAI_API_KEY)",
            "CUSTOM_OPENAI_MODEL_CONF": "自定义 OpenAI 模型配置 (CUSTOM_OPENAI_MODEL_CONF)",
            "source": "源",
            "target": "目标",
            "add_files": "添加文件",
            "add_folder": "添加文件夹",
            "clear_list": "清空列表",
            "open": "打开",
            "translate_tab": "翻译",
            "text_recognition_tab": "文本识别",
            "advanced_tab": "高级",
            "cli_options": "命令行参数",
            "overwrite": "覆盖已存在文件",
            "skip_no_text": "跳过无文本图像",
            "use_mtpe": "启用后期编辑(MTPE)",
            "save_text": "保存文本",
            "load_text": "加载文本",
            "save_text_file": "保存文本路径",
            "prep_manual": "为手动排版做准备",
            "save_quality": "图像保存质量",
            "verbose": "详细日志",
            "attempts": "重试次数 (-1为无限)",
            "ignore_errors": "忽略错误",
            "model_dir": "模型目录",
            "use_gpu": "使用 GPU",
            "use_gpu_limited": "使用 GPU（受限）",
            "font_path": "字体路径",
            "pre_dict": "译前替换字典",
            "post_dict": "译后替换字典",
            "kernel_size": "卷积核大小",
            "context_size": "上下文页数",
            "format": "输出格式",
            "batch_size": "批量大小",
            "batch_concurrent": "并发批量处理",
            "config_file": "配置文件",
            "template": "模板模式"
        }

    def translate(self, text):
        return self.translations.get(text, text)

    def create_translator_env_map(self):
        return {
            "youdao": ["YOUDAO_APP_KEY", "YOUDAO_SECRET_KEY"],
            "baidu": ["BAIDU_APP_ID", "BAIDU_SECRET_KEY"],
            "deepl": ["DEEPL_AUTH_KEY"],
            "caiyun": ["CAIYUN_TOKEN"],
            "openai": ["OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_API_BASE", "OPENAI_HTTP_PROXY", "OPENAI_GLOSSARY_PATH"],
            "chatgpt": ["OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_API_BASE", "OPENAI_HTTP_PROXY", "OPENAI_GLOSSARY_PATH"],
            "deepseek": ["DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE", "DEEPSEEK_MODEL"],
            "groq": ["GROQ_API_KEY", "GROQ_MODEL"],
            "gemini": ["GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_API_BASE"],
            "sakura": ["SAKURA_API_BASE", "SAKURA_DICT_PATH", "SAKURA_VERSION"],
            "custom_openai": ["CUSTOM_OPENAI_API_BASE", "CUSTOM_OPENAI_MODEL", "CUSTOM_OPENAI_API_KEY", "CUSTOM_OPENAI_MODEL_CONF"]
        }

    def on_translator_change(self, selected_translator):
        self.update_log(f"[DEBUG] on_translator_change called with: {selected_translator}\n")
        
        # 处理空值或无效值
        if not selected_translator:
            self.update_log("[DEBUG] No translator selected, skipping env UI setup.\n")
            return
            
        # 清理之前的环境变量UI
        if hasattr(self, 'env_params_frame') and self.env_params_frame.winfo_exists():
            self.env_params_frame.destroy()
        self.env_widgets = {}

        # 直接使用选择的翻译器名称查找环境变量
        required_keys = self.translator_env_map.get(selected_translator, [])
        self.update_log(f"[DEBUG] Required .env keys for {selected_translator}: {required_keys}\n")
        if not required_keys:
            self.update_log(f"[DEBUG] No .env keys required for translator '{selected_translator}', not creating UI section.\n")
            return

        # 创建环境变量UI框架
        try:
            self.env_params_frame = ctk.CTkFrame(self.translator_env_collapsible_frame.content_frame, fg_color="transparent")
            self.env_params_frame.pack(pady=5, padx=0, fill="x")

            current_env_values = dotenv_values(self.env_path)
            self.create_env_widgets(required_keys, current_env_values, self.env_params_frame)
            self.update_log(f"[DEBUG] Created .env UI section with {len(required_keys)} widgets.\n")
        except Exception as e:
            self.update_log(f"[ERROR] Failed to create env UI for {selected_translator}: {e}\n")

    def create_param_widgets(self, data, parent_frame, prefix="", start_row=0):
        if not isinstance(data, dict):
            return
        parent_frame.grid_columnconfigure(1, weight=1)

        for i, (key, value) in enumerate(data.items()):
            row = start_row + i
            full_key = f"{prefix}.{key}" if prefix else key
            
            label = ctk.CTkLabel(parent_frame, text=self.translate(key), anchor="w")
            widget = None

            if full_key == "render.font_path":
                widget_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
                widget_frame.grid_columnconfigure(0, weight=1)

                font_options = self.get_options_for_key(key) or []
                widget = ctk.CTkComboBox(widget_frame, values=font_options, command=lambda v, k=full_key: self._save_widget_change(k, value=v))
                widget.set(str(value) if value else "")
                widget.grid(row=0, column=0, sticky="ew")
                
                browse_button = ctk.CTkButton(widget_frame, text="打开目录", width=80, command=self._open_font_directory)
                browse_button.grid(row=0, column=1, padx=(5, 0))
                
                self.parameter_widgets[full_key] = widget
                self.font_path_widgets['label'] = label
                self.font_path_widgets['frame'] = widget_frame
                label.grid(row=row, column=0, padx=5, pady=2, sticky="w")
                widget_frame.grid(row=row, column=1, padx=5, pady=2, sticky="ew")
                continue

            if key == 'format':
                formats = ["不指定"] + list(OUTPUT_FORMATS.keys())
                widget = ctk.CTkOptionMenu(parent_frame, values=formats, command=lambda v, k=full_key: self._save_widget_change(k, value=v))
                widget.set(str(value) if value else "不指定")
            elif isinstance(value, bool):
                widget = ctk.CTkSwitch(parent_frame, text="", onvalue=True, offvalue=False, command=lambda k=full_key: self._save_widget_change(k))
                if value: widget.select()
                else: widget.deselect()
            elif isinstance(value, (int, float)):
                entry_var = ctk.StringVar(value=str(value))
                widget = ctk.CTkEntry(parent_frame, textvariable=entry_var)
                entry_var.trace_add("write", lambda *args, k=full_key, w=widget: self._save_widget_change(k, w))
            elif isinstance(value, str):
                options = self.get_options_for_key(key)
                if options:
                    if key == "renderer":
                        widget = ctk.CTkOptionMenu(parent_frame, values=options, command=self._on_renderer_changed)
                    elif key == "translator":
                        def translator_change_handler(v, k="translator.translator"):
                            self.on_translator_change(v)
                            self._save_widget_change(k, value=v)
                        widget = ctk.CTkOptionMenu(parent_frame, values=options, command=translator_change_handler)
                    elif key == "target_lang":
                        from services.translation_service import TranslationService
                        translation_service = TranslationService()
                        lang_map = translation_service.get_target_languages()
                        def lang_change_handler(v):
                            lang_code = next((code for code, name in lang_map.items() if name == v), None)
                            if lang_code:
                                self._save_widget_change("translator.target_lang", value=lang_code)
                        widget = ctk.CTkOptionMenu(parent_frame, values=options, command=lang_change_handler)
                    else:
                        widget = ctk.CTkOptionMenu(parent_frame, values=options, command=lambda v, k=full_key: self._save_widget_change(k, value=v))
                    
                    value_to_set = value
                    if key == "translator":
                        if value == "chatgpt": value_to_set = "openai"
                        elif value not in options:
                            try:
                                value_to_set = Translator(value).value
                                if value_to_set == "chatgpt": value_to_set = "openai"
                            except ValueError: pass
                    elif key == "target_lang":
                        from services.translation_service import TranslationService
                        translation_service = TranslationService()
                        lang_map = translation_service.get_target_languages()
                        value_to_set = lang_map.get(value, value)
                    widget.set(value_to_set)
                else:
                    entry_var = ctk.StringVar(value=value)
                    widget = ctk.CTkEntry(parent_frame, textvariable=entry_var)
                    entry_var.trace_add("write", lambda *args, k=full_key, w=widget: self._save_widget_change(k, w))
                    if full_key == "render.gimp_font":
                        self.gimp_font_widgets['label'] = label
                        self.gimp_font_widgets['widget'] = widget
            elif value is None:
                entry_var = ctk.StringVar(value="")
                widget = ctk.CTkEntry(parent_frame, textvariable=entry_var)
                entry_var.trace_add("write", lambda *args, k=full_key, w=widget: self._save_widget_change(k, w))
            else:
                entry_var = ctk.StringVar(value=str(value))
                widget = ctk.CTkEntry(parent_frame, textvariable=entry_var)
                entry_var.trace_add("write", lambda *args, k=full_key, w=widget: self._save_widget_change(k, w))
            
            if widget:
                label.grid(row=row, column=0, padx=5, pady=2, sticky="w")
                widget.grid(row=row, column=1, padx=5, pady=2, sticky="ew")
                self.parameter_widgets[full_key] = widget

    def create_env_widgets(self, keys, current_values, parent_frame):
        parent_frame.grid_columnconfigure(1, weight=1)
        for i, key in enumerate(keys):
            value = current_values.get(key, "")
            
            label = ctk.CTkLabel(parent_frame, text=self.translate(key), anchor="w")
            label.grid(row=i, column=0, padx=5, pady=2, sticky="w")

            entry_var = ctk.StringVar(value=value)
            widget = ctk.CTkEntry(parent_frame, textvariable=entry_var)
            widget.grid(row=i, column=1, padx=5, pady=2, sticky="ew")
            entry_var.trace_add("write", lambda *args, k=key, var=entry_var: self._save_env_change(k, var.get()))
            self.env_widgets[key] = widget

    def _save_env_change(self, key, value):
        try:
            # Create .env file if it doesn't exist
            if not os.path.exists(self.env_path):
                with open(self.env_path, 'w') as f:
                    pass
            set_key(self.env_path, key, value, quote_mode="never")
        except Exception as e:
            self.update_log(f"Error saving .env change: {e}\n")

    def get_options_for_key(self, key):
        if key == 'font_path':
            try:
                fonts_dir = resource_path('fonts')
                if os.path.isdir(fonts_dir):
                    return sorted([f for f in os.listdir(fonts_dir) if f.lower().endswith(('.ttf', '.otf', '.ttc'))])
                else:
                    return []
            except Exception as e:
                print(f"Error scanning fonts directory: {e}")
                return []

        if key == 'target_lang':
            from services.translation_service import TranslationService
            # This is not ideal, but for now it's the easiest way to get the languages
            # without a major refactoring of the service locator pattern.
            translation_service = TranslationService()
            return list(translation_service.get_target_languages().values())

        options = {
            "renderer": [member.value for member in Renderer],
            "alignment": [member.value for member in Alignment],
            "direction": [member.value for member in Direction],
            "upscaler": [member.value for member in Upscaler],
            "translator": [member.value for member in Translator],
            "detector": [member.value for member in Detector],
            "colorizer": [member.value for member in Colorizer],
            "inpainter": [member.value for member in Inpainter],
            "inpainting_precision": [member.value for member in InpaintPrecision],
            "ocr": [member.value for member in Ocr]
        }.get(key, None)
        
        # 特殊处理翻译器选项，将chatgpt显示为openai
        if key == "translator" and options:
            # 替换chatgpt为openai
            options = [option if option != "chatgpt" else "openai" for option in options]
        
        return options

    def _init_font_monitor(self):
        """初始化字体监控服务"""
        try:
            from services.font_monitor_service import FontMonitorService
            fonts_dir = resource_path('fonts')
            
            self.font_monitor = FontMonitorService(fonts_dir)
            self.font_monitor.register_callback(self._on_fonts_changed)
            self.font_monitor.start_monitoring()
            
        except Exception as e:
            print(f"初始化字体监控服务失败: {e}")
    
    def _on_fonts_changed(self, font_files: list):
        """字体文件变化回调"""
        try:
            # 更新字体下拉菜单
            font_widget = self.parameter_widgets.get("render.font_path")
            if font_widget and hasattr(font_widget, 'configure'):
                # 更新下拉菜单的选项
                font_widget.configure(values=font_files)
                print(f"[字体监控] 字体下拉菜单已更新: {len(font_files)} 个字体")
                
                # 记录日志
                self.update_log(f"检测到字体目录变化，已更新字体列表 ({len(font_files)} 个字体)\n")
        except Exception as e:
            print(f"更新字体下拉菜单失败: {e}")

    def _open_font_directory(self):
        fonts_dir = resource_path('fonts')
        try:
            if sys.platform == "win32":
                os.startfile(fonts_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", fonts_dir])
            else:
                subprocess.run(["xdg-open", fonts_dir])
        except Exception as e:
            self.update_log(f"Error opening font directory: {e}\n")
        
        # 手动刷新字体列表
        if self.font_monitor:
            self.font_monitor.refresh_fonts()

    def _select_font_path(self):
        font_path = filedialog.askopenfilename(
            title="Select Font File",
            filetypes=[("Font files", "*.ttf *.otf *.ttc"), ("All files", "*.* אמיתי")])
        if font_path:
            widget = self.parameter_widgets.get("render.font_path")
            if widget:
                # When selecting a file, we should probably just use the filename
                font_filename = os.path.basename(font_path)
                widget.set(font_filename)
                self._save_widget_change("render.font_path", value=font_filename)

    def _save_widget_change(self, full_key, widget=None, value=None):
        self.update_log(f"[DEBUG] _save_widget_change called for key: '{full_key}'\n")
        try:
            with open(self.current_config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            # If value is not provided directly, get it from the widget
            if value is None and widget is not None:
                value = self._get_widget_value(widget)
            
            self.update_log(f"[DEBUG] Value to save: {value} (Type: {type(value)})\n")

            # 特殊处理：将UI中的openai转换回后端期望的chatgpt
            if full_key == "translator.translator" and value == "openai":
                value = "chatgpt"

            # Update the nested dictionary
            keys = full_key.split('.')
            d = config_data
            for key in keys[:-1]:
                d = d.setdefault(key, {})
            d[keys[-1]] = value

            with open(self.current_config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
            
            self.update_log(f"[SUCCESS] Saved '{full_key}' = '{value}' to {os.path.basename(self.current_config_path)}\n")

            # Update the in-memory config in the service to keep it in sync
            config_service = get_config_service()
            if config_service:
                config_service.set_config(config_data)

        except FileNotFoundError:
            self.update_log(f"Error: Config file not found at {self.current_config_path} for saving.\n")
        except Exception as e:
            self.update_log(f"Error saving config change: {e}\n")
            import traceback
            self.update_log(f"{traceback.format_exc()}\n")

    def _get_widget_value(self, widget):
        if isinstance(widget, ctk.CTkSwitch):
            return widget.get() == 1
        elif isinstance(widget, ctk.CTkEntry):
            value_str = widget.get()
            if value_str == '':
                return None
            try:
                if '.' in value_str:
                    return float(value_str)
                else:
                    return int(value_str)
            except (ValueError, TypeError):
                # 确保字符串使用正确编码，避免乱码
                return str(value_str)
        elif isinstance(widget, ctk.CTkOptionMenu):
            value = widget.get()
            # 确保选项值编码正确
            return value if value != "不指定" else None
        return None

    def _on_layout_mode_change(self, display_name: str):
        self.update_log(f"[DEBUG] _on_layout_mode_change triggered with: {display_name}\n")
        display_map = {
            "默认模式 (有Bug)": "default",
            "智能缩放 (推荐)": "smart_scaling",
            "严格边界 (缩小字体)": "strict",
            "固定字体 (扩大文本框)": "fixed_font",
            "完全禁用 (裁剪文本)": "disable_all"
        }
        internal_name = display_map.get(display_name, 'smart_scaling')
        self.update_log(f"[DEBUG] Resolved layout mode to: {internal_name}\n")
        
        # Directly call the save function with the resolved value
        self._save_widget_change('render.layout_mode', value=internal_name)

    def _on_renderer_changed(self, selected_renderer: str):
        self.update_log(f"[DEBUG] Renderer changed to: {selected_renderer}\n")
        self._save_widget_change('render.renderer', value=selected_renderer)

        # Toggle visibility for gimp_font
        if self.gimp_font_widgets:
            label = self.gimp_font_widgets.get('label')
            widget = self.gimp_font_widgets.get('widget')
            if label and widget:
                if selected_renderer == 'gimp':
                    label.grid()
                    widget.grid()
                else:
                    label.grid_remove()
                    widget.grid_remove()

        # Toggle visibility for font_path
        if self.font_path_widgets:
            label = self.font_path_widgets.get('label')
            frame = self.font_path_widgets.get('frame')
            if label and frame:
                if selected_renderer == 'gimp':
                    label.grid_remove()
                    frame.grid_remove()
                else:
                    label.grid()
                    frame.grid()

    def load_default_config(self):
        main_view = self.views[MainView]
        self.parameter_widgets = {}
        self.cli_params_widgets = {}
        
        # 清理所有标签页内容
        frames_to_clear = [
            self.main_view_widgets.get('basic_left_frame'),
            self.main_view_widgets.get('basic_right_frame'),
            self.main_view_widgets.get('advanced_left_frame'),
            self.main_view_widgets.get('advanced_right_frame'),
            self.main_view_widgets.get('options_left_frame'),
            self.main_view_widgets.get('options_right_frame')
        ]
        
        for frame in frames_to_clear:
            if frame:
                for widget in frame.winfo_children():
                    widget.destroy()
        
        self.create_main_view_settings_tabbed()

    def create_main_view_settings_tabbed(self):
        """创建标签页布局的设置界面"""
        try:
            config_path = resource_path(os.path.join("examples", "config-example.json"))
            self.current_config_path = config_path
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except FileNotFoundError:
            # 如果配置文件不存在，在第一个标签页显示错误信息
            basic_left = self.main_view_widgets['basic_left_frame']
            label = ctk.CTkLabel(basic_left, text=self.translate("Default config file not found.\nPlease load a config file manually."), font=ctk.CTkFont(size=16))
            label.pack(pady=20)
            return

        # === 基础设置标签页 ===
        basic_left = self.main_view_widgets['basic_left_frame']
        basic_right = self.main_view_widgets['basic_right_frame']
        
        # 左侧：翻译器设置
        translator_frame = CollapsibleFrame(basic_left, title=self.translate("translator_parameters"))
        translator_frame.pack(fill="x", pady=5)
        
        self.translator_env_collapsible_frame = translator_frame
        self.translator_options_container = ctk.CTkFrame(translator_frame.content_frame, fg_color="transparent")
        self.translator_options_container.pack(fill="x")
        
        self.create_param_widgets(config.get("translator", {}), self.translator_options_container, "translator")
        
        current_translator = config.get("translator", {}).get("translator")
        if not current_translator:
            from manga_translator.config import Translator
            default_translator = list(Translator)[0].value
            current_translator = default_translator
        
        self.on_translator_change(current_translator)
        
        # 右侧：文本识别设置
        ocr_frame = CollapsibleFrame(basic_right, title=self.translate("text_recognition_tab"))
        ocr_frame.pack(fill="x", pady=5)
        self.create_param_widgets(config.get("ocr", {}), ocr_frame.content_frame, "ocr")
        
        # === 高级设置标签页 ===
        advanced_left = self.main_view_widgets['advanced_left_frame']
        advanced_right = self.main_view_widgets['advanced_right_frame']
        
        # 左侧：检测器和修复器
        detector_frame = CollapsibleFrame(advanced_left, title=self.translate("detector"))
        detector_frame.pack(fill="x", pady=5)
        self.create_param_widgets(config.get("detector"), detector_frame.content_frame, "detector")
        
        inpainter_frame = CollapsibleFrame(advanced_left, title=self.translate("inpainter"))
        inpainter_frame.pack(fill="x", pady=5)
        self.create_param_widgets(config.get("inpainter"), inpainter_frame.content_frame, "inpainter")

        # === 新增：修复参数 ===
        repair_frame = CollapsibleFrame(advanced_left, title="修复参数")
        repair_frame.pack(fill="x", pady=5)
        repair_params = {
            "filter_text": config.get("filter_text"),
            "kernel_size": config.get("kernel_size"),
            "mask_dilation_offset": config.get("mask_dilation_offset")
        }
        self.create_param_widgets(repair_params, repair_frame.content_frame, "") # Pass empty prefix for top-level keys
        
        # 右侧：渲染、超分、上色器
        render_frame = CollapsibleFrame(advanced_right, title=self.translate("render"))
        render_frame.pack(fill="x", pady=5)
        content_frame = render_frame.content_frame
        
        # 特殊处理render配置
        render_config = config.get("render", {})
        content_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(content_frame, text="排版模式", anchor="w").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        
        display_map = {
            'default': "默认模式 (有Bug)",
            'smart_scaling': "智能缩放 (推荐)",
            'strict': "严格边界 (缩小字体)",
            'fixed_font': "固定字体 (扩大文本框)",
            'disable_all': "完全禁用 (裁剪文本)"
        }
        
        layout_mode_widget = ctk.CTkOptionMenu(content_frame, values=list(display_map.values()), command=self._on_layout_mode_change)
        current_layout_mode = render_config.get('layout_mode', 'smart_scaling')
        layout_mode_widget.set(display_map.get(current_layout_mode, display_map['smart_scaling']))
        layout_mode_widget.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        self.parameter_widgets['render.layout_mode'] = layout_mode_widget

        other_render_params = render_config.copy()
        other_render_params.pop('layout_mode', None)
        self.create_param_widgets(other_render_params, content_frame, "render", start_row=1)
        
        upscale_frame = CollapsibleFrame(advanced_right, title=self.translate("upscale"))
        upscale_frame.pack(fill="x", pady=5)
        self.create_param_widgets(config.get("upscale"), upscale_frame.content_frame, "upscale")
        
        colorizer_frame = CollapsibleFrame(advanced_right, title=self.translate("colorizer"))
        colorizer_frame.pack(fill="x", pady=5)
        self.create_param_widgets(config.get("colorizer"), colorizer_frame.content_frame, "colorizer")
        
        # === 选项标签页 ===
        options_left = self.main_view_widgets['options_left_frame']
        
        cli_frame = CollapsibleFrame(options_left, title=self.translate("cli_options"))
        cli_frame.pack(fill="x", pady=5)
        cli_params = config.get("cli", {})
        self.create_param_widgets(cli_params, cli_frame.content_frame, "cli")

        # Set initial state for the template switch
        self._update_template_state()

        # Set initial visibility of font settings
        initial_renderer = config.get("render", {}).get("renderer", "default")
        self._on_renderer_changed(initial_renderer)

        # Set initial visibility of font settings
        initial_renderer = config.get("render", {}).get("renderer", "default")
        self._on_renderer_changed(initial_renderer)

    async def _process_txt_import_async(self):
        """异步处理TXT文件导入到JSON，避免UI卡死"""
        try:
            from services.workflow_service import import_with_custom_template, get_template_path_from_config

            template_path = get_template_path_from_config()
            successful_imports = 0
            total_to_import = 0
            files_to_process = self._resolve_input_files()

            # 先统计需要处理的文件
            files_to_import = []
            for file_path in files_to_process:
                json_path = os.path.splitext(file_path)[0] + "_translations.json"
                txt_path = os.path.splitext(file_path)[0] + "_translations.txt"
                
                if os.path.exists(txt_path) and os.path.exists(json_path):
                    files_to_import.append((txt_path, json_path))
                elif not os.path.exists(json_path):
                    self.update_log(f"跳过导入，因为 {os.path.basename(json_path)} 不存在。\n")
                else:
                    self.update_log(f"跳过导入，因为 {os.path.basename(txt_path)} 不存在。\n")

            total_to_import = len(files_to_import)
            
            if total_to_import == 0:
                self.update_log("未找到对应的TXT/JSON文件对进行导入预处理。\n")
                return False

            self.update_log(f"找到 {total_to_import} 个文件对需要导入处理...\n")
            
            # 异步处理每个文件
            for i, (txt_path, json_path) in enumerate(files_to_import):
                if self.stop_requested.is_set():
                    self.update_log("TXT导入过程被用户停止。\n")
                    break

                self.update_log(f"[{i+1}/{total_to_import}] 正在从 {os.path.basename(txt_path)} 导入到 {os.path.basename(json_path)}...\n")
                
                # 使用 run_in_executor 异步执行IO操作
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, 
                    import_with_custom_template,
                    txt_path, json_path, template_path
                )
                
                self.update_log(f"导入结果: {result}\n")
                if "成功" in result:
                    successful_imports += 1
                
                # 短暂暂停让UI有机会更新
                await asyncio.sleep(0.1)

            self.update_log(f"导入预处理完成: {successful_imports}/{total_to_import} 个文件被成功更新。\n")
            return successful_imports > 0

        except Exception as e:
            self.update_log(f"TXT导入过程中发生错误: {e}\n")
            import traceback
            self.update_log(f"详细错误信息: {traceback.format_exc()}\n")
            return False

    def add_files(self):
        files = filedialog.askopenfilenames(parent=self.app)
        for f in files:
            self.input_files.append(f)
        self.update_file_list_display()

    def add_folder(self):
        folder = filedialog.askdirectory(parent=self.app)
        if folder:
            self.input_files.append(folder)
        self.update_file_list_display()

    def clear_file_list(self):
        self.input_files = []
        self.update_file_list_display()
        
    def remove_selected_files(self):
        listbox = self.main_view_widgets['file_listbox']
        selected_indices = listbox.curselection()
        for i in reversed(selected_indices):
            self.input_files.pop(i)
        self.update_file_list_display()

    def update_file_list_display(self):
        listbox = self.main_view_widgets['file_listbox']
        listbox.delete(0, "end")
        for i, f in enumerate(self.input_files):
            base_f = os.path.basename(f)
            listbox.insert("end", base_f)
            
    def select_output_folder(self):
        folder = filedialog.askdirectory(parent=self.app)
        if folder:
            entry = self.main_view_widgets['output_folder_entry']
            entry.delete(0, "end")
            entry.insert(0, folder)
            self.save_output_path(folder)

    def open_output_folder(self):
        import subprocess
        output_dir = self.main_view_widgets['output_folder_entry'].get()
        if not output_dir:
            messagebox.showwarning("路径为空", "输出文件夹路径为空。")
            return
        
        if not os.path.isdir(output_dir):
            messagebox.showerror("路径无效", f"指定的路径不是一个有效的文件夹:\n{output_dir}")
            return

        try:
            if sys.platform == "win32":
                os.startfile(os.path.realpath(output_dir))
            elif sys.platform == "darwin": # macOS
                subprocess.run(["open", output_dir])
            else: # Linux and other UNIX-like
                subprocess.run(["xdg-open", output_dir])
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开文件夹: {e}")

    def save_output_path(self, path):
        try:
            with open(self.user_config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            config_data['last_output_path'] = path
            with open(self.user_config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.update_log(f"Error saving output path: {e}\n")

    def load_and_apply_output_path(self):
        try:
            if os.path.exists(self.user_config_path):
                with open(self.user_config_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                last_path = config_data.get("last_output_path")
                if last_path:
                    output_entry = self.main_view_widgets.get('output_folder_entry')
                    if output_entry:
                        output_entry.delete(0, "end")
                        output_entry.insert(0, last_path)
        except Exception as e:
            self.update_log(f"Error loading output path: {e}\n")

    def stop_translation(self):
        if self.translation_process and self.translation_process.poll() is None:  # 进程还在运行
            self.update_log("正在强制终止翻译进程...\n")
            try:
                # 先尝试优雅终止
                self.translation_process.terminate()
                
                # 等待5秒
                try:
                    self.translation_process.wait(timeout=5)
                    self.update_log("翻译进程已优雅终止\n")
                except subprocess.TimeoutExpired:
                    # 如果5秒后进程还存在，强制杀死
                    self.update_log("使用强制杀死进程...\n")
                    self.translation_process.kill()
                    self.translation_process.wait()
                    self.update_log("翻译进程已被强制终止\n")
                    
            except Exception as e:
                self.update_log(f"终止进程时出错: {e}\n")
            finally:
                self.translation_process = None
                self.translation_process_pid = None
                self._reset_start_button()
        else:
            self.update_log("没有正在运行的翻译进程\n")

    def _reset_start_button(self):
        self.main_view_widgets['start_translation_button'].configure(
            text="开始翻译",
            command=self.start_translation,
            fg_color=("#2CC985", "#2FA572"),
            hover_color=("#2FA572", "#106A43")
        )

    def _run_full_pipeline_thread(self):
        # Check if import is needed
        config_dict_for_check = self.get_config_from_widgets(as_dict=True)
        cli_params_for_check = config_dict_for_check.get('cli', {})
        load_text_enabled = cli_params_for_check.get('load_text', False)
        template_enabled = cli_params_for_check.get('template', False)

        if load_text_enabled and template_enabled:
            self.update_log("检测到加载文本和模板模式同时开启，正在执行从TXT文件导入翻译...\n")
            
            try:
                # Run the import process SYNCHRONOUSLY within this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(self._process_txt_import_async())
                loop.close()
                if result is False:
                    self.update_log("TXT导入预处理失败，任务中止。\n")
                    self.app.after(0, self._reset_start_button)
                    return
            except Exception as e:
                self.update_log(f"启动TXT导入时出错: {e}\n")
                self.app.after(0, self._reset_start_button)
                return
        
        # Now that import is done (or was not needed), run the main translation process
        self.run_translation_thread()

    def start_translation(self):
        # This method now only sets up the environment and starts the single pipeline thread.
        try:
            # --- Log config at the start of translation ---
            import json
            config_dict_for_log = self.get_config_from_widgets(as_dict=True)
            self.update_log(f"DEBUG: Final config from UI: {json.dumps(config_dict_for_log, indent=2, ensure_ascii=False)}\n")
            # --- End log ---

            config_dict = self.get_config_from_widgets(as_dict=True)
            
            translator_params = {}
            if 'cli' in config_dict:
                translator_params.update(config_dict.pop('cli'))
            
            # 提取字体路径并设置为翻译器参数
            render_config = config_dict.get('render', {})
            font_filename = render_config.get('font_path')
            if font_filename:
                # 构建完整的字体路径
                font_full_path = os.path.join(os.path.dirname(__file__), '..', 'fonts', font_filename)
                font_full_path = os.path.abspath(font_full_path)
                if os.path.exists(font_full_path):
                    translator_params['font_path'] = font_full_path
                    self.update_log(f"设置翻译器字体路径: {font_full_path}\n")
                else:
                    self.update_log(f"警告: 字体文件不存在: {font_full_path}\n")
            
            translator_params.update(config_dict)
            translator_params['is_ui_mode'] = True

            self.update_log("正在初始化翻译引擎... (首次运行需要一些时间)\n")
            MangaTranslator, _ = get_manga_translator_classes()
            self.service = MangaTranslator(params=translator_params)

            # Setup stop mechanism
            self.stop_requested.clear()

            async def stop_check_hook(state, finished):
                if self.stop_requested.is_set():
                    raise TranslationInterrupt("Translation stopped by user.")
            
            self.service.add_progress_hook(stop_check_hook)

        except Exception as e:
            self.update_log(f"初始化翻译引擎时出错: {e}\n")
            messagebox.showerror("初始化错误", f"初始化翻译引擎时出错: {e}")
            return

        if self.translation_process and self.translation_process.is_alive():
            self.update_log("\n翻译已在运行。请使用停止按钮终止当前翻译。")
            return

        # Update button state to Stop
        self.main_view_widgets['start_translation_button'].configure(
            text="停止翻译",
            command=self.stop_translation,
            fg_color=("#DB4437", "#C53929"),
            hover_color=("#C53929", "#B03021")
        )

        # 创建翻译配置文件
        temp_config_path = self._create_temp_config()
        
        # Start the subprocess for translation
        try:
            # 使用subprocess运行翻译脚本
            script_path = os.path.join(os.path.dirname(__file__), 'translation_worker.py')
            
            # 创建翻译工作脚本
            self._create_translation_worker_script()
            
            # 简化的进程启动方式
            self.translation_process = subprocess.Popen(
                [sys.executable, '-u', script_path, temp_config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 合并stdout和stderr
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',  # 强制使用UTF-8编码
                errors='ignore',   # 忽略编码错误
                env=dict(os.environ, 
                        PYTHONUNBUFFERED='1', 
                        PYTHONIOENCODING='utf-8',
                        PYTHONUTF8='1'),  # 强制Python使用UTF-8
                cwd=os.path.dirname(__file__)
            )
            
            self.translation_process_pid = self.translation_process.pid
            self.update_log(f"翻译进程已启动，PID: {self.translation_process_pid}\n")
            
            # 启动简单的监控线程
            monitor_thread = threading.Thread(target=self._monitor_translation_simple, daemon=True)
            monitor_thread.start()
            
        except Exception as e:
            self.update_log(f"启动翻译进程失败: {e}\n")
            import traceback
            self.update_log(f"详细错误: {traceback.format_exc()}\n")
            self._reset_start_button()

    def _create_temp_config(self):
        """创建临时配置文件供子进程使用"""
        temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        temp_config_path = os.path.join(temp_dir, 'translation_config.json')
        
        config_data = {
            'config_dict': self.get_config_from_widgets(as_dict=True),
            'input_files': self.input_files,
            'output_folder': self.main_view_widgets['output_folder_entry'].get(),
            'root_dir': self.root_dir
        }
        
        with open(temp_config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        
        return temp_config_path
    
    def _create_translation_worker_script(self):
        """创建翻译工作脚本"""
        script_path = os.path.join(os.path.dirname(__file__), 'translation_worker.py')
        
        script_content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import json
import asyncio
import logging
from pathlib import Path

# 强制设置UTF-8编码
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)

# 强制刷新输出
def flush_print(text):
    try:
        print(text, flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception as e:
        # 如果输出出错，尝试用ASCII输出
        try:
            print(str(text).encode('ascii', 'ignore').decode('ascii'), flush=True)
        except:
            pass

# 配置日志输出到stdout
def setup_logging():
    try:
        # 清除现有handlers
        logging.getLogger().handlers.clear()
        
        # 创建stdout handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        
        # 设置根logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
        
        # 确保manga_translator的日志也输出
        manga_logger = logging.getLogger('manga_translator')
        manga_logger.setLevel(logging.INFO)
        manga_logger.addHandler(handler)
    except Exception as e:
        flush_print(f"设置日志时出错: {e}")

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

def main():
    try:
        if len(sys.argv) != 2:
            flush_print("Usage: translation_worker.py <config_file>")
            sys.exit(1)
        
        config_file = sys.argv[1]
        flush_print(f"翻译工作进程启动，PID: {os.getpid()}")
        flush_print(f"配置文件: {config_file}")
        
        # 设置日志
        setup_logging()
        
        # 加载配置
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        config_dict = config_data['config_dict']
        input_files = config_data['input_files']
        output_folder = config_data['output_folder']
        
        flush_print(f"输入文件数: {len(input_files)}")
        flush_print(f"输出文件夹: {output_folder}")
        
        # 导入翻译相关模块
        flush_print("正在导入翻译模块...")
        try:
            from manga_translator.config import (
                Config, RenderConfig, UpscaleConfig, TranslatorConfig, DetectorConfig,
                ColorizerConfig, InpainterConfig, OcrConfig
            )
            from manga_translator.manga_translator import MangaTranslator
            from PIL import Image
            flush_print("翻译模块导入成功")
        except Exception as e:
            flush_print(f"导入翻译模块失败: {e}")
            import traceback
            flush_print(traceback.format_exc())
            return
        
        # 初始化翻译器
        translator_params = {}
        if 'cli' in config_dict:
            translator_params.update(config_dict.pop('cli'))
        
        # 提取字体路径并设置为翻译器参数
        render_config = config_dict.get('render', {})
        font_filename = render_config.get('font_path')
        if font_filename:
            # 构建完整的字体路径
            font_full_path = os.path.join(os.path.dirname(__file__), '..', 'fonts', font_filename)
            font_full_path = os.path.abspath(font_full_path)
            if os.path.exists(font_full_path):
                translator_params['font_path'] = font_full_path
                flush_print(f"设置翻译器字体路径: {font_full_path}")
            else:
                flush_print(f"警告: 字体文件不存在: {font_full_path}")
        
        translator_params.update(config_dict)
        translator_params['is_ui_mode'] = True
        
        flush_print("正在初始化翻译引擎...")
        try:
            translator = MangaTranslator(params=translator_params)
            flush_print("翻译引擎初始化完成")
        except Exception as e:
            flush_print(f"翻译引擎初始化失败: {e}")
            import traceback
            flush_print(traceback.format_exc())
            return
        
        # 解析输入文件
        flush_print("正在解析输入文件...")
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'}
        resolved_files = []
        
        for path in input_files:
            if os.path.isfile(path):
                _, ext = os.path.splitext(path.lower())
                if ext in image_extensions:
                    resolved_files.append(path)
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        _, ext = os.path.splitext(file.lower())
                        if ext in image_extensions:
                            resolved_files.append(os.path.join(root, file))
        
        if not resolved_files:
            flush_print("没有找到有效的图片文件")
            return
            
        flush_print(f"找到 {len(resolved_files)} 个图片文件")
        
        # 创建配置对象
        try:
            config = Config(
                render=RenderConfig(**config_dict.get('render', {})),
                upscale=UpscaleConfig(**config_dict.get('upscale', {})),
                translator=TranslatorConfig(**config_dict.get('translator', {})),
                detector=DetectorConfig(**config_dict.get('detector', {})),
                colorizer=ColorizerConfig(**config_dict.get('colorizer', {})),
                inpainter=InpainterConfig(**config_dict.get('inpainter', {})),
                ocr=OcrConfig(**config_dict.get('ocr', {}))
            )
            flush_print("配置对象创建成功")
        except Exception as e:
            flush_print(f"创建配置对象失败: {e}")
            import traceback
            flush_print(traceback.format_exc())
            return
        
        # 处理每个文件
        for i, file_path in enumerate(resolved_files):
            flush_print(f"\n=== [{i+1}/{len(resolved_files)}] 处理文件: {os.path.basename(file_path)} ===")
            
            try:
                # 加载图片
                flush_print(f"  -> 加载图片: {os.path.basename(file_path)}")
                image = Image.open(file_path)
                image.name = file_path
                flush_print(f"  -> 图片尺寸: {image.size}")
                
                flush_print(f"  -> 开始翻译处理...")
                
                # 异步翻译
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                ctx = loop.run_until_complete(translator.translate(image, config, image_name=image.name))
                loop.close()
                
                if ctx and ctx.result:
                    os.makedirs(output_folder, exist_ok=True)
                    output_filename = "translated_" + os.path.basename(file_path)
                    final_output_path = os.path.join(output_folder, output_filename)
                    
                    image_to_save = ctx.result
                    if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                        image_to_save = image_to_save.convert('RGB')
                    
                    image_to_save.save(final_output_path)
                    flush_print(f"  -> ✅ 翻译完成: {os.path.basename(final_output_path)}")
                    
                    # 显示识别的文本信息
                    if hasattr(ctx, 'text_regions') and ctx.text_regions:
                        flush_print(f"  -> 识别到 {len(ctx.text_regions)} 个文本区域")
                        for j, region in enumerate(ctx.text_regions[:3]):  # 只显示前3个
                            text = getattr(region, 'text', '') or getattr(region, 'translation', '')
                            if text:
                                flush_print(f"     区域{j+1}: {text[:50]}{'...' if len(text) > 50 else ''}")
                else:
                    flush_print(f"  -> ❌ 翻译失败: {os.path.basename(file_path)}")
                    
            except Exception as e:
                flush_print(f"  -> ❌ 处理文件时出错 {os.path.basename(file_path)}: {e}")
                import traceback
                flush_print(traceback.format_exc())
        
        flush_print("\n=== 翻译进程完成 ===")
        
    except Exception as e:
        flush_print(f"翻译工作进程出错: {e}")
        import traceback
        flush_print(traceback.format_exc())
        sys.exit(1)
    finally:
        # 清理临时文件
        try:
            if 'config_file' in locals() and os.path.exists(config_file):
                os.remove(config_file)
                flush_print(f"已清理临时配置文件: {config_file}")
        except Exception as e:
            flush_print(f"清理临时文件出错: {e}")

if __name__ == '__main__':
    main()
'''
        
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        return script_path
    
    def _monitor_translation_simple(self):
        """简化的翻译进程监控"""
        try:
            self.update_log("开始监控翻译进程输出...\n")
            
            while self.translation_process and self.translation_process.poll() is None:
                try:
                    line = self.translation_process.stdout.readline()
                    if line:
                        line_text = line.strip()
                        if line_text:
                            # 使用partial来避免闭包问题
                            from functools import partial
                            update_func = partial(self._update_log_safe, line_text + '\n')
                            self.app.after(0, update_func)
                except UnicodeDecodeError as decode_error:
                    from functools import partial
                    error_func = partial(self._update_log_safe, f"编码错误，跳过一行: {decode_error}\n")
                    self.app.after(0, error_func)
                    continue
                except Exception as read_error:
                    from functools import partial
                    error_func = partial(self._update_log_safe, f"读取输出时出错: {read_error}\n")
                    self.app.after(0, error_func)
                    break
            
            # 读取剩余输出
            try:
                remaining_output, _ = self.translation_process.communicate(timeout=5)
                if remaining_output:
                    lines = remaining_output.strip().split('\n')
                    for output_line in lines:
                        if output_line.strip():
                            from functools import partial
                            final_func = partial(self._update_log_safe, output_line.strip() + '\n')
                            self.app.after(0, final_func)
            except subprocess.TimeoutExpired:
                from functools import partial
                timeout_func = partial(self._update_log_safe, "等待进程结束超时\n")
                self.app.after(0, timeout_func)
            except UnicodeDecodeError as decode_error:
                from functools import partial
                decode_func = partial(self._update_log_safe, f"最终输出编码错误: {decode_error}\n")
                self.app.after(0, decode_func)
            except Exception as comm_error:
                from functools import partial
                comm_func = partial(self._update_log_safe, f"读取最终输出时出错: {comm_error}\n")
                self.app.after(0, comm_func)
            
            # 结束处理
            return_code = self.translation_process.returncode if self.translation_process else None
            from functools import partial
            end_func = partial(self._update_log_safe, f"翻译进程结束，返回码: {return_code}\n")
            self.app.after(0, end_func)
            self.app.after(0, self._reset_start_button)
            
        except Exception as monitor_error:
            from functools import partial
            monitor_func = partial(self._update_log_safe, f"监控进程时出错: {monitor_error}\n")
            self.app.after(0, monitor_func)
            self.app.after(0, self._reset_start_button)
    
    def _update_log_safe(self, text):
        """安全的日志更新方法"""
        self.update_log(text)
    
    def _monitor_translation_process_simple(self):
        """简化的进程监控 - 使用队列确保日志不丢失"""
        try:
            while self.translation_process and self.translation_process.poll() is None:
                # 读取stdout
                if self.translation_process.stdout:
                    try:
                        line = self.translation_process.stdout.readline()
                        if line and line.strip():
                            self.log_queue.put(('stdout', line.strip()))
                    except:
                        pass
                
                # 读取stderr  
                if self.translation_process.stderr:
                    try:
                        line = self.translation_process.stderr.readline()
                        if line and line.strip():
                            self.log_queue.put(('stderr', line.strip()))
                    except:
                        pass
                        
            # 进程结束，读取剩余输出
            if self.translation_process:
                try:
                    stdout, stderr = self.translation_process.communicate(timeout=3)
                    
                    if stdout:
                        for line in stdout.strip().split('\n'):
                            if line.strip():
                                self.log_queue.put(('stdout', line.strip()))
                                
                    if stderr:
                        for line in stderr.strip().split('\n'):
                            if line.strip():
                                self.log_queue.put(('stderr', line.strip()))
                except:
                    pass
            
            # 结束标记
            self.log_queue.put(('end', None))
            
        except Exception as e:
            self.log_queue.put(('error', f"监控进程出错: {e}"))
    
    def _process_translation_log_queue(self):
        """处理翻译日志队列"""
        try:
            while True:
                try:
                    msg_type, content = self.log_queue.get_nowait()
                    
                    if msg_type == 'stdout':
                        self.update_log(content + '\n')
                    elif msg_type == 'stderr':
                        self.update_log(f"[ERROR] {content}\n")
                    elif msg_type == 'error':
                        self.update_log(content + '\n')
                    elif msg_type == 'end':
                        self.update_log("翻译进程结束\n")
                        self._reset_start_button()
                        return  # 结束队列处理
                        
                except queue.Empty:
                    break
                    
        except Exception as e:
            self.update_log(f"处理日志队列出错: {e}\n")
        
        # 如果进程还在运行，继续处理队列
        if self.translation_process and self.translation_process.poll() is None:
            self.app.after(100, self._process_translation_log_queue)
        else:
            # 进程已结束，最后处理一次队列
            self.app.after(100, self._process_translation_log_queue_final)
    
    def _process_translation_log_queue_final(self):
        """最终处理剩余的日志"""
        try:
            while True:
                try:
                    msg_type, content = self.log_queue.get_nowait()
                    if msg_type == 'stdout':
                        self.update_log(content + '\n')
                    elif msg_type == 'stderr':
                        self.update_log(f"[ERROR] {content}\n")
                    elif msg_type == 'error':
                        self.update_log(content + '\n')
                except queue.Empty:
                    break
        except:
            pass
        finally:
            self._reset_start_button()
            self.update_log("翻译监控完成\n")
    
    def _monitor_translation_process(self):
        """监控翻译进程的输出"""
        try:
            while self.translation_process and self.translation_process.poll() is None:
                # 读取stdout
                try:
                    output = self.translation_process.stdout.readline()
                    if output:
                        output = output.strip()
                        if output:
                            # 直接在主线程中更新，避免lambda闭包问题
                            def update_ui(text=output):
                                self.update_log(text + '\n')
                            self.app.after(0, update_ui)
                except:
                    pass
                
                # 读取stderr
                try:
                    error = self.translation_process.stderr.readline() 
                    if error:
                        error = error.strip()
                        if error:
                            def update_ui_err(text=error):
                                self.update_log(f"[ERROR] {text}\n")
                            self.app.after(0, update_ui_err)
                except:
                    pass
                    
            # 读取剩余输出
            if self.translation_process:
                try:
                    stdout, stderr = self.translation_process.communicate(timeout=2)
                    
                    if stdout and stdout.strip():
                        for line in stdout.strip().split('\n'):
                            if line.strip():
                                def update_final(text=line.strip()):
                                    self.update_log(text + '\n')
                                self.app.after(0, update_final)
                                
                    if stderr and stderr.strip():
                        for line in stderr.strip().split('\n'):
                            if line.strip():
                                def update_final_err(text=line.strip()):
                                    self.update_log(f"[ERROR] {text}\n")
                                self.app.after(0, update_final_err)
                except:
                    pass
                    
        except Exception as e:
            def show_error():
                self.update_log(f"监控进程时出错: {e}\n")
            self.app.after(0, show_error)
        finally:
            # 重置按钮
            self.app.after(0, self._reset_start_button)
            def show_end():
                self.update_log("翻译进程监控结束\n")
            self.app.after(0, show_end)

    def run_translation_thread(self):
        _, TranslationInterrupt = get_manga_translator_classes()
        try:
            asyncio.run(self._run_translation_direct())
        except TranslationInterrupt as e:
            self.update_log(f"\n翻译已停止: {e}")
        except Exception as e:
            self.update_log(f"\n发生未知错误: {e}")
            traceback.print_exc()

    async def _run_translation_direct(self):
        self.update_log("Starting translation...\n")

        try:
            if not self.input_files:
                messagebox.showwarning("输入缺失", "请至少添加一个要翻译的文件或文件夹。")
                return

            base_output_dir = self.main_view_widgets['output_folder_entry'].get()
            if not base_output_dir:
                messagebox.showwarning("输入缺失", "请选择输出文件夹。" )
                return

            resolved_image_paths = self._resolve_input_files()
            if not resolved_image_paths:
                self.update_log("在指定的路径中没有找到有效的图片文件。\n")
                return
            
            self.update_log(f"Found {len(resolved_image_paths)} images. Starting sequential processing...\n")

            config = self.get_config_from_widgets()
            config_dict = self.get_config_from_widgets(as_dict=True)
            
            input_folders = {os.path.normpath(path) for path in self.input_files if os.path.isdir(path)}

            for i, file_path in enumerate(resolved_image_paths):
                self.update_log(f"Processing image {i+1}/{len(resolved_image_paths)}: {os.path.basename(file_path)}\n")
                try:
                    image = Image.open(file_path)
                    image.name = file_path
                    
                    ctx = await self.service.translate(image, config, image_name=image.name)

                    # --- Begin custom export logic for save_text + template mode ---
                    try:
                        cli_config = config_dict.get('cli', {})
                        if cli_config.get('save_text') and cli_config.get('template'):
                            json_path = os.path.splitext(file_path)[0] + "_translations.json"
                            if os.path.exists(json_path):
                                self.update_log(f"执行模板导出: {os.path.basename(json_path)}...\n")
                                
                                # Lazily import the required functions
                                generate_text_from_template, _, _, get_default_template_path = get_workflow_service()
                                template_path = get_default_template_path()
                                
                                # Run the export function
                                export_result = generate_text_from_template(json_path, template_path)
                                self.update_log(f"模板导出结果: {export_result}\n")
                            else:
                                self.update_log(f"跳过模板导出，因为未找到 {os.path.basename(json_path)}。\n")
                    except Exception as e:
                        self.update_log(f"执行模板导出时出错: {e}\n")
                    # --- End custom export logic ---

                    if ctx and ctx.result:
                        final_output_dir = base_output_dir
                        parent_dir = os.path.normpath(os.path.dirname(file_path))
                        
                        for folder in input_folders:
                            if parent_dir.startswith(folder):
                                final_output_dir = os.path.join(base_output_dir, os.path.basename(folder))
                                break
                        
                        os.makedirs(final_output_dir, exist_ok=True)
                        output_filename = "translated_" + os.path.basename(file_path)
                        final_output_path = os.path.join(final_output_dir, output_filename)
                        
                        image_to_save = ctx.result
                        if final_output_path.lower().endswith(('.jpg', '.jpeg')) and image_to_save.mode in ('RGBA', 'LA'):
                            self.update_log(f"Converting image to RGB for JPEG saving: {os.path.basename(file_path)}\n")
                            image_to_save = image_to_save.convert('RGB')

                        image_to_save.save(final_output_path)
                        self.update_log(f"Image {i+1}/{len(resolved_image_paths)}: Translation complete. Saved to {final_output_path}\n")
                    else:
                        self.update_log(f"Image {i+1}/{len(resolved_image_paths)}: Translation failed for {os.path.basename(file_path)}.\n")

                except TranslationInterrupt:
                    self.update_log(f"\nTranslation stopped by user. Halting process.")
                    break
                except Exception as e:
                    self.update_log(f"An error occurred while processing {os.path.basename(file_path)}: {e}\n")
                    if not config_dict.get('cli', {}).get('ignore_errors', False):
                        self.update_log("Processing halted due to error. To continue on errors, enable 'Ignore Errors' in options.\n")
                        import traceback
                        traceback.print_exc()
                        break
                    else:
                        import traceback
                        traceback.print_exc()

        except Exception as e:
            self.update_log(f"An unexpected error occurred in the translation process: {e}\n")
            import traceback
            traceback.print_exc()
        finally:
            self.main_view_widgets['start_translation_button'].configure(
                text="开始翻译",
                command=self.start_translation,
                fg_color=("#2CC985", "#2FA572"),
                hover_color=("#2FA572", "#106A43")
            )
            self.update_log("Translation finished.\n")
            self.check_and_prompt_editor_entry()

    def check_and_prompt_editor_entry(self):
        """检查是否需要提示用户进入编辑器"""
        try:
            config_dict = self.get_config_from_widgets(as_dict=True)
            cli_config = config_dict.get('cli', {})
            
            save_text_enabled = cli_config.get('save_text', False)
            template_enabled = cli_config.get('template', False)
            
            self.update_log(f"调试：save_text={save_text_enabled}, template={template_enabled}\n")
            
            resolved_files = self._resolve_input_files()
            if save_text_enabled and not template_enabled and resolved_files:
                from tkinter import messagebox
                
                result = messagebox.askyesno(
                    title="翻译完成", 
                    message=f"翻译已完成！\n\n检测到您启用了保存文本功能，是否进入可视化编辑器查看和编辑翻译结果？\n\n处理的文件数量：{len(resolved_files)}"
                )
                
                if result:
                    self.enter_editor_with_processed_files()
                    
        except Exception as e:
            self.update_log(f"检查编辑器提示时出错: {e}\n")
            import traceback
            traceback.print_exc()
    
    def enter_editor_with_processed_files(self):
        """进入编辑器并加载处理完的文件"""
        try:
            self.show_view(EditorView)
            
            editor_frame = self.views[EditorView].editor_frame
            
            resolved_files = self._resolve_input_files()
            if not resolved_files:
                self.update_log("没有找到可加载的文件\n")
                return

            files_with_json = []
            for file_path in resolved_files:
                json_path = os.path.splitext(file_path)[0] + "_translations.json"
                if os.path.exists(json_path):
                    files_with_json.append(file_path)
                else:
                    self.update_log(f"警告: 未找到JSON文件: {os.path.basename(json_path)}\n")
            
            import gc
            gc.collect()
            
            editor_frame._add_files_to_list(resolved_files)
            
            if resolved_files:
                self.app.after(500, lambda: self._load_first_file_in_editor(editor_frame, resolved_files[0]))
            
            self.update_log(f"已加载 {len(resolved_files)} 个文件到编辑器\n")
            self.update_log(f"其中 {len(files_with_json)} 个文件有翻译数据\n")
            
            try:
                from ui_components import show_toast
                self.app.after(1500, lambda: show_toast(
                    editor_frame, 
                    f"已加载 {len(resolved_files)} 个处理完的文件，其中 {len(files_with_json)} 个包含翻译数据", 
                    level="success"
                ))
            except Exception as e:
                print(f"显示提示消息失败: {e}")
                
        except Exception as e:
            self.update_log(f"进入编辑器时出错: {e}\n")
            import traceback
            traceback.print_exc()
    
    def _load_first_file_in_editor(self, editor_frame, file_path):
        """在编辑器中加载第一个文件"""
        try:
            editor_frame._on_file_selected_from_list(file_path)
            
            # 检查是否成功加载了翻译数据
            json_path = os.path.splitext(file_path)[0] + "_translations.json"
            if os.path.exists(json_path) and editor_frame.regions_data:
                translated_regions = sum(1 for region in editor_frame.regions_data if region.get('translation', '').strip())
                self.update_log(f"成功加载 {os.path.basename(file_path)}，包含 {len(editor_frame.regions_data)} 个文本区域，其中 {translated_regions} 个已翻译\n")
            else:
                self.update_log(f"已加载 {os.path.basename(file_path)}，但可能没有翻译数据\n")
                
        except Exception as e:
            self.update_log(f"加载第一个文件时出错: {e}\n")
            import traceback
            traceback.print_exc()

    def update_log(self, text, disable_log=False, from_queue=False):
        log_textbox = self.main_view_widgets['log_textbox']
        log_textbox.configure(state="normal")
        log_textbox.insert("end", text)
        log_textbox.see("end")
        if disable_log:
            log_textbox.configure(state="disabled")

    def get_config_from_widgets(self, as_dict=False):
        def get_widget_value(widget):
            if isinstance(widget, ctk.CTkSwitch):
                return widget.get() == 1
            elif isinstance(widget, ctk.CTkEntry):
                value_str = widget.get()
                if value_str == '':
                    return None
                try:
                    if '.' in value_str:
                        return float(value_str)
                    else:
                        return int(value_str)
                except (ValueError, TypeError):
                    return value_str
            elif isinstance(widget, ctk.CTkOptionMenu):
                value = widget.get()
                return value if value != "不指定" else None
            return None

        config_dict = {}
        for full_key, widget in self.parameter_widgets.items():
            keys = full_key.split('.')
            d = config_dict
            for key in keys[:-1]:
                d = d.setdefault(key, {})
            
            value = get_widget_value(widget)

            # Special handling for layout_mode to convert display name to internal name
            if full_key == 'render.layout_mode' and isinstance(value, str):
                display_map = {
                    "默认模式 (有Bug)": "default",
                    "智能缩放 (推荐)": "smart_scaling",
                    "严格边界 (缩小字体)": "strict",
                    "固定字体 (扩大文本框)": "fixed_font",
                    "完全禁用 (裁剪文本)": "disable_all"
                }
                value = display_map.get(value, 'smart_scaling')
            
            # Special handling for target_lang to convert Chinese display name to language code
            elif full_key == 'translator.target_lang' and isinstance(value, str):
                from services.translation_service import TranslationService
                translation_service = TranslationService()
                lang_map = translation_service.get_target_languages()
                # Find the language code for the Chinese display name
                for code, name in lang_map.items():
                    if name == value:
                        value = code
                        break

            if value is not None:
                d[keys[-1]] = value

        # --- DEBUGGING ---
        # import json
        # self.update_log(f"DEBUG: Final config from UI: {json.dumps(config_dict, indent=2, ensure_ascii=False)}\n")
        # --- END DEBUGGING ---

        if as_dict:
            return config_dict

        return Config(
            render=RenderConfig(**config_dict.get('render', {})),
            upscale=UpscaleConfig(**config_dict.get('upscale', {})),
            translator=TranslatorConfig(**config_dict.get('translator', {})),
            detector=DetectorConfig(**config_dict.get('detector', {})),
            colorizer=ColorizerConfig(**config_dict.get('colorizer', {})),
            inpainter=InpainterConfig(**config_dict.get('inpainter', {})),
            ocr=OcrConfig(**config_dict.get('ocr', {}))
        )

    def _on_load_text_toggled(self):
        load_switch = self.cli_widgets.get("load_text")
        save_switch = self.cli_widgets.get("save_text")
        
        if load_switch and save_switch and load_switch.get() == 1:
            save_switch.deselect()
            self._save_widget_change("cli.save_text", save_switch)
        
        self._update_template_state()
        self._save_widget_change("cli.load_text", load_switch)

    def _on_save_text_toggled(self):
        load_switch = self.cli_widgets.get("load_text")
        save_switch = self.cli_widgets.get("save_text")

        if load_switch and save_switch and save_switch.get() == 1:
            load_switch.deselect()
            self._save_widget_change("cli.load_text", load_switch)

        self._update_template_state()
        self._save_widget_change("cli.save_text", save_switch)

    def _update_template_state(self):
        load_switch = self.cli_widgets.get("load_text")
        save_switch = self.cli_widgets.get("save_text")
        template_switch = self.cli_widgets.get("template")

        if not all([load_switch, save_switch, template_switch]):
            return

        if load_switch.get() == 1 or save_switch.get() == 1:
            template_switch.configure(state="normal")
        else:
            template_switch.deselect()
            template_switch.configure(state="disabled")
            self._save_widget_change("cli.template", template_switch)

# Views现在可以直接访问导入的模块

class MainView(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        self.grid_columnconfigure(0, weight=1, minsize=300)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # --- Left Column (I/O) ---
        self.left_frame = ctk.CTkFrame(self, width=300)
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.left_frame.grid_rowconfigure(4, weight=1)

        self.source_frame = ctk.CTkFrame(self.left_frame)
        self.source_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.source_frame.grid_columnconfigure(0, weight=1)
        self.source_frame.grid_rowconfigure(3, weight=1)  # 让文件列表行可以扩展
        self.source_label = ctk.CTkLabel(self.source_frame, text=self.controller.translate("source"), font=ctk.CTkFont(weight="bold"))
        self.source_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        self.add_files_button = ctk.CTkButton(self.source_frame, text=self.controller.translate("add_files"), command=self.controller.add_files)
        self.add_files_button.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        self.add_folder_button = ctk.CTkButton(self.source_frame, text=self.controller.translate("add_folder"), command=self.controller.add_folder)
        self.add_folder_button.grid(row=2, column=0, padx=10, pady=5, sticky="ew")
        
        # 创建文件列表容器，包含滚动条
        self.file_list_container = ctk.CTkFrame(self.source_frame, fg_color="transparent")
        self.file_list_container.grid(row=3, column=0, padx=10, pady=5, sticky="nsew")
        self.file_list_container.grid_columnconfigure(0, weight=1)
        self.file_list_container.grid_rowconfigure(0, weight=1)
        
        self.file_listbox = Listbox(self.file_list_container, selectmode="extended", bg="#2B2B2B", fg="white", borderwidth=0, highlightthickness=0)
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        
        # 添加垂直滚动条
        self.file_list_scrollbar = ctk.CTkScrollbar(self.file_list_container, orientation="vertical", command=self.file_listbox.yview)
        self.file_list_scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_listbox.configure(yscrollcommand=self.file_list_scrollbar.set)
        
        self.file_list_buttons = ctk.CTkFrame(self.source_frame)
        self.file_list_buttons.grid(row=4, column=0, padx=10, pady=5, sticky="ew")
        self.remove_selected_button = ctk.CTkButton(self.file_list_buttons, text="移除所选项", command=self.controller.remove_selected_files)
        self.remove_selected_button.pack(side="left", expand=True, padx=2)
        self.clear_list_button = ctk.CTkButton(self.file_list_buttons, text=self.controller.translate("clear_list"), command=self.controller.clear_file_list)
        self.clear_list_button.pack(side="left", expand=True, padx=2)

        self.target_frame = ctk.CTkFrame(self.left_frame)
        self.target_frame.grid(row=1, column=0, padx=10, pady=10, sticky="ew")
        self.target_frame.grid_columnconfigure(0, weight=1)
        self.target_label = ctk.CTkLabel(self.target_frame, text=self.controller.translate("target"), font=ctk.CTkFont(weight="bold"))
        self.target_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        self.output_folder_entry = ctk.CTkEntry(self.target_frame, placeholder_text="选择输出文件夹...")
        self.output_folder_entry.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        self.output_folder_button = ctk.CTkButton(self.target_frame, text=self.controller.translate("open"), command=self.controller.select_output_folder)
        self.output_folder_button.grid(row=2, column=0, padx=10, pady=5, sticky="ew")

        self.open_output_dir_button = ctk.CTkButton(self.target_frame, text="打开输出文件夹", command=self.controller.open_output_folder)
        self.open_output_dir_button.grid(row=3, column=0, padx=10, pady=5, sticky="ew")

        self.start_translation_button = ctk.CTkButton(self.left_frame, text="开始翻译", command=self.controller.start_translation, fg_color="green", height=40)
        self.start_translation_button.grid(row=2, column=0, padx=10, pady=10, sticky="ew")

        self.editor_button = ctk.CTkButton(self.left_frame, text="视觉编辑器", command=lambda: self.controller.show_view(EditorView))
        self.editor_button.grid(row=3, column=0, padx=10, pady=10, sticky="ew")

        # --- Right Column (Settings & Log) ---
        self.right_container = ctk.CTkFrame(self, fg_color="transparent")
        self.right_container.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.right_container.grid_rowconfigure(0, weight=2)
        self.right_container.grid_rowconfigure(1, weight=1)
        self.right_container.grid_columnconfigure(0, weight=1)

        # 创建标签页视图
        self.settings_tabview = ctk.CTkTabview(self.right_container)
        self.settings_tabview.grid(row=0, column=0, sticky="nsew")
        
        # 添加标签页
        self.basic_tab = self.settings_tabview.add("基础设置")
        self.advanced_tab = self.settings_tabview.add("高级设置")
        self.options_tab = self.settings_tabview.add("选项")
        
        # 为每个标签页配置列布局 - 统一布局
        self.basic_tab.grid_columnconfigure(0, weight=1)
        self.basic_tab.grid_columnconfigure(1, weight=1)
        self.basic_tab.grid_rowconfigure(0, weight=1)
        
        self.advanced_tab.grid_columnconfigure(0, weight=1)
        self.advanced_tab.grid_columnconfigure(1, weight=1)
        self.advanced_tab.grid_rowconfigure(0, weight=1)
        
        # 修复第三个标签页的布局配置，保持一致
        self.options_tab.grid_columnconfigure(0, weight=1)
        self.options_tab.grid_columnconfigure(1, weight=1)  # 添加第二列配置
        self.options_tab.grid_rowconfigure(0, weight=1)
        
        # 为每个标签页创建滚动框架
        self.basic_left_frame = ctk.CTkScrollableFrame(self.basic_tab)
        self.basic_left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        self.basic_right_frame = ctk.CTkScrollableFrame(self.basic_tab)
        self.basic_right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        self.advanced_left_frame = ctk.CTkScrollableFrame(self.advanced_tab)
        self.advanced_left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        self.advanced_right_frame = ctk.CTkScrollableFrame(self.advanced_tab)
        self.advanced_right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        # 修复第三个标签页，添加左右两列保持一致
        self.options_left_frame = ctk.CTkScrollableFrame(self.options_tab)
        self.options_left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        self.options_right_frame = ctk.CTkScrollableFrame(self.options_tab)
        self.options_right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        # 设置快速滚动
        self.controller.app.after(100, lambda: self._setup_all_fast_scroll())

        self.log_frame = ctk.CTkFrame(self.right_container)
        self.log_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_frame.grid_rowconfigure(0, weight=1)
        
        self.log_textbox = ctk.CTkTextbox(self.log_frame, state="disabled", wrap="word")
        self.log_textbox.grid(row=0, column=0, sticky="nsew")

        self.controller.main_view_widgets = {
            "file_listbox": self.file_listbox,
            "output_folder_entry": self.output_folder_entry,
            "log_textbox": self.log_textbox,
            "start_translation_button": self.start_translation_button,
            # 添加新的标签页框架引用
            "basic_left_frame": self.basic_left_frame,
            "basic_right_frame": self.basic_right_frame,
            "advanced_left_frame": self.advanced_left_frame,
            "advanced_right_frame": self.advanced_right_frame,
            "options_left_frame": self.options_left_frame,
            "options_right_frame": self.options_right_frame
        }

        self.controller.create_main_view_settings_tabbed()
    
    def _setup_all_fast_scroll(self):
        """为所有滚动框架设置快速滚动"""
        scroll_frames = [
            self.basic_left_frame,
            self.basic_right_frame, 
            self.advanced_left_frame,
            self.advanced_right_frame,
            self.options_left_frame,
            self.options_right_frame
        ]
        
        for frame in scroll_frames:
            self._setup_fast_scroll(frame)
    
    def _setup_fast_scroll(self, scrollable_frame):
        """设置更快的滚动速度 - 简单有效的方法"""
        def fast_mousewheel(event):
            # 使用更大的滚动增量，再快一倍
            scrollable_frame._parent_canvas.yview_scroll(int(-1 * (event.delta / 30)), "units")
            return "break"
        
        # 绑定快速滚动事件
        scrollable_frame.bind("<MouseWheel>", fast_mousewheel)
        scrollable_frame._parent_canvas.bind("<MouseWheel>", fast_mousewheel)

class EditorView(ctk.CTkFrame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # 延迟导入EditorFrame来避免循环导入
        from editor_frame import EditorFrame
        
        self.editor_frame = EditorFrame(self,  
                                        return_callback=lambda: controller.show_view(MainView),
                                        shortcut_manager=controller.shortcut_manager)
        self.editor_frame.grid(row=0, column=0, sticky="nsew")

if __name__ == "__main__":
    app = App()
    app.mainloop()