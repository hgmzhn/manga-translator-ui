import asyncio
import builtins
import io
import os
import re
import json
from base64 import b64decode
from typing import Union, Optional

import requests
from PIL import Image
from fastapi import Request, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from manga_translator import Config, MangaTranslator
from manga_translator.server.myqueue import task_queue, wait_in_queue, QueueElement, BatchQueueElement
from manga_translator.server.streaming import notify, stream
from manga_translator.utils import BASE_PATH

class TranslateRequest(BaseModel):
    """This request can be a multipart or a json request"""
    image: bytes|str
    """can be a url, base64 encoded image or a multipart image"""
    config: Config = Config()
    """in case it is a multipart this needs to be a string(json.stringify)"""

class BatchTranslateRequest(BaseModel):
    """Batch translation request"""
    images: list[bytes|str]
    """List of images, can be URLs, base64 encoded strings, or binary data"""
    config: Config = Config()
    """Translation configuration"""
    batch_size: int = 4
    """Batch size, default is 4"""

async def to_pil_image(image: Union[str, bytes, Image.Image]) -> Image.Image:
    try:
        # 如果已经是 PIL Image 对象，直接返回（保留 name 属性）
        if isinstance(image, Image.Image):
            return image
        elif isinstance(image, builtins.bytes):
            image = Image.open(io.BytesIO(image))
            return image
        else:
            if re.match(r'^data:image/.+;base64,', image):
                value = image.split(',', 1)[1]
                image_data = b64decode(value)
                image = Image.open(io.BytesIO(image_data))
                return image
            else:
                response = requests.get(image)
                image = Image.open(io.BytesIO(response.content))
                return image
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


def prepare_translator_params(config: Config, workflow: str = "normal") -> dict:
    """
    准备翻译器参数（根据工作流程）
    
    注意：
    1. API 端点完全由 workflow 参数控制工作流程
    2. 忽略 config.cli 中的以下设置（这些由服务器启动参数或端点控制）：
       - 工作流程：load_text, template, generate_and_export, upscale_only, colorize_only
       - GPU 设置：use_gpu, use_gpu_limited（由服务器启动参数 --use-gpu 控制）
       - 重试次数：retry_attempts（使用默认值）
    3. 这些 cli 设置仅用于命令行模式
    """
    translator_params = {}
    
    # 清除 config 中的 cli 设置，确保完全由服务器启动参数和端点控制
    if hasattr(config, 'cli'):
        # 重置所有工作流程相关的 cli 设置
        if hasattr(config.cli, 'load_text'):
            config.cli.load_text = False
        if hasattr(config.cli, 'template'):
            config.cli.template = False
        if hasattr(config.cli, 'generate_and_export'):
            config.cli.generate_and_export = False
        if hasattr(config.cli, 'upscale_only'):
            config.cli.upscale_only = False
        if hasattr(config.cli, 'colorize_only'):
            config.cli.colorize_only = False
        
        # 忽略 GPU 设置（由服务器启动参数控制）
        if hasattr(config.cli, 'use_gpu'):
            config.cli.use_gpu = False
        if hasattr(config.cli, 'use_gpu_limited'):
            config.cli.use_gpu_limited = False
        
        # 忽略重试次数（使用默认值或服务器配置）
        if hasattr(config.cli, 'retry_attempts'):
            config.cli.retry_attempts = None
    
    # 处理字体路径
    if hasattr(config, 'render') and hasattr(config.render, 'font_path'):
        font_filename = config.render.font_path
        if font_filename and not os.path.isabs(font_filename):
            font_full_path = os.path.join(BASE_PATH, 'fonts', font_filename)
            if os.path.exists(font_full_path):
                translator_params['font_path'] = font_full_path
    
    # 根据工作流程设置参数（完全由端点控制）
    if workflow == "export_original":
        # 导出原文：只检测和 OCR，不翻译
        # 会生成 JSON 文件，包含原文
        translator_params['template'] = True
        translator_params['save_text'] = True
    
    elif workflow == "save_json":
        # 保存 JSON：正常翻译 + 保存 JSON（跳过渲染）
        translator_params['save_text'] = True
        translator_params['generate_and_export'] = True  # 跳过渲染，只翻译和导出
    
    elif workflow == "load_text":
        # 导入翻译并渲染：从 JSON 文件加载翻译
        translator_params['load_text'] = True
    
    elif workflow == "upscale_only":
        # 仅超分
        translator_params['upscale_only'] = True
    
    elif workflow == "colorize_only":
        # 仅上色
        translator_params['colorize_only'] = True
    
    # normal 模式不需要额外参数
    
    return translator_params


