"""
OCR翻译管理器
负责OCR识别、翻译服务和快捷键处理
"""
import os
import copy
import time
from typing import Dict, Any, List, Optional, Callable


class OcrTranslationManager:
    """OCR翻译管理器"""
    
    def __init__(self):
        # 服务初始化
        self.ocr_service = None
        self.translation_service = None
        self.config_service = None
        self.operation_manager = None
        
        # 回调函数
        self.callbacks: Dict[str, Callable] = {}
        
        # 初始化服务
        self._initialize_services()
    
    def register_callback(self, event_name: str, callback: Callable):
        """注册回调函数"""
        self.callbacks[event_name] = callback
    
    def set_operation_manager(self, operation_manager):
        """设置操作管理器"""
        self.operation_manager = operation_manager
    
    def _initialize_services(self):
        """初始化OCR和翻译服务"""
        try:
            from services import get_ocr_service, get_translation_service, get_config_service
            
            self.ocr_service = get_ocr_service()
            self.translation_service = get_translation_service()
            self.config_service = get_config_service()
            
            # Config is likely loaded by the main app logic, so we may not need to load it here.
            # config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'examples', 'config-example.json')
            # if os.path.exists(config_path):
            #     self.config_service.load_config(config_path)
            #     self.translation_service.load_config_from_file(config_path)
            
            print("OCR和翻译服务初始化成功")
            
        except Exception as e:
            print(f"OCR和翻译服务初始化失败: {e}")
            self.ocr_service = None
            self.translation_service = None
            self.config_service = None
    
    def is_available(self) -> bool:
        """检查服务是否可用"""
        return self.ocr_service is not None and self.translation_service is not None
    
    def get_ocr_config(self) -> Dict[str, Any]:
        """获取OCR配置"""
        if self.config_service:
            return self.config_service.get_config().get('ocr', {})
        return {'ocr': '48px'}
    
    def get_translator_config(self) -> Dict[str, Any]:
        """获取翻译器配置"""
        if self.config_service:
            return self.config_service.get_config().get('translator', {})
        return {'translator': 'sugoi', 'target_lang': 'CHS'}
    
    def ocr_recognize(self, region_index: int, image_data=None) -> bool:
        """OCR识别"""
        if not self.is_available() or not self.operation_manager:
            print("OCR服务不可用或操作管理器未设置")
            return False
        
        try:
            # 定义OCR操作函数
            def ocr_operation():
                # TODO: 这里将来集成真正的OCR服务
                time.sleep(2)  # 模拟OCR处理时间
                return "识别到的文本内容"
            
            # 使用操作管理器执行OCR
            self.operation_manager.execute_ocr_operation(
                operation_func=ocr_operation,
                success_callback=lambda result: self._on_ocr_result(region_index, result),
                error_callback=lambda error: self._on_ocr_error(region_index, error)
            )
            
            return True
            
        except Exception as e:
            print(f"OCR识别失败: {e}")
            return False
    
    def translate_text(self, region_index: int, text_to_translate: str) -> bool:
        """翻译文本"""
        if not self.is_available() or not self.operation_manager:
            print("翻译服务不可用或操作管理器未设置")
            return False
        
        if not text_to_translate.strip():
            print("没有文本可翻译")
            return False
        
        try:
            # 定义翻译操作函数
            def translation_operation():
                # TODO: 这里将来集成真正的翻译服务
                time.sleep(1.5)  # 模拟翻译处理时间
                return f"翻译结果: {text_to_translate}"
            
            # 使用操作管理器执行翻译
            self.operation_manager.execute_translation_operation(
                operation_func=translation_operation,
                success_callback=lambda result: self._on_translate_result(region_index, result),
                error_callback=lambda error: self._on_translate_error(region_index, error)
            )
            
            return True
            
        except Exception as e:
            print(f"翻译失败: {e}")
            return False
    
    def ocr_and_translate(self, region_index: int, image_data=None) -> bool:
        """OCR识别并翻译"""
        if not self.is_available() or not self.operation_manager:
            print("服务不可用或操作管理器未设置")
            return False
        
        try:
            # 定义OCR操作函数
            def ocr_operation():
                # TODO: 这里将来集成真正的OCR服务
                time.sleep(2)  # 模拟OCR处理时间
                return "识别到的文本内容"
            
            # 定义翻译操作函数
            def translation_operation(recognized_text):
                # TODO: 这里将来集成真正的翻译服务
                time.sleep(1.5)  # 模拟翻译处理时间
                return f"翻译结果: {recognized_text}"
            
            # 使用操作管理器执行组合操作
            self.operation_manager.execute_combined_operation(
                ocr_func=ocr_operation,
                translation_func=translation_operation,
                success_callback=lambda ocr_result, trans_result: self._on_ocr_and_translate_result(region_index, ocr_result, trans_result),
                error_callback=lambda error: self._on_ocr_and_translate_error(region_index, error)
            )
            
            return True
            
        except Exception as e:
            print(f"OCR识别和翻译失败: {e}")
            return False
    
    def _on_ocr_result(self, region_index: int, recognized_text: str):
        """处理OCR识别结果"""
        try:
            print(f"OCR识别完成: {recognized_text}")
            self._execute_callback('ocr_result', region_index, recognized_text)
            
        except Exception as e:
            print(f"处理OCR结果失败: {e}")
    
    def _on_ocr_error(self, region_index: int, error_message: str):
        """处理OCR识别错误"""
        print(f"OCR识别失败: {error_message}")
        self._execute_callback('ocr_error', region_index, error_message)
    
    def _on_translate_result(self, region_index: int, translated_text: str):
        """处理翻译结果"""
        try:
            print(f"翻译完成: {translated_text}")
            self._execute_callback('translate_result', region_index, translated_text)
            
        except Exception as e:
            print(f"处理翻译结果失败: {e}")
    
    def _on_translate_error(self, region_index: int, error_message: str):
        """处理翻译错误"""
        print(f"翻译失败: {error_message}")
        self._execute_callback('translate_error', region_index, error_message)
    
    def _on_ocr_and_translate_result(self, region_index: int, recognized_text: str, translated_text: str):
        """处理OCR识别和翻译结果"""
        try:
            print(f"OCR识别和翻译完成: {recognized_text} -> {translated_text}")
            self._execute_callback('ocr_and_translate_result', region_index, recognized_text, translated_text)
            
        except Exception as e:
            print(f"处理OCR识别和翻译结果失败: {e}")
    
    def _on_ocr_and_translate_error(self, region_index: int, error_message: str):
        """处理OCR识别和翻译错误"""
        print(f"OCR识别和翻译失败: {error_message}")
        self._execute_callback('ocr_and_translate_error', region_index, error_message)
    
    def setup_context_menu_callbacks(self, context_menu):
        """设置右键菜单回调函数"""
        context_menu.register_callback('ocr_recognize', self._on_context_ocr_recognize)
        context_menu.register_callback('translate_text', self._on_context_translate)
        context_menu.register_callback('ocr_and_translate', self._on_context_ocr_and_translate)
    
    def _on_context_ocr_recognize(self):
        """右键菜单OCR识别处理"""
        selected_index = self._execute_callback('get_selected_region_index')
        if selected_index is not None:
            self.ocr_recognize(selected_index)
        else:
            print("未选中区域")
    
    def _on_context_translate(self):
        """右键菜单翻译处理"""
        selected_index = self._execute_callback('get_selected_region_index')
        if selected_index is not None:
            text_to_translate = self._execute_callback('get_region_text', selected_index)
            if text_to_translate:
                self.translate_text(selected_index, text_to_translate)
            else:
                print("区域中没有文本可翻译")
        else:
            print("未选中区域")
    
    def _on_context_ocr_and_translate(self):
        """右键菜单OCR识别并翻译处理"""
        selected_index = self._execute_callback('get_selected_region_index')
        if selected_index is not None:
            self.ocr_and_translate(selected_index)
        else:
            print("未选中区域")
    
    def setup_shortcuts(self, widget):
        """设置OCR和翻译快捷键"""
        try:
            # F7: OCR识别
            widget.bind("<F7>", lambda e: self._shortcut_ocr_recognize())
            
            # F8: 翻译
            widget.bind("<F8>", lambda e: self._shortcut_translate())
            
            # F9: OCR识别并翻译
            widget.bind("<F9>", lambda e: self._shortcut_ocr_and_translate())
            
            print("OCR和翻译快捷键设置完成: F7=OCR识别, F8=翻译, F9=OCR识别并翻译")
            
        except Exception as e:
            print(f"设置OCR和翻译快捷键失败: {e}")
    
    def _shortcut_ocr_recognize(self):
        """快捷键OCR识别"""
        selected_index = self._execute_callback('get_selected_region_index')
        if selected_index is not None:
            self.ocr_recognize(selected_index)
        else:
            print("请先选中一个文本区域")
        return "break"
    
    def _shortcut_translate(self):
        """快捷键翻译"""
        selected_index = self._execute_callback('get_selected_region_index')
        if selected_index is not None:
            text_to_translate = self._execute_callback('get_region_text', selected_index)
            if text_to_translate:
                self.translate_text(selected_index, text_to_translate)
            else:
                print("区域中没有文本可翻译")
        else:
            print("请先选中一个文本区域")
        return "break"
    
    def _shortcut_ocr_and_translate(self):
        """快捷键OCR识别并翻译"""
        selected_index = self._execute_callback('get_selected_region_index')
        if selected_index is not None:
            self.ocr_and_translate(selected_index)
        else:
            print("请先选中一个文本区域")
        return "break"
    
    def _execute_callback(self, event_name: str, *args):
        """执行回调函数"""
        callback = self.callbacks.get(event_name)
        if callback:
            try:
                return callback(*args)
            except Exception as e:
                print(f"OCR翻译管理器回调执行失败 {event_name}: {e}")
        return None