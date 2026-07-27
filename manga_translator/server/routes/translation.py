"""
Translation routes module.

This module contains all /translate/* endpoints for the manga translator server.
"""

import io
import os
import secrets
import tempfile
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from manga_translator.image_formats import (
    RGB_PIL_FORMATS,
    resolve_output_image_format,
)
from manga_translator.server.core.config_manager import admin_settings, parse_config
from manga_translator.server.core.logging_manager import add_log
from manga_translator.server.core.response_utils import (
    apply_user_env_vars,
    transform_to_bytes,
    transform_to_image,
    transform_to_json,
)
from manga_translator.server.request_extraction import (
    BatchTranslateRequest,
    TranslateRequest,
    get_batch_ctx,
    get_ctx,
    while_streaming,
)
from manga_translator.server.routes.translation_auth import (
    log_translation_task_created,
    track_task_end,
    track_task_start,
    verify_translation_auth,
)
from manga_translator.server.to_json import TranslationResponse, to_translation
from manga_translator.utils import open_pil_image
from manga_translator.utils.image_modes import normalize_rgb_image

router = APIRouter(prefix="/translate", tags=["translation"])


def _get_translator_name(config) -> str:
    if hasattr(config, 'translator') and hasattr(config.translator, 'translator'):
        return config.translator.translator
    return "unknown"


async def _read_form_image_and_config(image: UploadFile, config: str):
    return await image.read(), parse_config(config)


def _translation_bytes_response(ctx) -> StreamingResponse:
    return StreamingResponse(content=to_translation(ctx).to_bytes())


def _png_response_from_ctx(ctx, error_detail: str) -> StreamingResponse:
    if not ctx.result:
        raise HTTPException(500, detail=error_detail)

    img_byte_arr = io.BytesIO()
    ctx.result.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)
    return StreamingResponse(img_byte_arr, media_type="image/png")


async def _authorize_translation(req: Request, config, *, set_username: bool = False):
    username, ip_address = await verify_translation_auth(req, config)
    if set_username:
        config._username = username
    return username, ip_address


async def _authorize_translation_with_env(
    req: Request,
    config,
    user_env_vars: str,
    *,
    set_username: bool = False,
):
    username, ip_address = await _authorize_translation(req, config, set_username=set_username)
    env_vars = await apply_user_env_vars(user_env_vars, config, admin_settings, username)
    config._user_env_vars = env_vars
    return username, ip_address


async def _run_tracked_translation(
    req: Request,
    config,
    image_data,
    workflow: str,
    build_response,
    *,
    set_username: bool = False,
):
    username, ip_address = await _authorize_translation(req, config, set_username=set_username)
    track_task_start(username)

    try:
        log_translation_task_created(
            username,
            ip_address,
            _get_translator_name(config),
            config,
        )
        ctx = await get_ctx(req, config, image_data, workflow)
        return build_response(ctx)
    finally:
        track_task_end(username)


async def _stream_translation(
    req: Request,
    transform,
    config,
    image_data,
    workflow: str,
    *,
    filename: str = None,
    log_task: bool = True,
    username: str = None,
    ip_address: str = None,
):
    if username is None or ip_address is None:
        username, ip_address = await _authorize_translation(req, config, set_username=True)
    else:
        config._username = username

    if log_task:
        log_translation_task_created(
            username,
            ip_address,
            _get_translator_name(config),
            config,
        )
    return await while_streaming(req, transform, config, image_data, workflow, filename)


# ============================================================================
# Basic Translation Endpoints (JSON format)
# ============================================================================

@router.post("/json", response_model=TranslationResponse, tags=["api", "json"],
             response_description="json structure inspired by the ichigo translator extension")
async def translate_json(req: Request, data: TranslateRequest):
    """Translate image and return JSON format result"""
    return await _run_tracked_translation(
        req,
        data.config,
        data.image,
        "save_json",
        to_translation,
    )


