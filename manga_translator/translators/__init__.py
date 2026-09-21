from typing import List, Optional

import py3langid as langid

from ..config import Config, Translator, TranslatorChain, TranslatorConfig
from ..utils import Context
from .common import *
from .gemini import GeminiTranslator
from .gemini_hq import GeminiHighQualityTranslator
from .none import NoneTranslator
from .openai import OpenAITranslator
from .openai_hq import OpenAIHighQualityTranslator
from .original import OriginalTranslator
from .sakura import SakuraTranslator

GPT_TRANSLATORS = {
    Translator.openai: OpenAITranslator,
    Translator.openai_hq: OpenAIHighQualityTranslator,
    Translator.gemini: GeminiTranslator,
    Translator.gemini_hq: GeminiHighQualityTranslator,
}

TRANSLATORS = {
    Translator.none: NoneTranslator,
    Translator.original: OriginalTranslator,
    Translator.sakura: SakuraTranslator,
    **GPT_TRANSLATORS,
}
translator_cache = {}
NON_CACHED_TRANSLATORS = {
    key for key in TRANSLATORS.keys() if key not in {Translator.none, Translator.original}
}

def get_translator(key: Translator, *args, **kwargs) -> CommonTranslator:
    if key not in TRANSLATORS:
        raise ValueError(f'Could not find translator for: "{key}". Choose from the following: %s' % ','.join(TRANSLATORS))
    if key in NON_CACHED_TRANSLATORS:
        return TRANSLATORS[key](*args, **kwargs)
    # Stateless translators can be safely reused.
    if key not in translator_cache:
        translator = TRANSLATORS[key]
        translator_cache[key] = translator(*args, **kwargs)
    return translator_cache[key]

async def prepare(chain: TranslatorChain):
    for key, tgt_lang in chain.chain:
        translator = get_translator(key)
        translator.supports_languages('auto', tgt_lang, fatal=True)

# TODO: Optionally take in strings instead of TranslatorChain for simplicity
async def dispatch(chain: TranslatorChain, queries: List[str], config: Config, use_mtpe: bool = False, args:Optional[Context] = None, device: str = 'cpu') -> List[str]:
    if not queries:
        return queries

    if chain.target_lang is not None:
        _text_lang = ISO_639_1_TO_VALID_LANGUAGES.get(langid.classify('\n'.join(queries))[0])
        translator = None
        flag=0
        for key, lang in chain.chain:           
            #if text_lang == lang:
                #translator = get_translator(key)
            #if translator is None:
            translator = get_translator(chain.translators[flag])
            translator.parse_args(config)
            queries = await translator.translate('auto', chain.langs[flag], queries, use_mtpe)
            flag+=1
        return queries
    if args is not None:
        args['translations'] = {}
    for key, tgt_lang in chain.chain:
        translator = get_translator(key)
        translator.parse_args(config)
        if key.value in ["gemini_hq", "openai_hq"]:
            queries = await translator.translate('auto', tgt_lang, queries, ctx=args)
        else:
            # 传递ctx参数（用于AI断句）
            queries = await translator.translate('auto', tgt_lang, queries, use_mtpe=use_mtpe, ctx=args)
        if args is not None:
            args['translations'][tgt_lang] = queries
    return queries


async def dispatch_batch(chain: TranslatorChain, batch_queries: List[List[str]], translator_config: Optional[TranslatorConfig] = None, use_mtpe: bool = False, args:Optional[Context] = None, device: str = 'cpu') -> List[List[str]]:
    """
    批量翻译调度器，将多个文本列表一次性发送给翻译器
    Args:
        chain: 翻译器链
        batch_queries: 批量查询列表，每个元素是一个字符串列表
        translator_config: 翻译器配置
        use_mtpe: 是否使用机器翻译后编辑
        args: 上下文参数
        device: 设备
    Returns:
        批量翻译结果列表
    """
    if not batch_queries or not any(batch_queries):
        return batch_queries
    
    # 将批量查询平铺为单一列表
    flat_queries = []
    query_mapping = []  # 记录每个查询属于哪个批次
    
    for batch_idx, queries in enumerate(batch_queries):
        for query in queries:
            flat_queries.append(query)
            query_mapping.append(batch_idx)
    
    # 使用现有的翻译调度器处理平铺的查询列表
    flat_results = await dispatch(chain, flat_queries, translator_config, use_mtpe, args, device)
    
    # 将结果重新分组回批量结构
    batch_results = [[] for _ in batch_queries]
    for result, batch_idx in zip(flat_results, query_mapping):
        batch_results[batch_idx].append(result)
    
    return batch_results

LANGDETECT_MAP = {
    'zh-cn': 'CHS',
    'zh-tw': 'CHT',
    'cs': 'CSY',
    'nl': 'NLD',
    'en': 'ENG',
    'fr': 'FRA',
    'de': 'DEU',
    'hu': 'HUN',
    'it': 'ITA',
    'ja': 'JPN',
    'ko': 'KOR',
    'pl': 'POL',
    'pt': 'PTB',
    'ro': 'ROM',
    'ru': 'RUS',
    'es': 'ESP',
    'tr': 'TRK',
    'uk': 'UKR',
    'vi': 'VIN',
    'ar': 'ARA',
    'fa': 'PER',
    'hr': 'HRV',
    'th': 'THA',
    'id': 'IND',
    'tl': 'FIL'
}

async def unload(key: Translator):
    translator = translator_cache.pop(key, None)
    if isinstance(translator, OfflineTranslator):
        # 参数仅为兼容 OfflineTranslator.unload(device) 签名，内部不依赖具体值。
        await translator.unload('cuda')
