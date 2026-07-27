"""
压缩包/文档格式图片提取工具
支持 PDF、EPUB、CBZ 格式
"""
import json
import os
import shutil
import tempfile
import zipfile
from typing import List, Optional, Tuple

from manga_translator.image_formats import SUPPORTED_IMAGE_EXTENSIONS

# 支持的压缩包/文档格式
ARCHIVE_EXTENSIONS = {'.pdf', '.epub', '.cbz', '.cbr', '.zip'}

# 支持的图片格式
IMAGE_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS

ORIGINAL_IMAGE_DIRNAME = 'original_images'
ARCHIVE_SOURCE_MARKER_FILENAME = '.archive_source.txt'
EXTRACT_META_FILENAME = '.extract_meta.json'


def is_archive_file(file_path: str) -> bool:
    """检查文件是否是支持的压缩包/文档格式"""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in ARCHIVE_EXTENSIONS


def get_output_extract_dir(output_base_dir: str, archive_path: str) -> str:
    """获取解压到输出目录下的目录：<输出目录>/<文件名>/original_images"""
    archive_name = os.path.splitext(os.path.basename(archive_path))[0]
    return os.path.join(output_base_dir, archive_name, ORIGINAL_IMAGE_DIRNAME)

def get_output_extract_root(output_base_dir: str, archive_path: str) -> str:
    """获取解压根目录：<输出目录>/<文件名>"""
    archive_name = os.path.splitext(os.path.basename(archive_path))[0]
    return os.path.join(output_base_dir, archive_name)

def get_output_extract_marker_path(output_base_dir: str, archive_path: str) -> str:
    """获取压缩包来源标记文件路径。"""
    return os.path.join(
        get_output_extract_root(output_base_dir, archive_path),
        ARCHIVE_SOURCE_MARKER_FILENAME
    )

def _normalize_abs_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))

def _build_extract_meta(archive_path: str) -> dict:
    return {
        'archive_path': _normalize_abs_path(archive_path),
        'archive_mtime': int(os.path.getmtime(archive_path)) if os.path.exists(archive_path) else 0,
        'archive_size': int(os.path.getsize(archive_path)) if os.path.exists(archive_path) else 0,
    }

def _get_extract_meta_path(output_dir: str) -> str:
    return os.path.join(output_dir, EXTRACT_META_FILENAME)

def _read_extract_meta(output_dir: str) -> Optional[dict]:
    meta_path = _get_extract_meta_path(output_dir)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None

def _write_extract_meta(output_dir: str, archive_path: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    meta_path = _get_extract_meta_path(output_dir)
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(_build_extract_meta(archive_path), f, ensure_ascii=False, indent=2)

def _clear_extract_output_dir(output_dir: str) -> None:
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir, ignore_errors=True)
    os.makedirs(output_dir, exist_ok=True)

def check_output_extract_conflict(output_base_dir: str, archive_path: str) -> bool:
    """
    检查同名解压目录是否和当前压缩包冲突。
    True 表示存在冲突（同名目录但来源不是当前 archive_path）。
    """
    root_dir = get_output_extract_root(output_base_dir, archive_path)
    if not os.path.isdir(root_dir):
        return False

    marker_path = get_output_extract_marker_path(output_base_dir, archive_path)
    if not os.path.exists(marker_path):
        # 兼容旧版本：尝试读取解压目录元数据判断来源
        extract_dir = get_output_extract_dir(output_base_dir, archive_path)
        cached_meta = _read_extract_meta(extract_dir)
        if cached_meta and cached_meta.get('archive_path') == _normalize_abs_path(archive_path):
            return False
        # 没有可用元数据时保守视为冲突，避免误复用同名目录
        return True

    try:
        with open(marker_path, 'r', encoding='utf-8') as f:
            recorded_source = f.read().strip()
    except Exception:
        return True

    if not recorded_source:
        return True

    return _normalize_abs_path(recorded_source) != _normalize_abs_path(archive_path)