@router.post("/bytes", response_class=StreamingResponse, tags=["api", "json"],
             response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
async def bytes(req: Request, data: TranslateRequest):
    """Translate image and return custom byte format result"""
    return await _run_tracked_translation(
        req,
        data.config,
        data.image,
        "save_json",
        _translation_bytes_response,
    )


@router.post("/image", response_description="the result image", tags=["api", "json"],
             response_class=StreamingResponse)
async def image(req: Request, data: TranslateRequest) -> StreamingResponse:
    """Translate image and return result image"""
    return await _run_tracked_translation(
        req,
        data.config,
        data.image,
        "normal",
        lambda ctx: _png_response_from_ctx(
            ctx,
            "Translation failed: no result image generated",
        ),
    )


# ============================================================================
# Streaming Translation Endpoints
# ============================================================================

@router.post("/json/stream", response_class=StreamingResponse, tags=["api", "json"],
             response_description="A stream over elements with structure(1byte status, 4 byte size, n byte data)")
async def stream_json(req: Request, data: TranslateRequest) -> StreamingResponse:
    """Translate image and stream JSON format result with progress"""
    return await _stream_translation(req, transform_to_json, data.config, data.image, "save_json")


@router.post("/bytes/stream", response_class=StreamingResponse, tags=["api", "json"],
             response_description="A stream over elements with structure(1byte status, 4 byte size, n byte data)")
async def stream_bytes(req: Request, data: TranslateRequest) -> StreamingResponse:
    """Translate image and stream byte format result with progress"""
    return await _stream_translation(req, transform_to_bytes, data.config, data.image, "save_json")


@router.post("/image/stream", response_class=StreamingResponse, tags=["api", "json"],
             response_description="A stream over elements with structure(1byte status, 4 byte size, n byte data)")
async def stream_image(req: Request, data: TranslateRequest) -> StreamingResponse:
    """Translate image and stream result image with progress"""
    return await _stream_translation(req, transform_to_image, data.config, data.image, "normal")


# ============================================================================
# Form-based Translation Endpoints
# ============================================================================

@router.post("/with-form/json", response_model=TranslationResponse, tags=["api", "form"],
             response_description="json structure inspired by the ichigo translator extension")
async def translate_json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """Translate image (form upload) and return JSON format result"""
    img, conf = await _read_form_image_and_config(image, config)
    return await _run_tracked_translation(
        req,
        conf,
        img,
        "save_json",
        to_translation,
        set_username=True,
    )


@router.post("/with-form/bytes", response_class=StreamingResponse, tags=["api", "form"],
             response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
async def bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """Translate image (form upload) and return custom byte format result"""
    img, conf = await _read_form_image_and_config(image, config)
    return await _run_tracked_translation(
        req,
        conf,
        img,
        "save_json",
        _translation_bytes_response,
        set_username=True,
    )


@router.post("/with-form/image", response_description="the result image", tags=["api", "form"],
             response_class=StreamingResponse)
async def image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    """Translate image (form upload) and return result image"""
    img, conf = await _read_form_image_and_config(image, config)
    return await _run_tracked_translation(
        req,
        conf,
        img,
        "normal",
        lambda ctx: _png_response_from_ctx(
            ctx,
            "Translation failed: no result image generated",
        ),
        set_username=True,
    )


# ============================================================================
# Form-based Streaming Translation Endpoints
# ============================================================================

@router.post("/with-form/json/stream", response_class=StreamingResponse, tags=["api", "form"],
             response_description="A stream over elements with structure(1byte status, 4 byte size, n byte data)")
async def stream_json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    """Translate image (form upload) and stream JSON format result with progress"""
    img, conf = await _read_form_image_and_config(image, config)
    # Mark this as Web frontend call for placeholder optimization
    conf._is_web_frontend = True
    return await _stream_translation(req, transform_to_json, conf, img, "save_json")


@router.post("/with-form/bytes/stream", response_class=StreamingResponse, tags=["api", "form"],
             response_description="A stream over elements with structure(1byte status, 4 byte size, n byte data)")
async def stream_bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    """Translate image (form upload) and stream byte format result with progress"""
    img, conf = await _read_form_image_and_config(image, config)
    return await _stream_translation(
        req,
        transform_to_bytes,
        conf,
        img,
        "save_json",
        log_task=False,
    )


@router.post("/with-form/image/stream", response_class=StreamingResponse, tags=["api", "form"],
             response_description="Standard streaming endpoint - returns complete image data. Suitable for API calls and scripts.")
async def stream_image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}"), 
                           user_env_vars: str = Form("{}")) -> StreamingResponse:
    """Generic streaming endpoint: returns complete image data, suitable for API calls and comicread scripts"""
    img, conf = await _read_form_image_and_config(image, config)
    username, ip_address = await _authorize_translation_with_env(
        req,
        conf,
        user_env_vars,
        set_username=True,
    )
    # Mark as generic mode, no placeholder optimization
    conf._web_frontend_optimized = False
    return await _stream_translation(
        req,
        transform_to_image,
        conf,
        img,
        "normal",
        filename=image.filename,
        username=username,
        ip_address=ip_address,
    )


@router.post("/with-form/image/stream/web", response_class=StreamingResponse, tags=["api", "form"],
             response_description="Web frontend optimized streaming endpoint - uses placeholder optimization for faster response.")
async def stream_image_form_web(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                                user_env_vars: str = Form("{}")) -> StreamingResponse:
    """Web frontend specific endpoint: uses placeholder optimization for ultra-fast experience"""
    img, conf = await _read_form_image_and_config(image, config)
    username, ip_address = await _authorize_translation_with_env(
        req,
        conf,
        user_env_vars,
        set_username=True,
    )
    # Mark as Web frontend optimized mode, use placeholder optimization
    conf._web_frontend_optimized = True
    return await _stream_translation(
        req,
        transform_to_image,
        conf,
        img,
        "normal",
        filename=image.filename,
        username=username,
        ip_address=ip_address,
    )


# ============================================================================
# Batch Translation Endpoints
# ============================================================================

