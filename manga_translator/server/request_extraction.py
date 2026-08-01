# -*- coding: utf-8 -*-
import asyncio
import builtins
import io
import json
import logging
import os
import re
from base64 import b64decode
from contextlib import asynccontextmanager
from typing import Union

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel

from manga_translator import Config
from manga_translator.image_formats import (
    RGB_PIL_FORMATS,
    resolve_output_image_format,
)
from manga_translator.utils import normalize_pil_image, open_pil_image
from manga_translator.utils.image_modes import normalize_rgb_image

logger = logging.getLogger('manga_translator.server')


class TaskLogHandler(logging.Handler):
    """任务专属的日志处理器，将日志发送到任务队列"""
    
    def __init__(self, task_id: str, session_id: str = None):
        super().__init__()
        self.task_id = task_id
        self.session_id = session_id
        self.ignored_loggers = {'uvicorn.access', 'uvicorn.error', 'httpcore', 'httpx'}
    
    def emit(self, record):
        try:
            if record.name in self.ignored_loggers:
                return
            
            from manga_translator.server.core.logging_manager import add_log
            msg = self.format(record)
            level = record.levelname
            # Root logger already prints the original record to the console.
            # Only mirror it into the task log queue here to avoid duplicate output.
            add_log(msg, level, self.task_id, self.session_id, skip_print=True)
        except Exception:
            self.handleError(record)


def _create_task_log_handler(task_id: str, session_id: str = None) -> TaskLogHandler:
    """创建任务专属的日志处理器并添加到相关logger"""
    handler = TaskLogHandler(task_id, session_id)
    formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    
    # 添加到 manga_translator logger（下划线命名空间）
    mt_logger = logging.getLogger('manga_translator')
    mt_logger.addHandler(handler)
    
    # 添加到 manga-translator logger（连字符命名空间，翻译器使用）
    mt_hyphen_logger = logging.getLogger('manga-translator')
    mt_hyphen_logger.addHandler(handler)
    
    return handler


def _remove_task_log_handler(handler: TaskLogHandler):
    """移除任务专属的日志处理器"""
    if handler is None:
        return
    
    try:
        mt_logger = logging.getLogger('manga_translator')
        mt_logger.removeHandler(handler)
    except Exception:
        pass
    
    try:
        mt_hyphen_logger = logging.getLogger('manga-translator')
        mt_hyphen_logger.removeHandler(handler)
    except Exception:
        pass


@asynccontextmanager
async def with_user_env_vars(config: Config):
    """
    Compatibility wrapper kept for the existing call sites.

    User-specific API settings are now carried on the request Config object and
    resolved explicitly by each backend, so we no longer mutate process-wide
    environment variables here.
    """
    del config
    yield

class TranslateRequest(BaseModel):
    """This request can be a multipart or a json request"""
    image: bytes|str
    config: Config = Config()

class BatchTranslateRequest(BaseModel):
    """Batch translation request"""
    images: list[bytes|str]
    config: dict | Config = {}
    batch_size: int = 4
    filenames: list[str] = []  # 原始文件名列表（可选）
    
    class Config:
        arbitrary_types_allowed = True

