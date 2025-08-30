"""
国际化支持模块
提供多语言翻译和本地化功能
"""
import os
import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import locale
import gettext

@dataclass
class LocaleInfo:
    """语言区域信息"""
    code: str  # 语言代码，如 'zh_CN'
    name: str  # 语言名称，如 '简体中文'
    english_name: str  # 英文名称，如 'Simplified Chinese'
    direction: str = "ltr"  # 文本方向: ltr (左到右) 或 rtl (右到左)

class I18nManager:
    """国际化管理器"""
    
    def __init__(self, locale_dir: str = "locales", fallback_locale: str = "en_US"):
        self.locale_dir = locale_dir
        self.fallback_locale = fallback_locale
        self.current_locale = fallback_locale
        self.translations: Dict[str, Dict[str, str]] = {}
        self.available_locales: Dict[str, LocaleInfo] = {}
        self.logger = logging.getLogger(__name__)
        
        # 确保locale目录存在
        os.makedirs(locale_dir, exist_ok=True)
        
        # 初始化支持的语言
        self._init_supported_locales()
        
        # 检测系统语言
        system_locale = self._detect_system_locale()
        if system_locale in self.available_locales:
            self.current_locale = system_locale
        
        # 加载翻译
        self._load_all_translations()
        
        self.logger.info(f"初始化国际化管理器，当前语言: {self.current_locale}")
    
    def _init_supported_locales(self):
        """初始化支持的语言列表"""
        self.available_locales = {
            "zh_CN": LocaleInfo("zh_CN", "简体中文", "Simplified Chinese"),
            "zh_TW": LocaleInfo("zh_TW", "繁體中文", "Traditional Chinese"),
            "en_US": LocaleInfo("en_US", "English", "English"),
            "ja_JP": LocaleInfo("ja_JP", "日本語", "Japanese"),
            "ko_KR": LocaleInfo("ko_KR", "한국어", "Korean"),
            "es_ES": LocaleInfo("es_ES", "Español", "Spanish"),
            "fr_FR": LocaleInfo("fr_FR", "Français", "French"),
            "de_DE": LocaleInfo("de_DE", "Deutsch", "German"),
            "it_IT": LocaleInfo("it_IT", "Italiano", "Italian"),
            "pt_BR": LocaleInfo("pt_BR", "Português", "Portuguese"),
            "ru_RU": LocaleInfo("ru_RU", "Русский", "Russian"),
            "ar_SA": LocaleInfo("ar_SA", "العربية", "Arabic", "rtl"),
        }
    
    def _detect_system_locale(self) -> str:
        """检测系统语言"""
        try:
            # 尝试获取系统语言
            system_locale = locale.getdefaultlocale()[0]
            if system_locale:
                # 标准化语言代码
                if '_' not in system_locale and len(system_locale) == 2:
                    # 如果只有语言代码，添加默认国家代码
                    lang_country_map = {
                        'zh': 'zh_CN',
                        'en': 'en_US',
                        'ja': 'ja_JP',
                        'ko': 'ko_KR',
                        'es': 'es_ES',
                        'fr': 'fr_FR',
                        'de': 'de_DE',
                        'it': 'it_IT',
                        'pt': 'pt_BR',
                        'ru': 'ru_RU',
                        'ar': 'ar_SA'
                    }
                    system_locale = lang_country_map.get(system_locale, self.fallback_locale)
                
                return system_locale
                
        except Exception as e:
            self.logger.warning(f"检测系统语言失败: {e}")
        
        return self.fallback_locale
    
    def _load_all_translations(self):
        """加载所有语言的翻译"""
        for locale_code in self.available_locales.keys():
            self._load_locale_translation(locale_code)
    
    def _load_locale_translation(self, locale_code: str):
        """加载特定语言的翻译"""
        try:
            translation_file = os.path.join(self.locale_dir, f"{locale_code}.json")
            
            if os.path.exists(translation_file):
                with open(translation_file, 'r', encoding='utf-8') as f:
                    self.translations[locale_code] = json.load(f)
                self.logger.debug(f"加载翻译文件: {translation_file}")
            else:
                # 如果翻译文件不存在，创建空的翻译字典
                self.translations[locale_code] = {}
                
                # 为主要语言创建基础翻译文件
                if locale_code in ['zh_CN', 'en_US']:
                    self._create_base_translation_file(locale_code)
                    
        except Exception as e:
            self.logger.error(f"加载翻译文件失败 {locale_code}: {e}")
            self.translations[locale_code] = {}
    
    def _create_base_translation_file(self, locale_code: str):
        """创建基础翻译文件"""
        try:
            base_translations = self._get_base_translations(locale_code)
            
            translation_file = os.path.join(self.locale_dir, f"{locale_code}.json")
            with open(translation_file, 'w', encoding='utf-8') as f:
                json.dump(base_translations, f, ensure_ascii=False, indent=2)
            
            self.translations[locale_code] = base_translations
            self.logger.info(f"创建基础翻译文件: {translation_file}")
            
        except Exception as e:
            self.logger.error(f"创建基础翻译文件失败: {e}")
    
    def _get_base_translations(self, locale_code: str) -> Dict[str, str]:
        """获取基础翻译内容"""
        if locale_code == "zh_CN":
            return {
                # 菜单和按钮
                "File": "文件",
                "Edit": "编辑",
                "View": "视图",
                "Tools": "工具",
                "Help": "帮助",
                "Open": "打开",
                "Save": "保存",
                "Exit": "退出",
                "Cancel": "取消",
                "OK": "确定",
                "Yes": "是",
                "No": "否",
                
                # 应用标题和界面
                "Manga Image Translator UI": "漫画图片翻译器 UI",
                "Main View": "主视图",
                "Editor View": "编辑器视图",
                "Settings": "设置",
                "About": "关于",
                
                # 翻译相关
                "Start Translation": "开始翻译",
                "Stop Translation": "停止翻译",
                "Translation Settings": "翻译设置",
                "Translator": "翻译引擎",
                "Target Language": "目标语言",
                "Source Language": "源语言",
                "Translation Progress": "翻译进度",
                "Translation Complete": "翻译完成",
                "Translation Failed": "翻译失败",
                
                # 文件操作
                "Add Files": "添加文件",
                "Add Folder": "添加文件夹",
                "Clear List": "清空列表",
                "Remove Selected": "删除选中",
                "Select All": "全选",
                "File List": "文件列表",
                "Output Folder": "输出文件夹",
                
                # 进度和状态
                "Progress": "进度",
                "Status": "状态",
                "Ready": "就绪",
                "Processing": "处理中",
                "Completed": "已完成",
                "Error": "错误",
                "Warning": "警告",
                "Information": "信息",
                
                # 编辑器
                "Editor": "编辑器",
                "Original Text": "原文",
                "Translated Text": "译文",
                "Font Size": "字体大小",
                "Font Color": "字体颜色",
                "Rotation": "旋转",
                "Position": "位置",
                "Copy": "复制",
                "Paste": "粘贴",
                "Undo": "撤销",
                "Redo": "重做",
                
                # 配置
                "Configuration": "配置",
                "Load Config": "加载配置",
                "Save Config": "保存配置",
                "Reset Config": "重置配置",
                "API Settings": "API设置",
                "Advanced Settings": "高级设置",
                
                # 错误信息
                "Error occurred": "发生错误",
                "File not found": "文件未找到",
                "Invalid file format": "无效的文件格式",
                "Network error": "网络错误",
                "API error": "API错误",
                "Configuration error": "配置错误",
                
                # 成功信息
                "Operation successful": "操作成功",
                "File saved successfully": "文件保存成功",
                "Configuration loaded": "配置已加载",
                "Translation completed successfully": "翻译完成",
            }
        elif locale_code == "en_US":
            return {
                # 菜单和按钮
                "File": "File",
                "Edit": "Edit",
                "View": "View",
                "Tools": "Tools",
                "Help": "Help",
                "Open": "Open",
                "Save": "Save",
                "Exit": "Exit",
                "Cancel": "Cancel",
                "OK": "OK",
                "Yes": "Yes",
                "No": "No",
                
                # 应用标题和界面
                "Manga Image Translator UI": "Manga Image Translator UI",
                "Main View": "Main View",
                "Editor View": "Editor View",
                "Settings": "Settings",
                "About": "About",
                
                # 翻译相关
                "Start Translation": "Start Translation",
                "Stop Translation": "Stop Translation",
                "Translation Settings": "Translation Settings",
                "Translator": "Translator",
                "Target Language": "Target Language",
                "Source Language": "Source Language",
                "Translation Progress": "Translation Progress",
                "Translation Complete": "Translation Complete",
                "Translation Failed": "Translation Failed",
                
                # 其他保持英文原样
                "Add Files": "Add Files",
                "Add Folder": "Add Folder",
                "Clear List": "Clear List",
                "Remove Selected": "Remove Selected",
                "Select All": "Select All",
                "File List": "File List",
                "Output Folder": "Output Folder",
            }
        else:
            # 其他语言返回空字典，使用回退机制
            return {}
    
    def set_locale(self, locale_code: str) -> bool:
        """设置当前语言"""
        if locale_code not in self.available_locales:
            self.logger.warning(f"不支持的语言: {locale_code}")
            return False
        
        old_locale = self.current_locale
        self.current_locale = locale_code
        
        # 如果翻译未加载，尝试加载
        if locale_code not in self.translations:
            self._load_locale_translation(locale_code)
        
        self.logger.info(f"切换语言: {old_locale} -> {locale_code}")
        return True
    
    def get_current_locale(self) -> str:
        """获取当前语言代码"""
        return self.current_locale
    
    def get_locale_info(self, locale_code: str = None) -> Optional[LocaleInfo]:
        """获取语言信息"""
        if locale_code is None:
            locale_code = self.current_locale
        return self.available_locales.get(locale_code)
    
    def get_available_locales(self) -> Dict[str, LocaleInfo]:
        """获取所有可用语言"""
        return self.available_locales.copy()
    
    def translate(self, key: str, locale_code: str = None, **kwargs) -> str:
        """翻译文本"""
        if locale_code is None:
            locale_code = self.current_locale
        
        # 尝试从当前语言获取翻译
        translation = self._get_translation(key, locale_code)
        
        # 如果没找到，尝试从回退语言获取
        if translation == key and locale_code != self.fallback_locale:
            translation = self._get_translation(key, self.fallback_locale)
        
        # 格式化翻译（支持参数替换）
        if kwargs and translation != key:
            try:
                translation = translation.format(**kwargs)
            except Exception as e:
                self.logger.warning(f"翻译格式化失败 {key}: {e}")
        
        return translation
    
    def _get_translation(self, key: str, locale_code: str) -> str:
        """获取翻译"""
        locale_translations = self.translations.get(locale_code, {})
        return locale_translations.get(key, key)
    
    def add_translation(self, key: str, value: str, locale_code: str = None):
        """添加翻译"""
        if locale_code is None:
            locale_code = self.current_locale
        
        if locale_code not in self.translations:
            self.translations[locale_code] = {}
        
        self.translations[locale_code][key] = value
    
    def add_translations(self, translations: Dict[str, str], locale_code: str = None):
        """批量添加翻译"""
        if locale_code is None:
            locale_code = self.current_locale
        
        if locale_code not in self.translations:
            self.translations[locale_code] = {}
        
        self.translations[locale_code].update(translations)
    
    def save_translations(self, locale_code: str = None) -> bool:
        """保存翻译到文件"""
        try:
            if locale_code is None:
                # 保存所有语言
                for code in self.translations.keys():
                    self._save_locale_translation(code)
                return True
            else:
                return self._save_locale_translation(locale_code)
                
        except Exception as e:
            self.logger.error(f"保存翻译失败: {e}")
            return False
    
    def _save_locale_translation(self, locale_code: str) -> bool:
        """保存特定语言的翻译"""
        try:
            translation_file = os.path.join(self.locale_dir, f"{locale_code}.json")
            translations = self.translations.get(locale_code, {})
            
            with open(translation_file, 'w', encoding='utf-8') as f:
                json.dump(translations, f, ensure_ascii=False, indent=2)
            
            self.logger.debug(f"保存翻译文件: {translation_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存翻译文件失败 {locale_code}: {e}")
            return False
    
    def export_missing_keys(self, locale_code: str, output_file: str) -> bool:
        """导出缺失的翻译键"""
        try:
            # 获取默认语言的所有键
            default_keys = set(self.translations.get(self.fallback_locale, {}).keys())
            
            # 获取目标语言的键
            target_keys = set(self.translations.get(locale_code, {}).keys())
            
            # 找出缺失的键
            missing_keys = default_keys - target_keys
            
            if missing_keys:
                missing_translations = {}
                for key in missing_keys:
                    missing_translations[key] = ""  # 空值等待翻译
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(missing_translations, f, ensure_ascii=False, indent=2)
                
                self.logger.info(f"导出 {len(missing_keys)} 个缺失翻译到: {output_file}")
                return True
            else:
                self.logger.info(f"语言 {locale_code} 没有缺失的翻译")
                return True
                
        except Exception as e:
            self.logger.error(f"导出缺失翻译失败: {e}")
            return False
    
    def get_text_direction(self, locale_code: str = None) -> str:
        """获取文本方向"""
        if locale_code is None:
            locale_code = self.current_locale
        
        locale_info = self.get_locale_info(locale_code)
        return locale_info.direction if locale_info else "ltr"
    
    def is_rtl_language(self, locale_code: str = None) -> bool:
        """是否为从右到左的语言"""
        return self.get_text_direction(locale_code) == "rtl"

# 全局国际化管理器
_i18n_manager = None

def get_i18n_manager() -> I18nManager:
    """获取全局国际化管理器"""
    global _i18n_manager
    if _i18n_manager is None:
        _i18n_manager = I18nManager()
    return _i18n_manager

def setup_i18n(locale_dir: str = "locales", fallback_locale: str = "en_US") -> I18nManager:
    """设置国际化"""
    global _i18n_manager
    _i18n_manager = I18nManager(locale_dir, fallback_locale)
    return _i18n_manager

# 便捷函数
def _(key: str, **kwargs) -> str:
    """翻译函数的简短别名"""
    return get_i18n_manager().translate(key, **kwargs)

def set_language(locale_code: str) -> bool:
    """设置语言的便捷函数"""
    return get_i18n_manager().set_locale(locale_code)

def get_current_language() -> str:
    """获取当前语言的便捷函数"""
    return get_i18n_manager().get_current_locale()

def get_available_languages() -> Dict[str, LocaleInfo]:
    """获取可用语言的便捷函数"""
    return get_i18n_manager().get_available_locales()