@router.post("/batch/json", response_model=list[TranslationResponse], tags=["api", "json", "batch"])
async def translate_batch_json(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return JSON format results"""
    import asyncio

    from manga_translator.server.core.logging_manager import generate_task_id
    from manga_translator.server.core.task_manager import (
        register_active_task,
        unregister_active_task,
    )
    
    task_id = generate_task_id()
    
    # 获取当前 asyncio Task 用于强制取消
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    
    # Convert config to Config object if needed using parse_config for consistency
    if isinstance(data.config, dict):
        import json

        from manga_translator.server.core.config_manager import parse_config
        config = parse_config(json.dumps(data.config))
    else:
        config = data.config
    
    # Verify authentication and permissions
    username, ip_address = await verify_translation_auth(req, config)
    
    # Extract translator name
    translator = "unknown"
    if hasattr(config, 'translator') and hasattr(config.translator, 'translator'):
        translator = config.translator.translator
    
    # Register active task for admin panel visibility
    register_active_task(task_id, current_task, username, translator)
    
    # Track task start
    track_task_start(username)
    
    try:
        # Log task creation
        log_translation_task_created(username, ip_address, translator, config, f"batch_{len(data.images)}")
        
        # 获取用户预设的 API Keys
        env_vars = await apply_user_env_vars("{}", config, admin_settings, username)
        config._user_env_vars = env_vars
        
        # Process batch translation (pass task_id for cancel checking)
        results = await get_batch_ctx(req, config, data.images, data.batch_size, "normal", task_id)
        
        # Save each result to history
        from manga_translator.server.request_extraction import (
            save_translation_to_history,
        )
        filenames = data.filenames if data.filenames else []
        for i, ctx in enumerate(results):
            if ctx and ctx.result:
                original_name = filenames[i] if i < len(filenames) else f"batch_{i+1}.png"
                try:
                    await save_translation_to_history(ctx, username, f"{task_id}_{i}", "normal", original_name, config)
                except Exception as e:
                    add_log(f"保存历史失败 (图片 {i+1}): {e}", "WARNING")
        
        return [to_translation(ctx) for ctx in results]
    except asyncio.CancelledError:
        add_log("批量翻译(JSON)被强制取消", "WARNING")
        raise HTTPException(499, detail="任务已被强制取消")
    except Exception as e:
        error_msg = str(e)
        if "已被取消" in error_msg or "cancelled" in error_msg.lower():
            add_log("批量翻译(JSON)已取消", "WARNING")
            raise HTTPException(499, detail="任务已被取消")
        raise
    finally:
        # Track task end
        track_task_end(username)
        # Unregister active task
        unregister_active_task(task_id)


@router.post("/batch/images", response_description="Zip file containing translated images", tags=["api", "batch"])
async def batch_images(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return zip archive containing translated images"""
    import asyncio

    from manga_translator.server.core.logging_manager import generate_task_id
    from manga_translator.server.core.task_manager import (
        register_active_task,
        unregister_active_task,
    )
    
    # 验证请求数据
    if not data.images or len(data.images) == 0:
        add_log("批量翻译请求失败: 没有提供图片", "ERROR")
        raise HTTPException(400, detail="没有提供图片")
    
    task_id = generate_task_id()
    
    # 获取当前 asyncio Task 用于强制取消
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    
    try:
        add_log(f"批量翻译请求: {len(data.images)} 张图片, batch_size={data.batch_size}", "INFO")
        
        # If config is dict, convert to Config object using parse_config for consistency
        if isinstance(data.config, dict):
            import json

            from manga_translator.server.core.config_manager import parse_config
            config = parse_config(json.dumps(data.config))
        else:
            config = data.config
        
        # Verify authentication and permissions
        username, ip_address = await verify_translation_auth(req, config)
        
        # 检查用户是否有离线翻译权限
        from manga_translator.server.core.middleware import get_services
        _, _, permission_service = get_services()
        allow_offline = permission_service.check_offline_translation_permission(username)
        
        # 如果允许离线翻译，创建一个永不断开的 Request 包装器
        if allow_offline:
            class OfflineRequest:
                """支持离线翻译的 Request 包装器"""
                async def is_disconnected(self):
                    return False  # 永不断开
            req = OfflineRequest()
            add_log(f"用户 {username} 启用离线翻译模式", "INFO")
        
        # 获取用户预设的 API Keys
        env_vars = await apply_user_env_vars("{}", config, admin_settings, username)
        config._user_env_vars = env_vars
        
        # Extract translator name
        translator = "unknown"
        if hasattr(config, 'translator') and hasattr(config.translator, 'translator'):
            translator = config.translator.translator
        
        # Register active task for admin panel visibility (with asyncio Task for force cancel)
        register_active_task(task_id, current_task, username, translator)
        
        # Track task start
        track_task_start(username)
        
        try:
            # Log task creation
            log_translation_task_created(username, ip_address, translator, config, f"batch_{len(data.images)}")
            
            # Process batch translation (pass task_id for cancel checking)
            results = await get_batch_ctx(req, config, data.images, data.batch_size, "normal", task_id)
            add_log(f"批量翻译完成: 收到 {len(results)} 个结果", "INFO")
        finally:
            # Track task end
            track_task_end(username)
            # Unregister active task
            unregister_active_task(task_id)
            
    except asyncio.CancelledError:
        add_log("批量翻译被强制取消", "WARNING")
        raise HTTPException(499, detail="任务已被强制取消")
    except Exception as e:
        error_msg = str(e)
        if "已被取消" in error_msg or "cancelled" in error_msg.lower():
            add_log("批量翻译已取消", "WARNING")
            raise HTTPException(499, detail="任务已被取消")
        add_log(f"批量翻译失败: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, detail=f"Batch translation failed: {error_msg}")
    
    # 获取原始文件名列表
    filenames = data.filenames if data.filenames else []
    
    # 立即将结果图片转换为字节数据，避免图片被关闭后无法访问
    result_images = []
    for i, ctx in enumerate(results):
        if ctx and ctx.result:
            try:
                # 复制图片数据到内存
                result_images.append({
                    'index': i,
                    'image': ctx.result.copy(),
                    'mode': ctx.result.mode,
                    'size': ctx.result.size
                })
            except Exception as e:
                add_log(f"复制图片 {i+1} 失败: {e}", "WARNING")
    
    # 获取配置中的输出格式
    output_format = None
    if config and hasattr(config, 'cli') and hasattr(config.cli, 'format'):
        fmt = config.cli.format
        if fmt and fmt != '不指定':
            output_format = fmt.lower()
    
    # Create temporary ZIP file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_file:
        tmp_file_name = tmp_file.name
    
    # File handle is closed, now safe to write
    image_count = 0
    with zipfile.ZipFile(tmp_file_name, 'w') as zip_file:
        for img_data in result_images:
            i = img_data['index']
            img_to_save = img_data['image']
            
            # 获取原始文件名
            original_name = filenames[i] if i < len(filenames) else None
            
            save_format, ext = resolve_output_image_format(
                output_format,
                original_path=original_name,
            )
            
            # 生成输出文件名
            if original_name:
                base_name = os.path.splitext(os.path.basename(original_name))[0]
                output_name = f"{base_name}{ext}"
            else:
                output_name = f"translated_{i+1}{ext}"
            
            img_byte_arr = io.BytesIO()
            if save_format in RGB_PIL_FORMATS and img_to_save.mode not in ('RGB', 'L'):
                img_to_save = normalize_rgb_image(img_to_save)
            img_to_save.save(img_byte_arr, format=save_format)
            zip_file.writestr(output_name, img_byte_arr.getvalue())
            image_count += 1
    
    add_log(f"ZIP文件创建完成: 包含 {image_count} 张图片", "INFO")
    
    # 保存历史记录（使用已复制的图片数据）
    from manga_translator.server.request_extraction import save_translation_to_history
    for img_data in result_images:
        i = img_data['index']
        original_name = filenames[i] if i < len(filenames) else f"batch_{i+1}.png"
        try:
            # 创建一个临时 ctx 对象用于保存历史
            class TempCtx:
                pass
            temp_ctx = TempCtx()
            temp_ctx.result = img_data['image']
            temp_ctx.text_regions = results[i].text_regions if results[i] and hasattr(results[i], 'text_regions') else None
            
            await save_translation_to_history(
                temp_ctx, username, f"{task_id}_{i}", "normal", 
                original_name, config
            )
        except Exception as e:
            add_log(f"保存历史失败 (图片 {i+1}): {e}", "WARNING")
    
    # 读取 ZIP 文件内容
    with open(tmp_file_name, 'rb') as f:
        zip_data = f.read()
    
    # 清理临时文件
    try:
        os.unlink(tmp_file_name)
    except PermissionError:
        import atexit
        atexit.register(lambda: os.unlink(tmp_file_name) if os.path.exists(tmp_file_name) else None)
    
    # 显式清理复制的图片内存
    for img_data in result_images:
        try:
            if img_data.get('image'):
                img_data['image'].close()
        except Exception:
            pass
    result_images.clear()
    
    # 返回 ZIP 数据，不使用 Content-Disposition: attachment（避免 IDM 拦截）
    # 使用 inline 或不设置，让浏览器直接处理而不触发下载
    return StreamingResponse(
        io.BytesIO(zip_data),
        media_type="application/octet-stream",  # 使用通用二进制类型，避免 IDM 识别为 ZIP
        headers={
            "Content-Length": str(len(zip_data)),
            "X-Content-Type": "application/zip"  # 自定义 header 告诉前端这是 ZIP
        }
    )


@router.post("/queue-size", response_model=int, tags=["api", "json"])
async def queue_size() -> int:
    """Get current translation queue size"""
    from manga_translator.server.myqueue import task_queue
    return len(task_queue.queue)



# ============================================================================
# Export Endpoints (Original/Translated Text)
# ============================================================================

@router.post("/export/original", response_class=StreamingResponse, tags=["api", "export"])
async def export_original(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                          user_env_vars: str = Form("{}")):
    """Export original text (ZIP archive: JSON + TXT)"""
    import json
    
    img = await image.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    
    # Use specified workflow for processing
    ctx = await get_ctx(req, conf, img, "export_original")
    
    # Convert result to JSON format
    translation_data = to_translation(ctx)
    
    # Create temporary JSON file (using same format as main translation program)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp_json:
        # Convert Pydantic model to dict
        response_dict = translation_data.model_dump()
        
        # Build JSON structure same as main translation program
        json_data = {
            "temp_image": response_dict
        }
        json.dump(json_data, tmp_json, ensure_ascii=False, indent=4)
        tmp_json_path = tmp_json.name
    
    try:
        # Generate TXT file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp_txt:
            tmp_txt_path = tmp_txt.name
        
        # Import module directly, avoid triggering other imports in __init__.py
        import importlib.util
        workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 
                                            'desktop_qt_ui', 'services', 'workflow_service.py')
        workflow_service_path = os.path.abspath(workflow_service_path)
        
        spec = importlib.util.spec_from_file_location("workflow_service", workflow_service_path)
        workflow_service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_service)
        
        # Get default template
        template_path = workflow_service.ensure_default_template_exists()
        if not template_path:
            raise HTTPException(500, detail="无法创建或找到默认模板文件")
        
        ui_generate_original_text = workflow_service.generate_original_text
        txt_path = ui_generate_original_text(tmp_json_path, template_path=template_path, output_path=tmp_txt_path)
        
        if txt_path.startswith("Error"):
            raise HTTPException(500, detail=txt_path)
        
        # Create ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add JSON file
            with open(tmp_json_path, 'r', encoding='utf-8') as f:
                json_content = f.read()
            zip_file.writestr("translation.json", json_content)
            
            # Add formatted text file
            with open(txt_path, 'r', encoding='utf-8') as f:
                txt_content = f.read()
            output_format = workflow_service.get_translation_output_format(template_path)
            zip_file.writestr(f"original.{output_format}", txt_content)
        
        # Clean up temporary files
        os.unlink(tmp_json_path)
        os.unlink(txt_path)
        
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=original_export.zip"}
        )
    except Exception as e:
        # Clean up temporary files
        if os.path.exists(tmp_json_path):
            os.unlink(tmp_json_path)
        if 'txt_path' in locals() and os.path.exists(txt_path):
            os.unlink(txt_path)
        raise HTTPException(500, detail=f"Error exporting files: {str(e)}")


