import json
import os
import re
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)


def restore_translation_to_text(json_path: str) -> bool:
    """
    在加载文本+模板模式下，将翻译结果写回到原文字段
    确保模板模式输出翻译而不是原文
    
    Args:
        json_path: JSON文件路径
        
    Returns:
        bool: 是否有修改并成功写回
    """
    try:
        if not os.path.exists(json_path):
            logger.warning(f"JSON file not found: {json_path}")
            return False
            
        # 读取JSON文件
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        modified = False
        processed_regions = 0
        
        # 遍历所有图片的数据
        for image_key, image_data in data.items():
            if isinstance(image_data, dict) and 'regions' in image_data:
                regions = image_data['regions']
                
                for region in regions:
                    if isinstance(region, dict):
                        # 获取翻译和原文
                        translation = region.get('translation', '').strip()
                        original_text = region.get('text', '').strip()
                        
                        # 只有翻译不为空且与原文不同时才写回
                        if translation and translation != original_text:
                            # 将翻译写回到原文字段
                            region['text'] = translation
                            
                            # 同时更新texts数组
                            if 'texts' in region and isinstance(region['texts'], list):
                                if len(region['texts']) > 0:
                                    region['texts'][0] = translation
                                else:
                                    region['texts'] = [translation]
                            
                            modified = True
                            processed_regions += 1
                            logger.debug(f"Restored translation to text: '{original_text}' -> '{translation}'")
        
        # 如果有修改，写回文件
        if modified:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            
            logger.info(f"Processed {processed_regions} regions in {os.path.basename(json_path)}")
            
        return modified
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON format in {json_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error processing {json_path}: {e}")
        return False


def batch_process_json_folder(folder_path: str, pattern: str = "*_translations.json") -> Tuple[int, int]:
    """
    批量处理文件夹中的JSON文件，将翻译写回原文
    
    Args:
        folder_path: 文件夹路径
        pattern: 文件匹配模式
        
    Returns:
        Tuple[int, int]: (成功处理的文件数, 总文件数)
    """
    import glob
    
    if not os.path.isdir(folder_path):
        logger.warning(f"Folder not found: {folder_path}")
        return 0, 0
    
    # 查找所有匹配的JSON文件
    search_pattern = os.path.join(folder_path, "**", pattern)
    json_files = glob.glob(search_pattern, recursive=True)
    
    successful = 0
    total = len(json_files)
    
    logger.info(f"Found {total} JSON files in {folder_path}")
    
    for json_file in json_files:
        try:
            if restore_translation_to_text(json_file):
                successful += 1
                logger.info(f"Successfully processed: {os.path.basename(json_file)}")
            else:
                logger.debug(f"No changes needed for: {os.path.basename(json_file)}")
        except Exception as e:
            logger.error(f"Failed to process {json_file}: {e}")
    
    return successful, total


def process_json_file_list(file_paths: List[str]) -> Tuple[int, int]:
    """
    处理指定的图片文件列表，查找对应的JSON文件并处理翻译写回
    
    Args:
        file_paths: 图片文件路径列表
        
    Returns:
        Tuple[int, int]: (成功处理的文件数, 总文件数)
    """
    successful = 0
    total = 0
    
    for file_path in file_paths:
        # 生成对应的JSON文件路径
        json_path = os.path.splitext(file_path)[0] + "_translations.json"
        
        if os.path.exists(json_path):
            total += 1
            try:
                if restore_translation_to_text(json_path):
                    successful += 1
                    logger.info(f"Processed JSON for: {os.path.basename(file_path)}")
            except Exception as e:
                logger.error(f"Failed to process JSON for {file_path}: {e}")
        else:
            logger.debug(f"No JSON file found for: {os.path.basename(file_path)}")
    
    return successful, total


def should_restore_translation_to_text(load_text_enabled: bool, template_enabled: bool) -> bool:
    """
    检查是否应该执行翻译写回原文的预处理
    
    Args:
        load_text_enabled: 是否启用了加载文本模式
        template_enabled: 是否启用了模板模式
        
    Returns:
        bool: 是否应该处理
    """
    result = load_text_enabled and template_enabled
    logger.debug(f"DEBUG: should_restore_translation_to_text - load_text={load_text_enabled}, template={template_enabled}, result={result}")
    return result

def parse_template(template_string: str):
    """
    Parses a free-form text template to find prefix, suffix, item_template, and separator.
    An 'item' is defined as a line containing the <original> placeholder.
    """
    logger.debug(f"Parsing template:\n---\n{template_string[:200]}...\n---")
    # Find all lines containing <original>
    lines = template_string.splitlines(True) # Keep endings to preserve original spacing
    item_line_indices = [i for i, line in enumerate(lines) if "<original>" in line]

    if not item_line_indices:
        raise ValueError("Template must contain at least one '<original>' placeholder.")

    # Define the item_template from the first found item line
    first_item_line_index = item_line_indices[0]
    item_template = lines[first_item_line_index]

    # Define prefix
    prefix_lines = lines[:first_item_line_index]
    prefix = "".join(prefix_lines)

    # Define separator and suffix
    if len(item_line_indices) > 1:
        # Separator is the content between the first and second item lines
        second_item_line_index = item_line_indices[1]
        separator_lines = lines[first_item_line_index + 1 : second_item_line_index]
        separator = "".join(separator_lines)
        
        # Suffix is the content after the last item line
        last_item_line_index = item_line_indices[-1]
        suffix_lines = lines[last_item_line_index + 1:]
        suffix = "".join(suffix_lines)
    else:
        # Only one item, so no separator, and suffix is everything after it
        separator = ""
        suffix_lines = lines[first_item_line_index + 1:]
        suffix = "".join(suffix_lines)
    
    logger.debug(f"Parsed template parts: prefix='{prefix.strip()}', separator='{separator.strip()}', suffix='{suffix.strip()}'")
    return prefix, item_template, separator, suffix

def generate_text_from_template(
    detailed_json_path: str, 
    template_path: str
) -> str:
    """
    Generates a custom text format based on a free-form text template file.
    """
    try:
        with open(detailed_json_path, 'r', encoding='utf-8') as f:
            source_data = json.load(f)
        with open(template_path, 'r', encoding='utf-8') as f:
            template_string = f.read()
    except Exception as e:
        return f"Error reading source files: {e}"

    try:
        prefix, item_template, separator, suffix = parse_template(template_string)
    except ValueError as e:
        return f"Error parsing template: {e}"

    image_data = next(iter(source_data.values()), None)
    if not image_data or 'regions' not in image_data:
        return "Error: Could not find 'regions' list in source JSON."
    regions = image_data.get('regions', [])
    
    output_parts = []
    for region in regions:
        original_text = region.get('text', '')
        translated_text = region.get('translation', '')
        
        original_text_json = json.dumps(original_text, ensure_ascii=False)
        translated_text_json = json.dumps(translated_text, ensure_ascii=False)

        part = item_template.replace("<original>", original_text_json)
        part = part.replace("<translated>", translated_text_json)
        output_parts.append(part)

    # Join all parts. If there is content, join by separator. Otherwise, it's an empty string.
    if output_parts:
        final_body = separator.join(output_parts)
    else:
        final_body = ""

    final_content = prefix + final_body + suffix
    output_path = os.path.splitext(detailed_json_path)[0] + ".txt"

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
    except Exception as e:
        return f"Error writing to output file: {e}"
        
    return output_path

def get_template_path_from_config(custom_path: str = None) -> str:
    """
    获取模板文件路径，支持自定义路径
    
    Args:
        custom_path: 用户指定的自定义模板路径
        
    Returns:
        str: 最终使用的模板路径
    """
    # 优先级: 用户指定 > 环境变量 > 默认路径
    if custom_path and os.path.exists(custom_path):
        return custom_path
    
    env_template = os.environ.get('MANGA_TEMPLATE_PATH')
    if env_template and os.path.exists(env_template):
        return env_template
    
    return get_default_template_path()


def create_template_selection_dialog(parent=None):
    """
    创建模板选择对话框
    
    Args:
        parent: 父窗口
        
    Returns:
        str: 选择的模板文件路径，如果取消则返回None
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
        
        # 如果没有父窗口，创建一个隐藏的root窗口
        if parent is None:
            root = tk.Tk()
            root.withdraw()
            parent = root
        
        # 打开文件选择对话框
        template_path = filedialog.askopenfilename(
            parent=parent,
            title="选择翻译模板文件",
            filetypes=[
                ("JSON模板文件", "*.json"),
                ("文本模板文件", "*.txt"),
                ("所有文件", "*.*")
            ],
            initialdir=os.path.dirname(get_default_template_path())
        )
        
        return template_path if template_path else None
        
    except ImportError:
        logger.warning("无法导入tkinter，无法显示文件选择对话框")
        return None
    except Exception as e:
        logger.error(f"创建模板选择对话框失败: {e}")
        return None


def export_with_custom_template(
    json_path: str, 
    template_path: str = None,
    output_path: str = None
) -> str:
    """
    使用自定义模板导出翻译文件
    
    Args:
        json_path: JSON文件路径
        template_path: 模板文件路径
        output_path: 输出文件路径，如果为None则自动生成
        
    Returns:
        str: 导出结果或错误信息
    """
    if not os.path.exists(json_path):
        return f"错误：JSON文件不存在: {json_path}"
    
    # 获取模板路径
    final_template_path = get_template_path_from_config(template_path)
    if not os.path.exists(final_template_path):
        return f"错误：模板文件不存在: {final_template_path}"
    
    # 生成输出路径
    if output_path is None:
        base_name = os.path.splitext(json_path)[0]
        if base_name.endswith("_translations"):
            # 从 "image_translations.json" 生成 "image_translations.txt"
            output_path = base_name + ".txt"
        else:
            output_path = base_name + ".txt"
    
    try:
        result_path = generate_text_from_template(json_path, final_template_path)
        if result_path and os.path.exists(result_path):
            return f"成功导出到: {result_path}"
        else:
            return f"导出失败: {result_path}"
    except Exception as e:
        return f"导出过程中出错: {e}"


def import_with_custom_template(
    txt_path: str,
    json_path: str = None, 
    template_path: str = None
) -> str:
    """
    使用自定义模板从TXT文件导入翻译到JSON
    
    Args:
        txt_path: TXT文件路径
        json_path: JSON文件路径，如果为None则自动推断
        template_path: 模板文件路径
        
    Returns:
        str: 导入结果或错误信息
    """
    if not os.path.exists(txt_path):
        return f"错误：TXT文件不存在: {txt_path}"
    
    # 自动推断JSON路径
    if json_path is None:
        base_name = os.path.splitext(txt_path)[0]
        json_path = base_name + ".json"
    
    if not os.path.exists(json_path):
        return f"错误：JSON文件不存在: {json_path}"
    
    # 获取模板路径
    final_template_path = get_template_path_from_config(template_path)
    if not os.path.exists(final_template_path):
        return f"错误：模板文件不存在: {final_template_path}"
    
    try:
        result = safe_update_large_json_from_text(txt_path, json_path, final_template_path)
        return result
    except Exception as e:
        return f"导入过程中出错: {e}"


def get_default_template_path() -> str:
    """获取默认模板文件路径"""
    return os.path.join(os.path.dirname(__file__), "..", "..", "examples", "translation_template.json")


def ensure_default_template_exists() -> str:
    """
    确保默认模板文件存在，如果不存在则自动创建
    
    Returns:
        str: 模板文件路径
    """
    template_path = get_default_template_path()
    
    if not os.path.exists(template_path):
        # 创建目录（如果不存在）
        template_dir = os.path.dirname(template_path)
        os.makedirs(template_dir, exist_ok=True)
        
        # 创建默认模板内容
        default_template_content = '''翻译模板文件

原文: <original>
译文: <translated>

'''
        
        try:
            with open(template_path, 'w', encoding='utf-8') as f:
                f.write(default_template_content)
            logger.info(f"Created default template at: {template_path}")
        except Exception as e:
            logger.error(f"Failed to create default template: {e}")
            return None
    
    return template_path


def smart_update_translations_from_images(
    image_file_paths: List[str],
    template_path: str = None
) -> str:
    """
    根据加载的图片文件路径，智能匹配对应的JSON和TXT文件进行翻译更新
    
    Args:
        image_file_paths: 图片文件路径列表
        template_path: 模板文件路径，如果为None则使用默认模板
        
    Returns:
        str: 处理结果报告
    """
    if not image_file_paths:
        return "错误：未提供图片文件路径"
    
    # 使用默认模板如果未指定，并确保模板文件存在
    if template_path is None:
        template_path = ensure_default_template_exists()
        if template_path is None:
            return "错误：无法创建或找到默认模板文件"
    
    if not os.path.exists(template_path):
        return f"错误：模板文件不存在: {template_path}"
    
    results = []
    
    for image_path in image_file_paths:
        if not os.path.exists(image_path):
            results.append(f"✗ {os.path.basename(image_path)}: 图片文件不存在")
            continue
        
        # 推理对应的JSON和TXT文件路径
        # 图片: "image.jpg" -> JSON: "image_translations.json", TXT: "image_translations.txt"
        base_name = os.path.splitext(image_path)[0]  # 去除扩展名得到"image"
        json_path = base_name + "_translations.json"
        txt_path = base_name + "_translations.txt"  # TXT和JSON同名，只是扩展名不同
        
        # 检查文件存在性
        json_exists = os.path.exists(json_path)
        txt_exists = os.path.exists(txt_path)
        
        if not json_exists:
            results.append(f"- {os.path.basename(image_path)}: 未找到JSON文件 ({os.path.basename(json_path)})")
            continue
            
        if not txt_exists:
            results.append(f"- {os.path.basename(image_path)}: 未找到TXT文件 ({os.path.basename(txt_path)})")
            continue
        
        # 执行翻译更新
        try:
            result = safe_update_large_json_from_text(txt_path, json_path, template_path)
            results.append(f"✓ {os.path.basename(image_path)}: {result}")
        except Exception as e:
            results.append(f"✗ {os.path.basename(image_path)}: 更新失败 - {e}")
    
    if not results:
        return "未找到任何可处理的文件"
    
    # 统计结果
    successful = len([r for r in results if r.startswith("✓")])
    total = len(results)
    
    summary = f"批量翻译更新完成 (成功: {successful}/{total}):\n" + "\n".join(results)
    return summary


def auto_detect_and_update_translations(
    directory_or_files,
    template_path: str = None
) -> str:
    """
    自动检测并更新翻译 - 支持目录或文件列表
    
    Args:
        directory_or_files: 目录路径(str) 或 图片文件路径列表(List[str])
        template_path: 模板文件路径
        
    Returns:
        str: 处理结果报告
    """
    if isinstance(directory_or_files, str):
        # 如果是目录路径，扫描目录中的图片文件
        if os.path.isdir(directory_or_files):
            import glob
            
            # 常见图片格式
            image_extensions = ['*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tiff', '*.webp']
            image_files = []
            
            for ext in image_extensions:
                pattern = os.path.join(directory_or_files, "**", ext)
                image_files.extend(glob.glob(pattern, recursive=True))
                # 也搜索大写扩展名
                pattern = os.path.join(directory_or_files, "**", ext.upper())
                image_files.extend(glob.glob(pattern, recursive=True))
            
            if not image_files:
                return f"在目录 {directory_or_files} 中未找到任何图片文件"
            
            return smart_update_translations_from_images(image_files, template_path)
        else:
            return f"错误：目录不存在: {directory_or_files}"
    
    elif isinstance(directory_or_files, list):
        # 如果是文件列表，直接处理
        return smart_update_translations_from_images(directory_or_files, template_path)
    
    else:
        return "错误：参数类型不正确，需要目录路径或图片文件路径列表"


def _load_large_json_optimized(json_file_path: str):
    """优化的大文件JSON加载"""
    import ijson
    try:
        # 使用ijson进行流式解析，并立即物化为字典
        with open(json_file_path, 'rb') as f:
            return dict(ijson.kvitems(f, ''))
    except ImportError:
        # 如果没有ijson，回退到标准方法但分块读取
        logger.warning("ijson不可用，使用标准方法读取大文件")
        with open(json_file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

def safe_update_large_json_from_text(
    text_file_path: str,
    json_file_path: str,
    template_path: str
) -> str:
    """
    安全地更新大型JSON文件，保护原始数据完整性
    """
    logger.debug(f"Starting safe update. TXT: '{os.path.basename(text_file_path)}', JSON: '{os.path.basename(json_file_path)}'")
    import gc
    import time
    import shutil
    import tempfile
    from datetime import datetime
    
    # 检查文件存在
    for file_path, name in [(text_file_path, "TXT"), (json_file_path, "JSON"), (template_path, "模板")]:
        if not os.path.exists(file_path):
            return f"错误：{name}文件不存在: {file_path}"
    
    # 获取文件大小信息
    json_size_mb = os.path.getsize(json_file_path) / (1024 * 1024)
    logger.info(f"处理JSON文件: {os.path.basename(json_file_path)} ({json_size_mb:.2f} MB)")
    
    try:
        # 1. 解析模板和TXT文件
        logger.debug("Reading template and text files.")
        with open(template_path, 'r', encoding='utf-8') as f:
            template_string = f.read()
        with open(text_file_path, 'r', encoding='utf-8') as f:
            text_content = f.read()
    except Exception as e:
        return f"错误：读取输入文件失败: {e}"

    try:
        prefix, item_template, separator, suffix = parse_template(template_string)
    except ValueError as e:
        return f"错误：解析模板失败: {e}"

    # 2. 解析翻译内容
    logger.debug("Parsing translations from text content.")
    translations = {}
    try:
        # 移除前缀和后缀
        if prefix and text_content.startswith(prefix):
            text_content = text_content[len(prefix):]
        if suffix and text_content.endswith(suffix):
            text_content = text_content[:-len(suffix)]

        # 分割条目
        if separator:
            items = text_content.split(separator)
        else:
            items = [text_content] if text_content.strip() else []
        logger.debug(f"Found {len(items)} items in text file.")

        # 解析每个条目
        parts = re.split(f'({re.escape("<original>")}|{re.escape("<translated>")})', item_template)
        parser_regex_str = ""
        group_order = []
        for part in parts:
            if part == "<original>":
                parser_regex_str += "(.+?)"
                group_order.append("original")
            elif part == "<translated>":
                parser_regex_str += "(.+?)"
                group_order.append("translated")
            else:
                parser_regex_str += re.escape(part)

        parser_regex = re.compile(parser_regex_str, re.DOTALL)

        for item in items:
            item_stripped = item.strip()
            if not item_stripped:
                continue

            match = parser_regex.search(item)
            if match:
                try:
                    result = {}
                    for j, group_name in enumerate(group_order):
                        captured_string = match.group(j + 1)
                        result[group_name] = json.loads(captured_string)
                    translations[result['original']] = result['translated']
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue  # 跳过解析失败的条目

    except Exception as e:
        return f"错误：解析TXT文件失败: {e}"

    if not translations:
        logger.warning(f"Could not parse any translations from '{os.path.basename(text_file_path)}'.")
        return "错误：未能从TXT文件中解析出任何翻译内容"

    logger.info(f"解析出 {len(translations)} 条翻译")

    # 3. 创建临时备份文件
    backup_path = None
    temp_path = None
    try:
        # 创建备份
        # timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # backup_path = f"{json_file_path}.backup_{timestamp}"
        # shutil.copy2(json_file_path, backup_path)
        # logger.info(f"创建备份: {os.path.basename(backup_path)}")

        # 4. 使用内存优化的方式加载和更新JSON
        gc.collect()
        start_time = time.time()
        
        # 对于大文件使用流式处理以减少内存占用
        if json_size_mb > 50:  # 大于50MB使用优化处理
            logger.debug(f"使用流式处理加载大文件: {os.path.basename(json_file_path)}")
            source_data = _load_large_json_optimized(json_file_path)
        else:
            logger.debug(f"Loading JSON file into memory: {os.path.basename(json_file_path)}")
            with open(json_file_path, 'r', encoding='utf-8') as f:
                source_data = json.load(f)
        
        load_time = time.time() - start_time
        logger.info(f"JSON加载完成，耗时 {load_time:.2f} 秒")

        # 5. 更新翻译内容
        logger.debug("Updating translations in memory.")
        updated_count = 0
        image_key = next(iter(source_data.keys()), None)
        
        if not image_key or 'regions' not in source_data[image_key]:
            return "错误：JSON文件格式不正确，找不到regions数据"

        start_time = time.time()
        
        for region in source_data[image_key]['regions']:
            original_text = region.get('text', '')
            if original_text in translations:
                old_translation = region.get('translation', '')
                new_translation = translations[original_text]
                
                # 只有当翻译实际改变时才更新
                if old_translation != new_translation:
                    region['translation'] = new_translation
                    updated_count += 1

        update_time = time.time() - start_time
        logger.info(f"更新完成，耗时 {update_time:.2f} 秒，更新了 {updated_count} 条")

        # 6. 写回文件（使用临时文件确保原子性）
        logger.debug("Writing updated data to temporary file.")
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, 
                                       dir=os.path.dirname(json_file_path), 
                                       suffix='.tmp') as temp_file:
            temp_path = temp_file.name
            
            # 使用优化的JSON编码器
            class OptimizedJSONEncoder(json.JSONEncoder):
                def default(self, obj):
                    if hasattr(obj, 'tolist'):  # numpy数组
                        return obj.tolist()
                    if hasattr(obj, '__int__'):  # numpy整数
                        return int(obj)
                    if hasattr(obj, '__float__'):  # numpy浮点数
                        return float(obj)
                    return super().default(obj)
            
            start_time = time.time()
            json.dump(source_data, temp_file, ensure_ascii=False, indent=4, 
                     cls=OptimizedJSONEncoder)
        
        write_time = time.time() - start_time
        logger.info(f"临时文件写入完成，耗时 {write_time:.2f} 秒")

        # 7. 原子性替换原文件
        logger.debug(f"Atomically moving temporary file to final destination: {os.path.basename(json_file_path)}")
        if os.name == 'nt':  # Windows
            # Windows需要先删除目标文件
            if os.path.exists(json_file_path):
                os.remove(json_file_path)
        
        shutil.move(temp_path, json_file_path)
        temp_path = None  # 标记已经移动，避免重复删除
        
        # 8. 清理内存
        logger.debug("Clearing source data from memory.")
        del source_data
        gc.collect()
        
        # 9. 验证文件完整性
        try:
            logger.debug("Verifying integrity of written JSON file.")
            with open(json_file_path, 'r', encoding='utf-8') as f:
                json.load(f)
            logger.info("文件完整性验证通过")
        except:
            # 如果验证失败，恢复备份
            logger.error("File integrity check failed! Restoring backup.")
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, json_file_path)
                return f"错误：文件写入后验证失败，已恢复备份。请检查磁盘空间和文件权限。"
        
        # 10. 清理旧备份（可选，保留最近3个备份）
        try:
            logger.debug("Cleaning up old backups.")
            backup_pattern = f"{json_file_path}.backup_*"
            backup_files = sorted(glob.glob(backup_pattern), reverse=True)
            for old_backup in backup_files[3:]:  # 保留最近3个备份
                try:
                    os.remove(old_backup)
                    logger.debug(f"删除旧备份: {os.path.basename(old_backup)}")
                except:
                    pass
        except:
            pass

        return f"成功更新 {updated_count} 条翻译 (总时间: {load_time + update_time + write_time:.2f}秒)"

    except Exception as e:
        # 错误恢复
        error_msg = f"错误：更新过程中出现异常: {e}"
        
        # 清理临时文件
        if temp_path and os.path.exists(temp_path):
            try:
                logger.debug(f"Cleaning up temporary file: {temp_path}")
                os.remove(temp_path)
            except:
                pass
        
        # 尝试恢复备份
        if backup_path and os.path.exists(backup_path):
            try:
                logger.warning("Exception occurred, attempting to restore backup.")
                shutil.copy2(backup_path, json_file_path)
                error_msg += " (已恢复备份文件)"
            except:
                error_msg += " (备份恢复失败，请手动恢复)"
        
        logger.error(error_msg)
        return error_msg

    finally:
        # 强制垃圾回收
        logger.debug("Running final garbage collection.")
        gc.collect()


def batch_update_directory_translations(
    directory_path: str,
    template_path: str = None,
    pattern: str = "*_translations.json"
) -> str:
    """
    批量更新目录中所有JSON文件的翻译
    
    Args:
        directory_path: 目录路径
        template_path: 模板文件路径
        pattern: JSON文件匹配模式
        
    Returns:
        str: 批量处理结果报告
    """
    logger.debug(f"Starting batch update in directory: '{directory_path}' with pattern '{pattern}'")
    import glob

    if not os.path.isdir(directory_path):
        return f"错误：目录不存在: {directory_path}"

    # 使用默认模板如果未指定，并确保模板文件存在
    if template_path is None:
        logger.debug("No template path provided, using default.")
        template_path = ensure_default_template_exists()
        if template_path is None:
            return "错误：无法创建或找到默认模板文件"

    if not os.path.exists(template_path):
        return f"错误：模板文件不存在: {template_path}"
    logger.debug(f"Using template: {template_path}")

    search_pattern = os.path.join(directory_path, "**", pattern)
    json_files = glob.glob(search_pattern, recursive=True)
    logger.debug(f"Found {len(json_files)} JSON files: {json_files}")

    if not json_files:
        return f"在目录 {directory_path} 中未找到匹配 '{pattern}' 的JSON文件"

    results = []
    for json_path in json_files:
        logger.debug(f"Processing file: {json_path}")
        # 从JSON路径推断TXT路径
        txt_path = os.path.splitext(json_path)[0] + ".txt"

        if not os.path.exists(txt_path):
            logger.warning(f"Could not find matching TXT file for '{os.path.basename(json_path)}', skipping.")
            results.append(f"- {os.path.basename(json_path)}: 未找到对应的TXT文件 ({os.path.basename(txt_path)})")
            continue
        
        try:
            result = safe_update_large_json_from_text(txt_path, json_path, template_path)
            results.append(f"✓ {os.path.basename(json_path)}: {result}")
        except Exception as e:
            logger.error(f"An exception occurred while processing '{os.path.basename(json_path)}': {e}", exc_info=True)
            results.append(f"✗ {os.path.basename(json_path)}: 更新失败 - {e}")

    successful = len([r for r in results if r.startswith("✓")])
    total = len(json_files)
    summary = f"批量更新完成 (处理: {successful}/{total}):\n" + "\n".join(results)
    logger.debug(f"Batch update summary:\n{summary}")
    return summary






