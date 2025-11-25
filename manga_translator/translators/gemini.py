import os
import re
import asyncio
import json
from typing import List, Dict, Any
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from .common import CommonTranslator, VALID_LANGUAGES
from .keys import GEMINI_API_KEY
from ..utils import Context


def _flatten_prompt_data(data: Any, indent: int = 0) -> str:
    """Recursively flattens a dictionary or list into a formatted string."""
    prompt_parts = []
    prefix = "  " * indent

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                prompt_parts.append(f"{prefix}- {key}:")
                prompt_parts.append(_flatten_prompt_data(value, indent + 1))
            else:
                prompt_parts.append(f"{prefix}- {key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                prompt_parts.append(_flatten_prompt_data(item, indent + 1))
            else:
                prompt_parts.append(f"{prefix}- {item}")
    
    return "\n".join(prompt_parts)

class GeminiTranslator(CommonTranslator):
    """
    Gemini纯文本翻译器
    支持批量文本翻译，不包含图片处理
    """
    _LANGUAGE_CODE_MAP = VALID_LANGUAGES
    
    # 类变量: 跨实例共享的RPM限制时间戳
    _GLOBAL_LAST_REQUEST_TS = {}  # {model_name: timestamp}
    
    def __init__(self):
        super().__init__()
        self.client = None
        self.prev_context = ""  # 用于存储多页上下文
        # Initial setup from environment variables
        # 重新加载 .env 文件以获取最新配置
        from dotenv import load_dotenv
        load_dotenv(override=True)
        self.api_key = os.getenv('GEMINI_API_KEY', GEMINI_API_KEY)
        self.base_url = os.getenv('GEMINI_API_BASE', 'https://generativelanguage.googleapis.com')
        self.model_name = os.getenv('GEMINI_MODEL', "gemini-1.5-flash")
        self.max_tokens = 8000  # 设置为8000，避免超过API限制
        self.temperature = 0.1
        self._MAX_REQUESTS_PER_MINUTE = 0  # 默认无限制
        # 使用全局时间戳,跨实例共享
        if self.model_name not in GeminiTranslator._GLOBAL_LAST_REQUEST_TS:
            GeminiTranslator._GLOBAL_LAST_REQUEST_TS[self.model_name] = 0
        self._last_request_ts_key = self.model_name
        self.safety_settings = [
            {
                "category": HarmCategory.HARM_CATEGORY_HARASSMENT,
                "threshold": HarmBlockThreshold.BLOCK_NONE,
            },
            {
                "category": HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                "threshold": HarmBlockThreshold.BLOCK_NONE,
            },
            {
                "category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                "threshold": HarmBlockThreshold.BLOCK_NONE,
            },
            {
                "category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                "threshold": HarmBlockThreshold.BLOCK_NONE,
            },
        ]
        self._setup_client()
    
    def set_prev_context(self, context: str):
        """设置多页上下文（用于context_size > 0时）"""
        self.prev_context = context if context else ""
    
    def parse_args(self, args):
        """解析配置参数"""
        # 从配置中读取RPM限制
        max_rpm = getattr(args, 'max_requests_per_minute', 0)
        if max_rpm > 0:
            self._MAX_REQUESTS_PER_MINUTE = max_rpm
            self.logger.info(f"Setting Gemini max requests per minute to: {max_rpm}")
    
    def _setup_client(self):
        """设置Gemini客户端"""
        if not self.client and self.api_key:
            client_options = {"api_endpoint": self.base_url} if self.base_url else None

            genai.configure(
                api_key=self.api_key,
                transport='rest',  # 支持自定义base_url
                client_options=client_options
            )
            
            # Apply different configs for different API types
            # 判断是否为官方 API：未设置 base_url 或 base_url 是官方地址
            is_official_api = not self.base_url or self.base_url == 'https://generativelanguage.googleapis.com' or self.base_url.startswith('https://generativelanguage.googleapis.com')

            if is_official_api:
                # Official Google API - full config
                generation_config = {
                    "temperature": self.temperature,
                    "top_p": 0.95,
                    "top_k": 64,
                    "max_output_tokens": self.max_tokens,
                    "response_mime_type": "text/plain",
                }
                model_args = {
                    "model_name": self.model_name,
                    "generation_config": generation_config,
                    "safety_settings": self.safety_settings
                }
                self.logger.info(f"使用官方Google API，应用完整配置（包含安全设置）。Base URL: {self.base_url or '默认'}")
            else:
                # Third-party API - minimal config to avoid format issues
                generation_config = {
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                }
                model_args = {
                    "model_name": self.model_name,
                    "generation_config": generation_config,
                }
                self.logger.info(f"检测到第三方API，使用简化配置（不发送安全设置）。Base URL: {self.base_url}")

            self.client = genai.GenerativeModel(**model_args)
    
    def _build_system_prompt(self, source_lang: str, target_lang: str, custom_prompt_json: Dict[str, Any] = None, line_break_prompt_json: Dict[str, Any] = None) -> str:
        """构建系统提示词"""
        # Map language codes to full names for clarity in the prompt
        lang_map = {
            "CHS": "Simplified Chinese",
            "CHT": "Traditional Chinese",
            "JPN": "Japanese",
            "ENG": "English",
            "KOR": "Korean",
            "VIN": "Vietnamese",
            "FRA": "French",
            "DEU": "German",
            "ITA": "Italian",
        }
        target_lang_full = lang_map.get(target_lang, target_lang)

        custom_prompt_str = ""
        if custom_prompt_json:
            custom_prompt_str = _flatten_prompt_data(custom_prompt_json)

        line_break_prompt_str = ""
        if line_break_prompt_json and line_break_prompt_json.get('line_break_prompt'):
            line_break_prompt_str = line_break_prompt_json['line_break_prompt']

        try:
            from ..utils import BASE_PATH
            import os
            import json
            prompt_path = os.path.join(BASE_PATH, 'dict', 'system_prompt_hq.json')
            with open(prompt_path, 'r', encoding='utf-8') as f:
                base_prompt_data = json.load(f)
            base_prompt = base_prompt_data['system_prompt']
        except Exception as e:
            self.logger.warning(f"Failed to load system prompt from file, falling back to hardcoded prompt. Error: {e}")
            base_prompt = f"""You are an expert manga translator. Your task is to accurately translate manga text from the source language into **{{{target_lang}}}**.

**CRITICAL INSTRUCTIONS (FOLLOW STRICTLY):**

1.  **DIRECT TRANSLATION ONLY**: Your output MUST contain ONLY the raw, translated text. Nothing else.
    -   DO NOT include the original text.
    -   DO NOT include any explanations, greetings, apologies, or any conversational text.
    -   DO NOT use Markdown formatting (like ```json or ```).
    -   The output is fed directly to an automated script. Any extra text will cause it to fail.

2.  **MATCH LINE COUNT**: The number of lines in your output MUST EXACTLY match the number of text regions you are asked to translate. Each line in your output corresponds to one numbered text region in the input.

3.  **TRANSLATE EVERYTHING**: Translate all text provided, including sound effects and single characters. Do not leave any line untranslated.

4.  **ACCURACY AND TONE**:
    -   Preserve the original tone, emotion, and character's voice.
    -   Ensure consistent translation of names, places, and special terms.
    -   For onomatopoeia (sound effects), provide the equivalent sound in {{{target_lang}}} or a brief description (e.g., '(rumble)', '(thud)').

---

**EXAMPLE OF CORRECT AND INCORRECT OUTPUT:**

**[ CORRECT OUTPUT EXAMPLE ]**
This is a correct response. Notice it only contains the translated text, with each translation on a new line.

(Imagine the user input was: "1. うるさい！", "2. 黙れ！")
```
吵死了！
闭嘴！
```

**[ ❌ INCORRECT OUTPUT EXAMPLE ]**
This is an incorrect response because it includes extra text and explanations.

(Imagine the user input was: "1. うるさい！", "2. 黙れ！")
```
好的，这是您的翻译：
1. 吵死了！
2. 闭嘴！
```
**REASONING:** The above example is WRONG because it includes "好的，这是您的翻译：" and numbering. Your response must be ONLY the translated text, line by line.

---

**FINAL INSTRUCTION:** Now, perform the translation task. Remember, your response must be clean, containing only the translated text.
"""

        # Replace placeholder with the full language name
        base_prompt = base_prompt.replace("{{{target_lang}}}", target_lang_full)

        # Combine prompts
        final_prompt = ""
        if line_break_prompt_str:
            final_prompt += f"{line_break_prompt_str}\n\n---\n\n"
        if custom_prompt_str:
            final_prompt += f"{custom_prompt_str}\n\n---\n\n"
        
        final_prompt += base_prompt
        return final_prompt

    def _build_user_prompt(self, texts: List[str], ctx: Any) -> str:
        """构建用户提示词（纯文本版）- 使用统一方法"""
        return self._build_user_prompt_for_texts(texts, ctx, self.prev_context)

    async def _translate_batch(self, texts: List[str], source_lang: str, target_lang: str, custom_prompt_json: Dict[str, Any] = None, line_break_prompt_json: Dict[str, Any] = None, ctx: Any = None, split_level: int = 0) -> List[str]:
        """批量翻译方法（纯文本）"""
        if not texts:
            return []
        
        if not self.client:
            self._setup_client()
        
        if not self.client:
            self.logger.error("Gemini客户端初始化失败")
            return texts
        
        # 打印输入的原文 - 已注释以减少日志输出
        # self.logger.info("--- Original Texts for Translation ---")
        # for i, text in enumerate(texts):
        #     self.logger.info(f"{i+1}: {text}")
        # self.logger.info("------------------------------------")

        # 添加系统提示词和用户提示词
        system_prompt = self._build_system_prompt(source_lang, target_lang, custom_prompt_json=custom_prompt_json, line_break_prompt_json=line_break_prompt_json)
        user_prompt = self._build_user_prompt(texts, ctx)
        
        combined_prompt = system_prompt + "\n\n" + user_prompt
        
        # 发送请求
        max_retries = self.attempts
        attempt = 0
        is_infinite = max_retries == -1
        local_attempt = 0  # 本次批次的尝试次数

        # Dynamically construct arguments for generate_content
        request_args = {
            "contents": combined_prompt
        }
        is_third_party_api = self.base_url and self.base_url != 'https://generativelanguage.googleapis.com'
        if is_third_party_api:
            self.logger.warning("Omitting safety settings for third-party API request.")
        else:
            request_args["safety_settings"] = self.safety_settings

        def generate_content_with_logging(**kwargs):
            return self.client.generate_content(**kwargs)

        while is_infinite or attempt < max_retries:
            # 检查全局尝试次数
            if not self._increment_global_attempt():
                self.logger.error("Reached global attempt limit. Stopping translation.")
                raise Exception(f"Global attempt limit reached: {self._global_attempt_count}/{self._max_total_attempts}")

            local_attempt += 1
            attempt += 1

            # 检查是否应该触发分割（重试2次后）
            if local_attempt > self._SPLIT_THRESHOLD and len(texts) > 1 and split_level < self._MAX_SPLIT_ATTEMPTS:
                self.logger.warning(f"Triggering split after {local_attempt} local attempts")
                raise self.SplitException(local_attempt, texts)

            try:
                # RPM限制
                if self._MAX_REQUESTS_PER_MINUTE > 0:
                    import time
                    now = time.time()
                    delay = 60.0 / self._MAX_REQUESTS_PER_MINUTE
                    elapsed = now - GeminiTranslator._GLOBAL_LAST_REQUEST_TS[self._last_request_ts_key]
                    if elapsed < delay:
                        sleep_time = delay - elapsed
                        self.logger.info(f'Ratelimit sleep: {sleep_time:.2f}s')
                        await asyncio.sleep(sleep_time)
                
                response = await asyncio.to_thread(
                    generate_content_with_logging,
                    **request_args
                )
                
                # 在API调用成功后立即更新时间戳，确保所有请求（包括重试）都被计入速率限制
                if self._MAX_REQUESTS_PER_MINUTE > 0:
                    GeminiTranslator._GLOBAL_LAST_REQUEST_TS[self._last_request_ts_key] = time.time()

                # 检查finish_reason，只有成功(1)才继续，其他都重试
                if hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    if hasattr(candidate, 'finish_reason'):
                        finish_reason = candidate.finish_reason

                        if finish_reason != 1:  # 不是STOP(成功)
                            attempt += 1
                            log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"

                            # 显示具体的finish_reason信息
                            finish_reason_map = {
                                1: "STOP(成功)",
                                2: "SAFETY(安全策略拦截)",
                                3: "MAX_TOKENS(达到最大token限制)",
                                4: "RECITATION(内容重复检测)",
                                5: "OTHER(其他未知错误)"
                            }
                            reason_desc = finish_reason_map.get(finish_reason, f"未知错误码({finish_reason})")

                            self.logger.warning(f"Gemini API失败 ({log_attempt}): finish_reason={finish_reason} - {reason_desc}")

                            if not is_infinite and attempt >= max_retries:
                                self.logger.error(f"Gemini翻译在多次重试后仍失败: {reason_desc}")
                                break
                            await asyncio.sleep(1)
                            continue

                # 尝试访问 .text 属性，如果API因安全原因等返回空内容，这里会触发异常
                result_text = response.text.strip()

                # 调试日志：打印Gemini的原始返回内容
                self.logger.info(f"--- Gemini Raw Response ---\n{result_text}\n---------------------------")

                # 增加清理步骤，移除可能的Markdown代码块
                if result_text.startswith("```") and result_text.endswith("```"):
                    result_text = result_text[3:-3].strip()
                
                # 如果成功获取文本，则处理并返回
                translations = []
                for line in result_text.split('\n'):
                    line = line.strip()
                    if line:
                        # 移除编号（如"1. "）
                        line = re.sub(r'^\d+\.\s*', '', line)
                        line = line.replace('\\n', '\n').replace('↵', '\n')
                        translations.append(line)
                
                # Strict validation: must match input count
                if len(translations) != len(texts):
                    attempt += 1
                    log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                    self.logger.warning(f"[{log_attempt}] Translation count mismatch: expected {len(texts)}, got {len(translations)}. Retrying...")
                    self.logger.warning(f"Expected texts: {texts}")
                    self.logger.warning(f"Got translations: {translations}")
                    
                    if not is_infinite and attempt >= max_retries:
                        raise Exception(f"Translation count mismatch after {max_retries} attempts: expected {len(texts)}, got {len(translations)}")
                    
                    await asyncio.sleep(2)
                    continue

                # 质量验证：检查空翻译、合并翻译、可疑符号等
                is_valid, error_msg = self._validate_translation_quality(texts, translations)
                if not is_valid:
                    attempt += 1
                    log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                    self.logger.warning(f"[{log_attempt}] Quality check failed: {error_msg}. Retrying...")

                    if not is_infinite and attempt >= max_retries:
                        raise Exception(f"Quality check failed after {max_retries} attempts: {error_msg}")

                    await asyncio.sleep(2)
                    continue

                # 打印原文和译文的对应关系
                self.logger.info("--- Translation Results ---")
                for original, translated in zip(texts, translations):
                    self.logger.info(f'{original} -> {translated}')
                self.logger.info("---------------------------")

                # BR检查：检查翻译结果是否包含必要的[BR]标记
                if not self._validate_br_markers(translations, queries=texts, ctx=ctx):
                    attempt += 1
                    log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                    self.logger.warning(f"[{log_attempt}] BR markers missing, retrying...")
                    
                    # 如果达到最大重试次数，抛出友好的异常
                    if not is_infinite and attempt >= max_retries:
                        from .common import BRMarkersValidationException
                        self.logger.error("Gemini翻译在多次重试后仍然失败：AI断句检查失败。")
                        raise BRMarkersValidationException(
                            missing_count=0,  # 具体数字在_validate_br_markers中已记录
                            total_count=len(texts),
                            tolerance=max(1, len(texts) // 10)
                        )
                    
                    await asyncio.sleep(2)
                    continue

                return translations[:len(texts)]

            except Exception as e:
                attempt += 1
                log_attempt = f"{attempt}/{max_retries}" if not is_infinite else f"Attempt {attempt}"
                self.logger.warning(f"Gemini翻译出错 ({log_attempt}): {e}")

                if "finish_reason: 2" in str(e) or "finish_reason is 2" in str(e):
                    self.logger.warning("检测到Gemini安全设置拦截。正在重试...")
                
                # 检查是否达到最大重试次数（注意：attempt已经+1了）
                if not is_infinite and attempt >= max_retries:
                    self.logger.error("Gemini翻译在多次重试后仍然失败。即将终止程序。")
                    raise e
                
                await asyncio.sleep(1)
        
        return texts

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str], ctx=None) -> List[str]:
        """主翻译方法"""
        if not self.client:
            from .. import manga_translator
            if hasattr(manga_translator, 'config') and hasattr(manga_translator.config, 'translator'):
                self.parse_args(manga_translator.config.translator)

        if not queries:
            return []

        # 重置全局尝试计数器
        self._reset_global_attempt_count()

        self.logger.info(f"使用Gemini纯文本翻译模式处理{len(queries)}个文本，最大尝试次数: {self._max_total_attempts}")
        custom_prompt_json = getattr(ctx, 'custom_prompt_json', None) if ctx else None
        line_break_prompt_json = getattr(ctx, 'line_break_prompt_json', None) if ctx else None

        # 使用分割包装器进行翻译
        translations = await self._translate_with_split(
            self._translate_batch,
            queries,
            split_level=0,
            source_lang=from_lang,
            target_lang=to_lang,
            custom_prompt_json=custom_prompt_json,
            line_break_prompt_json=line_break_prompt_json,
            ctx=ctx
        )

        # 应用文本后处理
        translations = [self._clean_translation_output(q, r, to_lang) for q, r in zip(queries, translations)]
        return translations