async def to_pil_image(image: Union[str, bytes, Image.Image]) -> Image.Image:
    try:
        if isinstance(image, Image.Image):
            return normalize_pil_image(image, eager=False)
        elif isinstance(image, builtins.bytes):
            image = open_pil_image(io.BytesIO(image), eager=False)
            return image
        else:
            if re.match(r'^data:image/.+;base64,', image):
                value = image.split(',', 1)[1]
                image_data = b64decode(value, validate=True)
                image = open_pil_image(io.BytesIO(image_data), eager=False)
                return image
            raise HTTPException(
                status_code=422,
                detail="Image must be uploaded as bytes or a base64 data URI",
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid image data")


def _run_translate_sync(pil_image, config: Config, task_id: str = None, cancel_check_callback=None):
    """
    同步执行翻译操作的辅助函数。
    用于在线程池中运行，避免阻塞 FastAPI 事件循环。
    使用全局翻译器实例，复用已加载的模型。
    
    Args:
        pil_image: PIL 图片
        config: 翻译配置
        task_id: 任务ID（用于更新线程信息）
        cancel_check_callback: 取消检查回调函数
    """
    import threading

    #     import gc
    from manga_translator.server.core.task_manager import (
        get_global_translator,
        update_task_thread_id,
    )
    
    # 更新任务的线程ID
    if task_id:
        update_task_thread_id(task_id, threading.current_thread().ident)
    
    # 获取全局翻译器实例（复用模型）
    translator = get_global_translator()
    
    # 设置取消检查回调
    if cancel_check_callback:
        translator.set_cancel_check_callback(cancel_check_callback)
    
    # 在新线程中创建事件循环来运行异步翻译
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(translator.translate(pil_image, config))
        return result
    finally:
        try:
            # 清除取消回调，避免影响下一个任务
            if cancel_check_callback:
                translator.set_cancel_check_callback(None)
            
            # 关闭事件循环前，取消所有待处理的任务
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            
            # 等待所有任务取消完成
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            
            # 关闭事件循环
            loop.close()
            
            # 清除线程局部的事件循环引用（Docker环境关键）
            asyncio.set_event_loop(None)
            
            # 请求级内存清理：清理翻译器内部状态，保留模型
            from manga_translator.server.core.task_manager import cleanup_after_request
            cleanup_after_request()
            
        except Exception as e:
            logger.warning(f"线程清理时出错: {e}")


def _run_translate_batch_sync(images_with_configs: list, batch_size: int, task_id: str = None, cancel_check_callback=None):
    """
    同步执行批量翻译操作的辅助函数。
    用于在线程池中运行，避免阻塞 FastAPI 事件循环。
    使用全局翻译器实例，复用已加载的模型。
    
    Args:
        images_with_configs: 图片和配置列表
        batch_size: 批量大小
        task_id: 任务ID（用于更新线程信息）
        cancel_check_callback: 取消检查回调函数
    """
    import threading

    #     import gc
    from manga_translator.server.core.task_manager import (
        get_global_translator,
        update_task_thread_id,
    )
    
    # 更新任务的线程ID
    if task_id:
        update_task_thread_id(task_id, threading.current_thread().ident)
    
    # 获取全局翻译器实例（复用模型）
    translator = get_global_translator()
    
    # 设置取消检查回调
    if cancel_check_callback:
        translator.set_cancel_check_callback(cancel_check_callback)
    
    # 在新线程中创建事件循环来运行异步翻译
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(translator.translate_batch(images_with_configs, batch_size))
        return result
    finally:
        try:
            # 清除取消回调
            if cancel_check_callback:
                translator.set_cancel_check_callback(None)
            
            # 关闭事件循环前，取消所有待处理的任务
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            
            # 等待所有任务取消完成
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            
            # 关闭事件循环
            loop.close()
            
            # 清除线程局部的事件循环引用（Docker环境关键）
            asyncio.set_event_loop(None)
            
            # 请求级内存清理：清理翻译器内部状态，保留模型
            from manga_translator.server.core.task_manager import cleanup_after_request
            cleanup_after_request()
            
        except Exception as e:
            logger.warning(f"线程清理时出错: {e}")


def prepare_translator_params(config: Config, workflow: str = "normal") -> dict:
    """Prepare translator parameters based on workflow."""
    translator_params = {}
    
    if hasattr(config, 'cli'):
        if hasattr(config.cli, 'load_text'):
            config.cli.load_text = False
        if hasattr(config.cli, 'translate_json_only'):
            config.cli.translate_json_only = False
        if hasattr(config.cli, 'template'):
            config.cli.template = False
        if hasattr(config.cli, 'generate_and_export'):
            config.cli.generate_and_export = False
        if hasattr(config.cli, 'upscale_only'):
            config.cli.upscale_only = False
        if hasattr(config.cli, 'colorize_only'):
            config.cli.colorize_only = False
        if hasattr(config.cli, 'inpaint_only'):
            config.cli.inpaint_only = False
        # 替换翻译功能只能在 Qt UI 中使用，Web UI 禁止使用
        if hasattr(config.cli, 'replace_translation'):
            config.cli.replace_translation = False
        if hasattr(config.cli, 'use_gpu'):
            config.cli.use_gpu = False
        if hasattr(config.cli, 'attempts'):
            attempts = config.cli.attempts
            # -1 表示无限重试；0 表示不重试（仅首次请求）
            if attempts is not None and (attempts >= 0 or attempts == -1):
                translator_params['attempts'] = attempts
    
    if hasattr(config, 'render') and getattr(config.render, 'font_family', None):
        translator_params['font_family'] = config.render.font_family
        logger.debug(f"Using font family: {config.render.font_family}")

    # 直接粘贴模式只能在 Qt UI 的替换翻译模式中使用，Web UI 禁止使用
    if hasattr(config, 'render') and hasattr(config.render, 'enable_template_alignment'):
        config.render.enable_template_alignment = False
    
    # 提示词路径 - 直接传递相对路径，翻译程序会自动用 BASE_PATH 拼接
    # (high_quality_prompt_path 在 config.translator 中，翻译程序会直接读取)
    
    if workflow == "export_original":
        translator_params['template'] = True
        translator_params['save_text'] = True
    elif workflow == "save_json":
        translator_params['save_text'] = True
        translator_params['generate_and_export'] = True
    elif workflow == "load_text":
        translator_params['load_text'] = True
    elif workflow == "upscale_only":
        translator_params['upscale_only'] = True
    elif workflow == "colorize_only":
        translator_params['colorize_only'] = True
    
    return translator_params


async def get_ctx(req: Request, config: Config, image: str|bytes, workflow: str = "normal"):
    """Translate single image. 使用全局翻译器实例，复用已加载的模型。"""
    from manga_translator.server.core.logging_manager import add_log
    from manga_translator.server.core.task_manager import get_semaphore
    
    # 动态获取 semaphore（支持热加载）
    translation_semaphore = get_semaphore()
    
    pil_image = await to_pil_image(image)
    
    try:
        # 准备工作流参数（这些会影响翻译行为，但不需要重建翻译器）
        prepare_translator_params(config, workflow)
        
        async with with_user_env_vars(config):
            # 等待获取翻译槽位
            if translation_semaphore:
                try:
                    waiters_count = len(translation_semaphore._waiters) if hasattr(translation_semaphore, '_waiters') and translation_semaphore._waiters else 0
                except Exception:
                    waiters_count = 0
                
                if waiters_count > 0:
                    add_log(f"等待翻译槽位... (队列中有 {waiters_count} 个任务)", "INFO")
                
                async with translation_semaphore:
                    add_log("获得翻译槽位，开始翻译", "INFO")
                    # 使用翻译线程池执行，复用全局翻译器
                    from manga_translator.server.core.task_manager import (
                        run_in_translator_thread,
                    )
                    ctx = await run_in_translator_thread(_run_translate_sync, pil_image, config)
            else:
                # 没有 semaphore 时直接执行
                from manga_translator.server.core.task_manager import (
                    run_in_translator_thread,
                )
                ctx = await run_in_translator_thread(_run_translate_sync, pil_image, config)
        
        result = {
            'success': ctx.success if hasattr(ctx, 'success') else (ctx.result is not None),
            'workflow': workflow
        }
        
        if ctx.result:
            result['has_image'] = True
        
        if hasattr(ctx, 'text_regions') and ctx.text_regions:
            result['text_regions'] = []
            for region in ctx.text_regions:
                region_data = {
                    'text': region.text if hasattr(region, 'text') else '',
                    'translation': region.translation if hasattr(region, 'translation') else '',
                }
                result['text_regions'].append(region_data)
        
        ctx._workflow_result = result
        return ctx
    
    finally:
        # 关闭输入图片
        try:
            pil_image.close()
        except Exception:
            pass
        
        # 注意：ctx的清理和内存回收已在_run_translate_sync的cleanup_after_request中处理
        # 这里只需要处理本函数创建的局部资源


async def while_streaming(req: Request, transform, config: Config, image: bytes | str, workflow: str = "normal", original_filename: str = None):
    """Streaming translation with concurrency control."""
    from manga_translator.server.core.config_manager import (
        reload_admin_settings_if_changed,
    )
    from manga_translator.server.core.logging_manager import (
        add_log,
        generate_task_id,
        set_session_id,
        set_task_id,
    )
    from manga_translator.server.core.task_manager import (
        get_semaphore,
        is_task_cancelled,
        register_active_task,
        unregister_active_task,
        update_task_status,
    )
    
    # 检查配置是否变化（热加载）
    reload_admin_settings_if_changed()
    
    # 动态获取 semaphore（支持热加载）
    translation_semaphore = get_semaphore()
    
    task_id = generate_task_id()
    set_task_id(task_id)
    
    # 保存原始文件名到 config 中
    if original_filename:
        config._original_filename = original_filename
    
    session_id = getattr(config, '_session_id', None)
    if session_id:
        set_session_id(session_id)
    
    username = getattr(config, '_username', 'unknown')
    translator_name = "unknown"
    if hasattr(config, 'translator') and hasattr(config.translator, 'translator'):
        translator_name = config.translator.translator
    
    current_task = None
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        pass
    register_active_task(task_id, current_task, username, translator_name)
    
    # 创建任务专属的日志处理器（类似Qt UI的做法）
    task_log_handler = None
    try:
        task_log_handler = _create_task_log_handler(task_id, session_id)
    except Exception as e:
        add_log(f"Failed to create task log handler: {e}", "WARNING")
    
    async def generate():
        # 在生成器内部重新获取 semaphore（确保使用最新的）
        nonlocal translation_semaphore
        translation_semaphore = get_semaphore()
        print(f"[DEBUG] generate() 开始, semaphore={translation_semaphore}, task_id={task_id}")
        
        # 先检查用户级并发限制（在获取 semaphore 之前）
        from manga_translator.server.core.middleware import (
            check_concurrent_limit,
            decrement_task_count,
            increment_task_count,
        )
        
        # 增加用户任务计数
        increment_task_count(username)
        
        try:
            # 检查是否超过用户并发限制
            check_concurrent_limit(username)
            
            if translation_semaphore is None:
                print("[DEBUG] semaphore 是 None，尝试初始化")
                from manga_translator.server.core.task_manager import init_semaphore
                init_semaphore()
                translation_semaphore = get_semaphore()
                print(f"[DEBUG] 初始化后 semaphore={translation_semaphore}")
            
            if translation_semaphore:
                # 检查当前等待队列
                try:
                    waiters_count = len(translation_semaphore._waiters) if hasattr(translation_semaphore, '_waiters') and translation_semaphore._waiters else 0
                except Exception:
                    waiters_count = 0
                
                if waiters_count > 0:
                    add_log(f"等待翻译槽位... (队列中有 {waiters_count} 个任务)", "INFO")
                    # 发送排队状态给前端
                    yield pack_message(1, json.dumps({
                        "stage": "queued", 
                        "message": f"排队中... (前面还有 {waiters_count} 个任务)",
                        "queue_position": waiters_count + 1
                    }, ensure_ascii=False).encode('utf-8'))
                
                # 等待获取 semaphore（这里会真正排队）
                print(f"[DEBUG] 准备获取 semaphore, task_id={task_id}, waiters={waiters_count}")
                async with translation_semaphore:
                    # 获得槽位后，更新状态为 running
                    print(f"[DEBUG] 获得 semaphore! task_id={task_id}, 更新状态为 running")
                    update_task_status(task_id, "running")
                    add_log("✓ 获得翻译槽位，开始翻译", "INFO")
                    # 发送获得槽位的通知
                    yield pack_message(1, json.dumps({
                        "stage": "slot_acquired", 
                        "message": "获得翻译槽位，开始处理..."
                    }, ensure_ascii=False).encode('utf-8'))
                    
                    async for chunk in _do_translation():
                        yield chunk
            else:
                async for chunk in _do_translation():
                    yield chunk
        finally:
            # 减少用户任务计数
            decrement_task_count(username)
    
    async def _do_translation():
        try:
            yield pack_message(1, json.dumps({"stage": "task_id", "task_id": task_id}, ensure_ascii=False).encode('utf-8'))
            
            add_log("开始翻译任务", "INFO")
            yield pack_message(1, json.dumps({"stage": "start", "message": "开始处理..."}, ensure_ascii=False).encode('utf-8'))
            
            add_log("加载图片", "INFO")
            yield pack_message(1, json.dumps({"stage": "image_loading", "message": "加载图片中..."}, ensure_ascii=False).encode('utf-8'))
            pil_image = await to_pil_image(image)
            
            add_log("准备翻译参数", "INFO")
            prepare_translator_params(config, workflow)
            
            if is_task_cancelled(task_id):
                add_log("任务已被取消", "WARNING")
                raise asyncio.CancelledError("任务已被管理员取消")
            
            async with with_user_env_vars(config):
                add_log("使用全局翻译器（模型复用）", "INFO")
                yield pack_message(1, json.dumps({"stage": "translator_init", "message": "初始化翻译器..."}, ensure_ascii=False).encode('utf-8'))
                
                if is_task_cancelled(task_id):
                    raise asyncio.CancelledError("任务已被管理员取消")
                
                add_log("执行翻译", "INFO")
                yield pack_message(1, json.dumps({"stage": "translating", "message": "翻译中..."}, ensure_ascii=False).encode('utf-8'))
                
                if is_task_cancelled(task_id):
                    raise asyncio.CancelledError("任务已被管理员取消")
                
                try:
                    add_log("调用翻译器", "INFO")
                    # 使用翻译线程池执行，复用全局翻译器
                    from manga_translator.server.core.task_manager import (
                        run_in_translator_thread,
                    )
                    def cancel_callback():
                        return is_task_cancelled(task_id)

                    ctx = await run_in_translator_thread(_run_translate_sync, pil_image, config, task_id, cancel_callback)
                    add_log(f"翻译完成，有结果: {ctx.result is not None if hasattr(ctx, 'result') else False}", "INFO")
                    
                    result = {
                        'success': ctx.success if hasattr(ctx, 'success') else (ctx.result is not None),
                        'workflow': workflow
                    }
                    
                    if ctx.result:
                        result['has_image'] = True
                    
                    if hasattr(ctx, 'text_regions') and ctx.text_regions:
                        result['text_regions'] = []
                        for region in ctx.text_regions:
                            region_data = {
                                'text': region.text if hasattr(region, 'text') else '',
                                'translation': region.translation if hasattr(region, 'translation') else '',
                            }
                            result['text_regions'].append(region_data)
                    
                    ctx._workflow_result = result
                    
                    yield pack_message(1, json.dumps({"stage": "translate_done", "message": "Processing result..."}, ensure_ascii=False).encode('utf-8'))
                except Exception as translate_error:
                    print(f"[STREAMING ERROR] Translation failed: {type(translate_error).__name__}")
                    yield pack_message(2, json.dumps({"error": "Translation failed", "stage": "translate"}, ensure_ascii=False).encode('utf-8'))
                    return
            
            has_result = ctx.result is not None if hasattr(ctx, 'result') else False
            has_text_regions = hasattr(ctx, 'text_regions') and ctx.text_regions
            text_region_count = len(ctx.text_regions) if has_text_regions else 0
            
            if has_text_regions:
                yield pack_message(1, json.dumps({
                    "stage": "processing", 
                    "message": f"Found {text_region_count} text regions"
                }, ensure_ascii=False).encode('utf-8'))
            
            if not has_result:
                error_msg = "Translation failed: no result image"
                print(f"[STREAMING ERROR] {error_msg}")
                yield pack_message(2, json.dumps({"error": error_msg, "stage": "no_result"}, ensure_ascii=False).encode('utf-8'))
                return
            
            try:
                yield pack_message(1, json.dumps({"stage": "transforming", "message": "Converting..."}, ensure_ascii=False).encode('utf-8'))
                result_data = transform(ctx)
                
                # 保存翻译结果到历史
                try:
                    original_filename = getattr(config, '_original_filename', None)
                    await save_translation_to_history(ctx, username, task_id, workflow, original_filename, config)
                    add_log("Translation saved to history", "INFO")
                except Exception as save_error:
                    add_log(f"Failed to save history: {save_error}", "WARNING")
                
                yield pack_message(1, json.dumps({"stage": "sending", "message": "Sending..."}, ensure_ascii=False).encode('utf-8'))
                yield pack_message(0, result_data)
                
                yield pack_message(1, json.dumps({"stage": "complete", "message": "Done!"}, ensure_ascii=False).encode('utf-8'))
            except Exception as transform_error:
                print(f"[STREAMING ERROR] Transform failed: {type(transform_error).__name__}")
                yield pack_message(2, json.dumps({"error": "Transform failed", "stage": "transform"}, ensure_ascii=False).encode('utf-8'))
                return
            
        except asyncio.CancelledError:
            add_log("Task cancelled", "WARNING")
            try:
                yield pack_message(2, json.dumps({"error": "Task cancelled by admin", "stage": "cancelled"}, ensure_ascii=False).encode('utf-8'))
            except Exception:
                pass
        except Exception as e:
            print(f"[STREAMING ERROR] Translation failed: {type(e).__name__}")
            try:
                yield pack_message(2, json.dumps({"error": "Translation failed", "stage": "unknown"}, ensure_ascii=False).encode('utf-8'))
            except Exception:
                pass
        finally:
            add_log("Cleaning up", "DEBUG")
            try:
                # 使用统一的Context清理函数
                if 'ctx' in locals() and ctx:
                    from manga_translator.server.core.task_manager import (
                        cleanup_context,
                    )
                    cleanup_context(ctx)
                
                # 关闭输入图片
                if 'pil_image' in locals() and pil_image:
                    try:
                        pil_image.close()
                    except Exception:
                        pass
                
                # 注意：不要调用translator.unload_models()
                # 因为我们使用全局翻译器，模型应该被保留复用
                # 内存清理由cleanup_after_request在_run_translate_sync中处理
                
                add_log("Cleanup done", "DEBUG")
            except Exception as cleanup_error:
                add_log(f"Cleanup failed: {cleanup_error}", "WARNING")
            
            # 移除任务专属的日志处理器
            _remove_task_log_handler(task_log_handler)
            
            unregister_active_task(task_id)
    
    print(f"[DEBUG] while_streaming 返回 StreamingResponse, task_id={task_id}")
    return StreamingResponse(generate(), media_type="application/octet-stream")


def pack_message(status: int, data: bytes) -> bytes:
    """Pack streaming message: 1 byte status + 4 bytes size + data"""
    return status.to_bytes(1, 'big') + len(data).to_bytes(4, 'big') + data



async def get_batch_ctx(req: Request, config: Config, images: list[str|bytes], batch_size: int = 4, workflow: str = "normal", task_id: str = None):
    """批量翻译（使用 UI 层逻辑）
    
    Args:
        task_id: 任务ID，用于检查取消状态
    """
    from manga_translator.server.core.logging_manager import add_log
    from manga_translator.server.core.task_manager import (
        get_semaphore,
        is_task_cancelled,
    )
    
    # 动态获取 semaphore（支持热加载）
    translation_semaphore = get_semaphore()
    
    pil_images = []
    contexts = []
    
    try:
        # 检查是否已取消
        if task_id and is_task_cancelled(task_id):
            raise Exception("任务已被取消")
        
        # Convert images to PIL Image objects
        for img in images:
            # 每张图片转换前检查取消状态
            if task_id and is_task_cancelled(task_id):
                raise Exception("任务已被取消")
            pil_img = await to_pil_image(img)
            pil_images.append(pil_img)
        
        # 准备翻译器参数（影响工作流行为）
        prepare_translator_params(config, workflow)
        
        # 准备批量数据
        images_with_configs = [(img, config) for img in pil_images]
        
        # 使用统一的环境变量管理包装器
        async with with_user_env_vars(config):
            # 翻译前再次检查取消状态
            if task_id and is_task_cancelled(task_id):
                raise Exception("任务已被取消")
            
            # 等待获取翻译槽位（与流式端点保持一致）
            if translation_semaphore:
                from manga_translator.server.core.task_manager import update_task_status
                try:
                    waiters_count = len(translation_semaphore._waiters) if hasattr(translation_semaphore, '_waiters') and translation_semaphore._waiters else 0
                except Exception:
                    waiters_count = 0
                
                print(f"[DEBUG] get_batch_ctx 准备获取 semaphore, task_id={task_id}, waiters={waiters_count}")
                if waiters_count > 0:
                    add_log(f"批量翻译等待槽位... (队列中有 {waiters_count} 个任务)", "INFO")
                
                async with translation_semaphore:
                    print(f"[DEBUG] get_batch_ctx 获得 semaphore! task_id={task_id}")
                    if task_id:
                        update_task_status(task_id, "running")
                    add_log("批量翻译获得槽位，开始执行", "INFO")
                    
                    # 使用翻译线程池执行，复用全局翻译器
                    from manga_translator.server.core.task_manager import (
                        run_in_translator_thread,
                    )
                    cancel_callback = (lambda: is_task_cancelled(task_id)) if task_id else None
                    contexts = await run_in_translator_thread(
                        _run_translate_batch_sync, images_with_configs, batch_size, task_id, cancel_callback
                    )
            else:
                # 没有 semaphore 时直接执行
                from manga_translator.server.core.task_manager import (
                    run_in_translator_thread,
                )
                cancel_callback = (lambda: is_task_cancelled(task_id)) if task_id else None
                contexts = await run_in_translator_thread(
                    _run_translate_batch_sync, images_with_configs, batch_size, task_id, cancel_callback
                )
            
            # 翻译后检查取消状态
            if task_id and is_task_cancelled(task_id):
                raise Exception("任务已被取消")
            
            # 为每个 context 添加工作流程结果
            for ctx in contexts:
                if ctx:
                    result = {
                        'success': ctx.success if hasattr(ctx, 'success') else (ctx.result is not None),
                        'workflow': workflow
                    }
                    if ctx.result:
                        result['has_image'] = True
                    if hasattr(ctx, 'text_regions') and ctx.text_regions:
                        result['text_regions'] = []
                        for region in ctx.text_regions:
                            region_data = {
                                'text': region.text if hasattr(region, 'text') else '',
                                'translation': region.translation if hasattr(region, 'translation') else '',
                            }
                            result['text_regions'].append(region_data)
                    ctx._workflow_result = result
        
        # 在返回前复制 result 图片，避免被 finally 清理影响
        for ctx in contexts:
            if ctx and hasattr(ctx, 'result') and ctx.result is not None:
                try:
                    ctx.result = ctx.result.copy()
                except Exception:
                    pass  # 如果复制失败，保留原引用
        
        return contexts
    
    finally:
        # 清理资源
        try:
            # 清理 PIL 图片（原始输入图片）
            for pil_img in pil_images:
                try:
                    pil_img.close()
                except Exception:
                    pass
            
            # 注意：contexts的清理和内存回收已在_run_translate_batch_sync的cleanup_after_request中处理
            # 不要调用translator.unload_models()，因为我们使用全局翻译器，模型应该被保留复用
            
        except Exception as cleanup_error:
            logger.warning(f"批量翻译资源清理失败: {cleanup_error}")


async def save_translation_to_history(ctx, username: str, task_id: str, workflow: str, original_filename: str = None, config = None) -> None:
    """
    保存翻译结果到历史记录
    
    Args:
        ctx: 翻译上下文，包含结果图片
        username: 用户名
        task_id: 任务ID
        workflow: 工作流程类型
        original_filename: 原始文件名（可选）
        config: 配置对象（可选，用于获取输出格式）
    """
    import shutil
    import tempfile
    from datetime import datetime, timezone

    from manga_translator.server.core.logging_manager import add_log
    
    add_log(f"Saving translation to history for user: {username}, task: {task_id[:8]}", "DEBUG")
    
    # 检查ctx是否有效
    if not ctx:
        add_log("Cannot save history: ctx is None", "WARNING")
        return
    
    if not hasattr(ctx, 'result') or ctx.result is None:
        add_log("Cannot save history: ctx.result is None", "WARNING")
        return
    
    try:
        # 获取历史服务
        from manga_translator.server.routes.history import get_history_service
        history_service = get_history_service()
        add_log("History service obtained successfully", "DEBUG")
    except Exception as e:
        add_log(f"History service not available: {e}", "WARNING")
        return
    
    # 保存结果图片到临时文件
    temp_dir = None
    temp_files = []
    try:
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        
        # 获取配置中的输出格式
        output_format = None
        if config and hasattr(config, 'cli') and hasattr(config.cli, 'format'):
            fmt = config.cli.format
            if fmt and fmt != '不指定':
                output_format = fmt.lower()
        
        # 安全过滤文件名，防止路径遍历攻击
        def sanitize_filename(filename: str) -> str:
            if not filename:
                return None
            # 只保留文件名部分，去掉路径
            filename = os.path.basename(filename)
            # 移除危险字符
            dangerous_chars = ['..', '/', '\\', '\x00', '<', '>', ':', '"', '|', '?', '*']
            for char in dangerous_chars:
                filename = filename.replace(char, '_')
            # 限制长度
            if len(filename) > 200:
                base, ext = os.path.splitext(filename)
                filename = base[:200-len(ext)] + ext
            return filename if filename else None
        
        # 确定文件名和保存格式
        safe_filename = sanitize_filename(original_filename) if original_filename else None
        
        if safe_filename:
            base_name = os.path.splitext(safe_filename)[0]
            save_format, ext = resolve_output_image_format(
                output_format,
                original_path=safe_filename,
            )
            result_filename = f"{base_name}{ext}"
        else:
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            save_format, ext = resolve_output_image_format(output_format)
            result_filename = f"translated_{timestamp}{ext}"
        
        result_path = os.path.join(temp_dir, result_filename)
        
        # 保存 PIL Image
        if hasattr(ctx.result, 'save'):
            # 复制图片以避免 "Operation on closed image" 错误
            try:
                img_to_save = ctx.result.copy()
            except Exception:
                # 如果复制失败，尝试直接使用原图
                img_to_save = ctx.result
            
            if save_format in RGB_PIL_FORMATS and img_to_save.mode not in ('RGB', 'L'):
                img_to_save = normalize_rgb_image(img_to_save)
                add_log(f"Converted {ctx.result.mode} to RGB for {save_format} format", "DEBUG")
            
            img_to_save.save(result_path, save_format)
            temp_files.append(result_path)
            add_log(f"Saved result image to temp: {result_path} (format: {save_format})", "DEBUG")
        else:
            add_log(f"ctx.result does not have save method, type: {type(ctx.result)}", "WARNING")
            return
        
        if not temp_files:
            add_log("No temp files to save", "WARNING")
            return
        
        # 构建元数据
        metadata = {
            'workflow': workflow,
            'task_id': task_id,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # 添加文本区域信息
        if hasattr(ctx, 'text_regions') and ctx.text_regions:
            text_data = []
            for region in ctx.text_regions:
                text_data.append({
                    'original': region.text if hasattr(region, 'text') else '',
                    'translated': region.translation if hasattr(region, 'translation') else ''
                })
            metadata['text_regions'] = text_data
            add_log(f"Added {len(text_data)} text regions to metadata", "DEBUG")
        
        # 保存到历史
        _result = history_service.save_translation_result(
            user_id=username,
            session_token=task_id,
            files=temp_files,
            metadata=metadata
        )
        add_log(f"Translation saved to history successfully, session: {task_id[:8]}", "INFO")
        
    except Exception as e:
        add_log(f"Failed to save translation to history ({type(e).__name__})", "ERROR")
    finally:
        # 清理临时目录
        try:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