async def get_ctx(req: Request, config: Config, image: str|bytes, workflow: str = "normal"):
    """
    翻译单张图片（使用 UI 层逻辑）
    
    支持的工作流程：
    - normal: 正常翻译
    - export_original: 导出原文（生成 JSON + TXT，包含原文）
    - save_json: 保存 JSON（正常翻译 + 保存 JSON + TXT）
    - load_text: 导入翻译并渲染（从 JSON 加载）
    - upscale_only: 仅超分
    - colorize_only: 仅上色
    """
    image = await to_pil_image(image)

    # 准备翻译器参数
    translator_params = prepare_translator_params(config, workflow)
    
    # 添加服务器配置（GPU 设置等）
    from manga_translator.server.main import server_config
    translator_params['use_gpu'] = server_config.get('use_gpu', False)
    translator_params['use_gpu_limited'] = server_config.get('use_gpu_limited', False)
    translator_params['verbose'] = server_config.get('verbose', False)
    translator_params['models_ttl'] = server_config.get('models_ttl', 0)
    # 如果命令行指定了 retry_attempts，则使用它（忽略 API 传入的配置）
    retry_attempts = server_config.get('retry_attempts', None)
    if retry_attempts is not None:
        translator_params['attempts'] = retry_attempts
    
    # 创建翻译器
    translator = MangaTranslator(params=translator_params)
    
    # 翻译
    ctx = await translator.translate(image, config)
    

    
    # 根据工作流程返回不同的结果
    result = {
        'success': ctx.success if hasattr(ctx, 'success') else (ctx.result is not None),
        'workflow': workflow
    }
    
    # 添加图片结果
    if ctx.result:
        result['has_image'] = True
    
    # 添加文本结果（如果有）
    if hasattr(ctx, 'text_regions') and ctx.text_regions:
        result['text_regions'] = []
        for region in ctx.text_regions:
            region_data = {
                'text': region.text if hasattr(region, 'text') else '',
                'translation': region.translation if hasattr(region, 'translation') else '',
            }
            result['text_regions'].append(region_data)
    
    # 注意：主翻译程序 (MangaTranslator) 在 export_original 和 save_json 模式下
    # 会自动生成 TXT 文件，所以这里不需要再次调用导出函数
    
    # 保存 ctx 以便后续使用
    ctx._workflow_result = result
    
    return ctx