def clear_output_extract_root(output_base_dir: str, archive_path: str) -> None:
    """删除同名解压根目录（用于覆盖模式下的冲突处理）。"""
    root_dir = get_output_extract_root(output_base_dir, archive_path)
    if os.path.exists(root_dir):
        shutil.rmtree(root_dir, ignore_errors=True)

def write_output_extract_marker(output_base_dir: str, archive_path: str) -> None:
    """写入压缩包来源标记，用于识别同名目录冲突。"""
    marker_path = get_output_extract_marker_path(output_base_dir, archive_path)
    os.makedirs(os.path.dirname(marker_path), exist_ok=True)
    with open(marker_path, 'w', encoding='utf-8') as f:
        f.write(_normalize_abs_path(archive_path))


def get_temp_extract_dir(archive_path: str) -> str:
    """获取压缩包的临时解压目录"""
    # 使用系统临时目录下的固定子目录，便于管理
    base_temp = os.path.join(tempfile.gettempdir(), 'manga_translator_archives')
    os.makedirs(base_temp, exist_ok=True)
    
    # 使用文件名和修改时间生成唯一目录名
    archive_name = os.path.splitext(os.path.basename(archive_path))[0]
    mtime = int(os.path.getmtime(archive_path)) if os.path.exists(archive_path) else 0
    unique_name = f"{archive_name}_{mtime}"
    
    return os.path.join(base_temp, unique_name)