@router.post("/export/translated", response_class=StreamingResponse, tags=["api", "export"])
async def export_translated(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                            user_env_vars: str = Form("{}")):
    """Export translated text (ZIP archive: JSON + TXT)"""
    import json
    
    img = await image.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    
    # Use specified workflow for processing
    ctx = await get_ctx(req, conf, img, "save_json")
    
    # Convert result to JSON format
    translation_data = to_translation(ctx)
    
    # Create temporary JSON file (using same format as main translation program)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp_json:
        # Convert Pydantic model to dict
        response_dict = translation_data.model_dump()
        
        # Build JSON structure same as main translation program
        json_data = {
            "temp_image": response_dict
        }
        json.dump(json_data, tmp_json, ensure_ascii=False, indent=4)
        tmp_json_path = tmp_json.name
    
    try:
        # Generate TXT file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp_txt:
            tmp_txt_path = tmp_txt.name
        
        # Import module directly, avoid triggering other imports in __init__.py
        import importlib.util
        workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 
                                            'desktop_qt_ui', 'services', 'workflow_service.py')
        workflow_service_path = os.path.abspath(workflow_service_path)
        
        spec = importlib.util.spec_from_file_location("workflow_service", workflow_service_path)
        workflow_service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_service)
        
        # Get default template
        template_path = workflow_service.ensure_default_template_exists()
        if not template_path:
            raise HTTPException(500, detail="无法创建或找到默认模板文件")
        
        ui_generate_translated_text = workflow_service.generate_translated_text
        txt_path = ui_generate_translated_text(tmp_json_path, template_path=template_path, output_path=tmp_txt_path)
        
        if txt_path.startswith("Error"):
            raise HTTPException(500, detail=txt_path)
        
        # Create ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add JSON file
            with open(tmp_json_path, 'r', encoding='utf-8') as f:
                json_content = f.read()
            zip_file.writestr("translation.json", json_content)
            
            # Add formatted text file
            with open(txt_path, 'r', encoding='utf-8') as f:
                txt_content = f.read()
            output_format = workflow_service.get_translation_output_format(template_path)
            zip_file.writestr(f"translated.{output_format}", txt_content)
        
        # Clean up temporary files
        os.unlink(tmp_json_path)
        os.unlink(txt_path)
        
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=translated_export.zip"}
        )
    except Exception as e:
        # Clean up temporary files
        if os.path.exists(tmp_json_path):
            os.unlink(tmp_json_path)
        if 'txt_path' in locals() and os.path.exists(txt_path):
            os.unlink(txt_path)
        raise HTTPException(500, detail=f"Error exporting files: {str(e)}")


