# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
spec_dir = os.path.dirname(os.path.abspath(__file__))

a = Analysis(['desktop-ui\main.py'],
             pathex=[spec_dir],

             binaries=[],
             datas=[],
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

# Add data files
datas += [
    ('models', 'models'),
    ('fonts', 'fonts'),
    ('dict', 'dict'),
    ('MangaStudio_Data', 'MangaStudio_Data'),
    ('desktop-ui/locales', 'desktop-ui/locales'),
    ('examples', 'examples')
]

# Add hidden imports
hiddenimports += [
    'manga_translator.detection.default',
    'manga_translator.detection.dbnet_convnext',
    'manga_translator.detection.ctd',
    'manga_translator.detection.craft',
    'manga_translator.detection.paddle_rust',
    'manga_translator.detection.none',
    'manga_translator.ocr.model_32px',
    'manga_translator.ocr.model_48px',
    'manga_translator.ocr.model_48px_ctc',
    'manga_translator.ocr.model_manga_ocr',
    'manga_translator.inpainting.inpainting_aot',
    'manga_translator.inpainting.inpainting_lama_mpe',
    'manga_translator.inpainting.inpainting_sd',
    'manga_translator.inpainting.none',
    'manga_translator.inpainting.original',
    'manga_translator.translators.baidu',
    'manga_translator.translators.deepl',
    'manga_translator.translators.youdao',
    'manga_translator.translators.papago',
    'manga_translator.translators.caiyun',
    'manga_translator.translators.chatgpt',
    'manga_translator.translators.chatgpt_2stage',
    'manga_translator.translators.nllb',
    'manga_translator.translators.sugoi',
    'manga_translator.translators.m2m100',
    'manga_translator.translators.mbart50',
    'manga_translator.translators.selective',
    'manga_translator.translators.none',
    'manga_translator.translators.original',
    'manga_translator.translators.sakura',
    'manga_translator.translators.qwen2',
    'manga_translator.translators.groq',
    'manga_translator.translators.gemini',
    'manga_translator.translators.gemini_2stage',
    'manga_translator.translators.custom_openai',
    'manga_translator.upscaling.waifu2x',
    'manga_translator.upscaling.esrgan',
    'manga_translator.upscaling.esrgan_pytorch',
    'manga_translator.colorization.manga_colorization_v2',
    'engineio.async_drivers.threading',
    'pytorch_lightning.core',
    'pytorch_lightning.utilities',
    'torchvision',
    'kornia.augmentation.functional',
    'kornia.geometry.transform',
    'timm.models',
    'safetensors.torch'
]


pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='manga_translator_ui',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=False, # Set to False for a GUI-only app
          windowed=True )
