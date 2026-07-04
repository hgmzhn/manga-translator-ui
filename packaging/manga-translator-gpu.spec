# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_all, get_package_paths
import os

# Collect data files dynamically instead of using a hardcoded path
# Use try-except to handle cases where packages might not be found
try:
    py3langid_datas = collect_data_files('py3langid')
except Exception:
    py3langid_datas = []

try:
    unidic_datas = collect_data_files('unidic_lite')
except Exception:
    unidic_datas = []

try:
    pythainlp_datas, pythainlp_binaries, pythainlp_hiddenimports = collect_all('pythainlp')
except Exception:
    pythainlp_datas, pythainlp_binaries, pythainlp_hiddenimports = [], [], []

try:
    nlpo3_datas, nlpo3_binaries, nlpo3_hiddenimports = collect_all('nlpo3')
except Exception:
    nlpo3_datas, nlpo3_binaries, nlpo3_hiddenimports = [], [], []

# OpenCC 简繁转换字典文件
try:
    opencc_datas = collect_data_files('opencc')
except Exception:
    opencc_datas = []

# 使用collect_all自动收集onnxruntime的所有内容
try:
    onnx_datas, onnx_binaries, onnx_hiddenimports = collect_all('onnxruntime')
except Exception:
    onnx_datas, onnx_binaries, onnx_hiddenimports = [], [], []

# 同时将onnxruntime的核心DLL也复制到根目录
try:
    onnxruntime_pkg_base, onnxruntime_pkg_dir = get_package_paths('onnxruntime')
    onnx_binaries.extend([
        (os.path.join(onnxruntime_pkg_dir, 'capi', 'onnxruntime.dll'), '.'),
        (os.path.join(onnxruntime_pkg_dir, 'capi', 'onnxruntime_providers_shared.dll'), '.'),
    ])
except Exception:
    pass

a = Analysis(
    ['../desktop_qt_ui/main.py'],  # 相对于packaging目录
    pathex=[],
    binaries=onnx_binaries + pythainlp_binaries + nlpo3_binaries,
    datas=py3langid_datas + unidic_datas + pythainlp_datas + nlpo3_datas + onnx_datas + opencc_datas,  # 添加所有数据文件
    hiddenimports=[
        'pydensecrf.eigen', 
        'bsdiff4.core', 
        'PyQt6.QtCore', 
        'PyQt6.QtGui', 
        'PyQt6.QtWidgets', 
        'matplotlib', 
        'matplotlib.pyplot',
        # PaddleOCR-VL 相关模块
        'manga_translator.ocr.paddleocr_vl_model',
        'manga_translator.ocr.paddleocr_vl_model.configuration_paddleocr_vl',
        'manga_translator.ocr.paddleocr_vl_model.modeling_paddleocr_vl',
        'manga_translator.ocr.paddleocr_vl_model.processing_paddleocr_vl',
        'manga_translator.ocr.paddleocr_vl_model.image_processing',
    ] + pythainlp_hiddenimports + nlpo3_hiddenimports + onnx_hiddenimports,  # 添加隐式导入
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(SPECPATH, 'pyi_rth_onnxruntime.py')],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='app',
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='../desktop_qt_ui/ui/icons/icon.ico',
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