@router.post("/export/original/stream", response_class=StreamingResponse, tags=["api", "export", "stream"])
async def export_original_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                                 user_env_vars: str = Form("{}")):
    """Export original text (streaming, with progress)"""
    img = await image.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    conf._username = username
    
    # Process translation (并发控制在 while_streaming 内部处理)
    return await while_streaming(req, transform_to_json, conf, img, "export_original")


@router.post("/export/translated/stream", response_class=StreamingResponse, tags=["api", "export", "stream"])
async def export_translated_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                                   user_env_vars: str = Form("{}")):
    """Export translated text (streaming, with progress)"""
    img = await image.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    conf._username = username
    
    # Process translation (并发控制在 while_streaming 内部处理)
    return await while_streaming(req, transform_to_json, conf, img, "save_json")



# ============================================================================
# Workflow Endpoints (Upscale, Colorize, Inpaint)
# ============================================================================

@router.post("/upscale", response_class=StreamingResponse, tags=["api", "process"])
async def upscale_only(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                       user_env_vars: str = Form("{}")):
    """Upscale only (image super-resolution)"""
    img, conf = await _read_form_image_and_config(image, config)
    await _authorize_translation_with_env(req, conf, user_env_vars)
    ctx = await get_ctx(req, conf, img, "upscale_only")
    return _png_response_from_ctx(ctx, "Upscaling failed")


