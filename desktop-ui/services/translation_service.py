"""
翻译服务
支持多种翻译器的选择和配置管理，根据配置文件参数调用相应的翻译器
"""
import asyncio
import logging
import sys
import os
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), '..'))

try:
    from manga_translator.translators import dispatch as dispatch_translator
    from manga_translator.config import Translator, TranslatorConfig, TranslatorChain
    from manga_translator.utils import Context
    TRANSLATOR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"翻译器后端模块导入失败: {e}")
    TRANSLATOR_AVAILABLE = False

@dataclass
class TranslationResult:
    original_text: str
    translated_text: str
    translator_used: str

class TranslationService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.current_translator_enum = Translator.sugoi
        self.current_target_lang = 'CHS'

    def get_available_translators(self) -> List[str]:
        if not TRANSLATOR_AVAILABLE:
            return []
        return [t.value for t in Translator]

    def get_target_languages(self) -> Dict[str, str]:
        """获取支持的目标语言列表（中文）"""
        return {
            'CHS': '简体中文',
            'CHT': '繁体中文',
            'JPN': '日语',
            'ENG': '英语',
            'KOR': '韩语',
            'FRA': '法语',
            'DEU': '德语',
            'SPA': '西班牙语',
            'RUS': '俄语',
            'ITA': '意大利语',
            'POR': '葡萄牙语'
        }

    async def translate_text_batch(self, texts: List[str], 
                                  translator: Optional[Translator] = None,
                                  target_lang: Optional[str] = None,
                                  config: Optional[TranslatorConfig] = None) -> List[Optional[TranslationResult]]:
        """批量翻译文本，保持上下文信息"""
        if not TRANSLATOR_AVAILABLE or not texts:
            return [None] * len(texts)

        translator_to_use = translator or self.current_translator_enum
        target_lang_to_use = target_lang or self.current_target_lang

        try:
            # TranslatorChain需要字符串形式的链定义
            chain_string = f"{translator_to_use.name}:{target_lang_to_use}"
            chain = TranslatorChain(chain_string)
            
            # 过滤空文本但保持索引对应关系
            queries = []
            valid_indices = []
            for i, text in enumerate(texts):
                if text and text.strip():
                    queries.append(text)
                    valid_indices.append(i)
            
            if not queries:
                return [None] * len(texts)

            ctx = Context()
            translated_texts = await dispatch_translator(
                chain,
                queries,
                translator_config=config,
                args=ctx
            )

            # 构建结果列表
            results = [None] * len(texts)
            for i, translated_text in enumerate(translated_texts or []):
                if i < len(valid_indices):
                    original_index = valid_indices[i]
                    results[original_index] = TranslationResult(
                        original_text=texts[original_index],
                        translated_text=translated_text,
                        translator_used=translator_to_use.value
                    )

            return results
            
        except Exception as e:
            self.logger.error(f"批量翻译失败: {e}")
            return [None] * len(texts)

    async def translate_text(self, text: str, 
                           translator: Optional[Translator] = None,
                           target_lang: Optional[str] = None,
                           config: Optional[TranslatorConfig] = None) -> Optional[TranslationResult]:
        if not TRANSLATOR_AVAILABLE or not text or not text.strip():
            return None

        translator_to_use = translator or self.current_translator_enum
        target_lang_to_use = target_lang or self.current_target_lang

        try:
            # TranslatorChain需要字符串形式的链定义
            chain_string = f"{translator_to_use.name}:{target_lang_to_use}"
            chain = TranslatorChain(chain_string)
            ctx = Context()
            ctx.text = text
            queries = [text]

            translated_texts = await dispatch_translator(
                chain,
                queries,
                translator_config=config,
                args=ctx
            )

            if translated_texts:
                return TranslationResult(
                    original_text=text,
                    translated_text=translated_texts[0],
                    translator_used=translator_to_use.value
                )
            return None
        except Exception as e:
            self.logger.error(f"翻译失败: {e}")
            raise

    def set_translator(self, translator_name: str):
        """设置翻译器，支持通过value或name设置"""
        if not TRANSLATOR_AVAILABLE:
            return
        
        try:
            # 先尝试通过name查找（最常见的情况）
            if hasattr(Translator, translator_name):
                self.current_translator_enum = Translator[translator_name]
                self.logger.info(f"通过name设置翻译器: {translator_name}")
                return
            
            # 如果name查找失败，尝试通过value查找
            for translator in Translator:
                if translator.value == translator_name:
                    self.current_translator_enum = translator
                    self.logger.info(f"通过value设置翻译器: {translator_name} -> {translator.name}")
                    return
            
            # 如果都没找到，记录错误但不抛异常
            self.logger.warning(f"未找到翻译器: {translator_name}，保持当前设置")
        except Exception as e:
            self.logger.error(f"设置翻译器时发生错误: {e}")

    def get_current_translator(self) -> str:
        """获取当前翻译器名称"""
        if self.current_translator_enum:
            return self.current_translator_enum.value
        return "sugoi"  # 默认值

    def set_target_language(self, lang_code: str):
        self.current_target_lang = lang_code