async def while_streaming(req: Request, transform, config: Config, image: bytes | str, workflow: str = "normal"):
    """
    流式翻译（简化版：直接翻译，不使用任务队列）
    """
    async def generate():
        try:
            # 发送进度：开始翻译
            print("[STREAMING] 开始翻译流程")
            yield pack_message(1, json.dumps({"stage": "start", "message": "开始翻译..."}, ensure_ascii=False).encode('utf-8'))
            
            # 执行翻译（与 get_ctx 相同的逻辑）
            print("[STREAMING] 转换图片格式")
            yield pack_message(1, json.dumps({"stage": "image_loading", "message": "加载图片..."}, ensure_ascii=False).encode('utf-8'))
            pil_image = await to_pil_image(image)
            
            # 准备翻译器参数
            print("[STREAMING] 准备翻译器参数")
            translator_params = prepare_translator_params(config, workflow)
            
            # 添加服务器配置（GPU 设置等）
            from manga_translator.server.main import server_config
            translator_params['use_gpu'] = server_config.get('use_gpu', False)
            translator_params['use_gpu_limited'] = server_config.get('use_gpu_limited', False)
            translator_params['verbose'] = server_config.get('verbose', False)
            translator_params['models_ttl'] = server_config.get('models_ttl', 0)
            # 如果命令行指定了 retry_attempts，则使用它（忽略 API 传入的配置）
            retry_attempts = server_config.get('retry_attempts', None)
            if retry_attempts is not None:
                translator_params['attempts'] = retry_attempts
            
            # 调试：打印配置
            print(f"[STREAMING] server_config={server_config}")
            print(f"[STREAMING] translator_params: use_gpu={translator_params.get('use_gpu')}, workflow={workflow}")
            
            # 创建翻译器
            print("[STREAMING] 创建翻译器实例")
            yield pack_message(1, json.dumps({"stage": "translator_init", "message": "初始化翻译器..."}, ensure_ascii=False).encode('utf-8'))
            translator = MangaTranslator(params=translator_params)
            
            # 发送进度：翻译中
            print("[STREAMING] 开始翻译")
            yield pack_message(1, json.dumps({"stage": "translating", "message": "正在翻译..."}, ensure_ascii=False).encode('utf-8'))
            
            # 翻译
            try:
                print("[STREAMING] 调用 translator.translate()")
                ctx = await translator.translate(pil_image, config)
                print(f"[STREAMING] 翻译完成，ctx.result={ctx.result is not None if hasattr(ctx, 'result') else 'no result attr'}")
                
                # 添加 workflow_result（与 get_ctx 保持一致）
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
                
                yield pack_message(1, json.dumps({"stage": "translate_done", "message": "翻译完成，处理结果..."}, ensure_ascii=False).encode('utf-8'))
            except Exception as translate_error:
                error_msg = f"翻译过程失败: {str(translate_error)}"
                print(f"[STREAMING ERROR] {error_msg}")
                import traceback
                traceback.print_exc()
                yield pack_message(2, json.dumps({"error": error_msg, "stage": "translate"}, ensure_ascii=False).encode('utf-8'))
                return
            
            # 调试：检查 ctx
            has_result = ctx.result is not None if hasattr(ctx, 'result') else False
            has_text_regions = hasattr(ctx, 'text_regions') and ctx.text_regions
            text_region_count = len(ctx.text_regions) if has_text_regions else 0
            
            print(f"[STREAMING] ctx.result={has_result}")
            print(f"[STREAMING] ctx.text_regions count={text_region_count}")
            
            if has_text_regions:
                yield pack_message(1, json.dumps({
                    "stage": "processing", 
                    "message": f"检测到 {text_region_count} 个文本区域"
                }, ensure_ascii=False).encode('utf-8'))
            
            # 检查是否有结果
            if not has_result:
                error_msg = "翻译失败：未生成结果图片（可能未检测到文本或翻译过程出错）"
                print(f"[STREAMING ERROR] {error_msg}")
                yield pack_message(2, json.dumps({"error": error_msg, "stage": "no_result"}, ensure_ascii=False).encode('utf-8'))
                return
            
            # 转换结果并发送
            try:
                print("[STREAMING] 转换结果")
                yield pack_message(1, json.dumps({"stage": "transforming", "message": "转换结果格式..."}, ensure_ascii=False).encode('utf-8'))
                result_data = transform(ctx)
                print(f"[STREAMING] 结果大小: {len(result_data)} bytes")
                
                yield pack_message(1, json.dumps({"stage": "sending", "message": "发送结果..."}, ensure_ascii=False).encode('utf-8'))
                yield pack_message(0, result_data)
                
                print("[STREAMING] 完成！")
                yield pack_message(1, json.dumps({"stage": "complete", "message": "完成！"}, ensure_ascii=False).encode('utf-8'))
            except Exception as transform_error:
                error_msg = f"转换结果失败: {type(transform_error).__name__}: {str(transform_error)}"
                print(f"[STREAMING ERROR] {error_msg}")
                import traceback
                traceback.print_exc()
                yield pack_message(2, json.dumps({"error": error_msg, "stage": "transform"}, ensure_ascii=False).encode('utf-8'))
                return  # 确保不继续执行
            
        except Exception as e:
            # 发送错误
            error_msg = f"翻译失败: {type(e).__name__}: {str(e)}"
            print(f"[STREAMING ERROR] {error_msg}")
            import traceback
            traceback.print_exc()
            try:
                yield pack_message(2, json.dumps({"error": error_msg, "stage": "unknown"}, ensure_ascii=False).encode('utf-8'))
            except:
                # 如果连错误消息都发送失败，至少不要打印二进制数据
                pass
    
    return StreamingResponse(generate(), media_type="application/octet-stream")


def pack_message(status: int, data: bytes) -> bytes:
    """打包流式消息：1字节状态 + 4字节大小 + 数据"""
    return status.to_bytes(1, 'big') + len(data).to_bytes(4, 'big') + data


async def get_batch_ctx(req: Request, config: Config, images: list[str|bytes], batch_size: int = 4, workflow: str = "normal"):
    """
    批量翻译（使用 UI 层逻辑）
    """
    # Convert images to PIL Image objects
    pil_images = []
    for img in images:
        pil_img = await to_pil_image(img)
        pil_images.append(pil_img)
    
    # 准备翻译器参数
    translator_params = prepare_translator_params(config, workflow)
    
    # 添加服务器配置（GPU 设置等）
    from manga_translator.server.main import server_config
    translator_params['use_gpu'] = server_config.get('use_gpu', False)
    translator_params['use_gpu_limited'] = server_config.get('use_gpu_limited', False)
    translator_params['verbose'] = server_config.get('verbose', False)
    translator_params['models_ttl'] = server_config.get('models_ttl', 0)
    # 如果命令行指定了 retry_attempts，则使用它（忽略 API 传入的配置）
    retry_attempts = server_config.get('retry_attempts', None)
    if retry_attempts is not None:
        translator_params['attempts'] = retry_attempts
    
    # 创建翻译器
    translator = MangaTranslator(params=translator_params)
    
    # 准备批量数据
    images_with_configs = [(img, config) for img in pil_images]
    
    # 批量翻译
    contexts = await translator.translate_batch(images_with_configs, batch_size)
    
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
    
    return contexts