@router.post("/colorize", response_class=StreamingResponse, tags=["api", "process"])
async def colorize_only(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                        user_env_vars: str = Form("{}")):
    """Colorize only (black and white image colorization)"""
    img, conf = await _read_form_image_and_config(image, config)
    await _authorize_translation_with_env(req, conf, user_env_vars)
    ctx = await get_ctx(req, conf, img, "colorize_only")
    return _png_response_from_ctx(ctx, "Colorization failed")


@router.post("/inpaint", response_class=StreamingResponse, tags=["api", "process"])
async def inpaint_only(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                       user_env_vars: str = Form("{}")):
    """Inpaint only (detect text and inpaint image)"""
    img, conf = await _read_form_image_and_config(image, config)
    await _authorize_translation_with_env(req, conf, user_env_vars)
    ctx = await get_ctx(req, conf, img, "inpaint_only")
    return _png_response_from_ctx(ctx, "Inpainting failed")


@router.post("/upscale/stream", response_class=StreamingResponse, tags=["api", "process", "stream"])
async def upscale_only_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                              user_env_vars: str = Form("{}")):
    """Upscale only (streaming, with progress)"""
    img, conf = await _read_form_image_and_config(image, config)
    username, ip_address = await _authorize_translation_with_env(
        req,
        conf,
        user_env_vars,
        set_username=True,
    )
    return await _stream_translation(
        req,
        transform_to_image,
        conf,
        img,
        "upscale_only",
        filename=image.filename,
        log_task=False,
        username=username,
        ip_address=ip_address,
    )


@router.post("/colorize/stream", response_class=StreamingResponse, tags=["api", "process", "stream"])
async def colorize_only_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                               user_env_vars: str = Form("{}")):
    """Colorize only (streaming, with progress)"""
    img, conf = await _read_form_image_and_config(image, config)
    username, ip_address = await _authorize_translation_with_env(
        req,
        conf,
        user_env_vars,
        set_username=True,
    )
    return await _stream_translation(
        req,
        transform_to_image,
        conf,
        img,
        "colorize_only",
        filename=image.filename,
        log_task=False,
        username=username,
        ip_address=ip_address,
    )


@router.post("/inpaint/stream", response_class=StreamingResponse, tags=["api", "process", "stream"])
async def inpaint_only_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                              user_env_vars: str = Form("{}")):
    """Inpaint only (streaming, with progress)"""
    img, conf = await _read_form_image_and_config(image, config)
    username, ip_address = await _authorize_translation_with_env(
        req,
        conf,
        user_env_vars,
        set_username=True,
    )
    return await _stream_translation(
        req,
        transform_to_image,
        conf,
        img,
        "inpaint_only",
        filename=image.filename,
        log_task=False,
        username=username,
        ip_address=ip_address,
    )



# ============================================================================
# Import Endpoints (JSON/TXT + Image Rendering)
# ============================================================================

@router.post("/import/json", response_class=StreamingResponse, tags=["api", "import"])
async def import_json_and_render(req: Request, image: UploadFile = File(...), json_file: UploadFile = File(...), 
                                config: str = Form("{}"), user_env_vars: str = Form("{}")):
    """Import JSON + image, return rendered image (load_text workflow)"""
#     import json
    from manga_translator.utils.path_manager import get_work_dir
    
    img_bytes = await image.read()
    json_content = await json_file.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    
    # Use temporary filename
    temp_name = f"temp_{secrets.token_hex(8)}"
    # Create temporary image path (in current directory or result directory)
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # Save JSON to temporary file
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    
    try:
        # Write JSON file
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # Save image to temporary location (using same name)
        temp_image = open_pil_image(io.BytesIO(img_bytes), eager=False)
        temp_image.save(temp_image_path)
        
        # Reload image and set name attribute
        temp_image = open_pil_image(temp_image_path, eager=False)
        temp_image.name = temp_image_path
        
        # Use load_text workflow, call through get_ctx (supports task queue)
        ctx = await get_ctx(req, conf, temp_image, "load_text")
        
        if ctx.result:
            img_byte_arr = io.BytesIO()
            ctx.result.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            
            # Clean up temporary files
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            
            return StreamingResponse(img_byte_arr, media_type="image/png")
        else:
            # Clean up temporary files
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            raise HTTPException(500, detail="Failed to render image")
    
    except Exception as e:
        # Clean up temporary files
        if os.path.exists(json_path):
            os.unlink(json_path)
        if 'temp_image_path' in locals() and os.path.exists(temp_image_path):
            os.unlink(temp_image_path)
        raise HTTPException(500, detail=f"Error importing and rendering: {str(e)}")


