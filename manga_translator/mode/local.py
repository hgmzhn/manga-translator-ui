#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
命令行翻译工具 - 直接使用 UI 层的翻译逻辑
"""
import os
import sys
import argparse
import asyncio
from pathlib import Path

# 添加项目根目录到 Python 路径
ROOT_DIR = Path(__file__).parent.parent.parent  # 上两级目录
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / 'desktop_qt_ui'))


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='漫画翻译命令行工具 - 使用与 UI 相同的翻译逻辑',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 翻译单个图片
  python -m manga_translator local -i manga.jpg
  
  # 翻译文件夹
  python -m manga_translator local -i ./manga_folder/ -o ./output/
  
  # 使用自定义配置
  python -m manga_translator local -i manga.jpg --config my_config.json
  
  # 详细日志
  python -m manga_translator local -i manga.jpg -v
        """
    )
    
    parser.add_argument('-i', '--input', required=True, nargs='+',
                        help='输入图片或文件夹路径')
    parser.add_argument('-o', '--output', default=None,
                        help='输出目录（默认：同目录加 -translated 后缀）')
    parser.add_argument('--config', default=None,
                        help='配置文件路径（默认：examples/config.json）')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='显示详细日志')
    parser.add_argument('--overwrite', action='store_true',
                        help='覆盖已存在的文件')
    
    return parser.parse_args()


