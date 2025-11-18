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
    from manga_translator.utils import Context, TextBlock
    TRANSLATOR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"翻译器后端模块导入失败: {e}")
    TRANSLATOR_AVAILABLE = False
    # 定义fallback类型
    class Translator:
        sugoi = "sugoi"
    
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
        from . import get_config_service  # Lazy import to avoid circular dependency
        self.config_service = get_config_service()
        
        # 从配置服务正确初始化当前状态
        initial_config = self.config_service.get_config()
        initial_translator_name = initial_config.translator.translator
        if TRANSLATOR_AVAILABLE and hasattr(Translator, initial_translator_name):
            self.current_translator_enum = Translator[initial_translator_name]
        else:
            self.current_translator_enum = Translator.sugoi # Fallback
        
        self.current_target_lang = initial_config.translator.target_lang or 'CHS'

    def get_available_translators(self) -> List[str]:
        if not TRANSLATOR_AVAILABLE:
            return []
        return [t.value for t in Translator]

    def get_target_languages(self) -> Dict[str, str]:
        """获取支持的目标语言列表（中文）"""
        return {
            'CHS': '简体中文',
            'CHT': '繁体中文',
            'CSY': '捷克语',
            'NLD': '荷兰语',
            'ENG': '英语',
            'FRA': '法语',
            'DEU': '德语',
            'HUN': '匈牙利语',
            'ITA': '意大利语',
            'JPN': '日语',
            'KOR': '韩语',
            'POL': '波兰语',
            'PTB': '葡萄牙语（巴西）',
            'ROM': '罗马尼亚语',
            'RUS': '俄语',
            'ESP': '西班牙语',
            'TRK': '土耳其语',
            'UKR': '乌克兰语',
            'VIN': '越南语',
            'ARA': '阿拉伯语',
            'SRP': '塞尔维亚语',
            'HRV': '克罗地亚语',
            'THA': '泰语',
            'IND': '印度尼西亚语',
            'FIL': '菲律宾语（他加禄语）'
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
                    # FIX: Instantiate TextBlock using dictionary unpacking, not a non-existent class method.
                    translator_args.text_regions = [TextBlock(**r) for r in regions]
                except (TypeError, KeyError) as e:
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