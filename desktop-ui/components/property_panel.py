"""
属性面板组件
提供文本区域的详细编辑功能
"""
import customtkinter as ctk
import tkinter as tk
from tkinter import colorchooser
from typing import Dict, Any, Callable, Optional
import logging

from ui_components import CollapsibleFrame
from manga_translator.config import Ocr, Translator

from services import get_config_service, get_translation_service, get_ocr_service


class PropertyPanel(ctk.CTkScrollableFrame):
    """属性面板"""
    
    def __init__(self, parent, shortcut_manager=None, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.logger = logging.getLogger(__name__)
        self.callbacks: Dict[str, Callable] = {}
        self.widgets: Dict[str, Any] = {}
        self.shortcut_manager = shortcut_manager
        self.canvas_frame = None
        
        self.ocr_service = get_ocr_service()
        self.translation_service = get_translation_service()
        self.config_service = get_config_service()

        # 当前编辑的区域数据
        self.current_region_data = None
        self.region_index = None
        
        self._create_widgets()

    def set_canvas_frame(self, canvas_frame):
        """设置对canvas_frame的引用"""
        self.canvas_frame = canvas_frame

    
    
    def _create_widgets(self):
        """创建组件"""
        self.grid_columnconfigure(0, weight=1)
        
        # 区域信息
        self._create_region_info_section()
        
        # 文本内容
        self._create_text_section()
        
        # 样式设置
        self._create_style_section()

        # 蒙版编辑
        self._create_mask_edit_section()
        
        # 操作按钮
        self._create_action_section()
    
    def _create_region_info_section(self):
        """创建区域信息部分"""
        # 区域信息标题
        info_label = ctk.CTkLabel(self, text="区域信息", font=ctk.CTkFont(size=14, weight="bold"))
        info_label.grid(row=0, column=0, sticky="w", padx=5, pady=(5, 0))
        
        # 区域信息框架
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        info_frame.grid_columnconfigure(1, weight=1)
        
        # 区域索引
        ctk.CTkLabel(info_frame, text="索引:").grid(row=0, column=0, sticky="w", pady=2)
        self.widgets['index_label'] = ctk.CTkLabel(info_frame, text="-")
        self.widgets['index_label'].grid(row=0, column=1, sticky="w", padx=(10, 0))
        
        # 边界框信息
        ctk.CTkLabel(info_frame, text="位置:").grid(row=1, column=0, sticky="w", pady=2)
        self.widgets['bbox_label'] = ctk.CTkLabel(info_frame, text="-")
        self.widgets['bbox_label'].grid(row=1, column=1, sticky="w", padx=(10, 0))
        
        # 尺寸信息
        ctk.CTkLabel(info_frame, text="尺寸:").grid(row=2, column=0, sticky="w", pady=2)
        self.widgets['size_label'] = ctk.CTkLabel(info_frame, text="-")
        self.widgets['size_label'].grid(row=2, column=1, sticky="w", padx=(10, 0))

        # 角度信息
        ctk.CTkLabel(info_frame, text="角度:").grid(row=3, column=0, sticky="w", pady=2)
        self.widgets['angle_label'] = ctk.CTkLabel(info_frame, text="-")
        self.widgets['angle_label'].grid(row=3, column=1, sticky="w", padx=(10, 0))
    
    def _create_text_section(self):
        """创建文本编辑部分"""
        # 文本内容标题
        text_label = ctk.CTkLabel(self, text="文本内容", font=ctk.CTkFont(size=14, weight="bold"))
        text_label.grid(row=2, column=0, sticky="w", padx=5, pady=(15, 0))
        
        # OCR和翻译配置
        self._create_ocr_translate_config_section()
        
        # 原文
        ctk.CTkLabel(self, text="原文:", anchor="w").grid(row=5, column=0, sticky="ew", padx=5, pady=(5, 0))
        self.widgets['original_text'] = ctk.CTkTextbox(self, height=60)
        self.widgets['original_text'].grid(row=6, column=0, sticky="ew", padx=5, pady=2)
        
        # 绑定原文编辑事件
        self.widgets['original_text'].bind("<KeyRelease>", self._on_original_text_change)
        self.widgets['original_text'].bind("<Button-1>", self._on_original_text_click)
        self.widgets['original_text'].bind("<FocusIn>", self._on_original_text_focus_in)
        self.widgets['original_text'].bind("<Double-Button-1>", self._on_original_text_double_click)
        
        # 原文编辑状态标签
        self.widgets['original_edit_status'] = ctk.CTkLabel(self, text="点击编辑原文", font=ctk.CTkFont(size=10), text_color="gray")
        self.widgets['original_edit_status'].grid(row=7, column=0, sticky="w", padx=5, pady=(0, 5))
        
        # 译文
        ctk.CTkLabel(self, text="译文:", anchor="w").grid(row=8, column=0, sticky="ew", padx=5, pady=(10, 0))
        self.widgets['translation_text'] = ctk.CTkTextbox(self, height=80)
        self.widgets['translation_text'].grid(row=9, column=0, sticky="ew", padx=5, pady=2)
        
        # 确保文本框可编辑
        self.widgets['translation_text'].configure(state="normal")
        
        # 绑定多个事件确保及时响应
        self.widgets['translation_text'].bind("<KeyRelease>", self._on_text_change)
        self.widgets['translation_text'].bind("<Button-1>", self._on_text_click)
        self.widgets['translation_text'].bind("<FocusIn>", self._on_text_focus_in)
        self.widgets['translation_text'].bind("<Double-Button-1>", self._on_text_double_click)
        self.widgets['original_text'].bind("<KeyPress>", self._handle_textbox_key_press)
        self.widgets['translation_text'].bind("<KeyPress>", self._handle_textbox_key_press)
        
        
        # 为文本框添加描述性属性
        self.widgets['translation_text']._textbox.configure(insertbackground="#000000")  # 设置光标颜色为黑色，与原文框一致
        self.widgets['translation_text']._textbox.configure(insertwidth=3)  # 设置光标宽度更宽
        self.widgets['translation_text']._textbox.configure(insertofftime=300)  # 光标闪烁关闭时间
        self.widgets['translation_text']._textbox.configure(insertontime=600)   # 光标闪烁打开时间
        self.widgets['translation_text']._textbox.configure(selectbackground="#0078d4")  # 设置选中背景色
        
        # 文本统计
        self.widgets['text_stats'] = ctk.CTkLabel(self, text="字符数: 0", font=ctk.CTkFont(size=10))
        self.widgets['text_stats'].grid(row=10, column=0, sticky="w", padx=5, pady=2)

    def _handle_textbox_key_press(self, event):
        """处理文本框中的按键事件，以允许全局快捷键"""
        if not self.shortcut_manager:
            return

        # 构造快捷键字符串
        parts = []
        if event.state & 0x4:  # Control
            parts.append("Control")
        if event.state & 0x8:  # Alt
            parts.append("Alt")
        if event.state & 0x1:  # Shift
            parts.append("Shift")
        
        keysym = event.keysym
        if len(keysym) == 1 and keysym.isalpha():
            keysym = keysym.lower()
        
        parts.append(keysym)
        shortcut_str = "<" + "-".join(parts) + ">"

        # 检查快捷键是否存在
        shortcut = self.shortcut_manager.shortcuts.get(shortcut_str)
        if shortcut and shortcut.enabled:
            # 在这里，我们允许所有上下文的快捷键，或者可以根据需要进行筛选
            # if shortcut.context == "global": 
            try:
                shortcut.callback()
                return "break"  # 阻止事件传播
            except Exception as e:
                self.logger.error(f"执行快捷键回调失败 {shortcut_str}: {e}")

        return None
    
    

    def _create_style_section(self):
        """创建样式设置部分"""
        # 样式标题
        style_label = ctk.CTkLabel(self, text="样式设置", font=ctk.CTkFont(size=14, weight="bold"))
        style_label.grid(row=11, column=0, sticky="w", padx=5, pady=(15, 0))
        
        # 样式框架
        style_frame = ctk.CTkFrame(self, fg_color="transparent")
        style_frame.grid(row=12, column=0, sticky="ew", padx=5, pady=5)
        style_frame.grid_columnconfigure(1, weight=1)
        
        # 字体大小
        ctk.CTkLabel(style_frame, text="字体大小:").grid(row=0, column=0, sticky="w", pady=2)
        font_frame = ctk.CTkFrame(style_frame, fg_color="transparent")
        font_frame.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        font_frame.grid_columnconfigure(0, weight=1)
        
        self.widgets['font_size'] = ctk.CTkEntry(font_frame, placeholder_text="12")
        self.widgets['font_size'].grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.widgets['font_size'].bind("<Return>", self._on_style_change)
        
        font_size_slider = ctk.CTkSlider(
            font_frame, 
            from_=8, 
            to=72, 
            number_of_steps=64,
            command=self._on_font_size_slider
        )
        font_size_slider.grid(row=1, column=0, sticky="ew", pady=2)
        self.widgets['font_size_slider'] = font_size_slider
        
        # 字体颜色
        ctk.CTkLabel(style_frame, text="字体颜色:").grid(row=1, column=0, sticky="w", pady=2)
        color_frame = ctk.CTkFrame(style_frame, fg_color="transparent")
        color_frame.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        color_frame.grid_columnconfigure(0, weight=1)  # FIX: Make entry expandable
        color_frame.grid_columnconfigure(1, weight=0)  # FIX: Keep button fixed size
        
        self.widgets['font_color'] = ctk.CTkEntry(color_frame, placeholder_text="#FFFFFF")
        self.widgets['font_color'].grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.widgets['font_color'].bind("<Return>", self._on_style_change)
        
        color_button = ctk.CTkButton(
            color_frame, 
            text="选择", 
            width=50,
            command=self._choose_color
        )
        color_button.grid(row=0, column=1)
        
        # 对齐方式
        ctk.CTkLabel(style_frame, text="对齐:").grid(row=2, column=0, sticky="w", pady=2)
        self.widgets['alignment'] = ctk.CTkOptionMenu(
            style_frame,
            values=["自动", "左对齐", "居中", "右对齐"],
            command=self._on_style_change
        )
        self.widgets['alignment'].grid(row=2, column=1, sticky="ew", padx=(10, 0))

        # 文字方向
        ctk.CTkLabel(style_frame, text="方向:").grid(row=3, column=0, sticky="w", pady=2)
        self.widgets['direction'] = ctk.CTkOptionMenu(
            style_frame,
            values=["自动", "横排", "竖排"],
            command=self._on_style_change
        )
        self.widgets['direction'].grid(row=3, column=1, sticky="ew", padx=(10, 0))

    def _create_mask_edit_section(self):
        self.mask_edit_collapsible_frame = CollapsibleFrame(self, title="蒙版编辑", start_expanded=False)
        self.mask_edit_collapsible_frame.grid(row=13, column=0, sticky="ew", padx=5, pady=5)
        
        content_frame = self.mask_edit_collapsible_frame.content_frame
        content_frame.grid_columnconfigure(0, weight=1)
        
        self.widgets['mask_tool_menu'] = ctk.CTkOptionMenu(content_frame, values=["不选择", "画笔", "橡皮擦"], command=lambda choice: self._execute_callback('set_edit_mode', choice))
        self.widgets['mask_tool_menu'].grid(row=0, column=0, pady=5, padx=5, sticky="ew")
        
        ctk.CTkLabel(content_frame, text="笔刷大小:").grid(row=1, column=0, pady=5, padx=5, sticky="w")
        self.widgets['brush_size_slider'] = ctk.CTkSlider(content_frame, from_=1, to=100, command=lambda val: self._execute_callback('brush_size_changed', val))
        self.widgets['brush_size_slider'].set(20)
        self.widgets['brush_size_slider'].grid(row=2, column=0, pady=5, padx=5, sticky="ew")
        
        self.widgets['show_mask_checkbox'] = ctk.CTkCheckBox(content_frame, text="显示蒙版", command=lambda: self._execute_callback('toggle_mask_visibility', self.widgets['show_mask_checkbox'].get()))
        self.widgets['show_mask_checkbox'].select()
        self.widgets['show_mask_checkbox'].grid(row=3, column=0, pady=5, padx=5, sticky="w")

        # 添加蒙版更新按钮
        self.widgets['update_mask_button'] = ctk.CTkButton(
            content_frame, 
            text="更新蒙版", 
            command=lambda: self._execute_callback('update_mask'),
            height=28
        )
        self.widgets['update_mask_button'].grid(row=4, column=0, pady=5, padx=5, sticky="ew")
        
        # 添加显示被优化掉区域的选项
        self.widgets['show_removed_checkbox'] = ctk.CTkCheckBox(
            content_frame, 
            text="显示被优化掉的区域", 
            command=lambda: self._execute_callback('toggle_removed_mask_visibility', self.widgets['show_removed_checkbox'].get())
        )
        self.widgets['show_removed_checkbox'].grid(row=5, column=0, pady=5, padx=5, sticky="w")

        self.mask_edit_collapsible_frame.grid_remove() # Hide by default
    
    def _create_action_section(self):
        """创建操作按钮部分"""
        # 操作标题
        action_label = ctk.CTkLabel(self, text="操作", font=ctk.CTkFont(size=14, weight="bold"))
        action_label.grid(row=15, column=0, sticky="w", padx=5, pady=(15, 0))
        
        # 操作按钮框架
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=16, column=0, sticky="ew", padx=5, pady=5)
        action_frame.grid_columnconfigure((0, 1, 2), weight=1)
        
        # 第一行按钮
        ctk.CTkButton(
            action_frame, 
            text="复制", 
            command=lambda: self._execute_callback('copy_region')
        ).grid(row=0, column=0, padx=2, pady=2, sticky="ew")
        
        ctk.CTkButton(
            action_frame, 
            text="粘贴", 
            command=lambda: self._execute_callback('paste_region')
        ).grid(row=0, column=1, padx=2, pady=2, sticky="ew")
        
        ctk.CTkButton(
            action_frame, 
            text="删除", 
            command=lambda: self._execute_callback('delete_region'),
            fg_color="#d32f2f",
            hover_color="#b71c1c"
        ).grid(row=0, column=2, padx=2, pady=2, sticky="ew")
    
    def _create_ocr_translate_config_section(self):
        config_frame = ctk.CTkFrame(self, fg_color="transparent")
        config_frame.grid(row=3, column=0, sticky="ew", padx=5, pady=5)
        config_frame.grid_columnconfigure(1, weight=1)

        if not all([self.ocr_service, self.translation_service, self.config_service]):
            error_label = ctk.CTkLabel(config_frame, text="错误：核心服务加载失败。", text_color="red")
            error_label.grid(row=0, column=0, columnspan=2)
            return
        
        # --- OCR Model ---
        ctk.CTkLabel(config_frame, text="OCR模型:").grid(row=0, column=0, sticky="w", pady=2)
        ocr_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        ocr_frame.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        ocr_frame.grid_columnconfigure(0, weight=1)
        
        ocr_models = self.ocr_service.get_available_models()
        ocr_config = self.config_service.get_config().get('ocr', {})
        default_ocr = ocr_config.get('ocr', ocr_models[0] if ocr_models else None)
        self.widgets['ocr_model'] = ctk.CTkOptionMenu(ocr_frame, values=ocr_models, command=self._on_ocr_model_change)
        if default_ocr and default_ocr in ocr_models:
            self.widgets['ocr_model'].set(default_ocr)
        self.widgets['ocr_model'].grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkButton(ocr_frame, text="识别", width=60, command=lambda: self._execute_callback('ocr_recognize')).grid(row=0, column=1)
        
        # --- Translator ---
        ctk.CTkLabel(config_frame, text="翻译器:").grid(row=1, column=0, sticky="w", pady=2)
        translator_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        translator_frame.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        translator_frame.grid_columnconfigure(0, weight=1)
        
        translators = self.translation_service.get_available_translators()
        translator_config = self.config_service.get_config().get('translator', {})
        default_translator = translator_config.get('translator', translators[0] if translators else None)
        self.widgets['translator'] = ctk.CTkOptionMenu(translator_frame, values=translators, command=self._on_translator_change)
        
        # 设置默认翻译器，支持name和value两种匹配方式
        if default_translator and translators:
            # 优先精确匹配
            if default_translator in translators:
                self.widgets['translator'].set(default_translator)
            else:
                # 尝试通过后端枚举映射
                try:
                    from manga_translator.config import Translator
                    # 查找对应的value
                    matched_value = None
                    if hasattr(Translator, default_translator):
                        # 通过name查找
                        matched_value = Translator[default_translator].value
                    else:
                        # 通过value查找
                        for t in Translator:
                            if t.value == default_translator:
                                matched_value = t.value
                                break
                    
                    if matched_value and matched_value in translators:
                        self.widgets['translator'].set(matched_value)
                    else:
                        self.widgets['translator'].set(translators[0])
                except ImportError:
                    self.widgets['translator'].set(translators[0])
        elif translators:
            self.widgets['translator'].set(translators[0])
        self.widgets['translator'].grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkButton(translator_frame, text="翻译", width=60, command=lambda: self._execute_callback('translate_text')).grid(row=0, column=1)
        
        # --- Target Language ---
        ctk.CTkLabel(config_frame, text="目标语言:").grid(row=2, column=0, sticky="w", pady=2)
        lang_map = self.translation_service.get_target_languages()
        self.lang_name_to_code = {v: k for k, v in lang_map.items()}
        lang_names = list(lang_map.values())
        
        default_lang_code = translator_config.get('target_lang', 'CHS')
        default_lang_name = lang_map.get(default_lang_code, lang_names[0] if lang_names else None)

        self.widgets['target_language'] = ctk.CTkOptionMenu(config_frame, values=lang_names, command=self._on_target_language_change)
        if default_lang_name: self.widgets['target_language'].set(default_lang_name)
        self.widgets['target_language'].grid(row=2, column=1, sticky="ew", padx=(10, 0))

    def load_region_data(self, region_data: Dict[str, Any], region_index: int):
        """加载区域数据到面板"""
        self.current_region_data = region_data
        self.region_index = region_index
        
        # 更新区域信息
        self.widgets['index_label'].configure(text=str(region_index))
        
        # 计算边界框
        bbox = self._calculate_bbox(region_data)
        if bbox:
            bbox_text = f"({bbox[0]:.0f}, {bbox[1]:.0f})"
            size_text = f"{bbox[2]-bbox[0]:.0f} × {bbox[3]-bbox[1]:.0f}"
            self.widgets['bbox_label'].configure(text=bbox_text)
            self.widgets['size_label'].configure(text=size_text)
        
        angle = region_data.get('angle', 0)
        self.widgets['angle_label'].configure(text=f"{angle:.1f}°")

        # 更新文本内容
        original_text = region_data.get('text', '')
        translation_text = region_data.get('translation', '')
        
        # 更新原文（现在可编辑）
        self.widgets['original_text'].delete("1.0", "end")
        self.widgets['original_text'].insert("1.0", original_text)
        
        # 更新译文
        self.widgets['translation_text'].delete("1.0", "end")
        self.widgets['translation_text'].insert("1.0", translation_text)
        
        # 更新文本统计
        self._update_text_stats()
        self._update_original_text_stats()
        
        # 更新样式
        self.widgets['font_size'].delete(0, "end")
        self.widgets['font_size'].insert(0, str(region_data.get('font_size', 12)))
        self.widgets['font_size_slider'].set(region_data.get('font_size', 12))
        
        self.widgets['font_color'].delete(0, "end")
        self.widgets['font_color'].insert(0, region_data.get('font_color', '#FFFFFF'))
        
        # Update alignment
        alignment_map_rev = {"auto": "自动", "left": "左对齐", "center": "居中", "right": "右对齐"}
        alignment_val = region_data.get('alignment', 'auto')
        self.widgets['alignment'].set(alignment_map_rev.get(alignment_val, "自动"))

        # Update direction
        direction_map_rev = {"auto": "自动", "h": "横排", "v": "竖排"}
        direction_val = region_data.get('direction', 'auto')
        self.widgets['direction'].set(direction_map_rev.get(direction_val, "自动"))
    
    def clear_panel(self):
        """清空面板"""
        self.current_region_data = None
        self.region_index = None
        
        # 清空所有字段
        self.widgets['index_label'].configure(text="-")
        self.widgets['bbox_label'].configure(text="-")
        self.widgets['size_label'].configure(text="-")
        self.widgets['angle_label'].configure(text="-")
        
        # 清空原文（现在可编辑）
        self.widgets['original_text'].delete("1.0", "end")
        self.widgets['original_edit_status'].configure(text="未选中文本框", text_color="gray")
        
        # 清空译文
        self.widgets['translation_text'].delete("1.0", "end")
        self.widgets['text_stats'].configure(text="字符数: 0")
        
        self.widgets['font_size'].delete(0, "end")
        self.widgets['font_color'].delete(0, "end")
        
        self.widgets['font_size_slider'].set(12)
    
    def register_callback(self, event_name: str, callback: Callable):
        """注册回调函数"""
        self.callbacks[event_name] = callback
    
    def _execute_callback(self, event_name: str, *args):
        """执行回调"""
        callback = self.callbacks.get(event_name)
        if callback:
            try:
                return callback(*args)
            except Exception as e:
                print(f"属性面板回调执行失败 {event_name}: {e}")
    
    def _on_text_change(self, event=None):
        """译文变化处理"""
        self._update_text_stats()
        self._execute_callback('text_changed')
    
    def _on_original_text_change(self, event=None):
        """原文变化处理"""
        self._update_original_text_stats()
        self._execute_callback('original_text_changed')
    
    def _on_original_text_click(self, event=None):
        """原文框点击处理"""
        try:
            self.widgets['original_text'].focus_set()
            self.widgets['original_text']._textbox.focus_set()
            self.widgets['original_edit_status'].configure(text="正在编辑原文...", text_color="#1f538d")
            print("原文文本框获得焦点")
        except Exception as e:
            print(f"设置原文文本框焦点失败: {e}")
    
    def _on_original_text_focus_in(self, event=None):
        """原文框获得焦点处理"""
        self.widgets['original_edit_status'].configure(text="可以编辑原文", text_color="#1f538d")
        print("原文文本框已获得焦点，可以编辑")
    
    def _on_original_text_double_click(self, event=None):
        """原文框双击处理"""
        try:
            # 双击全选文本
            self.widgets['original_text']._textbox.select_range("1.0", "end")
            print("原文已全选")
        except Exception as e:
            print(f"原文全选失败: {e}")
    
    def _on_text_click(self, event=None):
        """文本框点击处理"""
        # 确保文本框获得焦点
        try:
            self.widgets['translation_text'].focus_set()
            self.widgets['translation_text']._textbox.focus_set()
            print("译文文本框获得焦点")
        except Exception as e:
            print(f"设置文本框焦点失败: {e}")
    
    def _on_text_focus_in(self, event=None):
        """文本框获得焦点处理"""
        print("译文文本框已获得焦点，可以编辑")
        # 确保文本框处于正常编辑状态
        try:
            self.widgets['translation_text'].configure(state="normal")
        except Exception as e:
            print(f"设置文本框编辑状态失败: {e}")
    
    def _on_text_double_click(self, event=None):
        """文本框双击处理"""
        try:
            # 双击全选文本
            self.widgets['translation_text']._textbox.select_range("1.0", "end")
            print("文本已全选")
        except Exception as e:
            print(f"文本全选失败: {e}")
    
    def _on_style_change(self, event=None):
        """样式变化处理"""
        self._execute_callback('style_changed')
    
    def _on_transform_change(self, event=None):
        """变换变化处理"""
        # 同步滑块
        try:
            angle = float(self.widgets['angle'].get())
            self.widgets['angle_slider'].set(angle)
        except ValueError:
            pass
        
        self._execute_callback('transform_changed')
    
    def _on_font_size_slider(self, value):
        """字体大小滑块变化"""
        self.widgets['font_size'].delete(0, "end")
        self.widgets['font_size'].insert(0, str(int(value)))
        self._execute_callback('style_changed')
    
    def _on_angle_slider(self, value):
        """角度滑块变化"""
        self.widgets['angle'].delete(0, "end")
        self.widgets['angle'].insert(0, f"{value:.1f}")
        self._execute_callback('transform_changed')
    
    def _choose_color(self):
        """选择颜色"""
        current_color = self.widgets['font_color'].get() or '#FFFFFF'
        color = colorchooser.askcolor(color=current_color, title="选择字体颜色")
        
        if color[1]:  # 用户选择了颜色
            self.widgets['font_color'].delete(0, "end")
            self.widgets['font_color'].insert(0, color[1])
            self._execute_callback('style_changed')
    
    def _update_text_stats(self):
        """更新译文统计"""
        text = self.widgets['translation_text'].get("1.0", "end-1c")
        char_count = len(text)
        line_count = text.count('\n') + (1 if text else 0)
        
        stats_text = f"译文 - 字符数: {char_count}, 行数: {line_count}"
        self.widgets['text_stats'].configure(text=stats_text)
    
    def _update_original_text_stats(self):
        """更新原文统计"""
        text = self.widgets['original_text'].get("1.0", "end-1c")
        char_count = len(text)
        line_count = text.count('\n') + (1 if text else 0)
        
        status_text = f"原文: {char_count}个字符, {line_count}行"
        self.widgets['original_edit_status'].configure(text=status_text, text_color="#1f538d")
    
    def _calculate_bbox(self, region_data):
        """计算区域边界框"""
        lines = region_data.get('lines', [])
        if not lines:
            return None
        
        all_points = [point for line in lines for point in line]
        if not all_points:
            return None
        
        x_coords = [p[0] for p in all_points]
        y_coords = [p[1] for p in all_points]
        
        return (min(x_coords), min(y_coords), max(x_coords), max(y_coords))
    
    def _on_ocr_model_change(self, value):
        """OCR模型变化处理"""
        self._execute_callback('ocr_model_changed', value)
    
    def _on_translator_change(self, value):
        """翻译器变化处理"""
        self._execute_callback('translator_changed', value)
    
    def _on_target_language_change(self, value):
        """目标语言变化处理"""
        self._execute_callback('target_language_changed', value)
    
    def _on_ocr_recognize(self):
        """OCR识别按钮处理"""
        self._execute_callback('ocr_recognize')
    
    def _on_translate(self):
        """翻译按钮处理"""
        self._execute_callback('translate_text')