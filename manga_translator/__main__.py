#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Manga Translator - 命令行入口
支持多种运行模式：cli, local, ws, shared
"""
import asyncio
import logging
import os
import sys
import warnings

# 隐藏第三方库的警告；必须在导入 torch 前设置，才能拦截 torch.cuda 的 pynvml 提示。
warnings.filterwarnings('ignore', message='.*Triton.*')
warnings.filterwarnings('ignore', message='.*triton.*')
warnings.filterwarnings('ignore', message='.*pkg_resources.*')
warnings.filterwarnings('ignore', message='.*pynvml package is deprecated.*', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning, module='ctranslate2')

# 在 PyTorch 初始化前设置显存优化，允许使用共享显存
# expandable_segments 可以减少显存碎片，避免 OOM 错误
os.environ.setdefault('PYTORCH_ALLOC_CONF', 'expandable_segments:True')

# 在 PyQt6 之前加载 PyTorch，避免 PyQt6 的 Qt DLL 路径干扰 c10.dll 的加载
# 渲染模块 (text_render.py) 依赖 PyQt6，会触发 DLL 冲突
# 参考: https://github.com/pytorch/pytorch/issues/166628
try:
    import torch  # noqa: F401
except ImportError:
    pass

def main():
    """主函数"""
    from manga_translator.args import parse_args
    
    # 解析参数
    args = parse_args()

    # 统一导出 ONNX GPU 开关到环境变量，确保各运行模式都能生效
    if getattr(args, 'disable_onnx_gpu', False):
        os.environ['MT_DISABLE_ONNX_GPU'] = '1'
    
    # 延迟导入日志工具，避免加载大型库
    from manga_translator.utils.log import get_logger, init_logging, set_log_level
    
    # 初始化日志
    init_logging()
    set_log_level(level=logging.DEBUG if args.verbose else logging.INFO)
    logger = get_logger(args.mode)

    # 所有 CLI 模式在分发前统一释放外部配置表和 AI 提示词表。
    from manga_translator.runtime_files import ensure_runtime_files
    ensure_runtime_files(logger)
    
    # 根据模式分发
    if args.mode == 'web':
        # Web 服务器模式（API + Web界面）
        logger.info('[web] Starting Web server')
        from manga_translator.server import run_server
        run_server(args)
    
    elif args.mode == 'local':
        # Local 模式（命令行翻译）
        logger.info('Running in local mode')
        from manga_translator.mode.local import run_local_mode
        asyncio.run(run_local_mode(args))
    
    elif args.mode == 'ws':
        # WebSocket 模式
        logger.info('Running in WebSocket mode')
        from manga_translator.mode.ws import MangaTranslatorWS
        translator = MangaTranslatorWS(vars(args))
        asyncio.run(translator.listen(vars(args)))
    
    elif args.mode == 'shared':
        # Shared/API 模式
        logger.info('Running in shared/API mode')
        from manga_translator.mode.share import MangaShare
        translator = MangaShare(vars(args))
        asyncio.run(translator.listen(vars(args)))
    
    else:
        logger.error(f'Unknown mode: {args.mode}')
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nTranslation cancelled by user.')
        sys.exit(0)
    except asyncio.CancelledError:
        print('\nTranslation cancelled by user.')
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f'\n{e.__class__.__name__}: {e}')
        traceback.print_exc()
        sys.exit(1)
