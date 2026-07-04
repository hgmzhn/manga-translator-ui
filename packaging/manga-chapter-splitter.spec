# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# 只添加 manga_chapter_splitter 目录到路径
chapter_splitter_dir = os.path.abspath('../desktop_qt_ui/manga_chapter_splitter')

a = Analysis(
    ['../desktop_qt_ui/manga_chapter_splitter/run.py'],
    pathex=[chapter_splitter_dir],  # 明确指定只搜索这个目录
    binaries=[],
    datas=[
        ('../desktop_qt_ui/manga_chapter_splitter/chapter_splitter_window.py', '.'),
    ],
    hiddenimports=['chapter_splitter_window'],  # 添加隐藏导入
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch',
        'torchvision',
        'numpy',
        'scipy',
        'pandas',
        'onnxruntime',
        'xformers',
        'cv2',
        'PIL',
        'pydantic',
        'websockets',
        'triton',
        'fugashi',
        'manga_translator',
        'app_logic',  # 排除主程序的 app_logic
        'main_view',
        'editor_view',
        'services',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='漫画分割工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='../desktop_qt_ui/ui/icons/icon.ico' if os.path.exists('../desktop_qt_ui/ui/icons/icon.ico') else None,
)