@router.post("/import/txt", response_class=StreamingResponse, tags=["api", "import"])
async def import_txt_and_render(req: Request, image: UploadFile = File(...), txt_file: UploadFile = File(...), 
                               json_file: UploadFile = File(...), config: str = Form("{}"), 
                               template: UploadFile = File(None), user_env_vars: str = Form("{}")):
    """Import TXT + JSON + image, return rendered image (using UI layer import logic)"""
    import importlib.util

    from manga_translator.utils.path_manager import get_work_dir
    
    # Import workflow_service module directly, avoid triggering __init__.py
    workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 
                                        'desktop_qt_ui', 'services', 'workflow_service.py')
    workflow_service_path = os.path.abspath(workflow_service_path)
    spec = importlib.util.spec_from_file_location("workflow_service", workflow_service_path)
    workflow_service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow_service)
    
    safe_update_large_json_from_text = workflow_service.safe_update_large_json_from_text
    ensure_default_template_exists = workflow_service.ensure_default_template_exists
    
    img_bytes = await image.read()
    txt_content = await txt_file.read()
    
    # Parse config and get preset
    conf = parse_config(config)
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    json_content = await json_file.read()
    conf = parse_config(config)
    
    # Use temporary filename
    temp_name = f"temp_{secrets.token_hex(8)}"
    # Create temporary image path (in result directory)
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # Create work directory
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    temp_txt_path = os.path.join(work_dir, f"{temp_name}_temp.txt")
    
    try:
        # Save original JSON file
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # Save TXT file
        with open(temp_txt_path, 'wb') as f:
            f.write(txt_content)
        
        # Get template path
        if template:
            # If user provided template, save it
            template_content = await template.read()
            temp_template_path = os.path.join(work_dir, f"{temp_name}_template.txt")
            with open(temp_template_path, 'wb') as f:
                f.write(template_content)
            template_path = temp_template_path
        else:
            # Use default template
            template_path = ensure_default_template_exists()
            if not template_path:
                raise HTTPException(500, detail="无法找到或创建默认模板文件")
        
        # Use UI layer import logic (supports template parsing and fuzzy matching)
        import_result = safe_update_large_json_from_text(temp_txt_path, json_path, template_path)
        
        if import_result.startswith("错误"):
            raise HTTPException(400, detail=import_result)
        
        # Save image to temporary location
        temp_image = open_pil_image(io.BytesIO(img_bytes), eager=False)
        temp_image.save(temp_image_path)
        
        # Reload image and set name attribute
        temp_image = open_pil_image(temp_image_path, eager=False)
        temp_image.name = temp_image_path
        
        # Use load_text workflow, call through get_ctx (supports task queue)
        ctx = await get_ctx(req, conf, temp_image, "load_text")
        
        if ctx.result:
            img_byte_arr = io.BytesIO()
            ctx.result.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            
            # Clean up temporary files
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            
            return StreamingResponse(img_byte_arr, media_type="image/png")
        else:
            # Clean up temporary files
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            raise HTTPException(500, detail="Failed to render image")
    
    except Exception as e:
        # Clean up temporary files
        if os.path.exists(json_path):
            os.unlink(json_path)
        if 'temp_image_path' in locals() and os.path.exists(temp_image_path):
            os.unlink(temp_image_path)
        raise HTTPException(500, detail=f"Error importing and rendering: {str(e)}")


@router.post("/import/json/stream", response_class=StreamingResponse, tags=["api", "import", "stream"])
async def import_json_and_render_stream(req: Request, image: UploadFile = File(...), json_file: UploadFile = File(...), 
                                       config: str = Form("{}"), user_env_vars: str = Form("{}")):
    """Import JSON + image, return rendered image (streaming, with progress)"""
#     import json
    from manga_translator.utils.path_manager import get_work_dir
    
    img = await image.read()
    json_content = await json_file.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    conf._username = username
    
    # Use temporary filename
    temp_name = f"temp_{secrets.token_hex(8)}"
    # Create temporary image path (in result directory)
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # Save JSON to temporary file
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    
    try:
        # Write JSON file
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # Save image to temporary location
        temp_image = open_pil_image(io.BytesIO(img), eager=False)
        temp_image.save(temp_image_path)
        
        # Reload image and set name attribute
        temp_image = open_pil_image(temp_image_path, eager=False)
        temp_image.name = temp_image_path
        
        # Use streaming translation, pass PIL Image object
        # Note: Cannot delete files in finally block during streaming response
        # Temporary files will accumulate in result directory, need periodic cleanup
        # 并发控制在 while_streaming 内部处理
        return await while_streaming(req, transform_to_image, conf, temp_image, "load_text", image.filename)
    
    except Exception as _e:
        # Only clean up temporary files on error
        try:
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
        except Exception:
            pass  # Ignore cleanup errors
        raise