def extract_images_from_pdf(pdf_path: str, output_dir: str) -> List[str]:
    """从 PDF 文件中提取图片（优先提取嵌入原图，无嵌入图时回退渲染）"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("需要安装 PyMuPDF: pip install PyMuPDF")

    os.makedirs(output_dir, exist_ok=True)
    extracted_images = []
    img_count = 0

    doc = None
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            imgs = page.get_images(full=True)
            if imgs:
                # 提取页面内所有嵌入图片
                for img in imgs:
                    xref = img[0]
                    try:
                        base = doc.extract_image(xref)
                        img_count += 1
                        image_path = os.path.join(output_dir, f"page_{img_count:04d}.{base['ext']}")
                        with open(image_path, 'wb') as f:
                            f.write(base['image'])
                        extracted_images.append(image_path)
                    except Exception:
                        pass
            else:
                # 无嵌入图（纯文字/矢量页），回退渲染为 PNG
                try:
                    mat = fitz.Matrix(2.0, 2.0)
                    pix = page.get_pixmap(matrix=mat)
                    img_count += 1
                    image_path = os.path.join(output_dir, f"page_{img_count:04d}.png")
                    pix.save(image_path)
                    extracted_images.append(image_path)
                    pix = None
                except Exception:
                    pass
    finally:
        if doc is not None:
            doc.close()

    return sorted(extracted_images)


def extract_images_from_epub(epub_path: str, output_dir: str) -> List[str]:
    """从 EPUB 文件中提取图片"""
    os.makedirs(output_dir, exist_ok=True)
    extracted_images = []
    
    with zipfile.ZipFile(epub_path, 'r') as zf:
        for file_info in zf.infolist():
            ext = os.path.splitext(file_info.filename)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                # 提取图片，保持相对路径结构
                # 但简化文件名以避免路径过长
                base_name = os.path.basename(file_info.filename)
                # 添加序号前缀以保持顺序
                idx = len(extracted_images)
                new_name = f"{idx:04d}_{base_name}"
                output_path = os.path.join(output_dir, new_name)
                
                with zf.open(file_info) as src, open(output_path, 'wb') as dst:
                    dst.write(src.read())
                extracted_images.append(output_path)
    
    return sorted(extracted_images)


def extract_images_from_cbz(cbz_path: str, output_dir: str) -> List[str]:
    """从 CBZ (Comic Book ZIP) 文件中提取图片"""
    os.makedirs(output_dir, exist_ok=True)
    extracted_images = []
    
    with zipfile.ZipFile(cbz_path, 'r') as zf:
        # 获取所有图片文件并排序
        image_files = []
        for file_info in zf.infolist():
            if file_info.is_dir():
                continue
            ext = os.path.splitext(file_info.filename)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(file_info)
        
        # 按文件名自然排序
        image_files.sort(key=lambda x: natural_sort_key(x.filename))
        
        for idx, file_info in enumerate(image_files):
            base_name = os.path.basename(file_info.filename)
            # 添加序号前缀以保持顺序
            new_name = f"{idx:04d}_{base_name}"
            output_path = os.path.join(output_dir, new_name)
            
            with zf.open(file_info) as src, open(output_path, 'wb') as dst:
                dst.write(src.read())
            extracted_images.append(output_path)
    
    return extracted_images


def extract_images_from_cbr(cbr_path: str, output_dir: str) -> List[str]:
    """从 CBR (Comic Book RAR) 文件中提取图片"""
    try:
        import rarfile
    except ImportError:
        raise ImportError("需要安装 rarfile: pip install rarfile")
    
    os.makedirs(output_dir, exist_ok=True)
    extracted_images = []
    
    with rarfile.RarFile(cbr_path, 'r') as rf:
        image_files = []
        for file_info in rf.infolist():
            if file_info.is_dir():
                continue
            ext = os.path.splitext(file_info.filename)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(file_info)
        
        image_files.sort(key=lambda x: natural_sort_key(x.filename))
        
        for idx, file_info in enumerate(image_files):
            base_name = os.path.basename(file_info.filename)
            new_name = f"{idx:04d}_{base_name}"
            output_path = os.path.join(output_dir, new_name)
            
            with rf.open(file_info) as src, open(output_path, 'wb') as dst:
                dst.write(src.read())
            extracted_images.append(output_path)
    
    return extracted_images


def natural_sort_key(s: str):
    """自然排序键，支持数字排序"""
    import re
    return [int(text) if text.isdigit() else text.lower() 
            for text in re.split(r'(\d+)', s)]


def extract_images_from_archive(archive_path: str, output_dir: Optional[str] = None) -> Tuple[List[str], str]:
    """
    从压缩包/文档中提取图片
    
    Args:
        archive_path: 压缩包/文档路径
        output_dir: 输出目录，如果为 None 则使用临时目录
    
    Returns:
        (提取的图片路径列表, 输出目录)
    """
    if output_dir is None:
        output_dir = get_temp_extract_dir(archive_path)
    
    expected_meta = _build_extract_meta(archive_path)

    # 如果目录已存在且缓存元数据一致，直接返回缓存结果
    if os.path.exists(output_dir):
        existing_images = []
        for f in os.listdir(output_dir):
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                existing_images.append(os.path.join(output_dir, f))
        cached_meta = _read_extract_meta(output_dir)
        if existing_images and cached_meta == expected_meta:
            return sorted(existing_images), output_dir
        # 目录存在但缓存不可用（来源/版本不匹配或残留脏数据），清空后重解压
        _clear_extract_output_dir(output_dir)
    else:
        os.makedirs(output_dir, exist_ok=True)
    
    ext = os.path.splitext(archive_path)[1].lower()
    
    if ext == '.pdf':
        images = extract_images_from_pdf(archive_path, output_dir)
    elif ext == '.epub':
        images = extract_images_from_epub(archive_path, output_dir)
    elif ext in {'.cbz', '.zip'}:
        images = extract_images_from_cbz(archive_path, output_dir)
    elif ext == '.cbr':
        images = extract_images_from_cbr(archive_path, output_dir)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")

    _write_extract_meta(output_dir, archive_path)
    return images, output_dir


def cleanup_temp_archives():
    """清理所有临时解压目录"""
    base_temp = os.path.join(tempfile.gettempdir(), 'manga_translator_archives')
    if os.path.exists(base_temp):
        shutil.rmtree(base_temp, ignore_errors=True)


def cleanup_archive_temp(archive_path: str):
    """清理指定压缩包的临时解压目录"""
    temp_dir = get_temp_extract_dir(archive_path)
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
