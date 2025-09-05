import sys
sys.setrecursionlimit(sys.getrecursionlimit() * 5)

# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop-ui\\main.py'],
    pathex=['C:/Users/徐浩文/manga-image-translator/manga-translator-ui-package/desktop-ui'],
    binaries=[('.venv_gpu\\Lib\\site-packages\\pydensecrf\\*.pyd', 'pydensecrf')],
        datas=[('fonts', 'fonts'), ('dict', 'dict'), ('models', 'models'), ('MangaStudio_Data', 'MangaStudio_Data'), ('examples', 'examples'), ('desktop-ui/prompts.json', 'desktop-ui'), ('desktop-ui/translations_cn.json', 'desktop-ui')],
    hiddenimports=[
        'aiofiles', 'aiohttp', 'aioshutil', 'albumentations', 'arabic_reshaper', 'backports.statistics', 
        'colorama', 'ctranslate2', 'customtkinter', 'cv2', 'deepl', 'dotenv', 'einops', 'fastapi', 
        'freetype', 'google', 'google.generativeai', 'googletrans', 'groq', 'huggingface_hub', 'hyphen', 
        'httpcore', 'httpx', 'kornia', 'langcodes', 'loguru', 'manga_ocr', 'networkx', 'numpy', 
        'omegaconf', 'onnxruntime', 'openai', 'open_clip', 'packaging', 'pandas', 'PIL', 'psutil', 
        'py3langid', 'pyclipper', 'pydantic', 'pydensecrf', 'pydensecrf.utils', 'PySide6', 'pytest', 
        'pytorch_lightning', 'pytz', 'regex', 'requests', 'rich', 'rusty_manga_image_translator', 
        'safetensors', 'scipy', 'sentencepiece', 'setuptools', 'shapely', 'skimage', 'skimage.io', 
        'starlette', 'tiktoken', 'timm', 'torch', 'torchaudio', 'torchsummary', 'torchvision', 'tqdm', 
        'transformers', 'triton', 'uvicorn', 'websockets', 'xformers', 'yaml', 'bidi',
        'services.async_service', 'services.config_service', 'services.drag_drop_service', 'services.editor_history', 
        'services.erase_config_service', 'services.error_handler', 'services.file_service', 'services.i18n_service', 
        'services.json_preprocessor_service', 'services.lightweight_inpainter', 'services.log_service', 
        'services.mask_erase_preview_service', 'services.ocr_service', 'services.performance_optimizer', 
        'services.progress_manager', 'services.render_parameter_service', 'services.shortcut_manager', 
        'services.state_manager', 'services.transform_service', 'services.translation_service', 'services.workflow_service'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='manga-translator-gpu',
    
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='manga-translator-gpu',
)