@router.post("/import/txt/stream", response_class=StreamingResponse, tags=["api", "import", "stream"])
async def import_txt_and_render_stream(req: Request, image: UploadFile = File(...), txt_file: UploadFile = File(...), 
                                      json_file: UploadFile = File(...), config: str = Form("{}"), 
                                      template: UploadFile = File(None), user_env_vars: str = Form("{}")):
    """Import TXT + JSON + image, return rendered image (streaming, with progress, using UI layer import logic)"""
    import importlib.util

    from manga_translator.utils.path_manager import get_work_dir
    
    # Import workflow_service module directly, avoid triggering __init__.py
    workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 
                                        'desktop_qt_ui', 'services', 'workflow_service.py')
    workflow_service_path = os.path.abspath(workflow_service_path)
    spec = importlib.util.spec_from_file_location("workflow_service", workflow_service_path)
    workflow_service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow_service)
    
    safe_update_large_json_from_text = workflow_service.safe_update_large_json_from_text
    ensure_default_template_exists = workflow_service.ensure_default_template_exists
    
    img = await image.read()
    txt_content = await txt_file.read()
    json_content = await json_file.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    conf._username = username
    
    # Use temporary filename
    temp_name = f"temp_{secrets.token_hex(8)}"
    # Create temporary image path (in result directory)
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # Create work directory
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    temp_txt_path = os.path.join(work_dir, f"{temp_name}_temp.txt")
    
    try:
        # Save original JSON file
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # Save TXT file
        with open(temp_txt_path, 'wb') as f:
            f.write(txt_content)
        
        # Get template path
        if template:
            # If user provided template, save it
            template_content = await template.read()
            temp_template_path = os.path.join(work_dir, f"{temp_name}_template.txt")
            with open(temp_template_path, 'wb') as f:
                f.write(template_content)
            template_path = temp_template_path
        else:
            # Use default template
            template_path = ensure_default_template_exists()
            if not template_path:
                raise HTTPException(500, detail="无法找到或创建默认模板文件")
        
        # Use UI layer import logic
        import_result = safe_update_large_json_from_text(temp_txt_path, json_path, template_path)
        
        if import_result.startswith("错误"):
            raise HTTPException(400, detail=import_result)
        
        # Save image to temporary location
        temp_image = open_pil_image(io.BytesIO(img), eager=False)
        temp_image.save(temp_image_path)
        
        # Reload image and set name attribute
        temp_image = open_pil_image(temp_image_path, eager=False)
        temp_image.name = temp_image_path
        
        # Use streaming translation, pass PIL Image object
        # Note: Cannot delete files in finally block during streaming response
        # Temporary files will accumulate in result directory, need periodic cleanup
        return await while_streaming(req, transform_to_image, conf, temp_image, "load_text", image.filename)
    
    except Exception as _e:
        # Only clean up temporary files on error
        try:
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
        except Exception:
            pass  # Ignore cleanup errors
        raise


# ============================================================================
# Complete Translation Endpoint (Multipart Response)
# ============================================================================

@router.post("/complete", tags=["api", "form"])
async def translate_complete(req: Request, image: UploadFile = File(...), config: str = Form("{}"),
                             user_env_vars: str = Form("{}")):
    """Translate image, return complete result (JSON + image + TXT) in multipart form"""
#     import json
    from fastapi.responses import Response
    
    img = await image.read()
    conf = parse_config(config)
    
    # Verify authentication and get username for preset
    username, _ = await verify_translation_auth(req, conf)
    env_vars = await apply_user_env_vars(user_env_vars, conf, admin_settings, username)
    conf._user_env_vars = env_vars
    
    # Execute translation
    ctx = await get_ctx(req, conf, img, "normal")
    
    # Get JSON data
    translation_data = to_translation(ctx)
    json_str = translation_data.model_dump_json()
    
    # Get image data
    img_byte_arr = io.BytesIO()
    if ctx.result:
        ctx.result.save(img_byte_arr, format="PNG")
    img_bytes = img_byte_arr.getvalue()
    
    # Build multipart response
    boundary = "----WebKitFormBoundary" + secrets.token_hex(16)
    
    parts = []
    
    # Part 1: JSON
    parts.append(f'--{boundary}\r\n')
    parts.append('Content-Disposition: form-data; name="json"\r\n')
    parts.append('Content-Type: application/json\r\n\r\n')
    parts.append(json_str)
    parts.append('\r\n')
    
    # Part 2: Image
    parts.append(f'--{boundary}\r\n')
    parts.append('Content-Disposition: form-data; name="image"; filename="result.png"\r\n')
    parts.append('Content-Type: image/png\r\n\r\n')
    
    # Combine response
    response_parts = []
    for part in parts:
        if isinstance(part, str):
            response_parts.append(part.encode('utf-8'))
        else:
            response_parts.append(part)
    
    response_parts.append(img_bytes)
    response_parts.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
    
    response_body = b''.join(response_parts)
    
    return Response(
        content=response_body,
        media_type=f'multipart/form-data; boundary={boundary}',
        headers={
            "Content-Length": str(len(response_body))
        }
    )
