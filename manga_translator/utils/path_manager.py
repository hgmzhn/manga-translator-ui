#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路径管理模块
提供统一的文件路径生成和查找功能，支持新的目录结构和向后兼容
"""

import os
from typing import Optional, Tuple

# 工作目录名称常量
WORK_DIR_NAME = "manga_translator_work"
JSON_SUBDIR = "json"
TRANSLATIONS_SUBDIR = "translations"
ORIGINALS_SUBDIR = "originals"
YOLO_LABELS_SUBDIR = "yolo_labels"
INPAINTED_SUBDIR = "inpainted"
TRANSLATED_IMAGES_SUBDIR = "translated_images"  # 已翻译图片目录（替换翻译模式使用）
EDITOR_BASE_SUBDIR = "editor_base"
WORK_DIR_RESERVED_NAMES = {
    JSON_SUBDIR,
    TRANSLATIONS_SUBDIR,
    ORIGINALS_SUBDIR,
    YOLO_LABELS_SUBDIR,
    INPAINTED_SUBDIR,
    TRANSLATED_IMAGES_SUBDIR,
    EDITOR_BASE_SUBDIR,
}


def normalize_image_path(image_path: str) -> str:
    """规范化图片路径。"""
    return os.path.normpath(os.path.abspath(image_path))


def is_work_image_path(image_path: str) -> bool:
    """
    判断路径是否是编辑器专用的上色/超分底图。
    """
    norm_path = normalize_image_path(image_path)
    parent_dir = os.path.dirname(norm_path)
    grandparent_dir = os.path.dirname(parent_dir)

    # 新结构：manga_translator_work/editor_base/xxx.png
    if (
        os.path.basename(parent_dir) == EDITOR_BASE_SUBDIR and
        os.path.basename(grandparent_dir) == WORK_DIR_NAME
    ):
        return True

    # 兼容之前已经落在根目录的临时底图
    if os.path.basename(parent_dir) == WORK_DIR_NAME:
        return os.path.basename(norm_path) not in WORK_DIR_RESERVED_NAMES

    return False


def resolve_original_image_path(image_path: str) -> str:
    """
    将工作目录中的统一底图路径还原为原图路径，其它路径保持原样。
    """
    norm_path = normalize_image_path(image_path)
    if not is_work_image_path(norm_path):
        return norm_path

    parent_dir = os.path.dirname(norm_path)
    grandparent_dir = os.path.dirname(parent_dir)

    if (
        os.path.basename(parent_dir) == EDITOR_BASE_SUBDIR and
        os.path.basename(grandparent_dir) == WORK_DIR_NAME
    ):
        source_dir = os.path.dirname(grandparent_dir)
        return os.path.join(source_dir, os.path.basename(norm_path))

    source_dir = os.path.dirname(parent_dir)
    return os.path.join(source_dir, os.path.basename(norm_path))


def get_work_dir(image_path: str) -> str:
    """
    获取图片对应的工作目录路径
    
    Args:
        image_path: 原图片路径
        
    Returns:
        工作目录的绝对路径
    """
    image_dir = os.path.dirname(resolve_original_image_path(image_path))
    return os.path.join(image_dir, WORK_DIR_NAME)


def get_work_image_path(image_path: str, create_dir: bool = True) -> str:
    """
    获取编辑器专用的上色/超分底图路径。
    """
    if is_work_image_path(image_path):
        work_image_path = normalize_image_path(image_path)
        if create_dir:
            os.makedirs(os.path.dirname(work_image_path), exist_ok=True)
        return work_image_path

    original_path = resolve_original_image_path(image_path)
    work_dir = get_work_dir(original_path)
    editor_base_dir = os.path.join(work_dir, EDITOR_BASE_SUBDIR)
    if create_dir:
        os.makedirs(editor_base_dir, exist_ok=True)
    return os.path.join(editor_base_dir, os.path.basename(original_path))


def find_work_image_path(image_path: str) -> Optional[str]:
    """查找编辑器专用的上色/超分底图。"""
    work_image_path = get_work_image_path(image_path, create_dir=False)
    if os.path.exists(work_image_path):
        return work_image_path

    # 兼容之前可能已经落在根目录的底图
    original_path = resolve_original_image_path(image_path)
    legacy_root_work_image = os.path.join(get_work_dir(original_path), os.path.basename(original_path))
    if os.path.exists(legacy_root_work_image):
        return legacy_root_work_image

    return None


def get_legacy_inpainted_path(image_path: str, create_dir: bool = True) -> str:
    """
    获取旧版修复图路径（manga_translator_work/inpainted/*_inpainted.ext）。
    """
    original_path = resolve_original_image_path(image_path)
    work_dir = get_work_dir(original_path)
    inpainted_dir = os.path.join(work_dir, INPAINTED_SUBDIR)

    if create_dir:
        os.makedirs(inpainted_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(original_path))[0]
    ext = os.path.splitext(original_path)[1]
    return os.path.join(inpainted_dir, f"{base_name}_inpainted{ext}")


def get_json_path(image_path: str, create_dir: bool = True) -> str:
    """
    获取JSON配置文件的路径
    
    Args:
        image_path: 原图片路径
        create_dir: 是否自动创建目录
        
    Returns:
        JSON文件的绝对路径
    """
    work_dir = get_work_dir(image_path)
    json_dir = os.path.join(work_dir, JSON_SUBDIR)
    
    if create_dir:
        os.makedirs(json_dir, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(json_dir, f"{base_name}_translations.json")


def get_original_txt_path(image_path: str, create_dir: bool = True) -> str:
    """
    获取原文TXT文件的路径
    
    Args:
        image_path: 原图片路径
        create_dir: 是否自动创建目录
        
    Returns:
        原文TXT文件的绝对路径
    """
    work_dir = get_work_dir(image_path)
    originals_dir = os.path.join(work_dir, ORIGINALS_SUBDIR)
    
    if create_dir:
        os.makedirs(originals_dir, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(originals_dir, f"{base_name}_original.txt")


def get_translated_txt_path(image_path: str, create_dir: bool = True) -> str:
    """
    获取翻译TXT文件的路径
    
    Args:
        image_path: 原图片路径
        create_dir: 是否自动创建目录
        
    Returns:
        翻译TXT文件的绝对路径
    """
    work_dir = get_work_dir(image_path)
    translations_dir = os.path.join(work_dir, TRANSLATIONS_SUBDIR)
    
    if create_dir:
        os.makedirs(translations_dir, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(translations_dir, f"{base_name}_translated.txt")


def get_target_translated_txt_path(image_path: str, lang: str, create_dir: bool = True) -> str:
    """
    获取目标语言翻译TXT文件的路径
    
    Args:
        image_path: 原图片路径
        lang: 目标语言
        create_dir: 是否自动创建目录
        
    Returns:
        目标语言翻译TXT文件的绝对路径
    """
    work_dir = get_work_dir(image_path)
    translations_dir = os.path.join(work_dir, TRANSLATIONS_SUBDIR)

    if create_dir:
        os.makedirs(translations_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(translations_dir, f"{base_name}_{lang}_translated.txt")

def get_yolo_labels_dir(image_path: str, create_dir: bool = True) -> str:
    """
    获取 YOLO 标注目录路径。

    Args:
        image_path: 原图片路径
        create_dir: 是否自动创建目录

    Returns:
        YOLO 标注目录的绝对路径
    """
    work_dir = get_work_dir(image_path)
    yolo_labels_dir = os.path.join(work_dir, YOLO_LABELS_SUBDIR)

    if create_dir:
        os.makedirs(yolo_labels_dir, exist_ok=True)

    return yolo_labels_dir


def get_yolo_label_path(image_path: str, create_dir: bool = True) -> str:
    """
    获取图片对应的 YOLO 标注文件路径。

    Args:
        image_path: 原图片路径
        create_dir: 是否自动创建目录

    Returns:
        YOLO 标注文件的绝对路径
    """
    yolo_labels_dir = get_yolo_labels_dir(image_path, create_dir=create_dir)
    base_name = os.path.splitext(os.path.basename(resolve_original_image_path(image_path)))[0]
    return os.path.join(yolo_labels_dir, f"{base_name}.txt")


def find_yolo_label_path(image_path: str) -> Optional[str]:
    """
    查找图片对应的 YOLO 标注文件。

    Args:
        image_path: 原图片路径

    Returns:
        找到的 YOLO 标注文件路径，如果不存在返回 None
    """
    original_path = resolve_original_image_path(image_path)
    yolo_label_path = get_yolo_label_path(original_path, create_dir=False)
    if os.path.exists(yolo_label_path):
        return yolo_label_path

    legacy_yolo_label_path = os.path.splitext(original_path)[0] + ".txt"
    if os.path.exists(legacy_yolo_label_path):
        return legacy_yolo_label_path

    return None


def get_inpainted_path(image_path: str, create_dir: bool = True) -> str:
    """
    获取修复后图片的路径
    
    Args:
        image_path: 原图片路径
        create_dir: 是否自动创建目录
        
    Returns:
        修复后图片的绝对路径
    """
    return get_legacy_inpainted_path(image_path, create_dir=create_dir)


def get_translated_images_dir(image_path: str, create_dir: bool = True) -> str:
    """
    获取已翻译图片目录的路径
    
    Args:
        image_path: 原图片路径
        create_dir: 是否自动创建目录
        
    Returns:
        已翻译图片目录的绝对路径
    """
    work_dir = get_work_dir(image_path)
    translated_dir = os.path.join(work_dir, TRANSLATED_IMAGES_SUBDIR)
    
    if create_dir:
        os.makedirs(translated_dir, exist_ok=True)
    
    return translated_dir


def find_translated_source_json(target_image_path: str, translated_dir: str) -> Optional[str]:
    """
    在已翻译图片目录中查找与目标图同名的翻译数据JSON
    
    用于替换翻译模式：根据生肉图的文件名，在已翻译目录中查找同名图片的JSON
    
    Args:
        target_image_path: 目标图片（生肉）的路径
        translated_dir: 已翻译图片所在目录
        
    Returns:
        找到的JSON文件路径，如果不存在返回None
    """
    if not translated_dir or not os.path.isdir(translated_dir):
        return None
    
    # 获取目标图的基础文件名（不含扩展名）
    target_basename = os.path.splitext(os.path.basename(target_image_path))[0]
    
    # 在已翻译目录中查找同名图片
    # 尝试查找 manga_translator_work/json/文件名_translations.json
    translated_work_dir = os.path.join(translated_dir, WORK_DIR_NAME, JSON_SUBDIR)
    if os.path.isdir(translated_work_dir):
        json_path = os.path.join(translated_work_dir, f"{target_basename}_translations.json")
        if os.path.exists(json_path):
            return json_path
    
    # 向后兼容：查找 已翻译目录/文件名_translations.json
    old_json_path = os.path.join(translated_dir, f"{target_basename}_translations.json")
    if os.path.exists(old_json_path):
        return old_json_path
    
    # 尝试匹配任意图片扩展名
    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']:
        # 构造可能的已翻译图片路径
        possible_translated_image = os.path.join(translated_dir, f"{target_basename}{ext}")
        if os.path.exists(possible_translated_image):
            # 查找该图片对应的JSON
            json_path = find_json_path(possible_translated_image)
            if json_path:
                return json_path
    
    return None


def find_json_path(image_path: str) -> Optional[str]:
    """
    查找JSON配置文件，优先查找新位置，支持向后兼容
    
    Args:
        image_path: 原图片路径
        
    Returns:
        找到的JSON文件路径，如果不存在返回None
    """
    original_path = resolve_original_image_path(image_path)

    # 1. 优先查找新位置
    new_json_path = get_json_path(original_path, create_dir=False)
    if os.path.exists(new_json_path):
        return new_json_path
    
    # 2. 向后兼容：查找旧位置（图片同目录）
    old_json_path = os.path.splitext(original_path)[0] + '_translations.json'
    if os.path.exists(old_json_path):
        return old_json_path
    
    return None


def find_inpainted_path(image_path: str) -> Optional[str]:
    """
    查找修复后的图片文件
    
    Args:
        image_path: 原图片路径
        
    Returns:
        找到的修复后图片路径，如果不存在返回None
    """
    inpainted_path = get_inpainted_path(image_path, create_dir=False)
    if os.path.exists(inpainted_path):
        return inpainted_path
    
    return None


def find_txt_files(image_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    查找原文和翻译TXT文件
    
    Args:
        image_path: 原图片路径
        
    Returns:
        (原文TXT路径, 翻译TXT路径)，不存在的返回None
    """
    original_path = get_original_txt_path(image_path, create_dir=False)
    translated_path = get_translated_txt_path(image_path, create_dir=False)
    
    original_exists = original_path if os.path.exists(original_path) else None
    translated_exists = translated_path if os.path.exists(translated_path) else None
    
    # 向后兼容：查找旧格式的TXT文件
    if not translated_exists:
        old_txt_path = os.path.splitext(image_path)[0] + '_translations.txt'
        if os.path.exists(old_txt_path):
            translated_exists = old_txt_path
    
    return original_exists, translated_exists

def find_text_files_with_lang(image_path: str, lang: str) -> Tuple[Optional[str], Optional[str]]:
    """
    查找原文和翻译TXT文件
    
    Args:
        image_path: 原图片路径
        
    Returns:
        (原文TXT路径, 翻译TXT路径)，不存在的返回None
    """
    original_path = get_original_txt_path(image_path, create_dir=False)
    translated_path = get_translated_txt_path(image_path, create_dir=False)
    target_translated_path = get_translated_txt_path(image_path, create_dir=False, lang=lang)
    
    original_exists = original_path if os.path.exists(original_path) else None
    translated_exists = translated_path if os.path.exists(translated_path) else None
    target_translated_exists = target_translated_path if os.path.exists(target_translated_path) else None
    
    # 向后兼容：查找旧格式的TXT文件
    if not translated_exists:
        old_txt_path = os.path.splitext(image_path)[0] + '_translations.txt'
        if os.path.exists(old_txt_path):
            translated_exists = old_txt_path
    
    return original_exists, translated_exists, target_translated_exists


def get_legacy_json_path(image_path: str) -> str:
    """
    获取旧版JSON文件路径（图片同目录）
    用于向后兼容
    
    Args:
        image_path: 原图片路径
        
    Returns:
        旧版JSON文件路径
    """
    return os.path.splitext(image_path)[0] + '_translations.json'


def migrate_legacy_files(image_path: str, move_files: bool = False) -> dict:
    """
    迁移旧版文件到新目录结构
    
    Args:
        image_path: 原图片路径
        move_files: 是否移动文件（True）还是复制文件（False）
        
    Returns:
        迁移结果字典，包含成功和失败的文件列表
    """
    import shutil
    
    result = {
        'success': [],
        'failed': [],
        'skipped': []
    }
    
    # 检查并迁移JSON文件
    old_json = get_legacy_json_path(image_path)
    if os.path.exists(old_json):
        new_json = get_json_path(image_path, create_dir=True)
        if not os.path.exists(new_json):
            try:
                if move_files:
                    shutil.move(old_json, new_json)
                else:
                    shutil.copy2(old_json, new_json)
                result['success'].append(('json', old_json, new_json))
            except Exception as e:
                result['failed'].append(('json', old_json, str(e)))
        else:
            result['skipped'].append(('json', old_json, 'target exists'))
    
    # 检查并迁移旧版TXT文件
    old_txt = os.path.splitext(image_path)[0] + '_translations.txt'
    if os.path.exists(old_txt):
        new_txt = get_translated_txt_path(image_path, create_dir=True)
        if not os.path.exists(new_txt):
            try:
                if move_files:
                    shutil.move(old_txt, new_txt)
                else:
                    shutil.copy2(old_txt, new_txt)
                result['success'].append(('txt', old_txt, new_txt))
            except Exception as e:
                result['failed'].append(('txt', old_txt, str(e)))
        else:
            result['skipped'].append(('txt', old_txt, 'target exists'))
    
    return result