async def translate_files(input_paths, output_dir, config_service, verbose=False, overwrite=False):
    """翻译文件（使用 UI 层的逻辑）"""
    
    # 延迟导入，避免 --help 时加载所有模块
    from desktop_qt_ui.services.file_service import FileService
    from manga_translator import MangaTranslator, Config
    from manga_translator.utils import init_logging, set_log_level, get_logger
    from PIL import Image
    import logging
    
    init_logging()
    if verbose:
        set_log_level(logging.DEBUG)
    else:
        set_log_level(logging.INFO)
    
    # 确保 manga_translator 的日志也输出
    manga_logger = logging.getLogger('manga_translator')
    manga_logger.setLevel(logging.INFO)
    
    logger = get_logger('local')
    
    # 获取配置
    config = config_service.get_config()
    config_dict = config.dict()
    
    # 从配置文件读取 CLI 设置，命令行参数可以覆盖
    cli_config = config_dict.get('cli', {})
    
    # 应用命令行参数（如果提供了命令行参数，则覆盖配置文件）
    if verbose:
        cli_config['verbose'] = True
    else:
        verbose = cli_config.get('verbose', False)
    
    # overwrite: 命令行参数优先，否则使用配置文件
    if overwrite:
        cli_config['overwrite'] = True
    else:
        overwrite = cli_config.get('overwrite', False)
    
    config_dict['cli'] = cli_config
    
    print(f"\n{'='*60}")
    print(f"翻译器: {config_dict['translator']['translator']}")
    print(f"目标语言: {config_dict['translator']['target_lang']}")
    print(f"使用 GPU: {cli_config.get('use_gpu', True)}")
    print(f"批量大小: {cli_config.get('batch_size', 1)}")
    print(f"覆盖已存在文件: {overwrite}")
    print(f"输出格式: {cli_config.get('format') or '保持原格式'}")
    print(f"保存质量: {cli_config.get('save_quality', 95)}")
    print(f"{'='*60}\n")
    
    # 收集所有图片文件
    file_service = FileService()
    all_files = []
    
    for input_path in input_paths:
        input_path = os.path.abspath(input_path)
        if os.path.isfile(input_path):
            all_files.append(input_path)
        elif os.path.isdir(input_path):
            # 递归获取文件夹中的所有图片
            folder_files = file_service.get_image_files_from_folder(input_path, recursive=True)
            all_files.extend(folder_files)
    
    if not all_files:
        print("❌ 未找到图片文件")
        return
    
    print(f"📁 找到 {len(all_files)} 个图片文件\n")
    
    # 确定输出目录
    if output_dir:
        final_output_dir = os.path.abspath(output_dir)
    else:
        # 使用配置文件中的输出目录，或默认规则
        if config_dict.get('app', {}).get('last_output_path'):
            final_output_dir = config_dict['app']['last_output_path']
        else:
            # 默认：在第一个输入路径旁边创建 -translated 文件夹
            first_input = input_paths[0]
            if os.path.isdir(first_input):
                final_output_dir = first_input.rstrip('/\\') + '-translated'
            else:
                final_output_dir = os.path.dirname(first_input)
    
    os.makedirs(final_output_dir, exist_ok=True)
    print(f"📤 输出目录: {final_output_dir}\n")
    
    # 准备翻译参数（像 UI 一样）
    translator_params = config_dict.get('cli', {}).copy()
    translator_params.update(config_dict)
    
    # 处理 font_path
    font_filename = config_dict.get('render', {}).get('font_path')
    if font_filename and not os.path.isabs(font_filename):
        font_full_path = os.path.join(ROOT_DIR, 'fonts', font_filename)
        if os.path.exists(font_full_path):
            translator_params['font_path'] = font_full_path
            # 同时更新 config_dict 中的 font_path
            config_dict['render']['font_path'] = font_full_path
    
    # 创建翻译器
    translator = MangaTranslator(params=translator_params)
    
    # 创建 Config 对象
    explicit_keys = {'render', 'upscale', 'translator', 'detector', 'colorizer', 'inpainter', 'ocr'}
    config_for_translate = {k: v for k, v in config_dict.items() if k in explicit_keys}
    for key in ['filter_text', 'kernel_size', 'mask_dilation_offset', 'force_simple_sort']:
        if key in config_dict:
            config_for_translate[key] = config_dict[key]
    
    manga_config = Config(**config_for_translate)
    
    # 准备批量数据（像 UI 一样）
    images_with_configs = []
    
    # 收集输入文件夹（用于保持目录结构）
    input_folders = set()
    for input_path in input_paths:
        if os.path.isdir(input_path):
            input_folders.add(os.path.normpath(os.path.abspath(input_path)))
    
    print(f"\n📁 加载图片...")
    for file_path in all_files:
        # 加载图片
        try:
            with open(file_path, 'rb') as f:
                image = Image.open(f)
                image.load()  # 立即加载图片数据
            image.name = file_path
            images_with_configs.append((image, manga_config))
        except Exception as e:
            print(f"❌ 无法加载: {os.path.basename(file_path)} - {e}")
    
    if not images_with_configs:
        print("没有需要翻译的图片")
        return
    
    # 准备 save_info（像 UI 一样）
    output_format = cli_config.get('format')
    if not output_format or output_format == "不指定":
        output_format = None
    
    save_info = {
        'output_folder': final_output_dir,
        'format': output_format,
        'overwrite': overwrite,
        'input_folders': input_folders  # 保持为 set，翻译器内部会处理
    }
    
    # 调试：检查输出目录是否存在
    if not os.path.exists(final_output_dir):
        os.makedirs(final_output_dir, exist_ok=True)
        print(f"✅ 创建输出目录: {final_output_dir}")
    
    batch_size = cli_config.get('batch_size', 3)
    total_images = len(images_with_configs)
    total_batches = (total_images + batch_size - 1) // batch_size if batch_size > 0 else 1
    
    print(f"\n📊 批量处理模式：共 {total_images} 张图片，分 {total_batches} 个批次处理")
    print(f"📋 保存配置:")
    print(f"   输出目录: {final_output_dir}")
    print(f"   输出格式: {output_format or '保持原格式'}")
    print(f"   覆盖模式: {overwrite}")
    print(f"   保存质量: {cli_config.get('save_quality', 95)}")
    print(f"   批量大小: {batch_size} 张/批")
    if verbose and input_folders:
        print(f"   输入文件夹:")
        for folder in input_folders:
            print(f"      - {folder}")
    print()
    
    # 批量翻译（像 UI 一样，一次性调用）
    try:
        print(f"🚀 开始翻译...")
        print(f"📋 传递给翻译器的 save_info:")
        print(f"   output_folder: {save_info['output_folder']}")
        print(f"   format: {save_info['format']}")
        print(f"   overwrite: {save_info['overwrite']}")
        print(f"   input_folders: {save_info['input_folders']}")
        print()
        logger.info(f"开始批量翻译，save_info={save_info}")
        contexts = await translator.translate_batch(images_with_configs, save_info=save_info)
        
        # 统计结果（像 UI 一样）
        success_count = 0
        failed_count = 0
        
        print(f"\n📊 翻译完成，检查结果...\n")
        logger.info(f"收到 {len(contexts)} 个翻译结果")
        
        for i, ctx in enumerate(contexts, 1):
            if ctx:
                has_result = hasattr(ctx, 'result') and ctx.result is not None
                has_success = hasattr(ctx, 'success') and ctx.success
                has_error = hasattr(ctx, 'translation_error') and ctx.translation_error
                logger.info(f"Context {i}: result={has_result}, success={has_success}, error={has_error}")
        
        for ctx in contexts:
            if ctx:
                # 检查是否有翻译错误
                if hasattr(ctx, 'translation_error') and ctx.translation_error:
                    failed_count += 1
                    print(f"❌ 翻译失败: {os.path.basename(ctx.image_name)}")
                    if verbose:
                        print(f"   错误: {ctx.translation_error}")
                elif hasattr(ctx, 'success') and ctx.success:
                    # 优先检查 success 标志（因为 result 可能被清理了）
                    success_count += 1
                    print(f"✅ 完成: {os.path.basename(ctx.image_name)}")
                elif ctx.result:
                    success_count += 1
                    print(f"✅ 完成: {os.path.basename(ctx.image_name)}")
                else:
                    failed_count += 1
                    print(f"❌ 翻译失败: {os.path.basename(ctx.image_name)} - 翻译结果为空")
            else:
                failed_count += 1
                print(f"❌ 翻译失败: 未知图片")
        
        if failed_count > 0:
            print(f"\n⚠️ 批量翻译完成：成功 {success_count}/{total_images} 张，失败 {failed_count}/{total_images} 张")
        else:
            print(f"\n✅ 批量翻译完成：成功 {success_count}/{total_images} 张")
        print(f"💾 文件已保存到：{final_output_dir}")
                
    except Exception as e:
        print(f"\n❌ 批量翻译错误: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        success_count = 0
        failed_count = len(images_with_configs)
    
    # 总结
    print(f"\n{'='*60}")
    print(f"✅ 成功: {success_count}")
    print(f"❌ 失败: {failed_count}")
    print(f"📊 总计: {len(all_files)}")
    print(f"{'='*60}")
    
    # 检查输出目录
    if os.path.exists(final_output_dir):
        output_files = [f for f in os.listdir(final_output_dir) if os.path.isfile(os.path.join(final_output_dir, f))]
        print(f"\n📁 输出目录: {final_output_dir}")
        print(f"   包含 {len(output_files)} 个文件")
        if verbose and output_files:
            for f in output_files[:10]:  # 只显示前10个
                file_path = os.path.join(final_output_dir, f)
                file_size = os.path.getsize(file_path) / 1024
                print(f"   - {f} ({file_size:.1f} KB)")
            if len(output_files) > 10:
                print(f"   ... 还有 {len(output_files) - 10} 个文件")
    else:
        print(f"\n⚠️  输出目录不存在: {final_output_dir}")
    print()


async def run_local_mode(args):
    """运行 local 模式的入口函数"""
    # 延迟导入配置服务
    from desktop_qt_ui.services.config_service import ConfigService
    
    # 初始化配置服务
    config_service = ConfigService(str(ROOT_DIR))
    
    # 如果指定了配置文件，加载它
    if hasattr(args, 'config') and args.config:
        if not config_service.load_config_file(args.config):
            print(f"❌ 无法加载配置文件: {args.config}")
            sys.exit(1)
    
    # 运行翻译
    try:
        await translate_files(
            args.input,
            args.output if hasattr(args, 'output') else None,
            config_service,
            verbose=args.verbose if hasattr(args, 'verbose') else False,
            overwrite=args.overwrite if hasattr(args, 'overwrite') else False
        )
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        if hasattr(args, 'verbose') and args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def main():
    """主函数（用于直接运行）"""
    args = parse_args()
    asyncio.run(run_local_mode(args))


if __name__ == '__main__':
    main()
