"""
国际化支持模块
提供多语言翻译和本地化功能
"""
import json
import locale
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class LocaleInfo:
    """语言区域信息"""
    code: str  # 语言代码，如 'zh_CN'
    name: str  # 语言名称，如 '简体中文'
    english_name: str  # 英文名称，如 'Simplified Chinese'
    direction: str = "ltr"  # 文本方向: ltr (左到右) 或 rtl (右到左)

class I18nManager:
    """国际化管理器"""
    
    def __init__(self, locale_dir: str = "locales", fallback_locale: str = "zh_CN", config_language: str = "auto"):
        if not os.path.isabs(locale_dir):
            # 相对路径以当前服务目录为基准。
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.locale_dir = os.path.join(current_dir, '..', locale_dir)
        else:
            self.locale_dir = locale_dir
        
        self.fallback_locale = fallback_locale
        self.translations: Dict[str, Dict[str, str]] = {}
        self.available_locales: Dict[str, LocaleInfo] = {}
        self.logger = logging.getLogger(__name__)
        
        os.makedirs(self.locale_dir, exist_ok=True)
        
        # 初始化支持的语言（自动扫描 locale_dir 下的 *.json）
        self._init_supported_locales()
        
        # 根据配置决定语言
        if config_language == "auto":
            # 自动检测系统语言
            system_locale = self._detect_system_locale()
            resolved = self._resolve_locale(system_locale)
            self.current_locale = resolved if resolved else fallback_locale
        else:
            # 使用配置的语言（支持精确或语言前缀匹配）
            resolved = self._resolve_locale(config_language)
            self.current_locale = resolved if resolved else fallback_locale
        
        # 加载翻译
        self._load_all_translations()
        
    
    def _build_locale_info(self, locale_code: str) -> LocaleInfo:
        """使用 QLocale 自动构建语言信息"""
        native_name = locale_code
        english_name = locale_code
        direction = "ltr"
        try:
            from PyQt6.QtCore import QLocale, Qt
            qlocale = QLocale(locale_code)
            if qlocale.language() != QLocale.Language.AnyLanguage:
                ql_native = qlocale.nativeLanguageName()
                if ql_native:
                    native_name = ql_native[:1].upper() + ql_native[1:]
                ql_english = QLocale.languageToString(qlocale.language())
                if ql_english:
                    english_name = ql_english
                if qlocale.textDirection() == Qt.LayoutDirection.RightToLeft:
                    direction = "rtl"
        except Exception:
            pass

        return LocaleInfo(locale_code, native_name, english_name, direction=direction)

    def _init_supported_locales(self):
        """动态扫描语言目录并初始化支持的语言列表"""
        discovered_codes = []
        try:
            for entry in sorted(os.listdir(self.locale_dir)):
                if entry.endswith(".json") and not entry.startswith("."):
                    code = entry[:-5]
                    if code:
                        discovered_codes.append(code)
        except OSError as e:
            self.logger.warning(f"Failed to scan locale directory {self.locale_dir}: {e}")

        if not discovered_codes and self.fallback_locale:
            discovered_codes.append(self.fallback_locale)

        self.available_locales = {
            code: self._build_locale_info(code) for code in discovered_codes
        }

    def _resolve_locale(self, locale_code: Optional[str]) -> Optional[str]:
        """将任意语言代码解析为 available_locales 中存在的标准代码"""
        if not locale_code:
            return None
        normalized = str(locale_code).strip().split(".", 1)[0].replace("-", "_")
        if not normalized or normalized.upper() in ("C", "POSIX"):
            return None

        if normalized in self.available_locales:
            return normalized

        lower_map = {code.lower(): code for code in self.available_locales}
        if normalized.lower() in lower_map:
            return lower_map[normalized.lower()]

        lang = normalized.split("_", 1)[0].lower()
        if (
            self.fallback_locale
            and self.fallback_locale in self.available_locales
            and self.fallback_locale.split("_", 1)[0].lower() == lang
        ):
            return self.fallback_locale

        for code in self.available_locales:
            if code.split("_", 1)[0].lower() == lang:
                return code

        return None

    def _detect_system_locale(self) -> str:
        """检测系统语言"""
        candidates = []
        try:
            from PyQt6.QtCore import QLocale
            qt_locale = QLocale.system().name()
            if qt_locale:
                candidates.append(qt_locale)
        except Exception:
            pass

        try:
            sys_locale = locale.getlocale()[0]
            if sys_locale:
                candidates.append(sys_locale)
        except Exception:
            pass

        if not candidates:
            try:
                default_loc = locale.getdefaultlocale()[0]
                if default_loc:
                    candidates.append(default_loc)
            except Exception as e:
                self.logger.warning(f"Failed to detect system language: {e}")

        for candidate in candidates:
            resolved = self._resolve_locale(candidate)
            if resolved:
                return resolved

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
                self.logger.debug(f"Loading translation file: {translation_file}")
            else:
                self.translations[locale_code] = {}
                    
        except Exception as e:
            self.logger.error(f"Failed to load translation file for {locale_code}: {e}")
            self.translations[locale_code] = {}
    
    def set_locale(self, locale_code: str) -> bool:
        """设置当前语言"""
        if locale_code == "auto":
            resolved = self._resolve_locale(self._detect_system_locale()) or self.fallback_locale
        else:
            resolved = self._resolve_locale(locale_code)
        if not resolved:
            self.logger.warning(f"Unsupported language: {locale_code}")
            return False
        
        old_locale = self.current_locale
        self.current_locale = resolved
        
        # 每次切换语言都重载翻译，确保运行中更新的词条能立即生效
        self._load_locale_translation(resolved)
        
        self.logger.info(f"Switching language: {old_locale} -> {resolved}")
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
        
        # 检查键是否存在于当前语言的翻译中
        locale_translations = self.translations.get(locale_code, {})
        
        if key in locale_translations:
            # 键存在，使用当前语言的翻译
            translation = locale_translations[key]
        elif locale_code != self.fallback_locale:
            # 键不存在且不是回退语言，尝试从回退语言获取
            fallback_translations = self.translations.get(self.fallback_locale, {})
            translation = fallback_translations.get(key, key)
        else:
            # 键不存在且已经是回退语言，返回键本身
            translation = key
        
        # 格式化翻译（支持参数替换）
        if kwargs:
            try:
                translation = translation.format(**kwargs)
            except Exception as e:
                self.logger.warning(f"Failed to format translation {key}: {e}")
        
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
            self.logger.error(f"Failed to save translations: {e}")
            return False
    
    def _save_locale_translation(self, locale_code: str) -> bool:
        """保存特定语言的翻译"""
        try:
            translation_file = os.path.join(self.locale_dir, f"{locale_code}.json")
            translations = self.translations.get(locale_code, {})
            
            with open(translation_file, 'w', encoding='utf-8') as f:
                json.dump(translations, f, ensure_ascii=False, indent=2)
            
            self.logger.debug(f"Saving translation file: {translation_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save translation file for {locale_code}: {e}")
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
                
                self.logger.info(f"Exporting {len(missing_keys)} missing translations to: {output_file}")
                return True
            else:
                self.logger.info(f"No missing translations for language {locale_code}")
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to export missing translations: {e}")
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

def setup_i18n(locale_dir: str = "locales", fallback_locale: str = "zh_CN", config_language: str = "auto") -> I18nManager:
    """设置国际化"""
    global _i18n_manager
    _i18n_manager = I18nManager(locale_dir, fallback_locale, config_language)
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
