import io
import os
import secrets
import shutil
import signal
import subprocess
import sys
from argparse import Namespace
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from manga_translator import Config
from manga_translator.server.instance import ExecutorInstance, executor_instances
from manga_translator.server.myqueue import task_queue
from manga_translator.server.request_extraction import get_ctx, while_streaming, TranslateRequest, BatchTranslateRequest, get_batch_ctx
from manga_translator.server.to_json import to_translation, TranslationResponse

app = FastAPI()
nonce = None

# 全局服务器配置（从启动参数设置）
server_config = {
    'use_gpu': False,
    'use_gpu_limited': False,
    'verbose': False,
    'models_ttl': 0,
    'retry_attempts': None,
}

# 默认配置文件路径
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'examples', 'config.json')

def load_default_config() -> Config:
    """加载默认配置文件"""
    if os.path.exists(DEFAULT_CONFIG_PATH):
        try:
            with open(DEFAULT_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config_json = f.read()
            return Config.parse_raw(config_json)
        except Exception as e:
            print(f"[WARNING] Failed to load default config from {DEFAULT_CONFIG_PATH}: {e}")
            return Config()
    else:
        print(f"[WARNING] Default config file not found: {DEFAULT_CONFIG_PATH}")
        return Config()

def parse_config(config_str: str) -> Config:
    """解析配置，如果为空则使用默认配置"""
    if not config_str or config_str.strip() in ('{}', ''):
        print("[INFO] No config provided, using default config from examples/config.json")
        return load_default_config()
    else:
        return Config.parse_raw(config_str)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加result文件夹静态文件服务
if os.path.exists("../result"):
    app.mount("/result", StaticFiles(directory="../result"), name="result")

@app.post("/register", response_description="no response", tags=["internal-api"])
async def register_instance(instance: ExecutorInstance, req: Request, req_nonce: str = Header(alias="X-Nonce")):
    if req_nonce != nonce:
        raise HTTPException(401, detail="Invalid nonce")
    instance.ip = req.client.host
    executor_instances.register(instance)

def transform_to_image(ctx):
    # 检查 ctx.result 是否存在
    if ctx.result is None:
        raise HTTPException(500, detail="Translation failed: no result image generated")
    
    # 检查是否使用占位符（在web模式下final.png保存后会设置此标记）
    if hasattr(ctx, 'use_placeholder') and ctx.use_placeholder:
        # ctx.result已经是1x1占位符图片，快速传输
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        return img_byte_arr.getvalue()

    # 返回完整的翻译结果
    img_byte_arr = io.BytesIO()
    ctx.result.save(img_byte_arr, format="PNG")
    return img_byte_arr.getvalue()

def transform_to_json(ctx):
    return to_translation(ctx).model_dump_json().encode("utf-8")

def transform_to_bytes(ctx):
    return to_translation(ctx).to_bytes()

@app.post("/translate/json", response_model=TranslationResponse, tags=["api", "json"],response_description="json strucure inspired by the ichigo translator extension")
async def json(req: Request, data: TranslateRequest):
    ctx = await get_ctx(req, data.config, data.image, "save_json")
    return to_translation(ctx)

@app.post("/translate/bytes", response_class=StreamingResponse, tags=["api", "json"],response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
async def bytes(req: Request, data: TranslateRequest):
    ctx = await get_ctx(req, data.config, data.image, "save_json")
    return StreamingResponse(content=to_translation(ctx).to_bytes())

@app.post("/translate/image", response_description="the result image", tags=["api", "json"],response_class=StreamingResponse)
async def image(req: Request, data: TranslateRequest) -> StreamingResponse:
    ctx = await get_ctx(req, data.config, data.image, "normal")
    
    if not ctx.result:
        raise HTTPException(500, detail="Translation failed: no result image generated")
    
    img_byte_arr = io.BytesIO()
    ctx.result.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)

    return StreamingResponse(img_byte_arr, media_type="image/png")

@app.post("/translate/json/stream", response_class=StreamingResponse,tags=["api", "json"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_json(req: Request, data: TranslateRequest) -> StreamingResponse:
    return await while_streaming(req, transform_to_json, data.config, data.image, "save_json")

@app.post("/translate/bytes/stream", response_class=StreamingResponse, tags=["api", "json"],response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_bytes(req: Request, data: TranslateRequest)-> StreamingResponse:
    return await while_streaming(req, transform_to_bytes,data.config, data.image, "save_json")

@app.post("/translate/image/stream", response_class=StreamingResponse, tags=["api", "json"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_image(req: Request, data: TranslateRequest) -> StreamingResponse:
    return await while_streaming(req, transform_to_image, data.config, data.image, "normal")

@app.post("/translate/with-form/json", response_model=TranslationResponse, tags=["api", "form"],response_description="json strucure inspired by the ichigo translator extension")
async def json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    img = await image.read()
    conf = parse_config(config)
    ctx = await get_ctx(req, conf, img, "save_json")
    return to_translation(ctx)

@app.post("/translate/with-form/bytes", response_class=StreamingResponse, tags=["api", "form"],response_description="custom byte structure for decoding look at examples in 'examples/response.*'")
async def bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    img = await image.read()
    conf = parse_config(config)
    ctx = await get_ctx(req, conf, img, "save_json")
    return StreamingResponse(content=to_translation(ctx).to_bytes())

@app.post("/translate/with-form/image", response_description="the result image", tags=["api", "form"],response_class=StreamingResponse)
async def image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    img = await image.read()
    conf = parse_config(config)
    ctx = await get_ctx(req, conf, img, "normal")
    
    if not ctx.result:
        raise HTTPException(500, detail="Translation failed: no result image generated")
    
    img_byte_arr = io.BytesIO()
    ctx.result.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)

    return StreamingResponse(img_byte_arr, media_type="image/png")

@app.post("/translate/with-form/json/stream", response_class=StreamingResponse, tags=["api", "form"],response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_json_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    img = await image.read()
    conf = parse_config(config)
    # 标记这是Web前端调用，用于占位符优化
    conf._is_web_frontend = True
    return await while_streaming(req, transform_to_json, conf, img, "save_json")



@app.post("/translate/with-form/bytes/stream", response_class=StreamingResponse,tags=["api", "form"], response_description="A stream over elements with strucure(1byte status, 4 byte size, n byte data) status code are 0,1,2,3,4 0 is result data, 1 is progress report, 2 is error, 3 is waiting queue position, 4 is waiting for translator instance")
async def stream_bytes_form(req: Request, image: UploadFile = File(...), config: str = Form("{}"))-> StreamingResponse:
    img = await image.read()
    conf = parse_config(config)
    return await while_streaming(req, transform_to_bytes, conf, img, "save_json")

@app.post("/translate/with-form/image/stream", response_class=StreamingResponse, tags=["api", "form"], response_description="Standard streaming endpoint - returns complete image data. Suitable for API calls and scripts.")
async def stream_image_form(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    """通用流式端点：返回完整图片数据，适用于API调用和comicread脚本"""
    img = await image.read()
    conf = parse_config(config)
    # 标记为通用模式，不使用占位符优化
    conf._web_frontend_optimized = False
    return await while_streaming(req, transform_to_image, conf, img, "normal")

@app.post("/translate/with-form/image/stream/web", response_class=StreamingResponse, tags=["api", "form"], response_description="Web frontend optimized streaming endpoint - uses placeholder optimization for faster response.")
async def stream_image_form_web(req: Request, image: UploadFile = File(...), config: str = Form("{}")) -> StreamingResponse:
    """Web前端专用端点：使用占位符优化，提供极速体验"""
    img = await image.read()
    conf = parse_config(config)
    # 标记为Web前端优化模式，使用占位符优化
    conf._web_frontend_optimized = True
    return await while_streaming(req, transform_to_image, conf, img, "normal")

@app.post("/queue-size", response_model=int, tags=["api", "json"])
async def queue_size() -> int:
    return len(task_queue.queue)


@app.api_route("/result/{folder_name}/final.png", methods=["GET", "HEAD"], tags=["api", "file"])
async def get_result_by_folder(folder_name: str):
    """根据文件夹名称获取翻译结果图片"""
    result_dir = "../result"
    if not os.path.exists(result_dir):
        raise HTTPException(404, detail="Result directory not found")

    folder_path = os.path.join(result_dir, folder_name)
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        raise HTTPException(404, detail=f"Folder {folder_name} not found")

    final_png_path = os.path.join(folder_path, "final.png")
    if not os.path.exists(final_png_path):
        raise HTTPException(404, detail="final.png not found in folder")

    async def file_iterator():
        with open(final_png_path, "rb") as f:
            yield f.read()

    return StreamingResponse(
        file_iterator(),
        media_type="image/png",
        headers={"Content-Disposition": f"inline; filename=final.png"}
    )

@app.post("/translate/batch/json", response_model=list[TranslationResponse], tags=["api", "json", "batch"])
async def batch_json(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return JSON format results"""
    results = await get_batch_ctx(req, data.config, data.images, data.batch_size, "normal")
    return [to_translation(ctx) for ctx in results]

@app.post("/translate/batch/images", response_description="Zip file containing translated images", tags=["api", "batch"])
async def batch_images(req: Request, data: BatchTranslateRequest):
    """Batch translate images and return zip archive containing translated images"""
    import zipfile
    import tempfile
    
    results = await get_batch_ctx(req, data.config, data.images, data.batch_size, "normal")
    
    # Create temporary ZIP file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_file:
        tmp_file_name = tmp_file.name
    
    # 文件句柄已关闭，现在可以安全地写入
    with zipfile.ZipFile(tmp_file_name, 'w') as zip_file:
        for i, ctx in enumerate(results):
            if ctx.result:
                img_byte_arr = io.BytesIO()
                ctx.result.save(img_byte_arr, format="PNG")
                zip_file.writestr(f"translated_{i+1}.png", img_byte_arr.getvalue())
    
    # 读取 ZIP 文件内容
    with open(tmp_file_name, 'rb') as f:
        zip_data = f.read()
    
    # 清理临时文件（Windows 兼容）
    try:
        os.unlink(tmp_file_name)
    except PermissionError:
        # Windows 上可能需要延迟删除
        import atexit
        atexit.register(lambda: os.unlink(tmp_file_name) if os.path.exists(tmp_file_name) else None)
    
    return StreamingResponse(
        io.BytesIO(zip_data),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=translated_images.zip"}
        )

@app.post("/translate/export/original", response_class=StreamingResponse, tags=["api", "export"])
async def export_original(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """导出原文（ZIP 压缩包：JSON + TXT）"""
    workflow = "export_original"
    import json
    import tempfile
    import zipfile
    
    img = await image.read()
    conf = parse_config(config)
    
    # 使用指定的 workflow 进行处理
    ctx = await get_ctx(req, conf, img, workflow)
    
    # 将结果转换为 JSON 格式
    translation_data = to_translation(ctx)
    
    # 创建临时 JSON 文件（使用与主翻译程序相同的格式）
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp_json:
        # 将 Pydantic 模型转换为字典
        response_dict = translation_data.model_dump()
        
        # 构建与主翻译程序相同的 JSON 结构
        json_data = {
            "temp_image": response_dict
        }
        json.dump(json_data, tmp_json, ensure_ascii=False, indent=4)
        tmp_json_path = tmp_json.name
    
    try:
        # 生成 TXT 文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp_txt:
            tmp_txt_path = tmp_txt.name
        
        # 直接导入模块，避免触发 __init__.py 中的其他导入
        import sys
        import os
        workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', 'desktop_qt_ui', 'services', 'workflow_service.py')
        workflow_service_path = os.path.abspath(workflow_service_path)
        
        import importlib.util
        spec = importlib.util.spec_from_file_location("workflow_service", workflow_service_path)
        workflow_service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_service)
        
        # 获取默认模板
        template_path = workflow_service.ensure_default_template_exists()
        if not template_path:
            raise HTTPException(500, detail="无法创建或找到默认模板文件")
        
        ui_generate_original_text = workflow_service.generate_original_text
        txt_path = ui_generate_original_text(tmp_json_path, template_path=template_path, output_path=tmp_txt_path)
        
        if txt_path.startswith("Error"):
            raise HTTPException(500, detail=txt_path)
        
        # 创建 ZIP 文件
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 添加 JSON 文件
            with open(tmp_json_path, 'r', encoding='utf-8') as f:
                json_content = f.read()
            zip_file.writestr("translation.json", json_content)
            
            # 添加 TXT 文件
            with open(txt_path, 'r', encoding='utf-8') as f:
                txt_content = f.read()
            zip_file.writestr("original.txt", txt_content)
        
        # 清理临时文件
        os.unlink(tmp_json_path)
        os.unlink(txt_path)
        
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=original_export.zip"}
        )
    except Exception as e:
        # 清理临时文件
        if os.path.exists(tmp_json_path):
            os.unlink(tmp_json_path)
        if 'txt_path' in locals() and os.path.exists(txt_path):
            os.unlink(txt_path)
        raise HTTPException(500, detail=f"Error exporting files: {str(e)}")

@app.post("/translate/export/translated", response_class=StreamingResponse, tags=["api", "export"])
async def export_translated(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """导出译文（ZIP 压缩包：JSON + TXT）"""
    workflow = "save_json"
    import json
    import tempfile
    import zipfile
    
    img = await image.read()
    conf = parse_config(config)
    
    # 使用指定的 workflow 进行处理
    ctx = await get_ctx(req, conf, img, workflow)
    
    # 将结果转换为 JSON 格式
    translation_data = to_translation(ctx)
    
    # 创建临时 JSON 文件（使用与主翻译程序相同的格式）
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp_json:
        # 将 Pydantic 模型转换为字典
        response_dict = translation_data.model_dump()
        
        # 构建与主翻译程序相同的 JSON 结构
        json_data = {
            "temp_image": response_dict
        }
        json.dump(json_data, tmp_json, ensure_ascii=False, indent=4)
        tmp_json_path = tmp_json.name
    
    try:
        # 生成 TXT 文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp_txt:
            tmp_txt_path = tmp_txt.name
        
        # 直接导入模块，避免触发 __init__.py 中的其他导入
        import sys
        import os
        workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', 'desktop_qt_ui', 'services', 'workflow_service.py')
        workflow_service_path = os.path.abspath(workflow_service_path)
        
        import importlib.util
        spec = importlib.util.spec_from_file_location("workflow_service", workflow_service_path)
        workflow_service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_service)
        
        # 获取默认模板
        template_path = workflow_service.ensure_default_template_exists()
        if not template_path:
            raise HTTPException(500, detail="无法创建或找到默认模板文件")
        
        ui_generate_translated_text = workflow_service.generate_translated_text
        txt_path = ui_generate_translated_text(tmp_json_path, template_path=template_path, output_path=tmp_txt_path)
        
        if txt_path.startswith("Error"):
            raise HTTPException(500, detail=txt_path)
        
        # 创建 ZIP 文件
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 添加 JSON 文件
            with open(tmp_json_path, 'r', encoding='utf-8') as f:
                json_content = f.read()
            zip_file.writestr("translation.json", json_content)
            
            # 添加 TXT 文件
            with open(txt_path, 'r', encoding='utf-8') as f:
                txt_content = f.read()
            zip_file.writestr("translated.txt", txt_content)
        
        # 清理临时文件
        os.unlink(tmp_json_path)
        os.unlink(txt_path)
        
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=translated_export.zip"}
        )
    except Exception as e:
        # 清理临时文件
        if os.path.exists(tmp_json_path):
            os.unlink(tmp_json_path)
        if 'txt_path' in locals() and os.path.exists(txt_path):
            os.unlink(txt_path)
        raise HTTPException(500, detail=f"Error exporting files: {str(e)}")

@app.post("/translate/upscale", response_class=StreamingResponse, tags=["api", "process"])
async def upscale_only(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """仅超分（图片超分辨率）"""
    img = await image.read()
    conf = parse_config(config)
    ctx = await get_ctx(req, conf, img, "upscale_only")
    
    if ctx.result:
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return StreamingResponse(img_byte_arr, media_type="image/png")
    else:
        raise HTTPException(500, detail="Upscaling failed")

@app.post("/translate/colorize", response_class=StreamingResponse, tags=["api", "process"])
async def colorize_only(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """仅上色（黑白图片上色）"""
    img = await image.read()
    conf = parse_config(config)
    ctx = await get_ctx(req, conf, img, "colorize_only")
    
    if ctx.result:
        img_byte_arr = io.BytesIO()
        ctx.result.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return StreamingResponse(img_byte_arr, media_type="image/png")
    else:
        raise HTTPException(500, detail="Colorization failed")

@app.post("/translate/import/json", response_class=StreamingResponse, tags=["api", "import"])
async def import_json_and_render(req: Request, image: UploadFile = File(...), json_file: UploadFile = File(...), config: str = Form("{}")):
    """导入 JSON + 图片，返回渲染后的图片（load_text workflow）"""
    import json
    import tempfile
    from manga_translator.utils.path_manager import get_work_dir
    from PIL import Image as PILImage
    
    img_bytes = await image.read()
    json_content = await json_file.read()
    conf = parse_config(config)
    
    # 使用临时文件名
    temp_name = f"temp_{secrets.token_hex(8)}"
    # 先创建临时图片路径（在当前目录或result目录）
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # 保存 JSON 到临时文件
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    
    try:
        # 写入 JSON 文件
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # 保存图片到临时位置（使用相同的名称）
        temp_image = PILImage.open(io.BytesIO(img_bytes))
        temp_image.save(temp_image_path)
        
        # 重新加载图片并设置 name 属性
        temp_image = PILImage.open(temp_image_path)
        temp_image.name = temp_image_path
        
        # 使用 load_text workflow，通过 get_ctx 调用（支持任务队列）
        ctx = await get_ctx(req, conf, temp_image, "load_text")
        
        if ctx.result:
            img_byte_arr = io.BytesIO()
            ctx.result.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            
            # 清理临时文件
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            
            return StreamingResponse(img_byte_arr, media_type="image/png")
        else:
            # 清理临时文件
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            raise HTTPException(500, detail="Failed to render image")
    
    except Exception as e:
        # 清理临时文件
        if os.path.exists(json_path):
            os.unlink(json_path)
        if 'temp_image_path' in locals() and os.path.exists(temp_image_path):
            os.unlink(temp_image_path)
        raise HTTPException(500, detail=f"Error importing and rendering: {str(e)}")

@app.post("/translate/import/txt", response_class=StreamingResponse, tags=["api", "import"])
async def import_txt_and_render(req: Request, image: UploadFile = File(...), txt_file: UploadFile = File(...), json_file: UploadFile = File(...), config: str = Form("{}"), template: UploadFile = File(None)):
    """导入 TXT + JSON + 图片，返回渲染后的图片（使用 UI 层的导入逻辑）"""
    import tempfile
    import importlib.util
    from manga_translator.utils.path_manager import get_work_dir
    from PIL import Image as PILImage
    
    # 直接导入 workflow_service 模块，避免触发 __init__.py
    workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', 'desktop_qt_ui', 'services', 'workflow_service.py')
    workflow_service_path = os.path.abspath(workflow_service_path)
    spec = importlib.util.spec_from_file_location("workflow_service", workflow_service_path)
    workflow_service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow_service)
    
    safe_update_large_json_from_text = workflow_service.safe_update_large_json_from_text
    ensure_default_template_exists = workflow_service.ensure_default_template_exists
    
    img_bytes = await image.read()
    txt_content = await txt_file.read()
    json_content = await json_file.read()
    conf = parse_config(config)
    
    # 使用临时文件名
    temp_name = f"temp_{secrets.token_hex(8)}"
    # 先创建临时图片路径（在result目录）
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # 创建工作目录
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    temp_txt_path = os.path.join(work_dir, f"{temp_name}_temp.txt")
    
    try:
        # 保存原始 JSON 文件
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # 保存 TXT 文件
        with open(temp_txt_path, 'wb') as f:
            f.write(txt_content)
        
        # 获取模板路径
        if template:
            # 如果用户提供了模板，保存它
            template_content = await template.read()
            temp_template_path = os.path.join(work_dir, f"{temp_name}_template.txt")
            with open(temp_template_path, 'wb') as f:
                f.write(template_content)
            template_path = temp_template_path
        else:
            # 使用默认模板
            template_path = ensure_default_template_exists()
            if not template_path:
                raise HTTPException(500, detail="无法找到或创建默认模板文件")
        
        # 使用 UI 层的导入逻辑（支持模板解析和模糊匹配）
        import_result = safe_update_large_json_from_text(temp_txt_path, json_path, template_path)
        
        if import_result.startswith("错误"):
            raise HTTPException(400, detail=import_result)
        
        # 保存图片到临时位置
        temp_image = PILImage.open(io.BytesIO(img_bytes))
        temp_image.save(temp_image_path)
        
        # 重新加载图片并设置 name 属性
        temp_image = PILImage.open(temp_image_path)
        temp_image.name = temp_image_path
        
        # 使用 load_text workflow，通过 get_ctx 调用（支持任务队列）
        ctx = await get_ctx(req, conf, temp_image, "load_text")
        
        if ctx.result:
            img_byte_arr = io.BytesIO()
            ctx.result.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            
            # 清理临时文件
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            
            return StreamingResponse(img_byte_arr, media_type="image/png")
        else:
            # 清理临时文件
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            raise HTTPException(500, detail="Failed to render image")
    
    except Exception as e:
        # 清理临时文件
        if os.path.exists(json_path):
            os.unlink(json_path)
        if 'temp_image_path' in locals() and os.path.exists(temp_image_path):
            os.unlink(temp_image_path)
        raise HTTPException(500, detail=f"Error importing and rendering: {str(e)}")

@app.post("/translate/import/json/stream", response_class=StreamingResponse, tags=["api", "import", "stream"])
async def import_json_and_render_stream(req: Request, image: UploadFile = File(...), json_file: UploadFile = File(...), config: str = Form("{}")):
    """导入 JSON + 图片，返回渲染后的图片（流式，支持进度）"""
    import json
    from manga_translator.utils.path_manager import get_work_dir
    from PIL import Image
    
    img = await image.read()
    json_content = await json_file.read()
    conf = parse_config(config)
    
    # 使用临时文件名
    temp_name = f"temp_{secrets.token_hex(8)}"
    # 先创建临时图片路径（在result目录）
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # 保存 JSON 到临时文件
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    
    try:
        # 写入 JSON 文件
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # 保存图片到临时位置
        temp_image = Image.open(io.BytesIO(img))
        temp_image.save(temp_image_path)
        
        # 重新加载图片并设置 name 属性
        temp_image = Image.open(temp_image_path)
        temp_image.name = temp_image_path
        
        # 使用流式翻译，传递 PIL Image 对象
        # 注意：流式响应中不能在 finally 中删除文件，因为响应还在进行中
        # 临时文件会在 result 目录中累积，需要定期清理
        return await while_streaming(req, transform_to_image, conf, temp_image, "load_text")
    
    except Exception as e:
        # 只在出错时清理临时文件
        try:
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
        except:
            pass  # 忽略清理错误
        raise

@app.post("/translate/import/txt/stream", response_class=StreamingResponse, tags=["api", "import", "stream"])
async def import_txt_and_render_stream(req: Request, image: UploadFile = File(...), txt_file: UploadFile = File(...), json_file: UploadFile = File(...), config: str = Form("{}"), template: UploadFile = File(None)):
    """导入 TXT + JSON + 图片，返回渲染后的图片（流式，支持进度，使用 UI 层的导入逻辑）"""
    import tempfile
    import importlib.util
    from manga_translator.utils.path_manager import get_work_dir
    from PIL import Image
    
    # 直接导入 workflow_service 模块，避免触发 __init__.py
    workflow_service_path = os.path.join(os.path.dirname(__file__), '..', '..', 'desktop_qt_ui', 'services', 'workflow_service.py')
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
    
    # 使用临时文件名
    temp_name = f"temp_{secrets.token_hex(8)}"
    # 先创建临时图片路径（在result目录）
    temp_image_path = os.path.join("result", f"{temp_name}.png")
    os.makedirs("result", exist_ok=True)
    
    # 创建工作目录
    work_dir = get_work_dir(temp_image_path)
    json_dir = os.path.join(work_dir, 'json')
    os.makedirs(json_dir, exist_ok=True)
    
    json_path = os.path.join(json_dir, f"{temp_name}_translations.json")
    temp_txt_path = os.path.join(work_dir, f"{temp_name}_temp.txt")
    
    try:
        # 保存原始 JSON 文件
        with open(json_path, 'wb') as f:
            f.write(json_content)
        
        # 保存 TXT 文件
        with open(temp_txt_path, 'wb') as f:
            f.write(txt_content)
        
        # 获取模板路径
        if template:
            # 如果用户提供了模板，保存它
            template_content = await template.read()
            temp_template_path = os.path.join(work_dir, f"{temp_name}_template.txt")
            with open(temp_template_path, 'wb') as f:
                f.write(template_content)
            template_path = temp_template_path
        else:
            # 使用默认模板
            template_path = ensure_default_template_exists()
            if not template_path:
                raise HTTPException(500, detail="无法找到或创建默认模板文件")
        
        # 使用 UI 层的导入逻辑
        import_result = safe_update_large_json_from_text(temp_txt_path, json_path, template_path)
        
        if import_result.startswith("错误"):
            raise HTTPException(400, detail=import_result)
        
        # 保存图片到临时位置
        temp_image = Image.open(io.BytesIO(img))
        temp_image.save(temp_image_path)
        
        # 重新加载图片并设置 name 属性
        temp_image = Image.open(temp_image_path)
        temp_image.name = temp_image_path
        
        # 使用流式翻译，传递 PIL Image 对象
        # 注意：流式响应中不能在 finally 中删除文件，因为响应还在进行中
        # 临时文件会在 result 目录中累积，需要定期清理
        return await while_streaming(req, transform_to_image, conf, temp_image, "load_text")
    
    except Exception as e:
        # 只在出错时清理临时文件
        try:
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
        except:
            pass  # 忽略清理错误
        raise

@app.post("/translate/export/original/stream", response_class=StreamingResponse, tags=["api", "export", "stream"])
async def export_original_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """导出原文（流式，支持进度）"""
    img = await image.read()
    conf = parse_config(config)
    return await while_streaming(req, transform_to_json, conf, img, "export_original")

@app.post("/translate/export/translated/stream", response_class=StreamingResponse, tags=["api", "export", "stream"])
async def export_translated_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """导出译文（流式，支持进度）"""
    img = await image.read()
    conf = parse_config(config)
    return await while_streaming(req, transform_to_json, conf, img, "save_json")

@app.post("/translate/upscale/stream", response_class=StreamingResponse, tags=["api", "process", "stream"])
async def upscale_only_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """仅超分（流式，支持进度）"""
    img = await image.read()
    conf = parse_config(config)
    return await while_streaming(req, transform_to_image, conf, img, "upscale_only")

@app.post("/translate/colorize/stream", response_class=StreamingResponse, tags=["api", "process", "stream"])
async def colorize_only_stream(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """仅上色（流式，支持进度）"""
    img = await image.read()
    conf = parse_config(config)
    return await while_streaming(req, transform_to_image, conf, img, "colorize_only")



@app.post("/translate/complete", tags=["api", "form"])
async def translate_complete(req: Request, image: UploadFile = File(...), config: str = Form("{}")):
    """翻译图片，返回完整结果（JSON + 图片 + TXT）以 multipart 形式"""
    workflow = "normal"
    import json
    from fastapi.responses import Response
    
    img = await image.read()
    conf = parse_config(config)
    
    # 执行翻译
    ctx = await get_ctx(req, conf, img, workflow)
    
    # 获取 JSON 数据
    translation_data = to_translation(ctx)
    json_str = translation_data.model_dump_json()
    
    # 获取图片数据
    img_byte_arr = io.BytesIO()
    if ctx.result:
        ctx.result.save(img_byte_arr, format="PNG")
    img_bytes = img_byte_arr.getvalue()
    
    # 构建 multipart 响应
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
    
    # 组合响应
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

@app.get("/", tags=["info"])
async def root():
    """API 服务器信息"""
    return {
        "message": "Manga Translator API Server",
        "version": "2.0",
        "endpoints": {
            "translate": "/translate/image",
            "translate_stream": "/translate/with-form/image/stream",
            "batch": "/translate/batch/json",
            "docs": "/docs"
        }
    }

def generate_nonce():
    return secrets.token_hex(16)

def start_translator_client_proc(host: str, port: int, nonce: str, params: Namespace):
    cmds = [
        sys.executable,
        '-m', 'manga_translator',
        'shared',
        '--host', host,
        '--port', str(port),
        '--nonce', nonce,
    ]
    if params.use_gpu:
        cmds.append('--use-gpu')
    if params.use_gpu_limited:
        cmds.append('--use-gpu-limited')
    if params.ignore_errors:
        cmds.append('--ignore-errors')
    if params.verbose:
        cmds.append('--verbose')
    if params.models_ttl:
        cmds.append('--models-ttl=%s' % params.models_ttl)
    if getattr(params, 'pre_dict', None):
        cmds.extend(['--pre-dict', params.pre_dict])
    if getattr(params, 'post_dict', None):
        cmds.extend(['--post-dict', params.post_dict])       
    base_path = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(base_path)
    proc = subprocess.Popen(cmds, cwd=parent)
    executor_instances.register(ExecutorInstance(ip=host, port=port))

    def handle_exit_signals(signal, frame):
        proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit_signals)
    signal.signal(signal.SIGTERM, handle_exit_signals)

    return proc

def prepare(args):
    global nonce
    
    # web 模式没有 nonce 参数，使用 getattr 避免 AttributeError
    args_nonce = getattr(args, 'nonce', None)
    if args_nonce is None:
        nonce = os.getenv('MT_WEB_NONCE', generate_nonce())
    else:
        nonce = args_nonce
    
    # start_instance 也可能不存在于某些模式
    if getattr(args, 'start_instance', False):
        return start_translator_client_proc(args.host, args.port + 1, nonce, args)
    
    folder_name= "upload-cache"
    if os.path.exists(folder_name):
        shutil.rmtree(folder_name)
    os.makedirs(folder_name)

@app.post("/simple_execute/translate_batch", tags=["internal-api"])
async def simple_execute_batch(req: Request, data: BatchTranslateRequest):
    """Internal batch translation execution endpoint"""
    # Implementation for batch translation logic
    # Currently returns empty results, actual implementation needs to call batch translator
    from manga_translator import MangaTranslator
    translator = MangaTranslator({'batch_size': data.batch_size})
    
    # Prepare image-config pairs
    images_with_configs = [(img, data.config) for img in data.images]
    
    # Execute batch translation
    results = await translator.translate_batch(images_with_configs, data.batch_size)
    
    return results

@app.post("/execute/translate_batch", tags=["internal-api"])
async def execute_batch_stream(req: Request, data: BatchTranslateRequest):
    """Internal batch translation streaming execution endpoint"""
    # Streaming batch translation implementation
    from manga_translator import MangaTranslator
    translator = MangaTranslator({'batch_size': data.batch_size})
    
    # Prepare image-config pairs
    images_with_configs = [(img, data.config) for img in data.images]
    
    # Execute batch translation (streaming version requires more complex implementation)
    results = await translator.translate_batch(images_with_configs, data.batch_size)
    
    return results

@app.get("/results/list", tags=["api"])
async def list_results():
    """List all result directories"""
    result_dir = "../result"
    if not os.path.exists(result_dir):
        return {"directories": []}
    
    try:
        directories = []
        for item in os.listdir(result_dir):
            item_path = os.path.join(result_dir, item)
            if os.path.isdir(item_path):
                # Check if final.png exists in this directory
                final_png_path = os.path.join(item_path, "final.png")
                if os.path.exists(final_png_path):
                    directories.append(item)
        return {"directories": directories}
    except Exception as e:
        raise HTTPException(500, detail=f"Error listing results: {str(e)}")

@app.delete("/results/clear", tags=["api"])
async def clear_results():
    """Delete all result directories"""
    result_dir = "../result"
    if not os.path.exists(result_dir):
        return {"message": "No results directory found"}
    
    try:
        deleted_count = 0
        for item in os.listdir(result_dir):
            item_path = os.path.join(result_dir, item)
            if os.path.isdir(item_path):
                # Check if final.png exists in this directory
                final_png_path = os.path.join(item_path, "final.png")
                if os.path.exists(final_png_path):
                    shutil.rmtree(item_path)
                    deleted_count += 1
        
        return {"message": f"Deleted {deleted_count} result directories"}
    except Exception as e:
        raise HTTPException(500, detail=f"Error clearing results: {str(e)}")

@app.delete("/results/{folder_name}", tags=["api"])
async def delete_result(folder_name: str):
    """Delete a specific result directory"""
    result_dir = "../result"
    folder_path = os.path.join(result_dir, folder_name)
    
    if not os.path.exists(folder_path):
        raise HTTPException(404, detail="Result directory not found")
    
    try:
        # Check if final.png exists in this directory
        final_png_path = os.path.join(folder_path, "final.png")
        if not os.path.exists(final_png_path):
            raise HTTPException(404, detail="Result file not found")
        
        shutil.rmtree(folder_path)
        return {"message": f"Deleted result directory: {folder_name}"}
    except Exception as e:
        raise HTTPException(500, detail=f"Error deleting result: {str(e)}")

#todo: restart if crash
#todo: cache results
#todo: cleanup cache

@app.post("/cleanup/temp", tags=["api", "maintenance"])
async def cleanup_temp_files(max_age_hours: int = 24):
    """
    清理临时文件
    
    Args:
        max_age_hours: 清理多少小时前的临时文件（默认24小时）
    
    Returns:
        清理结果统计
    """
    import time
    
    result_dir = "result"
    if not os.path.exists(result_dir):
        return {"deleted": 0, "message": "No temp directory found"}
    
    deleted_count = 0
    current_time = time.time()
    max_age_seconds = max_age_hours * 3600
    
    try:
        for filename in os.listdir(result_dir):
            if filename.startswith("temp_"):
                filepath = os.path.join(result_dir, filename)
                try:
                    # 检查文件年龄
                    file_age = current_time - os.path.getmtime(filepath)
                    if file_age > max_age_seconds:
                        if os.path.isfile(filepath):
                            os.unlink(filepath)
                            deleted_count += 1
                        elif os.path.isdir(filepath):
                            shutil.rmtree(filepath)
                            deleted_count += 1
                except Exception as e:
                    # 忽略单个文件的删除错误（可能被占用）
                    continue
        
        return {
            "deleted": deleted_count,
            "message": f"Successfully cleaned up {deleted_count} temporary files older than {max_age_hours} hours"
        }
    except Exception as e:
        raise HTTPException(500, detail=f"Error during cleanup: {str(e)}")

def init_translator(use_gpu=False, verbose=False):
    """初始化翻译器（预留函数）"""
    # 这个函数用于预加载模型等初始化操作
    # 目前翻译器在首次请求时才会初始化
    pass

def main(args):
    """启动 Web API 服务器"""
    import uvicorn
    
    global nonce, server_config
    
    # 先设置服务器配置（在 prepare 之前）
    server_config['use_gpu'] = getattr(args, 'use_gpu', False)
    server_config['use_gpu_limited'] = getattr(args, 'use_gpu_limited', False)
    server_config['verbose'] = getattr(args, 'verbose', False)
    server_config['models_ttl'] = getattr(args, 'models_ttl', 0)
    server_config['retry_attempts'] = getattr(args, 'retry_attempts', None)
    print(f"[SERVER CONFIG] use_gpu={server_config['use_gpu']}, use_gpu_limited={server_config['use_gpu_limited']}, verbose={server_config['verbose']}, models_ttl={server_config['models_ttl']}, retry_attempts={server_config['retry_attempts']}")
    
    # web 模式不启动独立的翻译实例（与旧版本保持一致）
    args.start_instance = False
    proc = prepare(args)
    print("Nonce: "+nonce)
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    except Exception:
        if proc:
            proc.terminate()

if __name__ == '__main__':
    from args import parse_arguments
    args = parse_arguments()
    main(args)
