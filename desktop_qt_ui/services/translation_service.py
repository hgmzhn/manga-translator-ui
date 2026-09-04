"""
翻译服务
支持多种翻译器的选择和配置管理，根据配置文件参数调用相应的翻译器
"""
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from PIL import Image

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), '..'))

try:
    from manga_translator.config import Translator, TranslatorChain, TranslatorConfig
    from manga_translator.translators import dispatch as dispatch_translator
    from manga_translator.translators.common import KEEP_LANGUAGES
    from manga_translator.utils import Context, TextBlock
    TRANSLATOR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"翻译器后端模块导入失败: {e}")
    TRANSLATOR_AVAILABLE = False
    # 定义fallback类型
    class Translator:
        openai_hq = "openai_hq"
    
    class TranslatorConfig:
        pass
    
    class Context:
        pass

@dataclass
class TranslationResult:
    original_text: str
    translated_text: str
    translator_used: str

class TranslationService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        from . import (  # Lazy import to avoid circular dependency
            get_config_service,
            get_i18n_manager,
        )
        self.config_service = get_config_service()
        self.i18n = get_i18n_manager()
        
        # 从配置服务正确初始化当前状态
        initial_config = self.config_service.get_config()
        initial_translator_name = initial_config.translator.translator
        if TRANSLATOR_AVAILABLE and hasattr(Translator, initial_translator_name):
            self.current_translator_enum = Translator[initial_translator_name]
        else:
            self.current_translator_enum = Translator.openai_hq # Fallback
        
        self.current_target_lang = initial_config.translator.target_lang or 'CHS'
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def get_available_translators(self) -> List[str]:
        if not TRANSLATOR_AVAILABLE:
            return []
        return [t.value for t in Translator]

    def get_target_languages(self) -> Dict[str, str]:
        """获取支持的目标语言列表（支持国际化）"""
        return {
            'CHS': self._t('lang_CHS'),
            'CHT': self._t('lang_CHT'),
            'CSY': self._t('lang_CSY'),
            'NLD': self._t('lang_NLD'),
            'ENG': self._t('lang_ENG'),
            'FRA': self._t('lang_FRA'),
            'DEU': self._t('lang_DEU'),
            'HUN': self._t('lang_HUN'),
            'ITA': self._t('lang_ITA'),
            'JPN': self._t('lang_JPN'),
            'KOR': self._t('lang_KOR'),
            'POL': self._t('lang_POL'),
            'PTB': self._t('lang_PTB'),
            'ROM': self._t('lang_ROM'),
            'RUS': self._t('lang_RUS'),
            'ESP': self._t('lang_ESP'),
            'TRK': self._t('lang_TRK'),
            'UKR': self._t('lang_UKR'),
            'VIN': self._t('lang_VIN'),
            'ARA': self._t('lang_ARA'),
            'SRP': self._t('lang_SRP'),
            'HRV': self._t('lang_HRV'),
            'THA': self._t('lang_THA'),
            'IND': self._t('lang_IND'),
            'FIL': self._t('lang_FIL')
        }

    def get_keep_languages(self) -> Dict[str, str]:
        """获取合并后保留语言过滤可选项（支持国际化）"""
        if not TRANSLATOR_AVAILABLE:
            return {}
        return {
            code: self._t(f'lang_{code}')
            for code in KEEP_LANGUAGES.keys()
        }

    async def translate_text(self, text: str, 
                           translator: Optional[Translator] = None,
                           target_lang: Optional[str] = None,
                           config: Optional[TranslatorConfig] = None) -> Optional[TranslationResult]:
        if not TRANSLATOR_AVAILABLE or not text or not text.strip():
            return None

        translator_to_use = translator or self.current_translator_enum
        target_lang_to_use = target_lang or self.current_target_lang

        try:
            chain_string = f"{translator_to_use.value}:{target_lang_to_use}"
            chain = TranslatorChain(chain_string)
            ctx = Context()
            ctx.text = text
            queries = [text]

            translated_texts = await dispatch_translator(
                chain,
                queries,
                config=config if config is not None else self.config_service.get_config(),
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

    async def translate_text_batch(self, texts: List[str],
                                 translator: Optional[Translator] = None,
                                 target_lang: Optional[str] = None,
                                 config: Optional[TranslatorConfig] = None, # This is now effectively unused but kept for API compatibility
                                 image: Optional[Image.Image] = None,
                                 regions: Optional[List[Dict[str, Any]]] = None) -> List[Optional[TranslationResult]]:
        if not TRANSLATOR_AVAILABLE or not texts:
            return [None] * len(texts)

        translator_to_use = translator or self.current_translator_enum
        target_lang_to_use = target_lang or self.current_target_lang

        final_config = self.config_service.get_config()

        try:
            chain_string = f"{translator_to_use.value}:{target_lang_to_use}"
            chain = TranslatorChain(chain_string)

            # The `args` parameter for dispatch_translator is a flexible context object.
            # We build it manually here.
            translator_args = Context()

            if image is not None:
                translator_args.image = image
            if regions is not None:
                try:
                    # 转换 direction 字段
                    converted_regions = []
                    for r in regions:
                        region_copy = r.copy()
                        if 'direction' in region_copy:
                            dir_val = region_copy['direction']
                            if dir_val == 'horizontal':
                                region_copy['direction'] = 'h'
                            elif dir_val == 'vertical':
                                region_copy['direction'] = 'v'
                        converted_regions.append(region_copy)
                    
                    # FIX: Instantiate TextBlock using dictionary unpacking, not a non-existent class method.
                    translator_args.text_regions = [TextBlock(**r) for r in converted_regions]
                except (TypeError, KeyError, ValueError) as e:
                    # ValueError：translation_rich 严格校验（richtext.v1 未知键等）
                    # 失败时走 raw-dict 回退，而不是穿到外层 except 让整批翻译
                    # 静默返回 [None]*len(texts)（审查 F04c）
                    self.logger.warning(f"Could not convert all regions to TextBlock: {e}")
                    translator_args.text_regions = regions # Fallback to passing raw dicts

            # ADDED: Logic to load High-Quality prompt, mimicking manga_translator.py
            translator_args.custom_prompt_json = None
            if final_config.translator.high_quality_prompt_path:
                try:
                    prompt_path = final_config.translator.high_quality_prompt_path
                    self.logger.info(f"--- DIAGNOSTIC_PROMPT_PATH: Attempting to load HQ prompt from path: {prompt_path}") # 诊断日志
                    if not os.path.isabs(prompt_path):
                        # Assuming root_dir is accessible or using a known base path
                        prompt_path = os.path.join(self.config_service.root_dir, prompt_path)
                    
                    if os.path.exists(prompt_path):
                        with open(prompt_path, 'r', encoding='utf-8') as f:
                            import json
                            translator_args.custom_prompt_json = json.load(f)
                        self.logger.info(f"Successfully loaded custom HQ prompt from: {prompt_path}")
                    else:
                        self.logger.warning(f"Custom HQ prompt file not found at: {prompt_path}")
                except Exception as e:
                    self.logger.error(f"Error loading custom HQ prompt: {e}")

            translated_texts = await dispatch_translator(
                chain,
                texts,
                config=final_config, # Pass the full config object
                use_mtpe=False, # use_mtpe removed but kept for API compatibility
                args=translator_args, # Pass the constructed context object
                device='cuda' if final_config.cli.use_gpu else 'cpu'
            )

            if translated_texts and len(translated_texts) == len(texts):
                return [
                    TranslationResult(
                        original_text=original,
                        translated_text=translated,
                        translator_used=translator_to_use.value
                    ) for original, translated in zip(texts, translated_texts)
                ]
            
            self.logger.warning(f"Batch translation returned {len(translated_texts) if translated_texts else 0} results for {len(texts)} inputs.")
            return [None] * len(texts)

        except Exception as e:
            self.logger.error(f"批量翻译失败: {e}", exc_info=True)
            return [None] * len(texts)

    def set_translator(self, translator_name: str):
        if TRANSLATOR_AVAILABLE and hasattr(Translator, translator_name):
            self.current_translator_enum = Translator[translator_name]

    def set_target_language(self, lang_code: str):
        self.current_target_lang = lang_code

    def cleanup(self):
        """释放翻译服务持有的运行期状态。"""
